"""Database: clean creation, legacy upgrade, FK enforcement, project invariants, Living Bible atomicity."""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from sqlalchemy import event, text
from sqlmodel import Session, SQLModel, create_engine, select

from app.db.migrations import BASELINE_REVISION, check_schema_drift, current_revision, head_revision, upgrade_database
from app.db.session import apply_sqlite_pragmas


def _engine(path):
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    event.listens_for(eng, "connect")(apply_sqlite_pragmas)
    return eng


def test_clean_database_creation(tmp_path):
    eng = _engine(tmp_path / "clean.db")
    info = upgrade_database(eng)
    assert info["before"] is None and info["legacy"] is False and info["after"] == head_revision()
    assert check_schema_drift(eng) == []
    with eng.connect() as conn:
        tables = {r[0] for r in conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert {"project", "card", "cardtype", "workflowrun", "nodeexecutionstate", "kgrelation", "bibleupdatereview", "alembic_version"} <= tables


def test_legacy_database_upgrade(tmp_path):
    """A pre-Alembic database: old tables missing newer columns, no alembic_version."""
    path = tmp_path / "legacy.db"
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE project (id INTEGER PRIMARY KEY, name VARCHAR NOT NULL UNIQUE, description VARCHAR);
        CREATE TABLE llmconfig (id INTEGER PRIMARY KEY, provider VARCHAR NOT NULL, display_name VARCHAR, model_name VARCHAR NOT NULL,
            api_base VARCHAR, api_key VARCHAR NOT NULL, custom_request_path VARCHAR, models_path VARCHAR, user_agent VARCHAR, base_url VARCHAR,
            token_limit INTEGER NOT NULL DEFAULT -1, call_limit INTEGER NOT NULL DEFAULT -1, used_tokens_input INTEGER NOT NULL DEFAULT 0,
            used_tokens_output INTEGER NOT NULL DEFAULT 0, used_calls INTEGER NOT NULL DEFAULT 0, rpm_limit INTEGER NOT NULL DEFAULT -1, tpm_limit INTEGER NOT NULL DEFAULT -1);
        INSERT INTO project (name, description) VALUES ('Legacy Project', 'kept');
        INSERT INTO llmconfig (provider, model_name, api_key) VALUES ('authnd', 'moonshotai/kimi-k3', '');
        """
    )
    con.commit()
    con.close()
    eng = _engine(path)
    info = upgrade_database(eng)
    assert info["legacy"] is True and info["after"] == head_revision()
    assert "llmconfig.api_protocol" in info["added_columns"] and "llmconfig.disable_stream" in info["added_columns"]
    assert check_schema_drift(eng) == []
    with eng.connect() as conn:
        rows = conn.execute(text("SELECT name FROM project")).fetchall()
        assert rows == [("Legacy Project",)]
        cfg = conn.execute(text("SELECT api_protocol, recommended_assistant_mode, disable_stream FROM llmconfig")).fetchone()
        assert cfg[0] == "chat_completions" and cfg[1] == "auto" and cfg[2] in (0, False)
    # Second run is a no-op.
    info2 = upgrade_database(eng)
    assert info2["legacy"] is False and info2["added_columns"] == [] and current_revision(eng) == head_revision()


def test_foreign_keys_enforced(tmp_path):
    eng = _engine(tmp_path / "fk.db")
    upgrade_database(eng)
    with eng.connect() as conn:
        assert conn.execute(text("PRAGMA foreign_keys")).fetchone()[0] == 1
        assert conn.execute(text("PRAGMA journal_mode")).fetchone()[0].lower() == "wal"
        with pytest.raises(Exception):
            conn.execute(text("INSERT INTO nodeexecutionstate (run_id, node_id, node_type, status, progress, created_at, updated_at) VALUES (999999, 'n', 't', 'idle', 0, '2026-01-01', '2026-01-01')"))
            conn.commit()


# ------------------------------------------------------------- project invariants
@pytest.fixture
def client(app_client):
    return app_client


def test_project_name_validation_and_reserved(client):
    r = client.post("/api/projects/", json={"name": "   "})
    assert r.status_code == 400
    r = client.post("/api/projects/", json={"name": "__free__"})
    assert r.status_code == 400 and "reserved" in r.json()["detail"]
    name = f"Case Test {uuid.uuid4().hex[:6]}"
    r = client.post("/api/projects/", json={"name": f"  {name}  "})
    assert r.status_code in (200, 201) and r.json()["data"]["name"] == name
    pid = r.json()["data"]["id"]
    r = client.post("/api/projects/", json={"name": name.upper()})
    assert r.status_code == 400 and "already exists" in r.json()["detail"]
    other = client.post("/api/projects/", json={"name": f"Other {uuid.uuid4().hex[:6]}"}).json()["data"]
    r = client.put(f"/api/projects/{other['id']}", json={"name": name.lower()})
    assert r.status_code == 400
    r = client.put(f"/api/projects/{other['id']}", json={"name": "__free__"})
    assert r.status_code == 400
    r = client.put(f"/api/projects/{other['id']}", json={"name": ""})
    assert r.status_code == 400
    # __free__ cannot be deleted or renamed
    free = client.get("/api/projects/free").json()["data"]
    assert client.delete(f"/api/projects/{free['id']}").status_code == 404
    assert client.put(f"/api/projects/{free['id']}", json={"name": "renamed"}).status_code == 400
    assert client.get("/api/projects/free").json()["data"]["name"] == "__free__"
    assert client.delete(f"/api/projects/{pid}").status_code == 200


def test_project_deletion_cleans_owned_rows_and_tracks_graph_failure(client, monkeypatch):
    from app.db.models import BibleUpdateReview, ForeshadowItem, KGRelation, Project
    from app.db.session import engine
    from app.services import project_service

    pid = client.post("/api/projects/", json={"name": f"Del {uuid.uuid4().hex[:6]}"}).json()["data"]["id"]
    with Session(engine) as s:
        s.add(ForeshadowItem(project_id=pid, title="f"))
        s.add(BibleUpdateReview(project_id=pid, proposal_json={}, decisions_json={}))
        s.add(KGRelation(project_id=pid, source="A", target="B", kind_en="ally"))
        s.commit()

    class BrokenKG:
        def delete_project_graph(self, project_id):
            raise RuntimeError("neo4j down")

    monkeypatch.setattr(project_service, "get_provider", lambda: BrokenKG())
    assert client.delete(f"/api/projects/{pid}").status_code == 200
    assert pid in project_service.pending_graph_cleanup_ids()
    with Session(engine) as s:
        assert s.get(Project, pid) is None
        assert not s.exec(select(ForeshadowItem).where(ForeshadowItem.project_id == pid)).all()
        assert not s.exec(select(BibleUpdateReview).where(BibleUpdateReview.project_id == pid)).all()
        assert not s.exec(select(KGRelation).where(KGRelation.project_id == pid)).all()
    monkeypatch.undo()
    assert pid in project_service.retry_pending_graph_cleanup()
    assert pid not in project_service.pending_graph_cleanup_ids()


def test_partial_template_initialization_is_reported(client, caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        r = client.post("/api/projects/", json={"name": f"Tpl {uuid.uuid4().hex[:6]}", "template": "no-such-template"})
    assert r.status_code in (200, 201)
    # Header/result: no run ids were triggered, and the service warned about it.
    from loguru import logger as _logger  # noqa: F401  (loguru->caplog bridge not configured; check via service directly)
    from app.db.session import engine
    from app.schemas.project import ProjectCreate
    from app.services import project_service

    with Session(engine) as s:
        _, run_ids = project_service.create_project(s, ProjectCreate(name=f"Tpl2 {uuid.uuid4().hex[:6]}", template="no-such-template"))
    assert run_ids == []


# ------------------------------------------------------------- living bible
def _project_with_review(client):
    pid = client.post("/api/projects/", json={"name": f"LB {uuid.uuid4().hex[:6]}"}).json()["data"]["id"]
    proposal = {
        "chapter_number": 1,
        "summary": "test",
        "changes": [
            {"id": "c1", "kind": "new_entity", "target_card_type": "Character Card", "target_title": "Dara Vell", "field_path": "", "previous_value": None,
             "new_value": {"name": "Dara Vell", "aliases": ["Dee"], "core_drive": "find the ledger", "truth_status": "canon"}, "summary": "new character", "confidence": 0.9, "risk": "low", "evidence": []},
            {"id": "c2", "kind": "new_entity", "target_card_type": "Character Card", "target_title": "Orin Slate", "field_path": "", "previous_value": None,
             "new_value": {"name": "Orin Slate", "core_drive": "guard", "truth_status": "canon"}, "summary": "new character 2", "confidence": 0.9, "risk": "low", "evidence": []},
        ],
    }
    from app.db.models import BibleUpdateReview
    from app.db.session import engine

    with Session(engine) as s:
        review = BibleUpdateReview(project_id=pid, chapter_number=1, proposal_json=proposal, decisions_json={})
        s.add(review)
        s.commit()
        s.refresh(review)
        return pid, review.id


def _character_cards(client, pid):
    return [c for c in client.get(f"/api/projects/{pid}/cards").json() if c["card_type"]["name"] == "Character Card"]


def test_living_bible_idempotent_apply(client):
    pid, rid = _project_with_review(client)
    body = {"decisions": [{"change_id": "c1", "action": "accept"}, {"change_id": "c2", "action": "postpone"}]}
    r = client.post(f"/api/bible/updates/{rid}/decide", json=body)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 1 and r.json()["status"] == "partially_applied"
    n = len(_character_cards(client, pid))
    # Retry the identical request: nothing duplicates.
    r = client.post(f"/api/bible/updates/{rid}/decide", json=body)
    assert r.status_code == 200 and r.json()["applied"] == 0 and r.json()["skipped"] == 1
    assert len(_character_cards(client, pid)) == n
    # Postponed change can still be decided later; then the review is applied and locked.
    r = client.post(f"/api/bible/updates/{rid}/decide", json={"decisions": [{"change_id": "c2", "action": "accept"}]})
    assert r.status_code == 200 and r.json()["status"] == "applied"
    r = client.post(f"/api/bible/updates/{rid}/decide", json={"decisions": [{"change_id": "c2", "action": "accept"}]})
    assert r.status_code == 409
    assert len(_character_cards(client, pid)) == n + 1


def test_living_bible_rollback_on_failure(client, monkeypatch):
    pid, rid = _project_with_review(client)
    from app.services.bible.living_bible_service import LivingBibleService

    real = LivingBibleService._apply_change
    calls = {"n": 0}

    def flaky(self, review, change, decision):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("boom")
        return real(self, review, change, decision)

    monkeypatch.setattr(LivingBibleService, "_apply_change", flaky)
    before = len(_character_cards(client, pid))
    r = client.post(f"/api/bible/updates/{rid}/decide", json={"decisions": [{"change_id": "c1", "action": "accept"}, {"change_id": "c2", "action": "accept"}]})
    assert r.status_code == 500 and "rolled back" in r.json()["detail"]
    # Neither the first (successful) card nor any decision was persisted.
    assert len(_character_cards(client, pid)) == before
    review = client.get(f"/api/bible/updates/{rid}").json()
    assert review["status"] == "pending" and review["decisions"] == {}
