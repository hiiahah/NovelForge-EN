#!/usr/bin/env python3
"""
rebuild_bible_opus_4_6.py
Rebuilds the entire 12-file Novel Bible for:
'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
(살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)
Exclusively powered by Claude Opus 4.6 (Genspark) following the authentic
Korean Rom-Com Survival Academy Webnovel standard ('How to Survive in the Romance Fantasy Game').
"""

import os
import sys
import json
import sqlite3
import time
from datetime import datetime

sys.path.insert(0, "/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers")
import genspark_auth

DB_PATH = "/home/ubuntu/NovelForge-EN/backend/novelforge.db"
BIBLE_DIR = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/bible"
os.makedirs(BIBLE_DIR, exist_ok=True)

PROJECT_ID = 6
MODEL_NAME = "claude-opus-4-6"

APPROVED_CHAPTER_1_PATH = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/chapters/Chapter_001.md"
with open(APPROVED_CHAPTER_1_PATH, "r", encoding="utf-8") as f:
    CHAPTER_1_SNIPPET = f.read()[:4000]

SYSTEM_PROMPT = f"""You are Claude Opus 4.6, acting as the chief narrative architect and bestselling Korean webnovel author.
You specialize in top-ranking Novelpia and Munpia pure-love harem / academy survival comedies like 'How to Survive in the Romance Fantasy Game' and 'The Novel's Extra'.

CANON CONTINUITY (APPROVED CHAPTER 1 CANON):
- Title: 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?' (살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)
- Protagonist: Kang Min-jun / Lucen Gray. 23-year-old completionist gamer with 347 hours in 'Eschaton Hearts: Twilight of the Seven Stars'.
- Stats: Mana Index 0.009 / 100.000 (less than a wooden practice dummy). All stats F-rank. Luck is an unrated dash '—'.
- Status Overview: 'A person who should not exist.' (He was supposed to be the tutorial corpse who dies during intake).
- Core Twist: Lucen survived the intake mana spike by accident, making him a living glitch. He spent 3 days writing an encyclopedic game guide in Hangul (Korean), which First Princess Iris Aurelia ('The Dawn Tyrant', Route 4 Final Boss) confiscated. She is now watching him.
- Core Dynamic: DANGER IS ROMANCE, ROMANCE IS DANGER. Lucen's desperate, flustered survival tactics are misinterpreted by the yandere final boss heroines as profound nobility, secret devotion, and unfathomable genius.
- Recurring Comic Engine: The 49 Named Bad Endings (e.g. [Bad End No. 12: The Tyrant's Curiosity], [Bad End No. 47: Sunrise Eternal]).
- Style: 1-3 sentences per paragraph, 35-45% dialogue, witty, warm, hilarious, and publication-grade. NO pseudo-engineering techno-babble!

Output full, exhaustive, publication-ready markdown or JSON. No summaries, no placeholders."""

def ask_opus(prompt: str, max_retries: int = 3) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    [Calling Claude Opus 4.6 (Attempt {attempt}/{max_retries})...]")
            start_t = time.time()
            res = genspark_auth.send_chat_completion(
                messages=messages,
                model=MODEL_NAME,
                stream=False,
                log_stream=False,
                timeout=600
            )
            content = res.get("content", "").strip()
            elapsed = time.time() - start_t
            if content and len(content) > 300:
                print(f"    [✓ Received {len(content):,} chars in {elapsed:.1f}s]")
                return content
            else:
                print(f"    [!] Short response ({len(content)} chars), retrying...")
                time.sleep(4)
        except Exception as e:
            print(f"    [!] Error calling Claude Opus 4.6: {e}")
            time.sleep(5)
    raise RuntimeError(f"Failed to generate after {max_retries} attempts.")

def save_file(filename: str, content: str) -> str:
    path = os.path.join(BIBLE_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] Saved {filename} ({len(content):,} chars)")
    return path

def upsert_card(title: str, card_type_id: int, content: str, display_order: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    c.execute("SELECT id FROM card WHERE project_id = ? AND title = ?", (PROJECT_ID, title))
    row = c.fetchone()
    if row:
        c.execute("""
            UPDATE card SET
                content = ?,
                display_order = ?,
                model_name = ?,
                card_type_id = ?,
                ai_modified = 1
            WHERE id = ?
        """, (content, display_order, MODEL_NAME, card_type_id, row[0]))
    else:
        c.execute("""
            INSERT INTO card (title, card_type_id, project_id, content, display_order, model_name, created_at, ai_modified, needs_confirmation)
            VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0)
        """, (title, card_type_id, PROJECT_ID, content, display_order, MODEL_NAME, now))
    conn.commit()
    conn.close()
    print(f"  [✓] DB Card Synced: '{title}' (Type {card_type_id}, Order {display_order})")

def main():
    print("="*75)
    print("REBUILDING NOVEL BIBLE VIA CLAUDE OPUS 4.6")
    print("Vibe: Authentic Korean Rom-Com / Harem Survival Comedy")
    print("="*75)
    
    # 1. Work Tags
    print("\n[1/12] 01_Work_Tags.json...")
    tags_prompt = """Generate the complete, publication-grade '01_Work_Tags.json' for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.
Include:
- Official English Title, Korean Title (살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)
- Primary & Secondary Genres (Romance Fantasy, Academy, Game Transmigration, Survival Comedy, Pure-Love Harem, Misunderstanding, Action-Comedy)
- Target Audience & Novelpia/Munpia Benchmarks (How to Survive in the Romance Fantasy Game, The Novel's Extra, Trash of the Count's Family)
- Core Tropes & Tags (Challenge Runner MC, F-Rank Extra, Yandere Final Boss Heroines, Stolen Cheat-Sheet, Misunderstanding Engine, 49 Named Bad Endings, 0.009 Mana Anomaly)
- Structure: 8 Volumes, 450 Chapters target.
Output strictly valid JSON."""
    tags_content = ask_opus(tags_prompt)
    if "```json" in tags_content:
        tags_content = tags_content.split("```json")[1].split("```")[0].strip()
    elif "```" in tags_content:
        tags_content = tags_content.split("```")[1].split("```")[0].strip()
    save_file("01_Work_Tags.json", tags_content)
    upsert_card("Work Tags", 2, tags_content, 1)

    # 2. One Sentence Summary
    print("\n[2/12] 02_One_Sentence_Summary.md...")
    summary_prompt = """Generate '02_One_Sentence_Summary.md' for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.
Provide:
1. The Core Logline / One-Sentence Summary (highlighting the completionist gamer, the F-rank tutorial corpse glitch, and the final boss heroines who misinterpret his desperate cowardice as supreme nobility).
2. Expanded 3-Sentence Hook (Premise, Escalation, Comic Payoff).
3. The Three Core Selling Points (Dopamine hooks for webnovel readers).
4. The Central Emotional Engine (Fear intertwined with romantic comedy and cozy harem warmth).
5. The 450-Chapter Climax Promise."""
    summary_content = ask_opus(summary_prompt)
    save_file("02_One_Sentence_Summary.md", summary_content)
    upsert_card("One Sentence Summary", 4, summary_content, 2)

    # 3. Worldview Setting
    print("\n[3/12] 03_Worldview_Setting.md...")
    world_prompt = """Generate the complete, exhaustive '03_Worldview_Setting.md' for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.
Write in rich, immersive, authentic Korean webnovel lore. Include:
1. 'Eschaton Hearts: Twilight of the Seven Stars' — The game's setting, difficulty, lore, why the heroines are all final bosses of their respective routes with horrifying Bad Endings.
2. The Celestine Empire & Astraea Imperial Academy — The grand setting, the social divide between arrogant high nobility and exploited scholarship commoners, the dormitories, training grounds, and examination arenas.
3. The Intake Calibration System & The 0.009 Anomaly — The magical diagnostic apparatus, why 0.009 is an unprecedented absurdity (less mana than an enchanted practice dummy), and why Lucen's survival is an unexplainable glitch.
4. The Imperial Court & Faction Warfare — The Emperor's declining health, the Chancellor's treasonous faction, and why Princess Iris is hyper-paranoid about assassination plots.
5. The 7 Continental Calamities — The future apocalyptic disasters that occur if the heroines fall into their canonical Bad Endings.
NO pseudo-mechanical techno-babble! Make every lore piece fuel comedy, character tension, or high-stakes drama."""
    world_content = ask_opus(world_prompt)
    save_file("03_Worldview_Setting.md", world_content)
    upsert_card("Worldview Setting", 6, world_content, 3)

    # 4. Core Blueprint
    print("\n[4/12] 04_Core_Blueprint.md...")
    blueprint_prompt = """Generate '04_Core_Blueprint.md' detailing the narrative and comedic engine of the novel.
Detail:
1. The Fundamental Dynamic: 'Danger is Romance, Romance is Danger' (Every romantic gesture from a heroine carries mortal peril; every survival maneuver from Lucen accidentally triggers affection).
2. The Comedic Misunderstanding Engine: Concrete contrast table showing Lucen's internal screaming vs. What the Heroines/Nobles believe.
3. The Stolen Hangul Notebook Dynamic: Princess Iris holding his encrypted Korean route guide as collateral, summoning him for 'decryption sessions' that turn into tense, flustered, intimate dates.
4. The 49 Named Bad Endings System: How Lucen's encyclopedic knowledge of specific Bad Endings operates as both comedic punchlines and genuine tactical warnings.
5. The Original Protagonist (Lucas Ashford): His role as an earnest, handsome, golden-retriever hero who unknowingly triggers disaster flags that Lucen has to frantically defuse from the shadows."""
    blueprint_content = ask_opus(blueprint_prompt)
    save_file("04_Core_Blueprint.md", blueprint_content)
    upsert_card("Core Blueprint", 7, blueprint_content, 4)

    # 5. Story Outline
    print("\n[5/12] 05_Story_Outline.md...")
    outline_prompt = """Generate '05_Story_Outline.md' providing the complete 8-Volume Macro Arc (Volumes 1 through 8, Chapters 1–450).
For each Volume (1 to 8), specify:
- Volume Title & Chapter Range (approx 50-60 chapters per volume)
- Primary Heroine in Focus & Featured Bad Endings to Prevent
- Central Academy / Continental Conflict
- Lucen's Desperate Survival Objective vs. The Heroines' Escalating Obsession
- Climax, Boss Battle / Calamity Defusal, and Emotional Payoff."""
    outline_content = ask_opus(outline_prompt)
    save_file("05_Story_Outline.md", outline_content)
    upsert_card("Story Outline", 5, outline_content, 5)

    # 6. Special Ability
    print("\n[6/12] 06_Special_Ability.json...")
    ability_prompt = """Generate '06_Special_Ability.json' detailing Lucen Gray's system abilities and survival toolset.
Include:
- Full Status Window: Mana Index (0.009), F-rank stats, Luck (—), Overview ('A person who should not exist').
- Survival Passives: [Breath Partition] (heart-rate control, panic suppression), [Hitbox Sense] (dodging by pixel margins), [Memory Map Overlay] (wireframe speedrun recall), [Dialogue Tree Prediction].
- The Locked Trait: [The Unwritten Variable] (unlock conditions, how it protects him from fate).
- System Quests: The blunt, comical survival quests that offer only 'You live' as the reward.
Output strictly valid JSON."""
    ability_content = ask_opus(ability_prompt)
    if "```json" in ability_content:
        ability_content = ability_content.split("```json")[1].split("```")[0].strip()
    elif "```" in ability_content:
        ability_content = ability_content.split("```")[1].split("```")[0].strip()
    save_file("06_Special_Ability.json", ability_content)
    upsert_card("Special Ability: Speedrunner HUD & Survival Systems", 3, ability_content, 6)

    # 7. Voice Ledger
    print("\n[7/12] 07_Voice_Ledger.md...")
    voice_prompt = """Generate '07_Voice_Ledger.md'—the authoritative Writing Style & Prose Bible for the novel.
Detail:
1. Pacing & Mobile Formatting: 1-3 sentences per paragraph maximum, whitespace for comedic timing and suspense.
2. Protagonist Voice (Kang Min-jun / Lucen Gray): First-person internal monologue. Lovable, flustered gamer fanboy, Twitch streamer commentary on absurd tropes, self-deprecating humor.
3. Heroine Voices & Dialogue Styles (Iris's regal predatory teasing, the Saintess's saccharine yandere devotion, the Sword Prodigy's blunt tsundere challenges).
4. Dialogue Density: 35-45% of every chapter must be active dialogue and banter.
5. Banned Words & Clichés: Strictly prohibited Western AI clichés (no 'testament to', 'tapestry', 'shivers down spine', 'smirked', 'little did he know').
6. Comedic Timing Principles: Setup -> whitespace -> punchline."""
    voice_content = ask_opus(voice_prompt)
    save_file("07_Voice_Ledger.md", voice_content)
    upsert_card("Voice Ledger & Style Guide", 9, voice_content, 7)

    # 8. Character Matrix
    print("\n[8/12] 08_Character_Matrix.md...")
    char_prompt = """Generate the comprehensive '08_Character_Matrix.md' with complete, deep character sheets.
Detail in full:
1. Lucen Gray (Kang Min-jun): Age 19, appearance, psychological profile, gamer background, motivations, fear triggers.
2. First Princess Iris Aurelia ('The Dawn Tyrant', Route 4 Boss): Platinum hair, molten-gold eyes, imperial intelligence head, secret curse/burdens, how she views Lucen.
3. Celestia Rimehart ('The Frozen Saintess / Abyssal Scholar', Route 2 Boss): Holy magic prodigy with secret terrifying research obsession.
4. Rhea Valtor ('The Ashen Sword Prodigy', Route 1 Boss): Untouchable combat genius who interprets Lucen's dodging as legendary martial enlightenment.
5. Lyra Silverclaw ('The Golden Dragon Shifter', Route 6 Hidden Heroine): Ancient dragon disguised as a junior cadet, territorial, cuddly, lethal.
6. Lucas Ashford (The Original Game Protagonist): Dashing, well-meaning, clueless golden-boy.
7. Supporting Faculty & Villains: Chancellor Malrec, Headmaster Gideon."""
    char_content = ask_opus(char_prompt)
    save_file("08_Character_Matrix.md", char_content)
    upsert_card("Character Matrix", 14, char_content, 8)

    # 9. Intimacy Matrix
    print("\n[9/12] 09_Intimacy_Matrix.md...")
    intimacy_prompt = """Generate '09_Intimacy_Matrix.md' tracking the romantic and psychological progression across all 8 volumes.
For each of the 4 primary heroines (Iris, Celestia, Rhea, Lyra):
- Affection Stages: From Stage 0 (Suspicious Curiosity / Target of Elimination) to Stage 5 (Irrevocable Devotion / World-Defying Love).
- Yandere / Death Flag Thresholds (What triggers their specific Bad Endings if Lucen mishandles them).
- Key Romantic Turning Points & Misunderstandings per Volume.
- Intimacy Milestones (Touches, shared secrets, protective instincts)."""
    intimacy_content = ask_opus(intimacy_prompt)
    save_file("09_Intimacy_Matrix.md", intimacy_content)
    upsert_card("Intimacy Matrix & Relationship Progression", 18, intimacy_content, 9)

    # 10. Foreshadowing Registry
    print("\n[10/12] 10_Foreshadowing_Registry.md...")
    foreshadow_prompt = """Generate '10_Foreshadowing_Registry.md'.
Include:
1. The 49 Named Bad Endings Catalog (Listing at least 15 of the most iconic named Bad Endings, their triggers, and gruesome/hilarious descriptions).
2. The Mystery of the Intake Calibration Attack (Who tampered with the device to kill Lucen, and why).
3. The Secret of the Hangul Notebook & The True World Origin.
4. The Locked Trait [The Unwritten Variable] payoff."""
    foreshadow_content = ask_opus(foreshadow_prompt)
    save_file("10_Foreshadowing_Registry.md", foreshadow_content)
    upsert_card("Foreshadowing Registry", 18, foreshadow_content, 10)

    # 11. Master Timeline
    print("\n[11/12] 11_Master_Timeline.md...")
    timeline_prompt = """Generate '11_Master_Timeline.md' detailing the chronological progression.
Include:
1. Volume 1 Detailed Day-by-Day Timeline (Day 0 to Day 45: Intake aftermath, orientation, first practical exam, library infiltration, banquet confrontation).
2. Macro Calendar across Years 1 to 3 (Tracking the Academy terms, festival arcs, dungeon expeditions, imperial crises)."""
    timeline_content = ask_opus(timeline_prompt)
    save_file("11_Master_Timeline.md", timeline_content)
    upsert_card("Master Timeline", 18, timeline_content, 11)

    # 12. Volume 01 Execution Package
    print("\n[12/12] 12_Volume_01_Execution_Package.md...")
    vol1_prompt = """Generate '12_Volume_01_Execution_Package.md'—the complete, scene-by-scene structural specification for all 50 chapters of Volume 1 (Chapters 1–50).
Divide into 5 Stages (10 chapters each):
- Stage 1: Chapters 1–10 (The Save File Glitch & Surviving Orientation)
- Stage 2: Chapters 11–20 (The Decryption Sessions & The First Combat Evaluation)
- Stage 3: Chapters 21–30 (The Second Heroine Awakens: The Frozen Library)
- Stage 4: Chapters 31–40 (The Midterm Labyrinth & Defusing the First Calamity)
- Stage 5: Chapters 41–50 (The Sword Princess's Duel & The Imperial Shadow Banquet)

For each chapter (1 to 50):
- Title, Story Day, POV, Characters Present.
- Dramatic Objective & Central Misunderstanding.
- Beat-by-beat progression (Opening hook, rising comedic/romantic tension, climax, cliffhanger).
- Named Bad Ending referenced or narrowly avoided."""
    vol1_content = ask_opus(vol1_prompt)
    save_file("12_Volume_01_Execution_Package.md", vol1_content)
    upsert_card("Volume 01 Execution Package", 8, vol1_content, 12)

    print("\n" + "="*75)
    print("🎉 ALL 12 NOVEL BIBLE FILES REBUILT BY CLAUDE OPUS 4.6 SUCCESSFULLY!")
    print("="*75)

if __name__ == "__main__":
    main()
