"""Originality Firewall.

Producer: ``SourceProfile.from_chapters`` (built from the imported manuscript).
Consumers: original-Bible acceptance, draft validation (``validators``), audits.

Checks (all deterministic, no embeddings required):
1. named-entity overlap        (source entity names / aliases appearing in the text)
2. distinctive-term overlap    (rare capitalized or Hangul terms specific to the source)
3. long phrase overlap         (exact shared token windows >= ``phrase_window`` tokens)
4. dialogue overlap            (quoted lines shared with the source)
5. rare n-gram overlap         (4-grams that occur in the source but are rare in general prose)
6. scene-summary similarity    (token Jaccard between summaries)
7. ordered beat-sequence similarity (LCS ratio over beat-function sequences)
8. character-role mapping similarity
9. location / object similarity
10. accidental quotation       (verbatim spans located in the source)

Similarity is never judged by embeddings alone; every finding carries the
matching span so the repair step can target it.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from app.services.forge.textmetrics import detect_language, normalize_for_index, tokenize

FIREWALL_VERSION = "firewall-2"

_CAP_TERM = re.compile(r"\b[A-Z][a-zA-Z'\-]{2,}(?:\s+[A-Z][a-zA-Z'\-]{2,}){0,2}\b")
_HANGUL_TERM = re.compile(r"[\uac00-\ud7a3]{2,6}")
_QUOTED = re.compile(r"[\"“”「『]([^\"“”」』\n]{6,300})[\"“”」』]")
_EN_STOP = {
    "The", "And", "But", "She", "He", "They", "It", "That", "This", "There", "Then", "When", "What", "Why", "How", "Who", "Where",
    "You", "His", "Her", "Their", "Our", "For", "With", "From", "Not", "Yes", "No", "If", "In", "On", "At", "As", "Of", "To", "So",
    "Chapter", "Volume", "Part", "Prologue", "Epilogue", "Interlude", "Before", "After", "Because", "While", "Still", "Even", "Just",
    "Now", "Here", "Once", "Maybe", "Perhaps", "Nothing", "Something", "Someone", "Everyone", "Nobody", "All", "One", "Two", "Three",
    "Only", "Again", "Another", "Every", "Some", "Any", "Each", "Both", "Either", "Neither", "Well", "Right", "Left", "Good", "Fine", "Okay",
    "Please", "Thank", "Sorry", "Wait", "Stop", "Look", "Listen", "Come", "Let", "Get", "Go", "Do", "Did", "Was", "Were", "Had", "Have", "Has",
    "Would", "Could", "Should", "Will", "Can", "May", "Might", "Must", "Shall", "Am", "Are", "Is", "Be", "Been", "Being", "Its", "My", "Me", "We", "Us",
    "Morning", "Night", "Day", "Evening", "Afternoon", "Today", "Tomorrow", "Yesterday", "Sir", "Madam", "Miss", "Mister", "Lord", "Lady",
}
_KO_COMMON = {"그리고", "그러나", "하지만", "그런데", "그래서", "그때", "지금", "여기", "거기", "저기", "우리", "너희", "당신", "사람", "시간", "순간", "이제", "다시", "정말", "너무", "아주", "조금", "모두", "누구", "무엇", "어디", "어떻게", "왜냐하면", "때문에", "그것", "이것", "저것", "오늘", "내일", "어제", "아침", "저녁", "밤에", "하나", "둘이", "셋이"}
_GENERIC_ENTITY_WORDS = {
    w.lower() for w in _EN_STOP
} | {
    "first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth",
    "last", "senior", "junior", "elder", "younger",
    "empress", "emperor", "king", "queen", "prince", "princess", "monarch", "ruler",
    "the empress", "the emperor", "the king", "the queen", "the prince", "the princess",
    "master", "captain", "commander", "general", "soldier", "guard", "servant", "maid",
    "duke", "duchess", "marquis", "marchioness", "earl", "count", "countess", "viscount", "baron", "baroness",
    "priest", "priestess", "bishop", "pope", "saint", "hero", "villain", "extra", "protagonist", "antagonist",
    "father", "mother", "brother", "sister", "son", "daughter", "uncle", "aunt", "cousin",
    "man", "woman", "boy", "girl", "child", "person", "someone", "anyone", "everyone",
    "the protagonist", "the villain", "the hero", "the extra", "crowd", "death", "rose", "flower", "flowers",
    "tree", "trees", "water", "fire", "earth", "wind", "shadow", "light", "darkness", "sun", "moon", "star", "stars",
    "gold", "silver", "iron", "stone", "blood",
    "wolf", "wolves", "rook", "rooks", "pawn", "pawns", "knight", "knights", "mage", "mages",
    "snow", "butler", "coachman", "servants", "rabbit", "rabbits", "mom", "dad", "papa", "gramps", "grandpa",
    "young man", "old man", "principal", "dean", "dorm master", "evil god", "god", "goddess", "saintess",
    "head maid", "narrator", "dog", "cat", "horse", "dragon", "beast", "sword", "shield", "bow", "spear",
}

GENERIC_CATEGORY_WORDS = {
    "students", "servants", "professors", "officials", "captives", "workers", "guards",
    "heads", "mages", "knights", "teachers", "maids", "subordinates", "monsters", "people",
    "nobles", "elders", "bandits", "merchants", "attendants", "faculty", "classmates", "members",
}


def is_clean_proper_entity(name: str) -> bool:
    """Return True if ``name`` represents a specific named entity/proper noun rather than generic conversational noise."""
    if not name or not isinstance(name, str):
        return False
    n = name.strip()
    minimum = 2 if re.search(r"[\u3400-\u9fff\uac00-\ud7a3]", n) else 3
    if len(n) < minimum or len(n) > 50:
        return False
    low = n.lower()
    if low in _GENERIC_ENTITY_WORDS:
        return False
    if low.startswith(("the ", "a ", "an ")):
        return False
    # Parenthetical metadata or chapter citation residue from extraction
    if any(m in low for m in [": chapters [", "(mentioned)", "(referenced)", "(discussed)", "(memory)", "(dream child)", "(unnamed)", "(duplicate)", "(fifth)", "(anticipated)"]):
        return False
    words = [w for w in re.split(r"\s+", low) if w]
    if len(words) > 3:
        return False
    if any(w in GENERIC_CATEGORY_WORDS for w in words):
        return False
    return True


def text_values(value: Any) -> Iterable[str]:
    """Visit every nested string, including untrusted mapping keys."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                yield key
            yield from text_values(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from text_values(child)


def _name_pattern(name: str) -> str:
    escaped = re.escape(name)
    if re.search(r"[\u3400-\u9fff]", name):
        return rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])"
    if re.search(r"[\uac00-\ud7a3]", name):
        particles = r"(?:에게|한테|에서|으로|께|은|는|이|가|을|를|의|와|과|로|도|만|님)"
        return rf"(?<![\w]){escaped}(?:(?![\w])|(?={particles}+(?![\w])))"
    return rf"(?<![\w]){escaped}(?![\w])"


def check_payload(value: Any, profile: "SourceProfile", **kwargs: Any) -> "FirewallReport":
    return check_text("\n".join(text_values(value)), profile, **kwargs)



def _ngrams(tokens: Sequence[str], n: int) -> Set[Tuple[str, ...]]:
    return {tuple(tokens[i:i + n]) for i in range(0, max(0, len(tokens) - n + 1))}


def _lcs_len(a: Sequence[str], b: Sequence[str]) -> int:
    if not a or not b:
        return 0
    prev = [0] * (len(b) + 1)
    for x in a:
        cur = [0]
        for j, y in enumerate(b, start=1):
            cur.append(prev[j - 1] + 1 if x == y else max(prev[j], cur[j - 1]))
        prev = cur
    return prev[-1]


def _jaccard(a: Set[Any], b: Set[Any]) -> float:
    if not a and not b:
        return 0.0
    return len(a & b) / float(len(a | b) or 1)


def extract_candidate_terms(text: str, language: Optional[str] = None) -> Counter:
    """Capitalized multiword terms (EN) or Hangul noun-like tokens (KO) with counts."""
    lang = language or detect_language(text)
    counts: Counter = Counter()
    if lang == "ko":
        for m in _HANGUL_TERM.findall(text):
            if m not in _KO_COMMON and len(m) >= 2:
                counts[m] += 1
    else:
        for m in _CAP_TERM.findall(text):
            first = m.split()[0]
            if first in _EN_STOP or m in _EN_STOP:
                continue
            counts[m] += 1
    return counts


@dataclass
class SourceProfile:
    """Everything the firewall needs from the source manuscript. Excerpts are token
    windows and n-gram hashes, never full prose."""

    manuscript_id: str
    language: str
    entity_names: Set[str] = field(default_factory=set)
    distinctive_terms: Set[str] = field(default_factory=set)
    dialogue_lines: Set[str] = field(default_factory=set)
    phrase_windows: Set[Tuple[str, ...]] = field(default_factory=set)
    rare_ngrams: Set[Tuple[str, ...]] = field(default_factory=set)
    scene_summaries: List[str] = field(default_factory=list)
    beat_sequence: List[str] = field(default_factory=list)
    character_roles: Dict[str, str] = field(default_factory=dict)
    locations: Set[str] = field(default_factory=set)
    objects: Set[str] = field(default_factory=set)
    phrase_window: int = 8
    ngram_n: int = 4

    @classmethod
    def from_chapters(
        cls,
        chapters: Iterable[Any],
        *,
        manuscript_id: str = "",
        entity_names: Iterable[str] = (),
        scene_summaries: Iterable[str] = (),
        beat_sequence: Iterable[str] = (),
        character_roles: Optional[Dict[str, str]] = None,
        locations: Iterable[str] = (),
        objects: Iterable[str] = (),
        phrase_window: int = 8,
        ngram_n: int = 4,
        max_terms: int = 400,
    ) -> "SourceProfile":
        texts = [getattr(ch, "text", ch) if not isinstance(ch, str) else ch for ch in chapters]
        joined = "\n".join(str(t) for t in texts)
        lang = detect_language(joined)
        prof = cls(manuscript_id=manuscript_id, language=lang, phrase_window=phrase_window, ngram_n=ngram_n)
        prof.entity_names = {e.strip().lower() for e in entity_names if is_clean_proper_entity(e)}
        terms = extract_candidate_terms(joined, lang)
        # A distinctive term recurs (>= 3) and is not a common word. For Latin
        # text a single capitalized word must occur at least once *inside* a
        # sentence (not sentence-initial) and never as an ordinary lowercase
        # word; otherwise it is just a capitalized common word ("Same", "Quiet").
        lower_tokens = set(tokenize(joined, lang)) if lang != "ko" else set()
        prof.distinctive_terms = set()
        for t, c in terms.most_common(max_terms):
            if c < 3:
                continue
            if lang != "ko" and " " not in t:
                if re.search(rf"(?<![A-Za-z]){re.escape(t.lower())}(?![A-Za-z])", joined):
                    continue
                mid_sentence = re.search(rf"[a-z,;:]\s+{re.escape(t)}\b", joined)
                if not mid_sentence:
                    continue
            prof.distinctive_terms.add(t.lower())
        prof.distinctive_terms |= prof.entity_names
        for t in texts:
            for q in _QUOTED.findall(str(t)):
                # Only lines long enough to be distinctive; "Not yet." is not evidence of copying.
                if len(tokenize(q, lang)) >= 5:
                    prof.dialogue_lines.add(normalize_for_index(q).lower())
            toks = tokenize(str(t), lang)
            prof.phrase_windows |= _ngrams(toks, phrase_window)
        all_tokens = tokenize(joined, lang)
        grams = Counter(tuple(all_tokens[i:i + ngram_n]) for i in range(max(0, len(all_tokens) - ngram_n + 1)))
        # Rare = appears at most twice in the source and contains a non-stopword;
        # these are the source's idiosyncratic phrasings.
        stop = {w.lower() for w in _EN_STOP} | {"a", "an", "the", "of", "to", "in", "and", "was", "he", "she", "it", "his", "her", "had", "that", "with", "on", "at", "for", "as", "i", "you", "my", "me", "but", "not", "be", "is", "were", "they", "them", "their", "from", "this", "we"}
        prof.rare_ngrams = {g for g, c in grams.items() if c <= 2 and sum(1 for w in g if w not in stop) >= 3}
        prof.scene_summaries = [normalize_for_index(s).lower() for s in scene_summaries if s]
        prof.beat_sequence = [str(b) for b in beat_sequence]
        prof.character_roles = {k.strip().lower(): v for k, v in (character_roles or {}).items()}
        prof.locations = {x.strip().lower() for x in locations if x and x.strip()}
        prof.objects = {x.strip().lower() for x in objects if x and x.strip()}
        return prof


@dataclass
class Finding:
    check: str
    severity: str  # "critical" | "high" | "medium" | "low"
    detail: str
    span: Optional[Tuple[int, int]] = None
    matched: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"check": self.check, "severity": self.severity, "detail": self.detail, "span": list(self.span) if self.span else None, "matched": self.matched}


@dataclass
class FirewallReport:
    passed: bool
    findings: List[Finding]
    scores: Dict[str, float]
    version: str = FIREWALL_VERSION

    def as_dict(self) -> Dict[str, Any]:
        return {"passed": self.passed, "findings": [f.as_dict() for f in self.findings], "scores": self.scores, "version": self.version}

    @property
    def critical(self) -> List[Finding]:
        return [f for f in self.findings if f.severity in ("critical", "high")]


def _find_span(text: str, needle: str) -> Optional[Tuple[int, int]]:
    if not needle:
        return None
    m = re.search(re.escape(needle), text, flags=re.I)
    return (m.start(), m.end()) if m else None


def _find_token_window_span(text: str, window: Tuple[str, ...]) -> Optional[Tuple[int, int]]:
    pattern = r"\W+".join(re.escape(w) for w in window)
    m = re.search(pattern, text, flags=re.I)
    return (m.start(), m.end()) if m else None


def check_text(
    text: str,
    profile: SourceProfile,
    *,
    allowed_names: Iterable[str] = (),
    beat_sequence: Iterable[str] = (),
    scene_summaries: Iterable[str] = (),
    character_roles: Optional[Dict[str, str]] = None,
    locations: Iterable[str] = (),
    objects: Iterable[str] = (),
    max_phrase_hits: int = 0,
    max_rare_ngram_ratio: float = 0.02,
    max_beat_lcs_ratio: float = 0.75,
    max_summary_similarity: float = 0.6,
) -> FirewallReport:
    """Run every firewall check against ``text`` (a draft, outline or Bible dump)."""
    findings: List[Finding] = []
    lang = detect_language(text) if text.strip() else profile.language
    allowed = {a.strip().lower() for a in allowed_names if a and a.strip()}
    lowered = text.lower()

    # 1. named entities
    for name in sorted(profile.entity_names):
        if name in allowed or not is_clean_proper_entity(name):
            continue
        if re.search(_name_pattern(name), lowered):
            findings.append(Finding("entity_overlap", "critical", f"Source entity name '{name}' appears in the text", _find_span(text, name), name))

    # 2. distinctive terms
    term_hits = 0
    for term in sorted(profile.distinctive_terms - profile.entity_names):
        if term in allowed or len(term) < 4:
            continue
        if re.search(rf"(?<![\w]){re.escape(term)}(?![\w])", lowered):
            term_hits += 1
            if term_hits <= 25:
                findings.append(Finding("distinctive_term_overlap", "high", f"Source-specific term '{term}' appears in the text", _find_span(text, term), term))

    # 3. long phrase overlap
    toks = tokenize(text, lang)
    windows = _ngrams(toks, profile.phrase_window)
    shared = windows & profile.phrase_windows
    for w in sorted(shared)[:25]:
        findings.append(Finding("long_phrase_overlap", "critical", f"{profile.phrase_window}-token phrase copied from the source", _find_token_window_span(text, w), " ".join(w)))

    # 4. dialogue overlap
    for q in _QUOTED.findall(text):
        nq = normalize_for_index(q).lower()
        if nq in profile.dialogue_lines:
            findings.append(Finding("dialogue_overlap", "critical", "A dialogue line is identical to a source line", _find_span(text, q), q[:80]))

    # 5. rare n-grams
    grams = _ngrams(toks, profile.ngram_n)
    rare_hits = grams & profile.rare_ngrams
    rare_ratio = len(rare_hits) / float(len(grams) or 1)
    if rare_ratio > max_rare_ngram_ratio and len(rare_hits) >= 3:
        sample = sorted(rare_hits)[:5]
        findings.append(Finding("rare_ngram_overlap", "high", f"{len(rare_hits)} rare source {profile.ngram_n}-grams reused ({rare_ratio:.1%} of the text's n-grams)", _find_token_window_span(text, sample[0]) if sample else None, "; ".join(" ".join(g) for g in sample)))

    # 6. scene-summary similarity
    best_summary = 0.0
    for s in scene_summaries:
        st = set(tokenize(normalize_for_index(s).lower(), lang))
        for src in profile.scene_summaries:
            sim = _jaccard(st, set(tokenize(src, profile.language)))
            best_summary = max(best_summary, sim)
            if sim >= max_summary_similarity:
                findings.append(Finding("scene_summary_similarity", "high", f"Scene summary resembles a source scene (jaccard {sim:.2f})", None, s[:100]))
                break

    # 7. ordered beat sequence
    beats = [str(b) for b in beat_sequence]
    lcs_ratio = 0.0
    if beats and profile.beat_sequence and len(beats) >= 6:
        lcs_ratio = _lcs_len(beats, profile.beat_sequence) / float(len(beats))
        if lcs_ratio > max_beat_lcs_ratio:
            findings.append(Finding("beat_sequence_similarity", "high", f"Ordered beat sequence follows the source too closely (LCS ratio {lcs_ratio:.2f})", None, " > ".join(beats[:10])))

    # 8. character-role mapping
    role_overlap = 0.0
    if character_roles and profile.character_roles:
        src_roles = Counter(profile.character_roles.values())
        new_roles = Counter(character_roles.values())
        common = sum((src_roles & new_roles).values())
        role_overlap = common / float(sum(new_roles.values()) or 1)
        shared_names = {n for n in character_roles if n.strip().lower() in profile.character_roles}
        for n in sorted(shared_names):
            findings.append(Finding("character_role_mapping", "critical", f"Character '{n}' is a source character name", _find_span(text, n), n))
        if role_overlap >= 0.9 and len(new_roles) >= 5:
            findings.append(Finding("character_role_mapping", "medium", f"Role arrangement duplicates the source ({role_overlap:.0%} identical role slots)", None, ""))

    # 9. locations / objects
    for label, new, src in (("location", locations, profile.locations), ("object", objects, profile.objects)):
        for x in new:
            if x and x.strip().lower() in src and x.strip().lower() not in allowed:
                findings.append(Finding(f"{label}_similarity", "high", f"Source {label} '{x}' reused", _find_span(text, x), x))

    # 10. accidental quotation: consecutive shared windows form a verbatim run
    quote_runs = 0
    if shared:
        ordered = [i for i in range(len(toks) - profile.phrase_window + 1) if tuple(toks[i:i + profile.phrase_window]) in profile.phrase_windows]
        run = 1
        for a, b in zip(ordered, ordered[1:]):
            run = run + 1 if b == a + 1 else 1
            if run == 4:
                quote_runs += 1
    if quote_runs:
        findings.append(Finding("accidental_quotation", "critical", f"{quote_runs} verbatim run(s) of >= {profile.phrase_window + 3} tokens copied from the source", None, ""))

    scores = {
        "entity_hits": float(sum(1 for f in findings if f.check == "entity_overlap")),
        "distinctive_term_hits": float(term_hits),
        "phrase_hits": float(len(shared)),
        "dialogue_hits": float(sum(1 for f in findings if f.check == "dialogue_overlap")),
        "rare_ngram_ratio": round(rare_ratio, 4),
        "summary_similarity": round(best_summary, 4),
        "beat_lcs_ratio": round(lcs_ratio, 4),
        "role_overlap": round(role_overlap, 4),
    }
    passed = not any(f.severity in ("critical", "high") for f in findings) and len(shared) <= max_phrase_hits
    return FirewallReport(passed=passed, findings=findings, scores=scores)


def check_bible_cards(cards: Iterable[Dict[str, Any]], profile: SourceProfile) -> FirewallReport:
    """Check original-Bible card contents (names, aliases, descriptions) for source leakage."""
    names: List[str] = []
    roles: Dict[str, str] = {}
    locations: List[str] = []
    objects: List[str] = []
    dump_parts: List[str] = []
    for card in cards:
        content = card.get("content") if isinstance(card.get("content"), dict) else {}
        ctype = str(card.get("card_type") or "")
        name = str(content.get("name") or card.get("title") or "")
        if ctype == "Character Card":
            names.append(name)
            roles[name] = str(content.get("role_type") or "")
            names += [str(a) for a in (content.get("aliases") or []) if a]
        elif ctype == "Scene Card":
            locations.append(name)
        elif ctype == "Item Card":
            objects.append(name)
        dump_parts.append(name)
        dump_parts.extend(text_values(content))
    text = "\n".join(dump_parts)
    report = check_text(text, profile, character_roles=roles, locations=locations, objects=objects, max_phrase_hits=0)
    for n in names:
        if n and n.strip().lower() in profile.entity_names:
            if not any(f.matched == n for f in report.findings):
                report.findings.append(Finding("entity_overlap", "critical", f"Bible entity '{n}' is a source entity", None, n))
    report.passed = report.passed and not report.critical
    return report


__all__ = ["FIREWALL_VERSION", "Finding", "FirewallReport", "SourceProfile", "check_bible_cards", "check_payload", "check_text", "extract_candidate_terms", "text_values"]
