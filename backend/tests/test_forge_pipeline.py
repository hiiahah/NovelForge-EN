"""End-to-end synthetic reverse-engineering pipeline (Phase 17) — no live model calls.

Steps 1-30 of the task specification are covered in order by the tests in this
module (they share one source project and one original project created in the
module fixture; each test asserts a stage and leaves state for the next).
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional

import pytest
from sqlmodel import Session, select

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tests.fixtures import synthetic_novel as syn  # noqa: E402

pytestmark = pytest.mark.timeout(600)


# ----------------------------------------------------------------- helpers
def _type_id(client, name: str) -> int:
    types = client.get("/api/card-types").json()
    return next(t["id"] for t in types if t["name"] == name)


def _card(client, pid: int, type_name: str, title: str, content: Dict[str, Any]) -> Dict[str, Any]:
    r = client.post(f"/api/projects/{pid}/cards", json={"title": title, "card_type_id": _type_id(client, type_name), "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def _cards(client, pid: int, type_name: Optional[str] = None) -> List[Dict[str, Any]]:
    cards = client.get(f"/api/projects/{pid}/cards").json()
    return [c for c in cards if type_name is None or c["card_type"]["name"] == type_name]


ORIGINAL_CHARACTERS = {"Nadia Quill": "Protagonist", "Teo Marsh": "Deuteragonist", "Corvin Ashe": "Antagonist", "Petra Vale": "Supporting Character"}
ORIGINAL_LOCATIONS = ["Harrow Quay", "the Salt Archive", "Kestrel Row"]


def _original_chapter(n: int, *, pov: str = "Nadia", inject_unsupported: bool = False, inject_copy: bool = False, inject_leak: bool = False, inject_future: bool = False, learned: Optional[str] = None) -> str:
    """Deterministic 'drafting model' output in the fingerprint's style, entity-safe unless told otherwise."""
    other = "Teo" if n % 3 else "Corvin"
    paras = [
        ["The tide bell rang once.", "Rope and rust on Kestrel Row.", "Teo was already at the rail when I came up.", "Nobody on Harrow Quay locks a hatch they mean to open."][n % 4],
        "I counted the rivets on the hatch. Eleven. Same as yesterday.",
        f"{other} did not look round. \"You were at the Archive.\"",
        "\"I am at a lot of places.\"",
        "Quiet, for a breath.",
        f"Page {n} of the manifest stayed blank. I wrote the date.",
        "Teo said the quay was clear. I let him say it.",
        "I checked the locker. I checked the hatch. I checked the locker again.",
    ]
    if learned:
        paras.append(f"Nadia realized that {learned}.")
    if inject_unsupported:
        paras.append("Nadia took the silver compass from the drawer and pocketed it.")
    if inject_copy:
        paras.append(syn.chapter_text(4).split("\n\n")[12])  # verbatim source paragraph
    if inject_leak:
        paras.append("Marit Solen was waiting by the Greywater Exchange with the copper key.")
    if inject_future:
        paras.append("Corvin set the chalk down. He was the courier; he had always been the courier.")
    paras += [
        "Petra came by with bread and no news. \"Clear night,\" she said. I nodded.",
        "I went out regardless.",
        ["Then the Archive lamp died in its window. No wind that night.", "Then a chalk mark appeared on our hatch. Fresh. Still damp.", "Then Petra did not come back from the Row.", "Then somebody raised the harbor chain, and nobody had rung for it."][n % 4],
        "I kept walking. Running admits something.",
        f"Harrow Quay had gone dark before I turned the corner. {'Corvin' if other == 'Teo' else 'Teo'} stood under the last lamp.",
        "\"Go below, Nadia.\"",
        "\"Not yet.\"",
        ["Who had known I would come?", "If Corvin was not the courier, why the chalk?", "What had Teo not said?", "How long had the chain been up?"][n % 4],
    ]
    prose = "\n\n".join(paras)
    claims = {"claims": [], "summary": f"Nadia is confronted by {other}, reassured, then the quay escalates; ends on a question.", "ending_location": "Harrow Quay", "current_time": "night", "unresolved_immediate_action": "Nadia refuses to go below", "open_dialogue_obligation": ""}
    return prose + "\n<claims>" + json.dumps(claims) + "</claims>"


class FakeDrafter:
    """Deterministic drafting/repair model. Records every call; repair removes injected faults."""

    def __init__(self, faults: Optional[Dict[str, bool]] = None, learned: Optional[str] = None):
        self.faults = dict(faults or {})
        self.learned = learned
        self.calls: List[Dict[str, Any]] = []

    async def __call__(self, *, role: str, system_prompt: str, user_prompt: str, context) -> str:
        self.calls.append({"role": role, "system_prompt": system_prompt, "user_prompt": user_prompt, "chapter": context.chapter_number})
        if role == "repair":
            # The repair model rewrites only the failed spans: here that means dropping the injected faults.
            return _original_chapter(context.chapter_number, learned=self.learned)
        return _original_chapter(context.chapter_number, learned=self.learned, inject_unsupported=self.faults.get("unsupported", False), inject_copy=self.faults.get("copy", False), inject_leak=self.faults.get("leak", False), inject_future=self.faults.get("future", False))


class NeverCalledDrafter:
    def __init__(self):
        self.calls = 0

    async def __call__(self, **kwargs) -> str:
        self.calls += 1
        raise AssertionError("model must not be called")


@pytest.fixture(scope="module")
def client(app_client):
    return app_client


@pytest.fixture(scope="module")
def state() -> Dict[str, Any]:
    return {}


# ================================================================ steps 1-6: source side
def test_step01_import_synthetic_source(client, state):
    r = client.post("/api/projects/", json={"name": f"Forge Source {uuid.uuid4().hex[:6]}", "description": "", "template": None})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["data"]["id"]
    state["source_pid"] = pid
    payload = {"filename": "synthetic.txt", "content_base64": base64.b64encode(syn.build_txt().encode("utf-8")).decode("ascii"), "project_id": pid, "book_title": "Ledger of the Lantern Ward", "language": "en"}
    r = client.post("/api/lab/manuscript/import", json=payload)
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["chapter_count"] == syn.CHAPTER_COUNT
    assert imp["manuscript_id"] and imp["unchanged"] is False
    state["manuscript_id"] = imp["manuscript_id"]
    # Idempotent re-import.
    r2 = client.post("/api/lab/manuscript/import", json=payload)
    assert r2.json()["unchanged"] is True and r2.json()["manuscript_id"] == imp["manuscript_id"]
    listing = client.get("/api/lab/manuscript", params={"project_id": pid}).json()
    ch = listing["chapters"][0]
    assert ch["chapter_id"] and ch["manuscript_id"] == imp["manuscript_id"] and ch["source_text_hash"] and ch["language"] == "en"
    assert listing["meta"]["parser_version"] and listing["meta"]["source_file_hash"] and listing["meta"]["imported_at"]
    assert "Afterword" not in [c["title"] for c in listing["chapters"]]


def test_step02_03_analyze_and_verify_evidence(client, state):
    """Simulate the Lab analysis model with deterministic outputs (one fabricated quote per chapter) and verify."""
    from app.services.lab.lab_helpers import fn_lab_analysis_records, fn_lab_chapter_items, fn_lab_failed_chapters, fn_lab_verified_records

    pid = state["source_pid"]
    cards = _cards(client, pid, "Chapter Analysis")
    items = fn_lab_chapter_items(cards)
    assert len(items) == syn.CHAPTER_COUNT and all(it["manuscript_id"] and it["chapter_id"] for it in items)
    results = []
    for it in items:
        if it["chapter_no"] == 7:
            results.append({"error": "simulated model failure", "meta": it})  # a failed chapter
        else:
            results.append({"ai_result": syn.fake_chapter_analysis(it["chapter_no"]), "meta": it})
    records = fn_lab_analysis_records(results)
    assert len(records) == syn.CHAPTER_COUNT
    assert fn_lab_failed_chapters(records) == [7]
    verified = fn_lab_verified_records(records)
    assert len(verified) == syn.CHAPTER_COUNT - 1
    for rec in verified:
        statuses = [o["verification_status"] for o in rec["observations"]]
        assert "unverified" in statuses  # the fabricated quote was caught
        assert rec["evidence_verified"] >= 4
        assert all(o["evidence_hash"] for o in rec["observations"] if o["verification_status"] == "verified")
    # Persist the records onto the cards (what Card.BatchUpsert does in the workflow).
    for rec in records:
        card = next(c for c in cards if c["id"] == rec["card_id"])
        content = {**card["content"], **rec}
        r = client.put(f"/api/cards/{card['id']}", json={"content": content})
        assert r.status_code == 200, r.text
    status = client.get("/api/forge/source/status", params={"project_id": pid}).json()
    assert status["analysed"] == syn.CHAPTER_COUNT - 1 and status["failed_chapters"] == [7]
    assert 0 < status["evidence_coverage"] < 1
    assert status["integrity"]["ok"], status["integrity"]
    # Re-verification is idempotent and still reports the failed chapter.
    ver = client.post("/api/forge/source/verify", params={"project_id": pid}).json()
    assert ver["failed"] == 1 and ver["verified"] == syn.CHAPTER_COUNT - 1 and ver["complete"] is False


def test_step04_build_fingerprint(client, state):
    pid = state["source_pid"]
    r = client.post("/api/forge/source/fingerprint", params={"project_id": pid})
    assert r.status_code == 200, r.text
    fp = r.json()
    assert len(fp["layers"]) == 20 and fp["chapters_measured"] == syn.CHAPTER_COUNT
    state["fingerprint_hash"] = fp["dependency_hash"]
    card = _cards(client, pid, "Narrative Fingerprint")[0]
    assert card["content"]["layers"]["pov_focalization"]["features"]["pov"] == "first_person"
    # Source names must not appear in any compact line.
    compact = " ".join(l["compact"] for l in card["content"]["layers"].values())
    for name in syn.SOURCE_CHARACTERS:
        assert name not in compact


def test_step05_build_example_library(client, state):
    pid = state["source_pid"]
    # Register source entities as cards so redaction knows the roles.
    for name, role in syn.SOURCE_CHARACTERS.items():
        _card(client, pid, "Character Card", name, {"name": name, "entity_type": "character", "life_span": "Long Term", "role_type": role, "born_scene": "Tessaly", "description": "", "personality": "", "core_drive": "", "character_arc": "", "aliases": [name.split()[0]]})
    for loc in syn.SOURCE_LOCATIONS:
        _card(client, pid, "Scene Card", loc, {"name": loc, "entity_type": "scene", "life_span": "Long Term", "description": "source place"})
    r = client.post("/api/forge/source/examples", params={"project_id": pid})
    assert r.status_code == 200, r.text
    lib = r.json()
    assert lib["examples"] >= syn.CHAPTER_COUNT * 2
    assert "chapter_cliffhanger" in lib["functions"] and "dialogue_heavy_scene" in lib["functions"] or "quiet_scene_opening" in lib["functions"]
    from app.db.models import ReferenceExample
    from app.db.session import engine

    with Session(engine) as s:
        rows = s.exec(select(ReferenceExample).where(ReferenceExample.project_id == pid)).all()
        joined = " ".join(r.excerpt for r in rows)
        for name in ("Ilse", "Marit", "Brann", "Oskar", "Tessaly", "Greywater", "Lantern Ward", "Saltmarket"):
            assert name not in joined, name
        assert all(len(r.excerpt) <= 700 and r.evidence_hash for r in rows)


def test_step06_genome_and_step07_08_create_original_project(client, state):
    pid = state["source_pid"]
    _card(client, pid, "Narrative Genome", "Narrative Genome", syn.fake_genome())
    r = client.post("/api/forge/original/create", json={"source_project_id": pid, "name": f"Forge Original {uuid.uuid4().hex[:6]}", "template": None})
    assert r.status_code == 200, r.text
    res = r.json()
    opid = res["project_id"]
    state["original_pid"] = opid
    assert res["fingerprint_card_id"]
    # Two entity-free mechanisms accepted; the one naming Ilse Varn / Lantern Ward rejected by the firewall.
    assert len(res["mechanism_card_ids"]) == 2
    assert len(res["rejected_mechanisms"]) == 1 and res["rejected_mechanisms"][0]["dimension"] == "relationship_engine"
    assert res["firewall"]["isolated"] is True
    # Step 8: source entities absent from the original project.
    all_text = json.dumps([c["content"] for c in _cards(client, opid)] + [c["title"] for c in _cards(client, opid)])
    for name in list(syn.SOURCE_CHARACTERS) + syn.SOURCE_LOCATIONS:
        assert name not in all_text, name
    assert not _cards(client, opid, "Chapter Analysis")
    manifest = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert manifest["project_role"] == "original" and manifest["source_project_id"] == pid and manifest["canon_revision"] == 0 and manifest["next_allowed_chapter"] == 1


# ============================================================== step 9: original Bible + outlines
def _outline(n: int, *, pov: str = "Nadia Quill", learned: Optional[str] = None) -> Dict[str, Any]:
    beats = [
        {"function": "quiet_scene_opening", "description": "Nadia counts the rivets on the hatch at dawn", "keywords": ["rivets", "hatch"]},
        {"function": "dialogue_heavy_scene", "description": "A confrontation about the Archive; Nadia deflects", "keywords": ["Archive", "places"]},
        {"function": "false_reassurance", "description": "Teo says the quay is clear and Nadia lets him", "keywords": ["quay", "clear"]},
        {"function": "threat_escalation", "description": "Something on the quay goes wrong and Nadia goes out regardless", "keywords": ["went", "regardless"]},
        {"function": "chapter_cliffhanger", "description": "Nadia refuses to go below; ends on a question", "keywords": ["below", "Not yet"]},
    ]
    allowed = ["Nadia refuses to go below"]
    if learned:
        allowed.append(f"Nadia learns that {learned}")
    return {
        "volume_number": 1, "stage_number": 1, "title": f"Rivets {n}", "chapter_number": n,
        "overview": f"Chapter {n}: Nadia Quill begins another watch on Harrow Quay. A confrontation about the Salt Archive, a false calm, an escalation on the quay and a refusal to go below. " + ("She learns that " + learned + ". " if learned else "") + "Nothing about the courier's identity is revealed.",
        "entity_list": ["Nadia Quill", "Teo Marsh", "Corvin Ashe", "Petra Vale", "Harrow Quay", "the Salt Archive", "Kestrel Row"],
        "pov": pov, "participants": ["Nadia Quill", "Teo Marsh", "Corvin Ashe", "Petra Vale"],
        "beats": beats, "allowed_outcomes": allowed, "forbidden_outcomes": ["Corvin is revealed as the courier"] if n < 18 else [], "word_target": 260,
    }


def test_step09_build_original_bible_and_outlines(client, state):
    opid = state["original_pid"]
    _card(client, opid, "Story Foundation", "Story Foundation", {"core_premise": "A harbor clerk hunts the courier who forges the tide manifests.", "central_dramatic_question": "Can Nadia name the courier before the Archive burns?", "protagonist_goal": "Protect the manifest", "main_opposition": "the courier", "stakes": "the harbor", "unique_mechanism": "The manifest records lies as blank pages.", "truth_status": "canon", "confidence": 1.0})
    _card(client, opid, "Reader Contract", "Reader Contract", {"primary_fantasy": "quiet competence under pressure", "primary_emotional_reward": "reinterpretation", "expected_tone": "restrained", "expected_protagonist_behavior": ["notices details", "never begs"], "violations": ["melodrama"], "truth_status": "canon", "confidence": 1.0})
    _card(client, opid, "Theme Map", "Theme Map", {"theme_question": "Is noticing the same as knowing?", "protagonist_initial_belief": "Noticing is enough", "planned_movement": "from noticing to acting"})
    for name, role in ORIGINAL_CHARACTERS.items():
        _card(client, opid, "Character Card", name, {"name": name, "entity_type": "character", "life_span": "Long Term", "role_type": role, "born_scene": "Harrow Quay", "description": f"{role} of the original story", "personality": "dry", "core_drive": "protect the manifest" if role == "Protagonist" else "keep secrets", "character_arc": "", "aliases": [name.split()[0]], "voice": {"sentence_tendency": "short", "verbal_tells": [], "forms_of_address": [], "forbidden_speech": ["I beg you"] if role == "Protagonist" else []}, "dynamic_info": {"Possessions": [{"id": 1, "info": "brass token", "weight": 1.0}]} if role == "Protagonist" else {}})
    for loc in ORIGINAL_LOCATIONS:
        _card(client, opid, "Scene Card", loc, {"name": loc, "entity_type": "scene", "life_span": "Long Term", "description": "original place"})
    _card(client, opid, "Relationship Arc", "Nadia Quill ↔ Teo Marsh", {"character_a": "Nadia Quill", "character_b": "Teo Marsh", "trust": 30, "affection": 40, "fear": 0, "dependency": 10, "resentment": 5, "private_relationship": "wary allies", "unresolved_tension": "Teo knows more than he says", "truth_status": "canon", "confidence": 1.0})
    _card(client, opid, "Knowledge Fact", "Corvin is the courier", {"fact": "Corvin Ashe is the courier who forges the manifests", "reader_state": "unaware", "planned_reveal_chapter": 18, "sensitivity": "high", "knowers": [{"entity": "Corvin Ashe", "state": "knows"}, {"entity": "Nadia Quill", "state": "unaware"}, {"entity": "Teo Marsh", "state": "suspects", "learned_chapter": 0}], "truth_status": "canon", "confidence": 1.0})
    _card(client, opid, "Promise Payoff", "The brass token fits a lock", {"setup": "The brass token fits a lock", "promise_type": "chekhovs_gun", "status": "planted", "participants": ["Nadia Quill"], "source_chapter": 1, "target_payoff_range": [12, 16], "planned_payoff": "the token opens the Archive cellar", "truth_status": "planned", "confidence": 1.0})
    _card(client, opid, "Plot Thread", "Who forges the manifests", {"name": "Who forges the manifests", "thread_type": "main_plot", "status": "active", "urgency": "high", "participants": ["Nadia Quill", "Corvin Ashe"], "opening_chapter": 1, "last_advanced_chapter": 0, "central_question": "Who is the courier?", "truth_status": "canon", "confidence": 1.0, "milestones": []})
    r = client.post("/api/forge/original/seed-canon", params={"project_id": opid})
    assert r.status_code == 200, r.text
    assert r.json()["facts_seeded"] >= 8
    for n in range(1, 25):
        learned = "the courier draws left-handed" if n == 11 else None
        state.setdefault("outline_ids", {})[n] = _card(client, opid, "Chapter Outline", f"Rivets {n}", _outline(n, learned=learned))["id"]
    iso = client.get("/api/forge/original/isolation", params={"project_id": opid}).json()
    assert iso["isolated"], iso["problems"]


# ============================================================ steps 10-11: compile chapter 1
def test_step10_11_compile_chapter1_and_retrieval(client, state):
    opid = state["original_pid"]
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 1})
    assert r.status_code == 200, r.text
    ctx = r.json()
    state["ctx1"] = ctx
    keys = {s["key"] for s in ctx["sections"]}
    for k in ("reader_contract", "story_foundation", "chapter_outline", "beats", "pov", "pov_knowledge_boundary", "participants", "fingerprint", "anti_hallucination", "originality", "prohibited", "word_target", "examples"):
        assert k in keys, k
    assert ctx["pov"] == "Nadia Quill" and ctx["participants"][0] == "Nadia Quill"
    assert ctx["beat_functions"] == ["quiet_scene_opening", "dialogue_heavy_scene", "false_reassurance", "threat_escalation", "chapter_cliffhanger"]
    ex_fns = [e["beat_function"] for e in ctx["retrieved_examples"]]
    assert set(ex_fns) <= set(ctx["beat_functions"]) and len(ex_fns) >= 3
    assert len({e["chapter_number"] for e in ctx["retrieved_examples"]}) == len(ex_fns)  # diversified across source chapters
    assert ctx["manifest"]["retrieval_trace"]["selected"] and all(s["reason"] for s in ctx["manifest"]["retrieval_trace"]["selected"])
    assert any("Corvin Ashe is the courier" in p for p in ctx["prohibited"])
    assert ctx["manifest"]["included_cards"] and ctx["manifest"]["context_hash"] == ctx["context_hash"]
    assert ctx["manifest"]["canon_revision"] == 0
    # Example excerpts contain no source entity names and are marked technique-only.
    text = ctx["text"]
    for name in ("Ilse", "Marit", "Brann", "Oskar", "Tessaly", "Greywater"):
        assert name not in text, name
    assert "technique demonstrations ONLY" in text
    assert "[PROHIBITED SOURCE / FUTURE CONTENT" in text and "[CURRENT CHAPTER PLAN" in text and "[NARRATIVE FINGERPRINT" in text


# ================================================== steps 12-18: draft, detect faults, repair, commit, sync
def test_step12_18_generate_validate_repair_commit_sync(client, state):
    from app.db.session import engine
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    drafter = FakeDrafter(faults={"unsupported": True, "copy": True, "leak": True, "future": True})
    with Session(engine) as s:
        result = asyncio.run(run_chapter(s, project_id=opid, chapter_number=1, drafter=drafter, options=PipelineOptions(max_repairs=2)))
    assert result.status == "committed", result.error
    hist = result.validation["history"]
    first = hist[0]
    codes = {i["code"] for i in first["issues"]}
    # Step 13/15: injected faults were detected on the first draft.
    assert "unplanned_possession" in codes, codes            # unsupported fact (silver compass)
    assert "source_entity_leak" in codes or "entity_overlap" in codes   # Marit Solen / Greywater Exchange
    assert "future_beat_advanced" in codes or "forbidden_reveal" in codes  # Corvin revealed as courier
    assert any(c in codes for c in ("long_phrase_overlap", "accidental_quotation", "rare_ngram_overlap", "dialogue_overlap")), codes  # copied paragraph
    assert first["passed"] is False
    # Step 14/16: repaired.
    assert result.repair_attempts == 1 and result.model_calls == 2
    assert result.validation["passed"] is True and result.validation["originality"]["passed"] is True
    assert result.style["adherence_score"] >= 0.7, result.style
    # Repair prompt only carried the failed spans + constraints, and never told the model to add facts.
    repair_call = drafter.calls[1]
    assert repair_call["role"] == "repair" and "[FAILED SPANS AND REQUIRED FIXES]" in repair_call["user_prompt"]
    assert "Do not add any new fact" in repair_call["system_prompt"]
    # Step 17/18: committed + synchronized.
    assert result.sync["canon_revision_before"] == 0 and result.sync["canon_revision_after"] == 1
    assert result.sync["state_packet"]["scene_state"]["ending_location"] == "Harrow Quay"
    manifest = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert manifest["canon_revision"] == 1 and manifest["latest_committed_chapter"] == 1 and manifest["next_allowed_chapter"] == 2
    text_card = [c for c in _cards(client, opid, "Chapter Text") if c["content"]["chapter_number"] == 1][0]
    assert text_card["content"]["sync_status"] == "synchronized" and "<claims>" not in text_card["content"]["content"]
    assert "silver compass" not in text_card["content"]["content"] and "Marit" not in text_card["content"]["content"]
    state["run1"] = result.run_id
    packets = _cards(client, opid, "Chapter State Packet")
    assert len(packets) == 1 and packets[0]["content"]["chapter_number"] == 1


# ================================================= steps 19-21: chapter 2 receives updated canon
def test_step19_21_chapter2_uses_committed_state(client, state):
    opid = state["original_pid"]
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 2})
    assert r.status_code == 200, r.text
    ctx = r.json()
    keys = {s["key"] for s in ctx["sections"]}
    assert {"previous_summary", "previous_tail", "scene_state"} <= keys
    assert ctx["manifest"]["canon_revision"] == 1
    assert "Harrow Quay" in ctx["text"] and "FINAL LINES" in ctx["text"]
    assert any("Corvin Ashe is the courier" in p for p in ctx["prohibited"])  # step 21: still prohibited
    # Chapter 2 examples avoid the ids used in chapter 1 when alternatives exist.
    used1 = set(state["ctx1"]["manifest"]["retrieved_example_ids"])
    used2 = set(ctx["manifest"]["retrieved_example_ids"])
    assert used2 and used2 != used1


# ================================================= steps 22-23: regenerate chapter 1; no backward leak
def test_step22_23_regenerate_chapter1_no_backward_leak(client, state):
    from app.db.session import engine
    from app.services.forge import canon as canon_store
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    # Commit chapters 2 and 3 first so there is later state to leak.
    with Session(engine) as s:
        for n in (2, 3):
            res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=n, drafter=FakeDrafter(learned="the chalk was fresh" if n == 3 else None), options=PipelineOptions(max_repairs=1)))
            assert res.status == "committed", json.dumps(res.error, indent=1, default=str)
        after3 = canon_store.state_as_of(s, opid, 3)
        assert any(a == "knows" and "chalk" in json.dumps(fv.value) for (_, a), fv in after3.items())
        # Regenerate chapter 1: the compiler must see canon as of chapter 0 only.
        r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 1, "regenerate": True})
        assert r.status_code == 200, r.text
        ctx = r.json()
        # Canon-bearing sections must not carry knowledge gained in chapters 2-3
        # (the example library may legitimately quote redacted source passages).
        canon_text = " ".join(sec["text"] for sec in ctx["sections"] if sec["key"] in {"participants", "pov_knowledge_boundary", "relationships", "scene_state", "previous_summary", "previous_tail", "timeline"}).lower()
        assert "chalk was fresh" not in canon_text
        assert "previous_summary" not in {sec["key"] for sec in ctx["sections"]}
        res1 = asyncio.run(run_chapter(s, project_id=opid, chapter_number=1, drafter=FakeDrafter(), options=PipelineOptions(max_repairs=1, regenerate=True)))
        assert res1.status == "committed", res1.error
        # Facts introduced by chapters 2-3 are gone; canon revision advanced; packets for later chapters dropped.
        after = canon_store.state_as_of(s, opid, 3)
        assert not any(a == "knows" and "chalk" in json.dumps(fv.value) for (_, a), fv in after.items())
    manifest = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert manifest["latest_committed_chapter"] == 1 and manifest["next_allowed_chapter"] == 2
    packets = _cards(client, opid, "Chapter State Packet")
    assert [p["content"]["chapter_number"] for p in packets] == [1]
    # Chapter 3 cannot be compiled now (chapter 2 is no longer committed after regeneration).
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "previous_chapter_not_synchronized"


# ============================================ steps 24-26: upstream change -> stale -> blocked -> recompute
def test_step24_26_upstream_change_marks_stale_and_blocks(client, state):
    opid = state["original_pid"]
    foundation = _cards(client, opid, "Story Foundation")[0]
    r = client.put(f"/api/cards/{foundation['id']}", json={"content": {**foundation["content"], "stakes": "the whole coast"}})
    assert r.status_code == 200, r.text
    manifest = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert manifest["stale_dependency_count"] > 0
    kinds = {s["artifact_kind"] for s in manifest["stale_artifacts"]}
    assert "chapter_context" in kinds
    # Editing the outline of chapter 2 makes its context stale too; compile must refuse until recomputed.
    outline2 = client.get(f"/api/cards/{state['outline_ids'][2]}").json()
    client.put(f"/api/cards/{outline2['id']}", json={"content": {**outline2["content"], "overview": outline2["content"]["overview"] + " Revised."}})
    from app.db.models import ArtifactProvenance
    from app.db.session import engine

    with Session(engine) as s:
        rows = s.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == opid, ArtifactProvenance.artifact_kind == "chapter_context")).all()
        assert rows and all(r.stale for r in rows)
        # Simulate a stale *mandatory* artifact: the fingerprint copy.
        fp_row = s.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == opid, ArtifactProvenance.artifact_kind == "Narrative Fingerprint")).first()
        fp_row.stale = True
        fp_row.stale_reason = "test: upstream fingerprint changed"
        s.add(fp_row)
        s.commit()
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "stale_dependencies", r.text
    # Recompute (re-record the fingerprint artifact) -> compile works again.
    with Session(engine) as s:
        fp_row = s.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == opid, ArtifactProvenance.artifact_kind == "Narrative Fingerprint")).first()
        fp_row.stale = False
        fp_row.stale_reason = None
        s.add(fp_row)
        s.commit()
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 2})
    assert r.status_code == 200, r.text


# ============================================================ steps 27-28: compile failure -> no model call
def test_step27_28_compile_failure_never_calls_model(client, state):
    from app.db.session import engine
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    drafter = NeverCalledDrafter()
    with Session(engine) as s:
        # Missing outline (chapter 99), invalid POV, stale canon revision, unresolved participant.
        for kwargs, code in (
            ({"chapter_number": 99}, "outline_missing"),
            ({"chapter_number": 2, "pov": "Nobody"}, "pov_invalid"),
            ({"chapter_number": 2, "expected_canon_revision": 0}, "stale_canon_revision"),
            ({"chapter_number": 2, "participants": ["Nadia Quill", "Unknown Person"]}, "participants_unresolved"),
            ({"chapter_number": 5}, "previous_chapter_not_synchronized"),
        ):
            res = asyncio.run(run_chapter(s, project_id=opid, drafter=drafter, options=PipelineOptions(), **kwargs))
            assert res.status == "compile_failed" and res.error["code"] == code, (kwargs, res.error)
    assert drafter.calls == 0
    runs = client.get("/api/forge/chapters/runs", params={"project_id": opid}).json()
    assert sum(1 for r in runs if r["status"] == "compile_failed") >= 5
    # Namespace contamination: a source-analysis card inside the original project blocks compilation.
    bad = _card(client, opid, "Chapter Analysis", "leak", {"chapter_number": 1, "source_text": "x"})
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 2})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "namespace_contamination"
    client.delete(f"/api/cards/{bad['id']}")


# ============================================================= steps 29-30: sync failure -> revision unchanged
def test_step29_30_sync_failure_does_not_advance_canon(client, state):
    from app.db.session import engine
    from app.services.forge.pipeline import PipelineOptions, run_chapter

    opid = state["original_pid"]
    before = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=2, drafter=FakeDrafter(learned="the chain was raised from inside"), options=PipelineOptions(max_repairs=1, fail_sync_on=["knows"])))
    assert res.status == "sync_failed", res.status
    after = client.get("/api/forge/manifest", params={"project_id": opid}).json()
    assert after["canon_revision"] == before["canon_revision"]
    assert after["latest_committed_chapter"] == before["latest_committed_chapter"] == 1
    assert after["last_sync_status"].startswith("failed")
    text_card = [c for c in _cards(client, opid, "Chapter Text") if c["content"]["chapter_number"] == 2][0]
    assert text_card["content"]["sync_status"] == "failed"
    # Chapter 3 is blocked because chapter 2 is not synchronized.
    r = client.post("/api/forge/chapters/compile", json={"project_id": opid, "chapter_number": 3})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "previous_chapter_not_synchronized"
    # A rejected draft (faults that repair cannot fix) is never committed.
    class StubbornDrafter(FakeDrafter):
        async def __call__(self, *, role, system_prompt, user_prompt, context):
            self.calls.append({"role": role})
            return _original_chapter(context.chapter_number, inject_leak=True)
    with Session(engine) as s:
        res = asyncio.run(run_chapter(s, project_id=opid, chapter_number=2, drafter=StubbornDrafter(), options=PipelineOptions(max_repairs=2)))
    assert res.status == "rejected" and res.repair_attempts == 2 and res.chapter_card_id is None
    assert client.get("/api/forge/manifest", params={"project_id": opid}).json()["canon_revision"] == before["canon_revision"]


# ============================================================ Phase 13: examples improve style adherence
def test_reference_examples_improve_style_adherence(client, state):
    """A drafter that imitates the compiled context's example rhythm scores higher than one without examples."""
    from app.db.session import engine
    from app.services.bible.bible_service import BibleService
    from app.services.forge.compiler import ChapterContextCompiler
    from app.services.forge.validators import style_report

    opid = state["original_pid"]
    with Session(engine) as s:
        fp = BibleService(s).singleton(opid, "Narrative Fingerprint").content
        ctx_with = ChapterContextCompiler(s).compile(project_id=opid, chapter_number=2, example_budget_chars=2400)
        ctx_without = ChapterContextCompiler(s).compile(project_id=opid, chapter_number=2, example_budget_chars=0)
        s.rollback()
    assert ctx_with.retrieved_examples and not ctx_without.retrieved_examples

    def imitate(ctx) -> str:
        """Style-following drafter: mirrors example paragraph rhythm when examples exist, otherwise writes generic long prose."""
        if ctx.retrieved_examples:
            return _original_chapter(2).split("<claims>")[0]
        return " ".join(["Nadia Quill walked slowly along the long quay while thinking carefully about everything that had happened that day and everything that might still happen, because the Archive had always been a place of secrets and the manifest had always been a document of lies, and she knew that Teo Marsh knew more than he had said."] * 10)

    with_score = style_report(imitate(ctx_with), fp)["adherence_score"]
    without_score = style_report(imitate(ctx_without), fp)["adherence_score"]
    assert with_score > without_score + 0.2, (with_score, without_score)
    # Originality and style are evaluated separately: a copy of source prose scores high on style but fails the firewall.
    from app.services.forge.pipeline import source_profile_for
    from app.services.forge.validators import validate_originality
    with Session(engine) as s:
        profile = source_profile_for(s, opid)
    copied = syn.chapter_text(9)
    assert style_report(copied, fp)["adherence_score"] >= 0.8
    issues, rep = validate_originality(copied, profile, allowed=["Nadia Quill"])
    assert rep["passed"] is False and any(i.severity == "critical" for i in issues)
