"""End-to-end tests for Novel Bible 2.0 (no LLM calls).

Run: cd backend && ./venv/bin/python -m pytest tests/ -q
"""

from __future__ import annotations

import base64
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture(scope="module")
def client(app_client):
    return app_client


@pytest.fixture(scope="module")
def project(client):
    import uuid

    r = client.post("/api/projects/", json={"name": f"Bible E2E {uuid.uuid4().hex[:6]}", "description": "", "template": "bible"})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _type_id(client, name: str) -> int:
    types = client.get("/api/card-types").json()
    return next(t["id"] for t in types if t["name"] == name)


def _cards(client, project_id: int):
    return client.get(f"/api/projects/{project_id}/cards").json()


def test_bible_template_creates_foundation_chain(client, project):
    # The project-created trigger runs the template workflow in a background thread.
    import time

    names = set()
    for _ in range(60):
        cards = _cards(client, project["id"])
        names = {c["card_type"]["name"] for c in cards}
        if "Style Profile" in names:
            break
        time.sleep(0.5)
    for expected in ("Story Foundation", "Reader Contract", "Theme Map", "Power System", "Narrative Architecture", "Style Profile", "Core Blueprint"):
        assert expected in names, f"missing {expected}: {sorted(names)}"


def test_character_card_backward_compatible_and_deepenable(client, project):
    pid = project["id"]
    ct = _type_id(client, "Character Card")
    legacy = {"name": "Mira", "entity_type": "character", "life_span": "Long Term", "role_type": "Protagonist", "born_scene": "Capital", "description": "An underestimated strategist", "personality": "patient", "core_drive": "Escape the capital", "character_arc": "From hiding to leading"}
    r = client.post(f"/api/projects/{pid}/cards", json={"title": "Mira", "card_type_id": ct, "content": legacy})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["content"].get("aliases", []) == []

    from app.schemas.bible import CharacterBibleDeepening
    from app.services.bible import CharacterDeepeningService
    from app.db.session import engine
    from app.db.models import Card
    from sqlmodel import Session

    deep = CharacterBibleDeepening.model_validate({
        "name": "Mira",
        "dramatic_design": {"external_goal": "Escape the capital", "internal_need": "Trust someone", "false_belief": "Power is the only protection", "greatest_fear": "Being owned", "moral_boundary": "Never sacrifices a subordinate"},
        "voice": {"verbal_tells": ["answers questions with questions"], "forbidden_speech": ["begging"]},
        "consistency_rules": {"knowledge_restrictions": ["Does not know Daren is the traitor before chapter 48"]},
        "aliases": ["the Quiet Knife"],
    })
    with Session(engine) as s:
        c = s.get(Card, card["id"])
        updated = CharacterDeepeningService(s).apply(c, deep)
        assert updated.content["dramatic_design"]["false_belief"] == "Power is the only protection"
        assert updated.content["core_drive"] == "Escape the capital"  # legacy field intact
        assert any(h["field"] == "dramatic_design" for h in updated.content["history"])


def test_ledger_cards_dashboard_and_audits(client, project):
    pid = project["id"]
    thread_ct = _type_id(client, "Plot Thread")
    promise_ct = _type_id(client, "Promise Payoff")
    fact_ct = _type_id(client, "Knowledge Fact")
    rel_ct = _type_id(client, "Relationship Arc")
    chapter_ct = _type_id(client, "Chapter Text")

    client.post(f"/api/projects/{pid}/cards", json={"title": "Missing heir", "card_type_id": thread_ct, "content": {"name": "Missing heir", "thread_type": "mystery", "status": "active", "urgency": "high", "participants": ["Mira"], "opening_chapter": 1, "last_advanced_chapter": 2, "truth_status": "planned", "confidence": 1.0, "evidence": [], "history": [], "milestones": [{"description": "Mira finds the seal", "planned": True, "status": "planned"}]}})
    client.post(f"/api/projects/{pid}/cards", json={"title": "The cracked crown reacts to Mira", "card_type_id": promise_ct, "content": {"setup": "The cracked crown reacts to Mira", "promise_type": "chekhovs_gun", "status": "planted", "participants": ["Mira"], "source_chapter": 3, "target_payoff_range": [5, 10], "strength": "strong", "truth_status": "planned", "confidence": 1.0, "evidence": [], "history": []}})
    client.post(f"/api/projects/{pid}/cards", json={"title": "King is poisoned", "card_type_id": fact_ct, "content": {"fact": "King is poisoned", "reader_state": "unaware", "planned_reveal_chapter": 40, "sensitivity": "high", "knowers": [{"entity": "Daren", "state": "knows"}, {"entity": "Mira", "state": "unaware"}], "truth_status": "planned", "confidence": 1.0, "evidence": [], "history": []}})
    client.post(f"/api/projects/{pid}/cards", json={"title": "Daren ↔ Mira", "card_type_id": rel_ct, "content": {"character_a": "Daren", "character_b": "Mira", "trust": 60, "affection": 30, "fear": 10, "dependency": 40, "resentment": 5, "public_relationship": "allies", "private_relationship": "mutual distrust", "truth_status": "planned", "confidence": 1.0, "evidence": [], "history": [], "milestones": []}})
    for n in range(1, 21):
        client.post(f"/api/projects/{pid}/cards", json={"title": f"Chapter {n}", "card_type_id": chapter_ct, "content": {"volume_number": 1, "stage_number": 1, "chapter_number": n, "title": f"Chapter {n}", "entity_list": ["Mira"], "content": "Some prose."}})

    dash = client.get("/api/bible/dashboard", params={"project_id": pid}).json()
    assert dash["current_chapter"] == 20
    assert dash["sections"]["threads"]["count"] == 1
    kinds = {w["kind"] for w in dash["audits"]["warnings"]}
    assert "neglected_thread" in kinds
    assert "overdue_promise" in kinds

    km = client.get("/api/bible/knowledge-matrix", params={"project_id": pid}).json()
    assert km["rows"][0]["states"]["Daren"] == "knows"
    rel = client.get("/api/bible/relationships", params={"project_id": pid}).json()
    assert rel["arcs"][0]["trust"] == 60


def test_context_compiler_prohibits_future_knowledge(client, project):
    pid = project["id"]
    r = client.post("/api/bible/compile-context", json={"project_id": pid, "chapter_number": 21, "participants": ["Mira", "Daren"], "pov": "Mira", "budget_chars": 6000})
    assert r.status_code == 200, r.text
    data = r.json()
    sections = {b["section"] for b in data["blocks"]}
    assert "Characters" in sections and "Relationships" in sections and "Active Threads" in sections and "Open Promises" in sections
    assert any("King is poisoned" in p for p in data["prohibited"]), data["prohibited"]
    assert all(b["reason"] for b in data["blocks"])

    ctx = client.post("/api/context/assemble", json={"project_id": pid, "chapter_number": 21, "participants": ["Mira", "Daren"]}).json()
    assert ctx["bible_context"] and "[Reader Contract]" not in ctx["bible_context"]["text"] or ctx["bible_context"]["blocks"]


def test_living_bible_review_loop(client, project):
    pid = project["id"]
    from app.db.models import BibleUpdateReview
    from app.db.session import engine
    from app.schemas.bible_update import BibleUpdateProposal
    from sqlmodel import Session

    proposal = BibleUpdateProposal.model_validate({
        "chapter_number": 21,
        "changes": [
            {"id": "c1", "kind": "goal_change", "summary": "Mira's goal shifts to protecting the rebellion", "target_card_type": "Character Card", "target_title": "Mira", "field_path": "core_drive", "new_value": "Protect the rebellion while investigating its leader", "truth_status": "canon", "explicit": True, "confidence": 0.9, "risk": "medium", "evidence": [{"chapter_number": 21, "quote": "I will not let them fall."}]},
            {"id": "c2", "kind": "relationship_change", "summary": "Mira's trust in Daren decreased", "target_card_type": "Relationship Arc", "target_title": "Daren ↔ Mira", "field_path": "trust", "previous_value": 60, "new_value": 35, "truth_status": "inferred", "explicit": False, "confidence": 0.7, "risk": "medium", "evidence": [{"chapter_number": 21, "quote": "She checked the seal twice."}]},
            {"id": "c3", "kind": "payoff_delivered", "summary": "The cracked crown pays off", "target_card_type": "Promise Payoff", "target_title": "The cracked crown reacts to Mira", "field_path": "actual_payoff", "new_value": "The crown opens the gate for Mira", "truth_status": "canon", "confidence": 0.95, "risk": "high", "evidence": [{"chapter_number": 21, "quote": "The crown split and the gate answered."}]},
            {"id": "c4", "kind": "knowledge_change", "summary": "Mira learns the king is poisoned", "target_card_type": "Knowledge Fact", "target_title": "King is poisoned", "field_path": "knowers", "new_value": {"entity": "Mira", "state": "suspects", "learned_chapter": 21, "how_learned": "the physician's ledger"}, "truth_status": "canon", "confidence": 0.8, "risk": "low", "evidence": [{"chapter_number": 21, "quote": "The ledger listed hemlock."}]},
            {"id": "c5", "kind": "new_entity", "summary": "New location: Orath", "target_card_type": "Scene Card", "target_title": "", "field_path": "", "new_value": {"name": "Orath", "description": "A border town", "function_in_story": "Daren's hidden past"}, "truth_status": "canon", "confidence": 0.9, "risk": "low", "evidence": [{"chapter_number": 21, "quote": "Orath lay beyond the ridge."}]},
            {"id": "c6", "kind": "contradiction", "summary": "Daren claims he never visited Orath", "target_card_type": "Character Card", "target_title": "Daren", "field_path": "description", "new_value": "never visited Orath", "truth_status": "believed", "confidence": 0.6, "risk": "high", "evidence": [{"chapter_number": 21, "quote": "I have never been to Orath."}]},
        ],
    })
    with Session(engine) as s:
        review = BibleUpdateReview(project_id=pid, chapter_number=21, status="pending", proposal_json=proposal.model_dump(mode="json"), decisions_json={})
        s.add(review); s.commit(); s.refresh(review)
        review_id = review.id

    listed = client.get("/api/bible/updates", params={"project_id": pid}).json()
    assert any(item["id"] == review_id for item in listed["items"])

    r = client.post(f"/api/bible/updates/{review_id}/decide", json={"decisions": [
        {"change_id": "c1", "action": "accept"},
        {"change_id": "c2", "action": "accept", "edited_value": 30},
        {"change_id": "c3", "action": "accept"},
        {"change_id": "c4", "action": "accept"},
        {"change_id": "c5", "action": "accept"},
        {"change_id": "c6", "action": "postpone"},
    ]})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["applied"] == 5 and result["postponed"] == 1 and result["errors"] == [], result
    assert result["status"] == "partially_applied"

    cards = {c["title"]: c for c in _cards(client, pid)}
    mira = cards["Mira"]["content"]
    assert mira["core_drive"].startswith("Protect the rebellion")
    assert any(h["field"] == "core_drive" and h["previous"] == "Escape the capital" for h in mira["history"])
    rel = cards["Daren ↔ Mira"]["content"]
    assert rel["trust"] == 30 and rel["history"][-1]["previous"] == 60
    promise = cards["The cracked crown reacts to Mira"]["content"]
    assert promise["status"] == "paid_off" and promise["payoff_chapter"] == 21
    fact = cards["King is poisoned"]["content"]
    assert any(k["entity"] == "Mira" and k["state"] == "suspects" for k in fact["knowers"])
    assert "Orath" in cards and cards["Orath"]["card_type"]["name"] == "Scene Card"

    # After the reveal, the compiler must stop prohibiting the fact for Mira's POV.
    compiled = client.post("/api/bible/compile-context", json={"project_id": pid, "chapter_number": 22, "participants": ["Mira"], "pov": "Mira"}).json()
    assert not any("King is poisoned" in p for p in compiled["prohibited"])


def test_manuscript_import_wizard(client, project):
    pid = project["id"]
    text = "\n".join([
        "Copyright notice", "",
        "Volume 1", "",
        "Chapter 1 The Gate", *(["The gate stood open. " * 30] * 3), "",
        "Chapter 2 The Crown", *(["The crown cracked. " * 30] * 3), "",
        "Chapter 4 The Ridge", *(["Orath lay beyond the ridge. " * 30] * 3), "",
        "Afterword", "Thanks for reading.",
    ])
    payload = {"filename": "book.txt", "content_base64": base64.b64encode(text.encode("utf-8")).decode("ascii")}
    r = client.post("/api/lab/manuscript/preview", json=payload)
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["pattern_name"] == "chapter_word"
    titles = [c["title"] for c in prev["chapters"] if c["included"]]
    assert prev["chapters"][0]["section_type"] == "front_matter" and not prev["chapters"][0]["included"]
    assert titles[:3] == ["Chapter 1 The Gate", "Chapter 2 The Crown", "Chapter 4 The Ridge"]
    assert any("Missing chapter numbers: [3]" in w for w in prev["warnings"])
    assert "afterword" in prev["chapters"][-1]["flags"] and prev["chapters"][-1]["section_type"] == "afterword"
    assert prev["included_chapters"] == 3

    r = client.post("/api/lab/manuscript/import", json={**payload, "project_id": pid, "book_title": "Test Book"})
    assert r.status_code == 200, r.text
    assert r.json()["chapter_count"] == 3
    listing = client.get("/api/lab/manuscript", params={"project_id": pid}).json()
    assert listing["meta"]["book_title"] == "Test Book"
    assert [c["chapter_number"] for c in listing["chapters"]] == [1, 2, 3]
    assert all(c["analysis_status"] == "pending" for c in listing["chapters"])


def test_docx_and_epub_extraction():
    import io
    import zipfile
    from app.services.lab.manuscript_import import extract_text, detect_chapters

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapter 1 Start</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>Body one. ' + 'word ' * 100 + '</w:t></w:r></w:p>'
                    '<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Chapter 2 Next</w:t></w:r></w:p>'
                    '<w:p><w:r><w:t>Body two. ' + 'word ' * 100 + '</w:t></w:r></w:p></w:body></w:document>')
    docx_text = extract_text("a.docx", buf.getvalue())
    det = detect_chapters(docx_text)
    assert [c.title for c in det.chapters] == ["Chapter 1 Start", "Chapter 2 Next"]

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c1" href="c1.xhtml"/><item id="c2" href="c2.xhtml"/></manifest><spine><itemref idref="c2"/><itemref idref="c1"/></spine></package>')
        zf.writestr("OEBPS/c1.xhtml", "<html><body><h1>Chapter 2 Later</h1><p>" + "later " * 100 + "</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml", "<html><body><h1>Chapter 1 First</h1><p>" + "first " * 100 + "</p></body></html>")
    epub_text = extract_text("b.epub", buf.getvalue())
    det = detect_chapters(epub_text)
    assert [c.title for c in det.chapters] == ["Chapter 1 First", "Chapter 2 Later"]  # spine order respected
