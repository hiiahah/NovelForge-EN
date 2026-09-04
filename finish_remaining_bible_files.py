#!/usr/bin/env python3
"""
finish_remaining_bible_files.py
Generates the remaining 4 files of the Novel Bible (Tasks 9 through 12)
using Claude Opus 4.6 on the second account (or newly authenticated session),
fully informed by the 638-chapter EPUB DNA.
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

PROJECT_ID = 6
MODEL_NAME = "claude-opus-4-6"

DNA_PATH = os.path.join(BIBLE_DIR, "master_reference_epub_dna.json")
with open(DNA_PATH, "r", encoding="utf-8") as f:
    EPUB_DNA = json.load(f)

DNA_SUMMARY_TEXT = f"""
================================================================================
MASTER REFERENCE DNA: 'How to Survive in the Romance Fantasy Game' (638 Chapters)
================================================================================
1. PROTAGONIST ARCHETYPE:
   - Riley Hell -> Lucen Gray (Kang Min-jun): 23-year-old completionist speedrunner (347 hrs in 'Eschaton Hearts').
   - Stats: Mana Index 0.009 / 100.000 (less than a wooden practice dummy). All F-rank stats. Luck is an unrated dash '—'.
   - Status Overview: 'A person who should not exist.' (Living glitch who survived his tutorial death).
   - Core behavior: Desperately trying to fly under the radar as a disposable mob character, but every cowardly survival reflex is misinterpreted by heroines and nobility as supreme nobility, quiet strength, and tragic self-sacrifice.

2. THE "DANGER IS ROMANCE, ROMANCE IS DANGER" ENGINE:
   - Heroines are terrifying raid-bosses / yanderes who become continental disasters in their bad ends.
   - Every romantic interaction carries mortal peril; every evasive action accidentally triggers deep affection.
   - Heroine Cast:
     * First Princess Iris Aurelia ('The Dawn Tyrant', Route 4 Boss): Regal, molten-gold eyes, predatory intelligence, burns capital in Bad End 47.
     * Alice / Celestia Rimehart ('The Abyssal Witch / Frozen Saintess'): Playful, teasing, cuddly, secretly unhinged with terrifying magical circles.
     * Rhea Valtor ('The Ashen Sword'): Untouchable combat genius who interprets Lucen's dodging as legendary martial enlightenment.
     * Lyra Silverclaw ('The Golden Dragon Shifter'): Ancient dragon hidden as a junior cadet, fiercely possessive and cuddly.
     * Lucas Ashford (Original Protagonist): Handsome, earnest golden-boy hero who triggers crisis flags that Lucen cleans up from the shadows.

3. RECURRING COMEDIC & TACTICAL TOOLS:
   - 49 Named Bad Endings (e.g. [Bad End No. 12: The Tyrant's Curiosity], [Bad End No. 63: Skin for Love], [Bad End No. 47: Sunrise Eternal]).
   - Chaotic System Notes: [Note: An Evil Goddess warns you to stay away from bitches at all costs!]
   - The Stolen Hangul Notebook: Iris holds his handwritten Korean guide as collateral, forcing him into regular intimate 'decryption' sessions.

4. 638-CHAPTER MACRO ARC PROGRESSION (VOLUMES 1 TO 8, 450 CHAPTERS):
   - Vol 1 (Ch 1-50): The Save File Glitch & Surviving Orientation. Seat assignments, first evaluation test, Iris's scrutiny.
   - Vol 2 (Ch 51-100): The Grand Academy Festival. Secret interventions, mock duels, misunderstandings expanding to the student body.
   - Vol 3 (Ch 101-200): Midterms & The Abyssal Library. Heroine POV Interludes revealing their hidden trauma and obsessive love.
   - Vol 4 (Ch 201-300): The Emperor's Summons & Palace Banquet. Multiple heroines clash and threaten faculty/nobles to protect Lucen.
   - Vol 5 (Ch 301-370): Calamity Invasions & Intimate Romance. Deep emotional connections, saving heroines from their canonical deaths.
   - Vol 6 (Ch 371-410): The Continental Joint Tournament & Church Secrets. Unmasking the Chancellor's treason.
   - Vol 7 (Ch 411-440): The Starfall Lament Ignites. Continental Extinction Events trigger.
   - Vol 8 (Ch 441-450): Rewriting the True Ending. Lucen's 0.009 mana miracle achieves the impossible Harem True Ending.

5. PROSE STYLE & CADENCE:
   - 1-3 sentences per paragraph maximum! Generous white space for comedic timing.
   - High dialogue density (35-45%).
   - Zero pseudo-mechanical engineering jargon.
================================================================================
"""

SYSTEM_PROMPT = f"""You are Claude Opus 4.6, acting as the chief narrative architect and bestselling Korean webnovel author.
You specialize in top-ranking Novelpia and Munpia pure-love harem / academy survival comedies, perfectly matching the style, scope, and character dynamics of 'How to Survive in the Romance Fantasy Game' (638 chapters).

{DNA_SUMMARY_TEXT}

Output full, exhaustive, publication-grade markdown or JSON. Provide complete, rich, actionable specifications without placeholders or generic summaries."""

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
                account="sigmaishere619",
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
    print("COMPLETING FINAL 4 BIBLE MODULES VIA CLAUDE OPUS 4.6")
    print("===========================================================================")

    # 9. Intimacy Matrix
    print("\n[9/12] 09_Intimacy_Matrix.md...")
    p9 = """Generate '09_Intimacy_Matrix.md' tracking the romantic and psychological progression across all 8 volumes.
For each of the primary heroines (Iris, Celestia, Rhea, Lyra):
- Affection Stages 0 through 5 (from suspicious elimination target to fiercely protective/possessive love).
- Yandere / Death Flag Thresholds (Bad Endings triggered if mishandled).
- Key Romantic Turning Points & Misunderstandings per Volume.
- 3rd-Person Interlude Milestones (their private thoughts about Lucen)."""
    c9 = ask_opus(p9)
    save_file("09_Intimacy_Matrix.md", c9)
    upsert_card("Intimacy Matrix & Relationship Progression", 18, c9, 9)

    # 10. Foreshadowing Registry
    print("\n[10/12] 10_Foreshadowing_Registry.md...")
    p10 = """Generate '10_Foreshadowing_Registry.md'.
Include:
1. The 49 Named Bad Endings Catalog (Detailing at least 15 iconic Bad Endings, their triggers, and outcomes).
2. The Mystery of the Intake Calibration Attack.
3. The Hangul Notebook & True World Origins.
4. The Evil Goddess & System Secrets.
5. The Locked Trait [The Unwritten Variable] payoff."""
    c10 = ask_opus(p10)
    save_file("10_Foreshadowing_Registry.md", c10)
    upsert_card("Foreshadowing Registry", 18, c10, 10)

    # 11. Master Timeline
    print("\n[11/12] 11_Master_Timeline.md...")
    p11 = """Generate '11_Master_Timeline.md'.
Include:
1. Volume 1 Detailed Day-by-Day Timeline (Days 0 to 45: Intake aftermath, orientation, seating, first evaluation, library infiltration, banquet).
2. Macro Calendar across Years 1 to 3 (Tracking terms, festivals, expeditions, emperor summons, calamity triggers)."""
    c11 = ask_opus(p11)
    save_file("11_Master_Timeline.md", c11)
    upsert_card("Master Timeline", 18, c11, 11)

    # 12. Volume 01 Execution Package
    print("\n[12/12] 12_Volume_01_Execution_Package.md...")
    p12 = """Generate '12_Volume_01_Execution_Package.md'—the complete, scene-by-scene structural specification for all 50 chapters of Volume 1 (Chapters 1–50), modeled directly after Volume 1 of 'How to Survive in the Romance Fantasy Game'.
Divide into 5 Stages (10 chapters each):
- Stage 1: Chapters 1–10 (The Save File Glitch & Surviving Orientation)
- Stage 2: Chapters 11–20 (The Decryption Sessions & The First Evaluation Exam)
- Stage 3: Chapters 21–30 (The Second Heroine: The Frozen Library & Familiar Chaos)
- Stage 4: Chapters 31–40 (The Mock Duel & Defusing the First Calamity Flag)
- Stage 5: Chapters 41–50 (The Sword Princess's Challenge & The Shadow Banquet)

For each chapter (1 to 50):
- Title, Story Day, POV, Characters Present.
- Dramatic Objective & Central Misunderstanding.
- Beat-by-beat progression (Opening hook, rising comedic/romantic tension, climax, cliffhanger).
- Named Bad Ending referenced or narrowly avoided."""
    c12 = ask_opus(p12)
    save_file("12_Volume_01_Execution_Package.md", c12)
    upsert_card("Volume 01 Execution Package", 8, c12, 12)

    print("\n" + "="*75)
    print("🎉 ALL 12 NOVEL BIBLE FILES REBUILT BY CLAUDE OPUS 4.6 SUCCESSFULLY!")
    print("===========================================================================")

if __name__ == "__main__":
    main()
