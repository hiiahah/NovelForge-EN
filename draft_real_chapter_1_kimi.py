#!/usr/bin/env python3
"""
draft_real_chapter_1_kimi.py - Generate the CANONICAL Chapter 1 of
'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
strictly according to 12_Volume_01_Execution_Package.md:
"The Worst Possible Save File" (Roadside Bandit Ambush & Speedrunner Terrain Glitch).

Model: authnd moonshotai/kimi-k3
Reasoning Effort: high
Max Tokens: 65536
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
   - A single blank line between every paragraph.
   - NEVER write dense, wall-of-text paragraphs.
2. DIALOGUE & MONOLOGUE:
   - Deadpan, sarcastic, self-deprecating Korean gamer inner monologue (speedrunner/completionist logic).
   - High survival tension mixed with absurd comedy.
   - Swearing when panicked.
3. ABSOLUTE CANON FIDELITY TO BIBLE:
   - NO PRINCESS OR HEROINES IN THIS CHAPTER! Princess Iris is in the imperial capital.
   - Setting: Roadside forest clearing at night (Story Day 0, Prologue).
   - Protagonist wakes up in the middle of the game's opening tutorial cutscene as the nameless "Gray Servant Boy" (Lucen Gray).
   - 4 bandits are about to murder him. The merchant is already dead.
   - He survives by exploiting an infamous speedrunner bug: a broken collision mesh between an overturned wagon wheel and a boulder.
   - He breaks ribs falling into the ravine river, coughs blood and water, and averts [Bad End No. 1: A Nameless Grave].
   - The System alerts him that 637 Bad Endings remain.
4. CLOSING HOOK:
   - Ends with the signature realization:
     "How the fuck am I supposed to survive in this romance fantasy game?"
"""

user_prompt = f"""Write Chapter 1 of the Korean-style webnovel:
TITLE: I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?
(살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)

=== TONE & STYLE BENCHMARK (Study sentence rhythm, banter, and internal voice from this raw reference chapter) ===
{ref_prose}
========================================================================================================

=== CHAPTER 1 CANON SPECIFICATION (From Volume 1 Execution Package) ===
- Chapter Title: Chapter 1: The Worst Possible Save File
- Story Day: Day 0 (Prologue Night — the night before the Academy Entrance Ceremony)
- POV: Lucen Gray (real name Kang Min-jun, 23, completionist speedrunner gamer) — First Person.
- Setting: A moonlit silver birch forest clearing along the Imperial Highway. An overturned merchant carriage, smashed crates, spilled wine casks reeking of fermented grapes.
- Characters Present:
  1. Lucen Gray (Kang Min-jun): Wakes up in the malnourished, bruised body of a 16-year-old servant boy wearing a torn linen tunic.
  2. Four Bandits: Grimy, scarred highwaymen with rusted iron cleavers and torches, closing in.
  3. The System (The Evil Goddess): Sarcastic, mocking blue interface window.
- NO OTHER CHARACTERS. No heroines. No princess.

- Beat Progression:
  Beat 1: The Opening Rant & The Cutscene Awakening.
    - Min-jun was a 23-year-old completionist who sank 347 hours into [Eschaton Hearts], clearing all routes and unlocking all Bad Endings for the holographic skull trophy. He went to bed exhausted.
    - Wakes up coughing on dirt, staring at a moonlit forest clearing with silver birch trees and spilled wine.
    - His gamer brain recognizes the scene instantly: this is the unskippable 20-second opening tutorial cutscene!
    - In the game, an unnamed NPC "Gray Servant Boy" gets hacked to death by four Level 3 bandits. The next morning, the golden hero Lucas Ashford finds the boy's mangled corpse on the road, sheds tears, and swears his holy oath: "I will become strong enough to protect the weak!"
    - The realization hits him: *I am the corpse. My entire narrative purpose is to be a red puddle of character development for the protagonist.*
    - He has approximately 60 seconds before his head gets cleaved off.

  Beat 2: The Status Screen from Hell.
    - A translucent blue window appears:
      [System: Welcome, Player.]
      [You have been loaded into ESCHATON HEARTS — Story Mode.]
      [Current Identity: Lucen Gray, Servant Boy. Age 16.]
      [Status: A person who should not exist.]
      [Mana Index: 0.009 / 100,000]
      [All Stats: F]
      [Luck: —]
      [Note: An Evil Goddess warns you that you are already dead. Try not to make it worse.]
    - Min-jun's breakdown: 0.009 mana! A wooden training dummy has 0.01! He is officially less magical than a piece of oak furniture. He has no spells, no sword skill, no strength.

  Beat 3: The Threat & The Speedrunner Exploit.
    - The four bandits surround him, laughing, raising their rusty swords.
    - Bandits: "Look at this little rat. Still breathing? Boss said leave no witnesses."
    - Normal players or transmigrators would try to fight or beg. But Min-jun was a 347-hour completionist degenerate who watched speedruns.
    - He remembers: in Version 1.02 of *Eschaton Hearts*, speedrunners discovered a geometry bug! Behind the overturned wagon, the collision mesh between the rear wheel and the jagged river boulder had a 1-pixel gap with missing hitboxes because the developers never expected the scripted NPC corpse to move!
    - If you clip through that boundary, the physics engine fails to calculate floor collision and dumps you straight down the 50-foot river ravine into the Whispering Rapids — skipping the entire prologue!

  Beat 4: The Execution of the Glitch.
    - The lead bandit swings his blade down.
    - Lucen doesn't dodge normally. He does an unhinged, frantic backwards dive, kicking off the dirt, jamming his scrawny body straight into the bugged seam between the wagon wheel and the stone.
    - The bandit's sword cleaves empty air with a loud *THWACK* into the wood.
    - The world flickers. The terrain geometry tears. Lucen plunges into open void and falls down the cliff into the rushing black river!

  Beat 5: The Aftermath & The Riverbank.
    - Smashing into freezing water. Smacking against a river stone — *CRACK*. Two ribs broken.
    - Dragging his battered, half-drowned body onto the muddy gravel bank, coughing up river sludge and blood.
    - Freezing, shivering, ribs screaming in agony, but he is ALIVE.

  Beat 6: The Cliffhanger & Bad End Alert.
    - The blue system screen flashes:
      [Congratulations. You have survived Prologue Death Flag #1.]
      [Bad End No. 1: 'A Nameless Grave' — AVERTED.]
      [Warning: 637 potential Bad Endings remain.]
      [Note: The Evil Goddess thinks you should have stayed dead. It would have been kinder.]
    - Lucen stares at the screen in pure disbelief. 637 Bad Endings?! He only knew of 49 in the original game!
    - He lies on the gravel under the cold moon, bruised, broken, with 0.009 mana, a derailed tutorial, and 637 ways to die.
    - Closes with the exact line:
      "How the fuck am I supposed to survive in this romance fantasy game?"

Write the full, complete, high-quality chapter in rich, immersive, hilarious Korean webnovel prose!
"""

print("[2/5] Calling authnd moonshotai/kimi-k3 (reasoning_effort='high', max_tokens=65536)...")

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
    content = "# Chapter 1: The Worst Possible Save File\n\n" + content

# 4. Save to Chapter_001.md
os.makedirs(CHAPTERS_DIR, exist_ok=True)
ch1_file = os.path.join(CHAPTERS_DIR, "Chapter_001.md")
with open(ch1_file, "w", encoding="utf-8") as f:
    f.write(content)

print(f"[4/5] Saved to {ch1_file}")

# 5. Update novelforge.db Card 673
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
card_title = "Chapter 1: The Worst Possible Save File"
cursor.execute(
    "UPDATE card SET title=?, content=?, model_name=?, last_modified_by=? WHERE id=673",
    (card_title, json.dumps(content, ensure_ascii=False), "authnd/moonshotai/kimi-k3", "authnd_kimi_k3")
)
conn.commit()
conn.close()
print(f"[5/5] Updated novelforge.db Card #673 successfully!")

print("\n🎉 CANONICAL CHAPTER 1 GENERATION COMPLETE!")
