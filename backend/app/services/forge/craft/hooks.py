"""Dopamine & cliffhanger analysis: does the chapter deliver a win and end on a pull?

Deterministic signals only; the model rewrite happens in ``passes.sharpen_hook``.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from app.schemas.craft import HookAnalysis
from app.services.forge.textmetrics import classify_ending, count_units, detect_language, split_paragraphs

SOFT_ENDINGS = {"quiet_close", "image_close", "emotional_close", "empty"}

# Fade-outs: the last paragraph puts the reader to bed instead of pulling them forward.
_FADE_OUT = re.compile(r"\b(?:slept|fell asleep|went to (?:bed|sleep)|closed (?:my|his|her|their) eyes|drifted off|settled (?:in|into|over|down)|let the (?:long |quiet |dark )?(?:quiet|silence|night|dark) (?:settle|take|fall|come)|the night (?:closed|wore) on|and (?:that|it) was (?:enough|all)|for now\.?$|after all(?: and)?\b[^.]{0,40}\.|quiet(?:ly)? (?:settled|returned|fell)|nothing (?:else|more) happened|morning (?:came|would come)|(?:it|the harbor|the house|the city|the camp) (?:was|went|grew) (?:quiet|still|calm|dark) (?:again|at last|once more))", re.I)

# Hook-type cues in the final paragraphs. Order = precedence when several fire.
_HOOK_CUES = (
    ("threat_arrival", re.compile(r"\b(?:door|gate|window|shutter)s?\s+(?:burst|slammed|flew|swung|crashed)|\b(?:footsteps|hoofbeats|boots|a knock|the knock)\b|\b(?:stood|waiting|was standing)\s+(?:in|at|by|under)\s+the\s+(?:door(?:way)?|gate|threshold|last lamp|entrance)|\b(?:arrived|had come|were here|was here|had found)\b"
                                  # wrongness: something is where/how it should not be
                                  r"|\b(?:nobody|no one|none of us) had\b|\bhad gone (?:dark|quiet|silent|out|cold)\b|\bwas (?:gone|empty|missing|open|unlocked|wrong|unlit|still warm)\b|\b(?:wasn'?t|was not) supposed to\b|\bshouldn'?t have been\b|\band nobody\b|\band no one\b", re.I)),
    ("revelation", re.compile(r"\b(?:it was (?:him|her|you|them)\b|\bhad been\b[^.]{0,30}\ball along\b|\brealiz(?:ed|ation)\b|\bthe (?:truth|answer|name)\b[^.]{0,20}\bwas\b|\bnot (?:dead|human|alone|who)\b|\bI (?:know|knew) (?:who|what|why)\b)", re.I)),
    ("crisis", re.compile(r"\b(?:blood|bleeding|collapsed|fell|screamed|scream|fire|burning|blade|knife|sword|gun|shot|explosion|dead|dying|poison|trap|sprung|betray(?:ed|al))\b", re.I)),
    ("decision", re.compile(r"\b(?:I(?:'ll| will| would| am going to)|he would|she would|they would)\s+(?:kill|go|leave|stay|fight|take|open|tell|find|accept|refuse|do it)\b|\b(?:decided|chose|choice was made|there was no going back|no turning back)\b|\b(?:\"?(?:Yes|No|Fine|Done|Deal|Agreed)\.\"?)\s*$", re.I | re.M)),
    ("reversal", re.compile(r"\b(?:but|except|until|then)\s+(?:the|it|he|she|they)\b[^.]{0,40}\b(?:wasn'?t|weren'?t|never|no longer|instead)\b|\bthat was when\b|\bwhich was when\b", re.I)),
    ("question", re.compile(r"\?\s*$|\bwho had\b|\bwhy (?:would|had|did)\b|\bwhat (?:had|did|was)\b", re.I | re.M)),
)

# Micro-payoff detectors: the small wins that make a serialized chapter satisfying.
_PAYOFF_CUES = (
    ("deduction", re.compile(r"\b(?:so that(?:'s| was) (?:it|why|how)|which meant|that meant|that explained|now (?:I|he|she) (?:understood|knew|saw)|it (?:clicked|added up|fit)|the (?:pieces|numbers) (?:fit|added up|lined up)|I had (?:it|him|her|them)\b|\bso:|two (?:facts|things) for|I filed (?:it|that|them)|noted (?:it|that)\b|(?:he|she|they) (?:knew|wanted) (?:me|him|her|them) to know|which (?:he|she|they) never (?:does|did)|\bfor the price of\b)", re.I)),
    ("tactical", re.compile(r"\b(?:worked|it worked|had worked|bought (?:us|me|him|her|them) time|got (?:out|through|past|away)|slipped (?:past|through|out)|one step ahead|exactly (?:as|where) (?:I|he|she)(?:'d| had)? (?:planned|expected|said|wanted)|fell for it|took the bait)\b", re.I)),
    ("verbal_win", re.compile(r"\b(?:said nothing|had no answer|no reply|didn'?t (?:have an?|answer)|shut (?:his|her|their) mouth|mouth (?:opened|worked) (?:and|but) (?:nothing|no)|flushed|went red|looked away first|blinked first|for once,? (?:he|she|they) (?:was|were) (?:silent|quiet))\b", re.I)),
    ("comedic", re.compile(r"\b(?:snorted|barked a laugh|laughed despite|bit back a (?:laugh|grin|smile)|deadpan|dead-?pan|straight face|which was (?:not|hardly) the point|technically|in fairness|to be fair|of course (?:it|he|she|they) (?:was|did|were))\b", re.I)),
    ("status", re.compile(r"\b(?:promot(?:ed|ion)|rank(?:ed)? up|level(?:ed)? up|new (?:title|rank|skill|ability)|unlocked|awakened|breakthrough|stronger than|for the first time,? (?:I|he|she|they) (?:could|managed|held|won)|bowed|knelt|stepped aside|made way|called (?:me|him|her) (?:sir|my lord|my lady|master|captain|boss))\b", re.I)),
    ("reward", re.compile(r"\b(?:coins?|gold|silver|payment|paid|reward|prize|loot|spoils|contract|deed|key|map|letter)\b[^.]{0,40}\b(?:mine|ours|in (?:my|his|her|their) (?:hand|pocket|palm|grip)|pocketed|took|kept|counted)\b", re.I)),
)


def _tail(text: str, paragraphs: int = 3, max_chars: int = 1400) -> str:
    paras = split_paragraphs(text)
    tail = "\n\n".join(paras[-paragraphs:]) if paras else text
    return tail[-max_chars:]


def detect_hook_type(tail: str) -> str:
    for kind, rx in _HOOK_CUES:
        if rx.search(tail):
            return kind
    return "none"


def hook_strength(ending_class: str, hook_type: str, tail: str, language: Optional[str] = None) -> int:
    """0-10. Rewards sharp, short, forward-pulling endings; penalizes fade-outs."""
    score = {"question_hook": 6, "dialogue_hook": 6, "suspended_hook": 5, "punch_line": 6, "emotional_close": 3, "image_close": 2, "quiet_close": 1, "empty": 0}.get(ending_class, 3)
    score += {"threat_arrival": 3, "revelation": 3, "crisis": 3, "decision": 2, "reversal": 2, "question": 1, "none": 0}.get(hook_type, 0)
    paras = split_paragraphs(tail)
    if paras:
        last_units = count_units(paras[-1], language)
        if last_units <= 12:
            score += 1
        elif last_units > 60:
            score -= 2
    return int(max(0, min(10, score)))


def find_micro_payoffs(text: str) -> List[str]:
    found: List[str] = []
    for kind, rx in _PAYOFF_CUES:
        if rx.search(text):
            found.append(kind)
    return found


def analyze_hook(prose: str, *, closing_hook_plan: str = "", language: Optional[str] = None) -> HookAnalysis:
    lang = language or detect_language(prose)
    ending_class = classify_ending(prose, lang)
    tail = _tail(prose)
    paras = split_paragraphs(tail)
    last_para = paras[-1] if paras else tail
    # Hook cues must live in the final paragraph(s) to count; a threat two paragraphs up, followed by a fade-out, is still a fade-out.
    hook_type = detect_hook_type("\n\n".join(paras[-2:]) if paras else tail)
    fade = bool(_FADE_OUT.search(last_para))
    if fade and hook_type not in ("crisis",):
        hook_type = "none"
        if ending_class in ("punch_line", "image_close", "emotional_close"):
            ending_class = "quiet_close"
    strength = hook_strength(ending_class, hook_type, tail, lang)
    if fade:
        strength = min(strength, 2)
    is_soft = (ending_class in SOFT_ENDINGS and hook_type in ("none", "question")) or strength < 4 or fade
    payoffs = find_micro_payoffs(prose)
    plan = (closing_hook_plan or "").lower()
    suggested = "none"
    for kind, words in (("threat_arrival", ("arriv", "appear", "shows up", "at the door", "footsteps")), ("revelation", ("reveal", "learn", "discover", "realiz", "truth")), ("crisis", ("attack", "collapse", "wound", "fire", "trap", "ambush", "dies", "death")), ("decision", ("decid", "choose", "vow", "resolve", "agree", "refuse")), ("reversal", ("betray", "turns out", "instead", "twist"))):
        if any(w in plan for w in words):
            suggested = kind
            break
    if suggested == "none":
        suggested = "revelation" if "question" in ending_class else "threat_arrival"
    return HookAnalysis(ending_class=ending_class, hook_type=hook_type, strength=strength, is_soft=is_soft, tail_excerpt=tail[-600:], suggested_hook=suggested, micro_payoffs=payoffs, payoff_missing=not payoffs)


def split_for_hook_rewrite(prose: str, paragraphs: int = 2) -> Dict[str, str]:
    """Return the body to keep and the final paragraphs to rewrite."""
    paras = split_paragraphs(prose)
    if len(paras) <= paragraphs:
        return {"keep": "", "tail": prose}
    return {"keep": "\n\n".join(paras[:-paragraphs]), "tail": "\n\n".join(paras[-paragraphs:])}


__all__ = ["SOFT_ENDINGS", "analyze_hook", "detect_hook_type", "find_micro_payoffs", "hook_strength", "split_for_hook_rewrite"]
