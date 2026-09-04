#!/usr/bin/env python3
"""
draft_chapter_1_kimi.py - Generate Chapter 1 of 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
using authnd moonshotai/kimi-k3 with reasoning_effort='high' and max_tokens=65536.
"""

import os
import sys
import json
import sqlite3
import zipfile
from bs4 import BeautifulSoup

# Ensure authnd_auth is in path
sys.path.insert(0, '/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers')
import authnd_auth

CHAPTERS_DIR = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/chapters"
BIBLE_DIR = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/bible"
DB_PATH = "/home/ubuntu/NovelForge-EN/backend/novelforge.db"
EPUB_PATH = "/home/ubuntu/nvidia_chat_bot/How to survive in the Romance Fantasy Game.epub"

# 1. Extract reference text from EPUB
print("[1/5] Extracting tone reference from reference EPUB...")
ref_prose = ""
with zipfile.ZipFile(EPUB_PATH, 'r') as z:
    with z.open('OEBPS/Text/0001_Chapter_1_-_1_Surprises_are_Shit.xhtml') as f:
        soup = BeautifulSoup(f.read(), 'html.parser')
        ref_prose = soup.get_text()

print(f"  ✓ Loaded reference Chapter 1 ({len(ref_prose):,} chars)")

# 2. Build the System and User Prompt
system_prompt = """You are a master Korean Webnovel author serialized on Novelpia/Munpia, writing an English webnovel in the authentic, highly addictive Korean webnovel style.

YOUR MANDATORY WRITING RULES:
1. PARAGRAPH RHYTHM & PACING:
   - Strict 1 to 3 sentences per paragraph maximum!
   - Frequent 1-sentence punchy paragraphs.
   - A blank line between every paragraph.
   - NEVER write dense, wall-of-text paragraphs.
2. DIALOGUE & MONOLOGUE RATIO:
   - 40% active, sharp, entertaining dialogue.
   - 40% inner monologue: funny, deadpan, cynical, gamer-brained, deeply self-aware.
   - 20% vivid, punchy sensory beats.
3. THE "DANGER IS ROMANCE, ROMANCE IS DANGER" TONE:
   - The heroines are terrifying yanderes and final bosses: breathtakingly gorgeous, regal, but lethal, possessive, and capable of ending the protagonist on a whim.
   - The protagonist is in constant internal panic, calculating survival odds and Bad End flags, while externally maintaining a polite, composed, mildly bewildered poker face.
4. BAD END NOTIFICATIONS:
   - When danger spikes or the heroine makes a lethal/predatory expression, include bracketed Bad End notifications with CG memories:
     Example: [Bad End No. 12: The Tyrant's Curiosity]
     Example: [Bad End No. 4: Ashes of the Dawn]
5. STATUS SCREEN:
   - Include a crisp status window showing his pathetic stats:
     [Status Window]
     Name: Lucen Gray
     Title: The Tutorial Corpse (Glitch)
     Mana Affinity: 0.009 (Sub-Dummy)
     Special Trait: [Fate Glitch: A Person Who Should Not Exist]
6. CLOSING HOOK:
   - The chapter must build to a hilarious, high-stakes cliffhanger that ends with the exact signature realization:
     "How the fuck am I supposed to survive in this romance fantasy game?"
"""

user_prompt = f"""Write Chapter 1 of the Korean-style webnovel:
TITLE: I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?
(살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)

=== TONE & STYLE BENCHMARK (Study the sentence rhythm, banter, and internal voice from this raw reference chapter) ===
{ref_prose}
========================================================================================================

=== CHAPTER 1 ARCHITECTURE & SCENE BREAKDOWN ===
- Chapter Title: Chapter 1: Surviving the Save File
- POV: Lucen Gray (real name Kang Min-jun, 23, completionist gamer).
- Setting: Astraea Imperial Academy, Dormitory Room 402. Half past midnight. Cold moonlight through the window.
- Characters Present:
  1. Lucen Gray (Protagonist): Transmigrated 3 days ago into the body of the "tutorial corpse" who was scripted to die in the intake ceremony from a stray mana explosion so the golden hero Lucas Ashford could look noble. Lucen survived because Min-jun's soul took over. He has 0.009 mana (less than a training dummy).
  2. First Princess Iris Aurelia (Heroine): Crown Princess of the Celestine Empire, Final Boss of Route 1 ("The Dawn Tyrant"). Radiant platinum-blonde hair, terrifying molten-gold eyes, overwhelming sun/dawn mana heat that makes the dorm room air shimmer. Elegant, predatory, utterly dominant.

- Beat Progression:
  Beat 1: The opening reflection on being a completionist gamer. Surviving 347 hours, unlocking all 7 heroine routes and all 49 Bad Endings for the holographic achievement skull. The nightmare of losing a save file vs the true horror of waking up inside the save file as the tutorial corpse.
  Beat 2: The ridiculousness of surviving the intake. Waking up in the infirmary 3 days ago with the glitch status window:
    [Status Window]
    Name: Lucen Gray
    Title: The Tutorial Corpse (Glitch)
    Mana Affinity: 0.009 (Sub-Dummy)
    Special Trait: [Fate Glitch: A Person Who Should Not Exist]
  Beat 3: Productive panic. Over the last 3 days, Min-jun filled a leather-bound notebook with everything he knows: every heroine's psychological trauma, route flags, hidden items, and all 49 Bad Endings. He wrote it in Korean (Hangul), believing it was an unbreakable alien cipher in this Latin-alphabet fantasy world.
  Beat 4: The nightmare arrives. At midnight, Lucen wakes up / enters to find Princess Iris Aurelia sitting comfortably in his desk chair, moonlight glinting off her golden eyes, slowly turning the pages of his Korean notebook!
  Beat 5: The encounter & banter. Lucen freezes. His inner monologue goes into overdrive. Iris can't read Hangul, but she sensed the terrifying intent and noticed hand-drawn diagrams: the Imperial Palace layout, her private garden escape route, the crest of the Dawn Sect. She confronts him: "A commoner student with less mana than a wooden training post... writing state secrets in an unreadable demonic script. Care to explain, Lucen Gray?"
  Beat 6: The verbal tightrope. Lucen balances on the edge of death. Flashing Bad End prompts:
    [Bad End No. 12: The Tyrant's Curiosity]
    [Bad End No. 4: Ashes of the Dawn]
    Recalling the CG of Iris incinerating a spy into white ash. Lucen uses his quick wit and game lore to offer a plausible excuse that staves off execution without admitting he's from another world.
  Beat 7: The obsession hook. Iris is fascinated rather than appeased. She closes the notebook, stands up, steps close enough that he can smell sweet royal incense and feel her lethal radiant warmth. She taps his cheek or chest, declaring the notebook her collateral: "Until you translate every single stroke of this for me... your life belongs to me."
  Beat 8: Chapter Ending Hook. Iris leaves into the night. Lucen collapses against his bed, staring at the closed door in sheer disbelief and exhaustion.
  Ending with the punchline:
  "How the fuck am I supposed to survive in this romance fantasy game?"

Write the full, complete, high-quality chapter now in rich, immersive prose! Make it long, fully fleshed out, hilarious, and thrilling!
"""

print("[2/5] Calling authnd moonshotai/kimi-k3 with reasoning_effort='high' and max_tokens=65536...")

def log_print(msg):
    print(msg, flush=True)

messages = [
    {"role": "system", "content": system_prompt},
    {"role": "user", "content": user_prompt}
]

response = authnd_auth.send_chat_completion(
    model="moonshotai/kimi-k3",
    messages=messages,
    reasoning_enabled=True,
    reasoning_effort="high",
    max_tokens=65536,
    log_fn=log_print,
    stream=True
)

content = response.get("content", "").strip()
reasoning = response.get("reasoning_content", "").strip()

print(f"\n[3/5] Generation Complete!")
print(f"  ✓ Content length: {len(content):,} characters (~{len(content.split()):,} words)")
if reasoning:
    print(f"  ✓ Reasoning trace length: {len(reasoning):,} characters")

# Ensure chapter title header
if not content.startswith("# "):
    content = "# Chapter 1: Surviving the Save File\n\n" + content

# 4. Save to Chapter_001.md
os.makedirs(CHAPTERS_DIR, exist_ok=True)
ch1_file = os.path.join(CHAPTERS_DIR, "Chapter_001.md")
with open(ch1_file, "w", encoding="utf-8") as f:
    f.write(content)

print(f"[4/5] Saved to {ch1_file}")

# 5. Update novelforge.db Card 673
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
card_title = "Chapter 1: Surviving the Save File"
cursor.execute(
    "UPDATE card SET title=?, content=?, model_name=?, last_modified_by=? WHERE id=673",
    (card_title, json.dumps(content, ensure_ascii=False), "authnd/moonshotai/kimi-k3", "authnd_kimi_k3")
)
conn.commit()
conn.close()
print(f"[5/5] Updated novelforge.db Card #673 successfully!")

print("\n🎉 CHAPTER 1 GENERATION VIA KIMI K3 COMPLETE!")
