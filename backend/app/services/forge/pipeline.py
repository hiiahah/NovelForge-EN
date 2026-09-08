"""Chapter pipeline: compile -> draft -> extract claims -> validate -> repair -> revalidate -> commit -> sync.

The model interface is a plain async callable so tests inject deterministic
fakes and production wires ``llm_service`` through ``LLMDrafter``.

Guarantees:
- The drafting model is never called when compilation fails.
- A draft that still has blocking issues after ``max_repairs`` is not committed
  and the run ends in ``status='rejected'`` with a structured report.
- Repair prompts only receive failed spans plus their constraints, never a
  licence to add facts.
- Every run records model calls, context hash, validation and sync reports.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional, Protocol, Sequence, Tuple

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType, ChapterPipelineRun
from app.schemas.card import CardCreate
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge import canon as canon_store
from app.services.forge import claims as claims_mod
from app.services.forge import firewall as fw
from app.services.forge import provenance
from app.services.forge import sync as sync_mod
from app.services.forge import validators as v
from app.services.forge.compiler import ChapterContextCompiler, CompiledChapterContext, ContextCompileError
from app.services.forge.corpus import load_source_chapters
from app.services.forge.craft import CraftInputs, CraftOptions, craft_chapter
from app.services.forge.textmetrics import infer_pov, measure

PIPELINE_VERSION = "pipeline-1"
DRAFT_PROMPT_VERSION = "forge-draft-1"
REPAIR_PROMPT_VERSION = "forge-repair-1"

DRAFT_SYSTEM_PROMPT = (
    "You are the drafting engine of an original serialized novel. You receive a compiled context with clearly labelled sections. "
    "Obey the fact classes exactly: LOCKED CANON may be used but not changed; only PLANNED beats may happen and only in order; PROHIBITED facts must never surface; "
    "FLEXIBLE details may be invented but must not persist; UNKNOWN facts stay unknown. Keep the explicit POV for the whole chapter. Preserve each character's voice rules. "
    "REFERENCE TECHNIQUE EXAMPLES demonstrate technique only: their names, places, events and sentences are not part of this story and must not be reproduced or paraphrased. "
    "Write only original prose that matches the NARRATIVE FINGERPRINT targets. Output the chapter body only (no title, no notes). "
    "After the prose, output the continuity metadata:\n"
    "1. <chapter_summary> block containing a comprehensive summary of core events, climax, and status changes.\n"
    "2. <scene_handoff> block with: ending_location, current_time, present_characters, unresolved_action, open_dialogue.\n"
    "3. Optional <claims>{json}</claims> block with: claims (list of {kind, subject, value, evidence})."
)
REPAIR_SYSTEM_PROMPT = (
    "You repair specific failed spans of a chapter draft. Rewrite ONLY the listed spans so that every listed issue is resolved. Do not add any new fact, entity, "
    "location, item, injury, relationship change or knowledge. Do not paraphrase reference examples. Keep the POV and the surrounding prose intact. "
    "Return the complete corrected chapter body followed by the same <claims>{json}</claims> block format."
)


class Drafter(Protocol):
    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context: CompiledChapterContext) -> str: ...


@dataclass
class PipelineOptions:
    max_repairs: int = 2
    budget_chars: int = 32000
    example_budget_chars: int = 2400
    word_target: Optional[int] = None
    regenerate: bool = False
    use_examples: bool = True
    fail_sync_on: Sequence[str] = ()
    style_max_failed: int = 4
    craft: Optional["CraftOptions"] = None  # None = legacy single-shot drafting with no craft passes


@dataclass
class PipelineResult:
    status: str
    run_id: Optional[int]
    chapter_number: int
    context: Optional[Dict[str, Any]] = None
    prose: str = ""
    validation: Dict[str, Any] = field(default_factory=dict)
    style: Dict[str, Any] = field(default_factory=dict)
    sync: Dict[str, Any] = field(default_factory=dict)
    error: Optional[Dict[str, Any]] = None
    model_calls: int = 0
    repair_attempts: int = 0
    chapter_card_id: Optional[int] = None
    craft: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def source_profile_for(session: Session, project_id: int) -> Optional[fw.SourceProfile]:
    """Build (or reuse cached) firewall profile from the linked source project."""
    manifest = provenance.get_manifest(session, project_id, create=True)
    if not manifest.source_project_id:
        return None
    chapters = load_source_chapters(session, manifest.source_project_id)
    if not chapters:
        return None
    bible = BibleService(session)
    names: List[str] = []
    roles: Dict[str, str] = {}
    locations: List[str] = []
    objects: List[str] = []
    for card in bible.cards_of_type(manifest.source_project_id, "Character Card"):
        c = _c(card)
        n = str(c.get("name") or card.title).strip()
        if fw.is_clean_proper_entity(n):
            names.append(n)
        roles[n] = str(c.get("role_type") or "")
        for a in (c.get("aliases") or []):
            al = str(a).strip()
            if fw.is_clean_proper_entity(al):
                names.append(al)
    for card in bible.cards_of_type(manifest.source_project_id, "Organization Card"):
        oname = str(_c(card).get("name") or card.title).strip()
        if fw.is_clean_proper_entity(oname):
            names.append(oname)
    for card in bible.cards_of_type(manifest.source_project_id, "Scene Card"):
        sname = str(_c(card).get("name") or card.title).strip()
        if fw.is_clean_proper_entity(sname):
            locations.append(sname)
    for card in bible.cards_of_type(manifest.source_project_id, "Item Card"):
        iname = str(_c(card).get("name") or card.title).strip()
        if fw.is_clean_proper_entity(iname):
            objects.append(iname)
    summaries: List[str] = []
    beats: List[str] = []
    for ch in chapters:
        an = ch.analysis or {}
        for s in an.get("scenes") or []:
            if isinstance(s, dict):
                if s.get("summary") or s.get("goal"):
                    summaries.append(str(s.get("summary") or s.get("goal")))
                if s.get("function"):
                    beats.append(str(s["function"]))
    return fw.SourceProfile.from_chapters(chapters, manuscript_id=chapters[0].manuscript_id, entity_names=names, scene_summaries=summaries, beat_sequence=beats, character_roles=roles, locations=locations, objects=objects)


def _character_cards(session: Session, project_id: int) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for card in BibleService(session).cards_of_type(project_id, "Character Card"):
        c = _c(card)
        out[str(c.get("name") or card.title).strip().lower()] = c
    return out


def validate_draft(session: Session, ctx: CompiledChapterContext, prose: str, *, profile: Optional[fw.SourceProfile], fingerprint: Dict[str, Any], style_max_failed: int = 4) -> Tuple[v.ValidationReport, List[claims_mod.Claim], Optional[claims_mod.ChapterClaims]]:
    prose_only, model_claims = claims_mod.split_prose_and_claims(prose)
    lang = fingerprint.get("language") or None
    det = claims_mod.extract_claims(prose_only, lang)
    all_claims = claims_mod.merge_model_claims(prose_only, det, model_claims)
    report = v.ValidationReport()
    outline = session.get(Card, ctx.outline_card_id)
    oc = _c(outline) if outline else {}
    beats = [b for b in (oc.get("beats") or []) if isinstance(b, dict)]
    source_entities = sorted(profile.entity_names) if profile else []
    report.issues += v.validate_entities(prose_only, allowed=ctx.allowed_entities, source_entities=source_entities, language=lang)
    locked_state = canon_store.state_as_of(session, ctx.project_id, ctx.chapter_number - 1, canon_revision=ctx.canon_revision)
    locked = {k: fv.value for k, fv in locked_state.items()}
    report.issues += v.validate_facts(all_claims, locked=locked, planned=ctx.fact_classes.get("planned", []), allowed=ctx.allowed_entities, paid_off=ctx.fact_classes.get("paid_off", []), prose=prose_only)
    report.issues += v.validate_outline(prose_only, beats=beats, forbidden=ctx.fact_classes.get("prohibited", []), language=lang, participants=ctx.participants)
    pov_type = ((fingerprint.get("layers") or {}).get("pov_focalization") or {}).get("features", {}).get("pov", "third_person")
    report.issues += v.validate_pov(prose_only, pov=ctx.pov, others=[p for p in ctx.participants if p != ctx.pov], pov_type=pov_type, prohibited=ctx.prohibited, language=lang)
    report.issues += v.validate_characters(prose_only, character_cards=_character_cards(session, ctx.project_id), participants=ctx.participants)
    report.issues += v.validate_temporal(prose_only, language=lang)
    positions = v.locate_beats(prose_only, beats)
    style_issues, style = v.validate_style(prose_only, fingerprint, beat_positions=positions, max_failed=style_max_failed)
    report.issues += style_issues
    report.style = style
    orig_issues, orig = v.validate_originality(prose_only, profile, allowed=ctx.allowed_entities)
    report.issues += orig_issues
    report.originality = orig
    report.metrics = measure(prose_only, lang).as_dict()
    report.metrics["claims"] = [c.as_dict() for c in all_claims]
    return report, all_claims, model_claims


def build_draft_prompt(ctx: CompiledChapterContext) -> str:
    return (
        ctx.prompt_text()
        + "\n\n[KOREAN WEBNOVEL STYLE & PACING DIRECTIVES]\n"
        "- Breathing Room & Pacing: Do not rush through beats like an obstacle course. Let scenes breathe. Give each beat ample sensory detail, dialogue, and inner reaction (typically 400-600 words per beat).\n"
        "- Dual-Layer Internal Monologue vs. Spoken Dialogue: The protagonist's spoken dialogue should remain measured, aristocratic, or composed, while their internal monologue is sharp, analytical, cynical, or humorous about their absurd circumstances. Contrast outer composure with inner calculation.\n"
        "- Misunderstanding & Tension Dynamics: Side characters frequently misread the protagonist's silence, exhaustion, or pragmatic caution as unfathomable genius, hidden power, or deep schematics.\n"
        "- Visual Paragraph Flow: Avoid monolithic walls of text. Webnovel formatting favors 1-3 sentence paragraphs, crisp dialogue spacing, and punchy narrative beats optimized for serialized reading.\n\n"
        "[OUTPUT REQUIREMENTS]\n"
        "Write the complete chapter now in original, immersive webnovel prose.\n\n"
        "Immediately after the prose, provide the chapter summary and scene handoff blocks:\n"
        "<chapter_summary>\n"
        f"# Chapter {ctx.chapter_number} Summary\n"
        "- Core Events & Climax: (Recap of key narrative beats and choices)\n"
        "- Character Dynamics: (Shifts in relationships, discoveries, or secrets)\n"
        "- Ending State: (Current cliffhanger and where the protagonist is left)\n"
        "</chapter_summary>\n\n"
        "<scene_handoff>\n"
        "ending_location: (Specific room/place where this chapter ends)\n"
        "current_time: (Time of day / story day)\n"
        "present_characters: (Characters present at chapter end)\n"
        "unresolved_action: (Immediate cliffhanger or action in progress)\n"
        "open_dialogue: (Last spoken line or pending reply, if any)\n"
        "</scene_handoff>\n\n"
        "You may also optionally include the <claims>{\"claims\": [...]}</claims> block for any explicit canon updates."
    )


def build_repair_prompt(ctx: CompiledChapterContext, prose: str, issues: List[v.Issue]) -> str:
    lines = ["[CONSTRAINTS — unchanged]", ctx.sections_text_for(("pov", "pov_knowledge_boundary", "anti_hallucination", "originality", "prohibited", "beats")), "", "[FAILED SPANS AND REQUIRED FIXES]"]
    for i, issue in enumerate(issues, start=1):
        span = f"chars {issue.span[0]}-{issue.span[1]}: «{prose[issue.span[0]:issue.span[1]][:160]}»" if issue.span and issue.span[0] >= 0 else "(whole chapter)"
        lines.append(f"{i}. [{issue.layer}/{issue.code}] {issue.message} — {span}\n   fix: {issue.hint}")
    lines += ["", "[CURRENT DRAFT]", prose]
    return "\n".join(lines)


def _upsert_chapter_text(session: Session, project_id: int, ctx: CompiledChapterContext, prose: str, *, validation: Dict[str, Any]) -> Card:
    bible = BibleService(session)
    ct = session.exec(select(CardType).where(CardType.name == "Chapter Text")).first()
    if ct is None:
        raise RuntimeError("Card type 'Chapter Text' is not bootstrapped")
    outline = session.get(Card, ctx.outline_card_id)
    oc = _c(outline) if outline else {}
    existing = None
    for card in bible.cards_of_type(project_id, "Chapter Text"):
        if int(_c(card).get("chapter_number") or 0) == ctx.chapter_number:
            existing = card
            break
    content = {
        "volume_number": oc.get("volume_number") or 0, "stage_number": oc.get("stage_number") or 0, "title": oc.get("title") or f"Chapter {ctx.chapter_number}",
        "chapter_number": ctx.chapter_number, "entity_list": ctx.participants, "content": prose, "pov": ctx.pov, "participants": ctx.participants,
        "context_hash": ctx.context_hash, "validation_passed": validation.get("passed"), "sync_status": "pending", "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if existing is None:
        card = CardService(session).create(CardCreate(title=f"Chapter {ctx.chapter_number}: {content['title']}"[:200], content=content, card_type_id=ct.id, parent_id=outline.parent_id if outline else None), project_id, commit=False)
    else:
        existing.content = {**_c(existing), **content}
        flag_modified(existing, "content")
        session.add(existing)
        card = existing
    session.flush()
    return card


def craft_inputs_for(session: Session, ctx: CompiledChapterContext) -> CraftInputs:
    """Resolve the Bible material the craft passes need (cards, relationships, knowledge gaps) for this chapter."""
    bible = BibleService(session)
    outline = session.get(Card, ctx.outline_card_id)
    oc = _c(outline) if outline else {}
    beats = [b for b in (oc.get("beats") or []) if isinstance(b, dict)]
    cards_by_name: Dict[str, Dict[str, Any]] = {}
    for card in bible.cards_of_type(ctx.project_id, "Character Card"):
        c = dict(_c(card))
        c.setdefault("name", card.title)
        cards_by_name[str(c["name"]).strip().lower()] = c
        for alias in c.get("aliases") or []:
            cards_by_name.setdefault(str(alias).strip().lower(), c)
    relationships: Dict[str, Dict[str, Any]] = {}
    pov_l = ctx.pov.lower()
    for card in bible.cards_of_type(ctx.project_id, "Relationship Arc"):
        c = _c(card)
        a, b = str(c.get("character_a") or "").strip(), str(c.get("character_b") or "").strip()
        if a.lower() == pov_l and b:
            relationships[b.lower()] = c
        elif b.lower() == pov_l and a:
            relationships[a.lower()] = c
    gaps: Dict[str, List[str]] = {}
    for card in bible.cards_of_type(ctx.project_id, "Knowledge Fact"):
        c = _c(card)
        knowers = {str(k.get("entity", "")).lower(): k for k in (c.get("knowers") or []) if isinstance(k, dict)}
        pov_state = (knowers.get(pov_l) or {}).get("state")
        if pov_state in ("knows",):
            continue
        for name, k in knowers.items():
            if name != pov_l and k.get("state") == "knows":
                gaps.setdefault(name, []).append(str(c.get("fact") or card.title))
    prev_scene = ""
    for s in ctx.sections:
        if s.key == "scene_state":
            prev_scene = s.text
            break
    location = ""
    if prev_scene.startswith("location: "):
        location = prev_scene.split(";")[0].replace("location: ", "").strip()
    return CraftInputs(
        pov=ctx.pov, participants=list(ctx.participants), beats=beats, word_target=int(ctx.word_target or 2500),
        closing_hook=str(oc.get("closing_hook") or ""), location=location if location and location != "None" else "",
        cards_by_name=cards_by_name, relationships=relationships, knowledge_gaps=gaps,
    )


def _run_row(session: Session, project_id: int, chapter_number: int, ctx: Optional[CompiledChapterContext]) -> ChapterPipelineRun:
    manifest = provenance.get_manifest(session, project_id, create=True)
    row = ChapterPipelineRun(project_id=project_id, chapter_number=chapter_number, outline_card_id=ctx.outline_card_id if ctx else None, status="running", stage="compile", canon_revision_before=int(manifest.canon_revision), context_hash=ctx.context_hash if ctx else "", context_manifest=ctx.manifest if ctx else {})
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _finish(session: Session, row: ChapterPipelineRun, **fields: Any) -> None:
    for k, val in fields.items():
        setattr(row, k, val)
    row.updated_at = datetime.now()
    session.add(row)
    session.commit()


async def run_chapter(
    session: Session,
    *,
    project_id: int,
    chapter_number: int,
    drafter: Drafter,
    outline_card_id: Optional[int] = None,
    pov: Optional[str] = None,
    participants: Optional[Sequence[str]] = None,
    expected_canon_revision: Optional[int] = None,
    options: Optional[PipelineOptions] = None,
) -> PipelineResult:
    opts = options or PipelineOptions()
    compiler = ChapterContextCompiler(session)
    recent: List[str] = []
    for row in session.exec(select(ChapterPipelineRun).where(ChapterPipelineRun.project_id == project_id, ChapterPipelineRun.status == "committed").order_by(ChapterPipelineRun.id.desc()).limit(3)).all():
        recent += (row.context_manifest or {}).get("retrieved_example_ids") or []
    try:
        ctx = compiler.compile(project_id=project_id, chapter_number=chapter_number, outline_card_id=outline_card_id, pov=pov, participants=participants, expected_canon_revision=expected_canon_revision, budget_chars=opts.budget_chars, example_budget_chars=opts.example_budget_chars if opts.use_examples else 0, recently_used_examples=recent, word_target=opts.word_target, regenerate=opts.regenerate)
        session.commit()
    except ContextCompileError as exc:
        session.rollback()
        row = _run_row(session, project_id, chapter_number, None)
        _finish(session, row, status="compile_failed", stage="compile", error=json.dumps(exc.as_dict(), ensure_ascii=False))
        return PipelineResult(status="compile_failed", run_id=row.id, chapter_number=chapter_number, error=exc.as_dict())
    if not opts.use_examples:
        ctx.sections = [s for s in ctx.sections if s.key != "examples"]
        ctx.retrieved_examples = []
        ctx.manifest["retrieved_example_ids"] = []
    row = _run_row(session, project_id, chapter_number, ctx)
    fingerprint = _c(BibleService(session).singleton(project_id, "Narrative Fingerprint"))
    profile = source_profile_for(session, project_id)
    model_calls = 0
    craft_report: Dict[str, Any] = {}
    try:
        _finish(session, row, stage="draft")
        draft_system, draft_user = DRAFT_SYSTEM_PROMPT, build_draft_prompt(ctx)

        async def _single_shot() -> str:
            return await drafter(role="drafting", system_prompt=draft_system, user_prompt=draft_user, context=ctx)

        if opts.craft is None:
            raw = await _single_shot()
            model_calls += 1
        else:
            inputs = craft_inputs_for(session, ctx)
            if opts.craft.protagonist_voice and not any(s.key == "protagonist_voice" for s in ctx.sections):
                from app.services.forge.craft.passes import voice_section_text

                draft_user = draft_user + f"\n\n[PROTAGONIST VOICE — {ctx.pov}]\n" + voice_section_text(inputs)
            outcome = await craft_chapter(drafter, ctx, base_system_prompt=draft_system, base_user_prompt=draft_user, inputs=inputs, opts=opts.craft, single_shot=_single_shot)
            raw = outcome.prose
            model_calls += outcome.model_calls
            craft_report = outcome.report.model_dump(mode="json")
        prose = raw
        _finish(session, row, stage="validate", model_calls=model_calls)
        report, all_claims, model_claims = validate_draft(session, ctx, prose, profile=profile, fingerprint=fingerprint, style_max_failed=opts.style_max_failed)
        attempts = 0
        history: List[Dict[str, Any]] = [report.as_dict()]
        while report.blocking and attempts < opts.max_repairs:
            attempts += 1
            _finish(session, row, stage=f"repair-{attempts}", repair_attempts=attempts)
            prose_only, _ = claims_mod.split_prose_and_claims(prose)
            repaired = await drafter(role="repair", system_prompt=REPAIR_SYSTEM_PROMPT, user_prompt=build_repair_prompt(ctx, prose_only, report.blocking), context=ctx)
            model_calls += 1
            prose = repaired
            report, all_claims, model_claims = validate_draft(session, ctx, prose, profile=profile, fingerprint=fingerprint, style_max_failed=opts.style_max_failed)
            history.append(report.as_dict())
        final_report = report.as_dict()
        final_report["history"] = history
        if craft_report:
            final_report["craft"] = craft_report
        if report.blocking:
            _finish(session, row, status="rejected", stage="validate", validation_report=final_report, style_report=report.style, model_calls=model_calls, repair_attempts=attempts, error=f"{len(report.blocking)} blocking issue(s) remain after {attempts} repair attempt(s)")
            return PipelineResult(status="rejected", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose, validation=final_report, style=report.style, model_calls=model_calls, repair_attempts=attempts, error={"code": "validation_failed", "blocking": [i.as_dict() for i in report.blocking]}, craft=craft_report)
        prose_only, model_claims = claims_mod.split_prose_and_claims(prose)
        _finish(session, row, stage="commit", validation_report=final_report, style_report=report.style, model_calls=model_calls, repair_attempts=attempts)
        card = _upsert_chapter_text(session, project_id, ctx, prose_only, validation=final_report)
        session.commit()
        _finish(session, row, stage="sync", chapter_card_id=card.id)
        outline = session.get(Card, ctx.outline_card_id)
        allowed_outcomes = list((_c(outline) or {}).get("allowed_outcomes") or []) + [str(b.get("description") or "") for b in (_c(outline) or {}).get("beats") or [] if isinstance(b, dict)]
        try:
            sync_report = sync_mod.synchronize_chapter(session, project_id=project_id, chapter_number=chapter_number, chapter_card_id=card.id, pov=ctx.pov, participants=ctx.participants, prose=prose_only, claims=all_claims, model_claims=model_claims, allowed_outcomes=allowed_outcomes, outline_card_id=ctx.outline_card_id, fail_on=opts.fail_sync_on)
        except sync_mod.SyncError as exc:
            card = session.get(Card, card.id)
            if card is not None:
                cc = _c(card)
                cc["sync_status"] = "failed"
                card.content = cc
                flag_modified(card, "content")
                session.add(card)
                session.commit()
            _finish(session, row, status="sync_failed", stage="sync", error=str(exc), model_calls=model_calls, repair_attempts=attempts, validation_report=final_report, style_report=report.style)
            return PipelineResult(status="sync_failed", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose_only, validation=final_report, style=report.style, model_calls=model_calls, repair_attempts=attempts, chapter_card_id=card.id if card else None, error={"code": "sync_failed", "message": str(exc)}, craft=craft_report)
        _finish(session, row, status="committed", stage="done", sync_report=sync_report, canon_revision_after=sync_report["canon_revision_after"], model_calls=model_calls, repair_attempts=attempts, validation_report=final_report, style_report=report.style)
        return PipelineResult(status="committed", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose_only, validation=final_report, style=report.style, sync=sync_report, model_calls=model_calls, repair_attempts=attempts, chapter_card_id=card.id, craft=craft_report)
    except Exception as exc:  # unexpected failure: never report success
        session.rollback()
        _finish(session, row, status="error", error=f"{type(exc).__name__}: {exc}", model_calls=model_calls)
        return PipelineResult(status="error", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), model_calls=model_calls, error={"code": "exception", "message": f"{type(exc).__name__}: {exc}"})


class LLMDrafter:
    """Production drafter: routes roles to LLM configs through llm_service."""

    def __init__(self, session: Session, role_configs: Dict[str, int], *, temperature: float = 0.7, max_tokens: int = 65536, timeout: float = 240.0):
        self.session = session
        self.role_configs = role_configs
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout

    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context: CompiledChapterContext) -> str:
        from langchain_core.messages import HumanMessage, SystemMessage

        from app.services.ai.core.chat_model_factory import build_chat_model

        cid = self.role_configs.get(role) or self.role_configs.get("drafting")
        if not cid:
            raise RuntimeError(f"No LLM configuration for role '{role}'")
        temperature = {"drafting": self.temperature, "hook": min(1.0, self.temperature + 0.1), "polish": 0.5, "scene_planner": 0.4, "critic": 0.2}.get(role, 0.3)
        model = build_chat_model(session=self.session, llm_config_id=int(cid), temperature=temperature, max_tokens=self.max_tokens, timeout=self.timeout)
        result = await model.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = "".join(str(c.get("text", "") if isinstance(c, dict) else c) for c in content)
        return str(content)


__all__ = ["DRAFT_PROMPT_VERSION", "DRAFT_SYSTEM_PROMPT", "PIPELINE_VERSION", "REPAIR_PROMPT_VERSION", "CraftOptions", "Drafter", "LLMDrafter", "PipelineOptions", "PipelineResult", "build_draft_prompt", "build_repair_prompt", "craft_inputs_for", "run_chapter", "source_profile_for", "validate_draft"]
