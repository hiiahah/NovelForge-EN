"""Draft validation layers and the Style Adherence Report.

Consumers: ``pipeline`` (decides repair / reject / commit), UI reports, tests.

Layers: entity, fact, outline, pov, character, temporal, style, originality.
Each returns ``Issue`` objects with severity (critical/high/medium/low), a
span in the draft when available, and a repair hint. A draft is *committable*
only when no critical/high issue remains.

Style validation is deterministic (metrics vs. fingerprint target ranges). A
separate ``model_style_criteria`` builds the abstract criteria given to an
evaluator model; its answers are stored next to the metrics, never instead of
them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.services.forge import firewall as fw
from app.services.forge.claims import Claim, named_entities
from app.services.forge.textmetrics import detect_language, in_range, measure, split_paragraphs, split_sentences, tokenize

VALIDATORS_VERSION = "validators-1"
STYLE_EVALUATOR_VERSION = "style-eval-1"


@dataclass
class Issue:
    layer: str
    code: str
    severity: str
    message: str
    span: Optional[Tuple[int, int]] = None
    hint: str = ""
    evidence: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"layer": self.layer, "code": self.code, "severity": self.severity, "message": self.message, "span": list(self.span) if self.span else None, "hint": self.hint, "evidence": self.evidence[:200]}


@dataclass
class ValidationReport:
    issues: List[Issue] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    style: Dict[str, Any] = field(default_factory=dict)
    originality: Dict[str, Any] = field(default_factory=dict)
    version: str = VALIDATORS_VERSION

    @property
    def blocking(self) -> List[Issue]:
        return [i for i in self.issues if i.severity in ("critical", "high")]

    @property
    def passed(self) -> bool:
        return not self.blocking

    def as_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "issues": [i.as_dict() for i in self.issues], "blocking": len(self.blocking), "metrics": self.metrics, "style": self.style, "originality": self.originality, "version": self.version}


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


# ------------------------------------------------------------------ entities
def validate_entities(prose: str, *, allowed: Iterable[str], source_entities: Iterable[str] = (), language: Optional[str] = None, max_new_named: int = 0) -> List[Issue]:
    allowed_l = {_norm(a) for a in allowed if a}
    # Tokens of allowed multi-word names ("the Salt Archive" -> archive) are legitimate short references.
    allowed_tokens = {t for a in allowed_l for t in a.split() if len(t) >= 3 and t not in ("the", "and", "of")}
    source_l = {_norm(s) for s in source_entities if s}
    issues: List[Issue] = []
    for name, spans in named_entities(prose, language).items():
        n = _norm(name)
        parts = n.split()
        if n in allowed_l or any(p in allowed_l for p in parts) or all(p in allowed_tokens for p in parts):
            continue
        if n in source_l or any(p in source_l for p in parts):
            issues.append(Issue("entity", "source_entity_leak", "critical", f"Source-novel entity '{name}' appears in the draft", spans[0], "Replace with an allowed original entity or remove", name))
            continue
        # Unknown proper name: recurring (>=2 mentions) counts as an unauthorized recurring entity.
        if len(spans) >= 2:
            issues.append(Issue("entity", "unauthorized_entity", "high", f"Unplanned named entity '{name}' recurs {len(spans)} times", spans[0], "Remove the name or replace with an allowed participant / unnamed extra", name))
        elif max_new_named == 0:
            issues.append(Issue("entity", "unplanned_name", "medium", f"Unplanned proper name '{name}' (single mention)", spans[0], "Prefer an unnamed extra", name))
    return issues


# --------------------------------------------------------------------- facts
def validate_facts(claims: Sequence[Claim], *, locked: Dict[Tuple[str, str], Any], planned: Iterable[str], allowed: Iterable[str], paid_off: Iterable[str] = (), prose: str = "") -> List[Issue]:
    """Contradictions with locked canon and unsupported persistent additions."""
    issues: List[Issue] = []
    planned_text = " ".join(_norm(p) for p in planned)
    allowed_l = {_norm(a) for a in allowed}
    low = prose.lower()
    for payoff in paid_off:
        p = _norm(payoff)
        if p and p not in planned_text and p in low:
            m = low.find(p)
            issues.append(Issue("fact", "duplicate_payoff", "high", f"Payoff already delivered in an earlier chapter recurs: '{payoff}'", (m, m + len(p)), "Remove the repeated payoff; refer back to it instead", str(payoff)))
    for c in claims:
        subj = _norm(c.subject)
        if c.support == "unsupported":
            issues.append(Issue("fact", "unsupported_model_claim", "medium", f"Model claim '{c.kind}:{c.subject}={c.value}' has no evidence in the prose", None, "Ignore; not applied", c.evidence))
            continue
        if c.kind == "relationship_changed":
            current = locked.get((subj, "private_relationship"))
            if current is not None and _norm(current) != _norm(c.value) and _norm(c.value) not in planned_text:
                issues.append(Issue("fact", "relationship_reset", "high", f"Relationship {c.subject} changes from '{current}' to '{c.value}' without a planned beat", c.span, "Keep the locked relationship state or plan the change in the outline", c.evidence))
            continue
        if subj and subj not in allowed_l:
            continue  # entity layer already reports it
        if c.kind == "death":
            status = locked.get((subj, "status"))
            if status is not None and status != "dead" and "die" not in planned_text and "death" not in planned_text and "dead" not in planned_text and "kill" not in planned_text:
                issues.append(Issue("fact", "unplanned_death", "critical", f"{c.subject} dies but no beat plans it", c.span, "Remove the death or restore the character", c.evidence))
        elif c.kind == "location_changed":
            loc = _norm(c.value)
            if loc and loc not in planned_text and loc not in allowed_l:
                issues.append(Issue("fact", "unplanned_location", "high", f"{c.subject} moves to unplanned location '{c.value}'", c.span, "Use a planned/known location or neutral wording", c.evidence))
        elif c.kind in ("possession_gained",):
            val = _norm(c.value)
            if val and val not in planned_text and not any(val in a or a in val for a in allowed_l):
                issues.append(Issue("fact", "unplanned_possession", "high", f"{c.subject} gains unplanned item '{c.value}'", c.span, "Drop the acquisition or make it nonpersistent", c.evidence))
        elif c.kind == "injury":
            if _norm(c.value) not in planned_text and "injur" not in planned_text and "wound" not in planned_text and "hurt" not in planned_text and "다쳤" not in planned_text:
                issues.append(Issue("fact", "unplanned_injury", "high", f"{c.subject} is {c.value} but no beat plans an injury", c.span, "Remove the injury or reduce to nonpersistent discomfort", c.evidence))
        elif c.kind == "membership_changed":
            if _norm(c.value) not in planned_text:
                issues.append(Issue("fact", "unplanned_membership", "high", f"{c.subject} changes membership ('{c.value}') without a plan", c.span, "Remove", c.evidence))
    return issues


# ------------------------------------------------------------------- outline
def _beat_terms(beat: Dict[str, Any]) -> List[str]:
    text = str(beat.get("description") or beat.get("text") or "")
    keys = beat.get("keywords") or []
    terms: List[str] = []
    for k in keys:
        if k:
            norm_k = _norm(k)
            if norm_k and norm_k not in terms:
                terms.append(norm_k)
            for tok in tokenize(str(k)):
                tok_norm = _norm(tok)
                if len(tok_norm) >= 4 and tok_norm not in _PROHIBITED_STOP and tok_norm not in terms:
                    terms.append(tok_norm)
    desc_terms = [t for t in tokenize(text) if len(t) >= 4 and t not in _PROHIBITED_STOP]
    for dt in desc_terms:
        if dt not in terms:
            terms.append(dt)
    return terms


def locate_beats(prose: str, beats: Sequence[Dict[str, Any]]) -> List[Optional[int]]:
    """Approximate position (char offset) of each beat in the prose, or None when not found.

    A beat is found when at least half its keywords occur in a paragraph window;
    position = first such window's offset after the previous beat's position.
    """
    low = prose.lower()
    paras = split_paragraphs(prose)
    offsets: List[int] = []
    pos = 0
    for p in paras:
        idx = low.find(p.lower(), pos)
        offsets.append(idx if idx >= 0 else pos)
        pos = (idx if idx >= 0 else pos) + len(p)
    positions: List[Optional[int]] = []
    cursor = 0
    for beat in beats:
        terms = _beat_terms(beat)
        if not terms:
            positions.append(None)
            continue
        need = max(1, min(2, (len(terms) + 1) // 2))
        found: Optional[int] = None
        # Narrow windows first (one paragraph), then widen to three, preferring
        # positions at/after the previous beat.
        # Strictly-forward, full-keyword matches first so a recurring keyword
        # earlier in the chapter cannot pull a later beat out of order.
        for width in (1, 2, 3):
            for min_hits, lookback in ((len(terms), 0), (need, 0), (need, 400), (need, None)):
                for i in range(len(paras)):
                    if lookback is not None and offsets[i] < cursor - lookback:
                        continue
                    window = " ".join(paras[i:i + width]).lower()
                    hits = sum(1 for t in terms if t in window)
                    if hits >= min_hits:
                        found = offsets[i]
                        break
                if found is not None:
                    break
            if found is not None:
                break
        positions.append(found)
        if found is not None:
            cursor = found
    return positions


def _term_pattern(t: str) -> str:
    # Stem English inflectional suffixes (e.g. reveals -> reveal, poisoning -> poison, forges -> forg)
    # to catch morphological variations and paraphrasing in prose.
    stem = re.sub(r"(ing|ed|es|s|ers|er)$", "", t)
    if len(stem) >= 4:
        return rf"(?<![\w]){re.escape(stem)}\w*"
    return rf"(?<![\w]){re.escape(t)}(?![\w])"


def validate_outline(prose: str, *, beats: Sequence[Dict[str, Any]], forbidden: Iterable[str], language: Optional[str] = None, participants: Optional[Iterable[str]] = None) -> List[Issue]:
    issues: List[Issue] = []
    positions = locate_beats(prose, beats)
    for i, (beat, pos) in enumerate(zip(beats, positions)):
        if pos is None:
            issues.append(Issue("outline", "beat_missing", "high", f"Beat {i + 1} not found in the draft: {str(beat.get('description') or beat.get('text') or '')[:100]}", None, "Write the missing beat at its position"))
    found = [(i, p) for i, p in enumerate(positions) if p is not None]
    for (i1, p1), (i2, p2) in zip(found, found[1:]):
        if p2 < p1:
            issues.append(Issue("outline", "beat_out_of_order", "high", f"Beat {i2 + 1} occurs before beat {i1 + 1}", (p2, p2 + 1), "Reorder the scenes to follow the outline"))
    lang = language or detect_language(prose)
    sents = [s for s in split_sentences(prose, lang) if not s.rstrip().endswith(("?", "？"))]
    name_tokens: Set[str] = set()
    if participants:
        for p in participants:
            name_tokens.update(tokenize(str(p).lower()))
    for f in forbidden:
        terms = [t for t in tokenize(re.sub(r"^\(ch\.\d+\)\s*", "", str(f))) if len(t) >= 3 and t not in _PROHIBITED_STOP]
        if len(terms) < 2:
            continue
        content_terms = [t for t in terms if t not in name_tokens]
        min_content = min(2, len(content_terms)) if content_terms else 1
        threshold = max(2, int(len(terms) * 0.65 + 0.5)) if len(terms) >= 3 else len(terms)
        hit_span = None
        for i in range(len(sents)):
            window = " ".join(sents[i:i + 2]).lower()
            matched_terms = [t for t in terms if re.search(_term_pattern(t), window, re.I)]
            matched_content = [t for t in matched_terms if t not in name_tokens]
            if len(matched_terms) >= threshold and len(matched_content) >= min_content:
                idx = prose.find(sents[i])
                hit_span = (idx, idx + len(sents[i])) if idx >= 0 else None
                break
        if hit_span is not None:
            issues.append(Issue("outline", "future_beat_advanced", "critical", f"Forbidden / future outcome appears: {str(f)[:120]}", hit_span, "Remove the future event; end where the outline ends", str(f)))
    return issues


# ------------------------------------------------------------------------ pov
_THOUGHT_VERBS_EN = r"(?:thought|wondered|felt|knew|realized|realised|remembered|hoped|feared|decided|wanted|hated|loved|suspected)(?:\s+(?:about|of|that|how|why|whether))?"
_THOUGHT_VERBS_KO = r"(?:생각했다|느꼈다|알았다|깨달았다|기억했다|바랐다|두려워했다|결심했다|원했다|의심했다)"


def validate_pov(prose: str, *, pov: str, others: Iterable[str], pov_type: str = "third_person", prohibited: Iterable[str] = (), language: Optional[str] = None) -> List[Issue]:
    issues: List[Issue] = []
    lang = language or detect_language(prose)
    for other in others:
        if not other or _norm(other) == _norm(pov):
            continue
        if lang == "ko":
            rx = re.compile(rf"{re.escape(other)}(?:은|는|이|가)\s+[^.!?\n]{{0,40}}?{_THOUGHT_VERBS_KO}")
        else:
            rx = re.compile(rf"\b{re.escape(other)}\s+(?:secretly\s+|silently\s+|privately\s+)?{_THOUGHT_VERBS_EN}\b", re.I)
        for m in rx.finditer(prose):
            # Not head-hopping if the sentence is framed as the POV's perception.
            sent_start = max(prose.rfind("\n", 0, m.start()), prose.rfind(". ", 0, m.start()), prose.rfind("? ", 0, m.start()), prose.rfind("! ", 0, m.start()))
            sent_end_match = re.search(r"[.!?…](?:\s|$)", prose[m.end():])
            sent_end = m.end() + (sent_end_match.end() if sent_end_match else 0)
            sentence = prose[sent_start + 1:sent_end]
            if re.search(r"\b(?:seemed|looked as if|as though|apparently|must have|probably|clearly)\b|(?:듯했다|것 같았다|처럼 보였다|아마)", sentence, re.I):
                continue
            issues.append(Issue("pov", "head_hopping", "high", f"Interior state of non-POV character '{other}' narrated directly", (m.start(), m.end()), f"Rewrite as {pov}'s observation or inference", m.group(0)))
    if pov_type == "first_person":
        third = len(re.findall(r"\b(?:he|she)\s+(?:thought|felt|knew)\b", prose, re.I)) if lang != "ko" else 0
        if third >= 3:
            issues.append(Issue("pov", "pov_drift", "high", "First-person chapter reports other characters' feelings as fact", None, "Keep to the narrator's perception"))
    # Questions are not reveals: a POV wondering about a fact is legitimate suspense.
    name_tokens: Set[str] = set()
    if pov:
        name_tokens.update(tokenize(str(pov).lower()))
    for o in others:
        name_tokens.update(tokenize(str(o).lower()))

    sents = [s for s in split_sentences(prose, lang) if not s.rstrip().endswith(("?", "？"))]
    for p in prohibited:
        p_clean = re.sub(r"^\(ch\.\d+\)\s*", "", re.sub(r"\s*\(.*?\)\s*$", "", str(p)))
        terms = [t for t in tokenize(p_clean) if len(t) >= 3 and t not in _PROHIBITED_STOP]
        if len(terms) < 2:
            continue
        content_terms = [t for t in terms if t not in name_tokens]
        min_content = min(2, len(content_terms)) if content_terms else 1
        threshold = max(2, int(len(terms) * 0.65 + 0.5)) if len(terms) >= 3 else len(terms)
        hit_span = None
        for i in range(len(sents)):
            window = " ".join(sents[i:i + 2]).lower()
            matched_terms = [t for t in terms if re.search(_term_pattern(t), window, re.I)]
            matched_content = [t for t in matched_terms if t not in name_tokens]
            if len(matched_terms) >= threshold and len(matched_content) >= min_content:
                idx = prose.find(sents[i])
                hit_span = (idx, idx + len(sents[i])) if idx >= 0 else None
                break
        if hit_span is not None:
            issues.append(Issue("pov", "forbidden_reveal", "critical", f"Prohibited knowledge surfaces: {p_clean[:120]}", hit_span, "Remove every hint of this fact", p_clean))
    return issues


_PROHIBITED_STOP = {"the", "and", "for", "was", "are", "not", "but", "his", "her", "she", "him", "has", "had", "all", "any", "one", "two", "who", "how", "why", "did", "does", "that", "this", "with", "from", "have", "been", "were", "will", "would", "about", "before", "after", "their", "there", "which", "when", "what", "into", "onto", "over", "under", "than", "then", "them", "they", "your", "some", "very", "also", "just", "only", "chapter", "planned", "reveal", "revealed", "reveals", "revealing", "payoff", "window", "along", "pov", "unaware", "yet"}


# -------------------------------------------------------------------- character
def validate_characters(prose: str, *, character_cards: Dict[str, Dict[str, Any]], participants: Iterable[str]) -> List[Issue]:
    issues: List[Issue] = []
    for name in participants:
        card = character_cards.get(_norm(name)) or {}
        voice = card.get("voice") or {}
        for phrase in voice.get("forbidden_speech") or []:
            if phrase and len(str(phrase)) >= 4:
                for m in re.finditer(rf"[\"“][^\"”]*{re.escape(str(phrase))}[^\"”]*[\"”]", prose, re.I):
                    # Only if the line is attributed to this character nearby.
                    ctx = prose[max(0, m.start() - 160): m.end() + 160]
                    if name.lower() in ctx.lower():
                        issues.append(Issue("character", "forbidden_speech", "high", f"{name} says forbidden speech pattern '{phrase}'", (m.start(), m.end()), "Rewrite the line in the character's voice", m.group(0)))
        rules = card.get("consistency_rules") or {}
        for restriction in rules.get("knowledge_restrictions") or []:
            terms = [t for t in tokenize(str(restriction)) if len(t) >= 6 and t not in ("before", "chapter", "doesnt", "does", "know", "knows", "about")]
            if len(terms) >= 2 and all(t in prose.lower() for t in terms[:3]) and name.lower() in prose.lower():
                issues.append(Issue("character", "knowledge_restriction", "medium", f"{name} may violate knowledge restriction: {restriction[:100]}", None, "Check the POV boundary", str(restriction)))
    return issues


# ---------------------------------------------------------------------- temporal
_TIME_MARKERS_EN = [("dawn", 0), ("morning", 1), ("noon", 2), ("midday", 2), ("afternoon", 3), ("dusk", 4), ("evening", 5), ("night", 6), ("midnight", 7)]
_TIME_MARKERS_KO = [("새벽", 0), ("아침", 1), ("정오", 2), ("한낮", 2), ("오후", 3), ("저녁", 5), ("밤", 6), ("자정", 7)]


def validate_temporal(prose: str, *, start_time: Optional[str] = None, language: Optional[str] = None) -> List[Issue]:
    issues: List[Issue] = []
    lang = language or detect_language(prose)
    markers = _TIME_MARKERS_KO if lang == "ko" else _TIME_MARKERS_EN
    seq: List[Tuple[int, int, str]] = []
    for word, order in markers:
        for m in re.finditer(rf"(?<![\w]){re.escape(word)}(?![\w])" if lang != "ko" else re.escape(word), prose, re.I):
            # Skip when part of a day boundary phrase ("the next morning") which legitimately resets.
            ctx = prose[max(0, m.start() - 20): m.start()].lower()
            if "next" in ctx or "다음" in ctx or "이튿날" in ctx or "following" in ctx:
                seq.append((m.start(), -1, word))
            else:
                seq.append((m.start(), order, word))
    seq.sort()
    last = -1
    for pos, order, word in seq:
        if order == -1:
            last = -1
            continue
        if last >= 0 and order < last - 1:
            issues.append(Issue("temporal", "time_inversion", "medium", f"Time marker '{word}' follows a later time-of-day marker without a day change", (pos, pos + len(word)), "Fix the time sequence or mark the day change", word))
        last = max(last, order)
    return issues


# ------------------------------------------------------------------------ style
STYLE_DIMENSIONS = (
    ("sentence_len_mean", lambda m: (m.get("sentence_len") or {}).get("mean", 0.0), 2.0),
    ("paragraph_len_mean", lambda m: (m.get("paragraph_len") or {}).get("mean", 0.0), 6.0),
    ("dialogue_ratio", lambda m: m.get("dialogue_ratio", 0.0), 0.08),
    ("narration_ratio", lambda m: m.get("narration_ratio", 0.0), 0.08),
    ("internal_thought_ratio", lambda m: m.get("internal_thought_ratio", 0.0), 0.08),
    ("exposition_ratio", lambda m: m.get("exposition_ratio", 0.0), 0.06),
    ("question_freq", lambda m: m.get("question_freq", 0.0), 0.05),
    ("exclamation_freq", lambda m: m.get("exclamation_freq", 0.0), 0.05),
    ("fragment_freq", lambda m: m.get("fragment_freq", 0.0), 0.06),
    ("short_paragraph_ratio", lambda m: m.get("short_paragraph_ratio", 0.0), 0.1),
    ("explicit_emotion_density", lambda m: m.get("explicit_emotion_density", 0.0), 2.0),
    ("ellipsis_freq", lambda m: m.get("ellipsis_freq", 0.0), 3.0),
)


def style_report(prose: str, fingerprint: Dict[str, Any], *, beat_positions: Optional[List[Optional[int]]] = None) -> Dict[str, Any]:
    """Deterministic Style Adherence Report against fingerprint target ranges."""
    lang = fingerprint.get("language") or None
    m = measure(prose, lang).as_dict()
    targets = fingerprint.get("targets") or {}
    results: Dict[str, Any] = {}
    failed: List[str] = []
    deviations: List[Dict[str, Any]] = []
    for name, getter, slack in STYLE_DIMENSIONS:
        dist = targets.get(name) or {}
        value = float(getter(m) or 0.0)
        ok = in_range(value, dist, slack)
        results[name] = {"value": round(value, 4), "target_p10": dist.get("p10"), "target_p90": dist.get("p90"), "target_median": dist.get("median"), "ok": ok}
        if not ok:
            failed.append(name)
            deviations.append({"dimension": name, "value": round(value, 4), "target": [dist.get("p10"), dist.get("p90")], "direction": "high" if value > float(dist.get("p90", 0)) else "low"})
    for cat in ("opening_type", "ending_type"):
        dist = targets.get(cat) or {}
        value = m.get(cat)
        ok = (not dist) or dist.get(value, 0.0) >= 0.1 or value in list(dist)[:3]
        results[cat] = {"value": value, "target": dist, "ok": ok}
        if not ok:
            failed.append(cat)
            deviations.append({"dimension": cat, "value": value, "target": list(dist)[:3]})
    if lang == "ko":
        tl = targets.get("speech_levels") or {}
        for level, dist in tl.items():
            value = float((m.get("speech_levels") or {}).get(level, 0.0))
            ok = in_range(value, dist, 0.15)
            results[f"speech_level_{level}"] = {"value": value, "target_p10": dist.get("p10"), "target_p90": dist.get("p90"), "ok": ok}
            if not ok:
                failed.append(f"speech_level_{level}")
    scene_count = len([p for p in split_paragraphs(prose) if not p.strip()]) + prose.count("\n\n\n") + 1
    results["scene_count"] = {"value": scene_count}
    if beat_positions:
        found = [p for p in beat_positions if p is not None]
        spacing = [b - a for a, b in zip(found, found[1:])]
        results["beat_spacing_mean"] = {"value": round(sum(spacing) / len(spacing), 1) if spacing else None}
        reveal_idx = None
        results["reveal_position"] = {"value": reveal_idx}
    total = len([n for n, _, _ in STYLE_DIMENSIONS]) + 2
    score = round(1.0 - len(failed) / float(total), 3)
    recommendations = []
    for d in deviations:
        dim = d["dimension"]
        if dim in ("sentence_len_mean", "paragraph_len_mean"):
            recommendations.append(f"{'Shorten' if d.get('direction') == 'high' else 'Lengthen'} {dim.replace('_mean', 's').replace('_', ' ')} toward {d['target']}")
        elif dim in ("dialogue_ratio", "internal_thought_ratio", "exposition_ratio"):
            recommendations.append(f"{'Reduce' if d.get('direction') == 'high' else 'Increase'} {dim.replace('_', ' ')} toward {d['target']}")
        elif dim in ("opening_type", "ending_type"):
            recommendations.append(f"Use a {dim.replace('_', ' ')} among {d['target']} instead of '{d['value']}'")
        else:
            recommendations.append(f"Adjust {dim.replace('_', ' ')} toward {d['target']}")
    return {
        "evaluator_version": STYLE_EVALUATOR_VERSION,
        "metrics_version": m.get("metrics_version"),
        "fingerprint_version": fingerprint.get("version"),
        "fingerprint_dependency_hash": fingerprint.get("dependency_hash"),
        "results": results,
        "failed_dimensions": failed,
        "deviations": deviations,
        "adherence_score": score,
        "repair_recommendations": recommendations,
        "model_evaluation": None,
    }


def model_style_criteria(fingerprint: Dict[str, Any]) -> List[Dict[str, str]]:
    """Abstract criteria (never 'does it sound like the source') for the evaluator model."""
    layers = fingerprint.get("layers") or {}

    def rule(layer: str) -> str:
        return "; ".join((layers.get(layer) or {}).get("rules") or [])[:300]

    return [
        {"dimension": "pov_feel", "criterion": rule("pov_focalization")},
        {"dimension": "narrative_distance", "criterion": rule("global_voice")},
        {"dimension": "emotional_restraint", "criterion": rule("emotional_expression")},
        {"dimension": "humor_behavior", "criterion": rule("humor")},
        {"dimension": "exposition_strategy", "criterion": rule("exposition")},
        {"dimension": "dialogue_dynamics", "criterion": rule("dialogue")},
        {"dimension": "pacing", "criterion": rule("rhythm")},
        {"dimension": "tension_progression", "criterion": rule("suspense_reveal")},
        {"dimension": "chapter_hook", "criterion": rule("chapter_ending")},
    ]


def validate_style(prose: str, fingerprint: Dict[str, Any], *, beat_positions: Optional[List[Optional[int]]] = None, max_failed: int = 4) -> Tuple[List[Issue], Dict[str, Any]]:
    report = style_report(prose, fingerprint, beat_positions=beat_positions)
    issues: List[Issue] = []
    failed = report["failed_dimensions"]
    core = [f for f in failed if f in ("dialogue_ratio", "sentence_len_mean", "paragraph_len_mean", "ending_type", "opening_type", "internal_thought_ratio")]
    if len(failed) > max_failed or len(core) >= 3:
        issues.append(Issue("style", "style_out_of_range", "high", f"{len(failed)} style dimensions out of target range: {', '.join(failed)}", None, "; ".join(report["repair_recommendations"][:4])))
    elif failed:
        issues.append(Issue("style", "style_deviation", "low", f"Minor style deviations: {', '.join(failed)}", None, "; ".join(report["repair_recommendations"][:3])))
    return issues, report


# -------------------------------------------------------------------- originality
def validate_originality(prose: str, profile: Optional[fw.SourceProfile], *, allowed: Iterable[str]) -> Tuple[List[Issue], Dict[str, Any]]:
    if profile is None:
        return [], {"passed": True, "skipped": "no source profile"}
    report = fw.check_text(prose, profile, allowed_names=allowed)
    issues = [Issue("originality", f.check, "critical" if f.severity == "critical" else ("high" if f.severity == "high" else "low"), f.detail, f.span, "Rewrite the span with original wording / entities", f.matched) for f in report.findings]
    return issues, report.as_dict()


__all__ = [
    "STYLE_EVALUATOR_VERSION", "VALIDATORS_VERSION", "Issue", "ValidationReport", "locate_beats", "model_style_criteria", "style_report",
    "validate_characters", "validate_entities", "validate_facts", "validate_originality", "validate_outline", "validate_pov", "validate_style", "validate_temporal",
]
