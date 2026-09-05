"""Reference Example Library and function-specific hybrid retrieval.

Producer: ``build_example_library`` (from imported chapters + verified scene
analyses). Consumer: ``compiler.compile_chapter_context`` (retrieves examples
for the beat functions of the current outline) and tests.

Retrieval is hybrid and deterministic:
  structured filter (function, language, POV) -> lexical score (retrieval
  terms vs. beat description) -> metric proximity (dialogue ratio, pacing) ->
  rerank -> diversity selection (max one example per source chapter per
  function, no repeats across recent chapters) -> strict character budget.

Every returned excerpt is short (<= ``MAX_EXCERPT_CHARS``), carries its
evidence hash and a machine-readable reason. Source entity names inside an
excerpt are replaced by role tokens (``[ROLE:protagonist]``) before the
excerpt is stored so entity names never travel with the example.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from sqlmodel import Session, select

from app.db.models import ReferenceExample
from app.services.forge.textmetrics import (
    BEAT_FUNCTIONS,
    classify_ending,
    classify_opening,
    detect_language,
    infer_pov,
    measure,
    split_paragraphs,
    stable_id,
    tokenize,
)

EXAMPLES_VERSION = "examples-1"
MAX_EXCERPT_CHARS = 700
MIN_EXCERPT_CHARS = 120

# Scene function (analysis schema) -> beat functions the passage can demonstrate.
SCENE_FUNCTION_TO_BEATS: Dict[str, Tuple[str, ...]] = {
    "setup": ("quiet_scene_opening", "exposition_delivery"),
    "escalation": ("threat_escalation",),
    "discovery": ("mystery_clue", "reveal"),
    "reversal": ("comedic_reversal", "reveal"),
    "decision": ("internal_monologue",),
    "confrontation": ("dialogue_heavy_scene", "fight_choreography"),
    "payoff": ("reveal", "power_reveal"),
    "recovery": ("aftermath", "emotional_restraint"),
    "transition": ("transition",),
    "character_bonding": ("banter", "romantic_tension", "relationship_shift"),
    "world_revelation": ("exposition_delivery", "power_reveal"),
    "false_victory": ("false_reassurance",),
    "disaster": ("threat_escalation", "aftermath"),
}

_KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "banter": ("laugh", "grin", "smirk", "teas", "joke", "snort", "웃", "농담", "장난", "피식"),
    "romantic_tension": ("blush", "heart", "close", "hand", "warm", "breath", "설레", "가슴", "손을", "얼굴이", "붉"),
    "confession": ("confess", "love you", "i like you", "고백", "좋아해", "사랑해"),
    "threat_escalation": ("blade", "blood", "kill", "run", "scream", "danger", "죽", "칼", "피가", "위험", "도망"),
    "fight_choreography": ("swung", "dodged", "struck", "parried", "blade", "fist", "휘둘", "피했", "막았", "주먹", "검이"),
    "exposition_delivery": ("because", "history", "years ago", "the rule", "system", "때문에", "년 전", "규칙", "시스템", "역사"),
    "mystery_clue": ("strange", "odd", "why", "clue", "notice", "missing", "이상", "왜", "단서", "사라진", "수상"),
    "false_reassurance": ("fine", "relax", "nothing to worry", "safe", "괜찮", "걱정", "안전", "다행"),
    "reveal": ("realized", "truth", "was actually", "all along", "깨달", "진실", "사실은", "알게 되었", "정체"),
    "aftermath": ("silence", "after", "later", "quiet", "wound", "정적", "이후", "잠시 후", "상처", "고요"),
    "status_screen_presentation": ("[", "level", "skill", "status", "레벨", "스킬", "상태창", "능력치"),
    "power_reveal": ("power", "awaken", "unleash", "aura", "각성", "힘이", "기운", "폭발"),
    "internal_monologue": ("thought", "wondered", "i knew", "why did", "생각했", "왜", "나는", "내가"),
    "emotional_restraint": ("said nothing", "did not", "looked away", "swallowed", "말하지 않", "고개를 돌", "삼켰", "아무 말"),
}

_ROLE_TOKEN = "[ROLE:{role}]"


@dataclass
class ExampleCandidate:
    example_id: str
    chapter_card_id: Optional[int]
    chapter_number: int
    span_start: int
    span_end: int
    excerpt: str
    language: str
    pov_type: str
    scene_type: str
    dominant_emotion: str
    beat_function: str
    tags: List[str]
    position: str
    dialogue_ratio: float
    pacing: str
    entity_roles: List[str]
    metrics: Dict[str, Any]
    retrieval_terms: List[str]
    evidence_hash: str


def redact_entities(text: str, roles: Dict[str, str]) -> Tuple[str, List[str]]:
    """Replace source entity names by role tokens. Longest names first."""
    used: List[str] = []
    out = text
    for name in sorted(roles, key=len, reverse=True):
        if len(name) < 2:
            continue
        pattern = re.compile(rf"(?<![\w]){re.escape(name)}(?![\w])")
        if pattern.search(out):
            role = roles[name] or "character"
            out = pattern.sub(_ROLE_TOKEN.format(role=role), out)
            if role not in used:
                used.append(role)
    return out, used


def _pacing(metrics: Dict[str, Any]) -> str:
    mean = float((metrics.get("sentence_len") or {}).get("mean") or 0.0)
    short = float(metrics.get("short_paragraph_ratio") or 0.0)
    if mean and mean <= 10 and short >= 0.6:
        return "fast"
    if mean >= 20:
        return "slow"
    return "medium"


def _position(start: int, end: int, total: int) -> str:
    if total <= 0:
        return "middle"
    if start <= total * 0.12:
        return "opening"
    if end >= total * 0.88:
        return "ending"
    return "middle"


def _keyword_functions(text: str) -> List[str]:
    low = text.lower()
    hits: List[Tuple[int, str]] = []
    for fn, words in _KEYWORDS.items():
        n = sum(low.count(w) for w in words)
        if n:
            hits.append((n, fn))
    hits.sort(reverse=True)
    return [fn for _, fn in hits[:3]]


def tag_passage(text: str, *, position: str, metrics: Dict[str, Any], scene_function: Optional[str] = None, hook_type: Optional[str] = None) -> Tuple[str, List[str]]:
    """Return (primary beat function, all tags) for one passage."""
    tags: Set[str] = set()
    lang = metrics.get("language") or detect_language(text)
    if position == "opening":
        op = classify_opening(text, lang)
        tags.add({"dialogue_open": "action_opening" if metrics.get("action_ratio", 0) > 0.1 else "dialogue_heavy_scene", "fragment_open": "cold_open", "thought_open": "internal_monologue", "status_screen_open": "status_screen_presentation", "transition_open": "transition", "sensory_open": "quiet_scene_opening"}.get(op, "quiet_scene_opening"))
        if op in ("fragment_open", "dialogue_open") and metrics.get("action_ratio", 0) > 0.08:
            tags.add("action_opening")
    if position == "ending":
        en = classify_ending(text, lang)
        if en in ("question_hook", "suspended_hook", "dialogue_hook", "punch_line") or (hook_type and hook_type not in ("none", "quiet-turn")):
            tags.add("chapter_cliffhanger")
        else:
            tags.add("soft_chapter_ending")
    if metrics.get("dialogue_ratio", 0) >= 0.45:
        tags.add("dialogue_heavy_scene")
    if metrics.get("internal_thought_ratio", 0) >= 0.25:
        tags.add("internal_monologue")
    if metrics.get("status_window_count", 0) >= 2:
        tags.add("status_screen_presentation")
    if metrics.get("action_ratio", 0) >= 0.18:
        tags.add("fight_choreography")
    if metrics.get("exposition_ratio", 0) >= 0.2:
        tags.add("exposition_delivery")
    if metrics.get("explicit_emotion_density", 0) <= 2.0 and metrics.get("internal_thought_ratio", 0) >= 0.1:
        tags.add("emotional_restraint")
    for fn in _keyword_functions(text):
        tags.add(fn)
    if scene_function:
        for fn in SCENE_FUNCTION_TO_BEATS.get(scene_function, ()):
            tags.add(fn)
    ordered = [t for t in BEAT_FUNCTIONS if t in tags]
    if not ordered:
        ordered = ["transition"] if position == "middle" else (["quiet_scene_opening"] if position == "opening" else ["soft_chapter_ending"])
    # Primary = the most specific evidence: ending/opening tags first, then keyword-derived.
    primary = next((t for t in ("chapter_cliffhanger", "soft_chapter_ending", "cold_open", "action_opening", "status_screen_presentation", "confession", "fight_choreography", "power_reveal") if t in ordered), ordered[0])
    return primary, ordered


def segment_chapter(text: str, *, target_chars: int = 500) -> List[Tuple[int, int, str]]:
    """Split a chapter into paragraph-aligned windows of roughly ``target_chars``."""
    windows: List[Tuple[int, int, str]] = []
    pos = 0
    buf_start: Optional[int] = None
    buf: List[str] = []
    buf_len = 0
    for para in split_paragraphs(text):
        idx = text.find(para, pos)
        if idx < 0:
            idx = pos
        pos = idx + len(para)
        if buf_start is None:
            buf_start = idx
        buf.append(para)
        buf_len += len(para) + 1
        if buf_len >= target_chars:
            windows.append((buf_start, pos, "\n".join(buf)))
            buf, buf_len, buf_start = [], 0, None
    if buf and buf_start is not None:
        if windows and buf_len < MIN_EXCERPT_CHARS:
            s, _, t = windows[-1]
            windows[-1] = (s, pos, t + "\n" + "\n".join(buf))
        else:
            windows.append((buf_start, pos, "\n".join(buf)))
    return windows


def build_candidates(
    *,
    chapter_card_id: Optional[int],
    chapter_number: int,
    text: str,
    manuscript_id: str,
    roles: Dict[str, str],
    scene_functions: Sequence[Tuple[int, int, str]] = (),
    hook_type: Optional[str] = None,
    language: Optional[str] = None,
) -> List[ExampleCandidate]:
    """Segment one chapter and tag each window. ``scene_functions`` = (start, end, function) spans from verified analysis."""
    lang = language or detect_language(text)
    out: List[ExampleCandidate] = []
    total = len(text)
    for start, end, window in segment_chapter(text):
        if len(window) < MIN_EXCERPT_CHARS:
            continue
        excerpt_raw = window[:MAX_EXCERPT_CHARS]
        m = measure(excerpt_raw, lang).as_dict()
        pos = _position(start, end, total)
        fn = next((f for s, e, f in scene_functions if s <= start < e), None)
        primary, tags = tag_passage(excerpt_raw, position=pos, metrics=m, scene_function=fn, hook_type=hook_type if pos == "ending" else None)
        excerpt, used_roles = redact_entities(excerpt_raw, roles)
        terms = sorted({t for t in tokenize(excerpt, lang) if len(t) >= 4 and not t.startswith("[role")})[:40]
        out.append(ExampleCandidate(
            example_id=stable_id(manuscript_id, chapter_number, start, end),
            chapter_card_id=chapter_card_id,
            chapter_number=chapter_number,
            span_start=start,
            span_end=end,
            excerpt=excerpt,
            language=lang,
            pov_type=infer_pov(measure(excerpt_raw, lang)),
            scene_type=fn or "",
            dominant_emotion="restrained" if m.get("explicit_emotion_density", 0) < 2 else "explicit",
            beat_function=primary,
            tags=tags,
            position=pos,
            dialogue_ratio=float(m.get("dialogue_ratio") or 0.0),
            pacing=_pacing(m),
            entity_roles=used_roles,
            metrics={k: m[k] for k in ("dialogue_ratio", "internal_thought_ratio", "exposition_ratio", "action_ratio", "sentence_len", "paragraph_len", "short_paragraph_ratio", "explicit_emotion_density", "opening_type", "ending_type", "speech_levels") if k in m},
            retrieval_terms=terms,
            evidence_hash=stable_id(excerpt_raw, length=32),
        ))
    return out


def store_examples(session: Session, project_id: int, manuscript_id: str, candidates: Iterable[ExampleCandidate], *, replace: bool = True) -> int:
    if replace:
        for row in session.exec(select(ReferenceExample).where(ReferenceExample.project_id == project_id, ReferenceExample.manuscript_id == manuscript_id)).all():
            session.delete(row)
        session.flush()
    n = 0
    for c in candidates:
        session.add(ReferenceExample(
            project_id=project_id, manuscript_id=manuscript_id, example_id=c.example_id, chapter_card_id=c.chapter_card_id,
            chapter_number=c.chapter_number, span_start=c.span_start, span_end=c.span_end, excerpt=c.excerpt, language=c.language,
            pov_type=c.pov_type, scene_type=c.scene_type, dominant_emotion=c.dominant_emotion, beat_function=c.beat_function,
            tags=list(c.tags), position=c.position, dialogue_ratio=c.dialogue_ratio, pacing=c.pacing, entity_roles=list(c.entity_roles),
            metrics=dict(c.metrics), retrieval_terms=list(c.retrieval_terms), evidence_hash=c.evidence_hash,
        ))
        n += 1
    session.flush()
    return n


def library_status(session: Session, project_id: int) -> Dict[str, Any]:
    rows = session.exec(select(ReferenceExample).where(ReferenceExample.project_id == project_id)).all()
    by_fn: Dict[str, int] = {}
    for r in rows:
        by_fn[r.beat_function] = by_fn.get(r.beat_function, 0) + 1
        for t in r.tags or []:
            if t != r.beat_function:
                by_fn[t] = by_fn.get(t, 0) + 0
    return {"examples": len(rows), "functions": dict(sorted(by_fn.items())), "chapters": len({r.chapter_number for r in rows}), "manuscripts": sorted({r.manuscript_id for r in rows})}


# ------------------------------------------------------------------ retrieval

@dataclass
class RetrievedExample:
    example_id: str
    beat_function: str
    chapter_number: int
    excerpt: str
    score: float
    reason: str
    evidence_hash: str
    language: str
    tags: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


@dataclass
class RetrievalRequest:
    functions: List[str]
    language: Optional[str] = None
    pov_type: Optional[str] = None
    beat_descriptions: Dict[str, str] = field(default_factory=dict)
    target_dialogue_ratio: Optional[float] = None
    target_pacing: Optional[str] = None
    max_chapter_number: Optional[int] = None  # spoiler guard: examples only from <= this source chapter
    recently_used: Set[str] = field(default_factory=set)
    budget_chars: int = 2400
    per_function: int = 1


def _lexical_score(terms: Sequence[str], query: str, lang: str) -> float:
    if not query:
        return 0.0
    q = set(tokenize(query, lang))
    if not q:
        return 0.0
    t = set(terms)
    return len(q & t) / float(len(q))


def retrieve(session: Session, project_id: int, req: RetrievalRequest) -> Tuple[List[RetrievedExample], Dict[str, Any]]:
    """Hybrid retrieval with diversity and budget. Returns (examples, trace)."""
    stmt = select(ReferenceExample).where(ReferenceExample.project_id == project_id)
    if req.max_chapter_number is not None:
        stmt = stmt.where(ReferenceExample.chapter_number <= int(req.max_chapter_number))
    rows = session.exec(stmt).all()
    trace: Dict[str, Any] = {"requested_functions": list(req.functions), "candidates": len(rows), "selected": [], "skipped": []}
    chosen: List[RetrievedExample] = []
    used_chapters: Set[int] = set()
    used_ids: Set[str] = set()
    remaining = req.budget_chars
    for fn in req.functions:
        scored: List[Tuple[float, ReferenceExample, str]] = []
        for r in rows:
            if r.example_id in used_ids:
                continue
            fn_match = 1.0 if r.beat_function == fn else (0.6 if fn in (r.tags or []) else 0.0)
            if fn_match == 0.0:
                continue
            if req.language and r.language != req.language:
                continue
            score = fn_match
            reasons = [f"function={'primary' if fn_match == 1.0 else 'tag'}"]
            if req.pov_type and r.pov_type == req.pov_type:
                score += 0.15
                reasons.append("pov match")
            lex = _lexical_score(r.retrieval_terms or [], req.beat_descriptions.get(fn, ""), r.language)
            if lex:
                score += 0.4 * lex
                reasons.append(f"lexical={lex:.2f}")
            if req.target_dialogue_ratio is not None:
                prox = 1.0 - min(1.0, abs(r.dialogue_ratio - req.target_dialogue_ratio) / 0.5)
                score += 0.2 * prox
            if req.target_pacing and r.pacing == req.target_pacing:
                score += 0.1
                reasons.append("pacing match")
            if r.example_id in req.recently_used:
                score -= 0.5
                reasons.append("recently used penalty")
            if r.chapter_number in used_chapters:
                score -= 0.3
                reasons.append("chapter diversity penalty")
            scored.append((score, r, "; ".join(reasons)))
        scored.sort(key=lambda t: (-t[0], t[1].chapter_number, t[1].example_id))
        taken = 0
        for score, r, reason in scored:
            if taken >= req.per_function:
                break
            if len(r.excerpt) > remaining:
                trace["skipped"].append({"example_id": r.example_id, "reason": "budget"})
                continue
            chosen.append(RetrievedExample(example_id=r.example_id, beat_function=fn, chapter_number=r.chapter_number, excerpt=r.excerpt, score=round(score, 3), reason=reason, evidence_hash=r.evidence_hash, language=r.language, tags=list(r.tags or [])))
            trace["selected"].append({"example_id": r.example_id, "function": fn, "chapter": r.chapter_number, "score": round(score, 3), "reason": reason})
            used_ids.add(r.example_id)
            used_chapters.add(r.chapter_number)
            remaining -= len(r.excerpt)
            taken += 1
        if taken == 0:
            trace["skipped"].append({"function": fn, "reason": "no candidate"})
    trace["budget_chars"] = req.budget_chars
    trace["used_chars"] = req.budget_chars - remaining
    return chosen, trace


def functions_from_outline(outline: Dict[str, Any]) -> List[str]:
    """Derive required beat functions from an original Chapter Outline card content."""
    fns: List[str] = []

    def add(f: str) -> None:
        if f in BEAT_FUNCTIONS and f not in fns:
            fns.append(f)

    for beat in outline.get("beats") or []:
        if isinstance(beat, dict):
            add(str(beat.get("function") or ""))
            for f in beat.get("functions") or []:
                add(str(f))
    for f in outline.get("beat_functions") or []:
        add(str(f))
    if outline.get("opening_function"):
        add(str(outline["opening_function"]))
    if outline.get("ending_function"):
        add(str(outline["ending_function"]))
    if not fns:
        text = str(outline.get("overview") or "")
        for f in _keyword_functions(text):
            add(f)
    return fns


__all__ = [
    "EXAMPLES_VERSION", "MAX_EXCERPT_CHARS", "SCENE_FUNCTION_TO_BEATS", "ExampleCandidate", "RetrievalRequest", "RetrievedExample",
    "build_candidates", "functions_from_outline", "library_status", "redact_entities", "retrieve", "segment_chapter", "store_examples", "tag_passage",
]
