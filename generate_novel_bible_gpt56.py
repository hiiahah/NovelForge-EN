#!/usr/bin/env python3
"""
generate_novel_bible_gpt56.py
Generates the complete, high-tier 12-file Novel Bible for:
'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
(살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다)
Powered by Genspark AI (gpt-5.6-sol) with dual-account pool rotation.
"""

import os
import sys
import json
import sqlite3
import time
from datetime import datetime

sys.path.insert(0, "/home/ubuntu/NovelForge-EN/backend")
from app.services.ai.providers.genspark_auth import send_chat_completion

DB_PATH = "/home/ubuntu/NovelForge-EN/backend/novelforge.db"
BIBLE_DIR = "/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/bible"
os.makedirs(BIBLE_DIR, exist_ok=True)

PROJECT_ID = 6
MODEL_NAME = "gpt-5.6-sol"

def ask_gpt(prompt: str, system_prompt: str = "You are a master Korean webnovel narrative architect and worldbuilding genius specializing in top-ranking Novelpia and Munpia pure-love fantasy and rom-fan academy survival novels. Output in thorough, exhaustive, publication-grade detail. No placeholders, no summaries, provide complete actionable lore and specifications.") -> str:
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt}
    ]
    res = send_chat_completion(
        messages=messages,
        model=MODEL_NAME,
        stream=False,
        log_stream=True,
        timeout=600
    )
    return res.get("content", "").strip()

def save_file(filename: str, content: str):
    path = os.path.join(BIBLE_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] Saved {filename} ({len(content)} chars)")
    return path

def upsert_card(title: str, card_type_id: int, content: str, display_order: int):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    # Check if card exists
    c.execute("SELECT id FROM card WHERE project_id = ? AND title = ?", (PROJECT_ID, title))
    row = c.fetchone()
    if row:
        c.execute("""
            UPDATE card SET
                content = ?,
                display_order = ?,
                model_name = ?,
                card_type_id = ?
            WHERE id = ?
        """, (content, display_order, MODEL_NAME, card_type_id, row[0]))
    else:
        c.execute("""
            INSERT INTO card (title, card_type_id, project_id, content, display_order, model_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (title, card_type_id, PROJECT_ID, content, display_order, MODEL_NAME, now))
    conn.commit()
    conn.close()
    print(f"  [✓] DB Card: {title} (Type {card_type_id}, Order {display_order})")

print("="*70)
print("STARTING NOVEL BIBLE SYNTHESIS: I MAXED SURVIVAL (GPT-5.6 SOL)")
print("="*70)

# 1. Work Tags
print("\n[1/12] 01_Work_Tags.json...")
work_tags = {
    "title": "I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?",
    "korean_title": "살아남으려고 발버둥 쳤을 뿐인데, 최종보스 히로인들이 집착한다",
    "genre": ["Romance Fantasy", "Academy", "Game Transmigration", "Survival Comedy", "Pure-Love Harem", "Misunderstanding", "Fantasy Action"],
    "target_demographic": "Male-oriented pure-love fantasy webnovel readers (Novelpia / Munpia top-tier style)",
    "narrative_pov": "80% Male 1st-Person POV / 20% Female 3rd-Person Limited Interludes",
    "tags": [
        "Challenge Runner MC", "Extra Reincarnation", "Yandere Heroines", "Dragon Shifter",
        "Ice Princess", "Fallen Saintess", "Sword Prodigy", "Gigachad Misunderstandings",
        "Tactical Panic", "Academy Rankings", "No-Damage Speedrun", "Slow-Burn Devotion",
        "Satisfying 450-Chapter Climax"
    ],
    "target_total_chapters": 452,
    "volume_count": 8
}
save_file("01_Work_Tags.json", json.dumps(work_tags, indent=2, ensure_ascii=False))
upsert_card("Work Tags", 2, json.dumps(work_tags, indent=2, ensure_ascii=False), 1)

# 2. One Sentence Summary
print("\n[2/12] 02_One_Sentence_Summary.md...")
summary_md = """# One Sentence Summary & Narrative Promise

## Core Logline
> A completionist challenge runner awakens as Lucen Vale, an irrelevant minor noble fated to be mauled to death in the academy tutorial of the nightmare-difficulty game *Crown of Amaranth*, and uses perfect route knowledge to avoid every heroine—only for his cold-sweat evasions to convince four living calamities that he alone understands their hearts.

## Core Elevator Pitch
- **Target Experience**: The pure addictive rush of *How to Survive in the Romance Fantasy Game* combined with high-stakes academy rankings and dungeon raids.
- **The Central Misunderstanding**: Internally, Lucen is sweating bullets, cursing game developers, and measuring exit distances like a speedrunner dodging hitbox frames. Externally, his unyielding boundaries, deadpan composure, and uncanny foreknowledge look like the calm detachment of an invincible gigachad.
- **The Romance Promise**: Slow-burn, pure-love devotion where four terrifyingly powerful heroines (an ancient dragon duchess, a frost-cursed imperial princess, a corrupted saintess, and a battle-hungry sword prodigy) become irrevocably obsessed with him, while 20% 3rd-person interludes reveal their blushing private embarrassment and failed seduction schemes.
- **The Macro Promise**: A complete, satisfying 8-volume journey (452 chapters) that conquers the game's system, breaks every bad ending, and establishes a genuine, permanent happily-ever-after.
"""
save_file("02_One_Sentence_Summary.md", summary_md)
upsert_card("One Sentence Summary", 4, summary_md, 2)

# 3. Worldview Setting
print("\n[3/12] Generating 03_Worldview_Setting.md via GPT-5.6 Sol...")
p_worldview = """Write the comprehensive, exhaustive Worldview Setting document (03_Worldview_Setting.md) for our webnovel:
Title: 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
Setting: Asterion Imperial Academy and the empire of Sol-Rhea in the brutal otome RPG 'Crown of Amaranth: A Romance Written in Blood' (Nightmare Crown difficulty).

Provide full, exhaustive detail on:
1. THE GAME CONTEXT: Crown of Amaranth mechanics, Nightmare Crown difficulty rules (hidden affection values, autonomous NPC escalation, boss scaling when canonical routes break, the Prism Cube ranking tests, relic labyrinths).
2. ASTERION IMPERIAL ACADEMY: Campus architecture, the Class tier system (Crown Class, Gold Class, Silver Class, Extra/Wood Class), the monthly Prism Cube evaluations, the Labyrinth Midterms, and disciplinary codes.
3. MAGIC, AURA & MANA MECHANICS: The 6 Aspect Affinities, Draconic High-Density Mana, Frozen Stasis Soul Magic, Sentient Divine Miasma (the Corruption), and Sword Star Horizon Aura. How Lucen's non-standard stat builds allow him to exploit hitbox/mana seam bugs.
4. EMPIRE POLITICS & MAJOR FACTIONS: The Solgrave Imperial Dynasty, The Draconic Autonomous Peaks (House Veyr), The Church of the Radiant Dawn & Inquisitorial Order, The Northern Marcher Duchies (House Routh), and the shadowy 'Narrative Weavers' cult.
5. THE DANGER MATRIX: Why each of the 4 heroines is considered a walking natural disaster if her emotional route crashes, and how Lucen's foreknowledge tracks their lethal bad endings."""
worldview_md = ask_gpt(p_worldview)
save_file("03_Worldview_Setting.md", worldview_md)
upsert_card("Worldview Setting", 6, worldview_md, 3)

# 4. Core Blueprint
print("\n[4/12] Generating 04_Core_Blueprint.md via GPT-5.6 Sol...")
p_blueprint = """Write the Core Blueprint document (04_Core_Blueprint.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.
Focus on:
1. THE NARRATIVE DUAL-ENGINE: The hilarious, tension-packed contrast between Lucen's terrified speedrunner internal monologue vs. the heroines' perception of him as an omniscient, unshakeable saint/gigachad.
2. COMEDY & MISUNDERSTANDING ARCHITECTURE: Concrete mechanics of how his survival tactics (refusing gifts, avoiding eye contact, carrying emergency chalk and teleport stones, speaking in terse tactical instructions) trigger romantic flags.
3. PURE-LOVE ROMANCE DYNAMICS: How each heroine's obsession develops from self-interested fixation into genuine, deep, selfless devotion. How boundaries are maintained, ensuring he never feels like a passive pushover.
4. PACING & SERIAL WEBNOVEL HOOKS: The rhythmic cadence of chapters (cliffhangers, ranking reveals, interlude placement, battle tension followed by cozy domestic comedy)."""
blueprint_md = ask_gpt(p_blueprint)
save_file("04_Core_Blueprint.md", blueprint_md)
upsert_card("Core Blueprint", 7, blueprint_md, 4)

# 5. Special Ability
print("\n[5/12] 06_Special_Ability.json...")
special_ability = {
    "protagonist_abilities": {
        "innate_interface": {
            "name": "Challenge Runner's HUD (Fragmented)",
            "description": "A semi-translucent gamer interface retained from Kang Min-jun's 10,000+ hours in Crown of Amaranth.",
            "features": [
                {
                    "name": "Hitbox & Seam Perception",
                    "effect": "Visualizes collision frames and spell cast-times 0.2 seconds before impact, allowing no-damage dodging with minimal physical stats."
                },
                {
                    "name": "Route Compass (Corrupted)",
                    "effect": "Flashes crimson warning markers when a decision risks triggering an irreversible bad ending. Does NOT show romance affection numbers (they appear as glitched question marks '???')."
                },
                {
                    "name": "Tactical Item Synthesis",
                    "effect": "Enables combining mundane alchemical herbs, chalk, and dungeon gravel into speedrunner utility consumables (Stun Dust, Flash Stones, Scent Nullifiers)."
                }
            ]
        },
        "base_stats": {
            "strength": "D- (Frail minor noble baseline)",
            "agility": "B+ (Trained dodge-roll reflexes and sprint endurance)",
            "mana_capacity": "E+ (Insignificant; relies on external mana stones)",
            "perception_sense": "S (Maxed visual/auditory observation of micro-expressions and casting signs)",
            "mental_fortitude": "EX (Trained through hundreds of brutal permadeath wipeouts)"
        }
    },
    "academy_evaluation_system": {
        "device": "Prism Cube (33x33 enchanted resonance matrix)",
        "metrics": ["Destructive Output", "Mana Efficiency", "Combat Reflexes", "Theoretical Strategy"],
        "class_ranks": ["Crown Class (Ranks 1-10)", "Gold Class (Ranks 11-50)", "Silver Class (Ranks 51-150)", "Extra/Wood Class (Ranks 151+)"]
    }
}
save_file("06_Special_Ability.json", json.dumps(special_ability, indent=2, ensure_ascii=False))
upsert_card("Special Ability: Speedrunner HUD & Prism Cube Systems", 3, json.dumps(special_ability, indent=2, ensure_ascii=False), 5)

print("\nStep 1-5 successfully completed!")
