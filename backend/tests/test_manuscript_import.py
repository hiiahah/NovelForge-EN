"""Manuscript import: EPUB structure, side-story classification, corrections, atomic storage."""

from __future__ import annotations

import base64
import io
import re
import zipfile

import pytest

from conftest import FIXTURE_ITEMS, build_epub, make_app_client
from app.services.lab.manuscript_import import (
    MAIN_STORY_TYPES,
    DetectedChapter,
    apply_corrections,
    classify_sections,
    detect_chapters,
    estimate_input_tokens,
    extract_sections,
    included_chapters,
    parse_number,
)


def _types(res):
    return [(c.title, c.section_type, c.included) for c in res.chapters]


def test_epub_spine_order_and_resource_exclusion(fixture_epub):
    sections, info = extract_sections("book.epub", fixture_epub)
    assert [s.spine_index for s in sections] == sorted(s.spine_index for s in sections)
    paths = [s.source_path for s in sections]
    assert paths[0] == "OEBPS/Text/Cover.xhtml" and paths[-1] == "OEBPS/Text/0012_Afterword.xhtml"
    assert not any(p.endswith((".css", ".png", ".ncx")) for p in paths)
    assert info["meta"]["title"] == "Synthetic Fixture Book" and info["meta"]["creator"] == "Fixture Author"
    # <nav>/<button>/<title> content never leaks into the text.
    joined = "\n".join(s.text for s in sections)
    assert "Navigation noise" not in joined and "Next Chapter" not in joined
    assert "Chapter 1: Arrival" not in joined.replace("# Chapter 1: Arrival", "")  # heading only via h1 marker


def test_epub_relative_manifest_paths_and_entities():
    data = build_epub([("a.xhtml", "Chapter 1: Caf&#233; &amp; Co", 200), ("b.xhtml", "Chapter 2: Ünïcödé", 200)], opf_dir="deep/nested", text_dir="xhtml/parts")
    sections, _ = extract_sections("x.epub", data)
    assert [s.source_path for s in sections] == ["deep/nested/xhtml/parts/a.xhtml", "deep/nested/xhtml/parts/b.xhtml"]
    assert sections[0].nav_label == "Chapter 1: Café & Co"
    assert sections[1].nav_label == "Chapter 2: Ünïcödé"


def test_epub_spine_order_wins_over_filenames():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>')
        zf.writestr("OEBPS/content.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c1" href="c1.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="c2.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c2"/><itemref idref="c1"/></spine></package>')
        zf.writestr("OEBPS/c1.xhtml", "<html><body><h1>Chapter 2 Later</h1><p>" + "later " * 100 + "</p></body></html>")
        zf.writestr("OEBPS/c2.xhtml", "<html><body><h1>Chapter 1 First</h1><p>" + "first " * 100 + "</p></body></html>")
    res = detect_chapters("b.epub", buf.getvalue())
    assert [c.title for c in res.chapters] == ["Chapter 1 First", "Chapter 2 Later"]
    assert [c.number for c in res.chapters] == [1, 2]


def test_lowercase_roman_and_number_parsing():
    assert parse_number("iv") == 4 and parse_number("XII") == 12 and parse_number("twenty-one") == 21 and parse_number("三") == 3
    res = detect_chapters("book.epub", build_epub([("a.xhtml", "Chapter iv: Copper", 200), ("b.xhtml", "Chapter v: Tin", 200)], include_guide_cover=False))
    assert [c.number for c in res.chapters] == [4, 5]


def test_classification_of_fixture(fixture_epub):
    res = detect_chapters("book.epub", fixture_epub)
    by_title = {c.title: c for c in res.chapters}
    assert res.spine_item_count == len(FIXTURE_ITEMS)
    assert by_title["Cover"].section_type == "front_matter" and not by_title["Cover"].included
    assert by_title["Prologue"].section_type == "prologue" and by_title["Prologue"].included
    assert by_title["Interlude: The Harbor"].section_type == "main_interlude" and by_title["Interlude: The Harbor"].included
    assert by_title["Chapter iv: Copper"].number == 4 and by_title["Chapter iv: Copper"].included
    assert by_title["Epilogue) Morning (End)"].section_type == "main_epilogue" and by_title["Epilogue) Morning (End)"].included
    assert by_title["Side Story 1) A Quiet Road (1)"].section_type == "side_story" and not by_title["Side Story 1) A Quiet Road (1)"].included
    assert by_title["Bonus Story: The Window"].section_type == "bonus_story"
    assert by_title["Extra Chapter) An Answer"].section_type == "extra"
    assert by_title["Afterword"].section_type == "afterword" and not by_title["Afterword"].included
    # Source labels preserved verbatim.
    assert by_title["Side Story 1) A Quiet Road (1)"].source_label == "Side Story 1) A Quiet Road (1)"
    # Main-story boundary is the last epilogue part.
    end = next(c for c in res.chapters if c.index == res.main_story_end_index)
    assert end.title == "Epilogue) Morning (End)"
    assert all(c.section_type in MAIN_STORY_TYPES for c in included_chapters(res.chapters))
    assert [c.spine_index for c in included_chapters(res.chapters)] == sorted(c.spine_index for c in included_chapters(res.chapters))
    # Exclusion reasons and evidence populated.
    ss = by_title["Side Story 1) A Quiet Road (1)"]
    assert "Side story" in ss.exclusion_reason and ss.classification_evidence and 0 < ss.classification_confidence <= 1
    assert any("supplementary" in w for w in res.warnings)


def test_side_story_detected_from_nav_group_only(fixture_epub_grouped):
    res = detect_chapters("book.epub", fixture_epub_grouped)
    by_title = {c.title: c for c in res.chapters}
    ss = by_title["A Quiet Road (1)"]
    assert ss.section_type == "side_story" and not ss.included
    assert any("nav_group" in e for e in ss.classification_evidence)
    # EPUB3 landmarks mark the cover.
    assert by_title["Cover"].section_type == "front_matter"


def test_excluded_sections_do_not_enter_token_estimate_or_word_total(fixture_epub):
    res = detect_chapters("book.epub", fixture_epub)
    inc = included_chapters(res.chapters)
    est = estimate_input_tokens(res.chapters)
    assert est == int(sum(c.word_count for c in inc) * 1.4) + len(inc) * 1500
    excluded_words = sum(c.word_count for c in res.chapters if not c.included)
    assert excluded_words > 0
    assert est < int((sum(c.word_count for c in inc) + excluded_words) * 1.4) + len(inc) * 1500


def test_narrative_epilogue_not_afterword_and_trailing_back_matter_run():
    items = [("c1.xhtml", "Chapter 1: One", 200), ("c2.xhtml", "Chapter 2: Two", 200), ("e.xhtml", "Epilogue", 200), ("aw.xhtml", "Afterword", 60), ("about.xhtml", "About the Author", 40), ("ad.xhtml", "Coming Soon", 30)]
    res = detect_chapters("b.epub", build_epub(items, include_guide_cover=False))
    kinds = {c.title: c.section_type for c in res.chapters}
    assert kinds["Epilogue"] == "main_epilogue"
    assert kinds["Afterword"] == kinds["About the Author"] == kinds["Coming Soon"] == "afterword"
    assert [c.title for c in included_chapters(res.chapters)] == ["Chapter 1: One", "Chapter 2: Two", "Epilogue"]


def test_prologue_kept_and_afterword_kept_when_toggle_off():
    items = [("p.xhtml", "Prologue", 150), ("c1.xhtml", "Chapter 1: One", 200), ("aw.xhtml", "Afterword", 60)]
    res = detect_chapters("b.epub", build_epub(items, include_guide_cover=False), exclude_afterword=False)
    assert [c.title for c in included_chapters(res.chapters)] == ["Prologue", "Chapter 1: One", "Afterword"]


def test_chapter_numbering_restart_after_ending_is_supplementary():
    items = [("c1.xhtml", "Chapter 1", 200), ("c2.xhtml", "Chapter 2", 200), ("e.xhtml", "Epilogue", 200), ("s1.xhtml", "Chapter 1", 200), ("s2.xhtml", "Chapter 2", 200)]
    res = detect_chapters("b.epub", build_epub(items, include_guide_cover=False))
    assert [c.section_type for c in res.chapters] == ["main_chapter", "main_chapter", "main_epilogue", "side_story", "side_story"]


def test_generic_headings_do_not_become_side_stories():
    # "SS" inside a word, "extra" as a normal word, must not trigger classification.
    items = [("c1.xhtml", "Chapter 1: Glass Houses", 200), ("c2.xhtml", "Chapter 2: An Extra Mile", 200), ("c3.xhtml", "Chapter 3: Bonus Points", 200)]
    res = detect_chapters("b.epub", build_epub(items, include_guide_cover=False))
    assert all(c.section_type == "main_chapter" for c in res.chapters), _types(res)


def test_text_preamble_preface_toggle():
    text = "\n".join(["Some opening note before chapters.", "", "Chapter 1 The Gate", *(["gate " * 40] * 3), "", "Chapter 2 The Crown", *(["crown " * 40] * 3)])
    res = detect_chapters(text)
    assert res.chapters[0].section_type == "front_matter" and not res.chapters[0].included
    res2 = detect_chapters(text, exclude_front_matter=False)
    assert res2.chapters[0].title == "Preface" and res2.chapters[0].included
    assert [c.title for c in included_chapters(res2.chapters)] == ["Preface", "Chapter 1 The Gate", "Chapter 2 The Crown"]


def test_invalid_regex_raises_re_error():
    with pytest.raises(re.error):
        detect_chapters("Chapter 1\nbody", chapter_pattern="(unclosed")


def test_stable_ids_and_deterministic_corrections(fixture_epub):
    res = detect_chapters("book.epub", fixture_epub)
    res2 = detect_chapters("book.epub", fixture_epub)
    assert [c.section_id for c in res.chapters] == [c.section_id for c in res2.chapters]
    ids = {c.title: c.section_id for c in res.chapters}
    corrections = [
        {"op": "include", "section_id": ids["Bonus Story: The Window"]},
        {"op": "exclude", "section_id": ids["Chapter 2: The Ledger"], "reason": "test"},
        {"op": "rename", "section_id": ids["Chapter 1: Arrival"], "title": "Chapter 1: Renamed"},
        {"op": "set_type", "section_id": ids["Extra Chapter) An Answer"], "section_type": "main_interlude"},
        {"op": "merge_with_next", "section_id": ids["Chapter 3: Signals"]},
    ]
    a = apply_corrections(detect_chapters("book.epub", fixture_epub).chapters, corrections)
    b = apply_corrections(detect_chapters("book.epub", fixture_epub).chapters, corrections)
    assert [(c.title, c.included, c.section_type, c.word_count) for c in a] == [(c.title, c.included, c.section_type, c.word_count) for c in b]
    by = {c.title: c for c in a}
    assert by["Bonus Story: The Window"].included and by["Bonus Story: The Window"].manually_overridden
    assert not by["Chapter 2: The Ledger"].included and by["Chapter 2: The Ledger"].exclusion_reason == "test"
    assert "Chapter 1: Renamed" in by and by["Extra Chapter) An Answer"].section_type == "main_interlude" and by["Extra Chapter) An Answer"].included
    assert "Chapter iv: Copper" not in by  # merged into Chapter 3
    assert by["Chapter 3: Signals"].word_count > 800  # merged bodies (+ heading tokens)
    assert [c.index for c in a] == list(range(1, len(a) + 1))


def test_split_keeps_ids_stable():
    res = detect_chapters("Chapter 1 Alpha\n" + "alpha " * 100 + "\nMARKER " + "beta " * 100)
    ch = res.chapters[0]
    out = apply_corrections(res.chapters, [{"op": "split", "section_id": ch.section_id, "at_text": "MARKER", "new_title": "Chapter 1b"}])
    assert [c.title for c in out] == ["Chapter 1 Alpha", "Chapter 1b"] and out[1].section_id != out[0].section_id
    out2 = apply_corrections(detect_chapters("Chapter 1 Alpha\n" + "alpha " * 100 + "\nMARKER " + "beta " * 100).chapters, [{"op": "split", "section_id": ch.section_id, "at_text": "MARKER", "new_title": "Chapter 1b"}])
    assert out2[1].section_id == out[1].section_id


def test_archive_limits():
    from app.services.lab.manuscript_import import MAX_ARCHIVE_ENTRIES

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", "<container/>")
        for i in range(MAX_ARCHIVE_ENTRIES + 1):
            zf.writestr(f"OEBPS/f{i}.xhtml", "<html/>")
    with pytest.raises(ValueError):
        extract_sections("bomb.epub", buf.getvalue())
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("META-INF/container.xml", "<container/>")
        zf.writestr("../evil.xhtml", "<html/>")
    with pytest.raises(ValueError):
        extract_sections("evil.epub", buf.getvalue())


# ------------------------------------------------------------------ API level
@pytest.fixture(scope="module")
def client(app_client):
    return app_client


@pytest.fixture(scope="module")
def project(client):
    import uuid

    r = client.post("/api/projects/", json={"name": f"Manuscript Import {uuid.uuid4().hex[:6]}", "description": ""})
    assert r.status_code in (200, 201), r.text
    return r.json()["data"]


def _payload(data: bytes, **kw):
    return {"filename": "book.epub", "content_base64": base64.b64encode(data).decode("ascii"), **kw}


def test_api_malformed_base64_and_invalid_regex(client):
    r = client.post("/api/lab/manuscript/preview", json={"filename": "a.txt", "content_base64": "!!!not base64!!!"})
    assert r.status_code == 400 and "base64" in r.json()["detail"]
    r = client.post("/api/lab/manuscript/preview", json={"filename": "a.txt", "content_base64": base64.b64encode(b"Chapter 1\nbody").decode(), "chapter_pattern": "(bad"})
    assert r.status_code == 400 and "regex" in r.json()["detail"].lower()
    r = client.post("/api/lab/manuscript/preview", json={"filename": "a.txt", "content_base64": base64.b64encode(b"Chapter 1\nbody").decode(), "min_chapter_words": 0})
    assert r.status_code == 422


def test_api_preview_import_equivalence_and_exclusion(client, project, fixture_epub):
    ids_before = None
    r = client.post("/api/lab/manuscript/preview", json=_payload(fixture_epub))
    assert r.status_code == 200, r.text
    prev = r.json()
    assert prev["spine_item_count"] == len(FIXTURE_ITEMS)
    assert prev["excluded_side_story_count"] == 2 and prev["excluded_bonus_extra_count"] == 2 and prev["uncertain_count"] == 0
    assert prev["main_story_end_title"] == "Epilogue) Morning (End)"
    assert prev["min_chapter_words"] == 80 and "section_types" in prev
    excluded = [c for c in prev["chapters"] if not c["included"]]
    assert all(c["exclusion_reason"] for c in excluded)
    ids_before = [c["section_id"] for c in prev["chapters"]]

    # Manual override: re-include the bonus story, exclude the interlude.
    corr = [{"op": "include", "section_id": next(c["section_id"] for c in prev["chapters"] if c["section_type"] == "bonus_story")}, {"op": "exclude", "section_id": next(c["section_id"] for c in prev["chapters"] if c["section_type"] == "main_interlude")}]
    r = client.post("/api/lab/manuscript/preview", json=_payload(fixture_epub, corrections=corr))
    prev2 = r.json()
    assert prev2["included_chapters"] == prev["included_chapters"]  # +1 -1
    assert [c["section_id"] for c in prev2["chapters"]] == ids_before  # stable ids

    r = client.post("/api/lab/manuscript/import", json={**_payload(fixture_epub, corrections=corr), "project_id": project["id"], "book_title": "Fixture"})
    assert r.status_code == 200, r.text
    imp = r.json()
    assert imp["chapter_count"] == prev2["included_chapters"]
    assert imp["total_words"] == prev2["total_words"]
    listing = client.get("/api/lab/manuscript", params={"project_id": project["id"]}).json()
    titles = [c["title"] for c in listing["chapters"]]
    assert "Bonus Story: The Window" in titles and "Interlude: The Harbor" not in titles
    assert not any("Side Story" in t for t in titles)
    assert listing["meta"]["excluded_sections"] and all(s["reason"] for s in listing["meta"]["excluded_sections"])
    # Stored chapters carry stable identity and section type.
    cards = client.get(f"/api/projects/{project['id']}/cards").json()
    stored = [c for c in cards if c["card_type"]["name"] == "Chapter Analysis"]
    assert all(c["content"]["section_id"] and c["content"]["included"] for c in stored)
    assert [c["content"]["spine_index"] for c in sorted(stored, key=lambda c: c["content"]["chapter_number"])] == sorted(c["content"]["spine_index"] for c in stored)


def test_api_import_is_atomic_and_replace_rollback_safe(client, project, fixture_epub, monkeypatch):
    r = client.post("/api/lab/manuscript/import", json={**_payload(fixture_epub), "project_id": project["id"], "book_title": "Before", "replace_existing": True})
    assert r.status_code == 200, r.text
    listing_before = client.get("/api/lab/manuscript", params={"project_id": project["id"]}).json()
    n_before = len(listing_before["chapters"])
    assert n_before > 0
    from app.services.card_service import CardService

    real_create = CardService.create
    calls = {"n": 0}

    def flaky(self, card_create, project_id, *, commit=True):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated failure mid-import")
        return real_create(self, card_create, project_id, commit=commit)

    monkeypatch.setattr(CardService, "create", flaky)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            client.post("/api/lab/manuscript/import", json={**_payload(fixture_epub), "project_id": project["id"], "book_title": "After", "replace_existing": True})
    finally:
        monkeypatch.setattr(CardService, "create", real_create)
    listing_after = client.get("/api/lab/manuscript", params={"project_id": project["id"]}).json()
    # Old chapters survive a failed replace; nothing partial was written.
    assert len(listing_after["chapters"]) == n_before
    assert [c["title"] for c in listing_after["chapters"]] == [c["title"] for c in listing_before["chapters"]]
    assert listing_after["meta"]["book_title"] == "Before"
