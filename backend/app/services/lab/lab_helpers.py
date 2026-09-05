"""Expression helpers for the Narrative Reverse-Engineering Lab workflow.

Registered into the workflow expression engine via ``register_function`` so the
``.wf`` file stays declarative. Imported from ``services/workflow/expressions``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

from app.services.workflow.expressions.functions import register_function

ANALYSIS_PROMPT_VERSION = "Lab - Chapter Analysis@2"


def _as_dict(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    return {}


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


@register_function(
    "lab_chapter_items",
    summary="Turn Chapter Analysis cards into batch items with content=source_text and chapter metadata",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_chapter_items(chapters.cards)",
)
def fn_lab_chapter_items(
    cards: Any,
    start_chapter: int = 0,
    end_chapter: int = 0,
    include_chapters: Any = None,
    exclude_chapters: Any = None,
    only_missing: bool = False,
    only_stale: bool = False,
    prompt_version: str = "",
) -> List[Dict[str, Any]]:
    """Batch items for chapter analysis, scoped so a large manuscript is never analysed wholesale.

    - ``start_chapter``/``end_chapter``: inclusive normalized-chapter range (0 = open).
    - ``include_chapters``/``exclude_chapters``: explicit chapter numbers.
    - ``only_missing``: skip chapters whose stored analysis is already ``done``.
    - ``only_stale``: additionally re-analyse ``done`` chapters whose stored
      ``analysis_source_hash`` or ``prompt_version`` no longer matches.
    Chapters that were analysed by the same prompt against the same text are
    never re-sent when ``only_missing`` is set, so a failure at chapter 27
    never restarts chapters 1-26.
    """
    include = {int(x) for x in (include_chapters or []) if str(x).strip()}
    exclude = {int(x) for x in (exclude_chapters or []) if str(x).strip()}
    start = int(start_chapter or 0)
    end = int(end_chapter or 0)
    items: List[Dict[str, Any]] = []
    for card in cards or []:
        card = _as_dict(card)
        content = _as_dict(card.get("content"))
        text = str(content.get("source_text") or "")
        if not text.strip():
            continue
        chapter_no = int(content.get("chapter_number") or len(items) + 1)
        if include and chapter_no not in include:
            continue
        if chapter_no in exclude:
            continue
        if start and chapter_no < start:
            continue
        if end and chapter_no > end:
            continue
        if only_missing or only_stale:
            status = str(content.get("analysis_status") or "")
            if status == "done":
                current_hash = str(content.get("source_text_hash") or "")
                analysed_hash = str(content.get("analysis_source_hash") or "")
                analysed_prompt = str(content.get("prompt_version") or "")
                stale = bool(current_hash and analysed_hash and analysed_hash != current_hash) or bool(prompt_version and analysed_prompt and analysed_prompt != prompt_version)
                if not (only_stale and stale):
                    continue
        items.append({
            "card_id": card.get("id"),
            "chapter_no": chapter_no,
            "title": str(content.get("title") or card.get("title") or ""),
            "volume": str(content.get("volume") or ""),
            "word_count": int(content.get("word_count") or 0),
            "manuscript_id": str(content.get("manuscript_id") or ""),
            "chapter_id": str(content.get("chapter_id") or ""),
            "language": str(content.get("language") or ""),
            "source_text_hash": str(content.get("source_text_hash") or ""),
            "content": text,
        })
    items.sort(key=lambda it: it["chapter_no"])
    return items


@register_function(
    "lab_analysis_records",
    summary="Merge BatchStructured results back into per-chapter analysis dicts (ai_result + identity + card_id)",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_analysis_records(analysis_results.results)",
)
def fn_lab_analysis_records(results: Any) -> List[Dict[str, Any]]:
    """Merge results; verify every evidence quote against the chapter text.

    Failed items (no ``ai_result``) become explicit ``analysis_status="failed"``
    records so the chapter card records the failure instead of silently
    staying "pending" while the run reports success. Evidence verification
    downgrades fabricated quotes (see ``forge.evidence``).
    """
    from app.services.forge import evidence as forge_evidence

    records: List[Dict[str, Any]] = []
    for row in results or []:
        row = _as_dict(row)
        meta = _as_dict(row.get("meta"))
        ai = _as_dict(row.get("ai_result"))
        chapter_no = int(meta.get("chapter_no") or ai.get("chapter_number") or 0)
        card_title = f"Ch {chapter_no:04d} · {meta.get('title') or ai.get('title') or ''}"[:200]
        if not ai:
            records.append({
                "chapter_number": chapter_no, "title": meta.get("title") or "", "volume": meta.get("volume") or "", "word_count": int(meta.get("word_count") or 0),
                "card_id": meta.get("card_id"), "card_title": card_title, "analysis_status": "failed", "analysis_error": str(row.get("error") or "no structured result"),
            })
            continue
        record = dict(ai)
        record["chapter_number"] = chapter_no
        record["title"] = ai.get("title") or meta.get("title") or ""
        record["volume"] = ai.get("volume") or meta.get("volume") or ""
        record["word_count"] = int(meta.get("word_count") or 0)
        record["card_id"] = meta.get("card_id")
        record["analysis_status"] = "done"
        # Staleness anchors: which text and which prompt produced this analysis.
        record["analysis_source_hash"] = str(meta.get("source_text_hash") or "")
        record["prompt_version"] = ANALYSIS_PROMPT_VERSION
        record["extraction_model"] = str(meta.get("model_name") or "")
        # Must match the title produced by ManuscriptImportService.store_manuscript.
        # Use the imported title (not the AI-rewritten one) so the upsert updates
        # the existing chapter card instead of creating a duplicate.
        record["card_title"] = card_title
        emo = _as_dict(record.get("emotion"))
        if emo and not emo.get("chapter_number"):
            emo["chapter_number"] = record["chapter_number"]
            record["emotion"] = emo
        source_text = str(meta.get("content") or "")
        if source_text:
            record = forge_evidence.verify_chapter_analysis(
                record, source_text, manuscript_id=str(meta.get("manuscript_id") or ""), chapter_id=str(meta.get("chapter_id") or ""),
                chapter_number=chapter_no, extraction_model=str(meta.get("model_name") or ""), prompt_version=ANALYSIS_PROMPT_VERSION,
            )
        records.append(record)
    records.sort(key=lambda r: r["chapter_number"])
    return records


@register_function(
    "lab_merge_stored_analyses",
    summary="Union of freshly analysed records and Chapter Analysis cards already marked done (fresh wins); keeps downstream stages whole when analysis is scoped",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_merge_stored_analyses(analysis_records.result, chapter_cards.cards)",
)
def fn_lab_merge_stored_analyses(records: Any, cards: Any) -> List[Dict[str, Any]]:
    by_chapter: Dict[int, Dict[str, Any]] = {}
    for card in cards or []:
        card = _as_dict(card)
        content = _as_dict(card.get("content"))
        if content.get("analysis_status") != "done":
            continue
        chapter_no = int(content.get("chapter_number") or 0)
        if not chapter_no:
            continue
        stored = {k: v for k, v in content.items() if k != "source_text"}
        stored["card_id"] = card.get("id")
        by_chapter[chapter_no] = stored
    for rec in records or []:
        rec = _as_dict(rec)
        chapter_no = int(rec.get("chapter_number") or 0)
        if chapter_no:
            by_chapter[chapter_no] = rec
    return [by_chapter[k] for k in sorted(by_chapter)]


@register_function(
    "lab_verified_records",
    summary="Only analysis records whose evidence was verified (status done); failed chapters are excluded from downstream digests",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_verified_records(analysis_records.result)",
)
def fn_lab_verified_records(records: Any) -> List[Dict[str, Any]]:
    return [_as_dict(r) for r in (records or []) if _as_dict(r).get("analysis_status") == "done"]


@register_function(
    "lab_failed_chapters",
    summary="Chapter numbers whose analysis failed or whose evidence could not be verified",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_failed_chapters(analysis_records.result)",
)
def fn_lab_failed_chapters(records: Any) -> List[int]:
    return sorted(int(_as_dict(r).get("chapter_number") or 0) for r in (records or []) if _as_dict(r).get("analysis_status") == "failed")


@register_function(
    "lab_analysis_digest",
    summary="Compact text digest of chapter analyses for downstream prompts",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_analysis_digest(records, max_chars=60000)",
)
def fn_lab_analysis_digest(records: Any, max_chars: int = 60000, per_chapter_chars: int = 900) -> str:
    lines: List[str] = []
    for rec in records or []:
        rec = _as_dict(rec)
        emo = _as_dict(rec.get("emotion"))
        state_changes = "; ".join(
            f"{_as_dict(s).get('entity')}: {_as_dict(s).get('before')} -> {_as_dict(s).get('after')}"
            for s in (rec.get("state_changes") or [])
        )
        lines.append(
            f"## Ch {rec.get('chapter_number')} · {rec.get('title')} [{rec.get('volume')}]\n"
            f"POV: {rec.get('pov')} | Locations: {', '.join(rec.get('locations') or [])} | Function: {emo.get('dominant_function')} | "
            f"Tension {emo.get('tension')} Satisfaction {emo.get('satisfaction')} Curiosity {emo.get('curiosity')}\n"
            f"Summary: {_trim(rec.get('summary'), per_chapter_chars)}\n"
            f"Goal: {_trim(rec.get('chapter_goal'), 160)} | Conflict: {_trim(rec.get('main_conflict'), 160)} | Turn: {_trim(rec.get('turning_point'), 160)} | Hook: {_trim(rec.get('hook'), 120)} ({rec.get('hook_type')})\n"
            f"State changes: {_trim(state_changes, 400)}\n"
            f"Threads: {_trim(', '.join(rec.get('threads_advanced') or []), 200)} | Setups: {_trim(', '.join(rec.get('setups') or []), 200)} | Payoffs: {_trim(', '.join(rec.get('payoffs') or []), 200)} | Reveals: {_trim(', '.join(rec.get('reveals') or []), 200)}\n"
            f"Relationships: {_trim('; '.join(rec.get('relationship_changes') or []), 240)} | Knowledge: {_trim('; '.join(rec.get('knowledge_changes') or []), 240)}\n"
            f"Participants: {_trim(', '.join(rec.get('participants') or []), 240)}"
        )
    text = "\n\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…(digest truncated)"


@register_function(
    "lab_windows",
    summary="Split analysis records into processing windows (a processing detail, never a narrative boundary)",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_windows(records, size=40)",
)
def fn_lab_windows(records: Any, size: int = 40) -> List[Dict[str, Any]]:
    recs = [_as_dict(r) for r in (records or [])]
    if not recs:
        return []
    size = max(5, int(size or 40))
    windows: List[Dict[str, Any]] = []
    total = (len(recs) + size - 1) // size
    for i in range(0, len(recs), size):
        chunk = recs[i:i + size]
        windows.append({
            "chunk_index": len(windows) + 1,
            "total_chunks": total,
            "start_chapter": chunk[0].get("chapter_number"),
            "end_chapter": chunk[-1].get("chapter_number"),
            "chapter_count": len(chunk),
            "content": fn_lab_analysis_digest(chunk, max_chars=90000, per_chapter_chars=500),
        })
    return windows


@register_function(
    "lab_arc_candidates",
    summary="Flatten SequentialStructured arc results into a global candidate list with window provenance",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_arc_candidates(arc_results.results)",
)
def fn_lab_arc_candidates(results: Any) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in results or []:
        row = _as_dict(row)
        ai = _as_dict(row.get("ai_result"))
        meta = _as_dict(row.get("meta"))
        for arc in ai.get("arcs") or []:
            arc = dict(_as_dict(arc))
            arc["window"] = meta.get("chunk_index")
            out.append(arc)
    out.sort(key=lambda a: (int(a.get("chapter_start") or 0), int(a.get("chapter_end") or 0)))
    return out


@register_function(
    "lab_open_arc_carry",
    summary="Carry the still-open arc from one window into the next",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_open_arc_carry(ai_result)",
)
def fn_lab_open_arc_carry(ai_result: Any) -> Dict[str, Any]:
    ai = _as_dict(ai_result)
    arcs = [_as_dict(a) for a in (ai.get("arcs") or [])]
    if not arcs:
        return {"open_arc": "none"}
    last = arcs[-1]
    if not last.get("open_at_end"):
        return {"open_arc": "none"}
    return {"open_arc": json.dumps({"name": last.get("name"), "chapter_start": last.get("chapter_start"), "summary": _trim(last.get("summary"), 400)}, ensure_ascii=False)}


@register_function(
    "lab_entity_mentions",
    summary="Aggregate participant mentions per chapter for entity resolution",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_entity_mentions(records)",
)
def fn_lab_entity_mentions(records: Any, max_chars: int = 40000) -> str:
    counts: Dict[str, List[int]] = {}
    for rec in records or []:
        rec = _as_dict(rec)
        ch = int(rec.get("chapter_number") or 0)
        for name in rec.get("participants") or []:
            key = str(name).strip()
            if key:
                counts.setdefault(key, []).append(ch)
    lines = [f"- {name}: chapters {sorted(set(chs))[:30]}" for name, chs in sorted(counts.items(), key=lambda kv: -len(kv[1]))]
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…(truncated)"


@register_function(
    "lab_stage_cards",
    summary="Turn StoryStructureMap stages into Stage Outline-like items for card creation",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_stage_cards(structure.data.stages)",
)
def fn_lab_stage_cards(stages: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for st in stages or []:
        st = _as_dict(st)
        items.append({
            **st,
            "stage_title": f"Stage {st.get('stage_number')}: {st.get('name')} (Ch {st.get('chapter_start')}-{st.get('chapter_end')})",
        })
    return items


@register_function(
    "lab_emotional_rhythm",
    summary="Build an EmotionalRhythm content dict from analysis records",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_emotional_rhythm(records)",
)
def fn_lab_emotional_rhythm(records: Any) -> Dict[str, Any]:
    chapters: List[Dict[str, Any]] = []
    for rec in records or []:
        rec = _as_dict(rec)
        emo = dict(_as_dict(rec.get("emotion")))
        if not emo:
            continue
        emo["chapter_number"] = int(rec.get("chapter_number") or emo.get("chapter_number") or 0)
        chapters.append(emo)
    chapters.sort(key=lambda c: c["chapter_number"])
    observations: List[str] = []
    if chapters:
        low = [c["chapter_number"] for c in chapters if int(c.get("tension") or 0) <= 2 and int(c.get("satisfaction") or 0) <= 2]
        if len(low) >= 3:
            observations.append(f"{len(low)} chapters have both tension and satisfaction <= 2: {low[:15]}")
        peak = max(chapters, key=lambda c: int(c.get("tension") or 0))
        observations.append(f"Tension peaks at chapter {peak['chapter_number']} ({peak.get('tension')}/10), {round(100 * chapters.index(peak) / max(1, len(chapters) - 1))}% through the book")
        streak, start = 0, None
        for c in chapters:
            if not c.get("rewards"):
                streak += 1
                start = start or c["chapter_number"]
                if streak == 6:
                    observations.append(f"No reader reward recorded for 6+ consecutive chapters starting at {start}")
            else:
                streak, start = 0, None
    return {"chapters": chapters, "observations": observations}


@register_function(
    "lab_relationship_items",
    summary="Add a canonical 'A ↔ B' title to relationship arcs",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_relationship_items(bible.data.relationship_arcs)",
)
def fn_lab_relationship_items(arcs: Any) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    for arc in arcs or []:
        arc = dict(_as_dict(arc))
        a, b = sorted([str(arc.get("character_a") or ""), str(arc.get("character_b") or "")])
        arc["pair_title"] = f"{a} ↔ {b}"
        items.append(arc)
    return items


@register_function(
    "lab_bible_digest",
    summary="Compact digest of a reconstructed NarrativeArchitecture for the genome prompt",
    scenario="Reverse-engineering lab",
    priority=60,
    example="lab_bible_digest(bible.data)",
)
def fn_lab_bible_digest(bible: Any, max_chars: int = 30000) -> str:
    b = _as_dict(bible)
    lines: List[str] = []
    for t in b.get("plot_threads") or []:
        t = _as_dict(t)
        lines.append(f"[Thread] {t.get('name')} ({t.get('thread_type')}, {t.get('status')}): {_trim(t.get('central_question'), 160)}; ch {t.get('opening_chapter')}→{t.get('last_advanced_chapter')}; resolution: {_trim(t.get('actual_resolution'), 160)}")
    for p in b.get("promises") or []:
        p = _as_dict(p)
        lines.append(f"[Promise] {_trim(p.get('setup'), 120)} ({p.get('promise_type')}, {p.get('status')}): planted ch {p.get('source_chapter')}, payoff ch {p.get('payoff_chapter')}; {_trim(p.get('actual_payoff'), 120)}")
    for k in b.get("knowledge_facts") or []:
        k = _as_dict(k)
        lines.append(f"[Secret] {_trim(k.get('fact'), 140)}: reveal ch {k.get('planned_reveal_chapter')}; reader={k.get('reader_state')}")
    for r in b.get("relationship_arcs") or []:
        r = _as_dict(r)
        lines.append(f"[Relationship] {r.get('character_a')} ↔ {r.get('character_b')}: {_trim(r.get('private_relationship'), 120)}; {len(r.get('milestones') or [])} milestones")
    text = "\n".join(lines)
    return text if len(text) <= max_chars else text[:max_chars] + "\n…(truncated)"
