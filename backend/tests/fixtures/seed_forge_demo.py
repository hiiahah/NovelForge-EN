"""Seed a running backend with the synthetic source novel and an isolated original project.

Manual/preview helper only (not part of the test suite):
    python tests/fixtures/seed_forge_demo.py http://127.0.0.1:54321
"""

from __future__ import annotations

import base64
import os
import sys

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from tests.fixtures import synthetic_novel as syn  # noqa: E402


def main(base: str) -> None:
    c = httpx.Client(base_url=base, timeout=120)
    types = {t["name"]: t["id"] for t in c.get("/api/card-types").json()}

    def card(pid, type_name, title, content):
        r = c.post(f"/api/projects/{pid}/cards", json={"title": title, "card_type_id": types[type_name], "content": content})
        r.raise_for_status()
        return r.json()

    pid = c.post("/api/projects/", json={"name": "Synthetic Source (demo)", "description": "", "template": None}).json()["data"]["id"]
    c.post("/api/lab/manuscript/import", json={"filename": "synthetic.txt", "content_base64": base64.b64encode(syn.build_txt().encode()).decode(), "project_id": pid, "book_title": "Ledger of the Lantern Ward", "language": "en"}).raise_for_status()
    from app.services.lab.lab_helpers import fn_lab_analysis_records, fn_lab_chapter_items

    cards = [x for x in c.get(f"/api/projects/{pid}/cards").json() if x["card_type"]["name"] == "Chapter Analysis"]
    items = fn_lab_chapter_items(cards)
    for rec in fn_lab_analysis_records([{"ai_result": syn.fake_chapter_analysis(it["chapter_no"]), "meta": it} for it in items]):
        cd = next(x for x in cards if x["id"] == rec["card_id"])
        c.put(f"/api/cards/{cd['id']}", json={"content": {**cd["content"], **rec}}).raise_for_status()
    for name, role in syn.SOURCE_CHARACTERS.items():
        card(pid, "Character Card", name, {"name": name, "entity_type": "character", "life_span": "Long Term", "role_type": role, "born_scene": "Tessaly", "description": "", "personality": "", "core_drive": "", "character_arc": "", "aliases": [name.split()[0]]})
    for loc in syn.SOURCE_LOCATIONS:
        card(pid, "Scene Card", loc, {"name": loc, "entity_type": "scene", "life_span": "Long Term", "description": "source place"})
    c.post("/api/forge/source/fingerprint", params={"project_id": pid}).raise_for_status()
    c.post("/api/forge/source/examples", params={"project_id": pid}).raise_for_status()
    card(pid, "Narrative Genome", "Narrative Genome", syn.fake_genome())
    r = c.post("/api/forge/original/create", json={"source_project_id": pid, "name": "Original (demo)", "template": None})
    r.raise_for_status()
    opid = r.json()["project_id"]
    print(f"source project {pid}, original project {opid}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:54321")
