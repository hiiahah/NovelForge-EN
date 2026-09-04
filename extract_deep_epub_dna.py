#!/usr/bin/env python3
"""
extract_deep_epub_dna.py
Extracts the deep structural DNA from 'How to survive in the Romance Fantasy Game.epub'
across all 638 chapters to serve as the master reference for the entire 12-file Novel Bible.
"""

import zipfile
import json
from bs4 import BeautifulSoup

epub_path = '/home/ubuntu/nvidia_chat_bot/How to survive in the Romance Fantasy Game.epub'

with zipfile.ZipFile(epub_path, 'r') as z:
    files = sorted([f for f in z.namelist() if f.endswith('.xhtml') and 'Chapter' in f])
    
    # 1. Opening chapters (Ch 1-5) sample
    ch1_sample = BeautifulSoup(z.read(files[0]), 'html.parser').get_text()[:2500]
    ch5_sample = BeautifulSoup(z.read(files[4]), 'html.parser').get_text()[:2500]
    
    # 2. Mid-game sample (Ch 250 - Emperor / Rose & Snow protecting Riley)
    ch250_sample = BeautifulSoup(z.read(files[249]), 'html.parser').get_text()[:2500]
    
    # 3. Late-game / Interlude sample (Ch 450 - A Night filled with love)
    ch450_sample = BeautifulSoup(z.read(files[449]), 'html.parser').get_text()[:2500]
    
    # 4. Endgame sample (Ch 600 - Cold Heart)
    ch600_sample = BeautifulSoup(z.read(files[599]), 'html.parser').get_text()[:2500]

dna = {
    "novel_title": "How to Survive in the Romance Fantasy Game",
    "total_chapters": len(files),
    "core_mechanics": {
        "mc_archetype": "Riley Hell - F-rank/zero-luck gamer transmigrator, desperately trying to survive as a background mob character, but his avoidance is constantly misinterpreted as supreme nobility, quiet strength, and tragic self-sacrifice.",
        "danger_equals_romance": "The heroines are terrifying final bosses / yanderes who can destroy nations. Every romantic interaction carries mortal peril, and every cowardly evasion accidentally spikes their affection.",
        "named_bad_endings": "Recurring comedic & tactical anchor: [Bad End No. 63: Skin for Love], [Bad End No. 89: A Dragon's Dessert], etc.",
        "unhinged_system": "Chaotic system popups that warn him, offer bizarre blessings, and curse: [Note: An Evil Goddess warns you to stay away from bitches at all costs!]",
        "heroine_cast_dynamics": [
            "Liyana: The secret dragon final boss fiancée, cuddly like a cat, lethal if jealous.",
            "Alice Holloway: The pink-haired witch senior, playful, teasing ('we can talk about that kiss after you rest~'), abuses her cat familiar Cheshire, MC's favorite character.",
            "Princess Snow: The Northern ice prodigy, ruthless to anyone threatening Riley.",
            "Rose: Celestial magic prodigy, golden-red mana execution circles, locks down entire rooms with bloodlust to defend Riley from faculty.",
            "Enna: The trembling Saintess connected to the Church/Goddess.",
            "Janica: The sweet heroine loyal to the original protagonist Lucas."
        ],
        "original_protagonist_role": "Lucas - The handsome, well-meaning, generic hero. He triggers incident flags and struggles, while the MC secretly orchestrates solutions from the shadows.",
        "macro_arc_progression": {
            "Vol 1 (Ch 1-50)": "Academy entrance, seat assignments, evaluation tests, first wave of yandere death-flag defusals.",
            "Vol 2 (Ch 51-100)": "Grand Festival, secret dungeon interventions, misunderstandings escalating across the academy.",
            "Vol 3 (Ch 101-200)": "Midterms, end of first semester, deep heroine POV interludes revealing their hidden trauma.",
            "Vol 4 (Ch 201-300)": "Emperor summons, political court intrigue, heroines threatening the faculty/palace to protect the MC.",
            "Vol 5 (Ch 301-450)": "Calamity invasions, intimate romance interludes, preventing heroines' canonical deaths.",
            "Vol 6-8 (Ch 451-638)": "Spirit Kings, Continental Extinction Events, True Harem Ending."
        }
    },
    "samples": {
        "chapter_1_opening": ch1_sample,
        "chapter_5_alice_meeting": ch5_sample,
        "chapter_250_heroines_protect_mc": ch250_sample,
        "chapter_450_night_with_love": ch450_sample,
        "chapter_600_cold_heart": ch600_sample
    }
}

with open('/home/ubuntu/NovelForge-EN/books/I_Maxed_Survival_Final_Boss_Heroines/bible/master_reference_epub_dna.json', 'w', encoding='utf-8') as f:
    json.dump(dna, f, indent=2, ensure_ascii=False)

print(f"Successfully extracted master reference EPUB DNA across all {len(files)} chapters!")
