"""Story So Far: deterministic tiered recap + carry-forward state from digests.

Tiering (relative to the chapter about to be written):
- recent  (last ``recent_window`` chapters): full digest detail — events, state
  changes, hooks, knowledge, ending state.
- mid     (next ``mid_window`` chapters back): one line + persistent state
  changes + strong hooks.
- distant (everything older): grouped into volume/arc buckets of one-liners.

Carry-forward state folds every digest's ``state_changes`` in chapter order so
the latest value per (entity, kind) wins; hooks opened and never closed become
the dangling list. The whole block is trimmed to a character budget by dropping
distant detail first, then mid, never the recent ending state.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session

from app.schemas.story_memory import (
    CarryForwardEntity,
    ChapterDigest,
    DanglingHook,
    StoryMemorySettings,
    StorySoFar,
    StorySoFarTier,
)
from app.services.story_memory.digest_service import DigestService
from app.services.story_memory.settings import get_settings


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def _hook_key(text: str) -> str:
    """Loose key for matching an opened hook with a later closure."""
    toks = [t for t in re.findall(r"[a-z0-9']+", _norm(text)) if len(t) > 3 or t.isdigit()]
    return " ".join(sorted(set(toks))[:12])


def _hook_matches(open_hook: str, closed_hook: str) -> bool:
    if set(re.findall(r"\b\d+\b", open_hook)) != set(re.findall(r"\b\d+\b", closed_hook)):
        return False
    a = set(_hook_key(open_hook).split())
    b = set(_hook_key(closed_hook).split())
    if not a or not b:
        return _norm(open_hook) == _norm(closed_hook)
    overlap = len(a & b) / max(1, min(len(a), len(b)))
    return overlap >= 0.5


class StorySoFarCompiler:
    def __init__(self, session: Session):
        self.session = session
        self.digests = DigestService(session)

    # ------------------------------------------------------------- carry state
    @staticmethod
    def carry_forward(digests: List[ChapterDigest]) -> List[CarryForwardEntity]:
        state: Dict[str, CarryForwardEntity] = {}
        for d in digests:
            for name in d.participants:
                key = _norm(name)
                if not key:
                    continue
                ent = state.setdefault(key, CarryForwardEntity(entity=name))
                ent.last_seen_chapter = d.chapter_number
            for sc in d.state_changes:
                key = _norm(sc.entity)
                if not key:
                    continue
                ent = state.setdefault(key, CarryForwardEntity(entity=sc.entity))
                ent.last_seen_chapter = max(ent.last_seen_chapter or 0, d.chapter_number)
                if sc.kind == "location":
                    ent.location = sc.after
                elif sc.kind == "alive_dead":
                    ent.alive = not re.search(r"\b(dead|died|killed|deceased|slain|perished)\b", _norm(sc.after))
                    ent.states["alive_dead"] = sc.after
                elif sc.permanent or sc.kind in ("possession", "injury", "power", "status", "resource", "knowledge"):
                    previous = ent.states.get(sc.kind)
                    if sc.kind in ("possession", "injury", "knowledge", "power", "resource") and previous and sc.before != previous:
                        try:
                            snapshot = json.loads(sc.after)
                        except (TypeError, ValueError):
                            snapshot = None
                        # Accepted receipts use complete JSON snapshots. Free-text digests
                        # retain observations rather than guess additions or removals.
                        ent.states[sc.kind] = sc.after if isinstance(snapshot, list) else previous + (f"; ch.{d.chapter_number}: {sc.after}" if sc.after not in previous else "")
                    else:
                        ent.states[sc.kind] = sc.after
        return sorted(state.values(), key=lambda e: (-(e.last_seen_chapter or 0), e.entity))

    @staticmethod
    def dangling_hooks(digests: List[ChapterDigest], *, next_chapter: int, overdue_after: int) -> List[DanglingHook]:
        open_hooks: List[Tuple[int, ChapterDigest, object]] = []
        for d in digests:
            # Close earlier hooks that this chapter resolved.
            for closed in d.hooks_closed:
                for i, (ch, _, oh) in enumerate(list(open_hooks)):
                    if _hook_matches(oh.hook, closed.hook):
                        if closed.complete:
                            open_hooks.pop(i)
                        else:
                            open_hooks[i] = (d.chapter_number, d, oh)
                        break
            for oh in d.hooks_opened:
                open_hooks.append((d.chapter_number, d, oh))
        out: List[DanglingHook] = []
        for ch, _, oh in open_hooks:
            age = max(0, next_chapter - ch)
            strength_factor = {"strong": 0.6, "medium": 1.0, "weak": 1.6}.get(oh.strength, 1.0)
            window = _norm(oh.expected_payoff_window)
            limit = max(1, int(overdue_after * strength_factor))
            if "next chapter" in window:
                limit = 2
            elif any(w in window for w in ("long-term", "end of volume", "end of the volume", "end of series")):
                limit = next_chapter + 1  # No fixed date is implied by these horizons.
            elif "arc" in window:
                limit = max(limit, overdue_after * 3)
            out.append(DanglingHook(
                hook=oh.hook, hook_type=oh.hook_type, opened_chapter=ch, strength=oh.strength,
                expected_payoff_window=oh.expected_payoff_window, chapters_open=age,
                overdue=age >= limit,
            ))
        rank = {"strong": 0, "medium": 1, "weak": 2}
        out.sort(key=lambda h: (not h.overdue, rank.get(h.strength, 1), -h.chapters_open))
        return out

    # ------------------------------------------------------------------ tiers
    @staticmethod
    def _recent_text(d: ChapterDigest) -> str:
        lines = [f"## Chapter {d.chapter_number}{' — ' + d.title if d.title else ''} (POV {d.pov or '?'}; {d.story_time or 'time n/a'})"]
        lines.append(d.summary.strip() or d.one_line)
        if d.events:
            lines.append("Events: " + " → ".join(e.summary.rstrip(".") for e in d.events[:10]))
        if d.state_changes:
            lines.append("State now: " + "; ".join(f"{s.entity} [{s.kind}] {s.after}" for s in d.state_changes[:12]))
        if d.knowledge_deltas:
            lines.append("Learned: " + "; ".join(f"{k.entity} ← {k.learned}" + (" (FALSE belief)" if k.is_false_belief else "") for k in d.knowledge_deltas[:8]))
        if d.relationship_shifts:
            lines.append("Relationships: " + "; ".join(d.relationship_shifts[:6]))
        if d.hooks_opened:
            lines.append("Left open: " + "; ".join(f"{h.hook} [{h.hook_type}/{h.strength}]" for h in d.hooks_opened[:8]))
        if d.promises_made:
            lines.append("Promises made: " + "; ".join(d.promises_made[:5]))
        if d.continuity_risks:
            lines.append("Do not forget: " + "; ".join(d.continuity_risks[:8]))
        if d.ending_state:
            lines.append(f"Ends with: {d.ending_state}")
        return "\n".join(lines)

    @staticmethod
    def _mid_text(d: ChapterDigest) -> str:
        bits = [f"Ch.{d.chapter_number}: {d.one_line.rstrip('.')}"]
        perm = [f"{s.entity}={s.after}" for s in d.state_changes if s.permanent][:4]
        if perm:
            bits.append("(" + "; ".join(perm) + ")")
        strong = [h.hook for h in d.hooks_opened if h.strength == "strong"][:2]
        if strong:
            bits.append("open: " + "; ".join(strong))
        return " ".join(bits)

    @staticmethod
    def _distant_text(group: List[ChapterDigest]) -> str:
        if not group:
            return ""
        first, last = group[0].chapter_number, group[-1].chapter_number
        vol = group[0].volume_number
        head = f"Chapters {first}-{last}" + (f" (vol. {vol})" if vol is not None else "")
        pivots = [d for d in group if any(e.significance in ("major", "pivotal") for e in d.events)]
        lines = [f"{head}: " + " / ".join(d.one_line.rstrip(".") for d in group[:6]) + (" …" if len(group) > 6 else "")]
        if pivots:
            lines.append("  Pivotal: " + "; ".join(f"ch.{d.chapter_number} {next((e.summary for e in d.events if e.significance in ('major', 'pivotal')), d.one_line)}" for d in pivots[:5]))
        return "\n".join(lines)

    # ---------------------------------------------------------------- compile
    def compile(
        self,
        project_id: int,
        *,
        next_chapter: Optional[int] = None,
        budget_chars: Optional[int] = None,
        settings: Optional[StoryMemorySettings] = None,
        include_carry_forward: bool = True,
    ) -> StorySoFar:
        cfg = settings or get_settings(self.session, project_id)
        all_digests = self.digests.fresh_digests(project_id)
        coverage = self.digests.coverage(project_id)
        written = coverage["written"]
        latest = max(written) if written else (all_digests[-1].chapter_number if all_digests else 0)
        nxt = next_chapter or (latest + 1)
        through = min(latest, nxt - 1)
        # Only chapters before the one being written count as "so far".
        digests = [d for d in all_digests if d.chapter_number < nxt]
        budget = int(budget_chars or cfg.recap_budget_chars)

        result = StorySoFar(
            project_id=project_id, through_chapter=through, next_chapter=nxt,
            digested_chapters=[d.chapter_number for d in digests],
            missing_chapters=[n for n in coverage["missing"] if n < nxt],
            stale_chapters=[n for n in coverage["stale"] if n < nxt],
            budget_chars=budget,
        )
        if not digests:
            result.text = ""
            return result

        recent = digests[-cfg.recent_window:]
        rest = digests[: -cfg.recent_window] if len(digests) > cfg.recent_window else []
        mid = rest[-cfg.mid_window:] if cfg.mid_window and rest else []
        distant = rest[: len(rest) - len(mid)] if rest else []

        last = recent[-1]
        result.last_ending_state = last.ending_state
        result.last_paragraph_gist = last.last_paragraph_gist
        result.story_clock = last.story_time
        result.recent_knowledge = [f"ch.{d.chapter_number} {k.entity} ← {k.learned}" for d in recent for k in d.knowledge_deltas][:20]
        if include_carry_forward:
            result.carry_forward = self.carry_forward(digests)
        result.dangling_hooks = self.dangling_hooks(digests, next_chapter=nxt, overdue_after=cfg.hook_overdue_chapters)

        # Distant tier grouped by volume (or blocks of 10 when no volume info).
        groups: List[List[ChapterDigest]] = []
        for d in distant:
            key = d.volume_number if d.volume_number is not None else (d.chapter_number - 1) // 10
            if groups and len(groups[-1]) < 10 and (groups[-1][0].volume_number if groups[-1][0].volume_number is not None else (groups[-1][0].chapter_number - 1) // 10) == key:
                groups[-1].append(d)
            else:
                groups.append([d])

        tiers: List[StorySoFarTier] = []
        if distant:
            tiers.append(StorySoFarTier(name="distant", chapter_range=[distant[0].chapter_number, distant[-1].chapter_number], text="\n".join(self._distant_text(g) for g in groups)))
        if mid:
            tiers.append(StorySoFarTier(name="mid", chapter_range=[mid[0].chapter_number, mid[-1].chapter_number], text="\n".join(self._mid_text(d) for d in mid)))
        tiers.append(StorySoFarTier(name="recent", chapter_range=[recent[0].chapter_number, recent[-1].chapter_number], text="\n\n".join(self._recent_text(d) for d in recent)))
        result.tiers = tiers

        result.text = self._render(result, cfg)
        # Budget: drop distant → mid → carry-forward detail, never the recent ending.
        shrink_steps = [
            lambda: self._drop_tier(result, "distant"),
            lambda: self._truncate_tier(result, "mid", 0.5),
            lambda: self._drop_tier(result, "mid"),
            lambda: self._limit_carry(result, 12),
            lambda: self._truncate_tier(result, "recent", 0.6),
            lambda: self._limit_carry(result, 0),
        ]
        for step in shrink_steps:
            if len(result.text) <= budget:
                break
            if step():
                result.text = self._render(result, cfg)
        result.used_chars = len(result.text)
        return result

    def retrieve(self, project_id: int, *, before_chapter: int, participants: List[str], query: str, budget_chars: int = 5000) -> Dict[str, Any]:
        """Relevance-selected distant evidence plus recent continuity, never future or stale memory."""
        stop = {"this", "that", "with", "from", "their", "they", "then", "when", "were", "have", "chapter", "before", "after", "about", "through", "will", "into", "must", "which"}

        def tokens(text: str) -> set:
            return {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) >= 4 and t not in stop}

        names = {_norm(n) for n in participants}
        terms = tokens(query) - {t for n in names for t in tokens(n)}
        cards = {int((c.content or {}).get("chapter_number") or 0): c for c in self.digests.digest_cards(project_id)}
        ranked = []
        for digest in self.digests.fresh_digests(project_id):
            if digest.chapter_number >= before_chapter:
                continue
            events = [e.summary for e in digest.events]
            hooks = [h.hook for h in digest.hooks_opened]
            changes = [f"{s.entity}: {s.after}" for s in digest.state_changes]
            body = "\n".join([digest.one_line, *events, *changes, *hooks])
            overlap = terms & tokens(body)
            relevant_names = names & {_norm(n) for n in digest.participants}
            recent = digest.chapter_number >= before_chapter - 3
            score = len(overlap) * 8 + len(relevant_names) + (3 if recent else 0)
            if not (overlap or recent or (relevant_names and (hooks or changes))):
                continue
            reason = "Relevant terms: " + ", ".join(sorted(overlap)) if overlap else ("Recent continuity" if recent else "Participant state/obligation")
            ranked.append((score, digest, body, reason))
        ranked.sort(key=lambda item: (-item[0], -item[1].chapter_number))
        blocks = []
        used = 0
        for _, digest, body, reason in ranked:
            card = cards[digest.chapter_number]
            text = f"ch.{digest.chapter_number} ({reason}):\n{body[:1100]}"
            if used + len(text) + 2 > budget_chars:
                continue
            blocks.append({"chapter_number": digest.chapter_number, "card_id": card.id, "source_hash": digest.source_hash, "reason": reason, "text": text})
            used += len(text) + 2
            if len(blocks) >= 10:
                break
        blocks.sort(key=lambda b: b["chapter_number"])
        text = "\n\n".join(b["text"] for b in blocks)
        return {"text": text, "blocks": blocks, "used_chars": len(text), "budget_chars": budget_chars, "scope": "Fresh extractive memory; omitted events remain unverified"}

    @staticmethod
    def _drop_tier(r: StorySoFar, name: str) -> bool:
        before = len(r.tiers)
        r.tiers = [t for t in r.tiers if t.name != name]
        return len(r.tiers) != before

    @staticmethod
    def _truncate_tier(r: StorySoFar, name: str, keep_ratio: float) -> bool:
        for t in r.tiers:
            if t.name == name and t.text:
                lines = t.text.split("\n")
                keep = max(1, int(len(lines) * keep_ratio))
                if keep < len(lines):
                    t.text = "\n".join(lines[-keep:]) if name == "recent" else "\n".join(lines[:keep]) + "\n…"
                    return True
        return False

    @staticmethod
    def _limit_carry(r: StorySoFar, n: int) -> bool:
        if len(r.carry_forward) > n:
            r.carry_forward = r.carry_forward[:n]
            return True
        return False

    @staticmethod
    def _render(r: StorySoFar, cfg: StoryMemorySettings) -> str:
        parts: List[str] = [f"[Story So Far — through chapter {r.through_chapter}; you are writing chapter {r.next_chapter}]"]
        if r.missing_chapters:
            parts.append(f"(No memory for chapters {', '.join(map(str, r.missing_chapters[:12]))}{' …' if len(r.missing_chapters) > 12 else ''}: digest them for full context)")
        if r.stale_chapters:
            parts.append(f"(Stale memory excluded for chapters {', '.join(map(str, r.stale_chapters[:12]))}; refresh before relying on their events)")
        for t in r.tiers:
            label = {"distant": "Earlier arcs (compressed)", "mid": "Previous chapters (brief)", "recent": "Most recent chapters (detailed)"}[t.name]
            parts.append(f"### {label}\n{t.text}")
        if r.carry_forward:
            lines = []
            for e in r.carry_forward:
                bits = []
                if e.location:
                    bits.append(f"at {e.location}")
                if not e.alive:
                    bits.append("DEAD")
                bits += [f"{k}: {v}" for k, v in e.states.items() if k != "alive_dead"]
                if bits:
                    lines.append(f"- {e.entity} (last seen ch.{e.last_seen_chapter}): " + "; ".join(bits))
            if lines:
                parts.append("### Current state of the world (carry-forward)\n" + "\n".join(lines))
        if r.dangling_hooks:
            lines = [f"- {'OVERDUE ' if h.overdue else ''}{h.hook} [{h.hook_type}, {h.strength}, opened ch.{h.opened_chapter}]" for h in r.dangling_hooks[:14]]
            parts.append("### Still unresolved for the reader\n" + "\n".join(lines))
        if r.story_clock:
            parts.append(f"### Story clock\nLatest story time: {r.story_clock}")
        if r.last_ending_state:
            parts.append(f"### Where the previous chapter left off\n{r.last_ending_state}" + (f"\nLast paragraph: {r.last_paragraph_gist}" if r.last_paragraph_gist else ""))
        return "\n\n".join(parts).strip()
