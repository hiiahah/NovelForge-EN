"""Story Memory: digests, Story So Far, continuity guard, planner, health, settings, auto-digest hook.

No LLM calls: ``llm_service.generate_structured`` is monkeypatched where a digest
or LLM continuity pass is exercised.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from typing import Any, Dict

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.schemas.story_memory import ChapterDigest, LlmContinuityFindings  # noqa: E402


@pytest.fixture
def client(app_client):
    return app_client


def _type_id(client, name: str) -> int:
    types = client.get("/api/card-types").json()
    return next(t["id"] for t in types if t["name"] == name)


def _card(client, pid, type_name, title, content, **extra):
    r = client.post(f"/api/projects/{pid}/cards", json={"title": title, "content": content, "card_type_id": _type_id(client, type_name), **extra})
    assert r.status_code in (200, 201), r.text
    return r.json()


def _digest(n: int, **over: Any) -> Dict[str, Any]:
    base = ChapterDigest(
        chapter_number=n, title=f"Chapter {n}", pov="Mira Hale", participants=["Mira Hale", "Daren Voss"], locations=["Salt Archive"],
        story_time=f"day {n}", one_line=f"Mira does thing {n}.", summary=f"In chapter {n} Mira investigates the archive with Daren and finds a clue.",
        ending_state=f"Mira and Daren stand in the archive vault after finding clue {n}.", last_paragraph_gist="Mira closes the ledger.",
        events=[{"summary": f"Mira finds clue {n}", "participants": ["Mira Hale"], "significance": "notable"}],
        hooks_opened=[{"hook": f"Who left clue {n} in the vault?", "hook_type": "question", "strength": "medium", "expected_payoff_window": "within the arc"}],
        dominant_function="discovery", tension_end=6, hook_strength=6,
    ).model_dump(mode="json")
    base.update(over)
    return base


@pytest.fixture
def project(client):
    pid = client.post("/api/projects/", json={"name": f"SM {uuid.uuid4().hex[:6]}"}).json()["data"]["id"]
    _card(client, pid, "Character Card", "Mira Hale", {"name": "Mira Hale", "aliases": ["Mira"], "core_drive": "protect the archive", "truth_status": "canon", "role_type": "Protagonist"})
    _card(client, pid, "Character Card", "Daren Voss", {"name": "Daren Voss", "aliases": ["Daren"], "core_drive": "reclaim his post", "truth_status": "canon", "role_type": "Deuteragonist"})
    _card(client, pid, "Character Card", "Sela Quint", {"name": "Sela Quint", "aliases": [], "core_drive": "trade secrets", "truth_status": "canon", "role_type": "Antagonist"})
    _card(client, pid, "Knowledge Fact", "The seal is forged", {"fact": "The royal seal is forged", "knowers": [{"entity": "Sela Quint", "state": "knows"}], "reader_state": "unaware", "planned_reveal_chapter": 30, "sensitivity": "high", "truth_status": "canon"})
    _card(client, pid, "Promise Payoff", "The locked drawer", {"setup": "A locked drawer in the vault", "planned_payoff": "It holds Mira's mother's letter", "target_payoff_range": [3, 5], "status": "planted", "truth_status": "planned", "participants": ["Mira Hale"]})
    _card(client, pid, "Plot Thread", "Daren's reinstatement", {"name": "Daren's reinstatement", "thread_type": "subplot", "central_question": "Will Daren regain his post?", "participants": ["Daren Voss"], "status": "active", "urgency": "critical", "opening_chapter": 1, "last_advanced_chapter": 1, "truth_status": "planned"})
    return pid


# ---------------------------------------------------------------- settings

def test_settings_roundtrip_via_singleton_card(client, project):
    r = client.get("/api/story-memory/settings", params={"project_id": project})
    assert r.status_code == 200
    assert r.json()["settings"]["recent_window"] == 3
    r = client.put("/api/story-memory/settings", json={"project_id": project, "settings": {**r.json()["settings"], "recent_window": 5, "recap_budget_chars": 4000, "auto_digest_on_save": False}})
    assert r.status_code == 200, r.text
    r = client.get("/api/story-memory/settings", params={"project_id": project})
    assert r.json()["settings"]["recent_window"] == 5 and r.json()["settings"]["recap_budget_chars"] == 4000
    # Stored as exactly one singleton card.
    cards = client.get(f"/api/projects/{project}/cards").json()
    assert sum(1 for c in cards if c["card_type"]["name"] == "Story Memory Settings") == 1


# ----------------------------------------------------------------- digests

def test_digest_chapter_via_llm_is_stored_and_idempotent(client, project, monkeypatch):
    from app.services.ai.core import llm_service

    calls = {"n": 0}

    async def fake_generate_structured(**kw):
        calls["n"] += 1
        return ChapterDigest.model_validate(_digest(1, source_hash="", word_count=0, stale=False))

    monkeypatch.setattr(llm_service, "generate_structured", fake_generate_structured)
    text = "Mira walked into the Salt Archive. " * 40
    ch = _card(client, project, "Chapter Text", "Chapter 1", {"title": "Chapter 1", "chapter_number": 1, "volume_number": 1, "entity_list": ["Mira Hale", "Daren Voss"], "content": text})

    r = client.post("/api/story-memory/digests", json={"project_id": project, "llm_config_id": 1, "chapter_number": 1, "chapter_card_id": ch["id"]})
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["card_type"]["name"] == "Chapter Digest"
    assert card["content"]["source_hash"] and card["content"]["word_count"] == 240 and card["content"]["stale"] is False
    assert card["content"]["chapter_card_id"] == ch["id"]
    assert calls["n"] == 1

    # Same text -> no second LLM call.
    r = client.post("/api/story-memory/digests", json={"project_id": project, "llm_config_id": 1, "chapter_number": 1})
    assert r.status_code == 200 and calls["n"] == 1
    # force -> re-extract, still one card.
    r = client.post("/api/story-memory/digests", json={"project_id": project, "llm_config_id": 1, "chapter_number": 1, "force": True})
    assert r.status_code == 200 and calls["n"] == 2
    lst = client.get("/api/story-memory/digests", params={"project_id": project}).json()
    assert [i["chapter_number"] for i in lst["items"]] == [1]
    assert lst["coverage"] == {"written": [1], "digested": [1], "missing": [], "stale": []}
    # Digest cards live under a "Story Memory" folder.
    cards = client.get(f"/api/projects/{project}/cards").json()
    folder = next(c for c in cards if c["card_type"]["name"] == "Folder" and c["title"] == "Story Memory")
    assert next(c for c in cards if c["card_type"]["name"] == "Chapter Digest")["parent_id"] == folder["id"]


def test_editing_chapter_text_marks_digest_stale_and_coverage_reports_it(client, project):
    ch = _card(client, project, "Chapter Text", "Chapter 2", {"title": "Chapter 2", "chapter_number": 2, "entity_list": ["Mira Hale"], "content": "Original text. " * 30})
    from app.services.forge.textmetrics import sha256_text

    r = client.put("/api/story-memory/digests/2", params={"project_id": project}, json=_digest(2, source_hash=sha256_text(ch["content"]["content"].strip()), chapter_card_id=ch["id"]))
    assert r.status_code == 200, r.text
    assert client.get("/api/story-memory/digests", params={"project_id": project}).json()["coverage"]["stale"] == []

    # Auto-digest is on by default but no model is configured for the card -> only stale marking happens.
    r = client.put(f"/api/cards/{ch['id']}", json={"content": {**ch["content"], "content": "Changed text. " * 30}})
    assert r.status_code == 200, r.text
    cov = client.get("/api/story-memory/digests", params={"project_id": project}).json()["coverage"]
    assert cov["stale"] == [2]
    d = client.get("/api/story-memory/digests/2", params={"project_id": project}).json()
    assert d["stale"] is True


def test_auto_digest_runs_in_background_when_model_configured(client, project, monkeypatch):
    from app.services.ai.core import llm_service

    done = {"n": 0}

    async def fake_generate_structured(**kw):
        done["n"] += 1
        return ChapterDigest.model_validate(_digest(7))

    monkeypatch.setattr(llm_service, "generate_structured", fake_generate_structured)
    client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"auto_digest_on_save": True, "auto_digest_min_words": 50, "digest_llm_config_id": 1}})
    ch = _card(client, project, "Chapter Text", "Chapter 7", {"title": "Chapter 7", "chapter_number": 7, "entity_list": ["Mira Hale"], "content": "Short."})
    r = client.put(f"/api/cards/{ch['id']}", json={"content": {**ch["content"], "content": "Mira read the letter at last. " * 30}})
    assert r.status_code == 200
    for _ in range(80):
        if client.get("/api/story-memory/digests", params={"project_id": project}).json()["coverage"]["digested"].count(7):
            break
        time.sleep(0.1)
    assert done["n"] == 1
    assert 7 in client.get("/api/story-memory/digests", params={"project_id": project}).json()["coverage"]["digested"]
    # Below the min-words threshold nothing is scheduled.
    ch2 = _card(client, project, "Chapter Text", "Chapter 8", {"title": "Chapter 8", "chapter_number": 8, "entity_list": [], "content": ""})
    client.put(f"/api/cards/{ch2['id']}", json={"content": {**ch2["content"], "content": "Tiny."}})
    time.sleep(0.3)
    assert done["n"] == 1


def test_batch_digests_missing_and_stale_only(client, project, monkeypatch):
    from app.services.ai.core import llm_service

    seen = []

    async def fake_generate_structured(**kw):
        n = int(kw["user_prompt"].split("Chapter number: ")[1].split()[0])
        seen.append(n)
        return ChapterDigest.model_validate(_digest(n))

    monkeypatch.setattr(llm_service, "generate_structured", fake_generate_structured)
    client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"auto_digest_on_save": False}})
    for n in (11, 12, 13):
        _card(client, project, "Chapter Text", f"Chapter {n}", {"title": f"Chapter {n}", "chapter_number": n, "entity_list": ["Mira Hale"], "content": f"Text of chapter {n}. " * 30})
    r = client.post("/api/story-memory/digests/batch", json={"project_id": project, "llm_config_id": 1})
    assert r.status_code == 200, r.text
    assert set(r.json()["digested"]) >= {11, 12, 13} and not r.json()["failed"]
    # Second run: nothing missing/stale.
    seen.clear()
    r = client.post("/api/story-memory/digests/batch", json={"project_id": project, "llm_config_id": 1})
    assert r.json()["digested"] == [] and seen == []


# -------------------------------------------------------------- story so far

def _seed_digests(client, project, n_from: int, n_to: int, **over):
    for n in range(n_from, n_to + 1):
        r = client.put(f"/api/story-memory/digests/{n}", params={"project_id": project}, json=_digest(n, **over))
        assert r.status_code == 200, r.text


def test_story_so_far_tiers_carry_forward_and_dangling_hooks(client, project):
    client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"recent_window": 2, "mid_window": 3, "hook_overdue_chapters": 4, "recap_budget_chars": 20000}})
    _seed_digests(client, project, 1, 8)
    # Chapter 3 moves Daren and kills Sela; chapter 5 closes hook 1.
    client.put("/api/story-memory/digests/3", params={"project_id": project}, json=_digest(3, state_changes=[
        {"entity": "Daren Voss", "kind": "location", "before": "Salt Archive", "after": "the northern road"},
        {"entity": "Sela Quint", "kind": "alive_dead", "before": "alive", "after": "dead, drowned in the canal"},
        {"entity": "Mira Hale", "kind": "possession", "before": "carries the seal", "after": "lost the seal in the river"},
    ]))
    client.put("/api/story-memory/digests/5", params={"project_id": project}, json=_digest(5, hooks_closed=[{"hook": "Who left clue 1 in the vault?", "resolution": "It was Daren", "complete": True}]))

    r = client.post("/api/story-memory/story-so-far", json={"project_id": project, "next_chapter": 9})
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["through_chapter"] == 8 and s["next_chapter"] == 9
    names = [t["name"] for t in s["tiers"]]
    assert names == ["distant", "mid", "recent"]
    assert [t["chapter_range"] for t in s["tiers"]] == [[1, 3], [4, 6], [7, 8]]
    # Carry-forward folds state in chapter order.
    carry = {e["entity"]: e for e in s["carry_forward"]}
    assert carry["Sela Quint"]["alive"] is False
    assert carry["Daren Voss"]["location"] == "the northern road" or carry["Daren Voss"]["location"] == "Salt Archive"
    assert "possession" in carry["Mira Hale"]["states"]
    # Hook 1 closed in ch.5; hooks from 2..8 remain, overdue ones first.
    hooks = s["dangling_hooks"]
    assert not any("clue 1 " in h["hook"] for h in hooks)
    assert hooks[0]["overdue"] is True and hooks[0]["opened_chapter"] == 2
    assert s["last_ending_state"].startswith("Mira and Daren stand in the archive vault after finding clue 8")
    assert "[Story So Far — through chapter 8; you are writing chapter 9]" in s["text"]
    assert "DEAD" in s["text"] and "OVERDUE" in s["text"]


def test_story_so_far_respects_budget_dropping_distant_first(client, project):
    client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"recent_window": 2, "mid_window": 4}})
    _seed_digests(client, project, 1, 12)
    full = client.post("/api/story-memory/story-so-far", json={"project_id": project, "next_chapter": 13, "budget_chars": 60000}).json()
    assert [t["name"] for t in full["tiers"]] == ["distant", "mid", "recent"]
    tight = client.post("/api/story-memory/story-so-far", json={"project_id": project, "next_chapter": 13, "budget_chars": 1400}).json()
    assert tight["used_chars"] <= 1400 + 200  # recent ending is never dropped, so a tiny slack is acceptable
    assert "distant" not in [t["name"] for t in tight["tiers"]]
    assert any(t["name"] == "recent" for t in tight["tiers"])
    assert "Where the previous chapter left off" in tight["text"]


def test_story_so_far_only_counts_chapters_before_the_one_being_written(client, project):
    _seed_digests(client, project, 1, 6)
    s = client.post("/api/story-memory/story-so-far", json={"project_id": project, "next_chapter": 4}).json()
    assert s["digested_chapters"] == [1, 2, 3]
    assert s["last_ending_state"].endswith("clue 3.")


def test_context_assemble_includes_story_memory_and_brief(client, project):
    _seed_digests(client, project, 1, 3)
    r = client.post("/api/context/assemble", json={"project_id": project, "chapter_number": 4, "participants": ["Mira"], "include_chapter_brief": True})
    assert r.status_code == 200, r.text
    ctx = r.json()
    assert ctx["story_memory"] and ctx["story_memory"]["next_chapter"] == 4
    assert "Story So Far" in ctx["story_memory"]["text"]
    assert ctx["chapter_brief"] and ctx["chapter_brief"]["chapter_number"] == 4
    assert ctx["budget_stats"]["story_memory_used"] > 0
    # Opt out.
    r = client.post("/api/context/assemble", json={"project_id": project, "chapter_number": 4, "participants": ["Mira"], "include_story_memory": False})
    assert r.json()["story_memory"] is None


def test_continuation_context_enrichment_injects_memory_even_without_participants(client, project):
    from sqlmodel import Session

    from app.db.session import engine
    from app.schemas.ai import ContinuationRequest
    from app.services.ai.generation.continuation_context_service import enrich_continuation_context_info

    _seed_digests(client, project, 1, 2)
    with Session(engine) as session:
        req = ContinuationRequest(previous_content="", llm_config_id=1, project_id=project, chapter_number=3, participants=[], context_info="[Reference Context]\nOutline: Mira returns.")
        out = enrich_continuation_context_info(session, req)
        assert out.startswith("[Reference Context]")
        assert "[Story So Far — through chapter 2; you are writing chapter 3]" in out
        assert "[Next Chapter Brief — chapter 3]" in out
        # Project setting off -> not injected.
        client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"inject_into_continuation": False, "inject_brief_into_continuation": False}})
        out2 = enrich_continuation_context_info(session, req.model_copy())
        assert "[Story So Far" not in out2 and "[Next Chapter Brief" not in out2
        # Explicit request flag wins over project settings.
        out3 = enrich_continuation_context_info(session, req.model_copy(update={"include_story_memory": True}))
        assert "[Story So Far" in out3


# --------------------------------------------------------- continuity guard

def test_continuity_guard_detects_dead_entity_prohibited_reveal_and_hooks(client, project):
    _seed_digests(client, project, 1, 2)
    client.put("/api/story-memory/digests/2", params={"project_id": project}, json=_digest(2,
        state_changes=[{"entity": "Sela Quint", "kind": "alive_dead", "before": "alive", "after": "dead"}],
        hooks_opened=[{"hook": "Will the courier reach the capital before the gates close?", "hook_type": "deadline", "strength": "strong", "expected_payoff_window": "next chapter"}],
    ))
    draft = (
        "Mira Hale stood in the archive vault. Daren Voss watched the door.\n\n"
        "Sela Quint smiled and stepped out of the shadows, very much alive. \"You found it,\" Sela Quint said.\n\n"
        "Mira knew now: the royal seal is forged, a forgery pressed by Sela's own hand. She said nothing about the courier."
    )
    r = client.post("/api/story-memory/continuity/check", json={"project_id": project, "draft": draft, "chapter_number": 3, "participants": ["Mira Hale", "Daren Voss", "Sela Quint"], "pov": "Mira Hale"})
    assert r.status_code == 200, r.text
    rep = r.json()
    codes = {i["code"] for i in rep["issues"]}
    assert "dead_entity" in codes, rep
    assert "prohibited_reveal" in codes, rep
    assert rep["verdict"] == "block" and rep["score"] < 60
    dead = next(i for i in rep["issues"] if i["code"] == "dead_entity")
    assert dead["span"] and "Sela Quint" in dead["excerpt"]
    assert set(rep["checks_run"]) >= {"prohibited_reveal", "dead_entity", "unknown_entity", "dropped_strong_hook", "time_inversion"}


def test_continuity_guard_clean_draft_and_dropped_hook(client, project):
    _seed_digests(client, project, 1, 1)
    client.put("/api/story-memory/digests/1", params={"project_id": project}, json=_digest(1,
        hooks_opened=[{"hook": "Will the courier reach the capital before the gates close?", "hook_type": "deadline", "strength": "strong", "expected_payoff_window": "next chapter"}],
    ))
    draft = "Mira Hale walked through the archive. Daren Voss followed. They spoke of the ledgers and of nothing else."
    rep = client.post("/api/story-memory/continuity/check", json={"project_id": project, "draft": draft, "chapter_number": 2, "participants": ["Mira Hale", "Daren Voss"], "pov": "Mira Hale"}).json()
    assert {i["code"] for i in rep["issues"]} == {"dropped_strong_hook"}, rep
    assert rep["verdict"] == "review"
    draft_ok = draft + " Then the courier's horn sounded beyond the capital gates."
    rep = client.post("/api/story-memory/continuity/check", json={"project_id": project, "draft": draft_ok, "chapter_number": 2, "participants": ["Mira Hale", "Daren Voss"], "pov": "Mira Hale"}).json()
    assert rep["issues"] == [] and rep["verdict"] == "clean" and rep["score"] == 100


def test_continuity_guard_unknown_entity_and_forbidden_outcome(client, project):
    draft = "Mira Hale met Corvin Ashe by the gate. Corvin Ashe bowed. Corvin Ashe laughed. Then Mira broke the ancient seal and claimed the throne of the city."
    rep = client.post("/api/story-memory/continuity/check", json={"project_id": project, "draft": draft, "chapter_number": 2, "participants": ["Mira Hale"], "pov": "Mira Hale", "outline": {"forbidden_outcomes": ["Mira claims the throne of the city"]}}).json()
    codes = {i["code"] for i in rep["issues"]}
    assert "unknown_entity" in codes and "forbidden_outcome" in codes, rep


def test_continuity_guard_llm_pass_merges_findings(client, project, monkeypatch):
    from app.services.ai.core import llm_service

    async def fake_generate_structured(**kw):
        assert "[Story memory]" in kw["user_prompt"] and "[Draft]" in kw["user_prompt"]
        return LlmContinuityFindings(issues=[{"code": "contradiction", "severity": "high", "message": "Daren is on the northern road since ch.1", "excerpt": "Daren Voss followed", "suggestion": "Remove Daren"}], honoured_hooks=["clue 1"], notes=["Tone matches."])

    monkeypatch.setattr(llm_service, "generate_structured", fake_generate_structured)
    _seed_digests(client, project, 1, 1)
    draft = "Mira Hale walked through the archive. Daren Voss followed."
    rep = client.post("/api/story-memory/continuity/check", json={"project_id": project, "draft": draft, "chapter_number": 2, "participants": ["Mira Hale", "Daren Voss"], "pov": "Mira Hale", "use_llm": True, "llm_config_id": 1}).json()
    llm = [i for i in rep["issues"] if i["source"] == "llm"]
    assert any(i["code"] == "contradiction" and i["span"] for i in llm), rep
    assert any(i["code"] == "note" for i in llm)
    assert "llm_contradictions" in rep["checks_run"] and rep["verdict"] == "review"


# ------------------------------------------------------------------ planner

def test_next_chapter_brief_collects_overdue_promises_threads_hooks_and_prohibitions(client, project):
    client.put("/api/story-memory/settings", json={"project_id": project, "settings": {"hook_overdue_chapters": 2}})
    _seed_digests(client, project, 1, 6)
    _card(client, project, "Chapter Outline", "Ch 7", {"volume_number": 1, "stage_number": 1, "title": "The letter", "chapter_number": 7, "overview": "Mira finally opens the drawer and reads the letter while Daren keeps watch in the corridor outside the vault.", "entity_list": ["Mira Hale", "Daren Voss"], "pov": "Mira Hale", "forbidden_outcomes": ["Daren regains his post"], "allowed_outcomes": ["Mira learns her mother's name"]})
    r = client.post("/api/story-memory/brief", json={"project_id": project, "chapter_number": 7})
    assert r.status_code == 200, r.text
    b = r.json()
    kinds_must = {i["kind"] for i in b["must_address"]}
    kinds_should = {i["kind"] for i in b["should_consider"]}
    kinds_avoid = {i["kind"] for i in b["avoid"]}
    assert {"continue_from", "overdue_promise", "outline", "dangling_hook"} <= kinds_must, b
    assert "neglected_thread" in kinds_must or "neglected_thread" in kinds_should
    assert "allowed_outcome" in kinds_should
    assert {"prohibited_knowledge", "forbidden_outcome"} <= kinds_avoid, b
    assert "[Next Chapter Brief — chapter 7]" in b["text"] and "MUST address" in b["text"] and "AVOID" in b["text"]
    assert b["must_address"][0]["priority"] <= b["must_address"][-1]["priority"]


def test_brief_rhythm_advice_on_repeated_function(client, project):
    _seed_digests(client, project, 1, 3, dominant_function="setup", tension_end=2, hook_strength=2)
    b = client.post("/api/story-memory/brief", json={"project_id": project, "chapter_number": 4}).json()
    assert any("Three chapters in a row" in a for a in b["rhythm_advice"])
    assert any("ended quietly" in a for a in b["rhythm_advice"])


# ------------------------------------------------------------------- health

def test_bible_health_scores_and_improves_with_digests(client, project):
    _card(client, project, "Chapter Text", "Chapter 1", {"title": "Chapter 1", "chapter_number": 1, "entity_list": ["Mira Hale"], "content": "Words. " * 100})
    before = client.get("/api/story-memory/health", params={"project_id": project}).json()
    assert 0 <= before["score"] <= 100 and before["grade"] in "ABCDF"
    keys = {d["key"] for d in before["dimensions"]}
    assert keys == {"foundation", "characters", "relationships", "ledgers", "hygiene", "memory", "audits"}
    mem = next(d for d in before["dimensions"] if d["key"] == "memory")
    assert mem["score"] == 0 and mem["fix_hint"]
    _seed_digests(client, project, 1, 1)
    after = client.get("/api/story-memory/health", params={"project_id": project}).json()
    assert next(d for d in after["dimensions"] if d["key"] == "memory")["score"] == 100
    assert after["score"] > before["score"]


# ------------------------------------------------------------ schema hygiene

def test_digest_schema_excludes_system_fields_for_ai():
    from app.utils.schema_utils import filter_schema_for_ai

    s = filter_schema_for_ai(ChapterDigest.model_json_schema())
    for f in ("source_hash", "stale", "word_count", "chapter_card_id", "digested_at", "llm_config_id"):
        assert f not in s["properties"], f
    assert "ending_state" in s["properties"] and "hooks_opened" in s["properties"]


def test_card_types_and_prompts_bootstrapped(client):
    names = {t["name"] for t in client.get("/api/card-types").json()}
    assert {"Chapter Digest", "Story Memory Settings"} <= names
    digest_type = next(t for t in client.get("/api/card-types").json() if t["name"] == "Chapter Digest")
    assert digest_type["is_singleton"] is False and digest_type["is_ai_enabled"] is False
    prompts = {p["name"] for p in client.get("/api/prompts/", params={"limit": 500}).json()["data"]}
    assert {"Chapter Digest Extraction", "Continuity Guard"} <= prompts
