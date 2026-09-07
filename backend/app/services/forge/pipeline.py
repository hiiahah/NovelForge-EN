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
    "After the prose, on a new line, output a <claims>{json}</claims> block with: claims (list of {kind, subject, value, evidence}; evidence must be a verbatim sentence from your prose), "
    "summary (<= 120 words), ending_location, current_time, unresolved_immediate_action, open_dialogue_obligation."
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
    budget_chars: int = 16000
    example_budget_chars: int = 2400
    word_target: Optional[int] = None
    regenerate: bool = False
    use_examples: bool = True
    fail_sync_on: Sequence[str] = ()
    style_max_failed: int = 4


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
        n = str(c.get("name") or card.title)
        names.append(n)
        roles[n] = str(c.get("role_type") or "")
        names += [str(a) for a in (c.get("aliases") or [])]
    for card in bible.cards_of_type(manifest.source_project_id, "Organization Card"):
        names.append(str(_c(card).get("name") or card.title))
    for card in bible.cards_of_type(manifest.source_project_id, "Scene Card"):
        locations.append(str(_c(card).get("name") or card.title))
    for card in bible.cards_of_type(manifest.source_project_id, "Item Card"):
        objects.append(str(_c(card).get("name") or card.title))
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
    return ctx.prompt_text() + "\n\n[OUTPUT REQUIREMENTS]\nWrite the complete chapter now. Original prose only; then the <claims>{json}</claims> block."


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
    try:
        _finish(session, row, stage="draft")
        raw = await drafter(role="drafting", system_prompt=DRAFT_SYSTEM_PROMPT, user_prompt=build_draft_prompt(ctx), context=ctx)
        model_calls += 1
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
        if report.blocking:
            _finish(session, row, status="rejected", stage="validate", validation_report=final_report, style_report=report.style, model_calls=model_calls, repair_attempts=attempts, error=f"{len(report.blocking)} blocking issue(s) remain after {attempts} repair attempt(s)")
            return PipelineResult(status="rejected", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose, validation=final_report, style=report.style, model_calls=model_calls, repair_attempts=attempts, error={"code": "validation_failed", "blocking": [i.as_dict() for i in report.blocking]})
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
            return PipelineResult(status="sync_failed", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose_only, validation=final_report, style=report.style, model_calls=model_calls, repair_attempts=attempts, chapter_card_id=card.id if card else None, error={"code": "sync_failed", "message": str(exc)})
        _finish(session, row, status="committed", stage="done", sync_report=sync_report, canon_revision_after=sync_report["canon_revision_after"], model_calls=model_calls, repair_attempts=attempts, validation_report=final_report, style_report=report.style)
        return PipelineResult(status="committed", run_id=row.id, chapter_number=chapter_number, context=ctx.as_dict(), prose=prose_only, validation=final_report, style=report.style, sync=sync_report, model_calls=model_calls, repair_attempts=attempts, chapter_card_id=card.id)
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
        model = build_chat_model(session=self.session, llm_config_id=int(cid), temperature=self.temperature if role == "drafting" else 0.3, max_tokens=self.max_tokens, timeout=self.timeout)
        result = await model.ainvoke([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        content = getattr(result, "content", result)
        if isinstance(content, list):
            content = "".join(str(c.get("text", "") if isinstance(c, dict) else c) for c in content)
        return str(content)


__all__ = ["DRAFT_PROMPT_VERSION", "DRAFT_SYSTEM_PROMPT", "PIPELINE_VERSION", "REPAIR_PROMPT_VERSION", "Drafter", "LLMDrafter", "PipelineOptions", "PipelineResult", "build_draft_prompt", "build_repair_prompt", "run_chapter", "source_profile_for", "validate_draft"]
