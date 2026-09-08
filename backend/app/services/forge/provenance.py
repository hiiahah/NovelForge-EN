"""Artifact dependency graph, stale propagation and the Project Narrative Manifest.

Producer: every Forge stage that writes a card or index calls ``record``.
Consumers: ``compiler`` (refuses stale mandatory dependencies), UI status,
audits, ``mark_card_changed`` hook on card save.

Dependency edges are stored per artifact as ``upstream = [{kind, key, hash}]``.
``dependency_hash`` is the hash of the upstream hashes at production time, so
staleness is *computed*: an artifact is stale when any upstream's current
content hash differs from the recorded one (or when explicitly flagged).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlmodel import Session, select

from app.db.models import ArtifactProvenance, Card, ProjectManifest
from app.services.forge.corpus import manuscript_meta
from app.services.forge.textmetrics import sha256_text

COMPILER_VERSION = "chapter-compiler-1"

# The canonical dependency chain of an original project. Editing a card of
# kind K marks every kind downstream of K stale for the affected chapters.
DOWNSTREAM: Dict[str, Tuple[str, ...]] = {
    "Creative Compass": ("chapter_context",),
    "Story Foundation": ("Reader Contract", "Theme Map", "Narrative Architecture", "Core Blueprint", "Volume Outline", "Stage Outline", "Chapter Outline", "chapter_context"),
    "Reader Contract": ("Narrative Architecture", "Core Blueprint", "Volume Outline", "Stage Outline", "Chapter Outline", "chapter_context"),
    "Theme Map": ("Narrative Architecture", "Volume Outline", "Stage Outline", "Chapter Outline", "chapter_context"),
    "Narrative Architecture": ("Core Blueprint", "Volume Outline", "Stage Outline", "Chapter Outline", "chapter_context"),
    "Core Blueprint": ("Volume Outline", "Stage Outline", "Chapter Outline", "chapter_context"),
    "Volume Outline": ("Stage Outline", "Chapter Outline", "chapter_context"),
    "Stage Outline": ("Chapter Outline", "chapter_context"),
    "Chapter Outline": ("chapter_context",),
    "Narrative Fingerprint": ("chapter_context",),
    "Style Profile": ("chapter_context",),
    "Character Card": ("chapter_context",),
    "Relationship Arc": ("chapter_context",),
    "Knowledge Fact": ("chapter_context",),
    "Plot Thread": ("chapter_context",),
    "Promise Payoff": ("chapter_context",),
    "Timeline Event": ("chapter_context",),
    "World Rule": ("chapter_context",),
    "Power System": ("chapter_context",),
    "Organization Card": ("chapter_context",),
    "Scene Card": ("chapter_context",),
    "Item Card": ("chapter_context",),
}
MANDATORY_FOR_GENERATION = ("Chapter Outline", "Narrative Fingerprint", "Story Foundation", "Reader Contract")


def content_hash(content: Any) -> str:
    try:
        return sha256_text(json.dumps(content, sort_keys=True, ensure_ascii=False, default=str))
    except Exception:
        return sha256_text(str(content))


def card_hash(card: Card) -> str:
    return content_hash({"title": card.title, "content": card.content if isinstance(card.content, dict) else {}})


@dataclass
class Upstream:
    kind: str
    key: str
    hash: str

    def as_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "key": self.key, "hash": self.hash}


def upstream_of_card(card: Card) -> Upstream:
    kind = getattr(card.card_type, "name", "") or "Card"
    return Upstream(kind=kind, key=str(card.id), hash=card_hash(card))


def record(
    session: Session,
    *,
    project_id: int,
    artifact_kind: str,
    artifact_key: str,
    content: Any,
    upstream: Sequence[Upstream],
    producer: str,
    card_id: Optional[int] = None,
    model_role: str = "",
    model_name: str = "",
    prompt_version: str = "",
    schema_version: str = "",
) -> ArtifactProvenance:
    """Create or bump the provenance row for one artifact (no commit)."""
    row = session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == project_id, ArtifactProvenance.artifact_kind == artifact_kind, ArtifactProvenance.artifact_key == str(artifact_key))).first()
    ups = [u.as_dict() for u in upstream]
    dep_hash = sha256_text("|".join(f"{u['kind']}:{u['key']}:{u['hash']}" for u in ups))
    if row is None:
        row = ArtifactProvenance(project_id=project_id, artifact_kind=artifact_kind, artifact_key=str(artifact_key))
    else:
        row.version += 1
    row.card_id = card_id
    row.content_hash = content_hash(content)
    row.upstream = ups
    row.dependency_hash = dep_hash
    row.producer = producer
    row.model_role = model_role
    row.model_name = model_name
    row.prompt_version = prompt_version
    row.schema_version = schema_version
    row.stale = False
    row.stale_reason = None
    row.updated_at = datetime.now()
    session.add(row)
    session.flush()
    return row


def get(session: Session, project_id: int, artifact_kind: str, artifact_key: str) -> Optional[ArtifactProvenance]:
    return session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == project_id, ArtifactProvenance.artifact_kind == artifact_kind, ArtifactProvenance.artifact_key == str(artifact_key))).first()


def current_hash_of(session: Session, up: Dict[str, Any], project_id: Optional[int] = None) -> Optional[str]:
    """Current content hash of an upstream reference (card, manuscript, or another artifact)."""
    kind, key = str(up.get("kind") or ""), str(up.get("key") or "")
    if kind == "manuscript":
        # A manuscript upstream is identified by its manuscript_id (content-derived);
        # it is "current" while the project's imported manuscript still carries that id.
        if project_id is None:
            return None
        current = manuscript_meta(session, project_id).get("manuscript_id")
        return str(current) if current else None
    if key.isdigit():
        card = session.get(Card, int(key))
        if card is not None:
            return card_hash(card)
    row = session.exec(select(ArtifactProvenance).where(ArtifactProvenance.artifact_kind == kind, ArtifactProvenance.artifact_key == key)).first()
    return row.content_hash if row else None


def is_stale(session: Session, row: ArtifactProvenance) -> Tuple[bool, List[str]]:
    """Computed staleness: explicit flag OR any upstream hash changed / disappeared."""
    reasons: List[str] = []
    if row.stale:
        reasons.append(row.stale_reason or "flagged stale")
    for up in row.upstream or []:
        cur = current_hash_of(session, up, row.project_id)
        if cur is None:
            reasons.append(f"upstream {up.get('kind')}#{up.get('key')} missing")
        elif cur != up.get("hash"):
            reasons.append(f"upstream {up.get('kind')}#{up.get('key')} changed")
    return bool(reasons), reasons


def mark_card_changed(session: Session, card: Card) -> Dict[str, Any]:
    """Propagate a card change: flag every artifact that lists it upstream, and
    every downstream kind (per DOWNSTREAM) in the same project. Returns the
    affected chapter range. No commit."""
    kind = getattr(card.card_type, "name", "") or ""
    affected: List[int] = []
    rows = session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == card.project_id)).all()
    kinds_down = set(DOWNSTREAM.get(kind, ()))
    for row in rows:
        depends = any(str(u.get("key")) == str(card.id) for u in (row.upstream or []))
        if depends or row.artifact_kind in kinds_down:
            if not row.stale:
                row.stale = True
                row.stale_reason = f"{kind} '{card.title}' (card {card.id}) changed"
                row.updated_at = datetime.now()
                session.add(row)
            if row.artifact_kind == "chapter_context" and row.artifact_key.isdigit():
                affected.append(int(row.artifact_key))
    manifest = get_manifest(session, card.project_id, create=True)
    manifest.stale_dependency_count = count_stale(session, card.project_id)
    manifest.updated_at = datetime.now()
    session.add(manifest)
    return {"kind": kind, "card_id": card.id, "affected_chapters": sorted(set(affected)), "stale_count": manifest.stale_dependency_count}


def count_stale(session: Session, project_id: int) -> int:
    rows = session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == project_id)).all()
    return sum(1 for r in rows if is_stale(session, r)[0])


def stale_artifacts(session: Session, project_id: int) -> List[Dict[str, Any]]:
    out = []
    for r in session.exec(select(ArtifactProvenance).where(ArtifactProvenance.project_id == project_id)).all():
        stale, reasons = is_stale(session, r)
        if stale:
            out.append({"artifact_kind": r.artifact_kind, "artifact_key": r.artifact_key, "card_id": r.card_id, "reasons": reasons})
    return out


def get_manifest(session: Session, project_id: int, *, create: bool = False) -> Optional[ProjectManifest]:
    m = session.exec(select(ProjectManifest).where(ProjectManifest.project_id == project_id)).first()
    if m is None and create:
        m = ProjectManifest(project_id=project_id, context_compiler_version=COMPILER_VERSION)
        session.add(m)
        session.flush()
    return m


def manifest_dict(session: Session, project_id: int) -> Dict[str, Any]:
    m = get_manifest(session, project_id, create=True)
    return {
        "project_id": m.project_id,
        "project_role": m.project_role,
        "source_project_id": m.source_project_id,
        "source_manuscript_id": m.source_manuscript_id,
        "canon_revision": m.canon_revision,
        "outline_revision": m.outline_revision,
        "fingerprint_revision": m.fingerprint_revision,
        "latest_committed_chapter": m.latest_committed_chapter,
        "next_allowed_chapter": m.next_allowed_chapter,
        "context_compiler_version": m.context_compiler_version or COMPILER_VERSION,
        "unresolved_errors": list(m.unresolved_errors or []),
        "stale_dependency_count": count_stale(session, project_id),
        "stale_artifacts": stale_artifacts(session, project_id)[:50],
        "last_sync_status": m.last_sync_status,
        "last_sync_chapter": m.last_sync_chapter,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
    }


__all__ = [
    "COMPILER_VERSION", "DOWNSTREAM", "MANDATORY_FOR_GENERATION", "Upstream", "card_hash", "content_hash", "count_stale", "get",
    "get_manifest", "is_stale", "manifest_dict", "mark_card_changed", "record", "stale_artifacts", "upstream_of_card",
]
