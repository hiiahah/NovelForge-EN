"""Manuscript import for the Narrative Reverse-Engineering Lab.

Only user-supplied files are analysed. There is deliberately no fetching,
scraping or downloading of any kind here.

Pipeline:
1. ``extract_sections`` converts TXT / Markdown / EPUB / DOCX into a list of
   ``SourceSection`` objects. EPUB documents are read in OPF spine order with
   navigation labels attached; TXT/MD/DOCX are split with a chapter regex
   (auto-detected from a small set of common patterns when the user does not
   supply one).
2. ``classify_sections`` assigns every section a ``section_type`` (front matter,
   prologue, main chapter, epilogue, side story, ...) from deterministic
   evidence: navigation labels, headings, file names, position relative to the
   main-story ending and repeated supplementary labels. Anything that is not
   main narrative is excluded from downstream analysis (but stays visible in
   the preview so the user can override).
3. ``apply_corrections`` applies user split/merge/rename/exclude/include
   operations addressed by *stable section ids*.
4. ``store_manuscript`` writes each included chapter as a "Chapter Analysis"
   card whose content holds identity + word count + ``source_text`` so the Lab
   workflow can analyse chapters without re-reading files from disk. The write
   is one transaction.
"""

from __future__ import annotations

import hashlib
import html
import io
import posixpath
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from html.parser import HTMLParser
from typing import Any, Dict, Iterable, List, Optional, Tuple
from xml.etree import ElementTree

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType
from app.schemas.card import CardCreate
from app.services.card_service import CardService

SUPPORTED_EXTENSIONS = (".txt", ".md", ".markdown", ".epub", ".docx", ".pdf")
# Bump whenever splitting/classification output can change for the same bytes.
PARSER_VERSION = "manuscript-parser-2"

# Archive safety limits (zip bombs / pathological EPUBs).
MAX_ARCHIVE_ENTRIES = 5000
MAX_UNCOMPRESSED_BYTES = 400 * 1024 * 1024
MAX_SINGLE_ENTRY_BYTES = 60 * 1024 * 1024
DEFAULT_MIN_CHAPTER_WORDS = 80

# Ordered by specificity. Each pattern must expose the chapter number (or be numberless).
CHAPTER_PATTERN_CANDIDATES: List[Tuple[str, str]] = [
    ("chapter_word", r"^\s*(?:#+\s*)?Chapter\s+(\d+|[IVXLCivxlc]+|[A-Za-z\-]+)\b.*$"),
    ("cjk_chapter", r"^\s*第\s*([零一二三四五六七八九十百千0-9]+)\s*[章节回].*$"),
    # Korean web-novel headings: "제 12 화", "12화", "제3장", "에피소드 4", "EP.4"
    ("korean_hwa", r"^\s*(?:#+\s*)?(?:제\s*)?(\d{1,4})\s*[화장회편막절](?:\s|[.:：\-)]|$).*$"),
    ("korean_episode", r"^\s*(?:#+\s*)?(?:에피소드|EP|Ep|ep)\.?\s*(\d{1,4})\b.*$"),
    ("numbered_heading", r"^\s*#{1,3}\s*(\d+)[\.\):]?\s+.*$"),
    ("bare_number_title", r"^\s*(\d{1,4})[\.\)]\s+\S.*$"),
    ("markdown_h1", r"^\s*#\s+(.+)$"),
]

VOLUME_PATTERN_DEFAULT = r"^\s*(?:#+\s*)?(?:Volume|Book|Part|Arc)\s+(\d+|[IVXLCivxlc]+|[A-Za-z\-]+)\b.*$|^\s*第\s*([零一二三四五六七八九十百千0-9]+)\s*[卷部纪].*$|^\s*(?:제\s*)?(\d{1,3})\s*[권부]\s*(?:[.:：\-)]|$).*$"

# Section type vocabulary (see classify_sections).
SECTION_TYPES = (
    "front_matter", "preface", "prologue", "main_chapter", "main_interlude", "main_epilogue",
    "afterword", "appendix", "side_story", "bonus_story", "extra", "unknown",
)
MAIN_STORY_TYPES = {"prologue", "main_chapter", "main_interlude", "main_epilogue"}
EXCLUSION_REASONS = {
    "front_matter": "Front matter (title page, copyright, contents, dedication)",
    "preface": "Preface / foreword is not narrative",
    "afterword": "Afterword / author note / promotional back matter",
    "appendix": "Appendix / glossary / reference material",
    "side_story": "Side story: supplementary fiction outside the main narrative",
    "bonus_story": "Bonus story: supplementary fiction outside the main narrative",
    "extra": "Extra chapter: supplementary fiction outside the main narrative",
    "unknown": "Could not determine whether this section belongs to the main narrative",
}

FRONT_MATTER_HINTS = ("copyright", "table of contents", "contents", "dedication", "acknowledg", "title page", "colophon", "imprint", "cover")
PREFACE_HINTS = ("preface", "foreword", "introduction", "author's introduction")
# "epilogue" is intentionally NOT afterword: a story epilogue is narrative content.
AFTERWORD_HINTS = ("afterword", "about the author", "acknowledg", "postscript", "author's note", "authors note", "author note", "translator's note", "translators note", "tl note", "coming soon", "also by", "next volume preview", "advertisement", "sneak peek", "newsletter")
APPENDIX_HINTS = ("appendix", "glossary", "character sheet", "character profiles", "dramatis personae", "map of", "timeline of", "index")
INTERLUDE_HINTS = ("interlude", "intermission", "intermezzo", "entr'acte", "interstitial")
_SUPPLEMENT_RX = re.compile(
    r"(?i)\b("
    r"side[\s_\-]*stor(?:y|ies)|side[\s_\-]*chapter|side[\s_\-]*tale|"
    r"bonus[\s_\-]*(?:stor(?:y|ies)|chapter|episode|content)?|"
    r"extra[\s_\-]*(?:stor(?:y|ies)|chapter|episode)?|extras|"
    r"short[\s_\-]*stor(?:y|ies)|"
    r"after[\s_\-]*stor(?:y|ies)|"
    r"special[\s_\-]*(?:stor(?:y|ies)|chapter|episode)|"
    r"omake|gaiden|"
    r"web[\s_\-]*store[\s_\-]*bonus|store[\s_\-]*(?:exclusive|bonus)|"
    r"alternate[\s_\-]*(?:pov|perspective|point of view)|another[\s_\-]*(?:pov|perspective)|"
    r"character[\s_\-]*(?:side[\s_\-]*)?stor(?:y|ies)|"
    r"spin[\s_\-]*off|what[\s_\-]*if"
    r")\b|(?<![A-Za-z])SS(?:\s*\d+|[\s_\-:])"
)
_SUPPLEMENT_KIND = (
    ("bonus_story", re.compile(r"(?i)\bbonus|web[\s_\-]*store|store[\s_\-]*(?:exclusive|bonus)")),
    ("extra", re.compile(r"(?i)\bextra|omake|special[\s_\-]*(?:stor|chapter|episode)|alternate[\s_\-]*(?:pov|perspective)|another[\s_\-]*(?:pov|perspective)|what[\s_\-]*if")),
    ("side_story", re.compile(r"(?i)side[\s_\-]*(?:stor|chapter|tale)|short[\s_\-]*stor|after[\s_\-]*stor|gaiden|character[\s_\-]*(?:side[\s_\-]*)?stor|spin[\s_\-]*off|SS")),
)

_CJK_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_CJK_UNITS = {"十": 10, "百": 100, "千": 1000}
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}
_WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70,
    "eighty": 80, "ninety": 90, "hundred": 100,
}


def parse_number(token: str) -> Optional[int]:
    t = (token or "").strip()
    if not t:
        return None
    if t.isdigit():
        return int(t)
    if re.fullmatch(r"[IVXLCivxlc]+", t):
        total, prev = 0, 0
        for ch in reversed(t.upper()):
            v = _ROMAN[ch]
            total = total - v if v < prev else total + v
            prev = max(prev, v)
        return total
    if t and all(ch in _CJK_DIGITS or ch in _CJK_UNITS for ch in t):
        total, num = 0, 0
        for ch in t:
            if ch in _CJK_DIGITS:
                num = _CJK_DIGITS[ch]
            else:
                unit = _CJK_UNITS[ch]
                total += (num or 1) * unit
                num = 0
        return total + num
    words = re.split(r"[\s\-]+", t.lower())
    if words and all(w in _WORD_NUMBERS for w in words):
        total, current = 0, 0
        for w in words:
            v = _WORD_NUMBERS[w]
            if v == 100:
                current = (current or 1) * 100
            else:
                current += v
        return total + current
    return None


# --------------------------------------------------------------------------- text
class _HTMLText(HTMLParser):
    BLOCK_TAGS = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "section", "article", "blockquote", "tr", "hr"}
    SKIP_TAGS = {"script", "style", "head", "nav", "button", "noscript", "svg", "title"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: List[str] = []
        self.headings: List[str] = []
        self._skip = 0
        self._heading_buf: Optional[List[str]] = None

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in self.SKIP_TAGS:
            self._skip += 1
        if self._skip:
            return
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")
        if tag in ("h1", "h2", "h3"):
            self.parts.append("\n# ")
            self._heading_buf = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP_TAGS and self._skip:
            self._skip -= 1
            return
        if self._skip:
            return
        if tag in ("h1", "h2", "h3") and self._heading_buf is not None:
            heading = " ".join("".join(self._heading_buf).split())
            if heading:
                self.headings.append(heading)
            self._heading_buf = None
        if tag in self.BLOCK_TAGS or tag in ("h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.parts.append(data)
        if self._heading_buf is not None:
            self._heading_buf.append(data)

    def text(self) -> str:
        raw = "".join(self.parts)
        raw = raw.replace("\xa0", " ")
        raw = re.sub(r"[ \t]+\n", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _decode(data: bytes, encoding: Optional[str]) -> str:
    if encoding and encoding.lower() != "auto":
        return data.decode(encoding, errors="replace")
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _word_count(text: str) -> int:
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    words = len(re.findall(r"[A-Za-z0-9'’-]+", text))
    return cjk + words


def _stable_id(*parts: Any) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode("utf-8")).hexdigest()[:12]


@dataclass
class SourceSection:
    """One physical document (EPUB spine item) or one regex-split chapter."""
    source_path: str
    spine_index: int
    text: str
    nav_label: str = ""
    heading: str = ""
    filename_label: str = ""
    nav_parent: str = ""            # label of the nav ancestor (e.g. "Side Stories")
    landmark_type: str = ""         # EPUB landmark / guide type (cover, toc, bodymatter, ...)
    manifest_properties: str = ""   # nav, cover-image, ...
    linear: bool = True
    volume: str = ""

    @property
    def label(self) -> str:
        return self.nav_label or self.heading or self.filename_label or posixpath.basename(self.source_path)


# ---------------------------------------------------------------------- readers
def _safe_zip(data: bytes) -> zipfile.ZipFile:
    zf = zipfile.ZipFile(io.BytesIO(data))
    infos = zf.infolist()
    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise ValueError(f"Archive has too many entries ({len(infos)} > {MAX_ARCHIVE_ENTRIES})")
    total = 0
    for info in infos:
        if info.file_size > MAX_SINGLE_ENTRY_BYTES:
            raise ValueError(f"Archive entry too large: {info.filename}")
        total += info.file_size
        if total > MAX_UNCOMPRESSED_BYTES:
            raise ValueError("Archive expands beyond the allowed size")
        name = info.filename
        if name.startswith("/") or ".." in name.split("/"):
            raise ValueError(f"Unsafe archive entry path: {name}")
    return zf


def _resolve_href(base_dir: str, href: str) -> str:
    href = html.unescape(str(href or "")).split("#", 1)[0]
    if not href:
        return ""
    joined = posixpath.normpath(posixpath.join(base_dir, href)) if base_dir else posixpath.normpath(href)
    return joined.lstrip("./")


def _label_from_filename(path: str) -> str:
    name = posixpath.splitext(posixpath.basename(path))[0]
    name = re.sub(r"^\d{2,5}[_\-\s]*", "", name)      # strip leading sequence number
    name = re.sub(r"[_\-]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    # "Chapter 12 Title" -> "Chapter 12: Title"
    m = re.match(r"^(Chapter|Prologue|Epilogue|Side Story|Extra Chapter|Extra|Bonus|Interlude)\s*(\d+)?\s*(.*)$", name, re.I)
    if m and m.group(3):
        head = " ".join(x for x in (m.group(1), m.group(2)) if x)
        return f"{head}: {m.group(3)}"
    return name


def _epub_sections(data: bytes) -> Tuple[List[SourceSection], Dict[str, Any]]:
    """Return spine-ordered content sections plus book metadata (title/author/language)."""
    meta: Dict[str, Any] = {}
    with _safe_zip(data) as zf:
        names = set(zf.namelist())
        container = ElementTree.fromstring(zf.read("META-INF/container.xml"))
        ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
        rootfile = container.find(".//c:rootfile", ns)
        if rootfile is None or not rootfile.attrib.get("full-path"):
            raise ValueError("EPUB container.xml has no rootfile")
        opf_path = rootfile.attrib["full-path"]
        if opf_path not in names:
            raise ValueError(f"EPUB OPF not found: {opf_path}")
        opf = ElementTree.fromstring(zf.read(opf_path))
        base_dir = posixpath.dirname(opf_path)

        def _local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        for el in opf.iter():
            tag = _local(el.tag)
            if tag in ("title", "creator", "language", "identifier", "publisher") and (el.text or "").strip():
                meta.setdefault(tag, el.text.strip())

        manifest: Dict[str, Dict[str, str]] = {}
        for item in opf.iter():
            if _local(item.tag) != "item":
                continue
            manifest[item.attrib.get("id", "")] = {
                "href": _resolve_href(base_dir, item.attrib.get("href", "")),
                "media_type": item.attrib.get("media-type", ""),
                "properties": item.attrib.get("properties", "") or "",
            }

        # Navigation: EPUB3 nav document and/or EPUB2 NCX.
        nav_labels: Dict[str, str] = {}
        nav_parents: Dict[str, str] = {}
        landmarks: Dict[str, str] = {}
        nav_paths: set = set()
        for mid, item in manifest.items():
            props = item["properties"].split()
            if "nav" in props or item["media_type"] == "application/x-dtbncx+xml":
                nav_paths.add(item["href"])
                if item["href"] in names:
                    try:
                        _parse_nav(zf.read(item["href"]), posixpath.dirname(item["href"]), nav_labels, nav_parents, landmarks)
                    except Exception as exc:
                        logger.warning(f"[ManuscriptImport] nav parse failed for {item['href']}: {exc}")
        for ref in opf.iter():
            if _local(ref.tag) == "reference":
                path = _resolve_href(base_dir, ref.attrib.get("href", ""))
                if path:
                    landmarks.setdefault(path, ref.attrib.get("type", ""))

        spine_items: List[Tuple[str, bool]] = []
        for itemref in opf.iter():
            if _local(itemref.tag) == "itemref":
                item = manifest.get(itemref.attrib.get("idref", ""))
                if item and item["href"]:
                    spine_items.append((item["href"], itemref.attrib.get("linear", "yes").lower() != "no"))
        if not spine_items:
            spine_items = [(n, True) for n in sorted(names) if n.lower().endswith((".xhtml", ".html", ".htm"))]

        by_href = {item["href"]: item for item in manifest.values()}
        sections: List[SourceSection] = []
        seen_paths: set = set()
        for spine_index, (path, linear) in enumerate(spine_items):
            if path in seen_paths or path not in names or path in nav_paths:
                continue
            seen_paths.add(path)
            item = by_href.get(path, {})
            media = (item.get("media_type") or "").lower()
            if media and "html" not in media and "xml" not in media:
                continue
            props = item.get("properties", "")
            if "cover-image" in props:
                continue
            parser = _HTMLText()
            parser.feed(_decode(zf.read(path), None))
            text = parser.text()
            sections.append(SourceSection(
                source_path=path,
                spine_index=spine_index,
                text=text,
                nav_label=nav_labels.get(path, ""),
                heading=parser.headings[0] if parser.headings else "",
                filename_label=_label_from_filename(path),
                nav_parent=nav_parents.get(path, ""),
                landmark_type=landmarks.get(path, ""),
                manifest_properties=props,
                linear=linear,
            ))
    return sections, meta


def _parse_nav(data: bytes, base_dir: str, labels: Dict[str, str], parents: Dict[str, str], landmarks: Dict[str, str]) -> None:
    root = ElementTree.fromstring(data)

    def _local(tag: str) -> str:
        return tag.rsplit("}", 1)[-1]

    def _text(el) -> str:
        return " ".join("".join(el.itertext()).split())

    # EPUB2 NCX
    def _walk_ncx(node, parent_label: str) -> None:
        for child in node:
            if _local(child.tag) != "navPoint":
                continue
            label = ""
            src = ""
            for sub in child:
                if _local(sub.tag) == "navLabel":
                    label = _text(sub)
                elif _local(sub.tag) == "content":
                    src = _resolve_href(base_dir, sub.attrib.get("src", ""))
            has_children = any(_local(sub.tag) == "navPoint" for sub in child)
            if src and not has_children:
                labels[src] = label
                if parent_label:
                    parents.setdefault(src, parent_label)
            elif src and has_children:
                # Group heading pointing at its first child: record the group
                # as the child's parent but let the child keep its own label.
                parents.setdefault(src, label)
            _walk_ncx(child, label)

    for el in root.iter():
        if _local(el.tag) == "navMap":
            _walk_ncx(el, "")

    # EPUB3 nav document
    def _walk_ol(ol, parent_label: str) -> None:
        for li in ol:
            if _local(li.tag) != "li":
                continue
            label = ""
            href = ""
            child_ol = None
            for sub in li:
                t = _local(sub.tag)
                if t == "a":
                    label = _text(sub)
                    href = _resolve_href(base_dir, sub.attrib.get("href", ""))
                elif t == "span":
                    label = _text(sub)
                elif t == "ol":
                    child_ol = sub
            if href and child_ol is None:
                labels[href] = label
                if parent_label:
                    parents.setdefault(href, parent_label)
            elif href:
                parents.setdefault(href, label)
            if child_ol is not None:
                _walk_ol(child_ol, label)

    for el in root.iter():
        if _local(el.tag) != "nav":
            continue
        nav_type = ""
        for k, v in el.attrib.items():
            if k.rsplit("}", 1)[-1] == "type":
                nav_type = v
        if nav_type == "landmarks":
            for a in el.iter():
                if _local(a.tag) == "a":
                    href = _resolve_href(base_dir, a.attrib.get("href", ""))
                    for k, v in a.attrib.items():
                        if k.rsplit("}", 1)[-1] == "type" and href:
                            landmarks.setdefault(href, v)
            continue
        if nav_type in ("", "toc"):
            for ol in el:
                if _local(ol.tag) == "ol":
                    _walk_ol(ol, "")


def _docx_text(data: bytes) -> str:
    with _safe_zip(data) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines: List[str] = []
    for para in root.iter(f"{w}p"):
        style = para.find(f"{w}pPr/{w}pStyle")
        style_val = style.attrib.get(f"{w}val", "") if style is not None else ""
        text = "".join(t.text or "" for t in para.iter(f"{w}t")).strip()
        if not text:
            lines.append("")
            continue
        if style_val.lower().startswith("heading") or style_val.lower() == "title":
            lines.append(f"# {text}")
        else:
            lines.append(text)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _pdf_text(data: bytes) -> str:
    """Page-ordered text extraction for user-supplied PDFs (no OCR)."""
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - dependency is pinned in requirements
        raise ValueError("PDF import requires the 'pypdf' package") from exc
    reader = PdfReader(io.BytesIO(data))
    pages: List[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        pages.append(text.strip())
    joined = "\n\n".join(p for p in pages if p)
    # Reflow hard-wrapped PDF lines inside paragraphs; keep blank-line breaks.
    joined = re.sub(r"(?<![.!?…。！？\"”」』:])\n(?!\n)", " ", joined)
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


def extract_text(filename: str, data: bytes, encoding: Optional[str] = None) -> str:
    """Plain-text extraction (kept for callers that only need text)."""
    name = (filename or "").lower()
    if name.endswith(".epub"):
        sections, _ = _epub_sections(data)
        return "\n\n".join(s.text for s in sections if s.text)
    if name.endswith(".docx"):
        return _docx_text(data)
    if name.endswith(".pdf"):
        return _pdf_text(data)
    if name.endswith(SUPPORTED_EXTENSIONS[:3]):
        return _decode(data, encoding)
    raise ValueError(f"Unsupported file type: {filename}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")


# ------------------------------------------------------------------- detection
@dataclass
class DetectedChapter:
    index: int
    number: Optional[int]
    title: str
    volume: str
    text: str
    word_count: int
    start_line: int
    flags: List[str] = field(default_factory=list)
    # Stable identity + classification (new)
    section_id: str = ""
    source_path: str = ""
    spine_index: int = -1
    source_label: str = ""
    section_type: str = "main_chapter"
    is_main_story: bool = True
    included: bool = True
    exclusion_reason: str = ""
    classification_confidence: float = 1.0
    classification_evidence: List[str] = field(default_factory=list)
    manually_overridden: bool = False

    def set_type(self, section_type: str, confidence: float, evidence: Iterable[str], *, include_override: Optional[bool] = None) -> None:
        self.section_type = section_type
        self.is_main_story = section_type in MAIN_STORY_TYPES
        self.classification_confidence = round(float(confidence), 2)
        self.classification_evidence = [e for e in evidence if e]
        if include_override is None:
            self.included = self.is_main_story
        else:
            self.included = bool(include_override)
        self.exclusion_reason = "" if self.included else EXCLUSION_REASONS.get(section_type, "Excluded")
        self.flags = [f for f in self.flags if f not in ("front_matter", "afterword", "side_story", "excluded")]
        if not self.included:
            self.flags.append("excluded")
            if section_type in ("front_matter", "preface"):
                self.flags.append("front_matter")
            elif section_type in ("afterword", "appendix"):
                self.flags.append("afterword")
            elif section_type in ("side_story", "bonus_story", "extra"):
                self.flags.append("side_story")


@dataclass
class DetectionResult:
    chapter_pattern: str
    pattern_name: str
    volume_pattern: str
    chapters: List[DetectedChapter]
    volumes: List[str]
    warnings: List[str]
    total_words: int
    main_story_end_index: Optional[int] = None   # index (1-based) of the last main-story section
    book_meta: Dict[str, Any] = field(default_factory=dict)
    spine_item_count: int = 0


def auto_detect_pattern(lines: List[str]) -> Tuple[str, str]:
    best: Tuple[int, str, str] = (0, "", "")
    for name, pattern in CHAPTER_PATTERN_CANDIDATES:
        rx = re.compile(pattern, re.IGNORECASE)
        hits = sum(1 for ln in lines if rx.match(ln))
        if hits > best[0]:
            best = (hits, name, pattern)
    if best[0] < 2 and not (best[0] == 1 and best[1] in ("chapter_word", "cjk_chapter")):
        return "none", ""
    return best[1], best[2]


def _compile_user_pattern(pattern: Optional[str], flags: int) -> Optional[re.Pattern]:
    """Compile a user regex; invalid patterns raise ``re.error`` (API maps to HTTP 400)."""
    if not pattern:
        return None
    return re.compile(pattern, flags)


def _split_text_into_sections(
    text: str,
    *,
    chapter_pattern: Optional[str],
    volume_pattern: Optional[str],
) -> Tuple[List[SourceSection], List[str], str, str, str, List[str]]:
    """Regex-based splitting for TXT/MD/DOCX. Returns sections, warnings, pattern info, volumes."""
    lines = text.splitlines()
    pattern_name = "custom"
    if not chapter_pattern:
        pattern_name, chapter_pattern = auto_detect_pattern(lines)
    warnings: List[str] = []
    volume_pattern = volume_pattern or VOLUME_PATTERN_DEFAULT
    vol_rx = _compile_user_pattern(volume_pattern, re.IGNORECASE | re.MULTILINE)
    chap_rx = _compile_user_pattern(chapter_pattern, re.IGNORECASE)
    # Standalone headings for non-chapter sections so they never leak into a chapter body.
    section_rx = re.compile(
        r"^\s*(?:#+\s*)?(afterword|epilogue|prologue|foreword|preface|postscript|interlude|intermission|"
        r"side\s+story|bonus\s+(?:story|chapter)|extra\s+(?:story|chapter)|extra|short\s+story|"
        r"author'?s\s+note|about\s+the\s+author|acknowledg\w*|appendix|glossary)\b.{0,80}$",
        re.IGNORECASE,
    )

    sections: List[SourceSection] = []
    volumes: List[str] = []
    current_volume = "Volume 1"
    current_title: Optional[str] = None
    buffer: List[str] = []
    start_line = 0
    preface_buffer: List[str] = []

    def flush(end_line: int) -> None:
        nonlocal buffer, start_line
        body = "\n".join(buffer).strip()
        if current_title is None:
            if body:
                preface_buffer.append(body)
        else:
            sections.append(SourceSection(
                source_path=f"text:{start_line}", spine_index=len(sections), text=body,
                heading=current_title, volume=current_volume,
            ))
        buffer = []
        start_line = end_line

    for i, line in enumerate(lines):
        if vol_rx and vol_rx.match(line) and (not chap_rx or not chap_rx.match(line)):
            flush(i)
            current_volume = line.strip().lstrip("#").strip() or f"Volume {len(volumes) + 1}"
            if current_volume not in volumes:
                volumes.append(current_volume)
            current_title = None
            continue
        if chap_rx and chap_rx.match(line):
            flush(i)
            current_title = line.strip().lstrip("#").strip()
            continue
        if section_rx.match(line) and (chap_rx is None or not chap_rx.match(line)):
            flush(i)
            current_title = line.strip().lstrip("#").strip()
            continue
        buffer.append(line)
    flush(len(lines))

    preface_text = "\n\n".join(preface_buffer).strip()
    if preface_text:
        # The preamble stays visible as its own section; classification decides
        # whether it is excluded (front matter) or kept (Preface when the user
        # disables front-matter exclusion).
        sections.insert(0, SourceSection(source_path="text:preamble", spine_index=-1, text=preface_text, heading="Preface", volume=volumes[0] if volumes else current_volume))
        for k, s in enumerate(sections):
            s.spine_index = k
    if not sections and text.strip():
        sections.append(SourceSection(source_path="text:0", spine_index=0, text=text.strip(), heading="Chapter 1", volume=current_volume))
        warnings.append("No chapter headings detected; the whole file was treated as one chapter. Adjust the chapter pattern.")
    if not volumes:
        volumes = [current_volume]
    return sections, warnings, pattern_name, chapter_pattern or "", volume_pattern, volumes


def extract_sections(filename: str, data: bytes, encoding: Optional[str] = None, *, chapter_pattern: Optional[str] = None, volume_pattern: Optional[str] = None) -> Tuple[List[SourceSection], Dict[str, Any]]:
    """File -> ordered SourceSections + info dict (pattern info, volumes, warnings, meta)."""
    name = (filename or "").lower()
    info: Dict[str, Any] = {"pattern_name": "epub_spine", "chapter_pattern": "", "volume_pattern": volume_pattern or VOLUME_PATTERN_DEFAULT, "volumes": [], "warnings": [], "meta": {}, "spine_item_count": 0}
    if name.endswith(".epub"):
        sections, meta = _epub_sections(data)
        info["meta"] = meta
        info["spine_item_count"] = len(sections)
        info["volumes"] = ["Volume 1"]
        for s in sections:
            s.volume = "Volume 1"
        # A user-supplied chapter regex still applies to EPUB labels for numbering.
        if chapter_pattern:
            _compile_user_pattern(chapter_pattern, re.IGNORECASE)
            info["pattern_name"], info["chapter_pattern"] = "custom", chapter_pattern
        return sections, info
    if name.endswith(".docx"):
        text = _docx_text(data)
    elif name.endswith(".pdf"):
        text = _pdf_text(data)
    elif name.endswith(SUPPORTED_EXTENSIONS[:3]):
        text = _decode(data, encoding)
    else:
        raise ValueError(f"Unsupported file type: {filename}. Supported: {', '.join(SUPPORTED_EXTENSIONS)}")
    sections, warnings, pattern_name, chapter_pattern, volume_pattern, volumes = _split_text_into_sections(text, chapter_pattern=chapter_pattern, volume_pattern=volume_pattern)
    info.update({"pattern_name": pattern_name, "chapter_pattern": chapter_pattern, "volume_pattern": volume_pattern, "volumes": volumes, "warnings": warnings, "spine_item_count": len(sections)})
    return sections, info


# --------------------------------------------------------------- classification
_CHAPTER_NUM_RX = re.compile(r"(?i)\b(?:chapter|chap\.?|ch\.?|episode|ep\.?)\s*([0-9]+|[IVXLCivxlc]+)\b")
_LEADING_NUM_RX = re.compile(r"^\s*(\d{1,4})\s*[\.\):：-]\s*\S")
_CJK_CHAPTER_RX = re.compile(r"第\s*([零一二三四五六七八九十百千0-9]+)\s*[章节回]")
_PROLOGUE_RX = re.compile(r"(?i)\b(prologue|prolog|proem)\b")
_EPILOGUE_RX = re.compile(r"(?i)\b(epilogue|epilog|final chapter|finale|last chapter)\b")


def _chapter_number_from_label(label: str, chapter_rx: Optional[re.Pattern] = None) -> Optional[int]:
    if chapter_rx:
        m = chapter_rx.match(label)
        if m:
            for g in m.groups() or ():
                if g:
                    n = parse_number(g)
                    if n is not None:
                        return n
    m = _CHAPTER_NUM_RX.search(label)
    if m:
        return parse_number(m.group(1))
    m = _CJK_CHAPTER_RX.search(label)
    if m:
        return parse_number(m.group(1))
    m = _LEADING_NUM_RX.match(label)
    if m:
        return int(m.group(1))
    return None


_HEADING_SUPPLEMENT_RX = re.compile(
    r"(?i)(?:^|[|:\-–—)\]]\s*|\d\s+)("
    r"side[\s_\-]*stor(?:y|ies)|side[\s_\-]*chapter|side[\s_\-]*tale|bonus|extra(?:s)?\b|short[\s_\-]*stor(?:y|ies)|after[\s_\-]*stor(?:y|ies)|"
    r"special[\s_\-]*(?:stor(?:y|ies)|chapter|episode)|omake|gaiden|web[\s_\-]*store|store[\s_\-]*(?:exclusive|bonus)|"
    r"alternate[\s_\-]*(?:pov|perspective)|another[\s_\-]*(?:pov|perspective)|character[\s_\-]*(?:side[\s_\-]*)?stor(?:y|ies)|spin[\s_\-]*off|what[\s_\-]*if|SS(?=\s*\d|[\s_\-:)])"
    r")"
)


def _supplement_kind(text: str, *, has_chapter_number: bool = False) -> Optional[str]:
    if not text:
        return None
    if has_chapter_number:
        # "Chapter 2: An Extra Mile" is a normal chapter; only an explicit
        # supplementary heading (start of a label segment) counts.
        if not _HEADING_SUPPLEMENT_RX.search(text):
            return None
        m = _HEADING_SUPPLEMENT_RX.search(text)
        # The marker must start the label or a label segment, not sit mid-title.
        seg_start = max(text.rfind("|", 0, m.start(1)), text.rfind(":", 0, m.start(1)), text.rfind(")", 0, m.start(1)))
        between = text[seg_start + 1:m.start(1)].strip()
        if between and not re.fullmatch(r"(?i)(chapter|ch\.?|episode|ep\.?)?\s*\d*", between):
            return None
        # With a chapter number present, a bare "bonus"/"extra" adjective is not
        # enough ("Chapter 3: Bonus Points"); require the supplementary noun.
        marker = m.group(1).lower()
        tail = text[m.end(1):m.end(1) + 24].lower()
        if marker in ("bonus", "extra", "extras") and not re.match(r"\s*(stor(?:y|ies)|chapter|episode|content)\b", tail):
            return None
    if not _SUPPLEMENT_RX.search(text):
        return None
    for kind, rx in _SUPPLEMENT_KIND:
        if rx.search(text):
            return kind
    return "side_story"


def classify_sections(
    sections: List[SourceSection],
    *,
    exclude_front_matter: bool = True,
    exclude_afterword: bool = True,
    min_chapter_words: int = DEFAULT_MIN_CHAPTER_WORDS,
    chapter_pattern: Optional[str] = None,
) -> Tuple[List[DetectedChapter], List[str], Optional[int]]:
    """Deterministic section classification.

    Evidence, in priority order: landmark/guide type, manifest properties,
    navigation parent group, explicit supplementary labels (side story / bonus /
    extra / SS ...), prologue / epilogue / interlude labels, chapter numbering,
    front/back-matter hints, position relative to the main-story ending, size.
    """
    chapter_rx = _compile_user_pattern(chapter_pattern, re.IGNORECASE)
    chapters: List[DetectedChapter] = []
    warnings: List[str] = []

    for k, s in enumerate(sections):
        label = s.label
        number = _chapter_number_from_label(label, chapter_rx)
        if number is None and s.filename_label and s.filename_label != label:
            number = _chapter_number_from_label(s.filename_label, chapter_rx)
        ch = DetectedChapter(
            index=k + 1, number=number, title=label, volume=s.volume or "Volume 1", text=s.text,
            word_count=_word_count(s.text), start_line=s.spine_index,
            section_id=_stable_id(s.source_path, s.spine_index, label),
            source_path=s.source_path, spine_index=s.spine_index, source_label=label,
        )
        chapters.append(ch)

    # Pass 1: label-based classification.
    for ch, s in zip(chapters, sections):
        label = ch.title
        evidence_text = " | ".join(x for x in (s.nav_label, s.heading, s.filename_label, s.nav_parent) if x)
        low = evidence_text.lower()
        ev: List[str] = []
        landmark = (s.landmark_type or "").lower()
        props = (s.manifest_properties or "").lower()

        if landmark in ("cover", "toc", "titlepage", "title-page", "copyright-page", "copyright", "dedication", "acknowledgements", "acknowledgments", "frontmatter", "imprint", "colophon") or "cover" in props:
            ch.set_type("front_matter", 0.98, [f"landmark={landmark or props}"])
            continue
        if landmark in ("backmatter", "afterword", "appendix", "glossary", "bibliography", "index", "colophon"):
            ch.set_type("appendix" if landmark in ("appendix", "glossary", "bibliography", "index") else "afterword", 0.95, [f"landmark={landmark}"])
            continue
        if landmark in ("preface", "foreword"):
            ch.set_type("preface", 0.95, [f"landmark={landmark}"])
            continue

        parent_kind = _supplement_kind(s.nav_parent) if s.nav_parent else None
        if parent_kind:
            ch.set_type(parent_kind, 0.95, [f"nav_group='{s.nav_parent}'"])
            continue
        own_kind = _supplement_kind(evidence_text, has_chapter_number=ch.number is not None)
        if own_kind:
            ev.append(f"label='{label}' matches supplementary marker")
            ch.set_type(own_kind, 0.93, ev)
            continue

        if _PROLOGUE_RX.search(low):
            ch.set_type("prologue", 0.9, [f"label='{label}'"])
            continue
        if _EPILOGUE_RX.search(low):
            ch.set_type("main_epilogue", 0.9, [f"label='{label}'"])
            continue
        if any(h in low for h in INTERLUDE_HINTS):
            ch.set_type("main_interlude", 0.85, [f"label='{label}'"])
            continue
        if ch.number is not None:
            ch.set_type("main_chapter", 0.9, [f"chapter number {ch.number} from '{label}'"])
            continue
        if any(h in low for h in APPENDIX_HINTS):
            ch.set_type("appendix", 0.85, [f"label='{label}'"])
            continue
        if any(h in low for h in AFTERWORD_HINTS):
            ch.set_type("afterword", 0.88, [f"label='{label}'"])
            continue
        if any(h in low for h in PREFACE_HINTS):
            ch.set_type("preface", 0.85, [f"label='{label}'"])
            continue
        if any(h in low for h in FRONT_MATTER_HINTS) or (ch.word_count < min_chapter_words and ch.index <= 2):
            ch.set_type("front_matter", 0.8, [f"label='{label}'", f"{ch.word_count} words"])
            continue
        ch.set_type("unknown", 0.4, [f"no structural evidence for '{label}'"])

    # Pass 2: main-story ending boundary = last main_epilogue if present, else last
    # numbered main chapter. Unknown sections after the ending that are short or
    # follow supplementary sections are supplementary; unknown sections inside the
    # main run are interludes.
    main_positions = [i for i, c in enumerate(chapters) if c.section_type in MAIN_STORY_TYPES]
    end_pos: Optional[int] = None
    if main_positions:
        epilogues = [i for i in main_positions if chapters[i].section_type == "main_epilogue"]
        end_pos = max(epilogues) if epilogues else max(main_positions)
        first_main = min(main_positions)
        for i, c in enumerate(chapters):
            if c.section_type != "unknown":
                continue
            if first_main < i < end_pos:
                c.set_type("main_interlude", 0.6, c.classification_evidence + ["untitled section inside the main-story run"])
            elif i > end_pos:
                prev = chapters[i - 1]
                if prev.section_type in ("side_story", "bonus_story", "extra"):
                    c.set_type(prev.section_type, 0.6, c.classification_evidence + ["follows supplementary sections after the main ending"])
                elif any(h in c.title.lower() for h in AFTERWORD_HINTS) or c.word_count < min_chapter_words:
                    c.set_type("afterword", 0.6, c.classification_evidence + ["short back matter after the main ending"])
                else:
                    c.set_type("unknown", 0.4, c.classification_evidence + ["after the main-story ending; needs manual review"])
            elif i < first_main:
                c.set_type("front_matter" if c.word_count < min_chapter_words * 3 else "unknown", 0.5, c.classification_evidence + ["before the first main chapter"])
        # Prologue/epilogue chapters in the wrong place (e.g. a "Prologue" among
        # side stories) are supplementary fiction, not main story.
        for i, c in enumerate(chapters):
            if c.section_type == "prologue" and i > end_pos:
                c.set_type("side_story", 0.7, c.classification_evidence + ["prologue-labelled section after the main ending"])
        # A run of main chapters numbered from 1 again after the ending is a
        # separate story bundled into the file.
        restart = False
        for i in range(end_pos + 1, len(chapters)):
            c = chapters[i]
            if c.section_type == "main_chapter" and (c.number == 1 or restart):
                restart = True
                c.set_type("side_story", 0.65, c.classification_evidence + ["chapter numbering restarts after the main ending"])

    # Trailing back matter: the complete trailing run of afterword-ish sections.
    if chapters:
        i = len(chapters) - 1
        while i >= 0 and chapters[i].section_type in ("afterword", "appendix", "unknown") and any(h in chapters[i].title.lower() for h in AFTERWORD_HINTS + APPENDIX_HINTS):
            if chapters[i].section_type == "unknown":
                chapters[i].set_type("afterword", 0.7, chapters[i].classification_evidence + ["trailing back matter"])
            i -= 1

    # Exclusion toggles: when the user disables front/back-matter exclusion those
    # sections are kept (the preamble becomes a "Preface" chapter).
    for c in chapters:
        if c.source_path == "text:preamble" and c.section_type in ("front_matter", "preface", "unknown"):
            # The preamble is only a "Preface" chapter when the user keeps front matter.
            c.set_type("preface" if not exclude_front_matter else "front_matter", 0.9, ["text before the first chapter heading"])
            c.title = c.source_label = "Preface" if not exclude_front_matter else "Front matter"
        if c.section_type in ("front_matter", "preface") and not exclude_front_matter:
            c.set_type(c.section_type, c.classification_confidence, c.classification_evidence, include_override=True)
            c.exclusion_reason = ""
        if c.section_type in ("afterword", "appendix") and not exclude_afterword:
            c.set_type(c.section_type, c.classification_confidence, c.classification_evidence, include_override=True)
            c.exclusion_reason = ""

    # Warnings: duplicates, missing numbers, tiny chapters, uncertain sections.
    seen: Dict[str, int] = {}
    for c in chapters:
        key = re.sub(r"\s+", " ", c.title.strip().lower())
        if c.included and key in seen:
            c.flags.append("duplicate_title")
            warnings.append(f"Section {c.index} '{c.title}' duplicates section {seen[key]}.")
        elif c.included:
            seen[key] = c.index
        if c.included and c.word_count < min_chapter_words:
            c.flags.append("very_short")
    numbers = [c.number for c in chapters if c.included and c.section_type == "main_chapter" and c.number is not None]
    if numbers:
        expected = set(range(min(numbers), max(numbers) + 1))
        missing = sorted(expected - set(numbers))
        if missing:
            warnings.append(f"Missing chapter numbers: {missing[:20]}{'…' if len(missing) > 20 else ''}")
        dupes = sorted({n for n in numbers if numbers.count(n) > 1})
        if dupes:
            warnings.append(f"Repeated chapter numbers: {dupes[:20]}")
    uncertain = [c for c in chapters if c.section_type == "unknown"]
    if uncertain:
        warnings.append(f"{len(uncertain)} section(s) could not be classified and were excluded pending manual review: {', '.join(c.title for c in uncertain[:5])}{'…' if len(uncertain) > 5 else ''}")
    excluded_supp = [c for c in chapters if c.section_type in ("side_story", "bonus_story", "extra")]
    if excluded_supp:
        warnings.append(f"{len(excluded_supp)} supplementary section(s) (side/bonus/extra stories, {sum(c.word_count for c in excluded_supp):,} words) were excluded from analysis.")
    front_words = sum(c.word_count for c in chapters if c.section_type == "front_matter" and not c.included)
    if front_words:
        warnings.append(f"{front_words} words of front matter were excluded.")

    return chapters, warnings, (end_pos + 1 if end_pos is not None else None)


def detect_chapters(
    text_or_filename: str,
    data: Optional[bytes] = None,
    *,
    chapter_pattern: Optional[str] = None,
    volume_pattern: Optional[str] = None,
    exclude_front_matter: bool = True,
    exclude_afterword: bool = True,
    min_chapter_words: int = DEFAULT_MIN_CHAPTER_WORDS,
    encoding: Optional[str] = None,
) -> DetectionResult:
    """Detect and classify sections.

    ``detect_chapters(text, ...)`` keeps the legacy plain-text signature;
    ``detect_chapters(filename, data, ...)`` parses the file structurally
    (EPUB spine + navigation).
    """
    if data is None:
        sections, warnings, pattern_name, chapter_pattern_used, volume_pattern_used, volumes = _split_text_into_sections(
            text_or_filename, chapter_pattern=chapter_pattern, volume_pattern=volume_pattern,
        )
        info: Dict[str, Any] = {"pattern_name": pattern_name, "chapter_pattern": chapter_pattern_used, "volume_pattern": volume_pattern_used, "volumes": volumes, "warnings": warnings, "meta": {}, "spine_item_count": len(sections)}
    else:
        sections, info = extract_sections(text_or_filename, data, encoding, chapter_pattern=chapter_pattern, volume_pattern=volume_pattern)
    chapters, cls_warnings, end_index = classify_sections(
        sections,
        exclude_front_matter=exclude_front_matter,
        exclude_afterword=exclude_afterword,
        min_chapter_words=max(1, int(min_chapter_words or DEFAULT_MIN_CHAPTER_WORDS)),
        chapter_pattern=info.get("chapter_pattern") or None,
    )
    return DetectionResult(
        chapter_pattern=info.get("chapter_pattern") or "",
        pattern_name=info.get("pattern_name") or "",
        volume_pattern=info.get("volume_pattern") or VOLUME_PATTERN_DEFAULT,
        chapters=chapters,
        volumes=info.get("volumes") or ["Volume 1"],
        warnings=list(info.get("warnings") or []) + cls_warnings,
        total_words=sum(c.word_count for c in chapters),
        main_story_end_index=end_index,
        book_meta=info.get("meta") or {},
        spine_item_count=int(info.get("spine_item_count") or len(chapters)),
    )


# ------------------------------------------------------------------ corrections
def apply_corrections(chapters: List[DetectedChapter], corrections: List[Dict[str, Any]]) -> List[DetectedChapter]:
    """Apply user corrections addressed by stable ``section_id`` (legacy ``index`` accepted), then renumber.

    Operations: exclude, include, rename, merge_with_next, split, set_type.
    Applying the same list twice yields the same result (deterministic).
    """
    result = list(chapters)

    def _find(corr: Dict[str, Any]) -> Optional[int]:
        sid = str(corr.get("section_id") or "").strip()
        if sid:
            return next((i for i, c in enumerate(result) if c.section_id == sid), None)
        idx = corr.get("index")
        if idx is None:
            return None
        return next((i for i, c in enumerate(result) if c.index == int(idx)), None)

    for corr in corrections or []:
        op = str(corr.get("op") or "")
        pos = _find(corr)
        if pos is None:
            continue
        c = result[pos]
        if op == "exclude":
            c.included = False
            c.manually_overridden = True
            c.exclusion_reason = str(corr.get("reason") or "Excluded manually")
            if "excluded" not in c.flags:
                c.flags.append("excluded")
        elif op == "include":
            c.included = True
            c.manually_overridden = True
            c.exclusion_reason = ""
            c.flags = [f for f in c.flags if f not in ("front_matter", "afterword", "side_story", "excluded")]
        elif op == "set_type":
            new_type = str(corr.get("section_type") or "")
            if new_type in SECTION_TYPES:
                c.set_type(new_type, 1.0, [f"set manually to {new_type}"])
                c.manually_overridden = True
        elif op == "rename":
            c.title = str(corr.get("title") or c.title)
            c.manually_overridden = True
        elif op == "merge_with_next" and pos + 1 < len(result):
            b = result[pos + 1]
            c.text = f"{c.text}\n\n{b.text}".strip()
            c.word_count = _word_count(c.text)
            c.flags = [f for f in c.flags if f != "very_short"]
            c.manually_overridden = True
            result.pop(pos + 1)
        elif op == "split":
            marker = str(corr.get("at_text") or "").strip()
            if marker and marker in c.text:
                head, tail = c.text.split(marker, 1)
                c.text = head.strip()
                c.word_count = _word_count(c.text)
                c.manually_overridden = True
                new = DetectedChapter(
                    index=c.index, number=None, title=str(corr.get("new_title") or f"{c.title} (2)"), volume=c.volume,
                    text=(marker + tail).strip(), word_count=_word_count(marker + tail), start_line=c.start_line,
                    section_id=_stable_id(c.section_id, "split", marker[:40]), source_path=c.source_path, spine_index=c.spine_index,
                    source_label=c.source_label, section_type=c.section_type, is_main_story=c.is_main_story, included=c.included,
                    exclusion_reason=c.exclusion_reason, classification_confidence=c.classification_confidence,
                    classification_evidence=list(c.classification_evidence) + ["created by manual split"], manually_overridden=True,
                )
                result.insert(pos + 1, new)
    for i, c in enumerate(result, start=1):
        c.index = i
    return result


def included_chapters(chapters: List[DetectedChapter]) -> List[DetectedChapter]:
    return [c for c in chapters if c.included]


def estimate_input_tokens(chapters: List[DetectedChapter]) -> int:
    """Rough estimate over *included* chapters only: ~1.4 tokens/word + prompt overhead."""
    kept = included_chapters(chapters)
    return int(sum(c.word_count for c in kept) * 1.4) + len(kept) * 1500


# --------------------------------------------------------------------- storage
MANUSCRIPT_FOLDER_TITLE = "Imported Manuscript"


class ManuscriptImportService:
    def __init__(self, session: Session):
        self.session = session

    def _card_type(self, name: str) -> CardType:
        ct = self.session.exec(select(CardType).where(CardType.name == name)).first()
        if not ct:
            raise ValueError(f"Card type not found: {name}")
        return ct

    def _get_or_create_folder(self, project_id: int, title: str) -> Card:
        folder_type = self._card_type("Folder")
        existing = self.session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == folder_type.id, Card.title == title, Card.parent_id.is_(None))).first()
        if existing:
            return existing
        return CardService(self.session).create(CardCreate(title=title, content={}, card_type_id=folder_type.id, parent_id=None), project_id, commit=False)

    def store_manuscript(
        self,
        *,
        project_id: int,
        title: str,
        author: str,
        genre: str,
        language: str,
        chapters: List[DetectedChapter],
        replace_existing: bool = True,
        source_filename: str = "",
        source_bytes: Optional[bytes] = None,
        corrections: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Persist included chapters as Chapter Analysis cards in ONE transaction.

        Identity: ``manuscript_id`` = sha256(file hash + ordered chapter text
        hashes). Re-importing identical bytes with identical corrections is a
        no-op (idempotent). Importing different content replaces the chapter
        cards and marks every artifact derived from the previous manuscript
        stale (analysis cards, fingerprint, genome, reference examples).

        Excluded sections (side stories, front/back matter) are never stored as
        chapters; only their identity is recorded in the folder metadata.
        """
        from app.services.forge import corpus as forge_corpus

        kept = included_chapters(chapters)
        if not kept:
            raise ValueError("No included chapters to import")
        excluded = [c for c in chapters if not c.included]
        file_hash = hashlib.sha256(source_bytes).hexdigest() if source_bytes is not None else ""
        chapter_hashes = [hashlib.sha256(ch.text.encode("utf-8")).hexdigest() for ch in kept]
        manuscript_id = hashlib.sha256(("\x1f".join([file_hash] + chapter_hashes)).encode("utf-8")).hexdigest()[:24]
        now = datetime.now().isoformat(timespec="seconds")
        invalidated = 0
        try:
            analysis_type = self._card_type("Chapter Analysis")
            folder = self._get_or_create_folder(project_id, MANUSCRIPT_FOLDER_TITLE)
            previous_id = (folder.content or {}).get("manuscript_id") if isinstance(folder.content, dict) else None
            existing_cards = self.session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == analysis_type.id, Card.parent_id == folder.id).order_by(Card.display_order, Card.id)).all()

            prev_meta = {k: (folder.content or {}).get(k) for k in ("book_title", "author", "genre", "language", "source_filename")} if isinstance(folder.content, dict) else {}
            same_meta = prev_meta == {"book_title": title, "author": author, "genre": genre, "language": language, "source_filename": source_filename}
            if previous_id == manuscript_id and same_meta and existing_cards and len(existing_cards) == len(kept):
                # Idempotent re-import: identical bytes, corrections and metadata.
                self.session.rollback()
                return {
                    "folder_card_id": folder.id,
                    "chapter_card_ids": [c.id for c in existing_cards],
                    "chapter_count": len(existing_cards),
                    "total_words": sum(c.word_count for c in kept),
                    "excluded_count": len(excluded),
                    "excluded_words": sum(c.word_count for c in excluded),
                    "manuscript_id": manuscript_id,
                    "unchanged": True,
                    "invalidated": 0,
                }

            if existing_cards and not replace_existing:
                raise ValueError("A manuscript is already imported in this project; set replace_existing to replace it")

            if existing_cards:
                for c in existing_cards:
                    self.session.delete(c)
                self.session.flush()
                if previous_id and previous_id != manuscript_id:
                    invalidated = forge_corpus.invalidate_manuscript_dependents(self.session, project_id, previous_id, reason=f"manuscript replaced by {manuscript_id}")

            folder.content = {
                **(folder.content or {}),
                "manuscript_id": manuscript_id,
                "book_title": title,
                "author": author,
                "genre": genre,
                "language": language,
                "source_filename": source_filename,
                "source_file_hash": file_hash,
                "parser_version": PARSER_VERSION,
                "imported_at": now,
                "chapter_count": len(kept),
                "main_story_word_count": sum(c.word_count for c in kept),
                "excluded_sections": [
                    {"section_id": c.section_id, "title": c.title, "section_type": c.section_type, "word_count": c.word_count, "reason": c.exclusion_reason, "spine_index": c.spine_index}
                    for c in excluded
                ],
                "excluded_word_count": sum(c.word_count for c in excluded),
                "corrections": list(corrections or []),
                "previous_manuscript_id": previous_id if previous_id and previous_id != manuscript_id else (folder.content or {}).get("previous_manuscript_id"),
            }
            flag_modified(folder, "content")
            self.session.add(folder)

            service = CardService(self.session)
            card_ids: List[int] = []
            for seq, ch in enumerate(kept, start=1):
                text_hash = chapter_hashes[seq - 1]
                chapter_language = language or forge_corpus.detect_language(ch.text)
                content = {
                    "chapter_number": seq,
                    "title": ch.title,
                    "volume": ch.volume,
                    "word_count": ch.word_count,
                    "summary": "",
                    "source_text": ch.text,
                    "source_chapter_label": ch.number,
                    "source_label": ch.source_label or ch.title,
                    "section_id": ch.section_id,
                    "section_type": ch.section_type,
                    "spine_index": ch.spine_index,
                    "source_path": ch.source_path,
                    "included": True,
                    "is_main_story": ch.is_main_story,
                    "analysis_status": "pending",
                    # Corpus identity (Phase 2)
                    "manuscript_id": manuscript_id,
                    "source_project_id": project_id,
                    "source_filename": source_filename,
                    "source_file_hash": file_hash,
                    "chapter_id": forge_corpus.chapter_id(manuscript_id, seq, text_hash),
                    "original_chapter_number": ch.number,
                    "normalized_chapter_number": seq,
                    "source_order": seq,
                    "language": chapter_language,
                    "char_count": len(ch.text),
                    "unit_count": forge_corpus.count_units(ch.text, chapter_language),
                    "source_text_hash": text_hash,
                    "imported_at": now,
                    "parser_version": PARSER_VERSION,
                    "correction_history": [c for c in (corrections or []) if str(c.get("section_id") or "") == ch.section_id],
                    "flags": list(ch.flags),
                }
                card = service.create(CardCreate(title=f"Ch {seq:04d} · {ch.title}"[:200], content=content, card_type_id=analysis_type.id, parent_id=folder.id), project_id, commit=False)
                card_ids.append(card.id)
            self.session.commit()
        except Exception:
            self.session.rollback()
            logger.exception(f"[ManuscriptImport] import failed for project {project_id}; rolled back")
            raise
        logger.info(f"[ManuscriptImport] stored {len(card_ids)} chapters for project {project_id} manuscript {manuscript_id} ({len(excluded)} sections excluded, {invalidated} dependents invalidated)")
        return {
            "folder_card_id": folder.id,
            "chapter_card_ids": card_ids,
            "chapter_count": len(card_ids),
            "total_words": sum(c.word_count for c in kept),
            "excluded_count": len(excluded),
            "excluded_words": sum(c.word_count for c in excluded),
            "manuscript_id": manuscript_id,
            "unchanged": False,
            "invalidated": invalidated,
        }

    def list_manuscript(self, project_id: int) -> Dict[str, Any]:
        analysis_type = self._card_type("Chapter Analysis")
        folder_type = self._card_type("Folder")
        folder = self.session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == folder_type.id, Card.title == MANUSCRIPT_FOLDER_TITLE)).first()
        if not folder:
            return {"folder_card_id": None, "meta": {}, "chapters": []}
        cards = self.session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == analysis_type.id, Card.parent_id == folder.id).order_by(Card.display_order, Card.id)).all()
        chapters = []
        for c in cards:
            content = c.content if isinstance(c.content, dict) else {}
            chapters.append({
                "card_id": c.id,
                "chapter_number": content.get("chapter_number"),
                "title": content.get("title") or c.title,
                "volume": content.get("volume"),
                "word_count": content.get("word_count"),
                "section_type": content.get("section_type") or "main_chapter",
                "source_label": content.get("source_label") or content.get("title") or c.title,
                "analysis_status": content.get("analysis_status") or ("done" if content.get("summary") else "pending"),
                "scene_count": len(content.get("scenes") or []),
                "chapter_id": content.get("chapter_id"),
                "manuscript_id": content.get("manuscript_id"),
                "source_text_hash": content.get("source_text_hash"),
                "language": content.get("language"),
                "char_count": content.get("char_count"),
                "evidence_verified": content.get("evidence_verified"),
                "evidence_total": content.get("evidence_total"),
                "flags": content.get("flags") or [],
            })
        return {"folder_card_id": folder.id, "meta": {k: v for k, v in (folder.content or {}).items()}, "chapters": chapters}
