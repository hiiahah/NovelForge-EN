#!/usr/bin/env python3
"""
consult_claude_opus_critique.py
Queries Claude Opus 4.6 to critique the failed Chapter 1 draft and provide
the exact rules and rewrite blueprint to make it sound like an authentic Korean webnovel.
"""

import os
import sys
import json
import time

sys.path.insert(0, "/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers")
import genspark_auth

MODEL_NAME = "claude-opus-4-6"
CHAPTER_FILE = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/chapters/Chapter_001.md"

with open(CHAPTER_FILE, "r", encoding="utf-8") as f:
    chapter_text = f.read()

SYSTEM_PROMPT = """You are Claude Opus 4.6, acting as an elite webnovel editor and veteran bestselling webnovel author who has mastered top-ranking Korean webnovels on Novelpia, Munpia, and KakaoPage (such as 'How to Survive in the Romance Fantasy Game', 'The Novel's Extra', 'Trash of the Count's Family', and 'I Became a Third-Rate Villain in the Hero Academy').

You despise generic Western AI slop, dense pseudo-academic techno-babble, and confusing purple prose that obscures the narrative. You understand the psychology of webnovel readers who read on their phones and want immediate clarity, punchy pacing, dark humor, relatable speedrunner inner monologues, and effortless readability."""

USER_PROMPT = f"""The reader/user just read our first draft of Chapter 1 and gave this furious feedback:
\"what the fuck is this slop. i cant understand a single thing thats happening. ask claude opus 4.6 how to make it sound more like a webnovel\"

Here is the current text of Chapter 1 that caused this reaction:
---
{chapter_text}
---

Please do a deep, brutal, and authoritative breakdown addressing the user's complaint:

1. BRUTAL DIAGNOSTIC: WHY IS THIS CONFUSING AI SLOP?
   - Why couldn't the reader understand a single thing that was happening?
   - Point out the exact culprits: the techno-babble, the bizarre Western metaphors ("parking meter", "Kickstarter-funded bastards"), the mechanical overload (dwarven bore-shafts, kinetic calibration spikes, 0.009 threshold, spring pins, grease runnels), and the utter lack of clear grounding.
   - Contrast this with how authentic Korean webnovels actually introduce a transmigration scenario.

2. THE KOREAN WEBNOVEL FORMULA FOR CHAPTER 1:
   - What makes a Novelpia/Munpia academy/survival comedy opening actually work?
   - The "10-Second Rule": What must the reader understand within the first 5 lines? (Who I was, where I woke up, whose body I am in, what stupidly lethal death flag I am facing, and my absurd plan to survive).
   - How does a Korean speedrunner/gamer protagonist actually talk and think? (Conversational, self-deprecating, dry, hilarious, treating absurd fantasy tropes like annoying game bugs).
   - Paragraphing, pacing, and comedic timing.

3. CONCRETE REWRITE BLUEPRINT FOR CHAPTER 1:
   - Provide a clear, beat-by-beat guide for rewriting this exact scene (waking up on the execution/calibration slab, escaping the tutorial death flag, crawling out, bumping into Princess Iris Aurelia, escaping into the chute).
   - Show how to make every single beat crystal-clear, snappy, and entertaining so any webnovel reader instantly gets it and cannot stop smiling and reading.
   - Include sample opening paragraphs that demonstrate the exact voice and style we need.
"""

def main():
    print(f"[*] Querying {MODEL_NAME} for Webnovel Diagnosis & Rewrite Guide...")
    start_t = time.time()
    res = genspark_auth.send_chat_completion(
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT}
        ],
        model=MODEL_NAME,
        stream=False,
        log_stream=True,
        timeout=600
    )
    content = res.get("content", "").strip()
    elapsed = time.time() - start_t
    print(f"\n[✓] Received response in {elapsed:.1f}s ({len(content):,} chars)")
    
    out_file = "/home/ubuntu/NovelForge-EN/opus_4_6_webnovel_diagnosis.md"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] Saved complete diagnosis to {out_file}")

if __name__ == "__main__":
    main()
