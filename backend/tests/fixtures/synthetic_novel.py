"""Synthetic source novel used by the end-to-end reverse-engineering tests.

Deterministic, repository-owned, and deliberately stylized so the fingerprint
has something to measure:

- first-person POV (narrator: Ilse Varn), stable across every chapter
- short paragraphs, frequent fragments, restrained emotion (few emotion words)
- recurring scene template: quiet opening -> dialogue confrontation ->
  false reassurance -> escalation -> reinterpretation cliffhanger (question)
- asymmetric knowledge: Ilse does not know that Brann Hale is the courier
  until chapter 18 (a reveal ladder: clue ch.5, clue ch.11, clue ch.15, reveal ch.18)
- setup/payoff: the copper key planted in ch.2 pays off in ch.14
- relationship progression: Ilse <-> Marit Solen from wary to trusting
- every chapter ends with a one-line question hook

Entity names (Ilse Varn, Marit Solen, Brann Hale, Tessaly, Greywater Exchange,
the copper key, the Lantern Ward) are the "source entities" that must never
leak into an original project.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

SOURCE_CHARACTERS: Dict[str, str] = {"Ilse Varn": "Protagonist", "Marit Solen": "Deuteragonist", "Brann Hale": "Antagonist", "Oskar Penhallow": "Supporting Character"}
SOURCE_LOCATIONS = ["Tessaly", "Greywater Exchange", "Lantern Ward", "Saltmarket"]
SOURCE_ITEMS = ["copper key", "sealed ledger"]
SOURCE_ORGANIZATIONS = ["Greywater Exchange", "the Tidewardens"]

CHAPTER_COUNT = 24

_OPENINGS = [
    "The bell over the Saltmarket rang twice before dawn.",
    "Rain again. The Lantern Ward smelled of wet rope.",
    "Marit was already waiting when I came down.",
    "Nobody in Tessaly locks a door they expect to open again.",
    "The ledger sat where I had left it. Closed. Cold.",
    "Three lamps. One lit.",
]

_CONFRONTATIONS: List[Tuple[str, str]] = [
    ("You were seen at the Exchange.", "I was seen everywhere. That is what a clerk is for."),
    ("Brann asked about you.", "Brann asks about everyone. He collects questions."),
    ("The Tidewardens want the ledger.", "Then they can want it a while longer."),
    ("Someone opened the east gate last night.", "Someone always does."),
    ("You are lying to me, Ilse.", "Not to you. Around you. There is a difference."),
    ("Where is the key?", "Where it has always been."),
]

_REASSURANCE = [
    "Marit said it was nothing. I let her say it.",
    "Oskar laughed and told us the harbor was quiet. It was.",
    "For an hour the Ward was only a street.",
    "Nothing moved on the water. That should have settled me.",
    "Brann smiled the way he does. Easy. Practiced.",
    "I told myself the ledger was safe. Twice.",
]

_ESCALATIONS = [
    "Then the lamp in the Exchange window went out, and it was not the wind.",
    "Then the courier's mark appeared on our door. Fresh chalk. Still damp.",
    "Then Oskar did not come back from the Saltmarket.",
    "Then the Tidewardens closed the east gate from the inside.",
    "Then the sealed ledger was gone from the drawer, and the drawer was locked.",
    "Then Marit's hand found my sleeve, and it was shaking.",
]

_HOOKS = [
    "Who had known I would be there?",
    "If Brann was not the courier, why did he have the chalk?",
    "What had Marit not told me?",
    "How long had the gate been open?",
    "Whose ledger was it, really?",
    "Why did the key still fit?",
]

_CLUES: Dict[int, str] = {
    2: "Marit handed me the copper key. \"Keep it,\" she said. \"You will know the door when you see it.\" I put it in my coat and forgot it, which is the sort of thing I tell myself.",
    5: "There was chalk under Brann's nails. White, fine. He wiped it on his coat when he saw me looking.",
    11: "The courier's mark was always drawn left-handed. Brann poured with his left. I noticed and did not think about it.",
    14: "The copper key turned in the Exchange cellar door as if it had been cut for it. Marit had known. Marit had always known which door.",
    15: "Oskar said the courier smelled of lamp oil. Brann's coat smelled of lamp oil. I told myself half the Ward did.",
    18: "Brann set the chalk down between us. \"You knew,\" he said. \"You have known since the cellar.\" I had. I had not let myself finish the thought until now.",
}

_RELATIONSHIP: Dict[int, str] = {
    1: "I did not trust Marit. I wanted to, which is worse.",
    6: "Marit walked me home without asking. I did not send her away.",
    12: "\"You could have told me,\" I said. \"You would not have listened,\" Marit said. She was right.",
    20: "I gave Marit the ledger. Not because I had to. Because I had stopped counting the reasons not to.",
    24: "Marit did not ask where I was going. She came.",
}


def chapter_text(n: int) -> str:
    """Deterministic chapter n (1-based)."""
    i = n - 1
    open_line = _OPENINGS[i % len(_OPENINGS)]
    q, a = _CONFRONTATIONS[i % len(_CONFRONTATIONS)]
    reassurance = _REASSURANCE[i % len(_REASSURANCE)]
    escalation = _ESCALATIONS[i % len(_ESCALATIONS)]
    hook = _HOOKS[i % len(_HOOKS)]
    speaker = "Marit" if i % 3 else "Brann"
    paras = [
        open_line,
        "I counted the steps to the door. Fourteen. Same as yesterday.",
        f"{speaker} did not look up. \"{q}\"",
        f"\"{a}\"",
        "Silence. The good kind, for a moment.",
        f"Page {n} of the ledger was blank. I wrote the date and nothing else.",
        reassurance,
        "I checked the drawer. I checked the window. I checked the drawer again.",
    ]
    if n in _CLUES:
        paras.append(_CLUES[n])
    if n in _RELATIONSHIP:
        paras.append(_RELATIONSHIP[n])
    paras += [
        f"Oskar came by with bread and no news. \"Quiet night,\" he said. I nodded. We both knew what quiet meant in the Ward.",
        "I went out anyway.",
        escalation,
        "I did not run. Running is a confession.",
        f"The lamps along Greywater Exchange were dark by the time I reached the corner. {'Marit' if speaker == 'Brann' else 'Brann'} was standing under the last one.",
        "\"Go home, Ilse.\"",
        "\"Not yet.\"",
        hook,
    ]
    return "\n\n".join(paras)


def build_txt(count: int = CHAPTER_COUNT) -> str:
    parts = ["Ledger of the Lantern Ward", "", "A synthetic novel for automated tests.", ""]
    for n in range(1, count + 1):
        parts.append(f"Chapter {n}: {['Bell', 'Rain', 'Waiting', 'Doors', 'Ledger', 'Lamps'][(n - 1) % 6]} {n}")
        parts.append("")
        parts.append(chapter_text(n))
        parts.append("")
    parts += ["Afterword", "", "Thank you for reading this synthetic fixture. It exists only to exercise the pipeline.", ""]
    return "\n".join(parts)


def fake_chapter_analysis(n: int) -> Dict:
    """Deterministic 'analysis model' output for chapter n, with real quotes plus one fabricated quote."""
    text = chapter_text(n)
    paras = text.split("\n\n")
    real_quote = paras[2][:110]
    hook = paras[-1]
    fabricated = "The moon hung like a coin over the harbor and the ships sang."  # never in the text
    return {
        "chapter_number": n,
        "title": f"Chapter {n}",
        "summary": f"Ilse counts steps, is confronted by {'Marit' if (n - 1) % 3 else 'Brann'}, is reassured, then the situation escalates and ends on a question.",
        "pov": "Ilse Varn",
        "locations": ["Lantern Ward", "Greywater Exchange"],
        "scenes": [
            {"goal": "Ilse wants a quiet morning", "conflict": "a confrontation about the ledger", "function": "confrontation", "evidence": [{"chapter_number": n, "quote": real_quote}], "summary": "confrontation about the ledger"},
            {"goal": "Ilse wants reassurance", "conflict": "false calm", "function": "false_victory", "evidence": [{"chapter_number": n, "quote": paras[6][:100]}], "summary": "false calm before escalation"},
            {"goal": "Ilse investigates", "conflict": "escalation", "function": "escalation", "evidence": [{"chapter_number": n, "quote": paras[-6][:120]}], "summary": "escalation in the Ward"},
        ],
        "hook": hook,
        "hook_type": "question",
        "events": ["confrontation", "reassurance", "escalation"],
        "participants": ["Ilse Varn", "Marit Solen", "Brann Hale", "Oskar Penhallow"],
        "state_changes": [],
        "knowledge_changes": [_CLUES[n][:80]] if n in _CLUES else [],
        "relationship_changes": [_RELATIONSHIP[n][:80]] if n in _RELATIONSHIP else [],
        "setups": ["copper key"] if n == 2 else [],
        "payoffs": ["copper key"] if n == 14 else [],
        "reveals": ["Brann is the courier"] if n == 18 else [],
        "emotion": {"chapter_number": n, "tension": 5 + (n % 4), "satisfaction": 3 if n % 3 else 6, "curiosity": 7, "humor": 2, "intimacy": 2 + (n // 6), "rewards": ["mystery_answer"] if n in (14, 18) else (["competence"] if n % 2 == 0 else []), "dominant_function": "escalation"},
        "techniques": ["short paragraph blocks during confrontation", "false reassurance before escalation", "question-hook chapter ending", "withheld emotion through action"],
        "evidence": [
            {"chapter_number": n, "quote": paras[0][:100], "note": "quiet opening"},
            {"chapter_number": n, "quote": hook, "note": "question hook ending"},
            {"chapter_number": n, "quote": fabricated, "note": "FABRICATED quote for the evidence test"},
        ],
    }


def fake_genome() -> Dict:
    return {
        "genome_thinking": "Pressure and reward alternate on a two-chapter cycle; reveals reinterpret earlier scenes.",
        "source_title": "Ledger of the Lantern Ward",
        "patterns": [
            {"dimension": "chapter_hook_engine", "description": "Each chapter ends on a single-line question that reinterprets the chapter's false reassurance.", "typical_sequence": ["quiet opening", "dialogue confrontation", "false reassurance", "escalation", "question hook"], "average_cycle_chapters": "1", "conditions": ["every chapter"], "variations": ["the question targets a person, a place or an object in rotation"], "evidence": [{"chapter_number": 1}, {"chapter_number": 2}, {"chapter_number": 3}], "why_it_works": "The reader re-reads the reassurance as a lie.", "risks": ["monotony if the question form never varies"], "transferable_abstraction": "Close each chapter by turning an earlier moment of calm into an open question, without adding new information."},
            {"dimension": "mystery_engine", "description": "Clues about the antagonist's secret role are dropped through the narrator's noticed-but-dismissed details.", "typical_sequence": ["notice a physical detail", "dismiss it", "notice a second detail", "connect them late"], "average_cycle_chapters": "6", "conditions": ["a secret identity"], "variations": ["the dismissal is voiced by another character"], "evidence": [{"chapter_number": 5}, {"chapter_number": 11}, {"chapter_number": 18}], "why_it_works": "The reader connects clues before the narrator does.", "risks": ["clues too obvious"], "transferable_abstraction": "Let the narrator record incriminating details and explicitly refuse to interpret them, so the reveal is a completion rather than a surprise."},
            {"dimension": "relationship_engine", "description": "Trust between Ilse Varn and Marit Solen grows through unasked protective actions in the Lantern Ward.", "typical_sequence": ["distrust", "unasked help", "accepted help"], "average_cycle_chapters": "6", "conditions": [], "variations": [], "evidence": [{"chapter_number": 1}, {"chapter_number": 6}, {"chapter_number": 12}], "why_it_works": "", "risks": [], "transferable_abstraction": "Advance trust through Ilse Varn accepting help she did not ask for, inside the Lantern Ward."},
        ],
    }
