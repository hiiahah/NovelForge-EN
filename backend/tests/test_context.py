"""Context Compiler + context assembly + graph provider semantics."""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

import pytest
from sqlmodel import Session

from app.services.kg_provider import SQLModelKGProvider, build_subgraph, _register_aliases, _ALIAS_TABLES


@pytest.fixture
def client(app_client):
    return app_client


def _type_id(client, name: str) -> int:
    types = client.get("/api/card-types").json()
    return next(t["id"] for t in types if t["name"] == name)


def _card(client, pid, type_name, title, content):
    r = client.post(f"/api/projects/{pid}/cards", json={"title": title, "content": content, "card_type_id": _type_id(client, type_name)})
    assert r.status_code in (200, 201), r.text
    return r.json()


@pytest.fixture
def bible_project(client):
    pid = client.post("/api/projects/", json={"name": f"Ctx {uuid.uuid4().hex[:6]}"}).json()["data"]["id"]
    _card(client, pid, "Character Card", "Mira Hale", {"name": "Mira Hale", "aliases": ["Mira", "the Archivist"], "core_drive": "protect the archive", "truth_status": "canon", "confidence": 0.9})
    _card(client, pid, "Character Card", "Daren Voss", {"name": "Daren Voss", "aliases": ["Daren"], "core_drive": "reclaim his post", "truth_status": "canon", "confidence": 0.9})
    _card(client, pid, "Character Card", "Sela Quint", {"name": "Sela Quint", "aliases": [], "core_drive": "trade secrets", "truth_status": "canon", "confidence": 0.9})
    _card(client, pid, "Relationship Arc", "Daren ↔ Mira", {"character_a": "the Archivist", "character_b": "Daren", "public_relationship": "guard and ward", "private_relationship": "growing trust", "trust": 4, "truth_status": "canon"})
    _card(client, pid, "Knowledge Fact", "The seal is forged", {"fact": "The royal seal is forged", "knowers": [{"entity": "Sela Quint", "state": "knows"}], "reader_state": "unaware", "planned_reveal_chapter": 30, "sensitivity": "high", "truth_status": "canon"})
    _card(client, pid, "Knowledge Fact", "Mira knows the code", {"fact": "Mira knows the vault code", "knowers": [{"entity": "Mira", "state": "knows"}], "reader_state": "knows", "truth_status": "canon"})
    _card(client, pid, "Knowledge Fact", "Old rumor", {"fact": "The vault was moved north", "knowers": [{"entity": "Mira", "state": "knows"}], "reader_state": "knows", "truth_status": "obsolete"})
    _card(client, pid, "Knowledge Fact", "Planned twist", {"fact": "Daren has a twin", "knowers": [{"entity": "Daren", "state": "knows"}], "reader_state": "knows", "truth_status": "planned"})
    return pid


def _compile(client, pid, **kw):
    r = client.post("/api/context/assemble", json={"project_id": pid, "chapter_number": 5, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_explicit_pov_included_even_if_not_participant(client, bible_project):
    ctx = _compile(client, bible_project, participants=["Daren"], pov="Mira")
    blocks = ctx["bible_context"]["blocks"]
    chars = {b["title"]: b for b in blocks if b["section"] == "Characters"}
    assert "Mira Hale" in chars and chars["Mira Hale"]["reason"] == "POV character"
    assert "Daren Voss" in chars
    assert ctx["budget_stats"]["pov"] == "Mira Hale" and ctx["budget_stats"]["participants"][0] == "Mira Hale"


def test_aliases_and_case_normalization(client, bible_project):
    ctx = _compile(client, bible_project, participants=["THE ARCHIVIST", "daren voss"])
    blocks = ctx["bible_context"]["blocks"]
    assert any(b["section"] == "Relationships" and b["reason"] == "Both characters present" for b in blocks), blocks
    assert {b["title"] for b in blocks if b["section"] == "Characters"} == {"Mira Hale", "Daren Voss"}


def test_empty_participants_does_not_dump_whole_bible(client, bible_project):
    ctx = _compile(client, bible_project, participants=[])
    sections = {b["section"] for b in (ctx.get("bible_context") or {}).get("blocks", [])}
    # No participants -> no character consistency blocks (only always-on singletons/high-sensitivity prohibitions).
    assert "Characters" not in sections


def test_truth_status_filtering_and_labels(client, bible_project):
    ctx = _compile(client, bible_project, participants=["Mira", "Daren"], pov="Mira")
    texts = [b["text"] for b in ctx["bible_context"]["blocks"]]
    assert not any("moved north" in t for t in texts)  # obsolete dropped
    assert any(t.startswith("[planned]") and "twin" in t for t in texts)  # planned labelled
    assert not any(t.startswith("[canon]") for t in texts)


def test_prohibited_knowledge_for_pov(client, bible_project):
    ctx = _compile(client, bible_project, participants=["Mira", "Sela Quint"], pov="Mira")
    assert any("forged" in p for p in ctx["bible_context"]["prohibited"])
    assert not any("forged" in b["text"] for b in ctx["bible_context"]["blocks"])
    # Sela knows it: as POV the fact is available.
    ctx2 = _compile(client, bible_project, participants=["Sela Quint"], pov="Sela Quint")
    assert not any("forged" in p for p in ctx2["bible_context"]["prohibited"])
    assert any("forged" in b["text"] for b in ctx2["bible_context"]["blocks"])


def test_strict_rendered_budget_and_used_chars(client, bible_project):
    ctx = _compile(client, bible_project, participants=["Mira", "Daren"], pov="Mira", bible_quota_chars=300)
    bc = ctx["bible_context"]
    assert bc["budget_chars"] == 300
    assert bc["used_chars"] == len(bc["text"])
    assert bc["used_chars"] <= 300 or len(bc["blocks"]) == 1
    assert bc["dropped"] >= 1
    assert ctx["budget_stats"]["bible_used"] == bc["used_chars"]


def test_settings_driven_quota_and_request_override(client, bible_project, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings.context, "bible_quota_chars", 1234)
    ctx = _compile(client, bible_project, participants=["Mira"])
    assert ctx["budget_stats"]["bible_quota"] == 1234
    ctx = _compile(client, bible_project, participants=["Mira"], bible_quota_chars=900, facts_quota_chars=700)
    assert ctx["budget_stats"]["bible_quota"] == 900 and ctx["budget_stats"]["facts_quota"] == 700


def test_provider_failure_is_an_error_not_empty_context(client, bible_project, monkeypatch):
    from app.services import context_service

    class Broken:
        def ingest_aliases(self, *a, **k):
            return None

        def query_subgraph(self, **k):
            raise RuntimeError("graph down")

    monkeypatch.setattr(context_service, "get_provider", lambda: Broken())
    r = client.post("/api/context/assemble", json={"project_id": bible_project, "chapter_number": 5, "participants": ["Mira"]})
    assert r.status_code == 503 and "graph" in r.json()["detail"].lower()


# ------------------------------------------------------------- graph semantics
def _rel(a, b, kind_en, chapters: List[int], updated="2026-01-01T00:00:00"):
    return {"source": a, "target": b, "kind_en": kind_en, "kind_cn": kind_en, "fact": f"{a} {kind_en} {b}", "recent_event_summaries": [{"summary": f"ev{c}", "chapter_number": c} for c in chapters], "updated_at": updated}


@pytest.fixture
def relations():
    return [
        _rel("Mira", "Daren", "ally", [1, 2]),
        _rel("Daren", "Sela", "rival", [3]),
        _rel("Sela", "Orin", "employer", [9]),
        _rel("Mira", "Kest", "mentor", [2, 12]),
    ]


def test_relation_radius(relations):
    _ALIAS_TABLES.pop(1, None)
    r1 = build_subgraph(1, relations, ["Mira"], radius=1)
    assert {(e["source"], e["target"]) for e in r1["edges"]} == {("Mira", "Daren"), ("Mira", "Kest")}
    r2 = build_subgraph(1, relations, ["Mira"], radius=2)
    assert ("Daren", "Sela") in {(e["source"], e["target"]) for e in r2["edges"]}
    assert ("Sela", "Orin") not in {(e["source"], e["target"]) for e in r2["edges"]}
    r3 = build_subgraph(1, relations, ["Mira"], radius=3)
    assert ("Sela", "Orin") in {(e["source"], e["target"]) for e in r3["edges"]}


def test_edge_whitelist_and_chapter_cutoff(relations):
    _ALIAS_TABLES.pop(1, None)
    r = build_subgraph(1, relations, ["Mira"], radius=3, edge_type_whitelist=["mentor"])
    assert [(e["source"], e["target"]) for e in r["edges"]] == [("Mira", "Kest")]
    r = build_subgraph(1, relations, ["Mira", "Daren"], radius=2, max_chapter_id=2)
    pairs = {(e["source"], e["target"]) for e in r["edges"]}
    assert ("Daren", "Sela") not in pairs  # only event is ch3 > cutoff
    mentor = next(x for x in r["relation_summaries"] if x["b"] == "Kest")
    assert [ev["chapter_number"] for ev in mentor["recent_event_summaries"]] == [2]  # ch12 event trimmed


def test_alias_ingestion_resolves_participants(relations):
    _ALIAS_TABLES.pop(1, None)
    _register_aliases(1, {"Mira": ["the archivist", "M."]})
    r = build_subgraph(1, relations, ["The Archivist"], radius=1)
    assert {e["target"] for e in r["edges"]} == {"Daren", "Kest"}
    assert r["alias_table"]["the archivist"] == "Mira"
    assert r["nodes"] and all("name" in n for n in r["nodes"])  # never empty nodes


def test_sql_and_neo4j_providers_share_semantics(tmp_path, relations, monkeypatch):
    """Both providers feed raw relation items into build_subgraph; verify the SQL
    provider end-to-end and the Neo4j provider via a fake driver."""
    from sqlalchemy import event
    from sqlmodel import SQLModel, create_engine

    from app.db.session import apply_sqlite_pragmas
    from app.services import kg_provider as kgp

    eng = create_engine(f"sqlite:///{tmp_path / 'kg.db'}")
    event.listens_for(eng, "connect")(apply_sqlite_pragmas)
    SQLModel.metadata.create_all(eng)
    sql = SQLModelKGProvider(engine=eng)
    for rel in relations:
        sql.upsert_relation(7, rel)
    sql.ingest_aliases(7, {"Mira": ["the archivist"]})
    a = sql.query_subgraph(7, ["the archivist"], radius=2, max_chapter_id=3)

    class FakeRecord(dict):
        def __getitem__(self, k):
            return dict.__getitem__(self, k)

    class FakeSess:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def run(self, cypher, **params):
            for rel in relations:
                props = {"kind_en": rel["kind_en"], "kind": rel["kind_cn"], "fact": rel["fact"], "recent_event_summaries_json": __import__("json").dumps(rel["recent_event_summaries"]), "updated_at_epoch": 1}
                yield FakeRecord(source=rel["source"], target=rel["target"], props=props)

    class FakeDriver:
        def session(self):
            return FakeSess()

    neo = kgp.Neo4jKGProvider.__new__(kgp.Neo4jKGProvider)
    neo._driver = FakeDriver()
    neo.ingest_aliases(7, {"Mira": ["the archivist"]})
    b = neo.query_subgraph(7, ["the archivist"], radius=2, max_chapter_id=3)

    key = lambda r: sorted((e["source"], e["target"], e["kind"]) for e in r["edges"])
    assert key(a) == key(b)
    assert sorted(n["name"] for n in a["nodes"]) == sorted(n["name"] for n in b["nodes"])
    assert [sorted(x.items(), key=str) for x in a["relation_summaries"]] and a["alias_table"] == b["alias_table"]
    assert set(a.keys()) == set(b.keys()) == {"nodes", "edges", "alias_table", "fact_summaries", "relation_summaries"}


def test_single_participant_gets_adjacent_relationships(client, bible_project, monkeypatch):
    from app.services import context_service

    class One:
        def ingest_aliases(self, *a, **k):
            return None

        def query_subgraph(self, **k):
            return {"nodes": [], "edges": [], "alias_table": {}, "fact_summaries": ["Mira Hale Alliance Daren Voss"], "relation_summaries": [{"a": "Mira Hale", "b": "Daren Voss", "kind": "Alliance"}]}

    monkeypatch.setattr(context_service, "get_provider", lambda: One())
    ctx = _compile(client, bible_project, participants=["Mira"])
    assert ctx["facts_structured"]["relation_summaries"] and "Daren Voss" in ctx["facts_subgraph"]
