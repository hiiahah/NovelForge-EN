#!/usr/bin/env python3
"""
consult_claude_opus_epub.py
Sends Claude Opus 4.6 the exact opening chapters of 'How to survive in the Romance Fantasy Game.epub'
along with our failed Chapter 1 draft, to get a definitive diagnosis and overhaul plan.
"""

import os
import sys
import json
import time
import zipfile
from bs4 import BeautifulSoup

sys.path.insert(0, "/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers")
import genspark_auth

MODEL_NAME = "claude-opus-4-6"
EPUB_PATH = "/home/ubuntu/nvidia_chat_bot/How to survive in the Romance Fantasy Game.epub"
OUR_CHAPTER_PATH = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/chapters/Chapter_001.md"

# 1. Extract Chapters 1-5 from the reference EPUB
epub_chapters = []
with zipfile.ZipFile(EPUB_PATH, 'r') as z:
    for i in range(1, 6):
        matched = [f for f in z.namelist() if f.startswith(f'OEBPS/Text/{i:04d}_')]
        if matched:
            raw_html = z.read(matched[0]).decode('utf-8')
            text = BeautifulSoup(raw_html, 'html.parser').get_text().strip()
            epub_chapters.append(f"--- [REFERENCE NOVEL] CHAPTER {i} ---\n{text}\n")

reference_text = "\n".join(epub_chapters)

# 2. Read our current draft
with open(OUR_CHAPTER_PATH, "r", encoding="utf-8") as f:
    our_draft = f.read()

SYSTEM_PROMPT = """You are Claude Opus 4.6, serving as the chief editor and master webnovel author. 
You are analyzing the exact stylistic, structural, and narrative DNA of the popular webnovel 'How to Survive in the Romance Fantasy Game' and comparing it to an AI-generated draft that the user completely rejected as 'still slop'."""

USER_PROMPT = f"""The user just rejected our latest rewritten Chapter 1 with this exact message:
\"nah this still looks like slop . send claude opus 4.6 the whole epub and tell it how to fix this\"

Here is the EXACT reference text from Chapters 1 through 5 of 'How to Survive in the Romance Fantasy Game':
================================================================================
{reference_text}
================================================================================

And here is our current Chapter 1 draft that the user called 'still slop':
================================================================================
{our_draft}
================================================================================

Please provide a deep, unsparing, comprehensive analysis answering:

1. THE FUNDAMENTAL GENRE & PREMISE MISMATCH:
   - Look at how 'How to Survive in the Romance Fantasy Game' actually opens:
     * Chapter 1 starts with the hero in his room and the HEROINE (Liyana, the terrifying dragon final boss) knocking on the door: "Riley, Darling?"
     * The tension is 100% interpersonal, romantic, and comedic! He remembers specific bad endings (like [Bad end no.63: Skin for Love]).
     * She cuddles him like a cat while he freezes in mortal terror.
     * In Chapter 4/5, he meets his favorite heroine (Alice Holloway, the cute pink-haired witch) in the academy lecture hall, and accidentally sits next to her.
   - Now look at OUR draft:
     * A grim, lonely escape-room puzzle! 90% of the chapter is a guy alone on a slab in a dark basement dislocating his thumb, sliding through sewer pipes, and dodging mechanical brass spikes!
     * Almost ZERO heroine interaction, zero romantic tension, zero comedy of misunderstanding, zero warmth! It reads like a survival thriller / SAW movie rather than a romance fantasy academy comedy!
   - Why did this make our draft feel like cold, mechanical, repetitive 'slop'?

2. THE CORE INGREDIENTS OF 'HOW TO SURVIVE IN THE ROMANCE FANTASY GAME':
   - What makes Riley's voice so lovable, breezy, and addictive?
   - How does the novel balance genuine life-or-death fear with cozy, hilarious, fluffy harem comedy?
   - Notice the dialogue density: people actually TALK to each other. The heroines have vivid quirks and cute mannerisms.
   - Notice the gaming elements: named Bad Endings ([Bad end no.63: Skin for Love]), affection flags, routing, heroines being favorite waifus.

3. HOW TO RADICALLY OVERHAUL OUR CHAPTER 1 & PREMISE TO MATCH THE EPUB:
   - Should we completely scrap the solo basement escape-room setup?
   - How SHOULD Chapter 1 open so it immediately hooks the reader with the same charm as 'How to Survive in the Romance Fantasy Game'?
   - How should the protagonist and Princess Iris Aurelia (or another heroine) interact from page one?
   - Give us the exact, definitive redesign and scene-by-scene script to rewrite Chapter 1 so that it has the exact soul, charm, dialogue, and comedic brilliance of the reference novel.
"""

def main():
    print(f"[*] Sending Reference EPUB (Ch 1-5, {len(reference_text):,} chars) + Draft ({len(our_draft):,} chars) to {MODEL_NAME}...")
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
    print(f"\n[✓] Received analysis in {elapsed:.1f}s ({len(content):,} chars)")
    
    out_file = "/home/ubuntu/NovelForge-EN/opus_4_6_epub_comparison_diagnosis.md"
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[+] Saved complete comparison diagnosis to: {out_file}")

if __name__ == "__main__":
    main()
