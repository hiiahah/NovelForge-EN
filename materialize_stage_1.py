#!/usr/bin/env python3
"""
materialize_stage_1.py
Materializes Stage 1 (Chapters 1–10) and registers:
- 1 Stage Outline Card (Type 10)
- 10 Chapter Outline Cards (Type 11)
- 10 Chapter Text Cards (Type 12)
under Project ID 6 in novelforge.db.
"""

import sqlite3
import json
from datetime import datetime

DB_PATH = "/home/ubuntu/NovelForge-EN/backend/novelforge.db"
PROJECT_ID = 6
VOL_1_CARD_ID = 670

STAGE_1_DATA = {
    "stage_number": 1,
    "title": "Stage 1: The Tutorial Corpse & The Scale Refusal",
    "chapter_range": [1, 10],
    "timeline": "Days 1–5",
    "theme": "Evading the scripted tutorial death, invoking the ancient charter to refuse the Scale of Astra, and accidentally locking the interest of the Imperial Princess, Northern Heiress, and Sword Prodigy.",
    "chapters": [
        {
            "chapter_number": 1,
            "title": "Chapter 1: The Corpse Under the Scale Was Supposed to Be Me",
            "story_day": "Day 1, pre-dawn",
            "setting": "Astraea Academy underground calibration chamber",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Iris Aurelia", "Maintenance Crew"],
            "dramatic_objective": "Escape the scripted calibration spike murder before the game's opening tutorial cinematic begins.",
            "tactical_move": "Use Breath Partition to suppress heart rate, dislocate thumb to escape iron collar, substitute body with weighted mannequin, slip into drainage grate loosened with lamp oil.",
            "misunderstanding_beat": "Collides with First Princess Iris Aurelia half-dressed and bleeding in restricted corridor. Whispers 'Your Highness, please pretend you never saw me alive.' She interprets this as an imperial conspiracy warning.",
            "prohibited_knowledge": ["Lucen does not know Iris's frost/solar curse mechanics yet", "Readers do not know Assistant Professor Malrec is watching the drain"],
            "ending_hook": "A body drops behind the sealed chamber door, and Lucen hears his own voice whisper: 'You were supposed to stay dead.'"
        },
        {
            "chapter_number": 2,
            "title": "Chapter 2: Threat Ledger: One Voice That Should Not Exist",
            "story_day": "Day 1, dawn",
            "setting": "Academy service tunnels and eastern laundry",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Marta Bell", "Iris Aurelia (Indirect)"],
            "dramatic_objective": "Create a legally verifiable alibi for his presence before the morning entrance roll call.",
            "tactical_move": "Refuses to investigate the impossible phantom voice (rated 91% lethal), trades physical labor with laundry head Marta Bell for clean uniform and logged timestamp.",
            "misunderstanding_beat": "Iris sends a gold-sealed handkerchief to the laundry 'for the bleeding boy'. Supervisor Marta assumes Lucen spent his first morning seducing the imperial family.",
            "prohibited_knowledge": ["The true origin of the blackened prism shard embedded in Lucen's sleeve"],
            "ending_hook": "The prism shard burns against Lucen's palm, flashing: 'UNSCALED KEY CONFIRMED. SECOND MEASUREMENT REQUIRED.'"
        },
        {
            "chapter_number": 3,
            "title": "Chapter 3: The Entrance Ceremony Has a Casualty Quota",
            "story_day": "Day 1, morning",
            "setting": "Hall of Constellations",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Headmaster Gideon Arclight", "Iris Aurelia", "Assistant Professor Malrec Dain"],
            "dramatic_objective": "Remain in the public crowd while preventing Malrec Dain and the Ashen Choir from isolating him for execution.",
            "tactical_move": "Positions himself precisely between the infirmary door and a stone column blocking the Scale's line-of-sight.",
            "misunderstanding_beat": "Lucen repeatedly checks the emergency exit behind Iris's throne; Iris thinks he is signaling that her royal guard line is compromised and shifts aside to clear his line of sight.",
            "prohibited_knowledge": ["The exact corruption rate of the Scale of Astra"],
            "ending_hook": "The Scale of Astra resonates across the hall: 'Lucen Gray. Scholarship division. Step forward.'"
        },
        {
            "chapter_number": 4,
            "title": "Chapter 4: A Commoner's Right to Refuse Fate",
            "story_day": "Day 1, morning",
            "setting": "Hall of Constellations / Scale of Astra",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Instructor Orlan Vey", "Headmaster Arclight", "Iris Aurelia", "Malrec Dain"],
            "dramatic_objective": "Avoid touching the lethal measurement relic without getting expelled on the spot.",
            "tactical_move": "Invokes Founder Astraea's Charter, Article Seven: 'No free blood may be weighed without spoken consent,' legally taking Unscaled Probation.",
            "misunderstanding_beat": "When asked why he refuses, Lucen glances past Iris at Malrec and says: 'Because someone here already knows what the Scale intends to do to me.' The nobility believes he and the princess share an unspeakable secret.",
            "prohibited_knowledge": ["How the Scale detects transmigrated souls"],
            "ending_hook": "Without being touched, the massive Scale tilts violently toward Lucen and cracks down the center with a thunderous snap."
        },
        {
            "chapter_number": 5,
            "title": "Chapter 5: The Princess Counts Every Drop of Blood",
            "story_day": "Day 1, afternoon",
            "setting": "Imperial observation room",
            "pov": "Princess Iris Aurelia (3rd-person limited interlude)",
            "entities": ["Princess Iris Aurelia", "Tessa Roan (Maid)", "Lucen Gray (Observed)"],
            "dramatic_objective": "Investigate whether Lucen is an assassin, an imperial spy, or an innocent scapegoat while concealing her obsession from the Emperor's observers.",
            "tactical_move": "Reviews memory crystals frame-by-frame, observing that Lucen's eyes tracked escape routes rather than her face.",
            "misunderstanding_beat": "Keeps Lucen's bloodstained handkerchief for 'evidentiary preservation.' Her maid Tessa places it in the princess's velvet courtship casket, assuming it is a cherished token.",
            "prohibited_knowledge": ["Iris's secret Frost/Solar internal war"],
            "ending_hook": "The memory crystal reveals a missing frame where Malrec Dain smiles an instant before the Scale cracked."
        },
        {
            "chapter_number": 6,
            "title": "Chapter 6: Unscaled Probation Comes with a Bed Near the Morgue",
            "story_day": "Day 2, morning to evening",
            "setting": "Western Annex, Scholarship Dorm Room W-13",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Nell Quill (Roommate)", "Delivery Guards"],
            "dramatic_objective": "Fortify a deliberately isolated room located directly above the academy morgue.",
            "tactical_move": "Maps six escape vectors, places chalk dust barriers and hair-trigger thread alarms across window frame and floorboards.",
            "misunderstanding_beat": "An imperial delivery brings luxury mattresses, down quilts, and reinforced steel locks under Iris's crest. Lucen assumes hostile surveillance; Nell thinks the princess is furnishing a love nest.",
            "prohibited_knowledge": ["Why the morgue contains fresh identical cadavers"],
            "ending_hook": "Under Lucen's mattress rests an official morgue tag stamped with his own name and yesterday's date."
        },
        {
            "chapter_number": 7,
            "title": "Chapter 7: Never Follow the Bloody Footprints",
            "story_day": "Day 2, midnight",
            "setting": "Western annex condemned stairwell & morgue",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Masked Cultist", "Princess Iris Aurelia"],
            "dramatic_objective": "Recover altered morgue ledger records without engaging superior combatants.",
            "tactical_move": "Ignores the bloody bait trail, uses the laundry chute to flank the morgue from behind, vents chemical preservative fumes to flush out the intruder.",
            "misunderstanding_beat": "Iris investigates the disturbance; Lucen drags her behind a dissection cabinet to avoid detection. Faces inches apart in the dark, he whispers 'Please don't breathe.' She hears: 'Trust me with your life.'",
            "prohibited_knowledge": ["Celestia Rimehart's cursed frost timeline"],
            "ending_hook": "The stolen record strip lists three pre-planned deaths—including Northern Duchess Celestia Rimehart on Day 17."
        },
        {
            "chapter_number": 8,
            "title": "Chapter 8: The Girl Who Freezes the North Dies in Nine Days",
            "story_day": "Day 3, morning",
            "setting": "Introductory mana-control lecture hall",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Celestia Rimehart", "Instructor Kyle"],
            "dramatic_objective": "Verify whether the stolen death records are genuine murder plots without triggering Celestia's fatal frost barrier.",
            "tactical_move": "Detects an overloaded resonance crystal and kicks a copper ink tray to ground the electric surge, masking it as clumsy clumsiness.",
            "misunderstanding_beat": "Gives Celestia his seat by the radiator to protect himself from crystal shrapnel. In the Northern Duchy, offering one's warmed seat is an ancient formal declaration of courtship.",
            "prohibited_knowledge": ["The entity whispering inside Celestia's frost mana"],
            "ending_hook": "Under Celestia's desk, carved in frost: 'HE KNOWS HOW YOU DIE. ASK HIM.'"
        },
        {
            "chapter_number": 9,
            "title": "Chapter 9: The Best Way to Survive Attention Is to Look Pathetic",
            "story_day": "Day 4, afternoon",
            "setting": "Physical aptitude training grounds",
            "pov": "Lucen Gray (1st-person)",
            "entities": ["Lucen Gray", "Rhea Valtor (Sword Prodigy)", "First-Year Cadets"],
            "dramatic_objective": "Score in the lowest passing percentile to maintain a low threat profile while preventing student casualties.",
            "tactical_move": "Uses Three-Point Anchor to safely deflect a collapsing steel training rack while making it look like he clumsily slipped and fell under it.",
            "misunderstanding_beat": "Rhea grabs his collar and demands why his vital signs spike when he lies: 'Look at me when you lie.' Lucen replies: 'That sounds like an excellent way to die.' Rhea interprets his racing heart as romantic excitement rather than survival terror.",
            "prohibited_knowledge": ["Rhea's Crimson Measure berserk triggers"],
            "ending_hook": "Rhea unsheathes her rapier before the entire arena: 'Lucen Gray. Before this month ends, you will draw a weapon against me.'"
        },
        {
            "chapter_number": 10,
            "title": "Chapter 10: Three Monsters Learn the Extra's Name",
            "story_day": "Day 5, sunset",
            "setting": "Central Quadrangle & Academy Gates",
            "pov": "Celestia Rimehart (3rd-person limited interlude)",
            "entities": ["Celestia Rimehart", "Iris Aurelia", "Rhea Valtor", "Lucen Gray"],
            "dramatic_objective": "Determine whether Lucen is a prophet, an imperial agent, or an enemy before her frost core worsens.",
            "tactical_move": "Lucen backs toward the nearest open window, refusing all offers and associations.",
            "misunderstanding_beat": "Lucen tells them all bluntly: 'I'd prefer if none of you became interested in me.' Iris hears: 'He is shielding us from political scandal.' Celestia hears: 'He fears attachment because he saw my death.' Rhea hears: 'A martial challenge.'",
            "prohibited_knowledge": ["The upcoming Prism Cube exam trap"],
            "ending_hook": "Academy heralds sound the horn: The Prism Cube Ranking Exam begins tomorrow—and Lucen must rank in the Top 20 or face military conscription!"
        }
    ]
}

def materialize():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    now = datetime.utcnow().isoformat()
    
    # 1. Upsert Stage 1 Card (Type 10)
    c.execute("SELECT id FROM card WHERE project_id = ? AND title = ?", (PROJECT_ID, STAGE_1_DATA["title"]))
    row = c.fetchone()
    if row:
        stage_id = row[0]
        c.execute("""
            UPDATE card SET
                content = ?,
                parent_id = ?,
                card_type_id = 10,
                display_order = 1,
                model_name = 'gpt-5.6-sol'
            WHERE id = ?
        """, (json.dumps(STAGE_1_DATA, indent=2), VOL_1_CARD_ID, stage_id))
    else:
        c.execute("""
            INSERT INTO card (title, card_type_id, project_id, parent_id, content, display_order, model_name, created_at, ai_modified, needs_confirmation)
            VALUES (?, 10, ?, ?, ?, 1, 'gpt-5.6-sol', ?, 0, 0)
        """, (STAGE_1_DATA["title"], PROJECT_ID, VOL_1_CARD_ID, json.dumps(STAGE_1_DATA, indent=2), now))
        stage_id = c.lastrowid
    print(f"[✓] Stage 1 Card ready (ID: {stage_id})")

    # 2. Upsert 10 Chapter Outline (Type 11) and 10 Chapter Text (Type 12) Cards
    for ch in STAGE_1_DATA["chapters"]:
        ch_num = ch["chapter_number"]
        outline_title = f"{ch['title']} [Outline]"
        text_title = ch["title"]

        # Chapter Outline Card (Type 11)
        c.execute("SELECT id FROM card WHERE project_id = ? AND title = ?", (PROJECT_ID, outline_title))
        o_row = c.fetchone()
        ch_content = json.dumps(ch, indent=2)
        if o_row:
            c.execute("""
                UPDATE card SET
                    content = ?,
                    parent_id = ?,
                    card_type_id = 11,
                    display_order = ?,
                    model_name = 'gpt-5.6-sol'
                WHERE id = ?
            """, (ch_content, stage_id, ch_num, o_row[0]))
            o_id = o_row[0]
        else:
            c.execute("""
                INSERT INTO card (title, card_type_id, project_id, parent_id, content, display_order, model_name, created_at, ai_modified, needs_confirmation)
                VALUES (?, 11, ?, ?, ?, ?, 'gpt-5.6-sol', ?, 0, 0)
            """, (outline_title, PROJECT_ID, stage_id, ch_content, ch_num, now))
            o_id = c.lastrowid

        # Chapter Text Card (Type 12)
        c.execute("SELECT id FROM card WHERE project_id = ? AND title = ?", (PROJECT_ID, text_title))
        t_row = c.fetchone()
        init_text_content = json.dumps({"chapter_number": ch_num, "title": text_title, "status": "pending_draft", "prose": ""})
        if not t_row:
            c.execute("""
                INSERT INTO card (title, card_type_id, project_id, parent_id, content, display_order, model_name, created_at, ai_modified, needs_confirmation)
                VALUES (?, 12, ?, ?, ?, ?, 'claude-opus-4-6', ?, 0, 0)
            """, (text_title, PROJECT_ID, o_id, init_text_content, ch_num, now))
            t_id = c.lastrowid
        else:
            t_id = t_row[0]
        
        print(f"  [+] Chapter {ch_num:02d}: Outline ID {o_id} | Text ID {t_id}")

    conn.commit()
    conn.close()
    print("\n🎉 Stage 1 (Chapters 1–10) Materialization Complete!")

if __name__ == "__main__":
    materialize()
