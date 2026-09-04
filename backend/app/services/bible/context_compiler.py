"""Context Compiler: select the minimum relevant Bible slice for one chapter.

Selection is deterministic and explainable. Each emitted block records *why* it
was selected (participant match, due promise, POV knowledge...) so the user can
audit what the model was told. Ranking prioritises relevance, chronology,
truth status and confidence, then trims to a character budget.

The compiled text is exposed as the ``@bible.*`` tokens of the context DSL and
merged into the facts block used by chapter continuation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

from sqlmodel import Session

from app.db.models import Card
from app.services.bible.bible_service import BibleService

_TRUTH_RANK = {"canon": 0, "believed": 1, "planned": 2, "inferred": 3, "disputed": 4, "obsolete": 9}


@dataclass
class CompiledBlock:
    section: str
    title: str
    text: str
    reason: str
    truth_status: Optional[str] = None
    confidence: Optional[float] = None
    card_id: Optional[int] = None
    priority: int = 50  # lower = more important


@dataclass
class CompiledContext:
    blocks: List[CompiledBlock] = field(default_factory=list)
    prohibited: List[str] = field(default_factory=list)
    budget_chars: int = 0
    used_chars: int = 0
    dropped: int = 0

    def as_text(self) -> str:
        sections: Dict[str, List[str]] = {}
        for b in self.blocks:
            sections.setdefault(b.section, []).append(b.text)
        parts: List[str] = []
        for section, lines in sections.items():
            parts.append(f"[{section}]")
            parts.extend(lines)
            parts.append("")
        if self.prohibited:
            parts.append("[Prohibited future information: the POV character and the reader do NOT yet know these facts; never reveal or hint at them]")
            parts.extend(f"- {p}" for p in self.prohibited)
        return "\n".join(parts).strip()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.as_text(),
            "blocks": [b.__dict__ for b in self.blocks],
            "prohibited": self.prohibited,
            "budget_chars": self.budget_chars,
            "used_chars": self.used_chars,
            "dropped": self.dropped,
        }


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _lower_set(values: Any) -> Set[str]:
    if not isinstance(values, list):
        return set()
    return {str(v).strip().lower() for v in values if str(v).strip()}


class ContextCompiler:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)

    def compile(
        self,
        *,
        project_id: int,
        chapter_number: Optional[int],
        participants: List[str],
        pov: Optional[str] = None,
        budget_chars: int = 6000,
        chapter_goal: Optional[str] = None,
    ) -> CompiledContext:
        participants = [p for p in (participants or []) if p and str(p).strip()]
        chapter = chapter_number or self.bible.current_chapter_number(project_id) + 1
        blocks: List[CompiledBlock] = []
        prohibited: List[str] = []

        # Alias resolution: build alias/name -> canonical name from Character
        # Cards, so ledgers that reference a nickname or alias still match.
        alias_map: Dict[str, str] = {}
        for card in self.bible.cards_of_type(project_id, "Character Card"):
            c = _c(card)
            canonical = str(c.get("name") or card.title).strip().lower()
            if not canonical:
                continue
            alias_map.setdefault(canonical, canonical)
            for alias in _lower_set(c.get("aliases")):
                alias_map.setdefault(alias, canonical)

        def canon(name: Any) -> str:
            key = str(name or "").strip().lower()
            return alias_map.get(key, key)

        names = {canon(p) for p in participants}
        # An explicit POV is always a participant of its own chapter, even when
        # the caller forgot to list it.
        pov_name = canon(pov or (participants[0] if participants else ""))
        if pov_name:
            names.add(pov_name)

        # 1. Reader contract + style: always relevant, compact.
        contract = self.bible.singleton(project_id, "Reader Contract")
        if contract:
            c = _c(contract)
            blocks.append(CompiledBlock(
                section="Reader Contract", title=contract.title, priority=10, card_id=contract.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="Always included: defines the reward this chapter must serve",
                text=(
                    f"Primary fantasy: {_trim(c.get('primary_fantasy'), 200)} | Primary reward: {_trim(c.get('primary_emotional_reward'), 200)} | "
                    f"Reward priority: {', '.join(c.get('reward_types_priority') or [])} | Protagonist rules: {_trim('; '.join(c.get('expected_protagonist_behavior') or []), 300)} | "
                    f"Never: {_trim('; '.join(c.get('violations') or []), 300)}"
                ),
            ))
        style = self.bible.singleton(project_id, "Style Profile")
        if style:
            s = _c(style)
            blocks.append(CompiledBlock(
                section="Style Profile", title=style.title, priority=12, card_id=style.id,
                truth_status=s.get("truth_status"), confidence=s.get("confidence"),
                reason="Always included: voice constraints for prose generation",
                text=(
                    f"POV {s.get('pov_mode')} ({s.get('pov_distance')}), tense {s.get('tense')}; narrator: {_trim(s.get('narrator_personality'), 120)}; "
                    f"sentences: {_trim(s.get('sentence_tendency'), 120)}; exposition: {_trim(s.get('exposition_method'), 120)}; "
                    f"endings: {_trim(s.get('chapter_ending_style'), 120)}; techniques: {_trim('; '.join(s.get('signature_techniques') or []), 300)}; "
                    f"avoid: {_trim('; '.join((s.get('forbidden_cliches') or []) + (s.get('unwanted_ai_patterns') or [])), 300)}"
                ),
            ))
        theme = self.bible.singleton(project_id, "Theme Map")
        if theme:
            t = _c(theme)
            blocks.append(CompiledBlock(
                section="Theme", title=theme.title, priority=30, card_id=theme.id,
                truth_status=t.get("truth_status"), confidence=t.get("confidence"),
                reason="Theme question to dramatize through decisions and costs",
                text=f"{_trim(t.get('theme_question'), 160)} — protagonist currently believes: {_trim(t.get('protagonist_initial_belief'), 160)}; movement: {_trim(t.get('planned_movement'), 200)}",
            ))

        # 2. Character consistency for participants.
        for card in self.bible.cards_of_type(project_id, "Character Card"):
            c = _c(card)
            cname = canon(c.get("name") or card.title)
            aliases = _lower_set(c.get("aliases"))
            if names and cname not in names:
                continue
            dd = c.get("dramatic_design") or {}
            voice = c.get("voice") or {}
            rules = c.get("consistency_rules") or {}
            is_pov = bool(pov_name) and cname == pov_name
            lines = [f"{card.title}: goal={_trim(dd.get('external_goal') or c.get('core_drive'), 160)}; need={_trim(dd.get('internal_need'), 120)}; false_belief={_trim(dd.get('false_belief'), 120)}; fear={_trim(dd.get('greatest_fear'), 100)}; boundary={_trim(dd.get('moral_boundary'), 120)}"]
            if voice:
                lines.append(f"  voice: {_trim(voice.get('sentence_tendency'), 80)}; tells={_trim('; '.join(voice.get('verbal_tells') or []), 160)}; address={_trim('; '.join(voice.get('forms_of_address') or []), 120)}; never says: {_trim('; '.join(voice.get('forbidden_speech') or []), 160)}")
            if rules:
                lines.append(f"  rules: {_trim('; '.join((rules.get('behavioral_rules') or []) + (rules.get('moral_restrictions') or [])), 300)}")
                kr = rules.get("knowledge_restrictions") or []
                if kr:
                    lines.append(f"  knowledge limits: {_trim('; '.join(kr), 300)}")
            blocks.append(CompiledBlock(
                section="Characters", title=card.title, priority=15 if is_pov else 20, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="POV character" if is_pov else "Participant in this chapter",
                text="\n".join(lines),
            ))

        # 3. Relationships between participants.
        for card in self.bible.cards_of_type(project_id, "Relationship Arc"):
            c = _c(card)
            a, b = canon(c.get("character_a", "")), canon(c.get("character_b", ""))
            if names and not ({a, b} & names):
                continue
            both = bool(names) and a in names and b in names
            blocks.append(CompiledBlock(
                section="Relationships", title=card.title, priority=22 if both else 35, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="Both characters present" if both else "One participant involved",
                text=(
                    f"{card.title}: public={_trim(c.get('public_relationship'), 100)} / private={_trim(c.get('private_relationship'), 120)}; "
                    f"trust {c.get('trust')} affection {c.get('affection')} fear {c.get('fear')} dependency {c.get('dependency')} resentment {c.get('resentment')}; "
                    f"A thinks: {_trim(c.get('a_belief_about_b'), 120)}; B thinks: {_trim(c.get('b_belief_about_a'), 120)}; tension: {_trim(c.get('unresolved_tension'), 140)}; "
                    f"next planned shift: {_trim(c.get('planned_next_shift'), 140)}"
                ),
            ))

        # 4. Active plot threads, most urgent / most neglected first.
        for card in self.bible.cards_of_type(project_id, "Plot Thread"):
            c = _c(card)
            if c.get("status") in ("resolved", "abandoned", "obsolete"):
                continue
            parts = {canon(x) for x in _lower_set(c.get("participants"))}
            overlap = bool(parts & names) if names else True
            main = c.get("thread_type") == "main_plot"
            if not overlap and not main:
                continue
            last = c.get("last_advanced_chapter") or c.get("opening_chapter") or 0
            gap = (chapter - last) if isinstance(last, int) else 0
            urgency_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}.get(str(c.get("urgency")), 2)
            next_ms = next((m for m in (c.get("milestones") or []) if isinstance(m, dict) and m.get("status") == "planned"), None)
            blocks.append(CompiledBlock(
                section="Active Threads", title=card.title, priority=25 + urgency_rank - min(gap // 5, 3), card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason=("Main plot" if main else "Participants involved") + (f"; not advanced for {gap} chapters" if gap >= 5 else ""),
                text=f"{card.title} ({c.get('thread_type')}, {c.get('urgency')}): {_trim(c.get('central_question'), 140)}; last advanced ch.{last}; next planned: {_trim(next_ms.get('description') if next_ms else '', 160)}",
            ))

        # 5. Promises due or open with participants.
        for card in self.bible.cards_of_type(project_id, "Promise Payoff"):
            c = _c(card)
            if c.get("status") in ("paid_off", "subverted", "intentionally_abandoned", "contradicted"):
                continue
            parts = {canon(x) for x in _lower_set(c.get("participants"))}
            rng = c.get("target_payoff_range")
            due = isinstance(rng, (list, tuple)) and len(rng) == 2 and isinstance(rng[0], int) and rng[0] <= chapter
            overdue = isinstance(rng, (list, tuple)) and len(rng) == 2 and isinstance(rng[1], int) and chapter > rng[1]
            overlap = bool(parts & names) if names else True
            if not due and not overlap:
                continue
            reason = "Overdue payoff" if overdue else ("Payoff window open" if due else "Participants involved")
            blocks.append(CompiledBlock(
                section="Open Promises", title=card.title, priority=18 if overdue else (24 if due else 40), card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason=reason,
                text=f"{_trim(c.get('setup') or card.title, 140)} [{c.get('promise_type')}, {c.get('status')}] intended: {_trim(c.get('intended_interpretation'), 120)}; planned payoff: {_trim(c.get('planned_payoff'), 140)}; window {rng}",
            ))

        # 6. Knowledge: what the POV knows vs prohibited future information.
        for card in self.bible.cards_of_type(project_id, "Knowledge Fact"):
            c = _c(card)
            knowers = [k for k in (c.get("knowers") or []) if isinstance(k, dict)]
            involved = {canon(k.get("entity", "")) for k in knowers}
            if names and not (involved & names) and c.get("sensitivity") != "high":
                continue
            pov_state = next((k for k in knowers if canon(k.get("entity", "")) == pov_name), None) if pov_name else None
            reader_state = c.get("reader_state") or "unaware"
            reveal = c.get("planned_reveal_chapter")
            future = isinstance(reveal, int) and reveal > chapter
            pov_aware = bool(pov_state) and pov_state.get("state") in ("knows", "suspects", "false_belief")
            # A fact the POV character holds is available to the narration even if the
            # planned reveal is later; otherwise a future reveal or an unaware reader prohibits it.
            if not pov_aware and (future or reader_state == "unaware"):
                prohibited.append(_trim(c.get("fact") or card.title, 160) + (f" (planned reveal ch.{reveal})" if reveal else ""))
                continue
            states = ", ".join(f"{k.get('entity')}={k.get('state')}" + (f" [false: {_trim(k.get('false_belief'), 60)}]" if k.get("state") == "false_belief" else "") for k in knowers)
            blocks.append(CompiledBlock(
                section="Knowledge States", title=card.title, priority=28, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="Participants hold asymmetric knowledge of this fact",
                text=f"{_trim(c.get('fact') or card.title, 140)} — reader={reader_state}; {states}",
            ))

        # 7. World rules referencing participants or high story purpose; power system limits.
        for card in self.bible.cards_of_type(project_id, "World Rule"):
            c = _c(card)
            known = {canon(x) for x in _lower_set(c.get("known_by"))}
            overlap = bool(known & names) if names else True
            if not overlap and c.get("domain") not in ("magic_power", "law", "politics"):
                continue
            blocks.append(CompiledBlock(
                section="World Rules", title=card.title, priority=38, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="Known by a participant" if overlap else "Core domain rule",
                text=f"[{c.get('domain')}] {_trim(c.get('rule'), 160)}; cost: {_trim(c.get('costs'), 80)}; exceptions: {_trim('; '.join(c.get('exceptions') or []), 120)}" + (f"; secret truth (do not reveal unless a knower speaks): {_trim(c.get('secret_truth'), 100)}" if c.get("secret_truth") else ""),
            ))
        for card in self.bible.cards_of_type(project_id, "Power System"):
            c = _c(card)
            blocks.append(CompiledBlock(
                section="Power System", title=card.title, priority=36, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason="Limits that keep the protagonist's advantage from removing tension",
                text=f"{c.get('name') or card.title}: restrictions={_trim('; '.join(c.get('restrictions') or []), 200)}; counters={_trim('; '.join(c.get('counters') or []), 160)}; tension limits={_trim('; '.join(c.get('tension_preserving_limits') or []), 200)}",
            ))

        # 8. Timeline constraints: recent events and anything overlapping this chapter.
        events = []
        for card in self.bible.cards_of_type(project_id, "Timeline Event"):
            c = _c(card)
            ch = c.get("chapter_number")
            if isinstance(ch, int) and chapter - 6 <= ch <= chapter:
                events.append((ch, card, c))
        events.sort(key=lambda e: e[0])
        for ch, card, c in events[-6:]:
            blocks.append(CompiledBlock(
                section="Recent Timeline", title=card.title, priority=42, card_id=card.id,
                truth_status=c.get("truth_status"), confidence=c.get("confidence"),
                reason=f"Occurred in chapter {ch}, within the recent window",
                text=f"ch.{ch} {c.get('story_time') or ''} {card.title} @ {c.get('location') or '?'}: {_trim(c.get('action'), 120)}; delayed effect: {_trim(c.get('delayed_effect'), 100)}; travel: {_trim(c.get('travel_time'), 60)}",
            ))

        # Rank: priority, then truth status, then confidence.
        def rank(b: CompiledBlock):
            return (b.priority, _TRUTH_RANK.get(b.truth_status or "planned", 5), -(b.confidence or 0.0))

        blocks.sort(key=rank)
        result = CompiledContext(budget_chars=budget_chars)
        result.prohibited = prohibited[:20]
        for b in blocks:
            # Obsolete facts never belong in generation context.
            if b.truth_status == "obsolete":
                result.dropped += 1
                continue
            # Keep planned / inferred / disputed / believed clearly separated
            # from canon so the model does not treat them as settled truth.
            if b.truth_status and b.truth_status != "canon" and not b.text.startswith(f"[{b.truth_status}] "):
                b.text = f"[{b.truth_status}] {b.text}"
            # Budget is enforced against the actually rendered text (headings,
            # separators and the prohibited list included), not an estimate.
            result.blocks.append(b)
            if len(result.as_text()) > budget_chars and len(result.blocks) > 1:
                result.blocks.pop()
                result.dropped += 1
        result.used_chars = len(result.as_text())
        return result
