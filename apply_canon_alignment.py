#!/usr/bin/env python3
"""
apply_canon_alignment.py - Surgically enforces 00_Locked_Canon_Registry.json
across all existing chapters (Chapter 1 to Chapter 14).
"""

import os
import re
import json
import sqlite3

CHAPTERS_DIR = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/chapters"
REGISTRY_PATH = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/bible/00_Locked_Canon_Registry.json"
DB_PATH = "/home/ubuntu/NovelForge-EN/backend/novelforge.db"

with open(REGISTRY_PATH, "r", encoding="utf-8") as f:
    registry = json.load(f)

def clean_chapter_text(ch_num, text):
    modified = False
    original = text

    # 1. ACADEMY NAME STANDARDIZATION
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
        new_text = re.sub(pattern, repl, text)
        if new_text != text:
            modified = True
            text = new_text

    # 2. GAME TITLE STANDARDIZATION
    game_patterns = [
        (r'\*Hero\'s Legacy\*', '*Eschaton Hearts*'),
        (r'\bHero\'s Legacy\b', 'Eschaton Hearts'),
        (r'\*Hero\'s Dawn\*', '*Eschaton Hearts*'),
        (r'\bHero\'s Dawn\b', 'Eschaton Hearts'),
        (r'\*Hero\'s Eternal Dawn\*', '*Eschaton Hearts*'),
        (r'\bHero\'s Eternal Dawn\b', 'Eschaton Hearts'),
    ]
    for pattern, repl in game_patterns:
        new_text = re.sub(pattern, repl, text)
        if new_text != text:
            modified = True
            text = new_text

    # 3. PROTAGONIST PAST IDENTITY STANDARDIZATION
    id_patterns = [
        (r'\bKim Min-jun\b', 'Kang Min-jun'),
        (r'\btwenty-six years old\b', 'twenty-three years old'),
        (r'\btwenty-seven-year-old\b', 'twenty-three-year-old'),
        (r'\bformer office worker\b', 'unemployed job-seeker and completionist gamer'),
        (r'\btwenty-three-year-old office worker\b', 'twenty-three-year-old completionist gamer'),
    ]
    for pattern, repl in id_patterns:
        new_text = re.sub(pattern, repl, text)
        if new_text != text:
            modified = True
            text = new_text

    # 4. CHAPTER-SPECIFIC SURGICAL FIXES
    if ch_num == 3:
        # Mana index 0.03 -> 0.009
        text = re.sub(r'0\.03\b', '0.009', text)
        text = re.sub(r'point zero three', 'point zero zero nine', text, flags=re.IGNORECASE)
        text = re.sub(r'approximate magical output of a decorative throw pillow',
                      'measurably less magical than an enchanted wooden training dummy', text)
        modified = True

    elif ch_num == 6:
        # Classroom seat in Iris POV: Seat 7-F
        old_seat_pattern = r'Lucen Gray did not sit in the back\..*?partially block the sightline from the door\.'
        new_seat_block = (
            "Lucen Gray sat in Seat 7-F — the dead corner by the far right window, furthest from the lectern, "
            "partially obscured by the morning glare and a stone pillar. To an amateur instructor, it looked like a normal "
            "lazy student hiding in the back. But Iris knew academy architecture. Seat 7-F was the one desk in the entire "
            "lecture hall that fell outside every standard surveillance ward's focal radius."
        )
        new_text = re.sub(old_seat_pattern, new_seat_block, text, flags=re.DOTALL)
        if new_text != text:
            text = new_text
            modified = True

    elif ch_num == 7:
        # Lucas already enrolled fix in journal
        text = re.sub(
            r'If the game\'s story proceeded on rails, he\'d arrive at the academy in week two.*?"I just have to survive until Lucas shows up," I reasoned',
            'Lucas Ashford was already here. Enrolled early on Day 2, sitting right in Homeroom 1-A. The timeline had accelerated. "I just have to keep my head down until his protagonist gravity activates," I reasoned',
            text, flags=re.DOTALL
        )
        text = re.sub(
            r'Lucas arrives in two weeks\. Male lead gravity\. Everything rebalances\. I just have to keep my head down for fourteen days\.\.\.',
            'Lucas is already on campus. His protagonist gravity is starting to warp the narrative. I just have to survive until the First Evaluation Exam without getting caught in his wake...',
            text
        )
        text = re.sub(
            r'1\. Thirteen days until Lucas Ashford enrolls\. When the male lead\'s gravity arrives, everything normalizes\. Survive until then\.',
            '1. Lucas Ashford has enrolled early. The canon timeline is compressed. Avoid his line of sight and let him trigger his own quest flags.',
            text
        )
        modified = True

    elif ch_num == 8:
        # Fix scramble narrator lines about Lucas
        old_scramble = r"['\"]Thirteen days,['\"] I reminded myself, watching him\..*?That was the whole—['\"]"
        new_scramble = (
            "'Lucas is already here,' I reminded myself, watching him from across the yard. "
            "'The protagonist enrolled early, the timeline is accelerated, and every bad ending trigger is on a hair-trigger.'"
        )
        text = re.sub(old_scramble, new_scramble, text, flags=re.DOTALL)
        # Roland Huxley -> Derrick Holt
        text = re.sub(r'Roland Huxley\'s crowd', "Derrick Holt's crowd", text)
        text = re.sub(r'Roland had gone', "Derrick had gone", text)
        # demon-boar disaster -> homeroom stare
        text = re.sub(r'at the demon-boar disaster, that guilt-recognition look',
                      'in homeroom on Day 2, that strange guilt-recognition look', text)
        modified = True

    elif ch_num == 9:
        # Holt vs Rhea sparring correction
        old_spar = r'Instructor Vance matched me against Rhea Valtor\.'
        new_spar = (
            "Instructor Vance matched me against Erik Holt, but I had to use the Ashenveil Retreat to keep my ribs intact—"
            "and Rhea Valtor was watching from the bleachers. She recognized the footwork."
        )
        new_text = re.sub(old_spar, new_spar, text)
        if new_text != text:
            text = new_text
            modified = True

    elif ch_num == 10:
        # Mr. Thorne reference removal
        text = re.sub(r'Mr\. Thorne\'s crimson eyes still live in my dreams rent-free\.',
                      'The memory of that roadside cleaver still lives in my dreams rent-free.', text)
        # missing noun typo: "the could kill" -> "the bread roll could kill"
        text = re.sub(r'the could kill a man if thrown', 'the bread roll could kill a man if thrown', text)
        modified = True

    elif ch_num == 11:
        # Typo: "It had a name,* *I said." -> "It had a name," I said.
        text = re.sub(r'"It had a name,\*\s*\*I said\."', '"It had a name," I said.', text)
        modified = True

    return text, modified

def update_db(ch_num, title, content):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    full_title = f"Chapter {ch_num}: {title}"
    cursor.execute(
        "UPDATE card SET title=?, content=?, last_modified_by='bible_canon_aligner' WHERE project_id=6 AND card_type_id=12 AND display_order=?",
        (full_title, json.dumps(content, ensure_ascii=False), ch_num)
    )
    conn.commit()
    conn.close()

def main():
    print("=" * 70)
    print("SURGICAL BIBLE CANON ALIGNMENT (CHAPTERS 1 TO 14)")
    print("=" * 70)

    # Titles from Volume 1 package
    titles = {
        1: "The Worst Possible Save File",
        2: "Stats of a Practice Dummy",
        3: "Enrollment by Clerical Error",
        4: "Seat 7-F, Right Side, Window",
        5: "The Protagonist Does Protagonist Things",
        6: "The Princess Asks a Question",
        7: "The Evil Goddess Has Opinions",
        8: "Lucas Ashford Is Annoyingly Perfect",
        9: "The Notebook Incident",
        10: "Week One Survival Report",
        11: "Decryption Session #1: The Art of Lying to a Dragon",
        12: "The First Evaluation Exam (Announcement)",
        13: "Dawn Sparring with the Ashen Sword",
        14: "Decryption Session #2: Controlled Detonation",
    }

    modified_count = 0
    for ch_num in range(1, 15):
        ch_file = os.path.join(CHAPTERS_DIR, f"Chapter_{ch_num:03d}.md")
        if not os.path.exists(ch_file):
            continue

        with open(ch_file, "r", encoding="utf-8") as f:
            content = f.read()

        cleaned, was_modified = clean_chapter_text(ch_num, content)
        if was_modified:
            with open(ch_file, "w", encoding="utf-8") as f:
                f.write(cleaned)
            title = titles.get(ch_num, f"Chapter {ch_num}")
            update_db(ch_num, title, cleaned)
            print(f"  [✓] Aligned and updated Chapter {ch_num:02d}: {title}")
            modified_count += 1
        else:
            print(f"  [-] Chapter {ch_num:02d} already in canon compliance.")

    print(f"\nDone! Aligned {modified_count} chapters to 00_Locked_Canon_Registry.json.")

if __name__ == "__main__":
    main()
