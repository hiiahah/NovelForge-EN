"""20+ chapter continuity run over the synthetic pipeline (Phase 17, long-run checks).

The drafter is a deterministic stand-in for the drafting model that follows the
compiled context; one chapter (14) is a faulty draft that tries to leak knowledge,
reset a relationship and duplicate a payoff, and must be repaired before commit.
No live model calls.
"""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from typing import Any, Dict, List, Optional

import pytest
from sqlmodel import Session

from tests import test_forge_pipeline as pl
from tests.fixtures import synthetic_novel as syn

pytestmark = pytest.mark.timeout(900)

CHAPTERS = 22
PAYOFF_CHAPTER = 13
REVEAL_CHAPTER = 18
FAULTY_CHAPTER = 14


def _outline(n: int) -> Dict[str, Any]:
    learned = None
    if n == 11:
        learned = "the courier draws left-handed"
    if n == REVEAL_CHAPTER:
        learned = "Corvin Ashe is the courier who forges the manifests"
    o = pl._outline(n, learned=learned)
    o["forbidden_outcomes"] = ["Corvin is revealed as the courier"] if n < REVEAL_CHAPTER else []
    if n == PAYOFF_CHAPTER:
        o["allowed_outcomes"].append("the token opens the Archive cellar")
        o["beats"][3] = {"function": "threat_escalation", "description": "Nadia tries the brass token; the token opens the Archive cellar", "keywords": ["token", "cellar"]}
    if n == 7:
        o["allowed_outcomes"].append("Nadia promised to keep the manifest whole")
    if n >= 16:
        o["allowed_outcomes"].append("Nadia and Teo Marsh become trusted allies")
    return o


def _prose(n: int, *, faulty: bool = False) -> str:
    learned = None
    if n == 11:
        learned = "the courier draws left-handed"
    if n == REVEAL_CHAPTER:
        learned = "Corvin Ashe is the courier who forges the manifests"
    text = pl._original_chapter(n, learned=learned)
    prose, claims_json = text.split("<claims>")
    claims = json.loads(claims_json.replace("</claims>", ""))
    extra: List[str] = []
    if n == PAYOFF_CHAPTER:
        extra.append("The brass token turned in the cellar lock. The token opens the Archive cellar; I stood there a while with the door open.")
    if n == 7:
        extra.append("Nadia promised to keep the manifest whole.")
    if n == 16:
        extra.append("Something had settled between us. Nadia and Teo Marsh become trusted allies, though neither of us said so.")
        claims["claims"].append({"kind": "relationship_changed", "subject": "Nadia Quill ↔ Teo Marsh", "value": "trusted allies", "evidence": "Nadia and Teo Marsh become trusted allies, though neither of us said so."})
    if faulty:
        # Knowledge leak before the reveal, a duplicate payoff and a relationship reset.
        extra.append("Nadia realized that Corvin Ashe is the courier who forges the manifests.")
        extra.append("The brass token turned in the cellar lock again. The token opens the Archive cellar.")
        extra.append("We were strangers again, Teo and I, and I let it stand.")
        claims["claims"].append({"kind": "relationship_changed", "subject": "Nadia Quill ↔ Teo Marsh", "value": "strangers", "evidence": "We were strangers again, Teo and I, and I let it stand."})
    if extra:
        # Insert before the escalation/cliffhanger block so the outline's beat order holds.
        paras = prose.rstrip().split("\n\n")
        cut = next(i for i, p in enumerate(paras) if p.startswith("I went out"))
        prose = "\n\n".join(paras[:cut + 1] + extra + paras[cut + 1:]) + "\n"
    if n == REVEAL_CHAPTER:
        claims["claims"].append({"kind": "knowledge_gained", "subject": "Nadia Quill", "value": "Corvin Ashe is the courier who forges the manifests", "evidence": "Nadia realized that Corvin Ashe is the courier who forges the manifests."})
    return prose + "<claims>" + json.dumps(claims) + "</claims>"


class ContinuityDrafter:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context) -> str:
        self.calls.append({"role": role, "chapter": context.chapter_number, "user_prompt": user_prompt})
        return _prose(context.chapter_number, faulty=(context.chapter_number == FAULTY_CHAPTER and role == "drafting"))


@pytest.fixture(scope="module")
def client(app_client):
    return app_client


@pytest.fixture(scope="module")
def state() -> Dict[str, Any]:
    return {}


def _setup_projects(client, state) -> None:
    from app.services.lab.lab_helpers import fn_lab_analysis_records, fn_lab_chapter_items

    r = client.post("/api/projects/", json={"name": f"Continuity Source {uuid.uuid4().hex[:6]}", "description": "", "template": None})
    pid = r.json()["data"]["id"]
    payload = {"filename": "synthetic.txt", "content_base64": base64.b64encode(syn.build_txt().encode("utf-8")).decode("ascii"), "project_id": pid, "book_title": "Ledger of the Lantern Ward", "language": "en"}
    assert client.post("/api/lab/manuscript/import", json=payload).status_code == 200
    cards = pl._cards(client, pid, "Chapter Analysis")
    items = fn_lab_chapter_items(cards)
    records = fn_lab_analysis_records([{"ai_result": syn.fake_chapter_analysis(it["chapter_no"]), "meta": it} for it in items])
    for rec in records:
        card = next(c for c in cards if c["id"] == rec["card_id"])
        assert client.put(f"/api/cards/{card['id']}", json={"content": {**card["content"], **rec}}).status_code == 200
    for name, role in syn.SOURCE_CHARACTERS.items():
        pl._card(client, pid, "Character Card", name, {"name": name, "entity_type": "character", "life_span": "Long Term", "role_type": role, "born_scene": "Tessaly", "description": "", "personality": "", "core_drive": "", "character_arc": "", "aliases": [name.split()[0]]})
    for loc in syn.SOURCE_LOCATIONS:
        pl._card(client, pid, "Scene Card", loc, {"name": loc, "entity_type": "scene", "life_span": "Long Term", "description": "source place"})
    assert client.post("/api/forge/source/fingerprint", params={"project_id": pid}).status_code == 200
    assert client.post("/api/forge/source/examples", params={"project_id": pid}).status_code == 200
    pl._card(client, pid, "Narrative Genome", "Narrative Genome", syn.fake_genome())
    r = client.post("/api/forge/original/create", json={"source_project_id": pid, "name": f"Continuity Original {uuid.uuid4().hex[:6]}", "template": None})
    assert r.status_code == 200, r.text
    opid = r.json()["project_id"]
    state["source_pid"], state["original_pid"] = pid, opid

    pl._card(client, opid, "Story Foundation", "Story Foundation", {"core_premise": "A harbor clerk hunts the courier who forges the tide manifests.", "central_dramatic_question": "Can Nadia name the courier before the Archive burns?", "protagonist_goal": "Protect the manifest", "main_opposition": "the courier", "stakes": "the harbor", "unique_mechanism": "The manifest records lies as blank pages.", "truth_status": "canon", "confidence": 1.0})
    pl._card(client, opid, "Reader Contract", "Reader Contract", {"primary_fantasy": "quiet competence under pressure", "primary_emotional_reward": "reinterpretation", "expected_tone": "restrained", "expected_protagonist_behavior": ["notices details"], "violations": ["melodrama"], "truth_status": "canon", "confidence": 1.0})
    for name, role in pl.ORIGINAL_CHARACTERS.items():
        pl._card(client, opid, "Character Card", name, {"name": name, "entity_type": "character", "life_span": "Long Term", "role_type": role, "born_scene": "Harrow Quay", "description": f"{role} of the original story", "personality": "dry", "core_drive": "protect the manifest" if role == "Protagonist" else "keep secrets", "character_arc": "", "aliases": [name.split()[0]], "voice": {"sentence_tendency": "short", "verbal_tells": [], "forms_of_address": [], "forbidden_speech": ["I beg you"] if role == "Protagonist" else []}, "dynamic_info": {"Possessions": [{"id": 1, "info": "brass token", "weight": 1.0}]} if role == "Protagonist" else {}})
    for loc in pl.ORIGINAL_LOCATIONS:
        pl._card(client, opid, "Scene Card", loc, {"name": loc, "entity_type": "scene", "life_span": "Long Term", "description": "original place"})
    pl._card(client, opid, "Relationship Arc", "Nadia Quill ↔ Teo Marsh", {"character_a": "Nadia Quill", "character_b": "Teo Marsh", "trust": 30, "affection": 40, "fear": 0, "dependency": 10, "resentment": 5, "private_relationship": "wary allies", "unresolved_tension": "Teo knows more than he says", "truth_status": "canon", "confidence": 1.0})
    pl._card(client, opid, "Knowledge Fact", "Corvin is the courier", {"fact": "Corvin Ashe is the courier who forges the manifests", "reader_state": "unaware", "planned_reveal_chapter": REVEAL_CHAPTER, "sensitivity": "high", "knowers": [{"entity": "Corvin Ashe", "state": "knows"}, {"entity": "Nadia Quill", "state": "unaware"}, {"entity": "Teo Marsh", "state": "suspects", "learned_chapter": 0}], "truth_status": "canon", "confidence": 1.0})
    pl._card(client, opid, "Promise Payoff", "The brass token fits a lock", {"setup": "The brass token fits a lock", "promise_type": "chekhovs_gun", "status": "planted", "participants": ["Nadia Quill"], "source_chapter": 1, "target_payoff_range": [12, 16], "planned_payoff": "the token opens the Archive cellar", "truth_status": "planned", "confidence": 1.0})
    pl._card(client, opid, "Plot Thread", "Who forges the manifests", {"name": "Who forges the manifests", "thread_type": "main_plot", "status": "active", "urgency": "high", "participants": ["Nadia Quill", "Corvin Ashe"], "opening_chapter": 1, "last_advanced_chapter": 0, "central_question": "Who is the courier?", "truth_status": "canon", "confidence": 1.0, "milestones": []})
    assert client.post("/api/forge/original/seed-canon", params={"project_id": opid}).status_code == 200
    for n in range(1, CHAPTERS + 1):
        pl._card(client, opid, "Chapter Outline", f"Rivets {n}", _outline(n))


def test_continuity_22_chapters(client, state):
    from app.db.session import engine
    from app.services.forge import canon as canon_store
    from app.services.forge.pipeline import PipelineOptions, run_chapter
    from app.services.forge.validators import style_report

    _setup_projects(client, state)
    opid = state["original_pid"]
    drafter = ContinuityDrafter()
    results: Dict[int, Any] = {}
    with Session(engine) as s:
        for n in range(1, CHAPTERS + 1):
            res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=n, drafter=drafter, options=PipelineOptions(max_repairs=2)))
            assert res.status == "committed", (n, json.dumps(res.error, default=str)[:6000])
            results[n] = res

        # --- the faulty chapter was caught and repaired, never committed as drafted
        faulty = results[FAULTY_CHAPTER]
        first_codes = {i["code"] for i in faulty.validation["history"][0]["issues"]}
        assert faulty.repair_attempts >= 1
        assert "forbidden_reveal" in first_codes or "future_beat_advanced" in first_codes, first_codes
        assert "duplicate_payoff" in first_codes, first_codes
        assert "relationship_reset" in first_codes, first_codes

        # --- no knowledge leakage: Nadia does not "know" the courier before the reveal chapter
        for n in range(1, REVEAL_CHAPTER):
            st = canon_store.state_as_of(s, opid, n)
            knows = st.get(("nadia quill", "knows"))
            assert not (knows and any("courier who forges" in str(v) for v in (knows.value or []))), (n, knows)
        st = canon_store.state_as_of(s, opid, REVEAL_CHAPTER)
        assert any("courier who forges" in str(v) for v in (st[("nadia quill", "knows")].value or []))
        # knowledge gained in ch.11 persists through the end
        for n in range(11, CHAPTERS + 1):
            st = canon_store.state_as_of(s, opid, n)
            assert any("left-handed" in str(v) for v in (st[("nadia quill", "knows")].value or [])), (n, results[11].sync, results[11].validation["issues"])

        # --- no relationship reset: once trusted allies (ch.16), stays so
        for n in range(16, CHAPTERS + 1):
            st = canon_store.state_as_of(s, opid, n)
            assert st[("nadia quill ↔ teo marsh", "private_relationship")].value == "trusted allies", n
        assert canon_store.state_as_of(s, opid, 15)[("nadia quill ↔ teo marsh", "private_relationship")].value == "wary allies"

        # --- no timeline inversion / no character disappearance across chapters
        for n in range(1, CHAPTERS + 1):
            assert not any(i["code"] == "time_inversion" for i in results[n].validation["issues"]), n
            packet = results[n].sync["state_packet"]
            assert packet["chapter_number"] == n and set(packet["scene_state"]["participants"]) == set(pl.ORIGINAL_CHARACTERS)
            assert packet["scene_state"]["ending_location"] == "Harrow Quay"
            assert results[n].sync["canon_revision_after"] == n

    # --- promise paid off once, in its window, then not offered again
    promises = pl._cards(client, opid, "Promise Payoff")
    assert promises[0]["content"]["status"] == "paid_off" and promises[0]["content"]["payoff_chapter"] == PAYOFF_CHAPTER
    ctx_after = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": CHAPTERS, "regenerate": True}).json()
    assert "promises" not in {sec["key"] for sec in ctx_after["sections"]}

    # --- forgotten promise check: the ch.7 promise is carried in canon until the end
    with Session(engine) as s:
        st = canon_store.state_as_of(s, opid, CHAPTERS)
        oq = st.get(("nadia quill", "open_questions"))
        assert oq and any("keep the manifest whole" in str(v) for v in oq.value), oq

    # --- thread advanced every chapter, timeline card per chapter
    thread = pl._cards(client, opid, "Plot Thread")[0]["content"]
    assert thread["last_advanced_chapter"] == CHAPTERS
    timeline = sorted(c["content"]["chapter_number"] for c in pl._cards(client, opid, "Timeline Event"))
    assert timeline == list(range(1, CHAPTERS + 1))

    # --- no source entity leakage anywhere in the original project
    texts = pl._cards(client, opid, "Chapter Text")
    assert sorted(c["content"]["chapter_number"] for c in texts) == list(range(1, CHAPTERS + 1))
    all_text = json.dumps([c["content"] for c in pl._cards(client, opid)])
    for name in list(syn.SOURCE_CHARACTERS) + syn.SOURCE_LOCATIONS:
        assert name not in all_text, name
    for c in texts:
        assert c["content"]["sync_status"] == "synchronized" and c["content"]["canon_revision"] == c["content"]["chapter_number"]

    # --- no style drift: adherence stays within a narrow band across the run
    fp = pl._cards(client, opid, "Narrative Fingerprint")[0]["content"]
    scores = [style_report(c["content"]["content"], fp)["adherence_score"] for c in sorted(texts, key=lambda c: c["content"]["chapter_number"])]
    assert min(scores) >= 0.7 and max(scores) - min(scores) <= 0.15, scores

    # --- manifest and audit
    manifest = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert manifest["latest_committed_chapter"] == CHAPTERS and manifest["next_allowed_chapter"] == CHAPTERS + 1 and manifest["canon_revision"] == CHAPTERS
    audit = client.get("/api/forge/audit", params={"project_id": opid}).json()
    assert not any(f["severity"] == "critical" for f in audit["findings"]), audit["findings"]
    # Every model call happened for exactly the chapters run (1 draft each + repairs for the faulty chapter).
    assert sum(1 for c in drafter.calls if c["role"] == "drafting") == CHAPTERS
    assert all(c["chapter"] == FAULTY_CHAPTER for c in drafter.calls if c["role"] == "repair")
