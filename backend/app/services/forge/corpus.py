"""Source corpus identity and integrity.

Producer: ``ManuscriptImportService.store_manuscript``.
Consumers: evidence verification, fingerprint, reference example library,
originality firewall, source/original isolation checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import ArtifactProvenance, Card, CardType, ReferenceExample
from app.services.forge.textmetrics import count_units, detect_language, sha256_text, stable_id

MANUSCRIPT_FOLDER_TITLE = "Imported Manuscript"
# Artifact kinds whose validity depends on the exact manuscript bytes.
MANUSCRIPT_DEPENDENT_KINDS = ("chapter_analysis", "narrative_fingerprint", "narrative_genome", "reference_example_index", "story_structure_map", "emotional_rhythm", "evidence_index")


def chapter_id(manuscript_id: str, normalized_number: int, text_hash: str) -> str:
    return f"{manuscript_id[:12]}-c{int(normalized_number):04d}-{text_hash[:10]}"


@dataclass
class SourceChapter:
    card_id: int
    chapter_id: str
    manuscript_id: str
    chapter_number: int
    original_chapter_number: Optional[int]
    title: str
    volume: str
    section_type: str
    is_main_story: bool
    language: str
    text: str
    text_hash: str
    analysis: Dict[str, Any] = field(default_factory=dict)

    @property
    def unit_count(self) -> int:
        return count_units(self.text, self.language)


def _type(session: Session, name: str) -> Optional[CardType]:
    return session.exec(select(CardType).where(CardType.name == name)).first()


def manuscript_folder(session: Session, project_id: int) -> Optional[Card]:
    ft = _type(session, "Folder")
    if not ft:
        return None
    return session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == ft.id, Card.title == MANUSCRIPT_FOLDER_TITLE, Card.parent_id.is_(None))).first()


def manuscript_meta(session: Session, project_id: int) -> Dict[str, Any]:
    folder = manuscript_folder(session, project_id)
    return dict(folder.content) if folder and isinstance(folder.content, dict) else {}


def load_source_chapters(session: Session, project_id: int, *, include_excluded: bool = False) -> List[SourceChapter]:
    """Ordered imported chapters with their identity and any stored analysis."""
    ct = _type(session, "Chapter Analysis")
    if not ct:
        return []
    folder = manuscript_folder(session, project_id)
    stmt = select(Card).where(Card.project_id == project_id, Card.card_type_id == ct.id)
    if folder:
        stmt = stmt.where(Card.parent_id == folder.id)
    cards = session.exec(stmt.order_by(Card.display_order, Card.id)).all()
    out: List[SourceChapter] = []
    for c in cards:
        content = c.content if isinstance(c.content, dict) else {}
        text = str(content.get("source_text") or "")
        if not text.strip():
            continue
        if not include_excluded and content.get("included") is False:
            continue
        text_hash = str(content.get("source_text_hash") or sha256_text(text))
        manuscript = str(content.get("manuscript_id") or "")
        number = int(content.get("normalized_chapter_number") or content.get("chapter_number") or len(out) + 1)
        out.append(SourceChapter(
            card_id=c.id,
            chapter_id=str(content.get("chapter_id") or chapter_id(manuscript or "legacy", number, text_hash)),
            manuscript_id=manuscript,
            chapter_number=number,
            original_chapter_number=content.get("original_chapter_number", content.get("source_chapter_label")),
            title=str(content.get("title") or c.title),
            volume=str(content.get("volume") or ""),
            section_type=str(content.get("section_type") or "main_chapter"),
            is_main_story=bool(content.get("is_main_story", True)),
            language=str(content.get("language") or detect_language(text)),
            text=text,
            text_hash=text_hash,
            analysis={k: v for k, v in content.items() if k != "source_text"},
        ))
    out.sort(key=lambda ch: ch.chapter_number)
    return out


def integrity_report(chapters: List[SourceChapter]) -> Dict[str, Any]:
    """Deterministic corpus checks: gaps, duplicates, outliers, hash mismatches."""
    issues: List[Dict[str, Any]] = []
    seen_hash: Dict[str, int] = {}
    numbers = [ch.chapter_number for ch in chapters]
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        issues.append({"kind": "non_contiguous_numbering", "detail": numbers[:30]})
    units = [ch.unit_count for ch in chapters]
    median = sorted(units)[len(units) // 2] if units else 0
    for ch in chapters:
        if ch.text_hash in seen_hash:
            issues.append({"kind": "duplicate_chapter", "chapter": ch.chapter_number, "duplicate_of": seen_hash[ch.text_hash]})
        else:
            seen_hash[ch.text_hash] = ch.chapter_number
        if sha256_text(ch.text) != ch.text_hash:
            issues.append({"kind": "hash_mismatch", "chapter": ch.chapter_number})
        if median and ch.unit_count < max(40, median * 0.2):
            issues.append({"kind": "suspiciously_short", "chapter": ch.chapter_number, "units": ch.unit_count})
        if median and ch.unit_count > median * 4:
            issues.append({"kind": "suspiciously_long", "chapter": ch.chapter_number, "units": ch.unit_count})
        if not ch.is_main_story:
            issues.append({"kind": "non_main_story_included", "chapter": ch.chapter_number, "section_type": ch.section_type})
    manuscripts = {ch.manuscript_id for ch in chapters if ch.manuscript_id}
    if len(manuscripts) > 1:
        issues.append({"kind": "mixed_manuscripts", "detail": sorted(manuscripts)})
    return {
        "chapters": len(chapters),
        "manuscript_id": next(iter(manuscripts)) if len(manuscripts) == 1 else None,
        "language": max(((ch.language, 1) for ch in chapters), key=lambda kv: kv[1])[0] if chapters else "und",
        "issues": issues,
        "ok": not any(i["kind"] in ("duplicate_chapter", "hash_mismatch", "mixed_manuscripts", "non_main_story_included") for i in issues),
    }


def invalidate_manuscript_dependents(session: Session, project_id: int, manuscript_id: str, *, reason: str) -> int:
    """Mark every artifact derived from ``manuscript_id`` stale and drop its example index.

    Called inside the import transaction (no commit here).
    """
    count = 0
    rows = session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == project_id, ArtifactProvenance.artifact_kind.in_(MANUSCRIPT_DEPENDENT_KINDS))).all()
    for row in rows:
        depends = any(str(u.get("id") or "") == manuscript_id or u.get("kind") == "manuscript" for u in (row.upstream or []))
        if depends or not row.upstream:
            row.stale = True
            row.stale_reason = reason
            session.add(row)
            count += 1
    examples = session.exec(select(ReferenceExample).where(ReferenceExample.project_id == project_id, ReferenceExample.manuscript_id == manuscript_id)).all()
    for ex in examples:
        session.delete(ex)
        count += 1
    # Analysis-derived singleton cards in the source project are stale too.
    for type_name in ("Narrative Fingerprint", "Narrative Genome", "Story Structure Map", "Emotional Rhythm"):
        ct = _type(session, type_name)
        if not ct:
            continue
        for card in session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == ct.id)).all():
            content = card.content if isinstance(card.content, dict) else {}
            if content.get("manuscript_id") in (None, manuscript_id):
                content = {**content, "stale": True, "stale_reason": reason}
                card.content = content
                flag_modified(card, "content")
                session.add(card)
                count += 1
    return count


def redact_for_log(text: str, keep: int = 0) -> str:
    """Never log manuscript prose: log its length and hash only."""
    text = text or ""
    return f"<text len={len(text)} sha256={sha256_text(text)[:12]}>"


__all__ = [
    "MANUSCRIPT_FOLDER_TITLE", "SourceChapter", "chapter_id", "count_units", "detect_language", "integrity_report",
    "invalidate_manuscript_dependents", "load_source_chapters", "manuscript_folder", "manuscript_meta", "redact_for_log", "stable_id",
]
