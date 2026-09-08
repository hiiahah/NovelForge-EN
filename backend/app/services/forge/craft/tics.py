"""Deterministic AI-tic detector for webnovel prose.

Every rule is a compiled regex with an editor-facing message and a rewrite hint.
Rules are grouped by family so the critic can report counts and so the polish
prompt can cite spans precisely. The catalogue is intentionally opinionated:
it targets the patterns that make model prose read as "generic literary AI"
rather than a serialized webnovel with a living narrator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Tuple


@dataclass(frozen=True)
class TicRule:
    code: str
    family: str
    pattern: re.Pattern
    message: str
    hint: str
    severity: str = "medium"
    max_per_chapter: int = 0  # 0 = every hit is a tic; N = tolerate N occurrences before flagging


@dataclass
class TicHit:
    code: str
    family: str
    severity: str
    span: Tuple[int, int]
    quote: str
    message: str
    hint: str

    def as_dict(self) -> Dict[str, object]:
        return {"code": self.code, "family": self.family, "severity": self.severity, "span": list(self.span), "quote": self.quote, "message": self.message, "hint": self.hint}


def _rx(p: str, flags: int = re.I) -> re.Pattern:
    return re.compile(p, flags)


# Words that carry no image and are almost never used by human genre authors at this density.
_ABSTRACT_NOUNS = r"(?:testament|tapestry|symphony|kaleidoscope|labyrinth|crucible|juxtaposition|paradigm|dichotomy|cacophony|palpable)"
_THERAPY = r"(?:process(?:ing)? (?:his|her|their|my) (?:emotions|feelings|trauma)|(?:emotional|mental) (?:bandwidth|labou?r)|boundar(?:y|ies)|validat(?:e|ed|ing) (?:his|her|their|my) feelings|trauma response|coping mechanism|self[- ]care|hold(?:ing)? space)"

RULES: Sequence[TicRule] = (
    # --- contrast scaffolds -----------------------------------------------
    TicRule("not_x_but_y", "contrast_scaffold", _rx(r"\b(?:it was(?:n't| not)|wasn't|weren't|isn't|aren't|not)\s+(?:just|merely|simply|only|so much)?\s*[^.;!?\n]{2,60}?[,;]?\s+but\s+(?:rather\s+|also\s+)?(?=[a-z])"),
            "'Not X, but Y' contrast scaffold", "State the Y directly, or dramatize the difference through action", "medium", 1),
    TicRule("not_because_but", "contrast_scaffold", _rx(r"\bnot because\b[^.;!?\n]{3,80}\bbut because\b"), "'Not because… but because…' explanation scaffold", "Cut the disclaimed reason; keep the real one and show it", "medium", 1),
    TicRule("less_x_more_y", "contrast_scaffold", _rx(r"\b(?:less|more)\s+(?:a|an|of\s+a|like\s+a)?\s*[\w' -]{2,40}?\s+(?:and|than)\s+(?:more|less)\s+(?:a|an|of\s+a|like\s+a)?\b"), "'Less X, more Y' scaffold", "Pick one image", "low", 1),
    # --- narrator-as-lecturer -------------------------------------------------
    TicRule("silent_testament", "lecture", _rx(r"\b(?:a |the )?(?:silent |quiet |stark |grim |living )?" + _ABSTRACT_NOUNS + r"\s+(?:to|of)\b"), "Abstract-noun lecturing ('a testament to', 'a tapestry of')", "Replace with a concrete thing the POV can see, hear or count", "high"),
    TicRule("reminder_that", "lecture", _rx(r"\b(?:a|the)\s+(?:stark|grim|sobering|cruel|gentle|quiet|constant)?\s*reminder (?:that|of)\b"), "'A reminder that…' narrator gloss", "Delete the gloss; the reader felt it already", "medium"),
    TicRule("in_that_moment", "lecture", _rx(r"\b(?:in|at) that (?:single |exact |precise )?(?:moment|instant)\b"), "'In that moment' epiphany stamp", "Cut; put the realization in an action or a thought with its own voice", "medium", 1),
    TicRule("something_shifted", "lecture", _rx(r"\bsomething (?:in|inside|within) (?:him|her|them|me|the (?:room|air))\s+(?:shifted|changed|broke|snapped|settled|clicked)\b"), "'Something shifted' vague interior turn", "Name what changed in the POV's own idiom", "medium"),
    TicRule("unspoken_understanding", "lecture", _rx(r"\b(?:an?\s+)?(?:unspoken|silent|wordless)\s+(?:understanding|agreement|acknowledg(?:e)?ment|communication|conversation)\b"), "'Unspoken understanding' telepathy shortcut", "Show the two gestures that did the talking", "medium"),
    TicRule("weight_of", "lecture", _rx(r"\bthe (?:full |sheer |crushing |sudden )?weight of (?:his|her|their|my|the|what|it|everything)\b"), "'The weight of…' abstraction", "Give the weight a body: shoulders, breath, a thing set down", "low", 1),
    TicRule("mix_of_emotions", "lecture", _rx(r"\b(?:a )?(?:mix(?:ture)?|blend|cocktail|swirl|storm|wave|surge|flood|pang|flicker)\s+of\s+(?:\w+\s+){0,2}(?:emotion|feeling|fear|relief|guilt|anger|hope|dread|shame|pride)s?\b"), "Emotion-cocktail noun phrase", "One emotion, shown through what the POV does next", "medium"),
    TicRule("couldnt_help_but", "lecture", _rx(r"\bcouldn'?t help but\b"), "'Couldn't help but'", "Just do the thing", "low", 1),
    TicRule("found_himself", "lecture", _rx(r"\bfound (?:himself|herself|themselves|myself)\s+\w+ing\b"), "'Found himself doing' passive agency", "Let the character act", "low", 2),
    TicRule("little_did", "lecture", _rx(r"\blittle did (?:he|she|they|I) (?:know|realize|suspect)\b"), "Omniscient 'little did he know'", "Stay inside the POV; foreshadow with a detail instead", "high"),
    # --- portentous person tags ----------------------------------------------
    TicRule("the_man_who", "portent", _rx(r"\b(?:the|a) (?:man|woman|boy|girl|one|person|kind of (?:man|woman|person)) who (?:had|would|could|never|always)\b"), "'The man who…' epithet portent", "Use the name and a specific action", "medium", 1),
    TicRule("eyes_that_held", "portent", _rx(r"\beyes that (?:held|carried|spoke|betrayed|promised|had seen)\b"), "'Eyes that held…' portent", "What did the eyes do? Describe the movement", "medium"),
    TicRule("voice_barely_whisper", "portent", _rx(r"\b(?:voice|words?)\s+(?:was|were|came out|dropped to)?\s*(?:barely|little more than|no more than|hardly)\s+(?:above\s+)?a whisper\b"), "'Barely above a whisper'", "Cut the volume gloss or use a distinctive verb", "low", 1),
    TicRule("breath_didnt_know", "portent", _rx(r"\bbreath (?:he|she|they|I) (?:hadn'?t|didn'?t) (?:know|realize)[^.]{0,20}(?:holding|held)\b"), "'A breath he didn't know he was holding'", "Delete; it is the single most recognizable AI beat", "high"),
    TicRule("sent_shivers", "portent", _rx(r"\b(?:sent|send) (?:a )?(?:shivers?|chills?) (?:down|up|along) (?:his|her|their|my) spine\b"), "'Shivers down the spine'", "Replace with a body reaction specific to this character", "medium"),
    TicRule("time_stood_still", "portent", _rx(r"\btime (?:seemed to |itself )?(?:stood|stand|stopped|stop|slowed|slow)(?: still| down)?\b"), "'Time stood still'", "Slow the prose with short concrete beats instead of saying it", "medium"),
    TicRule("chapter_closing_maxim", "portent", _rx(r"\b(?:And|But) (?:that|this)\s+(?:was|is)\s+(?:the|when|how|where)\s+[^.]{5,60}(?:began|ended|changed|started)\.\s*$", re.I | re.M), "Chapter-closing maxim ('And that was when everything changed')", "End on an action, a line of dialogue or a concrete threat", "medium"),
    # --- stacked modifiers / triads ----------------------------------------
    TicRule("adjective_triad", "stacking", _rx(r"\b(\w+ly\s+)?\w+,\s+\w+,\s+(?:and|yet|but)\s+(?:utterly|deeply|impossibly|almost)?\s*\w+\s+(?:eyes|voice|smile|silence|man|woman|room|night|air)\b"), "Adjective triad before a noun", "Keep the sharpest adjective", "low", 2),
    TicRule("rule_of_three_fragment", "stacking", _rx(r"(?:^|\n)\s*\w+\.\s+\w+\.\s+\w+\.\s*(?:\n|$)", re.M), "Three one-word fragments in a row", "Allowed once per chapter for punch; more reads as a tic", "low", 1),
    TicRule("impossibly_intensifier", "stacking", _rx(r"\b(?:impossibly|unbearably|achingly|devastatingly|hauntingly|almost painfully)\s+\w+\b"), "Overwrought intensifier", "Pick a plain adverb or none", "low", 1),
    # --- therapy-speak / modern register in genre prose ---------------------
    TicRule("therapy_speak", "register", _rx(r"\b" + _THERAPY + r"\b"), "Therapy vocabulary in narration", "Translate into the world's idiom and the character's own words", "high"),
    TicRule("navigate_dynamics", "register", _rx(r"\b(?:navigat(?:e|ed|ing) (?:the |this |their |his |her )?(?:complexit|dynamic|situation|tension|relationship)|(?:power|social|group) dynamics?)\b"), "Corporate/therapy 'navigate the dynamics'", "Say what the character actually did", "medium"),
    TicRule("delve", "register", _rx(r"\bdelv(?:e|ed|ing)\b"), "'Delve' (model favourite)", "Use dig, search, go, ask", "low"),
    TicRule("tapestry_of", "register", _rx(r"\b(?:rich )?tapestry of\b"), "'Tapestry of'", "Concrete list of three things instead", "medium"),
    # --- dialogue mechanics ---------------------------------------------------
    TicRule("adverb_said", "dialogue", _rx(r"\b(?:said|asked|replied|answered|whispered|murmured|muttered)\s+\w+ly\b"), "Adverb-propped dialogue tag", "Cut the adverb; let the line and an action carry it", "low", 3),
    TicRule("exotic_tag", "dialogue", _rx(r"\b(?:he|she|they|I)\s+(?:intoned|opined|articulated|vocalized|expounded|elucidated|queried|interjected|breathed)\b"), "Exotic dialogue verb", "'Said' or nothing", "low", 1),
    TicRule("name_in_every_line", "dialogue", _rx(r"\"[^\"\n]{0,40}\b([A-Z][a-z]+)\b[^\"\n]{0,40}\"[^\n]{0,40}\n\s*\"[^\"\n]{0,40}\b\1\b"), "Characters using each other's names in consecutive lines", "People rarely say the name of the person in front of them", "low", 1),
    TicRule("as_if_to_say", "dialogue", _rx(r"\bas (?:if|though) to say\b"), "'As if to say' gesture gloss", "Let the gesture stand", "low", 1),
    # --- melodrama ---------------------------------------------------------------
    TicRule("single_tear", "melodrama", _rx(r"\b(?:a )?single tear\b"), "'A single tear'", "Cut or make the body do something less staged", "medium"),
    TicRule("heart_pounded_chest", "melodrama", _rx(r"\bheart (?:pounded|hammered|thundered|slammed|thudded) (?:in|against) (?:his|her|their|my) (?:chest|ribs|ribcage)\b"), "'Heart pounded in his chest'", "Where else would it pound? Use a fresher body cue or the POV's calculation", "low", 1),
    TicRule("world_narrowed", "melodrama", _rx(r"\b(?:the )?world (?:narrowed|shrank|fell away|dissolved|tilted)\b"), "'The world narrowed'", "Pick the one thing the POV can still see", "low", 1),
    TicRule("shattered_into_pieces", "melodrama", _rx(r"\b(?:shatter(?:ed|ing)|broke) into (?:a )?(?:thousand|million) (?:pieces|shards|fragments)\b"), "'Shattered into a thousand pieces'", "Cut", "medium"),
)

_FAMILY_ORDER = ("lecture", "portent", "contrast_scaffold", "register", "melodrama", "stacking", "dialogue")


def find_tics(text: str, *, rules: Iterable[TicRule] = RULES, max_hits: int = 60) -> List[TicHit]:
    """Return every tic hit (respecting per-rule tolerance), sorted by position."""
    hits: List[TicHit] = []
    for rule in rules:
        matches = list(rule.pattern.finditer(text))
        if not matches:
            continue
        tolerated = rule.max_per_chapter
        for m in matches[tolerated:]:
            s, e = m.start(), m.end()
            quote = text[s:e].strip().replace("\n", " ")
            hits.append(TicHit(code=rule.code, family=rule.family, severity=rule.severity, span=(s, e), quote=quote[:160], message=rule.message, hint=rule.hint))
    hits.sort(key=lambda h: h.span[0])
    return hits[:max_hits]


def tic_summary(hits: Sequence[TicHit]) -> Dict[str, object]:
    by_family: Dict[str, int] = {}
    by_code: Dict[str, int] = {}
    for h in hits:
        by_family[h.family] = by_family.get(h.family, 0) + 1
        by_code[h.code] = by_code.get(h.code, 0) + 1
    return {"total": len(hits), "by_family": {f: by_family[f] for f in _FAMILY_ORDER if f in by_family}, "by_code": by_code, "high": sum(1 for h in hits if h.severity in ("high", "critical"))}


def tic_density(hits: Sequence[TicHit], word_count: int) -> float:
    """Tics per 1000 words; the critic converts this into the ai_tics score."""
    if word_count <= 0:
        return 0.0
    return round(len(hits) * 1000.0 / float(word_count), 2)


def tics_score(density: float, high_count: int) -> int:
    """1-10: 10 = clean, 1 = saturated. Weighted so a single 'breath he didn't know' hurts."""
    score = 10.0 - density * 1.6 - high_count * 0.8
    return int(max(1, min(10, round(score))))


__all__ = ["RULES", "TicHit", "TicRule", "find_tics", "tic_density", "tic_summary", "tics_score"]
