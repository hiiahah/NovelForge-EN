"""Continuity Guard: check a chapter draft against the Bible and Story Memory.

Deterministic checks (no LLM, always run):
- ``prohibited_reveal``   compiled prohibited-knowledge list surfaces in prose
- ``dead_entity``         an entity recorded dead acts/speaks in the draft
- ``unknown_entity``      recurring proper name not in Bible, digests or extras
- ``head_hopping``        interior state of a non-POV participant narrated
- ``time_inversion``      time-of-day markers run backwards without a day change
- ``possession_conflict`` draft mentions an item an entity no longer holds
- ``location_teleport``   POV opens somewhere else than the previous ending without travel words
- ``dropped_strong_hook`` strong hook opened last chapter and neither mentioned nor in outline
- ``forbidden_outcome``   outline's forbidden_outcomes appear (keyword match)

Optional LLM pass asks a model for contradictions against the compiled memory
and returns cited issues; deterministic findings are never removed by it.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger
from sqlmodel import Session

from app.schemas.story_memory import ContinuityIssue, ContinuityReport, LlmContinuityFindings
from app.services import prompt_service
from app.services.ai.core import llm_service
from app.services.bible.bible_service import BibleService
from app.services.bible.context_compiler import ContextCompiler
from app.services.forge.claims import named_entities
from app.services.forge.textmetrics import detect_language, split_sentences, tokenize
from app.services.forge.validators import _PROHIBITED_STOP, validate_pov, validate_temporal
from app.services.story_memory.digest_service import DigestService
from app.services.story_memory.story_so_far import StorySoFarCompiler

PROMPT_NAME = "Continuity Guard"

_SEVERITY_COST = {"info": 0, "low": 3, "medium": 8, "high": 15, "critical": 30}
_TRAVEL_WORDS = re.compile(r"\b(travel|travell|journey|rode|ride|walked|marched|sailed|flew|arrived|reached|left|departed|days? later|hours? later|by the time|after (?:a|the) (?:long|short)|road|carriage|horse|ship|gate|crossed)\w*", re.I)
_DEATH_WORDS = re.compile(r"\b(dead|died|killed|deceased|slain|perished|corpse|funeral|buried)\b", re.I)
_ACTION_VERBS = re.compile(r"\b(said|asked|replied|whispered|shouted|laughed|nodded|walked|stood|stepped|looked|smiled|grabbed|drew|struck|turned|answered|muttered|grinned|frowned)\b", re.I)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _c(card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _find(prose: str, needle: str) -> Optional[Tuple[int, int]]:
    m = re.search(re.escape(needle), prose, re.I)
    return (m.start(), m.end()) if m else None


def _excerpt(prose: str, span: Optional[Tuple[int, int]], width: int = 140) -> str:
    if not span:
        return ""
    a = max(0, span[0] - width // 2)
    b = min(len(prose), span[1] + width // 2)
    return ("…" if a > 0 else "") + prose[a:b].replace("\n", " ") + ("…" if b < len(prose) else "")


class ContinuityGuard:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)
        self.digests = DigestService(session)
        self.recap = StorySoFarCompiler(session)

    # ------------------------------------------------------------ vocabulary
    def _known_names(self, project_id: int) -> Set[str]:
        names: Set[str] = set()
        for t in ("Character Card", "Scene Card", "Organization Card", "Item Card", "Concept Card"):
            for card in self.bible.cards_of_type(project_id, t):
                c = _c(card)
                for n in [c.get("name"), card.title, *(c.get("aliases") or [])]:
                    if n and str(n).strip():
                        names.add(_norm(n))
        for d in self.digests.fresh_digests(project_id):
            for n in [*d.participants, *d.named_extras, *d.objects_introduced, *d.locations]:
                if n:
                    names.add(_norm(n))
        return names

    # ---------------------------------------------------------------- checks
    def check(
        self,
        *,
        project_id: int,
        draft: str,
        chapter_number: Optional[int] = None,
        participants: Optional[List[str]] = None,
        pov: Optional[str] = None,
        outline: Optional[Dict[str, Any]] = None,
    ) -> ContinuityReport:
        draft = draft or ""
        digests = [d for d in self.digests.fresh_digests(project_id) if chapter_number is None or d.chapter_number < chapter_number]
        chapter = chapter_number or (self.bible.current_chapter_number(project_id) + 1)
        participants = [p for p in (participants or []) if p and str(p).strip()]
        lang = detect_language(draft)
        issues: List[ContinuityIssue] = []
        checks: List[str] = []

        compiled = ContextCompiler(self.session).compile(project_id=project_id, chapter_number=chapter, participants=participants, pov=pov, budget_chars=12000)
        pov_name = pov or (participants[0] if participants else "")

        # 1. Prohibited reveals + head hopping (reuse Forge validators).
        checks.append("prohibited_reveal")
        checks.append("head_hopping")
        for issue in validate_pov(draft, pov=pov_name, others=[p for p in participants if _norm(p) != _norm(pov_name)], prohibited=compiled.prohibited, language=lang):
            code = "prohibited_reveal" if issue.code == "forbidden_reveal" else issue.code
            sev = "critical" if code == "prohibited_reveal" else "medium"
            issues.append(ContinuityIssue(code=code, severity=sev, message=issue.message, excerpt=_excerpt(draft, issue.span), span=list(issue.span) if issue.span else None, suggestion=issue.hint))

        # 2. Time inversion.
        checks.append("time_inversion")
        for issue in validate_temporal(draft, language=lang):
            issues.append(ContinuityIssue(code="time_inversion", severity="low", message=issue.message, excerpt=_excerpt(draft, issue.span), span=list(issue.span) if issue.span else None, suggestion=issue.hint))

        # 3. Carry-forward state: dead entities, possessions, teleport.
        carry = StorySoFarCompiler.carry_forward(digests)
        checks.append("dead_entity")
        checks.append("possession_conflict")
        for ent in carry:
            if not ent.alive:
                for m in re.finditer(rf"\b{re.escape(ent.entity)}\b\s+(?:\w+\s+){{0,2}}{_ACTION_VERBS.pattern}", draft, re.I):
                    window = draft[max(0, m.start() - 200): m.end() + 60]
                    if _DEATH_WORDS.search(window) or re.search(r"\b(ghost|memory|remembered|dream|vision|spirit|had once|used to)\b", window, re.I):
                        continue
                    issues.append(ContinuityIssue(code="dead_entity", severity="critical", message=f"'{ent.entity}' was recorded dead (ch.{ent.last_seen_chapter}) but acts in this draft", excerpt=_excerpt(draft, (m.start(), m.end())), span=[m.start(), m.end()], suggestion="Remove the action, frame it as memory/vision, or update the Bible if the death was reversed"))
                    break
            lost = ent.states.get("possession", "")
            if lost and re.search(r"\b(lost|stolen|gave away|destroyed|broken|no longer|dropped|surrendered|taken)\b", lost, re.I):
                item_terms = [t for t in tokenize(lost) if len(t) >= 4 and t not in _PROHIBITED_STOP and t not in ("lost", "stolen", "gave", "away", "destroyed", "broken", "longer", "dropped", "surrendered", "taken")]
                for term in item_terms[:3]:
                    rx = re.compile(rf"\b{re.escape(ent.entity)}\b[^.!?\n]{{0,80}}\b(?:drew|held|raised|gripped|used|swung|wielded|clutched|drawing)\b[^.!?\n]{{0,40}}\b{re.escape(term)}\b", re.I)
                    m = rx.search(draft)
                    if m:
                        issues.append(ContinuityIssue(code="possession_conflict", severity="high", message=f"'{ent.entity}' uses '{term}' but memory says: {lost}", excerpt=_excerpt(draft, (m.start(), m.end())), span=[m.start(), m.end()], suggestion="Reconcile with the recorded loss or show how it was recovered"))
                        break

        checks.append("location_teleport")
        if digests and pov_name:
            last = digests[-1]
            last_loc = ""
            for sc in last.state_changes:
                if sc.kind == "location" and _norm(sc.entity) == _norm(pov_name):
                    last_loc = sc.after
            if last_loc:
                opening = draft[:1500]
                loc_terms = [t for t in tokenize(last_loc) if len(t) >= 4 and t not in _PROHIBITED_STOP]
                mentions_last = any(re.search(rf"\b{re.escape(t)}\b", opening, re.I) for t in loc_terms)
                other_locs = {_norm(l) for d in digests for l in d.locations if _norm(l) != _norm(last_loc)}
                other_hit = next((l for l in other_locs if l and re.search(rf"\b{re.escape(l)}\b", opening, re.I)), None)
                if other_hit and not mentions_last and not _TRAVEL_WORDS.search(opening) and not re.search(r"\b(meanwhile|elsewhere|far away|across the)\b", opening, re.I):
                    span = _find(opening, other_hit)
                    issues.append(ContinuityIssue(code="location_teleport", severity="medium", message=f"Previous chapter left {pov_name} at '{last_loc}'; this draft opens at '{other_hit}' with no transition", excerpt=_excerpt(draft, span), span=list(span) if span else None, suggestion="Add a transition (travel, time skip or scene marker) or start where the last chapter ended"))

        # 4. Unknown recurring entities.
        checks.append("unknown_entity")
        known = self._known_names(project_id)
        known_tokens = {t for n in known for t in n.split() if len(t) >= 3}
        allowed = {_norm(p) for p in participants} | known
        for name, spans in named_entities(draft, lang).items():
            n = _norm(name)
            parts = n.split()
            if n in allowed or any(p in allowed for p in parts) or all(p in known_tokens for p in parts):
                continue
            if len(spans) >= 3:
                issues.append(ContinuityIssue(code="unknown_entity", severity="medium", message=f"Recurring proper name '{name}' ({len(spans)}×) is not in the Bible or any digest", excerpt=_excerpt(draft, spans[0]), span=list(spans[0]), suggestion="Create a card for it, add it as a named extra in the digest, or make it an unnamed extra"))

        # 5. Dropped strong hook from the immediately previous chapter.
        checks.append("dropped_strong_hook")
        if digests:
            prev = digests[-1]
            outline_text = _norm(json.dumps(outline or {}, ensure_ascii=False))
            draft_l = _norm(draft)
            for h in prev.hooks_opened:
                if h.strength != "strong" or _norm(h.expected_payoff_window) not in ("next chapter", "immediately", "next scene", "the next chapter"):
                    continue
                terms = [t for t in tokenize(h.hook) if len(t) >= 4 and t not in _PROHIBITED_STOP]
                if not terms:
                    continue
                hits = sum(1 for t in terms if t in draft_l or t in outline_text)
                if hits < max(1, len(terms) // 3):
                    issues.append(ContinuityIssue(code="dropped_strong_hook", severity="high", message=f"Strong hook from ch.{prev.chapter_number} expected '{h.expected_payoff_window}' is not addressed: {h.hook}", suggestion="Address it, at least acknowledge it, or downgrade its expected window in the digest"))

        # 6. Outline forbidden outcomes.
        checks.append("forbidden_outcome")
        for fo in (outline or {}).get("forbidden_outcomes") or []:
            terms = [t for t in tokenize(str(fo)) if len(t) >= 4 and t not in _PROHIBITED_STOP]
            if len(terms) < 2:
                continue
            sents = split_sentences(draft, lang)
            for i in range(len(sents)):
                window = _norm(" ".join(sents[i:i + 2]))
                matched = [t for t in terms if re.search(rf"\b{re.escape(t)}\w*", window)]
                if len(matched) >= max(2, int(len(terms) * 0.6 + 0.5)):
                    span = _find(draft, sents[i][:60])
                    issues.append(ContinuityIssue(code="forbidden_outcome", severity="high", message=f"Outline forbids this outcome here: {fo}", excerpt=_excerpt(draft, span), span=list(span) if span else None, suggestion="Move this outcome to the chapter where it is planned"))
                    break

        return self._finish(project_id, chapter, draft, issues, checks)

    @staticmethod
    def _finish(project_id: int, chapter: int, draft: str, issues: List[ContinuityIssue], checks: List[str]) -> ContinuityReport:
        # De-duplicate by (code, message).
        seen: Set[Tuple[str, str]] = set()
        unique: List[ContinuityIssue] = []
        for i in issues:
            key = (i.code, i.message)
            if key in seen:
                continue
            seen.add(key)
            unique.append(i)
        order = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
        unique.sort(key=lambda i: (order.get(i.severity, 5), i.span[0] if i.span else 10**9))
        score = max(0, 100 - sum(_SEVERITY_COST.get(i.severity, 0) for i in unique))
        verdict = "block" if any(i.severity == "critical" for i in unique) else ("review" if any(i.severity in ("high", "medium") for i in unique) else "clean")
        return ContinuityReport(project_id=project_id, chapter_number=chapter, checked_chars=len(draft), issues=unique, score=score, verdict=verdict, checks_run=checks)

    # ------------------------------------------------------------- LLM pass
    async def check_with_llm(
        self,
        *,
        project_id: int,
        draft: str,
        llm_config_id: int,
        chapter_number: Optional[int] = None,
        participants: Optional[List[str]] = None,
        pov: Optional[str] = None,
        outline: Optional[Dict[str, Any]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> ContinuityReport:
        report = self.check(project_id=project_id, draft=draft, chapter_number=chapter_number, participants=participants, pov=pov, outline=outline)
        prompt = prompt_service.get_prompt_by_name(self.session, PROMPT_NAME)
        if not prompt or not prompt.template:
            raise ValueError(f"Prompt not found: {PROMPT_NAME}")
        recap = self.recap.compile(project_id, next_chapter=report.chapter_number, budget_chars=14000)
        compiled = ContextCompiler(self.session).compile(project_id=project_id, chapter_number=report.chapter_number, participants=participants or [], pov=pov, budget_chars=9000)
        system_prompt = prompt_service.inject_knowledge(self.session, str(prompt.template)) + (
            "\n\nOutput strictly as JSON matching this schema:\n" + json.dumps(LlmContinuityFindings.model_json_schema(), ensure_ascii=False)
        )
        user_prompt = "\n\n".join([
            f"Chapter being checked: {report.chapter_number}; POV: {pov or '(unknown)'}; participants: {', '.join(participants or []) or '(unknown)'}",
            "[Story memory]\n" + (recap.text or "(no digests yet)"),
            "[Bible slice]\n" + (compiled.as_text() or "(empty)"),
            "[Chapter outline]\n" + (json.dumps(outline, ensure_ascii=False) if outline else "(none)"),
            "[Draft]\n" + draft,
        ])
        try:
            result = await llm_service.generate_structured(
                session=self.session, llm_config_id=llm_config_id, user_prompt=user_prompt, output_type=LlmContinuityFindings,
                system_prompt=system_prompt, temperature=temperature if temperature is not None else 0.1, max_tokens=max_tokens, timeout=timeout,
            )
            findings = result if isinstance(result, LlmContinuityFindings) else LlmContinuityFindings.model_validate(result)
        except Exception as exc:
            logger.warning(f"[ContinuityGuard] LLM pass failed: {exc}")
            report.issues.append(ContinuityIssue(code="llm_pass_failed", severity="info", message=f"LLM continuity pass failed: {exc}", source="llm"))
            return report
        for issue in findings.issues:
            issue.source = "llm"
            if issue.excerpt and not issue.span:
                span = _find(draft, issue.excerpt[:80])
                issue.span = list(span) if span else None
            report.issues.append(issue)
        for note in findings.notes[:6]:
            report.issues.append(ContinuityIssue(code="note", severity="info", message=note, source="llm"))
        report.checks_run.append("llm_contradictions")
        return self._finish(project_id, report.chapter_number, draft, report.issues, report.checks_run)
