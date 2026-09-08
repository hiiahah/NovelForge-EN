"""Deterministic prose measurements.

Everything here is pure, language-aware and cheap: it is the foundation of the
Narrative Fingerprint (source side) and of Style Validation (draft side), so
the same function must produce the same numbers for both.

Korean text is measured in its original form: sentence boundaries follow
Korean terminal punctuation and sentence-ending verb forms; speech level is
classified from the ending morpheme (formal ``-습니다``, polite ``-요``, plain
``-다``, intimate/casual). Nothing is translated.
"""

from __future__ import annotations

import hashlib
import math
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

METRICS_VERSION = "textmetrics-1"

_HANGUL = re.compile(r"[\uac00-\ud7a3\u1100-\u11ff\u3130-\u318f]")
_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_LATIN_WORD = re.compile(r"[A-Za-z\u00c0-\u024f']+")
_SENT_SPLIT = re.compile(r"(?<=[.!?…。！？])\s+|(?<=[.!?…。！？])(?=[\"'“”‘’)\]])\s*|\n+")
_DIALOGUE_LINE = re.compile(r"^\s*[\"“”「『‘'].+", re.M)
_QUOTED = re.compile(r"[\"“”「『]([^\"“”」』]{1,400})[\"“”」』]")
_INNER_THOUGHT = re.compile(r"(?:[‘'『「][^’'』」]{2,200}[’'』」])|(?:\*[^*]{2,200}\*)|(?:\([^)]{2,120}\))")
_STATUS_WINDOW = re.compile(r"^\s*[\[【\(<][^\]】\)>]{2,80}[\]】\)>]\s*$", re.M)
_ONOMATOPOEIA_KO = re.compile(r"(?:쿵|쾅|철컥|덜컥|스윽|후욱|휘익|퍽|탁|툭|끼익|우웅|쿨럭|찰칵|파삭|바스락|두근|덜덜|부들|흠칫|피식|킥킥|하하|헉|흐읍|쩍|팟|번쩍)")
_KO_SENT_END = re.compile(r"(?:습니다|습니까|입니다|십니다|세요|에요|예요|네요|군요|죠|요|다|까|냐|니|지|어|아|야|게|래|자|마|구나|는데|던데|잖아)\s*[.!?…]?$")
_FORMAL = re.compile(r"(?:습니다|습니까|입니다|십니다|십시오|소이다|나이다|옵니다)\s*[.!?…]*$")
_POLITE = re.compile(r"(?:세요|에요|예요|네요|군요|죠|요|셨어요|였어요|었어요|았어요|고요|는데요|까요)\s*[.!?…]*$")
_PLAIN = re.compile(r"(?:다|였다|었다|았다|한다|이다|는다|았었다|겠다|더라|구나|로다|노라)\s*[.!?…]*$")
_INTIMATE = re.compile(r"(?:어|아|야|지|니|냐|자|래|게|마|잖아|는데|던데|거든|을까|ㄹ까|까)\s*[.!?…]*$")
_HONORIFIC_ADDRESS = re.compile(r"(?:씨|님|선배|후배|형|누나|오빠|언니|선생님|사장님|대표님|팀장님|과장님|부장님|전하|폐하|각하|나리|공자|소저|어르신)\b")
_RECAP_KO = re.compile(r"^(?:지난|이전|앞서|전편|지난 이야기|앞 이야기)")
_TRANSITION_EN = re.compile(r"^(?:meanwhile|later|afterward|afterwards|the next (?:morning|day|night)|hours later|that night|by the time|when|after|before|then|suddenly|at the same time|elsewhere)\b", re.I)
_TRANSITION_KO = re.compile(r"^(?:한편|그리고|그러나|하지만|그때|그 순간|잠시 후|다음 날|그날 밤|얼마 후|며칠 후|한참 후|이윽고|그러자|결국|문득)")
_METAPHOR_EN = re.compile(r"\b(?:like a|like the|as if|as though|as a|resembled|reminded (?:him|her|them|me) of)\b", re.I)
_METAPHOR_KO = re.compile(r"(?:처럼|같이|마냥|듯이|듯한|같은|마치|흡사)")
_RHETORICAL_Q = re.compile(r"\?\s*$")
_FRAGMENT_EN = re.compile(r"^(?:[A-Z][a-z']*|[A-Z][a-z']*\s[a-z']+|No\.|Yes\.|Silence\.|Nothing\.|[A-Z][a-z]+\s[a-z]+\s[a-z]+\.)$")
_ELLIPSIS = re.compile(r"(?:\.\.\.|…)")
_DASH = re.compile(r"(?:—|–|--)")
_FIRST_PERSON_EN = re.compile(r"\b(?:I|I'm|I'd|I've|I'll|me|my|mine|myself)\b")
_THIRD_PERSON_EN = re.compile(r"\b(?:he|she|his|her|him|hers|himself|herself|they|them|their)\b", re.I)
_FIRST_PERSON_KO = re.compile(r"(?:^|[\s,\"“])(?:나는|내가|나의|내|나도|나를|나에게|난|날|저는|제가|저의|제)(?=[\s,.!?]|$)")
_THIRD_PERSON_KO = re.compile(r"(?:그는|그녀는|그가|그녀가|그의|그녀의|그를|그녀를|그들은|그들이)")
_PAST_EN = re.compile(r"\b(?:was|were|had|did|went|came|said|looked|turned|took|thought|knew|felt|saw|made)\b", re.I)
_PRESENT_EN = re.compile(r"\b(?:is|are|am|has|does|goes|comes|says|looks|turns|takes|thinks|knows|feels|sees|makes)\b", re.I)
_SENSORY_EN = re.compile(r"\b(?:smell|scent|odou?r|taste|bitter|sweet|salt|sour|cold|warm|hot|damp|rough|smooth|echo|hum|buzz|whisper|glow|glimmer|shadow|bright|dim|flicker|ache|sting|throb|silk|iron|smoke|dust|rain|wind)\w*\b", re.I)
_SENSORY_KO = re.compile(r"(?:냄새|향기|소리|빛|그림자|차가|뜨거|따뜻|축축|거칠|매끄|쓴맛|달콤|짭짤|시큼|울리|웅웅|반짝|어둑|희미|욱신|얼얼|연기|먼지|비|바람|어둠)")
_EXPLICIT_EMOTION_EN = re.compile(r"\b(?:felt|feel|feeling|angry|anger|sad|sadness|happy|happiness|afraid|fear|terrified|joy|grief|ashamed|jealous|lonely|furious|anxious|nervous|relief|relieved|love|hate|hated|loved)\b", re.I)
_EXPLICIT_EMOTION_KO = re.compile(r"(?:슬펐|슬프|기뻤|기쁘|화가|분노|두려|무서|불안|초조|외로|질투|안도|사랑|미워|증오|서러|억울|부끄|창피|설레|행복)")
_SAID_TAG_EN = re.compile(r"\b(?:said|asked|replied|answered|muttered|whispered|shouted|snapped|murmured|called|added|continued)\b", re.I)
_SAID_TAG_KO = re.compile(r"(?:말했다|물었다|대답했다|중얼거렸다|속삭였다|외쳤다|소리쳤다|덧붙였다|말을 이었다|되물었다|답했다|웅얼거렸다)")

BEAT_FUNCTIONS: Tuple[str, ...] = (
    "cold_open", "quiet_scene_opening", "action_opening", "dialogue_heavy_scene", "banter", "romantic_tension",
    "emotional_restraint", "confession", "internal_monologue", "comedic_reversal", "threat_escalation",
    "fight_choreography", "exposition_delivery", "mystery_clue", "false_reassurance", "reveal", "aftermath",
    "transition", "chapter_cliffhanger", "soft_chapter_ending", "relationship_shift", "power_reveal",
    "status_screen_presentation",
)


def sha256_text(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def stable_id(*parts: Any, length: int = 16) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:length]


def detect_language(text: str) -> str:
    """Return 'ko', 'zh', 'en' or 'und' from character classes (no model call)."""
    sample = (text or "")[:20000]
    if not sample.strip():
        return "und"
    hangul = len(_HANGUL.findall(sample))
    cjk = len(_CJK.findall(sample))
    latin = len(_LATIN_WORD.findall(sample))
    total = hangul + cjk + latin
    if total == 0:
        return "und"
    if hangul / total > 0.25:
        return "ko"
    if cjk / total > 0.25:
        return "zh"
    if latin / total > 0.5:
        return "en"
    return "und"


def normalize_for_index(text: str) -> str:
    """Indexing normalization only. Never applied to stored source text."""
    t = unicodedata.normalize("NFKC", text or "")
    t = t.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    t = re.sub(r"[ \t\u3000]+", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


def split_paragraphs(text: str) -> List[str]:
    parts = re.split(r"\n\s*\n|\n", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def split_sentences(text: str, language: Optional[str] = None) -> List[str]:
    lang = language or detect_language(text)
    out: List[str] = []
    for para in split_paragraphs(text):
        if lang in ("ko", "zh"):
            pieces = re.split(r"(?<=[.!?…。！？])\s*", para)
        else:
            pieces = _SENT_SPLIT.split(para)
        for p in pieces:
            p = (p or "").strip()
            if p:
                out.append(p)
    return out


def tokenize(text: str, language: Optional[str] = None) -> List[str]:
    lang = language or detect_language(text)
    if lang in ("ko", "zh"):
        # Whitespace-delimited eojeol for Korean; per-character for Chinese.
        if lang == "ko":
            return [t for t in re.split(r"\s+", (text or "").strip()) if t]
        return [c for c in text if _CJK.match(c)]
    return [w.lower() for w in _LATIN_WORD.findall(text or "")]


def count_units(text: str, language: Optional[str] = None) -> int:
    return len(tokenize(text, language))


def _dist(values: Sequence[float]) -> Dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "stdev": 0.0, "p10": 0.0, "p90": 0.0, "max": 0.0, "n": 0}
    vals = sorted(float(v) for v in values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        k = (n - 1) * p
        f = math.floor(k)
        c = min(f + 1, n - 1)
        return vals[f] + (vals[c] - vals[f]) * (k - f)

    return {
        "mean": round(statistics.fmean(vals), 3),
        "median": round(statistics.median(vals), 3),
        "stdev": round(statistics.pstdev(vals), 3) if n > 1 else 0.0,
        "p10": round(pct(0.1), 3),
        "p90": round(pct(0.9), 3),
        "max": round(vals[-1], 3),
        "n": n,
    }


def _is_dialogue_paragraph(p: str) -> bool:
    return bool(_DIALOGUE_LINE.match(p)) or bool(_QUOTED.search(p) and len(_QUOTED.findall(p)) >= 1 and sum(len(m) for m in _QUOTED.findall(p)) > 0.4 * len(p))


def speech_level(sentence: str) -> str:
    s = sentence.strip().rstrip('"”』」\'’)')
    if not _HANGUL.search(s):
        return "n/a"
    if _FORMAL.search(s):
        return "formal"
    if _POLITE.search(s):
        return "polite"
    if _PLAIN.search(s):
        return "plain"
    if _INTIMATE.search(s):
        return "intimate"
    return "other"


def classify_opening(text: str, language: Optional[str] = None) -> str:
    paras = split_paragraphs(text)
    if not paras:
        return "empty"
    first = paras[0]
    lang = language or detect_language(text)
    if _is_dialogue_paragraph(first):
        return "dialogue_open"
    if _STATUS_WINDOW.match(first):
        return "status_screen_open"
    if _TRANSITION_EN.match(first) or _TRANSITION_KO.match(first) or (lang == "ko" and _RECAP_KO.match(first)):
        return "transition_open"
    if _INNER_THOUGHT.match(first) or (lang == "en" and _FIRST_PERSON_EN.search(first) and len(first) < 160):
        return "thought_open"
    tokens = count_units(first, lang)
    if tokens <= 12:
        return "fragment_open"
    if _SENSORY_EN.search(first) or _SENSORY_KO.search(first):
        return "sensory_open"
    return "narrative_open"


def classify_ending(text: str, language: Optional[str] = None) -> str:
    paras = split_paragraphs(text)
    if not paras:
        return "empty"
    last = paras[-1]
    lang = language or detect_language(text)
    if _RHETORICAL_Q.search(last):
        return "question_hook"
    if _is_dialogue_paragraph(last):
        return "dialogue_hook"
    if _ELLIPSIS.search(last[-6:]) or _DASH.search(last[-6:]):
        return "suspended_hook"
    tokens = count_units(last, lang)
    if tokens <= 8:
        return "punch_line"
    if _EXPLICIT_EMOTION_EN.search(last) or _EXPLICIT_EMOTION_KO.search(last):
        return "emotional_close"
    if _SENSORY_EN.search(last) or _SENSORY_KO.search(last):
        return "image_close"
    return "quiet_close"


@dataclass
class ProseMetrics:
    language: str
    char_count: int
    unit_count: int
    sentence_count: int
    paragraph_count: int
    sentence_len: Dict[str, float]
    paragraph_len: Dict[str, float]
    dialogue_ratio: float
    narration_ratio: float
    internal_thought_ratio: float
    exposition_ratio: float
    action_ratio: float
    question_freq: float
    exclamation_freq: float
    ellipsis_freq: float
    dash_freq: float
    fragment_freq: float
    rhetorical_question_freq: float
    metaphor_density: float
    sensory_density: float
    explicit_emotion_density: float
    dialogue_tag_density: float
    attribution_omission_ratio: float
    first_person_ratio: float
    third_person_ratio: float
    past_tense_ratio: float
    transition_marker_freq: float
    short_paragraph_ratio: float
    status_window_count: int
    onomatopoeia_density: float
    honorific_density: float
    speech_levels: Dict[str, float]
    opening_type: str
    ending_type: str
    extra: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        d = dict(self.__dict__)
        d["metrics_version"] = METRICS_VERSION
        return d


def _ratio(a: float, b: float) -> float:
    return round(a / b, 4) if b else 0.0


def measure(text: str, language: Optional[str] = None) -> ProseMetrics:
    """Measure one text (chapter, scene, excerpt). Deterministic."""
    text = text or ""
    lang = language or detect_language(text)
    paras = split_paragraphs(text)
    sents = split_sentences(text, lang)
    units_total = count_units(text, lang) or 1
    sent_lens = [count_units(s, lang) for s in sents]
    para_lens = [count_units(p, lang) for p in paras]

    dialogue_units = sum(count_units(p, lang) for p in paras if _is_dialogue_paragraph(p))
    thought_units = sum(count_units(m, lang) for m in _INNER_THOUGHT.findall(text))
    if lang == "en":
        thought_units += sum(count_units(s, lang) for s in sents if _FIRST_PERSON_EN.search(s) and not _QUOTED.search(s) and re.search(r"\b(?:thought|wondered|realized|knew|felt|remembered|supposed|figured)\b", s, re.I))
    narration_units = max(0, units_total - dialogue_units)
    action_units = sum(count_units(s, lang) for s in sents if not _QUOTED.search(s) and re.search(r"\b(?:ran|grabbed|struck|swung|dodged|slammed|kicked|pulled|pushed|threw|lunged|drew|fired|leapt|jumped|rolled|blocked|parried|dashed)\b|(?:달렸|휘둘|막았|피했|쳤다|뛰었|던졌|잡았|찔렀|베었|덤볐|밀쳤|당겼|굴렀)", s, re.I))
    exposition_units = sum(count_units(s, lang) for s in sents if not _QUOTED.search(s) and re.search(r"\b(?:because|since|was known|had been|history|centuries|years ago|the system|the rule|the law|it was said|according to)\b|(?:왜냐하면|때문에|알려져|년 전|역사|규칙|법칙|시스템|이라고 한다|전해진다)", s, re.I))

    questions = sum(1 for s in sents if s.rstrip().endswith(("?", "？")))
    exclamations = sum(1 for s in sents if s.rstrip().endswith(("!", "！")))
    rhetorical = sum(1 for s in sents if s.rstrip().endswith(("?", "？")) and not _QUOTED.search(s))
    fragments = sum(1 for s, n in zip(sents, sent_lens) if n <= 3 and not _QUOTED.search(s))
    metaphors = len(_METAPHOR_EN.findall(text)) + len(_METAPHOR_KO.findall(text))
    sensory = len(_SENSORY_EN.findall(text)) + len(_SENSORY_KO.findall(text))
    emotions = len(_EXPLICIT_EMOTION_EN.findall(text)) + len(_EXPLICIT_EMOTION_KO.findall(text))
    tags = len(_SAID_TAG_EN.findall(text)) + len(_SAID_TAG_KO.findall(text))
    quotes = len(_QUOTED.findall(text))
    transitions = sum(1 for p in paras if _TRANSITION_EN.match(p) or _TRANSITION_KO.match(p))
    first_person = len(_FIRST_PERSON_EN.findall(text)) if lang == "en" else len(_FIRST_PERSON_KO.findall(text))
    third_person = len(_THIRD_PERSON_EN.findall(text)) if lang == "en" else len(_THIRD_PERSON_KO.findall(text))
    pronouns = (first_person + third_person) or 1
    past = len(_PAST_EN.findall(text))
    present = len(_PRESENT_EN.findall(text))

    levels: Dict[str, int] = {}
    if lang == "ko":
        for s in sents:
            lv = speech_level(s)
            if lv != "n/a":
                levels[lv] = levels.get(lv, 0) + 1
    level_total = sum(levels.values()) or 1
    per_k = 1000.0 / units_total

    return ProseMetrics(
        language=lang,
        char_count=len(text),
        unit_count=units_total if text.strip() else 0,
        sentence_count=len(sents),
        paragraph_count=len(paras),
        sentence_len=_dist(sent_lens),
        paragraph_len=_dist(para_lens),
        dialogue_ratio=_ratio(dialogue_units, units_total),
        narration_ratio=_ratio(narration_units, units_total),
        internal_thought_ratio=_ratio(min(thought_units, units_total), units_total),
        exposition_ratio=_ratio(exposition_units, units_total),
        action_ratio=_ratio(action_units, units_total),
        question_freq=_ratio(questions, len(sents)),
        exclamation_freq=_ratio(exclamations, len(sents)),
        ellipsis_freq=round(len(_ELLIPSIS.findall(text)) * per_k, 3),
        dash_freq=round(len(_DASH.findall(text)) * per_k, 3),
        fragment_freq=_ratio(fragments, len(sents)),
        rhetorical_question_freq=_ratio(rhetorical, len(sents)),
        metaphor_density=round(metaphors * per_k, 3),
        sensory_density=round(sensory * per_k, 3),
        explicit_emotion_density=round(emotions * per_k, 3),
        dialogue_tag_density=round(tags * per_k, 3),
        attribution_omission_ratio=_ratio(max(0, quotes - tags), quotes),
        first_person_ratio=_ratio(first_person, pronouns),
        third_person_ratio=_ratio(third_person, pronouns),
        past_tense_ratio=_ratio(past, (past + present) or 1) if lang == "en" else 0.0,
        transition_marker_freq=_ratio(transitions, len(paras)),
        short_paragraph_ratio=_ratio(sum(1 for n in para_lens if n <= 15), len(paras)),
        status_window_count=len(_STATUS_WINDOW.findall(text)),
        onomatopoeia_density=round(len(_ONOMATOPOEIA_KO.findall(text)) * per_k, 3) if lang == "ko" else 0.0,
        honorific_density=round(len(_HONORIFIC_ADDRESS.findall(text)) * per_k, 3) if lang == "ko" else 0.0,
        speech_levels={k: round(v / level_total, 4) for k, v in sorted(levels.items())},
        opening_type=classify_opening(text, lang),
        ending_type=classify_ending(text, lang),
    )


def infer_pov(metrics: ProseMetrics) -> str:
    if metrics.first_person_ratio >= 0.55:
        return "first_person"
    if metrics.third_person_ratio >= 0.55:
        return "third_person"
    return "mixed_or_unknown"


def aggregate(metric_dicts: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate per-chapter metric dicts into target ranges (p10..p90 of chapter means)."""
    rows = [m for m in metric_dicts if isinstance(m, dict)]
    if not rows:
        return {"chapters": 0}
    scalar_keys = [
        "dialogue_ratio", "narration_ratio", "internal_thought_ratio", "exposition_ratio", "action_ratio",
        "question_freq", "exclamation_freq", "ellipsis_freq", "dash_freq", "fragment_freq", "rhetorical_question_freq",
        "metaphor_density", "sensory_density", "explicit_emotion_density", "dialogue_tag_density",
        "attribution_omission_ratio", "first_person_ratio", "third_person_ratio", "past_tense_ratio",
        "transition_marker_freq", "short_paragraph_ratio", "onomatopoeia_density", "honorific_density",
    ]
    out: Dict[str, Any] = {"chapters": len(rows)}
    for key in scalar_keys:
        out[key] = _dist([float(r.get(key) or 0.0) for r in rows])
    out["sentence_len_mean"] = _dist([float((r.get("sentence_len") or {}).get("mean") or 0.0) for r in rows])
    out["paragraph_len_mean"] = _dist([float((r.get("paragraph_len") or {}).get("mean") or 0.0) for r in rows])
    out["unit_count"] = _dist([float(r.get("unit_count") or 0.0) for r in rows])
    for cat in ("opening_type", "ending_type"):
        counts: Dict[str, int] = {}
        for r in rows:
            v = str(r.get(cat) or "unknown")
            counts[v] = counts.get(v, 0) + 1
        out[cat] = {k: round(v / len(rows), 4) for k, v in sorted(counts.items(), key=lambda kv: -kv[1])}
    levels: Dict[str, List[float]] = {}
    for r in rows:
        for k, v in (r.get("speech_levels") or {}).items():
            levels.setdefault(k, []).append(float(v))
    out["speech_levels"] = {k: _dist(v) for k, v in levels.items()}
    langs: Dict[str, int] = {}
    for r in rows:
        langs[str(r.get("language") or "und")] = langs.get(str(r.get("language") or "und"), 0) + 1
    out["language"] = max(langs.items(), key=lambda kv: kv[1])[0]
    return out


def in_range(value: float, dist: Dict[str, float], slack: float = 0.0) -> bool:
    """True when value falls within [p10 - slack, p90 + slack] of the source distribution."""
    if not dist or not dist.get("n"):
        return True
    lo = float(dist.get("p10", 0.0)) - slack
    hi = float(dist.get("p90", 0.0)) + slack
    return lo <= value <= hi
