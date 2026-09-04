#!/usr/bin/env python3
"""
build_complete_bible.py
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

SYSTEM_PROMPT = """You are a master Korean webnovel narrative architect and worldbuilding genius specializing in top-ranking Novelpia and Munpia pure-love fantasy and rom-fan academy survival novels (like 'How to Survive in the Romance Fantasy Game' and 'I Became the Academy's Blind Prodigy').
Your output must be exhaustive, publication-grade, and deeply engaging. Include concrete lore, specific names, tactical mechanics, dialogue snippets, and precise emotional progression.
DO NOT use vague summaries, lazy placeholders, or generic tropes. Output directly in clean Markdown format (or JSON where specified)."""

def ask_gpt(prompt: str, max_retries: int = 3) -> str:
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt}
    ]
    for attempt in range(1, max_retries + 1):
        try:
            print(f"    [Calling GPT-5.6 Sol (Attempt {attempt}/{max_retries})...]")
            start_t = time.time()
            res = send_chat_completion(
                messages=messages,
                model=MODEL_NAME,
                stream=False,
                log_stream=False,
                timeout=600
            )
            content = res.get("content", "").strip()
            elapsed = time.time() - start_t
            if content and len(content) > 200:
                print(f"    [Received {len(content)} chars in {elapsed:.1f}s]")
                return content
            else:
                print(f"    [Warning: Empty or short response ({len(content)} chars), retrying...]")
                time.sleep(3)
        except Exception as e:
            print(f"    [Error calling GPT-5.6 Sol: {e}]")
            time.sleep(5)
    raise RuntimeError(f"Failed to generate after {max_retries} attempts.")

def save_file(filename: str, content: str) -> str:
    path = os.path.join(BIBLE_DIR, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] Saved file: {filename} ({len(content)} chars)")
    return path

def is_file_present(filename: str, min_chars: int = 500) -> bool:
    path = os.path.join(BIBLE_DIR, filename)
    if os.path.exists(path) and os.path.getsize(path) >= min_chars:
        return True
    return False

def read_file(filename: str) -> str:
    path = os.path.join(BIBLE_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

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
                card_type_id = ?
            WHERE id = ?
        """, (content, display_order, MODEL_NAME, card_type_id, row[0]))
    else:
        c.execute("""
            INSERT INTO card (title, card_type_id, project_id, content, display_order, model_name, created_at, ai_modified, needs_confirmation)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
        """, (title, card_type_id, PROJECT_ID, content, display_order, MODEL_NAME, now))
    conn.commit()
    conn.close()
    print(f"  [✓] DB Card Synced: '{title}' (Type {card_type_id}, Order {display_order})")

def step_1_work_tags():
    print("\n" + "="*60)
    print("[1/12] 01_Work_Tags.json")
    print("="*60)
    filename = "01_Work_Tags.json"
    if is_file_present(filename, min_chars=100):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
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
        content = json.dumps(work_tags, indent=2, ensure_ascii=False)
        save_file(filename, content)
    upsert_card("Work Tags", 2, content, 1)

def step_2_one_sentence_summary():
    print("\n" + "="*60)
    print("[2/12] 02_One_Sentence_Summary.md")
    print("="*60)
    filename = "02_One_Sentence_Summary.md"
    if is_file_present(filename, min_chars=500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        content = """# One Sentence Summary & Narrative Promise

## Core Logline
> A legendary challenge runner awakens as Lucen Vale, an irrelevant minor noble fated to be mauled to death in the academy tutorial of the nightmare-difficulty game *Crown of Amaranth*, and uses perfect speedrunner route knowledge to desperately avoid every heroine—only for his cold-sweat evasions to convince four living calamities that he alone understands their hearts.

## Core Elevator Pitch
- **Target Experience**: The pure addictive rush and comedic survival panic of *How to Survive in the Romance Fantasy Game* combined with high-stakes academy rankings and dungeon raids.
- **The Central Misunderstanding**: Internally, Lucen is sweating bullets, cursing ruthless game developers, and measuring exit distances like a speedrunner dodging hitbox frames. Externally, his unyielding boundaries, deadpan composure, and uncanny foreknowledge look like the calm detachment of an invincible gigachad.
- **The Romance Promise**: Slow-burn, pure-love devotion where four terrifyingly powerful heroines (an ancient dragon duchess, a frost-cursed imperial princess, a corrupted saintess, and a battle-hungry sword prodigy) become irrevocably obsessed with him, while 20% 3rd-person interludes reveal their blushing private embarrassment and failed seduction schemes.
- **The Macro Promise**: A complete, satisfying 8-volume journey (452 chapters) that conquers the game's system, breaks every bad ending, and establishes a genuine, permanent happily-ever-after.

## The Comedic Engine: The Speedrunner's Dilemma
1. **Survival Reflex**: Lucen acts solely to avoid triggering lethal flags, death cutscenes, or aggroing walking raid bosses.
2. **Gigachad Projection**: The heroines, accustomed to sycophants, power-mongers, and arranged suitors, view his evasions as extraordinary moral integrity, unmatched chivalric discretion, and profound emotional empathy.
3. **Escalation**: Every time he successfully dodges a death flag, he inadvertently plants an unshakeable obsession flag.
"""
        save_file(filename, content)
    upsert_card("One Sentence Summary", 4, content, 2)

def step_3_worldview_setting():
    print("\n" + "="*60)
    print("[3/12] 03_Worldview_Setting.md via GPT-5.6 Sol")
    print("="*60)
    filename = "03_Worldview_Setting.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive, exhaustive Worldview Setting document (03_Worldview_Setting.md) for our webnovel:
Title: 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'
Setting: Asterion Imperial Academy and the empire of Sol-Rhea in the brutal otome RPG 'Crown of Amaranth: A Romance Written in Blood' (Nightmare Crown difficulty).

Provide full, exhaustive detail on:
1. THE GAME CONTEXT:
   - 'Crown of Amaranth: A Romance Written in Blood' core systems.
   - Nightmare Crown difficulty rules: hidden affection thresholds, autonomous NPC emotional escalation, boss scaling when canonical routes break, the Prism Cube ranking tests, relic labyrinths.
   - The 'Glitched Interface': Why Lucen's status window shows question marks for heroine affection ('???') while flashing crimson danger warnings for bad endings.
2. ASTERION IMPERIAL ACADEMY:
   - Campus architecture, the Class tier system (Crown Class Ranks 1-10, Gold Class Ranks 11-50, Silver Class Ranks 51-150, Extra/Wood Class Ranks 151+).
   - Monthly Prism Cube evaluations, the Labyrinth Midterms, combat arena layouts, and the strict disciplinary codes.
3. MAGIC, AURA & MANA MECHANICS:
   - The 6 Aspect Affinities (Solar Dawn, Deep Abyss, Verdant Gale, Terra Core, Azure Frost, Draconic Origin).
   - Draconic High-Density Mana (dense physical heat that melts normal spells).
   - Frozen Stasis Soul Magic (Princess Eirwen's curse of permanent hypothermia).
   - Sentient Divine Miasma (Saint Mirielle's parasitic corruption).
   - Sword Star Horizon Aura (Kaela's world-cleaving edge).
   - Lucen's non-standard stat build: Low mana/strength, maxed perception/agility, enabling hitbox/seam frame dodging.
4. EMPIRE POLITICS & MAJOR FACTIONS:
   - The Solgrave Imperial Dynasty (the paranoid Emperor and ironclad throne succession).
   - The Draconic Autonomous Peaks (House Veyr, ancient dragon shifters treating human politics as trivial).
   - The Church of the Radiant Dawn & Inquisitorial Order (theocracy with dark purges).
   - The Northern Marcher Duchies (House Routh, frontline demon slayers).
   - The shadowy 'Narrative Weavers' cult (fanatics seeking to force the scripted game apocalypse).
5. THE DANGER MATRIX:
   - Why each of the 4 heroines is considered a walking natural disaster if her emotional route crashes (Ashen Cataclysm, Absolute Zero Frost Age, Eclipse of the Saint, Blood Slaughter Field).
   - How Lucen's foreknowledge tracks their lethal bad endings and how his evasive maneuvers prevent global extinction."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Worldview Setting", 6, content, 3)

def step_4_core_blueprint():
    print("\n" + "="*60)
    print("[4/12] 04_Core_Blueprint.md via GPT-5.6 Sol")
    print("="*60)
    filename = "04_Core_Blueprint.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the Core Blueprint document (04_Core_Blueprint.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Focus on:
1. THE NARRATIVE DUAL-ENGINE:
   - Detailed dissection of Lucen's terrified speedrunner internal monologue (cynical gamer jargon, calculating hitbox frames, swearing at developer malice, measuring exit doors) vs. his external projection (unflinching aristocratic calm, deadpan chivalry, mysterious omniscient composure).
2. COMEDY & MISUNDERSTANDING ARCHITECTURE:
   - Concrete mechanics of how his survival tactics trigger romantic flags:
     a) Refusing gifts (afraid of dragon mating vows / imperial bribes) -> interpreted as untainted pure chivalry.
     b) Avoiding eye contact (avoiding charm/curse gaze triggers) -> interpreted as profound reverent respect and shyness.
     c) Carrying emergency chalk, flash stones, and smoke bombs -> interpreted as master tactical preparation to defend his loved ones.
     d) Speaking in terse, urgent tactical instructions -> interpreted as absolute unwavering confidence in crisis.
3. PURE-LOVE ROMANCE DYNAMICS & BOUNDARIES:
   - How each heroine's obsession develops from self-interested fixation into genuine, deep, selfless devotion.
   - Maintaining Lucen's agency: He is never a passive doormat or weakling; he establishes firm boundaries, calls out crazy behavior, and forces the heroines to grow emotionally.
4. PACING & SERIAL WEBNOVEL HOOKS:
   - The rhythmic cadence of chapters: High-octane survival tension or academic exams -> comedic misunderstanding climax -> intimate interlude -> cliffhanger hook.
   - Balancing the 80% Male 1st POV with 20% Female 3rd-Person Limited Interludes for maximum emotional payoff."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Core Blueprint", 7, content, 4)

def step_5_story_outline():
    print("\n" + "="*60)
    print("[5/12] 05_Story_Outline.md via GPT-5.6 Sol")
    print("="*60)
    filename = "05_Story_Outline.md"
    if is_file_present(filename, min_chars=2000):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive Story Outline document (05_Story_Outline.md) for the complete 8-Volume (452 Chapters) Macro Arc of:
'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'

For EACH of the 8 Volumes, provide:
- Volume Number, Title, Chapter Range (e.g. Vol 1: Ch 1-52).
- Core Theme & Central Conflict.
- Opening Setup (Initial status, Lucen's tactical objective).
- Mid-Volume Escalation (The unexpected crisis or system glitch).
- Climax & Boss Encounter (Academy exam, dungeon raid, or faction confrontation).
- Heroine Development Milestones (How Lysandra, Eirwen, Mirielle, and Kaela evolve).
- Volume Ending Serial Hook (Massive cliffhanger setting up the next volume).

The 8 Volumes are:
- Vol 1: The Extra Who Refused the Tutorial Death (Ch 1–52) — Tutorial evasion, Prism Cube ranking exam, accidental Rank 12, the Dragon Scale refusal, the Frost stabilization.
- Vol 2: Please Stop Calling My Panic a Master Plan (Ch 53–108) — Labyrinth Midterm exam, map seam exploits, Saintess Mirielle's arrival, Crown Class squad leadership.
- Vol 3: Four Routes Are Not a Harem; They Are a Disaster (Ch 109–166) — Imperial Founding Festival, masquerade ball, political duels, terrorist infiltration, Emperor's proposal vs Dragon's wrath.
- Vol 4: The Dragon's Proposal Is a Raid Boss (Ch 167–224) — Draconic Covenant Expedition to airborne ruins, preventing the apocalyptic Dragon Bride ending, equal covenant pact.
- Vol 5: The Saintess Read the Hidden Patch Notes (Ch 225–282) — Holy City infiltration, stopping the Saint's Ascension brainwashing ritual, Lucen confesses his foreknowledge.
- Vol 6: The Protagonist of the Dead Timelines (Ch 283–340) — The return of canonical regressor Cassian Roel, academy civil war, heroines reject destined fate for Lucen.
- Vol 7: Speedrunning the End of the World (Ch 341–398) — System collapse, world reset graveyard, rejecting the trolley-problem sacrifice, dismantling administrator privilege.
- Vol 8: A Romance Fantasy Without Bad Endings (Ch 399–452) — Final system raid, saving Cassian, rewriting autonomous world laws, multi-heroine domestic resolution, 5-year post-graduation epilogue."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Story Outline", 5, content, 5)

def step_6_special_ability():
    print("\n" + "="*60)
    print("[6/12] 06_Special_Ability.json")
    print("="*60)
    filename = "06_Special_Ability.json"
    if is_file_present(filename, min_chars=500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        special_ability = {
            "protagonist_abilities": {
                "innate_interface": {
                    "name": "Challenge Runner's HUD (Fragmented)",
                    "description": "A semi-translucent gamer interface retained from Kang Min-jun's 10,000+ hours in Crown of Amaranth.",
                    "features": [
                        {
                            "name": "Hitbox & Seam Perception",
                            "effect": "Visualizes collision frames, attack trajectories, and spell cast-times 0.2 seconds before impact, allowing no-damage dodging with minimal physical stats."
                        },
                        {
                            "name": "Route Compass (Corrupted)",
                            "effect": "Flashes crimson warning markers when an action risks triggering an irreversible bad ending. Heroine affection levels are permanently glitched as '???', preventing easy metagaming."
                        },
                        {
                            "name": "Tactical Item Synthesis",
                            "effect": "Enables combining mundane herbs, chalk, and dungeon gravel into speedrunner utility consumables (Stun Dust, Flash Stones, Scent Nullifiers, Anti-Dragon Drake-Bane Powder)."
                        }
                    ]
                },
                "base_stats": {
                    "strength": "D- (Frail minor noble baseline; struggles in direct weapon clashes)",
                    "agility": "B+ (Speedrunner dodge-roll reflexes, frame cancels, sprint endurance)",
                    "mana_capacity": "E+ (Insignificant; relies on external disposable mana cartridges)",
                    "perception_sense": "S (Maxed visual/auditory observation of enemy tells and micro-expressions)",
                    "mental_fortitude": "EX (Forged through hundreds of brutal permadeath wipeouts; immune to panic)"
                },
                "signature_tactics": [
                    "Frame-Skip Dodge: Canceling momentum against terrain to evade fatal broad-area slashes",
                    "Disruption Line: Drawing enchanted chalk lines on the ground to short-circuit ritual mana circles",
                    "Pocket Alchemy: Throwing blinding flash powder directly into enemy casting focus"
                ]
            },
            "academy_evaluation_system": {
                "device": "Prism Cube (33x33 enchanted resonance matrix)",
                "evaluation_pillars": [
                    {"name": "Destructive Output", "metric": "Raw physical/magical kinetic yield"},
                    {"name": "Mana Efficiency", "metric": "Resonance purity and flow control"},
                    {"name": "Combat Reflexes", "metric": "Reaction time against illusory beast assaults"},
                    {"name": "Theoretical Strategy", "metric": "Solving tactical battlefield dilemma projections"}
                ],
                "class_ranks": [
                    {"rank_name": "Crown Class", "tier": "Ranks 1–10", "privileges": "Private quarters, unlimited library clearance, squad leadership"},
                    {"rank_name": "Gold Class", "tier": "Ranks 11–50", "privileges": "Advanced training grounds, priority dungeon slots"},
                    {"rank_name": "Silver Class", "tier": "Ranks 51–150", "privileges": "Standard dormitories, general curriculum"},
                    {"rank_name": "Extra/Wood Class", "tier": "Ranks 151+", "privileges": "Outskirts annex, basic supplies, target practice fodder"}
                ]
            }
        }
        content = json.dumps(special_ability, indent=2, ensure_ascii=False)
        save_file(filename, content)
    upsert_card("Special Ability: Speedrunner HUD & Prism Cube Systems", 3, content, 6)

def step_7_voice_ledger():
    print("\n" + "="*60)
    print("[7/12] 07_Voice_Ledger.md via GPT-5.6 Sol")
    print("="*60)
    filename = "07_Voice_Ledger.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the exhaustive Voice Ledger & Style Guide document (07_Voice_Ledger.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Must cover:
1. THE 80/20 POV GOLDEN RATIO:
   - 80% Male 1st-Person POV (Lucen Vale): Fast-paced, cynical, gamer-adjacent tactical reasoning. He internally complains about sadistic developers, analyzes monster hitboxes, and curses his terrible luck, while maintaining a deadpan, polite, aristocratic facade.
   - 20% Female 3rd-Person Limited Interludes: Focalized strictly through one heroine at a time (Lysandra, Eirwen, Mirielle, or Kaela). Reveals their blushing private thoughts, obsessive rationalizations, jealousy, and awkward failed seduction plans.
   - Strict rule: NO head-hopping within the same scene. Interludes must be clearly designated section breaks.
2. PROSE CADENCE & FORMATTING:
   - Mobile-optimized Korean webnovel rhythm: 1–3 sentence paragraphs.
   - Punchy dialogue, sharp internal monologue, and immediate sensory cues (smell of ozone, the chill of frost mana, the heavy rattle of scales).
   - Generous use of line breaks for comedic timing and dramatic tension.
3. BANNED AI CLICHÉS & FORBIDDEN EXPRESSIONS:
   - Complete list of banned phrases: 'a testament to', 'couldn't help but', 'shivers down the spine', 'smirked', 'little did he know', 'a dance of death', 'tapestry of', 'like a moth to a flame'.
   - Rules against melodramatic purple prose; replace with grounded, physical actions and snappy internal reactions.
4. SIGNATURE CHARACTER DIALOGUE & MONOLOGUE PROFILES:
   - Lucen Vale: Terse, practical, respectful to superiors to avoid death flags, internally chaotic.
   - Duchess Lysandra Veyr: Imperious, possessive, treats mortals like insects, but stutters and flares her dragon scales when Lucen treats her like a normal human.
   - Princess Eirwen Solgrave: Monotone, regal, military diction; internally screaming like an embarrassed teenage girl who memorized dating advice from forbidden romance novels.
   - Saint Mirielle Aster: Serene theological cadence that subtly twists into unhinged devotional obsession; treating Lucen's safety as divine dogma.
   - Kaela Routh: Direct, blunt, aggressive sword metaphors; equates romantic confession to a death match."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Voice Ledger & Style Guide", 9, content, 7)

def step_8_character_matrix():
    print("\n" + "="*60)
    print("[8/12] 08_Character_Matrix.md via GPT-5.6 Sol")
    print("="*60)
    filename = "08_Character_Matrix.md"
    if is_file_present(filename, min_chars=2000):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive Character Matrix document (08_Character_Matrix.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Provide complete, granular dossiers for:
1. PROTAGONIST:
   - Lucen Vale (Kang Min-jun, age 16/27): Appearance, background as the 3rd son of an impoverished frontier barony, gamer psychology, survival hierarchy, combat style (hitbox evasion, consumable alchemy), core emotional wound.
2. THE FOUR CALAMITY HEROINES:
   a) Duchess Lysandra Veyr (The Ashen Dragon Duchess):
      - Age: 300+ (looks 17). Hair: Molten silver with iridescent red undertones. Eyes: Slitted golden dragon irises.
      - Status: Sovereign Heiress of the Dragon Peaks; disguised student in Asterion Academy.
      - Power: Draconic Origin Mana, partial scale manifestation, cataclysmic breath.
      - Obsession Trait: Extreme territorial hoarding. Treats Lucen's belongings as her dragon hoard; stalks his dorm roof; threatens rival suitors.
      - Weakness: Craves being seen as a person rather than an apocalyptic weapon.
   b) Princess Eirwen Solgrave (The Winter Crown):
      - Age: 17. Hair: Crystalline platinum-white. Eyes: Glacial ice-blue.
      - Status: Second Princess of Sol-Rhea Empire; Supreme Commander of the Imperial Frost Guard.
      - Power: Azure Frost Mana, Absolute Zero Stasis.
      - Obsession Trait: Rigid, awkward, by-the-book stalking. Uses imperial decrees to force joint assignments; reads terrible romance handbooks.
      - Weakness: Chronic mana backlash that freezes her own heart without Lucen's stabilization.
   c) Saint Mirielle Aster (The Hollow Halo):
      - Age: 17. Hair: Pale ash-blonde braided in holy silk. Eyes: Violet with faint crimson corruption rings.
      - Status: First Saintess of the Radiant Dawn Church.
      - Power: Divine Solar Healing intertwined with sentient Devouring Shadow miasma.
      - Obsession Trait: Heretical worship. Sanctifies Lucen as her 'True God'; rationalizes kidnapping rivals as 'spiritual cleansing'.
      - Weakness: Terrified that people only love her immaculate saint facade.
   d) Kaela Routh (The Sword Star of the North):
      - Age: 16. Hair: Raven-black cropped short. Eyes: Sharp amber hawk eyes.
      - Status: Sole Heiress of the Marcher Duchy of Routh.
      - Power: Horizon Sword Aura, Instantaneous Flash Step.
      - Obsession Trait: Combat yandere. Challenges anyone who insults him; interprets Lucen's dodge-rolls as profound martial poetry; blushes while sharpening blades.
      - Weakness: Crippling fear of fighting alone against the northern demon hordes.
3. KEY SUPPORTING CAST:
   - Headmaster Gabriel Vane (Ancient archmage seeking stability).
   - Instructor Kyle (Gruff combat evaluator who thinks Lucen is hiding grandmaster cultivation).
   - Deacon Valas (Secret operative of the Narrative Weavers cult).
   - Julian Vance (Arrogant second-year noble who repeatedly tries to harass Lucen and gets terrified by the heroines)."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Character Matrix", 14, content, 8)

def step_9_intimacy_matrix():
    print("\n" + "="*60)
    print("[9/12] 09_Intimacy_Matrix.md via GPT-5.6 Sol")
    print("="*60)
    filename = "09_Intimacy_Matrix.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive Intimacy Matrix & Relationship Progression document (09_Intimacy_Matrix.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Must detail:
1. THE 5 STAGES OF AFFECTION PROGRESSION:
   - Stage 1: Intense Curiosity & Suspicion (Vol 1, Ch 1-52) — Heroines think he's hiding a massive agenda; Lucen is terrified of triggering death flags.
   - Stage 2: Secret Indebtedness & Emotional Reliance (Vol 2-3, Ch 53-166) — Lucen solves their fatal flaws under the guise of saving his own skin; heroines experience genuine safety for the first time.
   - Stage 3: Territorial Possession & Open Jealousy (Vol 4-5, Ch 167-282) — Active romantic competition; private interlude schemes; public protection wars.
   - Stage 4: Vulnerable Truth & Mutual De-escalation (Vol 6-7, Ch 283-398) — Lucen admits his terror and reveals the game loop; heroines vow to break the world system for his sake.
   - Stage 5: Unbreakable Bond & Negotiated Multi-Partner Resolution (Vol 8, Ch 399-452) — Domestic peace, mutual acceptance, and true happily-ever-after.
2. PHYSICAL CONTACT & BOUNDARY MATRIX:
   - Define exact physical milestones for each heroine: Eye contact, handholding, scale brushing (for Lysandra), mana warmth sharing (for Eirwen), confessional touches (for Mirielle), sword grip sharing (for Kaela), first kiss, and private sanctuary boundaries.
3. COMEDIC MISUNDERSTANDING DYNAMICS:
   - Concrete examples of how Lucen's evasions spark romantic escalation.
   - The Inter-Heroine Standoffs: Dragon territory vs Imperial guard checkpoints vs Holy sanctums vs Sword challenges outside Lucen's bedroom.
4. PURE-LOVE CONVICTION:
   - Why this is NOT shallow harem slop: Every heroine's devotion is built on mutual respect, deep emotional healing, and unshakeable loyalty."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Intimacy Matrix & Relationship Progression", 18, content, 9)

def step_10_foreshadowing_registry():
    print("\n" + "="*60)
    print("[10/12] 10_Foreshadowing_Registry.md via GPT-5.6 Sol")
    print("="*60)
    filename = "10_Foreshadowing_Registry.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the exhaustive Foreshadowing Registry document (10_Foreshadowing_Registry.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Track at least 10 major setups and payoffs across all 8 Volumes (Ch 1–452):
1. The Tutorial Corpse Anomaly (Why Kang Min-jun woke up in Lucen's body).
2. The Glitched Crimson Compass (Why heroine affection shows '???' and what happens when it unlocks).
3. Duchess Lysandra's Severed Dragon Heart Scale (The ancient covenant vow and the dragon king's missing eye).
4. The Imperial Solgrave Frost Curse (The secret frozen throne beneath the palace).
5. The Entity in Saint Mirielle's Shadow (The true origin of the parasitic corruption).
6. Kaela's Northern Border Prophecy (The impending demonic breach).
7. The Transmigrator's Administrator Credential (`KANG_01` found in ancient ruins).
8. The Original Protagonist Cassian Roel's 10,000 Failed Loops (Why the game's canonical hero became a broken regressor).
9. The World Reset Protocol and the Four Anchor Tokens (How Lucen and the heroines survive reality erasure).
10. The System Core's True Nature (The autonomous game AI trying to enforce tragedy).

For each item, specify:
- Clue Name & Nature.
- Chapter & Volume of Initial Setup.
- Escalation Beats across the middle volumes.
- Grand Climax & Resolution Chapter.
- Narrative & Emotional Impact."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Foreshadowing Registry", 18, content, 10)

def step_11_master_timeline():
    print("\n" + "="*60)
    print("[11/12] 11_Master_Timeline.md via GPT-5.6 Sol")
    print("="*60)
    filename = "11_Master_Timeline.md"
    if is_file_present(filename, min_chars=1500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive Master Timeline document (11_Master_Timeline.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.

Construct the complete chronological timeline covering:
1. PRE-CANON & AWAKENING (Day -30 to Day 0): Kang Min-jun's transmigration, Lucen Vale's arrival at Asterion Academy.
2. ACADEMY YEAR 1 (Days 1–365, Vol 1–3):
   - Month 1: Entrance Ceremony, Tutorial Death Evasion, First Prism Cube Ranking Exam.
   - Month 2-3: Formation of Class Tiers, The Cold Tea Incident, Kaela's Duel Challenge.
   - Month 4-6: Midterm Labyrinth Dungeon Examination, Saint Mirielle's Inquisitorial Arrival.
   - Month 7-9: Summer Vacation / Northern Border & Dragon Peaks Expeditions.
   - Month 10-12: Imperial Founding Festival, Masquerade Ball, Siege of Lumina, First Year Finals.
3. ACADEMY YEAR 2 (Days 366–730, Vol 4–5):
   - Draconic Covenant Crisis, Holy City Ascension Infiltration, the Truth of the System.
4. ACADEMY YEAR 3 & THE WAR OF RESET (Days 731–1095, Vol 6–7):
   - Arrival of Regressor Cassian Roel, Academy Civil War, Reality Collapse, The Graveyard of Timelines.
5. THE FINAL RAID & POST-WAR ERA (Vol 8, Days 1096–1150 + 5-Year Time Skip):
   - System Core Raid, Autonomous World Genesis, Graduation, and Year 5 Epilogue.

Format with clear tables, dates, seasons, academic terms, and corresponding chapter numbers."""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Master Timeline", 18, content, 11)

def step_12_volume_01_execution_package():
    print("\n" + "="*60)
    print("[12/12] 12_Volume_01_Execution_Package.md via GPT-5.6 Sol")
    print("="*60)
    filename = "12_Volume_01_Execution_Package.md"
    if is_file_present(filename, min_chars=2500):
        content = read_file(filename)
        print(f"  [i] Already exists ({len(content)} chars).")
    else:
        prompt = """Write the comprehensive Volume 1 Execution Package (12_Volume_01_Execution_Package.md) for 'I Maxed Survival, So Why Are the Final Boss Heroines Chasing Me?'.
Volume 1 Title: 'The Extra Who Refused the Tutorial Death' (Chapters 1–52).

Provide a scene-by-scene structural blueprint for all 52 chapters, grouped into 5 Stages:
- Stage 1: Chapters 1–10 (The Tutorial Corpse & The Scale Refusal — Days 1–5)
- Stage 2: Chapters 11–20 (The Prism Cube Ranking Exam & Accidental Rank 12 — Days 6–12)
- Stage 3: Chapters 21–30 (The Cold Tea Incident & Frost Mana Stabilization — Days 13–22)
- Stage 4: Chapters 31–40 (The Compulsory Duel & The Three-Hit Yield — Days 23–35)
- Stage 5: Chapters 41–52 (The Mock Dungeon Infiltration & The Crimson Alert — Days 36–45)

For EACH chapter (1 to 52), provide:
1. Chapter Number & Working Title.
2. Story Day & Setting.
3. Narrative POV: 80% Male 1st POV (Lucen's tactical panic / survival calculations) or designated 20% Heroine 3rd-Person Limited Interlude (blushing possessiveness / misunderstanding).
4. Dramatic Objective & Tactical Survival Move.
5. The Comedic Misunderstanding / Romance Flag planted.
6. Chapter Ending Cliffhanger Hook.

Ensure maximum webnovel addictive pacing, high tension, hilarious misunderstandings, and zero filler!"""
        content = ask_gpt(prompt)
        save_file(filename, content)
    upsert_card("Volume 01 Execution Package", 8, content, 12)

def main():
    print("="*70)
    print("LAUNCHING COMPLETE 12-FILE NOVEL BIBLE BUILDER")
    print("Project ID: 6 | Engine: GPT-5.6 Sol (Genspark Dual-Account Pool)")
    print("="*70)
    
    step_1_work_tags()
    step_2_one_sentence_summary()
    step_3_worldview_setting()
    step_4_core_blueprint()
    step_5_story_outline()
    step_6_special_ability()
    step_7_voice_ledger()
    step_8_character_matrix()
    step_9_intimacy_matrix()
    step_10_foreshadowing_registry()
    step_11_master_timeline()
    step_12_volume_01_execution_package()
    
    print("\n" + "="*70)
    print("🎉 ALL 12 NOVEL BIBLE DOCUMENTS SUCCESSFULLY CREATED & SYNCED TO DB!")
    print("="*70)

if __name__ == "__main__":
    main()
