"""Source -> original transfer: Abstract Mechanism Library and original project creation.

Pipeline (Phase 6):
  Source Project -> Evidence Index -> Narrative Fingerprint -> Narrative Genome
  -> Abstract Mechanism Library -> Originality Transformation -> Original Project Bible.

Producer: ``create_original_project`` / ``import_transformation``.
Consumers: original project cards, manifest link (``source_project_id``), firewall.

The original receives an English target projection and screened, evidence-linked
lessons. Source observations, prose and ordered scene sequences stay in the study.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from app.db.models import Card, CardType
from app.schemas.card import CardCreate
from app.schemas.creative import InfluenceLesson
from app.schemas.project import ProjectCreate
from app.services import project_service
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge import firewall as fw
from app.services.forge import provenance
from app.services.forge.corpus import load_source_chapters, manuscript_meta
from app.services.forge.fingerprint import english_target_fingerprint
from app.services.forge.textmetrics import detect_language, stable_id

TRANSFER_VERSION = "transfer-2"
MECHANISM_TYPE = "Abstract Mechanism"
FINGERPRINT_TYPE = "Narrative Fingerprint"
_DIMENSIONS = {
    "protagonist_engine", "conflict_engine", "escalation_engine", "reward_engine", "progression_loop",
    "mystery_engine", "relationship_engine", "world_expansion_loop", "chapter_hook_engine", "antagonist_pattern",
    "arc_length_pattern", "reveal_frequency", "tension_release_pattern", "character_retention_engine",
}


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _type(session: Session, name: str) -> CardType:
    ct = session.exec(select(CardType).where(CardType.name == name)).first()
    if ct is None:
        raise ValueError(f"Card type '{name}' is not bootstrapped")
    return ct


def _bounded(text: str, limit: int = 500) -> str:
    text = text.strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def _spread(values: List[Any], limit: int) -> List[Any]:
    if len(values) <= limit:
        return values
    return [values[i * (len(values) - 1) // (limit - 1)] for i in range(limit)]


def source_profile(session: Session, source_project_id: int) -> Optional[fw.SourceProfile]:
    """Source-only collision index, shared by transfer and original ideation."""
    chapters = load_source_chapters(session, source_project_id)
    if not chapters:
        return None
    bible = BibleService(session)
    names: List[str] = []
    roles: Dict[str, str] = {}
    locations: List[str] = []
    objects: List[str] = []
    for type_name in ("Character Card", "Organization Card", "Scene Card", "Item Card", "Concept Card"):
        for card in bible.cards_of_type(source_project_id, type_name):
            content = _c(card)
            name = str(content.get("name") or card.title).strip()
            names.extend([name, *[a for a in content.get("aliases") or [] if isinstance(a, str)]])
            if type_name == "Character Card":
                roles[name] = str(content.get("role_type") or "character")
            elif type_name == "Scene Card":
                locations.append(name)
            elif type_name == "Item Card":
                objects.append(name)
    summaries: List[str] = []
    beats: List[str] = []
    for chapter in chapters:
        analysis = chapter.analysis or {}
        names.extend(p for p in analysis.get("participants") or [] if isinstance(p, str))
        names.extend(p for p in analysis.get("locations") or [] if isinstance(p, str))
        for scene in analysis.get("scenes") or []:
            if not isinstance(scene, dict):
                continue
            summaries.append(str(scene.get("summary") or scene.get("goal") or ""))
            if scene.get("function"):
                beats.append(str(scene["function"]))
    return fw.SourceProfile.from_chapters(
        chapters, manuscript_id=chapters[0].manuscript_id, entity_names=names,
        scene_summaries=summaries, beat_sequence=beats, character_roles=roles,
        locations=locations, objects=objects,
    )


def abstract_mechanisms(
    genome: Dict[str, Any], profile: fw.SourceProfile, *, verified_chapters: Optional[Iterable[int]] = None,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Screen all exportable fields before bounding them; source-only fields never transfer."""
    accepted: List[Dict[str, Any]] = []
    rejected: List[Dict[str, Any]] = []
    verified = set(verified_chapters) if verified_chapters is not None else None
    seen = set()
    for p in genome.get("patterns") or []:
        if not isinstance(p, dict):
            continue
        dimension = p.get("dimension") if p.get("dimension") in _DIMENSIONS else "unknown"
        fields = {key: p.get(key) for key in ("transferable_abstraction", "why_it_works", "conditions", "variations", "risks")}
        report = fw.check_payload(fields, profile, max_phrase_hits=0)
        reasons = sorted({finding.check for finding in report.critical})
        abstraction = p.get("transferable_abstraction")
        if not isinstance(abstraction, str) or not abstraction.strip():
            reasons.append("missing_abstraction")
        if any(p.get(key) is not None and not isinstance(p[key], str) for key in ("transferable_abstraction", "why_it_works")):
            reasons.append("invalid_lesson_fields")
        if any(not isinstance(p.get(key) or [], list) or any(not isinstance(item, str) for item in p.get(key) or []) for key in ("conditions", "variations", "risks")):
            reasons.append("invalid_lesson_fields")
        if detect_language(" ".join(fw.text_values(fields))) in ("ko", "zh"):
            reasons.append("english_adaptation_needed")
        cited = sorted({e["chapter_number"] for e in p.get("evidence") or [] if isinstance(e, dict) and type(e.get("chapter_number")) is int and e["chapter_number"] > 0})
        if not cited or (verified is not None and not set(cited).issubset(verified)):
            reasons.append("unsupported_evidence")
        if dimension == "unknown":
            reasons.append("unknown_dimension")
        if reasons:
            rejected.append({"dimension": dimension, "rejected_because": sorted(set(reasons))})
            continue
        mechanism = _bounded(abstraction)
        if mechanism.casefold() in seen:
            continue
        seen.add(mechanism.casefold())
        application = "Explore this effect through the new cast's motives and consequences in natural English, not translated phrasing or a borrowed scene sequence."
        if p.get("conditions"):
            application += " When useful: " + "; ".join(p["conditions"])
        if p.get("variations"):
            application += " Alternatives: " + "; ".join(p["variations"])
        risk = _bounded("; ".join(p.get("risks") or []) or "A shared technique does not justify repeating the source's causality or distinctive expression.")
        accepted.append({
            "mechanism_id": stable_id(TRANSFER_VERSION, dimension, mechanism),
            "dimension": dimension, "abstraction": mechanism,
            "why_it_works": _bounded(p.get("why_it_works") or ""), "application": _bounded(application),
            "risks": [risk], "evidence_chapters": _spread(cited, 40),
        })
    return accepted, rejected


def _source_lessons(session: Session, source_project_id: int) -> Tuple[List[InfluenceLesson], List[str], List[Dict[str, Any]]]:
    profile = source_profile(session, source_project_id)
    if profile is None:
        return [], ["No source manuscript is available; no source lessons were transferred."], []
    bible = BibleService(session)
    genomes = sorted(bible.cards_of_type(source_project_id, "Narrative Genome"), key=lambda card: card.id, reverse=True)
    current = manuscript_meta(session, source_project_id).get("manuscript_id")
    genome = next((card for card in genomes if not _c(card).get("stale") and _c(card).get("manuscript_id") in (None, "", current)), None)
    if genome is None:
        return [], ["A current, evidence-backed source study is needed before transferring lessons."], []
    chapters = load_source_chapters(session, source_project_id)
    verified = {ch.chapter_number for ch in chapters if ch.analysis.get("analysis_status") == "done" and any(o.get("verification_status") == "verified" for o in ch.analysis.get("observations") or [] if isinstance(o, dict))}
    accepted, rejected = abstract_mechanisms(_c(genome), profile, verified_chapters=verified)
    lessons = [InfluenceLesson(
        mechanism=record["abstraction"], reader_effect=record["why_it_works"], application=record["application"],
        risk="; ".join(record["risks"]), evidence_chapters=record["evidence_chapters"],
    ) for record in _spread(accepted, 12)]
    warnings = ["Source lessons are evidence-linked interpretations, not proof of originality or permission to reproduce a plot."]
    if len(verified) < len(chapters):
        warnings.append(f"Verified evidence covers {len(verified)} of {len(chapters)} imported chapters; incomplete coverage limits these lessons.")
    if rejected:
        warnings.append(f"{len(rejected)} lesson(s) were withheld because abstraction, English adaptation, evidence or source-leak screening was insufficient.")
    return lessons, warnings, rejected


def source_lessons(session: Session, source_project_id: int) -> Tuple[List[InfluenceLesson], List[str]]:
    """The only source-to-creative lesson boundary; warnings never quote rejected material."""
    lessons, warnings, _ = _source_lessons(session, source_project_id)
    return lessons, warnings


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

    Copies an English target and screened lessons, never source measurements,
    prose, ordered scene sequences, ledgers or entity cards.
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
    lessons, _, rejected = _source_lessons(session, source_project_id)

    project, _ = project_service.create_project(session, ProjectCreate(name=name, description=description or f"Original project derived from source project {source_project_id} (mechanisms only)", template=template))
    fp_content = english_target_fingerprint(_c(fp_card))
    fp_content["source_project_id"] = source_project_id
    fp_content["derived_from_card_id"] = fp_card.id
    fp_type = _type(session, FINGERPRINT_TYPE)
    new_fp = CardService(session).create(CardCreate(title="Narrative Fingerprint", content=fp_content, card_type_id=fp_type.id), project.id, commit=False)
    provenance.record(session, project_id=project.id, artifact_kind=FINGERPRINT_TYPE, artifact_key=str(new_fp.id), content=fp_content, upstream=[provenance.Upstream(FINGERPRINT_TYPE, str(fp_card.id), provenance.card_hash(fp_card))], producer=TRANSFER_VERSION, schema_version=fp_content.get("version", ""), card_id=new_fp.id)

    mech_ids: List[int] = []
    genome = bible.singleton(source_project_id, "Narrative Genome")
    if lessons and genome is not None:
        mech_type = _type(session, MECHANISM_TYPE)
        for index, lesson in enumerate(lessons, 1):
            mech = lesson.model_dump(mode="json")
            card = CardService(session).create(CardCreate(title=f"Source Lesson {index}", content={**mech, "source_project_id": source_project_id}, card_type_id=mech_type.id), project.id, commit=False)
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
        profile = source_profile(session, manifest.source_project_id)
        if profile is not None:
            cards = []
            for type_name in ("Character Card", "Organization Card", "Scene Card", "Item Card", "Concept Card", "Story Foundation", "Reader Contract", "Theme Map", "Chapter Outline", "Volume Outline", "Stage Outline", "Plot Thread", "Promise Payoff", "Knowledge Fact", "World Rule", "Power System", "Chapter Text", "Abstract Mechanism", "Creative Compass"):
                for card in bible.cards_of_type(original_project_id, type_name):
                    cards.append({"card_type": type_name, "title": card.title, "content": _c(card), "card_id": card.id})
            rep = fw.check_bible_cards(cards, profile)
            firewall = rep.as_dict()
            for f in rep.critical:
                problems.append({"kind": "source_leak", "check": f.check, "detail": f.detail, "matched": f.matched})
    return {"project_id": original_project_id, "source_project_id": manifest.source_project_id, "isolated": not problems, "problems": problems, "firewall": firewall}


__all__ = ["FINGERPRINT_TYPE", "MECHANISM_TYPE", "TRANSFER_VERSION", "OriginalProjectResult", "abstract_mechanisms", "create_original_project", "isolation_report", "source_lessons", "source_profile"]
