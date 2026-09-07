"""Claim extraction from a chapter draft.

Two channels:
1. A structured channel: the drafting model may return a JSON block of claims
   (``ChapterClaims`` schema) *outside* the visible prose. It is optional.
2. A deterministic channel: ``extract_claims`` always runs over the prose and
   produces entity mentions, possession/location/injury/knowledge statements
   with exact evidence spans. Deterministic claims are what validation and
   synchronization trust; model claims are cross-checked against them and are
   never applied unless an evidence span in the prose supports them.

Every claim carries its evidence span and a support level:
explicit (a sentence states it), strongly_entailed (a pattern implies it
unambiguously), weakly_inferred (needs interpretation) or unsupported.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.services.forge.textmetrics import detect_language, split_sentences

CLAIMS_VERSION = "claims-1"
CLAIMS_BLOCK_RX = re.compile(r"<claims>\s*(\{.*?\})\s*</claims>", re.S)
_CAP_NAME = re.compile(r"\b[A-Z][a-z]+(?:\s[A-Z][a-z]+)?\b")
_HANGUL_NAME = re.compile(r"[\uac00-\ud7a3]{2,4}(?=(?:은|는|이|가|을|를|의|에게|과|와|도|만|께서|이가)?\s)")
_COMMON_CAP = {
    "The", "And", "But", "She", "He", "They", "It", "That", "This", "There", "Then", "When", "What", "Why", "How", "Who", "Where", "You", "His", "Her", "Their", "For", "With", "From", "Not", "Yes", "No", "If", "In", "On", "At", "As", "Of", "To", "So", "Now", "Here", "Once", "Maybe", "Nothing", "Something", "Someone", "Everyone", "Nobody", "All", "One", "Only", "Again", "Another", "Every", "Some", "Any", "Well", "Right", "Left", "Good", "Fine", "Okay", "Please", "Thank", "Sorry", "Wait", "Stop", "Look", "Listen", "Come", "Let", "Go", "Do", "Did", "Was", "Were", "Had", "Have", "Has", "Would", "Could", "Should", "Will", "Can", "May", "Might", "Must", "Am", "Are", "Is", "Be", "Been", "Its", "My", "Me", "We", "Us", "Morning", "Night", "Day", "Evening", "Today", "Tomorrow", "Yesterday", "Sir", "Madam", "Miss", "Lord", "Lady", "Before", "After", "Because", "While", "Still", "Even", "Just", "Perhaps", "Chapter", "Inside", "Outside", "Behind", "Above", "Below", "Later", "Meanwhile", "Somewhere", "Nowhere", "Anyone", "Whatever", "Whoever", "Instead", "Except", "Until", "Unless", "Though", "Although", "Yet", "Also", "Almost", "Already", "Always", "Never", "Often", "Sometimes", "Soon", "Suddenly", "Finally", "Actually", "Really", "Very", "Too", "Quite", "Rather", "Enough", "Both", "Either", "Neither", "Each", "Few", "Many", "Most", "Much", "Several", "Such", "Whole", "Half", "Two", "Three", "Four", "Five", "Ten", "Hundred", "Thousand", "First", "Second", "Third", "Last", "Next", "Other", "Same", "Own", "Old", "New", "Long", "Short", "High", "Low", "Big", "Small", "Little", "Great", "Cold", "Hot", "Dark", "Light", "Silence", "Somehow", "Everything", "Anything",
    "Like", "Or", "Nor", "Try", "Trying", "Tried", "Tries", "Student", "Students", "Teacher", "Teachers", "Class", "Classes", "Black", "White", "Red", "Blue", "Green", "Yellow", "Gold", "Silver", "Death", "Flag", "Flags", "Game", "Master", "Admin", "System", "Status", "Window", "Interface", "Scene", "Room", "Dorm", "Dormitory", "Academy", "Office", "Infirmary", "Library", "Don", "Won", "Cannot", "Couldn", "Wouldn", "Shouldn", "Didn", "Isn", "Aren", "Wasn", "Weren", "Haven", "Hasn", "Hadn", "Looked", "Looking", "Looks", "Seemed", "Seeming", "Seems", "Think", "Thinks", "Thought", "Thinking", "Ask", "Asks", "Asked", "Asking", "Say", "Says", "Said", "Saying", "Tell", "Tells", "Told", "Telling", "Feel", "Feels", "Felt", "Feeling", "Turn", "Turns", "Turned", "Turning", "Walk", "Walks", "Walked", "Walking", "Step", "Steps", "Stepped", "Stepping", "Stand", "Stands", "Stood", "Standing", "Sit", "Sits", "Sat", "Sitting", "Take", "Takes", "Took", "Taking", "Give", "Gives", "Gave", "Giving", "Make", "Makes", "Made", "Making", "Came", "Coming", "Went", "Going", "Know", "Knows", "Knew", "Knowing", "See", "Sees", "Saw", "Seeing", "Hear", "Hears", "Heard", "Hearing", "Find", "Finds", "Found", "Finding", "Leave", "Leaves", "Leaving",
    "Eyes", "Eye", "Oh", "Ah", "Ha", "Hmm", "Demon", "Demon Lord", "Hero", "Heroine", "King", "Queen", "Prince", "Princess", "Duke", "Duchess", "Count", "Countess", "Baron", "Baroness", "God", "Goddess", "Lord", "Lady",
    "Your", "Yours", "Mine", "Ours", "Theirs",
    "Poisoned", "Injured", "Wounded", "Bleeding", "Dying", "Dead", "Corrupted", "Broken", "Shocked", "Terrified", "Trapped", "Forced", "Surrounded", "Determined", "Unable", "Aware", "Unaware", "Afraid", "Lost", "Hidden", "Suddenly", "Immediately", "Naturally", "Unfortunately", "Fortunately", "Clearly", "Obviously", "Slowly", "Quickly", "Carefully", "Silently", "Softly", "Loudly", "Gently", "Calmly"
}

_POSSESS_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:took|picked up|pocketed|received|was handed|accepted|stole|grabbed|kept|now held|carried)\s+(?:the|a|an|his|her)?\s*([a-z][a-z\- ]{2,40}?)(?:[.,;]| and | from | that | which )")
_LOSE_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:dropped|lost|gave away|handed over|surrendered|threw away|left behind)\s+(?:the|a|an|his|her)?\s*([a-z][a-z\- ]{2,40}?)(?:[.,;]| to | and )")
_MOVE_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:arrived at|reached|entered|walked into|stepped into|returned to|left for|rode to|traveled to|travelled to|came to)\s+(?:the\s+)?([A-Z][A-Za-z' ]{2,40}?)(?:[.,;]|\s(?:and|where|as|with|before|after)\b)")
_INJURY_EN = re.compile(r"\b([A-Z][a-z]+)(?:'s)?\s+(?:was|were|got|had been)\s+(wounded|injured|cut|stabbed|shot|burned|bruised|poisoned|broken|blinded|knocked out|bleeding)")
_LEARN_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:learned|realized|realised|discovered|found out|now knew|understood at last|understood)\s+(?:that\s+)?([^.?!\n]{6,160})")
_DIE_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:died|was dead|was killed|fell dead|breathed (?:his|her) last)")
_JOIN_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:joined|was inducted into|swore (?:an oath|fealty) to|became a member of|was expelled from|left)\s+(?:the\s+)?([A-Z][A-Za-z' ]{2,40}?)(?:[.,;]|\s(?:and|as)\b)")
_PROMISE_EN = re.compile(r"\b([A-Z][a-z]+)\s+(?:promised|swore|vowed)\s+(?:to\s+)?([^.?!\n]{4,140})")

_POSSESS_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가)\s+([\uac00-\ud7a3 ]{2,20}?)(?:을|를)\s+(?:받았다|챙겼다|집어\s?들었다|손에\s?넣었다|얻었다|가져갔다|건네받았다)")
_LOSE_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가)\s+([\uac00-\ud7a3 ]{2,20}?)(?:을|를)\s+(?:잃었다|떨어뜨렸다|넘겨주었다|버렸다|빼앗겼다)")
_MOVE_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가)\s+([\uac00-\ud7a3 ]{2,20}?)(?:에|으로|로)\s+(?:도착했다|들어섰다|들어갔다|돌아왔다|향했다|올랐다|내려갔다)")
_INJURY_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가|의)\s+[\uac00-\ud7a3 ]{0,12}?(다쳤다|베였다|찔렸다|부러졌다|피를\s?흘렸다|중독되었다|쓰러졌다|화상을\s?입었다)")
_LEARN_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가)\s+([^.?!\n]{4,80}?)(?:는|다는|라는)\s+(?:것을|사실을)\s+(?:알았다|알게\s?되었다|깨달았다|눈치챘다)")
_DIE_KO = re.compile(r"([\uac00-\ud7a3]{2,4})(?:은|는|이|가)\s+(?:죽었다|숨을\s?거두었다|목숨을\s?잃었다|살해당했다)")


class ClaimModel(BaseModel):
    """One structured claim the drafting model may report."""

    kind: str = Field(description="entity_introduced | possession_gained | possession_lost | location_changed | injury | knowledge_gained | relationship_changed | death | membership_changed | promise_made | other")
    subject: str = Field(default="")
    value: str = Field(default="")
    evidence: str = Field(default="", description="Exact sentence from the prose supporting the claim")


class ChapterClaims(BaseModel):
    claims: List[ClaimModel] = Field(default_factory=list)
    summary: str = Field(default="")
    ending_location: str = Field(default="")
    current_time: str = Field(default="")
    unresolved_immediate_action: str = Field(default="")
    open_dialogue_obligation: str = Field(default="")


@dataclass
class Claim:
    kind: str
    subject: str
    value: Any
    evidence: str
    span: Tuple[int, int]
    support: str = "explicit"
    source: str = "deterministic"  # or "model"

    def as_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "subject": self.subject, "value": self.value, "evidence": self.evidence[:240], "span": list(self.span), "support": self.support, "source": self.source}


def split_prose_and_claims(raw: str) -> Tuple[str, Optional[ChapterClaims]]:
    """Separate the visible prose from an optional <claims>{json}</claims> block."""
    m = CLAIMS_BLOCK_RX.search(raw or "")
    if not m:
        return (raw or "").strip(), None
    prose = (raw[: m.start()] + raw[m.end():]).strip()
    try:
        data = json.loads(m.group(1))
        return prose, ChapterClaims.model_validate(data)
    except Exception:
        return prose, None


def _find(prose: str, needle: str, start: int = 0) -> Tuple[int, int]:
    idx = prose.find(needle, start)
    return (idx, idx + len(needle)) if idx >= 0 else (-1, -1)


def named_entities(prose: str, language: Optional[str] = None) -> Dict[str, List[Tuple[int, int]]]:
    """Candidate proper names with spans. EN: capitalized non-sentence-initial-stopwords; KO: Hangul 2-4 syllable tokens before particles."""
    lang = language or detect_language(prose)
    out: Dict[str, List[Tuple[int, int]]] = {}
    if lang == "ko":
        for m in _HANGUL_NAME.finditer(prose):
            out.setdefault(m.group(0), []).append((m.start(), m.end()))
        return out
    sentences = split_sentences(prose, lang)
    starts: set = set()
    pos = 0
    for s in sentences:
        idx = prose.find(s, pos)
        if idx >= 0:
            starts.add(idx)
            m_lead = re.match(r'^[\s"“\'‘(\[]+', s)
            if m_lead:
                starts.add(idx + m_lead.end())
            pos = idx + len(s)
    for m in re.finditer(r'(?:^|\n)[\s"“\'‘(\[]*', prose):
        starts.add(m.end())
    raw_matches: Dict[str, List[Tuple[int, int]]] = {}
    for m in _CAP_NAME.finditer(prose):
        token = m.group(0)
        first = token.split()[0]
        if first in _COMMON_CAP:
            continue
        # Skip tokens that are part of a contraction (e.g. Don't, Won't, It's)
        if m.end() < len(prose) and prose[m.end()] in ("'", "’"):
            continue
        # A sentence-initial single capitalized word needs a second occurrence
        # (anywhere) to count as a name; a one-off could be an ordinary word.
        if m.start() in starts and " " not in token:
            others = [x for x in re.finditer(rf"\b{re.escape(token)}\b", prose) if x.start() != m.start()]
            if not others:
                continue
        out.setdefault(token, []).append((m.start(), m.end()))
    return out


def extract_claims(prose: str, language: Optional[str] = None) -> List[Claim]:
    lang = language or detect_language(prose)
    claims: List[Claim] = []

    def add(kind: str, subject: str, value: Any, m: re.Match, support: str = "explicit") -> None:
        claims.append(Claim(kind=kind, subject=subject.strip(), value=value, evidence=prose[m.start():m.end()], span=(m.start(), m.end()), support=support))

    if lang == "ko":
        for m in _POSSESS_KO.finditer(prose):
            add("possession_gained", m.group(1), m.group(2).strip(), m)
        for m in _LOSE_KO.finditer(prose):
            add("possession_lost", m.group(1), m.group(2).strip(), m)
        for m in _MOVE_KO.finditer(prose):
            add("location_changed", m.group(1), m.group(2).strip(), m)
        for m in _INJURY_KO.finditer(prose):
            add("injury", m.group(1), m.group(2).strip(), m)
        for m in _LEARN_KO.finditer(prose):
            add("knowledge_gained", m.group(1), m.group(2).strip(), m)
        for m in _DIE_KO.finditer(prose):
            add("death", m.group(1), "dead", m)
        return claims
    for m in _POSSESS_EN.finditer(prose):
        add("possession_gained", m.group(1), m.group(2).strip(), m)
    for m in _LOSE_EN.finditer(prose):
        add("possession_lost", m.group(1), m.group(2).strip(), m)
    for m in _MOVE_EN.finditer(prose):
        add("location_changed", m.group(1), m.group(2).strip(), m)
    for m in _INJURY_EN.finditer(prose):
        add("injury", m.group(1), m.group(2).strip(), m)
    for m in _LEARN_EN.finditer(prose):
        add("knowledge_gained", m.group(1), m.group(2).strip(), m, support="strongly_entailed" if "realized" in m.group(0) or "realised" in m.group(0) else "explicit")
    for m in _DIE_EN.finditer(prose):
        add("death", m.group(1), "dead", m)
    for m in _JOIN_EN.finditer(prose):
        add("membership_changed", m.group(1), m.group(2).strip(), m)
    for m in _PROMISE_EN.finditer(prose):
        add("promise_made", m.group(1), m.group(2).strip(), m, support="strongly_entailed")
    return claims


def merge_model_claims(prose: str, deterministic: List[Claim], model: Optional[ChapterClaims]) -> List[Claim]:
    """Model claims are accepted only when their evidence sentence exists in the prose."""
    out = list(deterministic)
    if model is None:
        return out
    for mc in model.claims:
        ev = (mc.evidence or "").strip()
        span = _find(prose, ev) if ev else (-1, -1)
        if span[0] < 0:
            out.append(Claim(kind=mc.kind, subject=mc.subject, value=mc.value, evidence=ev, span=(-1, -1), support="unsupported", source="model"))
            continue
        dup = any(c.kind == mc.kind and c.subject.lower() == mc.subject.lower() and str(c.value).lower() == str(mc.value).lower() for c in out)
        if not dup:
            out.append(Claim(kind=mc.kind, subject=mc.subject, value=mc.value, evidence=ev, span=span, support="strongly_entailed", source="model"))
    return out


__all__ = ["CLAIMS_VERSION", "ChapterClaims", "Claim", "ClaimModel", "extract_claims", "merge_model_claims", "named_entities", "split_prose_and_claims"]
