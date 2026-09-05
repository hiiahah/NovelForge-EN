"""Source -> original transfer: Abstract Mechanism Library and original project creation.

Pipeline (Phase 6):
  Source Project -> Evidence Index -> Narrative Fingerprint -> Narrative Genome
  -> Abstract Mechanism Library -> Originality Transformation -> Original Project Bible.

Producer: ``create_original_project`` / ``import_transformation``.
Consumers: original project cards, manifest link (``source_project_id``), firewall.

The original project receives only: the Narrative Fingerprint (measurable,
entity-free), the Abstract Mechanism Library (genome patterns stripped of
names/terms and firewall-checked) and whatever *original* premise the planning
model or the user supplies. Nothing with a source entity passes the firewall.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType, Project
from app.schemas.card import CardCreate
from app.schemas.project import ProjectCreate
from app.services import project_service
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge import firewall as fw
from app.services.forge import provenance
from app.services.forge.corpus import load_source_chapters

TRANSFER_VERSION = "transfer-1"
MECHANISM_TYPE = "Abstract Mechanism"
FINGERPRINT_TYPE = "Narrative Fingerprint"


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _type(session: Session, name: str) -> CardType:
    ct = session.exec(select(CardType).where(CardType.name == name)).first()
    if ct is None:
        raise ValueError(f"Card type '{name}' is not bootstrapped")
    return ct


def abstract_mechanisms(genome: Dict[str, Any], profile: fw.SourceProfile) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Convert genome patterns into entity-free mechanisms. Returns (accepted, rejected)."""
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    for p in genome.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        mech = {
            "mechanism_id": f"{p.get('dimension')}:{abs(hash(str(p.get('transferable_abstraction') or p.get('description')))) % 10**8}",
            "dimension": p.get("dimension"),
            "abstraction": str(p.get("transferable_abstraction") or ""),
            "typical_sequence": [str(s) for s in (p.get("typical_sequence") or [])],
            "conditions": [str(s) for s in (p.get("conditions") or [])],
            "variations": [str(s) for s in (p.get("variations") or [])],
            "risks": [str(s) for s in (p.get("risks") or [])],
            "why_it_works": str(p.get("why_it_works") or ""),
            "average_cycle_chapters": str(p.get("average_cycle_chapters") or ""),
            "evidence_chapters": sorted({int(e.get("chapter_number")) for e in (p.get("evidence") or []) if isinstance(e, dict) and isinstance(e.get("chapter_number"), int)}),
        }
        text = " ".join([mech["abstraction"], *mech["typical_sequence"], *mech["conditions"], *mech["variations"], mech["why_it_works"]])
        report = fw.check_text(text, profile, max_phrase_hits=0)
        leaks = [f for f in report.findings if f.check in ("entity_overlap", "distinctive_term_overlap", "long_phrase_overlap", "dialogue_overlap", "location_similarity", "object_similarity")]
        if leaks:
            rejected.append({**mech, "rejected_because": [f.as_dict() for f in leaks]})
        else:
            accepted.append(mech)
    return accepted, rejected


@dataclass
class OriginalProjectResult:
    project_id: int
    name: str
    fingerprint_card_id: int
    mechanism_card_ids: List[int]
    rejected_mechanisms: List[Dict[str, Any]]
    firewall: Dict[str, Any]


def create_original_project(
    session: Session,
    *,
    source_project_id: int,
    name: str,
    description: str = "",
    template: Optional[str] = None,
) -> OriginalProjectResult:
    """Create a strictly separate original project linked to the source project.

    Copies the entity-free Narrative Fingerprint and the firewall-checked
    Abstract Mechanism Library. Never copies analysis, ledger or entity cards.
    """
    bible = BibleService(session)
    fp_card = bible.singleton(source_project_id, FINGERPRINT_TYPE)
    if fp_card is None or not _c(fp_card).get("layers"):
        raise ValueError("Build the Narrative Fingerprint in the source project first")
    if _c(fp_card).get("stale"):
        raise ValueError("The Narrative Fingerprint is stale; rebuild it before creating an original project")
    chapters = load_source_chapters(session, source_project_id)
    if not chapters:
        raise ValueError("Source project has no imported manuscript")
    names: List[str] = []
    for card in bible.cards_of_type(source_project_id, "Character Card") + bible.cards_of_type(source_project_id, "Organization Card") + bible.cards_of_type(source_project_id, "Scene Card") + bible.cards_of_type(source_project_id, "Item Card"):
        c = _c(card)
        names.append(str(c.get("name") or card.title))
        names += [str(a) for a in (c.get("aliases") or [])]
    profile = fw.SourceProfile.from_chapters(chapters, manuscript_id=chapters[0].manuscript_id, entity_names=names)

    project, _ = project_service.create_project(session, ProjectCreate(name=name, description=description or f"Original project derived from source project {source_project_id} (mechanisms only)", template=template))
    fp_content = {k: v for k, v in _c(fp_card).items() if k not in ("per_chapter_metrics",)}
    fp_content["source_project_id"] = source_project_id
    fp_content["derived_from_card_id"] = fp_card.id
    fp_type = _type(session, FINGERPRINT_TYPE)
    new_fp = CardService(session).create(CardCreate(title="Narrative Fingerprint", content=fp_content, card_type_id=fp_type.id), project.id, commit=False)
    provenance.record(session, project_id=project.id, artifact_kind=FINGERPRINT_TYPE, artifact_key=str(new_fp.id), content=fp_content, upstream=[provenance.Upstream(FINGERPRINT_TYPE, str(fp_card.id), provenance.card_hash(fp_card))], producer=TRANSFER_VERSION, schema_version=fp_content.get("version", ""), card_id=new_fp.id)

    mech_ids: List[int] = []
    rejected: List[Dict[str, Any]] = []
    genome = bible.singleton(source_project_id, "Narrative Genome")
    if genome is not None and _c(genome).get("patterns"):
        accepted, rejected = abstract_mechanisms(_c(genome), profile)
        mech_type = _type(session, MECHANISM_TYPE)
        for mech in accepted:
            card = CardService(session).create(CardCreate(title=f"Mechanism · {mech['dimension']}", content={**mech, "source_project_id": source_project_id}, card_type_id=mech_type.id), project.id, commit=False)
            provenance.record(session, project_id=project.id, artifact_kind=MECHANISM_TYPE, artifact_key=str(card.id), content=mech, upstream=[provenance.Upstream("Narrative Genome", str(genome.id), provenance.card_hash(genome))], producer=TRANSFER_VERSION, card_id=card.id)
            mech_ids.append(card.id)

    manifest = provenance.get_manifest(session, project.id, create=True)
    manifest.project_role = "original"
    manifest.source_project_id = source_project_id
    manifest.source_manuscript_id = chapters[0].manuscript_id
    manifest.fingerprint_revision = 1
    manifest.context_compiler_version = provenance.COMPILER_VERSION
    session.add(manifest)
    src_manifest = provenance.get_manifest(session, source_project_id, create=True)
    src_manifest.project_role = "source"
    src_manifest.source_manuscript_id = chapters[0].manuscript_id
    session.add(src_manifest)
    session.commit()

    report = isolation_report(session, project.id)
    return OriginalProjectResult(project_id=project.id, name=project.name, fingerprint_card_id=new_fp.id, mechanism_card_ids=mech_ids, rejected_mechanisms=rejected, firewall=report)


def isolation_report(session: Session, original_project_id: int) -> Dict[str, Any]:
    """Verify structural separation: no source-only card types, no source entity names, no manuscript text."""
    bible = BibleService(session)
    manifest = provenance.get_manifest(session, original_project_id, create=True)
    problems: List[Dict[str, Any]] = []
    if manifest.project_role != "original":
        # Isolation is a property of original projects; a source project legitimately holds source material.
        return {"project_id": original_project_id, "source_project_id": None, "isolated": False, "not_applicable": True, "problems": [], "firewall": {"passed": True, "skipped": f"project role is '{manifest.project_role}'"}}
    for type_name in ("Chapter Analysis", "Narrative Genome", "Story Structure Map", "Emotional Rhythm"):
        for card in bible.cards_of_type(original_project_id, type_name):
            problems.append({"kind": "source_card_type_present", "card_id": card.id, "card_type": type_name})
    firewall: Dict[str, Any] = {"passed": True, "skipped": "no source project linked"}
    if manifest.source_project_id:
        chapters = load_source_chapters(session, manifest.source_project_id)
        names: List[str] = []
        for card in bible.cards_of_type(manifest.source_project_id, "Character Card") + bible.cards_of_type(manifest.source_project_id, "Organization Card") + bible.cards_of_type(manifest.source_project_id, "Scene Card") + bible.cards_of_type(manifest.source_project_id, "Item Card"):
            c = _c(card)
            names.append(str(c.get("name") or card.title))
            names += [str(a) for a in (c.get("aliases") or [])]
        if chapters:
            profile = fw.SourceProfile.from_chapters(chapters, manuscript_id=chapters[0].manuscript_id, entity_names=names)
            cards = []
            for type_name in ("Character Card", "Organization Card", "Scene Card", "Item Card", "Concept Card", "Story Foundation", "Reader Contract", "Theme Map", "Chapter Outline", "Volume Outline", "Stage Outline", "Plot Thread", "Promise Payoff", "Knowledge Fact", "World Rule", "Power System", "Chapter Text"):
                for card in bible.cards_of_type(original_project_id, type_name):
                    cards.append({"card_type": type_name, "title": card.title, "content": _c(card), "card_id": card.id})
            rep = fw.check_bible_cards(cards, profile)
            firewall = rep.as_dict()
            for f in rep.critical:
                problems.append({"kind": "source_leak", "check": f.check, "detail": f.detail, "matched": f.matched})
    return {"project_id": original_project_id, "source_project_id": manifest.source_project_id, "isolated": not problems, "problems": problems, "firewall": firewall}


__all__ = ["FINGERPRINT_TYPE", "MECHANISM_TYPE", "TRANSFER_VERSION", "OriginalProjectResult", "abstract_mechanisms", "create_original_project", "isolation_report"]
