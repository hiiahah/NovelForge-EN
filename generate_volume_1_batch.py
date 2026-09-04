#!/usr/bin/env python3
"""
generate_volume_1_batch.py - Autonomous batch generator for Volume 1
(Chapters 2 through 50) of 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'

Uses:
- Model: authnd moonshotai/kimi-k3 (reasoning_effort='high', max_tokens=65536)
- Sliding tail continuity handoff (last 2,000 chars of Chapter N-1 injected into Chapter N)
- Infinite retry logic with backoff for rate limits, hCaptcha, timeouts, and network glitches
- Automatic SQLite database card upsert (Card Type 12)
- Automatic Git commit & push after each chapter
"""

import os
import sys
import re
import time
import json
import sqlite3
import datetime
import subprocess
import zipfile
from bs4 import BeautifulSoup

# Add authnd provider path
sys.path.insert(0, '/home/ubuntu/NovelForge-EN/backend/app/services/ai/providers')
import authnd_auth

PROJECT_ROOT = "/home/ubuntu/NovelForge-EN"
CHAPTERS_DIR = os.path.join(PROJECT_ROOT, "books/I_Maxed_Survival_Final_Boss_Heroines/chapters")
BIBLE_PATH = os.path.join(PROJECT_ROOT, "books/I_Maxed_Survival_Final_Boss_Heroines/bible/12_Volume_01_Execution_Package.md")
REGISTRY_PATH = os.path.join(PROJECT_ROOT, "books/I_Maxed_Survival_Final_Boss_Heroines/bible/00_Locked_Canon_Registry.json")
DB_PATH = os.path.join(PROJECT_ROOT, "backend/novelforge.db")
EPUB_PATH = "/home/ubuntu/nvidia_chat_bot/How to survive in the Romance Fantasy Game.epub"

os.makedirs(CHAPTERS_DIR, exist_ok=True)

# 1. Load Tone Reference from EPUB & Sanitize
print("=" * 70)
print("INITIALIZING VOLUME 1 BATCH GENERATOR (CHAPTERS 2 TO 50)")
print("=" * 70)

ref_prose = ""
try:
    with zipfile.ZipFile(EPUB_PATH, 'r') as z:
        with z.open('OEBPS/Text/0001_Chapter_1_-_1_Surprises_are_Shit.xhtml') as f:
            soup = BeautifulSoup(f.read(), 'html.parser')
            raw_ref = soup.get_text()[:4000]
            # SANITIZATION: Prevent external novel entities from contaminating our story
            raw_ref = re.sub(r'\[?Hero\'s Legacy\]?', 'Eschaton Hearts', raw_ref)
            raw_ref = re.sub(r'\bRiley\b', 'Lucen', raw_ref)
            raw_ref = re.sub(r'\bLiyana\b', 'Iris', raw_ref)
            ref_prose = raw_ref
    print(f"✓ Loaded & sanitized tone reference sample ({len(ref_prose):,} chars)")
except Exception as e:
    print(f"⚠️ Warning loading reference EPUB: {e}")

# Load Locked Canon Registry
with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
    LOCKED_REGISTRY = json.load(f)
print(f"✓ Loaded 00_Locked_Canon_Registry.json (Single Source of Truth)")

# 2. Parse all 50 Chapters from 12_Volume_01_Execution_Package.md
with open(BIBLE_PATH, "r", encoding="utf-8") as f:
    bible_text = f.read()

chapter_pattern = r'(### CHAPTER (\d+)\s*[—–-]\s*\"([^\"]+)\".*?)(?=### CHAPTER \d+|\Z)'
chapter_blocks = {}
for m in re.finditer(chapter_pattern, bible_text, re.DOTALL):
    num = int(m.group(2))
    title = m.group(3).strip()
    spec = m.group(1).strip()
    chapter_blocks[num] = {"title": title, "spec": spec}

print(f"✓ Parsed {len(chapter_blocks)} chapter execution specifications from Volume 1 Package")

# 3. Automated Bible Canon Linter & Gatekeeper
def validate_and_enforce_canon(content, ch_num):
    """
    Scans and deterministically enforces 00_Locked_Canon_Registry.json
    rules before writing to disk or database.
    """
    # 1. Enforce Academy Name
    banned_academies = [
        (r'\bAldenhearst Academy\b', 'Stellaris Royal Academy'),
        (r'\bAldenhearst\b', 'Stellaris'),
        (r'\bSirius Academy\b', 'Stellaris Royal Academy'),
        (r'\bSirius\b(?=[\s\',.]+(?:Academy|grounds|dining|hall|gates))', 'Stellaris'),
        (r'\bAsterion Academy\b', 'Stellaris Royal Academy'),
        (r'\bAsterion\b(?=[\s\',.]+(?:Academy|like a lid|gates|students|hall))', 'Stellaris'),
        (r'\bSolhart Academy\b', 'Stellaris Royal Academy'),
        (r'\bSolhart\b(?=[\s\',.]+(?:Academy|first-years|hall))', 'Stellaris'),
        (r'\bAstraea Imperial Academy\b', 'Stellaris Royal Academy'),
        (r'\bAstraea\b', 'Stellaris'),
    ]
    for pattern, repl in banned_academies:
        content = re.sub(pattern, repl, content)

    # 2. Enforce Game Title
    content = re.sub(r'\*?Hero\'s Legacy\*?', '*Eschaton Hearts*', content)
    content = re.sub(r'\*?Hero\'s Dawn\*?', '*Eschaton Hearts*', content)
    content = re.sub(r'\*?Hero\'s Eternal Dawn\*?', '*Eschaton Hearts*', content)

    # 3. Enforce Protagonist Past Identity
    content = re.sub(r'\bKim Min-jun\b', 'Kang Min-jun', content)
    content = re.sub(r'\btwenty-six years old\b', 'twenty-three years old', content)
    content = re.sub(r'\btwenty-seven-year-old\b', 'twenty-three-year-old', content)
    content = re.sub(r'\bformer office worker\b', 'unemployed job-seeker and completionist gamer', content)
    content = re.sub(r'\btwenty-three-year-old office worker\b', 'twenty-three-year-old completionist gamer', content)

    # 4. Enforce Phantoms
    content = re.sub(r'\bRoland Huxley\b', 'Derrick Holt', content)
    content = re.sub(r'Mr\. Thorne\'s crimson eyes', 'The memory of the bandit\'s blade', content)
    content = re.sub(r'at the demon-boar disaster', 'in homeroom on Day 2', content)

    return content

# 4. Helper Functions
def get_db_connection():
    return sqlite3.connect(DB_PATH)

def upsert_db_card(ch_num, title, content):
    conn = get_db_connection()
    cursor = conn.cursor()
    full_title = f"Chapter {ch_num}: {title}"
    now = datetime.datetime.utcnow().isoformat()
    cursor.execute(
        "SELECT id FROM card WHERE project_id=6 AND card_type_id=12 AND display_order=?",
        (ch_num,)
    )
    row = cursor.fetchone()
    if row:
        card_id = row[0]
        cursor.execute(
            "UPDATE card SET title=?, content=?, model_name=?, last_modified_by=? WHERE id=?",
            (full_title, json.dumps(content, ensure_ascii=False), "authnd/moonshotai/kimi-k3", "authnd_kimi_k3", card_id)
        )
    else:
        cursor.execute(
            """INSERT INTO card (title, content, model_name, created_at, project_id, card_type_id, display_order, ai_modified, needs_confirmation, last_modified_by)
               VALUES (?, ?, ?, ?, 6, 12, ?, 1, 0, ?)""",
            (full_title, json.dumps(content, ensure_ascii=False), "authnd/moonshotai/kimi-k3", now, ch_num, "authnd_kimi_k3")
        )
        card_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return card_id

def git_commit_and_push(ch_num, title):
    try:
        ch_file = os.path.join(CHAPTERS_DIR, f"Chapter_{ch_num:03d}.md")
        subprocess.run(["git", "add", "-f", ch_file], cwd=PROJECT_ROOT, check=True)
        msg = f"feat(chapter{ch_num}): draft Chapter {ch_num} ({title}) via authnd Kimi K3"
        subprocess.run(["git", "commit", "-m", msg], cwd=PROJECT_ROOT, check=True)
        subprocess.run(["git", "push", "sigma123388", "main"], cwd=PROJECT_ROOT, check=True)
        print(f"  [✓] Git committed and pushed Chapter {ch_num} to sigma123388/main")
    except Exception as e:
        print(f"  [!] Git push error for Chapter {ch_num}: {e}")

# 5. System Prompt Definition
SYSTEM_PROMPT = """You are a master Korean Webnovel author serialized on Novelpia/Munpia, writing an English webnovel in the authentic, highly addictive Korean webnovel style.

=== 🔒 IMMUTABLE LOCKED CANON (ZERO DEVIATION ALLOWED) ===
1. ACADEMY NAME: MUST ALWAYS be 'Stellaris Royal Academy' (or 'Stellaris'). NEVER use Aldenhearst, Sirius, Asterion, Solhart, or Astraea.
2. GAME UNIVERSE: The game is strictly 'Eschaton Hearts'. NEVER use Hero's Legacy or Hero's Dawn.
3. PROTAGONIST: Current name Lucen Gray. Prior identity is Kang Min-jun (강민준), 23 years old, unemployed completionist speedrunner gamer (347 hours played). NEVER call him Kim Min-jun or an office worker.
4. MANA INDEX: Strictly 0.009 / 100,000. Less magical than an enchanted wooden training dummy (0.01). NEVER use 0.03.
5. SEATING: Seat 7-F (Row 7, Column F — back right corner by the window). NEVER place him in the 2nd row or center aisle.
6. TIMELINE: Lucas Ashford enrolled on Day 2 in Homeroom 1-A (Seat 4-C) and is ALREADY on campus. NEVER say he has not arrived or is arriving in two weeks.
7. PHANTOM ENTITIES: NEVER invent unintroduced characters (Roland Huxley, Mr. Thorne) or unoccurred events (demon-boars).
==========================================================

YOUR MANDATORY WRITING RULES:
1. PARAGRAPH RHYTHM & PACING:
   - Strict 1 to 3 sentences per paragraph maximum!
   - Frequent 1-sentence punchy paragraphs.
   - A single blank line between every paragraph.
   - NEVER write dense, wall-of-text paragraphs.
2. DIALOGUE & MONOLOGUE:
   - Deadpan, sarcastic, self-deprecating Korean gamer inner monologue (speedrunner/completionist logic).
   - High survival tension mixed with absurd comedy.
   - Frequent funny reactions, meta-commentary on game cliches, and panic calculations.
3. CANON & CONTINUITY FIDELITY:
   - Follow the chapter scene architecture, dramatic objective, and beat-by-beat progression strictly.
   - Seamlessly transition from the exact ending state of the previous chapter.
   - Include System window alerts, the sarcastic Evil Goddess comments, and bracketed [Bad End No. X: ...] notifications whenever danger spikes.
4. TARGET LENGTH:
   - Write a complete, substantial chapter of roughly 2,000 to 3,500 words (10,000 to 18,000 characters).
"""

# 5. Chapter Generation Function with Infinite Retry
def generate_single_chapter(ch_num):
    info = chapter_blocks.get(ch_num)
    if not info:
        print(f"❌ Error: Chapter {ch_num} not found in parsed outline!")
        return False

    title = info["title"]
    spec = info["spec"]
    ch_file = os.path.join(CHAPTERS_DIR, f"Chapter_{ch_num:03d}.md")

    # Read previous chapter tail for continuity handoff
    prev_file = os.path.join(CHAPTERS_DIR, f"Chapter_{ch_num-1:03d}.md")
    prev_tail = ""
    if os.path.exists(prev_file):
        with open(prev_file, "r", encoding="utf-8") as pf:
            prev_full = pf.read().strip()
            prev_tail = prev_full[-2500:] if len(prev_full) > 2500 else prev_full

    user_prompt = f"""Write Chapter {ch_num} of the Korean-style webnovel:
TITLE: I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?
(살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)

=== TONE & STYLE BENCHMARK (Sentence rhythm, banter, and internal voice) ===
{ref_prose}
==========================================================================

=== PREVIOUS CHAPTER IMMEDIATE ENDING (EXACT CLOSING STATE) ===
{prev_tail}
==============================================================
CONTINUITY MANDATE:
Pick up the story seamlessly from the physical, emotional, temporal, and spatial state at the end of the previous chapter. Maintain exact consistency of injuries, inventory, location, time of day, and active system alerts without jarring timeskips.

=== CHAPTER {ch_num} CANON SPECIFICATION (From Volume 1 Execution Package) ===
{spec}
=============================================================================

Write the full, complete, high-quality chapter in rich, immersive, hilarious Korean webnovel prose!
Format the output starting with '# Chapter {ch_num}: {title}'.
"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt}
    ]

    attempt = 1
    min_length = 5000  # Minimum acceptable character count

    while True:
        print(f"\n[{ch_num}/50] Generating Chapter {ch_num}: '{title}' (Attempt {attempt})...")
        t0 = time.time()
        try:
            response = authnd_auth.send_chat_completion(
                model="moonshotai/kimi-k3",
                messages=messages,
                reasoning_enabled=True,
                reasoning_effort="high",
                max_tokens=65536,
                log_fn=lambda m: print(f"    {m}", flush=True),
                stream=True
            )

            content = (response.get("content") or "").strip()
            reasoning = (response.get("reasoning_content") or "").strip()
            elapsed = time.time() - t0

            if len(content) < min_length:
                raise RuntimeError(f"Output too short ({len(content)} chars < {min_length} required).")

            # Clean header if needed
            header = f"# Chapter {ch_num}: {title}"
            if not content.startswith("# "):
                content = f"{header}\n\n" + content
            elif not content.startswith(header):
                # Replace first line with canonical title
                lines = content.splitlines()
                lines[0] = header
                content = "\n".join(lines)

            # Enforce 00_Locked_Canon_Registry rules via automated gatekeeper
            content = validate_and_enforce_canon(content, ch_num)

            # Save to disk
            with open(ch_file, "w", encoding="utf-8") as f:
                f.write(content)

            words = len(content.split())
            chars = len(content)
            print(f"  [✓] Chapter {ch_num} succeeded in {elapsed:.1f}s ({chars:,} chars, ~{words:,} words)")
            if reasoning:
                print(f"  [✓] Reasoning tokens used: ~{len(reasoning)//4:,}")

            # Database sync
            card_id = upsert_db_card(ch_num, title, content)
            print(f"  [✓] Synced DB Card #{card_id} for Chapter {ch_num}")

            # Git commit and push
            git_commit_and_push(ch_num, title)

            return True

        except Exception as exc:
            elapsed = time.time() - t0
            sleep_time = min(120, 15 + attempt * 10)
            print(f"  [⚠️] Attempt {attempt} failed after {elapsed:.1f}s: {exc}")
            print(f"  [↻] Retrying Chapter {ch_num} in {sleep_time}s (infinite retry active)...")
            time.sleep(sleep_time)
            attempt += 1

# 6. Main Batch Execution Loop (Chapters 2 to 50)
def main():
    start_ch = 2
    end_ch = 50

    print(f"\nStarting batch generation from Chapter {start_ch} to Chapter {end_ch}...")
    for ch_num in range(start_ch, end_ch + 1):
        ch_file = os.path.join(CHAPTERS_DIR, f"Chapter_{ch_num:03d}.md")
        if os.path.exists(ch_file) and os.path.getsize(ch_file) > 5000:
            print(f"[-] Chapter {ch_num} already exists ({os.path.getsize(ch_file):,} bytes), skipping to next.")
            continue

        success = generate_single_chapter(ch_num)
        if not success:
            print(f"❌ Failed to generate Chapter {ch_num}. Pausing 30s before retry...")
            time.sleep(30)

    print("\n" + "=" * 70)
    print("🎉 ALL 50 CHAPTERS OF VOLUME 1 GENERATED & SYNCHRONIZED SUCCESSFULLY!")
    print("=" * 70)

if __name__ == "__main__":
    main()
