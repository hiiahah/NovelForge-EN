"""Next Chapter Brief: what the upcoming chapter must address, should consider, and must avoid.

Deterministic. Sources: dangling hooks (Story Memory), due/overdue promises and
neglected threads (Bible ledgers + audits), pending relationship shifts,
prohibited knowledge (Context Compiler), reader-contract reward rhythm and the
chapter outline when one exists.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from sqlmodel import Session

from app.schemas.bible import REWARD_TYPES
from app.schemas.story_memory import BriefItem, NextChapterBrief
from app.services.bible.bible_service import BibleService
from app.services.bible.context_compiler import ContextCompiler
from app.services.story_memory.digest_service import DigestService
from app.services.story_memory.settings import get_settings
from app.services.story_memory.story_so_far import StorySoFarCompiler


def _c(card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


class NextChapterPlanner:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)
        self.digests = DigestService(session)
        self.recap = StorySoFarCompiler(session)

    def _outline_for(self, project_id: int, chapter_number: int) -> Optional[Dict[str, Any]]:
        for card in self.bible.cards_of_type(project_id, "Chapter Outline"):
            c = _c(card)
            try:
                if int(c.get("chapter_number") or -1) == chapter_number:
                    return {"card_id": card.id, **c}
            except (TypeError, ValueError):
                continue
        return None

    def brief(self, project_id: int, *, chapter_number: Optional[int] = None, participants: Optional[List[str]] = None, pov: Optional[str] = None) -> NextChapterBrief:
        cfg = get_settings(self.session, project_id)
        current = self.bible.current_chapter_number(project_id)
        chapter = chapter_number or current + 1
        outline = self._outline_for(project_id, chapter)
        parts: List[str] = [p for p in (participants or []) if isinstance(p, str) and p.strip()]
        if not parts and outline:
            parts = [p for p in (outline.get("participants") or outline.get("entity_list") or []) if isinstance(p, str) and p.strip()]
        pov = pov or (outline or {}).get("pov") or (parts[0] if parts else None)

        must: List[BriefItem] = []
        should: List[BriefItem] = []
        avoid: List[BriefItem] = []
        rhythm: List[str] = []

        digests = [d for d in self.digests.fresh_digests(project_id) if d.chapter_number < chapter]
        hooks = StorySoFarCompiler.dangling_hooks(digests, next_chapter=chapter, overdue_after=cfg.hook_overdue_chapters)
        for h in hooks[:12]:
            item = BriefItem(kind="dangling_hook", priority=5 if h.overdue else (12 if h.strength == "strong" else 30), text=f"{h.hook} (opened ch.{h.opened_chapter}, {h.hook_type})", reason="Overdue for the reader" if h.overdue else f"Open {h.chapters_open} chapter(s)")
            (must if h.overdue or (h.strength == "strong" and h.chapters_open <= 1) else should).append(item)

        if digests:
            last = digests[-1]
            if last.ending_state:
                must.append(BriefItem(kind="continue_from", priority=1, text=f"Pick up from: {_trim(last.ending_state, 260)}", reason="Previous chapter ending"))
            for risk in last.continuity_risks[:6]:
                avoid.append(BriefItem(kind="continuity_risk", priority=10, text=risk, reason=f"Flagged by ch.{last.chapter_number} digest"))
            if last.hook_strength <= 3 and last.tension_end <= 4:
                rhythm.append("The previous chapter ended quietly; open with momentum or a sharp new question.")
            if last.tension_end >= 8:
                rhythm.append("Tension is very high; deliver the confrontation or a real consequence before any lull.")
            funcs = [d.dominant_function for d in digests[-3:]]
            if len(funcs) == 3 and len(set(funcs)) == 1:
                rhythm.append(f"Three chapters in a row were '{funcs[0]}'; change the dominant function.")

        for card in self.bible.cards_of_type(project_id, "Promise Payoff"):
            c = _c(card)
            if c.get("status") in ("paid_off", "subverted", "intentionally_abandoned", "contradicted"):
                continue
            rng = c.get("target_payoff_range")
            if isinstance(rng, (list, tuple)) and len(rng) == 2 and isinstance(rng[0], int) and isinstance(rng[1], int):
                if chapter > rng[1]:
                    must.append(BriefItem(kind="overdue_promise", priority=3, text=f"Pay off (overdue since ch.{rng[1]}): {_trim(c.get('setup') or card.title, 160)} → {_trim(c.get('planned_payoff'), 140)}", card_id=card.id, reason="Target window passed"))
                elif rng[0] <= chapter <= rng[1]:
                    should.append(BriefItem(kind="due_promise", priority=15, text=f"Payoff window open: {_trim(c.get('setup') or card.title, 160)} → {_trim(c.get('planned_payoff'), 140)}", card_id=card.id, reason=f"Window {rng[0]}-{rng[1]}"))
                elif chapter < rng[0] and c.get("status") == "planted" and chapter - int(c.get("source_chapter") or chapter) >= 6 and not c.get("reinforcement_chapters"):
                    should.append(BriefItem(kind="reinforce_promise", priority=35, text=f"Reinforce (planted ch.{c.get('source_chapter')}, never reinforced): {_trim(c.get('setup') or card.title, 140)}", card_id=card.id, reason="Reader may forget the setup before the payoff"))

        neglect_window = {"critical": 5, "high": 8, "medium": 15, "low": 30}
        for card in self.bible.cards_of_type(project_id, "Plot Thread"):
            c = _c(card)
            if c.get("status") not in ("active", "planned"):
                continue
            last_adv = c.get("last_advanced_chapter") or c.get("opening_chapter")
            urgency = str(c.get("urgency") or "medium")
            if isinstance(last_adv, int):
                gap = chapter - last_adv
                if gap >= neglect_window.get(urgency, 15):
                    (must if urgency in ("critical", "high") else should).append(BriefItem(kind="neglected_thread", priority=6 if urgency in ("critical", "high") else 25, text=f"Advance thread '{card.title}' ({urgency}; {gap} chapters idle): {_trim(c.get('central_question'), 140)}", card_id=card.id, reason="Neglected relative to urgency"))
            nxt = c.get("next_expected_chapter")
            if isinstance(nxt, int) and chapter >= nxt:
                must.append(BriefItem(kind="thread_due", priority=7, text=f"Thread '{card.title}' was due by ch.{nxt}: {_trim(c.get('central_question'), 140)}", card_id=card.id, reason="Scheduled advancement"))

        names = {p.lower() for p in parts}
        for card in self.bible.cards_of_type(project_id, "Relationship Arc"):
            c = _c(card)
            a, b = str(c.get("character_a", "")).lower(), str(c.get("character_b", "")).lower()
            if names and not ({a, b} <= names):
                continue
            shift = c.get("planned_next_shift")
            if shift:
                should.append(BriefItem(kind="relationship_shift", priority=20, text=f"{card.title}: planned shift — {_trim(shift, 160)}", card_id=card.id, reason="Both characters present" if names else "Pending relationship shift"))

        compiled = ContextCompiler(self.session).compile(project_id=project_id, chapter_number=chapter, participants=parts, pov=pov, budget_chars=8000)
        for p in compiled.prohibited[:12]:
            avoid.append(BriefItem(kind="prohibited_knowledge", priority=2, text=p, reason="POV/reader must not learn this yet"))

        if outline:
            for fo in (outline.get("forbidden_outcomes") or [])[:8]:
                avoid.append(BriefItem(kind="forbidden_outcome", priority=4, text=str(fo), card_id=outline.get("card_id"), reason="Reserved for a later chapter"))
            for ao in (outline.get("allowed_outcomes") or [])[:8]:
                should.append(BriefItem(kind="allowed_outcome", priority=18, text=f"May establish: {ao}", card_id=outline.get("card_id"), reason="Outline allows it"))
            beats = outline.get("beats") or []
            if beats:
                must.append(BriefItem(kind="outline_beats", priority=2, text="Beats in order: " + " → ".join(_trim(b.get("description") if isinstance(b, dict) else b, 90) for b in beats[:10]), card_id=outline.get("card_id"), reason="Chapter outline"))
            elif outline.get("overview"):
                must.append(BriefItem(kind="outline", priority=2, text=_trim(outline.get("overview"), 500), card_id=outline.get("card_id"), reason="Chapter outline"))

        contract = self.bible.singleton(project_id, "Reader Contract")
        if contract and digests:
            cc = _c(contract)
            priority = [r for r in (cc.get("reward_types_priority") or []) if r in REWARD_TYPES]
            if priority:
                recent = digests[-6:]
                delivered = {r for d in recent for r in d.rewards_delivered}
                missing = [r for r in priority[:3] if r not in delivered]
                if missing:
                    rhythm.append(f"None of the last {len(recent)} chapters delivered '{missing[0]}' (a top promised reward); find a way to deliver it.")

        must.sort(key=lambda i: i.priority)
        should.sort(key=lambda i: i.priority)
        avoid.sort(key=lambda i: i.priority)
        brief = NextChapterBrief(project_id=project_id, chapter_number=chapter, must_address=must[:14], should_consider=should[:16], avoid=avoid[:16], rhythm_advice=rhythm[:5])
        brief.text = self.render(brief)
        return brief

    @staticmethod
    def render(b: NextChapterBrief) -> str:
        parts = [f"[Next Chapter Brief — chapter {b.chapter_number}]"]
        if b.must_address:
            parts.append("MUST address:\n" + "\n".join(f"- {i.text}" for i in b.must_address))
        if b.should_consider:
            parts.append("SHOULD consider:\n" + "\n".join(f"- {i.text}" for i in b.should_consider))
        if b.avoid:
            parts.append("AVOID / never reveal:\n" + "\n".join(f"- {i.text}" for i in b.avoid))
        if b.rhythm_advice:
            parts.append("Rhythm:\n" + "\n".join(f"- {r}" for r in b.rhythm_advice))
        return "\n\n".join(parts) if len(parts) > 1 else ""
