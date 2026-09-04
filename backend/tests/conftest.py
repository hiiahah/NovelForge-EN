"""Shared fixtures. No live network, no AuthND browser, no Neo4j."""

from __future__ import annotations

import io
import os
import sys
import zipfile
from typing import Dict, List, Optional, Tuple

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
# Tests must never touch the developer's novelforge.db. Test modules import
# app.* at collection time, so the database path has to be fixed *before* any
# app module is imported: use a per-session temporary file here.
import tempfile  # noqa: E402

_TEST_DB_DIR = tempfile.mkdtemp(prefix="novelforge-tests-")
os.environ["NOVELFORGE_DB_PATH"] = os.path.join(_TEST_DB_DIR, "test.db")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Never let a unit test start the browser token helper.
os.environ.setdefault("AUTHND_TOKEN_MODE", "pool")
os.environ.setdefault("AUTHND_TOKEN_POOL_URL", "http://127.0.0.1:9/never")


def _body(words: int, seed: str) -> str:
    """Deterministic neutral filler text (no copyrighted content)."""
    vocab = ["harbor", "lantern", "ledger", "quiet", "signal", "morning", "copper", "window", "answer", "road"]
    out = []
    for i in range(words):
        out.append(vocab[(i * 7 + len(seed)) % len(vocab)])
        if i % 12 == 11:
            out[-1] += "."
    return " ".join(out)


def build_epub(
    items: List[Tuple[str, str, Optional[int]]],
    *,
    nav_groups: Optional[Dict[str, List[str]]] = None,
    epub3: bool = False,
    include_guide_cover: bool = True,
    heading_in_body: bool = True,
    opf_dir: str = "OEBPS",
    text_dir: str = "Text",
) -> bytes:
    """Build a tiny synthetic EPUB.

    items: (filename, label, word_count or None). Labels drive the NCX/nav.
    nav_groups: {group_label: [filenames]} nested under a parent nav point.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        zf.writestr("META-INF/container.xml", f'<?xml version="1.0"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="{opf_dir}/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')
        manifest = ['<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>', '<item id="css" href="Styles/style.css" media-type="text/css"/>', '<item id="cover-img" href="Images/cover.png" media-type="image/png" properties="cover-image"/>']
        spine = []
        if epub3:
            manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')
        zf.writestr(f"{opf_dir}/Styles/style.css", "p { margin: 0 }")
        zf.writestr(f"{opf_dir}/Images/cover.png", b"\x89PNG\r\n\x1a\n")
        for i, (fname, label, words) in enumerate(items):
            iid = f"it{i}"
            manifest.append(f'<item id="{iid}" href="{text_dir}/{fname}" media-type="application/xhtml+xml"/>')
            spine.append(f'<itemref idref="{iid}"/>')
            body = _body(words, fname) if words else ""
            heading = f"<h1>{label}</h1>" if heading_in_body and label else ""
            paragraphs = "".join(f"<p>{body[k:k+300]}</p>" for k in range(0, len(body), 300)) if body else ""
            zf.writestr(
                f"{opf_dir}/{text_dir}/{fname}",
                f'<?xml version="1.0" encoding="utf-8"?><html xmlns="http://www.w3.org/1999/xhtml"><head><title>{label}</title><link href="../Styles/style.css" rel="stylesheet"/></head><body><nav>Navigation noise &amp; menu</nav>{heading}{paragraphs}<button>Next Chapter</button></body></html>',
            )
        guide = f'<guide><reference type="cover" title="Cover" href="{text_dir}/{items[0][0]}"/></guide>' if include_guide_cover and items else ""
        version = "3.0" if epub3 else "2.0"
        zf.writestr(
            f"{opf_dir}/content.opf",
            f'<?xml version="1.0" encoding="utf-8"?><package xmlns="http://www.idpf.org/2007/opf" version="{version}" unique-identifier="id">'
            f'<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Synthetic Fixture Book</dc:title><dc:creator>Fixture Author</dc:creator><dc:language>en</dc:language><dc:identifier id="id">urn:uuid:fixture</dc:identifier></metadata>'
            f'<manifest>{"".join(manifest)}</manifest><spine toc="ncx">{"".join(spine)}</spine>{guide}</package>',
        )
        grouped = {f for files in (nav_groups or {}).values() for f in files}
        points = []
        order = 1
        for fname, label, _ in items:
            if fname in grouped:
                continue
            points.append(f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{label}</text></navLabel><content src="{text_dir}/{fname}"/></navPoint>')
            order += 1
        for group, files in (nav_groups or {}).items():
            children = []
            for f in files:
                label = next(l for (fn, l, _) in items if fn == f)
                children.append(f'<navPoint id="n{order}" playOrder="{order}"><navLabel><text>{label}</text></navLabel><content src="{text_dir}/{f}"/></navPoint>')
                order += 1
            points.append(f'<navPoint id="g{order}" playOrder="{order}"><navLabel><text>{group}</text></navLabel><content src="{text_dir}/{files[0]}"/>{"".join(children)}</navPoint>')
            order += 1
        zf.writestr(f"{opf_dir}/toc.ncx", f'<?xml version="1.0"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head/><docTitle><text>Synthetic</text></docTitle><navMap>{"".join(points)}</navMap></ncx>')
        if epub3:
            lis = "".join(f'<li><a href="{text_dir}/{fn}">{label}</a></li>' for fn, label, _ in items if fn not in grouped)
            for group, files in (nav_groups or {}).items():
                inner = "".join(f'<li><a href="{text_dir}/{f}">{next(l for (fn, l, _) in items if fn == f)}</a></li>' for f in files)
                lis += f"<li><span>{group}</span><ol>{inner}</ol></li>"
            zf.writestr(f"{opf_dir}/nav.xhtml", f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><body><nav epub:type="toc"><ol>{lis}</ol></nav><nav epub:type="landmarks"><ol><li><a epub:type="cover" href="{text_dir}/{items[0][0]}">Cover</a></li></ol></nav></body></html>')
    return buf.getvalue()


# Mirrors the structure of a long web-novel EPUB: cover, prologue, numbered
# chapters, an interlude, multi-part epilogue, then side stories and an extra.
FIXTURE_ITEMS: List[Tuple[str, str, Optional[int]]] = [
    ("Cover.xhtml", "Cover", None),
    ("0000_Prologue.xhtml", "Prologue", 60),
    ("0001_Chapter_1_Arrival.xhtml", "Chapter 1: Arrival", 400),
    ("0002_Chapter_2_The_Ledger.xhtml", "Chapter 2: The Ledger", 400),
    ("0003_Interlude_The_Harbor.xhtml", "Interlude: The Harbor", 200),
    ("0004_Chapter_3_Signals.xhtml", "Chapter 3: Signals", 400),
    ("0005_Chapter_iv_Copper.xhtml", "Chapter iv: Copper", 400),
    ("0006_Epilogue_1.xhtml", "Epilogue) Morning (1)", 300),
    ("0007_Epilogue_End.xhtml", "Epilogue) Morning (End)", 300),
    ("0008_Side_Story_1_A.xhtml", "Side Story 1) A Quiet Road (1)", 350),
    ("0009_Side_Story_1_B.xhtml", "Side Story 1) A Quiet Road (2)", 350),
    ("0010_Bonus_Story_Window.xhtml", "Bonus Story: The Window", 300),
    ("0011_Extra_Chapter_Answer.xhtml", "Extra Chapter) An Answer", 300),
    ("0012_Afterword.xhtml", "Afterword", 80),
]


@pytest.fixture(scope="session")
def fixture_epub() -> bytes:
    return build_epub(FIXTURE_ITEMS)


@pytest.fixture(scope="session")
def fixture_epub_grouped() -> bytes:
    """Side stories carry NO marker in their labels; only the nav group says so."""
    items = list(FIXTURE_ITEMS)
    items[9] = ("0008_Side_Story_1_A.xhtml", "A Quiet Road (1)", 350)
    items[10] = ("0009_Side_Story_1_B.xhtml", "A Quiet Road (2)", 350)
    items[9] = ("0008_ss_a.xhtml", "A Quiet Road (1)", 350)
    items[10] = ("0009_ss_b.xhtml", "A Quiet Road (2)", 350)
    return build_epub(items, nav_groups={"Side Stories": ["0008_ss_a.xhtml", "0009_ss_b.xhtml"]}, epub3=True)


_APP_CLIENT = None


def make_app_client(tmp_path_factory, name: str):
    """Application client over one shared clean test database.

    SQLModel metadata is process-global, so the app is created once per test
    session (fresh database file); modules isolate themselves by project name.
    """
    global _APP_CLIENT
    from fastapi.testclient import TestClient

    if _APP_CLIENT is None:
        db_path = os.environ["NOVELFORGE_DB_PATH"]
        from main import app  # noqa: WPS433

        client = TestClient(app)
        client.__enter__()
        _APP_CLIENT = (client, str(db_path))
    return _APP_CLIENT


@pytest.fixture(scope="session")
def app_client(tmp_path_factory):
    client, _ = make_app_client(tmp_path_factory, "session")
    yield client
    client.__exit__(None, None, None)
