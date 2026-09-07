"""Authoritative chapter context compiler (fail-closed).

Producer of the *only* generation context used by the Forge pipeline.
Consumers: ``pipeline.run_chapter`` (draft + repair prompts), ``validators``
(fact classes), UI preview.

Inputs are ids, never frontend-assembled text. The compiler resolves every
card itself, reads canon as-of chapter N-1 from the temporal store, retrieves
reference examples for the outline's beat functions, and emits a machine-
readable manifest (included cards + revisions, evidence ids, example ids,
dropped items with reasons, budget, canon revision, context hash).

Fail-closed conditions raise ``ContextCompileError`` (the model is never
called):
  missing / stale chapter outline, missing or unresolved POV, unresolvable
  participants, stale mandatory dependencies, stale canon revision, previous
  chapter not synchronized, namespace contamination, budget that would drop a
  mandatory section, unresolved future-knowledge restriction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlmodel import Session, select

from app.db.models import Card, CardType, ChapterPipelineRun
from app.services.bible.bible_service import BibleService
from app.services.forge import canon as canon_store
from app.services.forge import examples as example_lib
from app.services.forge import provenance
from app.services.forge.fingerprint import compact_fingerprint
from app.services.forge.textmetrics import sha256_text, split_paragraphs, tokenize
from app.services.forge.claims import _CAP_NAME
from app.services.forge.validators import _PROHIBITED_STOP

COMPILER_VERSION = provenance.COMPILER_VERSION

FACT_CLASSES = ("locked_canon", "planned", "prohibited", "paid_off", "flexible", "unknown")

FLEXIBLE_DETAIL_POLICY = {
    "allowed": ["nonspecific weather", "generic room atmosphere", "minor body movement and gesture", "nonpersistent sensory language", "unnamed background extras with no lines of consequence"],
    "requires_canon_or_outline": ["names", "locations", "relationships", "history and backstory", "powers and abilities", "injuries", "possessions", "organization membership", "secrets", "dates and durations", "travel time", "permanent physical traits", "major decisions", "new recurring characters", "new plot causes"],
}

MANDATORY_SECTIONS = ("reader_contract", "story_foundation", "chapter_outline", "beats", "pov", "pov_knowledge_boundary", "prohibited", "anti_hallucination", "originality", "fingerprint", "word_target")


class ContextCompileError(RuntimeError):
    def __init__(self, code: str, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}

    def as_dict(self) -> Dict[str, Any]:
        return {"code": self.code, "message": str(self), "details": self.details}


@dataclass
class Section:
    key: str
    title: str
    text: str
    mandatory: bool = False
    card_ids: List[int] = field(default_factory=list)
    revisions: List[str] = field(default_factory=list)
    priority: int = 50

    @property
    def chars(self) -> int:
        return len(self.text) + len(self.title) + 6


@dataclass
class CompiledChapterContext:
    project_id: int
    chapter_number: int
    outline_card_id: int
    pov: str
    participants: List[str]
    sections: List[Section]
    manifest: Dict[str, Any]
    fact_classes: Dict[str, List[str]]
    prohibited: List[str]
    allowed_entities: List[str]
    retrieved_examples: List[Dict[str, Any]]
    beat_functions: List[str]
    word_target: int
    canon_revision: int
    context_hash: str = ""

    def prompt_text(self) -> str:
        parts: List[str] = []
        for s in self.sections:
            parts.append(f"[{s.title}]\n{s.text.strip()}")
        return "\n\n".join(parts)

    def sections_text_for(self, keys: Iterable[str]) -> str:
        wanted = set(keys)
        return "\n\n".join(f"[{s.title}]\n{s.text}" for s in self.sections if s.key in wanted)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "project_id": self.project_id, "chapter_number": self.chapter_number, "outline_card_id": self.outline_card_id, "pov": self.pov,
            "participants": self.participants, "text": self.prompt_text(), "sections": [{"key": s.key, "title": s.title, "chars": s.chars, "mandatory": s.mandatory, "card_ids": s.card_ids, "text": s.text} for s in self.sections],
            "manifest": self.manifest, "fact_classes": self.fact_classes, "prohibited": self.prohibited, "allowed_entities": self.allowed_entities,
            "retrieved_examples": self.retrieved_examples, "beat_functions": self.beat_functions, "word_target": self.word_target, "canon_revision": self.canon_revision, "context_hash": self.context_hash,
        }


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _rev(card: Card) -> str:
    return f"card:{card.id}@{provenance.card_hash(card)[:12]}"


class ChapterContextCompiler:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)

    # ------------------------------------------------------------------ lookups
    def _outline(self, project_id: int, chapter_number: int, outline_card_id: Optional[int]) -> Card:
        if outline_card_id:
            card = self.session.get(Card, int(outline_card_id))
            if card is None or card.project_id != project_id:
                raise ContextCompileError("outline_missing", f"Chapter Outline card {outline_card_id} not found in project {project_id}")
            if int(_c(card).get("chapter_number") or 0) != int(chapter_number):
                raise ContextCompileError("outline_mismatch", f"Chapter Outline {outline_card_id} is for chapter {_c(card).get('chapter_number')}, not {chapter_number}")
            return card
        for card in self.bible.cards_of_type(project_id, "Chapter Outline"):
            if int(_c(card).get("chapter_number") or 0) == int(chapter_number):
                return card
        raise ContextCompileError("outline_missing", f"No Chapter Outline exists for chapter {chapter_number}")

    def _chapter_text_card(self, project_id: int, chapter_number: int) -> Optional[Card]:
        for card in self.bible.cards_of_type(project_id, "Chapter Text"):
            if int(_c(card).get("chapter_number") or 0) == int(chapter_number):
                return card
        return None

    def _character_index(self, project_id: int) -> Tuple[Dict[str, Card], Dict[str, str]]:
        by_name: Dict[str, Card] = {}
        alias_map: Dict[str, str] = {}
        for card in self.bible.cards_of_type(project_id, "Character Card"):
            c = _c(card)
            name = str(c.get("name") or card.title).strip()
            if not name:
                continue
            by_name[name.lower()] = card
            alias_map[name.lower()] = name
            for a in c.get("aliases") or []:
                alias_map[str(a).strip().lower()] = name
        return by_name, alias_map

    # ---------------------------------------------------------------- guards
    def _check_namespace(self, project_id: int, manifest) -> None:
        """Original projects must not contain source-manuscript artifacts."""
        for type_name in ("Chapter Analysis", "Narrative Genome", "Story Structure Map"):
            cards = self.bible.cards_of_type(project_id, type_name)
            if cards:
                raise ContextCompileError("namespace_contamination", f"Original project {project_id} contains source-analysis cards of type '{type_name}'", {"card_ids": [c.id for c in cards][:10]})
        if manifest and manifest.project_role == "source":
            raise ContextCompileError("namespace_contamination", f"Project {project_id} is a source-analysis project; chapters are generated only in original projects")

    def _check_previous_sync(self, project_id: int, chapter_number: int, manifest) -> None:
        if chapter_number <= 1:
            return
        if manifest is None or manifest.latest_committed_chapter < chapter_number - 1:
            raise ContextCompileError("previous_chapter_not_synchronized", f"Chapter {chapter_number - 1} has not been committed and synchronized (latest committed: {manifest.latest_committed_chapter if manifest else 0})")
        prev_run = self.session.exec(select(ChapterPipelineRun).where(ChapterPipelineRun.project_id == project_id, ChapterPipelineRun.chapter_number == chapter_number - 1).order_by(ChapterPipelineRun.id.desc())).first()
        if prev_run is not None and prev_run.status not in ("committed",):
            raise ContextCompileError("previous_chapter_not_synchronized", f"Latest pipeline run for chapter {chapter_number - 1} ended in status '{prev_run.status}'")

    def _check_stale(self, project_id: int, outline: Card, fingerprint: Optional[Card]) -> None:
        stale_rows = provenance.stale_artifacts(self.session, project_id)
        blocking = []
        for s in stale_rows:
            if s["artifact_kind"] in provenance.MANDATORY_FOR_GENERATION or s["card_id"] in {outline.id, getattr(fingerprint, "id", None)}:
                blocking.append(s)
        outline_prov = provenance.get(self.session, project_id, "Chapter Outline", str(outline.id))
        if outline_prov is not None:
            stale, reasons = provenance.is_stale(self.session, outline_prov)
            if stale:
                blocking.append({"artifact_kind": "Chapter Outline", "artifact_key": str(outline.id), "reasons": reasons})
        if fingerprint is not None and _c(fingerprint).get("stale"):
            blocking.append({"artifact_kind": "Narrative Fingerprint", "artifact_key": str(fingerprint.id), "reasons": [_c(fingerprint).get("stale_reason") or "flagged stale"]})
        if blocking:
            raise ContextCompileError("stale_dependencies", "Mandatory dependencies are stale; recompute them before generating", {"stale": blocking})

    # --------------------------------------------------------------- compile
    def compile(
        self,
        *,
        project_id: int,
        chapter_number: int,
        outline_card_id: Optional[int] = None,
        pov: Optional[str] = None,
        participants: Optional[Sequence[str]] = None,
        expected_canon_revision: Optional[int] = None,
        budget_chars: int = 16000,
        example_budget_chars: int = 2400,
        recently_used_examples: Iterable[str] = (),
        word_target: Optional[int] = None,
        regenerate: bool = False,
    ) -> CompiledChapterContext:
        manifest = provenance.get_manifest(self.session, project_id, create=True)
        self._check_namespace(project_id, manifest)
        if expected_canon_revision is not None and int(expected_canon_revision) != int(manifest.canon_revision):
            raise ContextCompileError("stale_canon_revision", f"Canon revision {expected_canon_revision} is stale (current {manifest.canon_revision})")
        outline = self._outline(project_id, chapter_number, outline_card_id)
        oc = _c(outline)
        if not regenerate:
            self._check_previous_sync(project_id, chapter_number, manifest)
        elif chapter_number > manifest.latest_committed_chapter + 1:
            raise ContextCompileError("previous_chapter_not_synchronized", f"Cannot regenerate chapter {chapter_number}: chapter {chapter_number - 1} is not committed")

        fingerprint = self.bible.singleton(project_id, "Narrative Fingerprint")
        self._check_stale(project_id, outline, fingerprint)
        if fingerprint is None:
            raise ContextCompileError("fingerprint_missing", "No Narrative Fingerprint card: build the fingerprint before generating")
        fp = _c(fingerprint)
        if not fp.get("layers"):
            raise ContextCompileError("fingerprint_missing", "Narrative Fingerprint card is empty")

        by_name, alias_map = self._character_index(project_id)
        pov_raw = (pov or oc.get("pov") or "").strip()
        if not pov_raw:
            raise ContextCompileError("pov_missing", "Explicit POV is required (request.pov or Chapter Outline.pov)")
        pov_name = alias_map.get(pov_raw.lower())
        if not pov_name:
            raise ContextCompileError("pov_invalid", f"POV '{pov_raw}' does not resolve to a Character Card", {"known": sorted(by_name)})
        raw_participants = list(participants or oc.get("participants") or oc.get("entity_list") or [])
        resolved: List[str] = []
        unresolved: List[str] = []
        for p in raw_participants:
            key = str(p).strip().lower()
            if not key:
                continue
            name = alias_map.get(key)
            if name is None:
                # Non-character entities (organizations, scenes, items) are resolved below.
                if self._entity_card(project_id, key) is None:
                    unresolved.append(str(p))
                    continue
                name = str(p).strip()
            if name not in resolved:
                resolved.append(name)
        if unresolved:
            raise ContextCompileError("participants_unresolved", "Participants do not resolve to Bible cards", {"unresolved": unresolved})
        if pov_name not in resolved:
            resolved.insert(0, pov_name)

        prev_chapter = chapter_number - 1
        canon_rev = int(manifest.canon_revision)
        state = canon_store.state_as_of(self.session, project_id, prev_chapter, canon_revision=canon_rev)
        names_l = {n.lower() for n in resolved}

        sections: List[Section] = []
        included: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        prohibited: List[str] = []
        fact_classes: Dict[str, List[str]] = {k: [] for k in FACT_CLASSES}

        def include(card: Card, why: str) -> None:
            included.append({"card_id": card.id, "card_type": getattr(card.card_type, "name", ""), "title": card.title, "revision": _rev(card), "reason": why})

        # 1-2. Reader Contract, Story Foundation (mandatory)
        contract = self.bible.singleton(project_id, "Reader Contract")
        foundation = self.bible.singleton(project_id, "Story Foundation")
        if contract is None or foundation is None:
            raise ContextCompileError("bible_incomplete", "Reader Contract and Story Foundation are required", {"missing": [n for n, c in (("Reader Contract", contract), ("Story Foundation", foundation)) if c is None]})
        cc, fc = _c(contract), _c(foundation)
        sections.append(Section("reader_contract", "READER CONTRACT", f"fantasy: {_trim(cc.get('primary_fantasy'), 220)} | reward: {_trim(cc.get('primary_emotional_reward'), 220)} | tone: {_trim(cc.get('expected_tone'), 120)} | protagonist must: {_trim('; '.join(cc.get('expected_protagonist_behavior') or []), 300)} | never: {_trim('; '.join(cc.get('violations') or []), 300)}", mandatory=True, card_ids=[contract.id], revisions=[_rev(contract)], priority=1))
        include(contract, "mandatory")
        sections.append(Section("story_foundation", "STORY FOUNDATION", f"premise: {_trim(fc.get('core_premise'), 300)} | dramatic question: {_trim(fc.get('central_dramatic_question'), 200)} | protagonist goal: {_trim(fc.get('protagonist_goal'), 160)} | opposition: {_trim(fc.get('main_opposition'), 160)} | stakes: {_trim(fc.get('stakes'), 160)} | mechanism: {_trim(fc.get('unique_mechanism'), 200)}", mandatory=True, card_ids=[foundation.id], revisions=[_rev(foundation)], priority=2))
        include(foundation, "mandatory")

        # 3. Theme movement
        theme = self.bible.singleton(project_id, "Theme Map")
        if theme is not None:
            tc = _c(theme)
            sections.append(Section("theme", "THEME MOVEMENT", f"{_trim(tc.get('theme_question'), 160)} — protagonist believes: {_trim(tc.get('protagonist_initial_belief'), 160)}; planned movement: {_trim(tc.get('planned_movement'), 200)}", card_ids=[theme.id], revisions=[_rev(theme)], priority=20))
            include(theme, "theme")

        # 3b. Abstract mechanisms (entity-free, already firewalled at transfer time)
        mech_lines: List[str] = []
        mech_cards = self.bible.cards_of_type(project_id, "Abstract Mechanism")
        for card in mech_cards[:6]:
            mc = _c(card)
            if not mc.get("abstraction"):
                continue
            seq = " -> ".join(str(s) for s in (mc.get("typical_sequence") or [])[:5])
            mech_lines.append(f"- [{mc.get('dimension') or 'mechanism'}] {_trim(mc.get('abstraction'), 220)}" + (f" | sequence: {_trim(seq, 200)}" if seq else ""))
            include(card, "abstract mechanism")
        if mech_lines:
            sections.append(Section("mechanisms", "ABSTRACT NARRATIVE MECHANISMS (technique patterns only; invent all concrete content)", "\n".join(mech_lines), card_ids=[c.id for c in mech_cards[:6]], revisions=[_rev(c) for c in mech_cards[:6]], priority=21))

        # 4. Stage / volume purpose
        vol_no, stage_no = oc.get("volume_number"), oc.get("stage_number")
        for type_name, num_key, num in (("Volume Outline", "volume_number", vol_no), ("Stage Outline", "stage_number", stage_no)):
            for card in self.bible.cards_of_type(project_id, type_name):
                c = _c(card)
                if num is not None and int(c.get(num_key) or -1) == int(num):
                    sections.append(Section(type_name.lower().replace(" ", "_"), type_name.upper() + " PURPOSE", _trim(c.get("main_target") or c.get("overview") or c.get("goal") or "", 500) + (f" | side: {_trim(c.get('branch_line'), 200)}" if c.get("branch_line") else ""), card_ids=[card.id], revisions=[_rev(card)], priority=18))
                    include(card, f"{type_name} {num}")
                    break

        # 5-8. Chapter outline, beats, allowed and forbidden outcomes (mandatory)
        beats = [b for b in (oc.get("beats") or []) if isinstance(b, dict)]
        beat_lines = [f"{i + 1}. [{b.get('function') or 'beat'}] {_trim(b.get('description') or b.get('text') or b, 300)}" for i, b in enumerate(beats)]
        if not beat_lines:
            raise ContextCompileError("outline_incomplete", f"Chapter Outline {outline.id} has no ordered beats", {"outline_card_id": outline.id})
        sections.append(Section("chapter_outline", f"CURRENT CHAPTER PLAN — chapter {chapter_number}: {_trim(oc.get('title'), 80)}", _trim(oc.get("overview"), 1800), mandatory=True, card_ids=[outline.id], revisions=[_rev(outline)], priority=3))
        include(outline, "mandatory")
        sections.append(Section("beats", "ORDERED BEATS (write these, in this order, and nothing beyond the last)", "\n".join(beat_lines), mandatory=True, card_ids=[outline.id], priority=4))
        allowed_outcomes = [str(x) for x in (oc.get("allowed_outcomes") or [])]
        forbidden_outcomes = [str(x) for x in (oc.get("forbidden_outcomes") or [])]
        if allowed_outcomes:
            sections.append(Section("allowed_outcomes", "ALLOWED OUTCOMES OF THIS CHAPTER (PLANNED FACTS)", "\n".join(f"- {x}" for x in allowed_outcomes), mandatory=True, card_ids=[outline.id], priority=5))
        fact_classes["planned"] += allowed_outcomes + [str(b.get("description") or b.get("text") or "") for b in beats]
        for s in (oc.get("setups") or []):
            fact_classes["planned"].append(str(s))
        # Later chapters' outlines are prohibited future outcomes — except beats
        # that the current chapter also plans (recurring scene templates, setups, or overlapping outcomes).
        def _norm_token(t: str) -> str:
            return t.strip("'\"“”‘’").lower()

        planned_norm = {" ".join(str(x).lower().split()) for x in fact_classes["planned"]}
        planned_tokens = [{_norm_token(w) for w in tokenize(str(p).lower()) if len(_norm_token(w)) >= 3 and _norm_token(w) not in _PROHIBITED_STOP} for p in fact_classes["planned"]]

        def _is_planned_here(text: str) -> bool:
            t = " ".join(str(text).lower().split())
            if t in planned_norm:
                return True
            words = {_norm_token(w) for w in tokenize(str(text).lower()) if len(_norm_token(w)) >= 3 and _norm_token(w) not in _PROHIBITED_STOP}
            if not words:
                return False
            for p_words in planned_tokens:
                if not p_words:
                    continue
                exact_overlap = len(words & p_words)
                if exact_overlap >= min(len(words), len(p_words)) * 0.7 or (len(words) >= 3 and exact_overlap >= 3):
                    return True
                stem_overlap = sum(1 for w in words if any(pw == w or (len(w) >= 4 and len(pw) >= 4 and (w.startswith(pw[:4]) or pw.startswith(w[:4]))) for pw in p_words))
                if stem_overlap >= min(len(words), len(p_words)) * 0.7 or (len(words) >= 3 and stem_overlap >= 3):
                    return True
            return False

        for card in self.bible.cards_of_type(project_id, "Chapter Outline"):
            c = _c(card)
            n = int(c.get("chapter_number") or 0)
            if n > chapter_number:
                for b in (c.get("beats") or [])[:3]:
                    desc = b.get("description") or b.get("text") if isinstance(b, dict) else None
                    if desc and not _is_planned_here(desc):
                        forbidden_outcomes.append(f"(ch.{n}) {_trim(desc, 160)}")
                for x in c.get("allowed_outcomes") or []:
                    if not _is_planned_here(x):
                        forbidden_outcomes.append(f"(ch.{n}) {_trim(x, 160)}")
        if forbidden_outcomes:
            seen_f: Set[str] = set()
            deduped: List[str] = []
            for f in forbidden_outcomes:
                key = " ".join(f.split(")", 1)[-1].lower().split()) if f.startswith("(ch.") else " ".join(f.lower().split())
                if key not in seen_f:
                    seen_f.add(key)
                    deduped.append(f)
            forbidden_outcomes = deduped
            prohibited += forbidden_outcomes
        fact_classes["prohibited"] += forbidden_outcomes

        # 9-10. POV and knowledge boundary (mandatory)
        pov_card = by_name.get(pov_name.lower())
        pov_state = {attr: fv.value for (subj, attr), fv in state.items() if subj == pov_name.lower()}
        knows = pov_state.get("knows") or []
        sections.append(Section("pov", "POV (explicit, fixed for the whole chapter)", f"{pov_name} — {_trim((_c(pov_card).get('voice') or {}).get('sentence_tendency') if pov_card else '', 160)}; narrate only what {pov_name} perceives, remembers or infers.", mandatory=True, card_ids=[pov_card.id] if pov_card else [], priority=6))
        boundary_lines = [f"- knows: {_trim(k, 200)}" for k in (knows if isinstance(knows, list) else [knows])][:20]
        for card in self.bible.cards_of_type(project_id, "Knowledge Fact"):
            c = _c(card)
            knowers = {str(k.get("entity", "")).lower(): k for k in (c.get("knowers") or []) if isinstance(k, dict)}
            pv = knowers.get(pov_name.lower())
            reveal = c.get("planned_reveal_chapter")
            pov_knows = pv is not None and pv.get("state") in ("knows", "suspects") and (pv.get("learned_chapter") is None or int(pv.get("learned_chapter")) <= prev_chapter)
            if pov_knows:
                boundary_lines.append(f"- {pv.get('state')}: {_trim(c.get('fact'), 200)}")
            else:
                if isinstance(reveal, int) and reveal <= chapter_number and pv is not None:
                    # Reveal planned in or before this chapter: allowed only if the outline plans it.
                    if reveal == chapter_number:
                        continue
                if pv is not None and pv.get("state") == "false_belief":
                    boundary_lines.append(f"- falsely believes: {_trim(pv.get('false_belief') or c.get('public_belief'), 200)}")
                prohibited.append(f"{_trim(c.get('fact'), 200)} (POV unaware{f'; reveal planned ch.{reveal}' if isinstance(reveal, int) else ''})")
                fact_classes["prohibited"].append(str(c.get("fact") or card.title))
            include(card, "knowledge fact")
        if not boundary_lines:
            boundary_lines = ["- (no recorded knowledge beyond the character card; do not invent backstory knowledge)"]
        sections.append(Section("pov_knowledge_boundary", f"WHAT {pov_name.upper()} KNOWS (knowledge boundary)", "\n".join(boundary_lines), mandatory=True, priority=7))

        # 11. Participant states from canon as-of previous chapter
        state_lines: List[str] = []
        allowed_entities: List[str] = list(resolved)
        for name in resolved:
            card = by_name.get(name.lower())
            attrs = {attr: fv.value for (subj, attr), fv in state.items() if subj == name.lower()}
            if card is not None:
                c = _c(card)
                dd = c.get("dramatic_design") or {}
                voice = c.get("voice") or {}
                rules = c.get("consistency_rules") or {}
                line = f"{name}: role={c.get('role_type')}; goal={_trim(dd.get('external_goal') or c.get('core_drive'), 140)}; need={_trim(dd.get('internal_need'), 100)}; fear={_trim(dd.get('greatest_fear'), 80)}; boundary={_trim(dd.get('moral_boundary'), 100)}"
                if voice:
                    line += f"\n  voice: {_trim(voice.get('sentence_tendency'), 80)}; tells={_trim('; '.join(voice.get('verbal_tells') or []), 120)}; address={_trim('; '.join(voice.get('forms_of_address') or []), 120)}; never says: {_trim('; '.join(voice.get('forbidden_speech') or []), 120)}"
                if rules:
                    line += f"\n  rules: {_trim('; '.join((rules.get('behavioral_rules') or []) + (rules.get('moral_restrictions') or [])), 240)}"
                include(card, "participant")
                allowed_entities += [str(a) for a in (c.get("aliases") or [])]
            else:
                line = f"{name}:"
            if attrs:
                line += "\n  current (locked canon as of ch.%d): " % prev_chapter + "; ".join(f"{k}={_trim(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v, 140)}" for k, v in sorted(attrs.items()) if k != "knows")
                fact_classes["locked_canon"] += [f"{name}.{k}={_trim(json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v, 120)}" for k, v in sorted(attrs.items())]
            state_lines.append(line)
        sections.append(Section("participants", "PARTICIPANT STATES (LOCKED CANON)", "\n".join(state_lines), mandatory=True, priority=8))

        # 12. Relationships
        rel_lines: List[str] = []
        for card in self.bible.cards_of_type(project_id, "Relationship Arc"):
            c = _c(card)
            a, b = str(c.get("character_a", "")), str(c.get("character_b", ""))
            if a.lower() not in names_l and b.lower() not in names_l:
                continue
            subj = f"{a} ↔ {b}".lower()
            vals = {attr: fv.value for (s, attr), fv in state.items() if s == subj}
            rel_lines.append(f"{a} ↔ {b}: private={_trim(vals.get('private_relationship') or c.get('private_relationship'), 140)}; trust {vals.get('trust', c.get('trust'))} affection {vals.get('affection', c.get('affection'))} fear {vals.get('fear', c.get('fear'))} resentment {vals.get('resentment', c.get('resentment'))}; tension: {_trim(c.get('unresolved_tension'), 120)}")
            fact_classes["locked_canon"] += [f"{a}↔{b}.{k}={v}" for k, v in vals.items()]
            include(card, "relationship")
        if rel_lines:
            sections.append(Section("relationships", "RELATIONSHIP STATES (LOCKED CANON)", "\n".join(rel_lines), priority=12))

        # 13-15. Items, locations, organizations relevant to participants
        for type_name, key, title in (("Item Card", "items", "RELEVANT ITEMS AND OWNERSHIP"), ("Scene Card", "locations", "RELEVANT LOCATIONS"), ("Organization Card", "organizations", "RELEVANT ORGANIZATIONS")):
            lines = []
            for card in self.bible.cards_of_type(project_id, type_name):
                c = _c(card)
                name = str(c.get("name") or card.title)
                mentioned = name.lower() in {p.lower() for p in raw_participants} or name.lower() in str(oc.get("overview") or "").lower() or any(name.lower() in str(b.get("description") or "").lower() for b in beats)
                owned = any(isinstance(fv.value, list) and name in fv.value for (s, attr), fv in state.items() if attr == "possesses" and s in names_l)
                if not (mentioned or owned):
                    continue
                desc = _trim(c.get("description"), 160)
                extra = ""
                if type_name == "Item Card":
                    owners = [fv.subject for (s, attr), fv in state.items() if attr == "possesses" and isinstance(fv.value, list) and name in fv.value]
                    extra = f"; owner={', '.join(owners) if owners else _trim(c.get('owner_hint'), 60)}; state={_trim(c.get('current_state'), 80)}"
                lines.append(f"{name}: {desc}{extra}")
                allowed_entities.append(name)
                include(card, key)
            if lines:
                sections.append(Section(key, title, "\n".join(lines), priority=24))

        # 16. World and power rules
        rule_lines = []
        for card in self.bible.cards_of_type(project_id, "World Rule"):
            c = _c(card)
            if c.get("truth_status") == "obsolete":
                dropped.append({"card_id": card.id, "reason": "obsolete"})
                continue
            rule_lines.append(f"- {_trim(c.get('rule'), 160)}; cost: {_trim(c.get('costs'), 80)}; exceptions: {_trim('; '.join(c.get('exceptions') or []), 100)}")
            include(card, "world rule")
            allowed_entities.append(card.title)
            for m in _CAP_NAME.finditer(card.title):
                allowed_entities.append(m.group(0))
        for card in self.bible.cards_of_type(project_id, "Power System"):
            c = _c(card)
            rule_lines.append(f"- power '{c.get('name') or card.title}': restrictions={_trim('; '.join(c.get('restrictions') or []), 160)}; counters={_trim('; '.join(c.get('counters') or []), 100)}")
            include(card, "power system")
            allowed_entities.append(str(c.get("name") or card.title))
        if rule_lines:
            sections.append(Section("rules", "WORLD AND POWER RULES (LOCKED CANON)", "\n".join(rule_lines[:14]), priority=22))
            fact_classes["locked_canon"] += rule_lines[:14]

        # 17-18. Active threads and due promises
        thread_lines = []
        for card in self.bible.cards_of_type(project_id, "Plot Thread"):
            c = _c(card)
            if c.get("status") in ("resolved", "abandoned", "obsolete"):
                continue
            parts = {str(x).lower() for x in (c.get("participants") or [])}
            if not (parts & names_l) and c.get("thread_type") != "main_plot":
                continue
            thread_lines.append(f"- {card.title} ({c.get('thread_type')}, {c.get('urgency')}): {_trim(c.get('central_question'), 140)}; last advanced ch.{c.get('last_advanced_chapter') or c.get('opening_chapter') or 0}")
            include(card, "active thread")
            allowed_entities.append(card.title)
            for m in _CAP_NAME.finditer(card.title):
                allowed_entities.append(m.group(0))
        if thread_lines:
            sections.append(Section("threads", "ACTIVE PLOT THREADS", "\n".join(thread_lines[:10]), priority=26))
        promise_lines = []
        for card in self.bible.cards_of_type(project_id, "Promise Payoff"):
            c = _c(card)
            if c.get("status") in ("paid_off", "subverted", "intentionally_abandoned", "contradicted"):
                paid_ch = c.get("payoff_chapter")
                if c.get("status") == "paid_off" and c.get("planned_payoff") and isinstance(paid_ch, int) and paid_ch < chapter_number:
                    # Already paid off: a second payoff is a duplicate, not a callback.
                    fact_classes["paid_off"].append(str(c.get("planned_payoff")))
                    include(card, "paid-off promise")
                continue
            rng = c.get("target_payoff_range")
            due = isinstance(rng, (list, tuple)) and len(rng) == 2 and isinstance(rng[0], int) and rng[0] <= chapter_number
            parts = {str(x).lower() for x in (c.get("participants") or [])}
            if not due and not (parts & names_l):
                continue
            promise_lines.append(f"- {_trim(c.get('setup') or card.title, 140)} [{c.get('promise_type')}] planned payoff: {_trim(c.get('planned_payoff'), 120)}; window {rng}{' (DUE)' if due else ''}")
            include(card, "promise")
            if not due:
                fact_classes["prohibited"].append(f"payoff of '{_trim(c.get('setup') or card.title, 80)}' before its window {rng}")
        if promise_lines:
            sections.append(Section("promises", "SETUPS AND DUE PAYOFFS", "\n".join(promise_lines[:10]), priority=27))

        # 19. Recent timeline
        events = []
        for card in self.bible.cards_of_type(project_id, "Timeline Event"):
            c = _c(card)
            ch = c.get("chapter_number")
            if isinstance(ch, int) and chapter_number - 5 <= ch <= prev_chapter:
                events.append((ch, f"ch.{ch} {c.get('story_time') or ''} {card.title} @ {c.get('location') or '?'}: {_trim(c.get('action'), 120)}"))
                include(card, "recent timeline")
        events.sort()
        if events:
            sections.append(Section("timeline", "RECENT CANONICAL TIMELINE", "\n".join(e[1] for e in events[-6:]), priority=28))

        # 20-22. Previous chapter summary, bounded tail, scene state (from the state packet)
        packet = self._state_packet(project_id, prev_chapter)
        if chapter_number > 1:
            if packet is None:
                raise ContextCompileError("state_packet_missing", f"Next Chapter State Packet for chapter {prev_chapter} is missing; synchronize chapter {prev_chapter} first")
            sections.append(Section("previous_summary", f"PREVIOUS CHAPTER {prev_chapter} SUMMARY", _trim(packet.get("summary"), 900), mandatory=True, priority=9))
            prev_text_card = self._chapter_text_card(project_id, prev_chapter)
            if prev_text_card is not None:
                tail = _tail(str(_c(prev_text_card).get("content") or ""), 1200)
                sections.append(Section("previous_tail", f"PREVIOUS CHAPTER {prev_chapter} — FINAL LINES (continue after these; do not repeat)", tail, priority=10, card_ids=[prev_text_card.id], revisions=[_rev(prev_text_card)]))
            scene = packet.get("scene_state") or {}
            sections.append(Section("scene_state", "CURRENT SCENE STATE AT CHAPTER START", f"location: {scene.get('ending_location')}; time: {scene.get('current_time')}; present: {', '.join(scene.get('participants') or [])}; unresolved action: {_trim(scene.get('unresolved_immediate_action'), 160)}; open dialogue obligation: {_trim(scene.get('open_dialogue_obligation'), 160)}; physical: {_trim(json.dumps(scene.get('physical_states') or {}, ensure_ascii=False), 240)}; emotional: {_trim(json.dumps(scene.get('emotional_states') or {}, ensure_ascii=False), 240)}", mandatory=True, priority=11))
            for c in packet.get("next_chapter_constraints") or []:
                fact_classes["planned"].append(str(c))

        # 23. Reference examples for the outline's beat functions (technique only)
        beat_functions = example_lib.functions_from_outline(oc)
        source_pid = manifest.source_project_id
        retrieved: List[Dict[str, Any]] = []
        trace: Dict[str, Any] = {"requested_functions": beat_functions, "selected": [], "skipped": [], "reason": "no source project linked"}
        recent: Set[str] = set(recently_used_examples)
        for row in self.session.exec(select(ChapterPipelineRun).where(ChapterPipelineRun.project_id == project_id, ChapterPipelineRun.status == "committed", ChapterPipelineRun.chapter_number < chapter_number).order_by(ChapterPipelineRun.id.desc()).limit(3)).all():
            recent.update((row.context_manifest or {}).get("retrieved_example_ids") or [])
        if source_pid and beat_functions and example_budget_chars > 0:
            req = example_lib.RetrievalRequest(
                functions=beat_functions, language=fp.get("language") or None, pov_type=(fp.get("layers") or {}).get("pov_focalization", {}).get("features", {}).get("pov"),
                beat_descriptions={str(b.get("function")): str(b.get("description") or "") for b in beats if b.get("function")},
                target_dialogue_ratio=((fp.get("targets") or {}).get("dialogue_ratio") or {}).get("median"),
                max_chapter_number=oc.get("max_reference_chapter"), recently_used=recent, budget_chars=example_budget_chars,
            )
            examples, trace = example_lib.retrieve(self.session, source_pid, req)
            retrieved = [e.as_dict() for e in examples]
            if retrieved:
                ex_lines = [f"({i + 1}) function={e['beat_function']} [source ch.{e['chapter_number']}, id {e['example_id']}]\n{e['excerpt']}" for i, e in enumerate(retrieved)]
                sections.append(Section("examples", "REFERENCE TECHNIQUE EXAMPLES — technique demonstrations ONLY. Their characters ([ROLE:*]), places, events and wording are NOT part of this story and must not appear in your prose. Study rhythm, beat execution and restraint; then write completely original sentences.", "\n\n".join(ex_lines), priority=40))

        # 24. Compact fingerprint (mandatory)
        sections.append(Section("fingerprint", "NARRATIVE FINGERPRINT (measurable targets; match these, never imitate any author)", compact_fingerprint(fp, functions=beat_functions), mandatory=True, card_ids=[fingerprint.id], revisions=[_rev(fingerprint)], priority=13))
        include(fingerprint, "mandatory")

        # 25-26. Anti-hallucination and originality constraints (mandatory)
        sections.append(Section("anti_hallucination", "FACT CLASSES AND HARD CONSTRAINTS", (
            "LOCKED CANON: use as given; never alter unless a beat above explicitly changes it.\n"
            "PLANNED FACTS: only the ordered beats and allowed outcomes above may happen; do not advance later beats.\n"
            "PROHIBITED FACTS: everything under PROHIBITED below, all future-chapter outcomes, all knowledge outside the POV boundary, and any source-novel fact.\n"
            "FLEXIBLE DETAILS (may be invented, never persist): " + "; ".join(FLEXIBLE_DETAIL_POLICY["allowed"]) + ".\n"
            "REQUIRES CANON OR OUTLINE (never invent): " + "; ".join(FLEXIBLE_DETAIL_POLICY["requires_canon_or_outline"]) + ".\n"
            "UNKNOWN stays unknown: if a fact is not supplied, write around it with neutral wording instead of inventing it.\n"
            f"Named entities allowed in this chapter: {', '.join(sorted(set(allowed_entities)))}. At most two unnamed, background-only extras."
        ), mandatory=True, priority=14))
        sections.append(Section("originality", "ORIGINALITY REQUIREMENTS", "All wording, imagery, names, dialogue and scene details must be original. Reference examples demonstrate technique only; reproducing their phrases, entities or events is a failure. Do not paraphrase example sentences.", mandatory=True, priority=15))
        if prohibited:
            sections.append(Section("prohibited", "PROHIBITED SOURCE / FUTURE CONTENT (never reveal, hint or foreshadow)", "\n".join(f"- {p}" for p in prohibited[:40]), mandatory=True, priority=16))
        else:
            sections.append(Section("prohibited", "PROHIBITED SOURCE / FUTURE CONTENT", "- (no additional prohibited facts recorded)", mandatory=True, priority=16))

        # 27. Word target and pacing
        target = int(word_target or oc.get("word_target") or ((fp.get("targets") or {}).get("unit_count") or {}).get("median") or 2500)
        sections.append(Section("word_target", "LENGTH AND PACING TARGET", f"≈{target} {'eojeol' if fp.get('language') == 'ko' else 'words'} (±15%); {len(beats)} beats; opening type and ending type per fingerprint.", mandatory=True, priority=17))

        # Budget: mandatory sections first; drop optional lowest-priority sections until it fits.
        sections.sort(key=lambda s: s.priority)
        mandatory_chars = sum(s.chars for s in sections if s.mandatory)
        effective_budget = max(budget_chars, mandatory_chars + 1000)
        total = sum(s.chars for s in sections)
        while total > effective_budget:
            candidates = [s for s in sections if not s.mandatory]
            if not candidates:
                break
            victim = max(candidates, key=lambda s: s.priority)
            sections.remove(victim)
            dropped.append({"section": victim.key, "reason": "budget", "chars": victim.chars})
            total = sum(s.chars for s in sections)
        for key in MANDATORY_SECTIONS:
            if key not in {s.key for s in sections}:
                raise ContextCompileError("mandatory_section_missing", f"Mandatory section '{key}' missing after compilation")

        ctx = CompiledChapterContext(
            project_id=project_id, chapter_number=chapter_number, outline_card_id=outline.id, pov=pov_name, participants=resolved, sections=sections,
            manifest={}, fact_classes={k: sorted(set(v)) for k, v in fact_classes.items()}, prohibited=prohibited, allowed_entities=sorted(set(allowed_entities)),
            retrieved_examples=retrieved, beat_functions=beat_functions, word_target=target, canon_revision=canon_rev,
        )
        text = ctx.prompt_text()
        ctx.context_hash = sha256_text(text)
        ctx.manifest = {
            "compiler_version": COMPILER_VERSION,
            "included_cards": included,
            "included_revisions": sorted({i["revision"] for i in included}),
            "evidence_ids": [(fp.get("layers") or {}).get("evidence_index", {}).get("features", {}).get("observation_ids", [])[:50]][0],
            "retrieved_example_ids": [e["example_id"] for e in retrieved],
            "retrieval_trace": trace,
            "dropped": dropped,
            "budget_chars": budget_chars,
            "used_chars": len(text),
            "canon_revision": canon_rev,
            "outline_revision": manifest.outline_revision,
            "fingerprint_revision": manifest.fingerprint_revision,
            "fingerprint_dependency_hash": fp.get("dependency_hash"),
            "context_hash": ctx.context_hash,
            "beat_functions": beat_functions,
            "pov": pov_name,
            "participants": resolved,
        }
        # Record the context as an artifact whose upstream is every included card.
        ups = [provenance.Upstream(kind=i["card_type"], key=str(i["card_id"]), hash=i["revision"].split("@")[1]) for i in included]
        provenance.record(self.session, project_id=project_id, artifact_kind="chapter_context", artifact_key=str(chapter_number), content={"hash": ctx.context_hash}, upstream=ups, producer=COMPILER_VERSION, schema_version=COMPILER_VERSION)
        return ctx

    # --------------------------------------------------------------- helpers
    def _entity_card(self, project_id: int, key: str) -> Optional[Card]:
        for type_name in ("Organization Card", "Scene Card", "Item Card", "Concept Card"):
            for card in self.bible.cards_of_type(project_id, type_name):
                if str(_c(card).get("name") or card.title).strip().lower() == key:
                    return card
        return None

    def _state_packet(self, project_id: int, chapter_number: int) -> Optional[Dict[str, Any]]:
        if chapter_number < 1:
            return None
        for card in self.bible.cards_of_type(project_id, "Chapter State Packet"):
            if int(_c(card).get("chapter_number") or 0) == int(chapter_number):
                return _c(card)
        return None


def _tail(text: str, chars: int) -> str:
    paras = split_paragraphs(text)
    out: List[str] = []
    total = 0
    for p in reversed(paras):
        if total + len(p) > chars and out:
            break
        out.insert(0, p)
        total += len(p) + 1
    return "\n".join(out)


__all__ = ["COMPILER_VERSION", "FACT_CLASSES", "FLEXIBLE_DETAIL_POLICY", "MANDATORY_SECTIONS", "ChapterContextCompiler", "CompiledChapterContext", "ContextCompileError", "Section"]
