"""Seed a demo project that exercises Story Memory against a running backend.

Usage (backend running on 127.0.0.1:54321):

    python scripts/demo_story_memory.py [--base http://127.0.0.1:54321/api] [--name "The Salt Archive"]

Creates characters, ledgers, eight written chapters with hand-written digests
(what the LLM would produce), one stale digest, one undigested chapter and a
chapter-9 outline, then prints coverage, health and the compiled recap. No
model is called.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request

BODY = ("Mira Hale walked the long shelves of the Salt Archive while Daren Voss kept to the doorway. " * 30).strip()


def call(base: str, method: str, path: str, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:54321/api")
    ap.add_argument("--name", default="The Salt Archive")
    args = ap.parse_args()
    base = args.base

    types = {t["name"]: t["id"] for t in call(base, "GET", "/card-types")}
    pid = call(base, "POST", "/projects/", {"name": args.name, "description": "Story Memory demo"})["data"]["id"]
    print(f"project {pid}")

    def mk(type_name: str, title: str, content: dict) -> dict:
        return call(base, "POST", f"/projects/{pid}/cards", {"title": title, "card_type_id": types[type_name], "content": content})

    mk("Character Card", "Mira Hale", {"name": "Mira Hale", "aliases": ["Mira", "the Archivist"], "role_type": "Protagonist", "core_drive": "protect the archive", "truth_status": "canon",
                                       "dramatic_design": {"external_goal": "keep the Salt Archive out of the Crown's hands", "internal_need": "to trust someone again", "false_belief": "knowledge is safest when nobody shares it"},
                                       "voice": {"verbal_tells": ["answers questions with questions"]}})
    mk("Character Card", "Daren Voss", {"name": "Daren Voss", "aliases": ["Daren"], "role_type": "Deuteragonist", "core_drive": "reclaim his post", "truth_status": "canon"})
    mk("Character Card", "Sela Quint", {"name": "Sela Quint", "aliases": [], "role_type": "Antagonist", "core_drive": "trade secrets", "truth_status": "canon"})
    mk("Knowledge Fact", "The seal is forged", {"fact": "The royal seal is forged", "knowers": [{"entity": "Sela Quint", "state": "knows"}], "reader_state": "unaware", "planned_reveal_chapter": 30, "sensitivity": "high", "truth_status": "canon"})
    mk("Promise Payoff", "The locked drawer", {"setup": "A locked drawer in the vault Mira never opens", "planned_payoff": "It holds her mother's last letter", "target_payoff_range": [3, 5], "status": "planted", "truth_status": "planned", "participants": ["Mira Hale"], "source_chapter": 1, "strength": "strong"})
    mk("Plot Thread", "Daren's reinstatement", {"name": "Daren's reinstatement", "thread_type": "subplot", "central_question": "Will Daren regain his post at the Gate?", "participants": ["Daren Voss"], "status": "active", "urgency": "high", "opening_chapter": 1, "last_advanced_chapter": 1, "truth_status": "planned"})
    mk("Relationship Arc", "Mira ↔ Daren", {"character_a": "Mira Hale", "character_b": "Daren Voss", "public_relationship": "archivist and disgraced guard", "private_relationship": "wary allies", "trust": 4, "affection": 3, "fear": 1, "dependency": 5, "resentment": 2, "planned_next_shift": "Daren admits he was sent to watch her", "truth_status": "canon"})
    mk("Reader Contract", "Reader Contract", {"primary_fantasy": "outwitting a corrupt court with knowledge", "primary_emotional_reward": "competence", "reward_types_priority": ["competence", "revelation", "victory"], "truth_status": "canon"})

    chapter_cards = {}
    for n in range(1, 10):
        card = mk("Chapter Text", f"Chapter {n}", {"title": f"Chapter {n}", "chapter_number": n, "volume_number": 1, "stage_number": 1, "entity_list": ["Mira Hale", "Daren Voss"], "content": BODY})
        chapter_cards[n] = card["id"]
    mk("Chapter Outline", "Ch 10", {"volume_number": 1, "stage_number": 1, "title": "The letter", "chapter_number": 10, "overview": "Mira finally forces the locked drawer and reads her mother's letter while Daren stands watch in the corridor outside the vault; a courier's horn sounds from the gate.", "entity_list": ["Mira Hale", "Daren Voss"], "pov": "Mira Hale", "forbidden_outcomes": ["Daren regains his post"], "allowed_outcomes": ["Mira learns her mother's name"]})

    for n in range(1, 9):
        digest = {
            "chapter_number": n, "volume_number": 1, "title": f"Chapter {n}", "pov": "Mira Hale", "participants": ["Mira Hale", "Daren Voss"], "locations": ["Salt Archive"],
            "story_time": f"night, day {n} of the siege", "one_line": f"Mira finds clue {n} in the archive while Daren watches the door.",
            "summary": f"In chapter {n} Mira Hale searches the Salt Archive and uncovers clue {n}, a ledger page pointing at the Crown. Daren Voss guards the doorway and reports movement at the gate. The chapter ends with both of them hearing footsteps in the corridor.",
            "ending_state": f"Mira and Daren stand in the archive vault after finding clue {n}; footsteps approach in the corridor.", "last_paragraph_gist": "Mira closes the ledger and blows out the lamp.",
            "events": [{"summary": f"Mira finds clue {n}", "participants": ["Mira Hale"], "significance": "notable", "location": "Salt Archive"}],
            "hooks_opened": [{"hook": f"Who left clue {n} in the vault?", "hook_type": "question", "strength": "medium", "expected_payoff_window": "within the arc"}],
            "dominant_function": "discovery", "tension_end": 3 + n % 5, "hook_strength": 4 + n % 4, "rewards_delivered": ["competence"], "digested_at": "2026-09-08T08:00:00",
        }
        if n == 3:
            digest["state_changes"] = [
                {"entity": "Daren Voss", "kind": "location", "before": "Salt Archive", "after": "the northern road"},
                {"entity": "Sela Quint", "kind": "alive_dead", "before": "alive", "after": "dead, drowned in the canal"},
                {"entity": "Mira Hale", "kind": "possession", "before": "carries the brass key", "after": "lost the brass key in the river"},
            ]
            digest["hooks_opened"].append({"hook": "Who sent the courier with the black seal?", "hook_type": "mystery", "strength": "strong", "expected_payoff_window": "within the arc"})
            digest["events"][0]["significance"] = "pivotal"
        if n == 5:
            digest["hooks_closed"] = [{"hook": "Who left clue 1 in the vault?", "resolution": "It was Daren", "complete": True}]
        if n == 8:
            digest["hooks_opened"].append({"hook": "Will the courier reach the capital before the gates close?", "hook_type": "deadline", "strength": "strong", "expected_payoff_window": "next chapter"})
            digest["continuity_risks"] = ["It is still night", "Mira has not yet read the letter", "Daren is on the northern road, not in the archive"]
            digest["tension_end"], digest["hook_strength"] = 8, 8
        call(base, "PUT", f"/story-memory/digests/{n}?project_id={pid}", digest)

    # Make chapter 8 stale (edit the text after digesting); chapter 9 stays undigested.
    c8 = call(base, "GET", f"/cards/{chapter_cards[8]}")
    call(base, "PUT", f"/cards/{chapter_cards[8]}", {"content": {**c8["content"], "content": c8["content"]["content"] + " Then Mira heard the courier horn."}})

    print("coverage:", json.dumps(call(base, "GET", f"/story-memory/digests?project_id={pid}")["coverage"]))
    health = call(base, "GET", f"/story-memory/health?project_id={pid}")
    print("health:", health["score"], health["grade"], "-", health["summary"])
    recap = call(base, "POST", "/story-memory/story-so-far", {"project_id": pid})
    print(f"recap: {recap['used_chars']}/{recap['budget_chars']} chars, tiers={[t['name'] for t in recap['tiers']]}, dangling={len(recap['dangling_hooks'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
