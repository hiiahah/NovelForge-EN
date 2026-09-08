"""Card-quality audits (Phase 15). Deterministic, no model calls.

Consumer: ``/api/forge/audit`` and the frontend status panel.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Set

from sqlmodel import Session

from app.db.models import Card
from app.services.bible.bible_service import BibleService
from app.services.forge import provenance
from app.services.forge.transfer import isolation_report

# Card types and the component that consumes each (documented contract; checked by tests).
CARD_CONSUMERS: Dict[str, List[str]] = {
    "Story Foundation": ["compiler.story_foundation", "provenance.DOWNSTREAM"],
    "Reader Contract": ["compiler.reader_contract"],
    "Theme Map": ["compiler.theme"],
    "Narrative Architecture": ["architecture fan-out workflow"],
    "Core Blueprint": ["outline workflows"],
    "Volume Outline": ["compiler.volume_outline"],
    "Stage Outline": ["compiler.stage_outline"],
    "Chapter Outline": ["compiler.chapter_outline", "examples.functions_from_outline"],
    "Chapter Text": ["compiler.previous_tail", "sync"],
    "Character Card": ["compiler.participants", "validators.validate_characters", "canon.seed_from_bible"],
    "Relationship Arc": ["compiler.relationships", "sync", "canon.seed_from_bible"],
    "Knowledge Fact": ["compiler.pov_knowledge_boundary", "sync"],
    "Plot Thread": ["compiler.threads", "sync"],
    "Promise Payoff": ["compiler.promises", "sync"],
    "Timeline Event": ["compiler.timeline", "sync"],
    "World Rule": ["compiler.rules"],
    "Power System": ["compiler.rules"],
    "Organization Card": ["compiler.organizations"],
    "Scene Card": ["compiler.locations"],
    "Item Card": ["compiler.items", "canon.seed_from_bible"],
    "Narrative Fingerprint": ["compiler.fingerprint", "validators.style_report", "transfer.create_original_project"],
    "Abstract Mechanism": ["compiler.mechanisms"],
    "Chapter State Packet": ["compiler.previous_summary", "compiler.scene_state"],
    "Chapter Analysis": ["evidence.verify_chapter_analysis", "fingerprint", "examples"],
    "Narrative Genome": ["transfer.abstract_mechanisms"],
    "Style Profile": ["legacy ContextCompiler (superseded by Narrative Fingerprint)"],
    "Story Structure Map": ["Lab UI"],
    "Emotional Rhythm": ["fingerprint.pacing_reward (via analyses)"],
    "Originality Transformation": ["transfer (planning input)"],
}


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def audit_project(session: Session, project_id: int) -> Dict[str, Any]:
    bible = BibleService(session)
    findings: List[Dict[str, Any]] = []
    names: Dict[str, List[int]] = {}
    aliases: Dict[str, List[int]] = {}
    entity_types = ("Character Card", "Organization Card", "Scene Card", "Item Card", "Concept Card")
    known: Set[str] = set()
    for t in entity_types:
        for card in bible.cards_of_type(project_id, t):
            c = _c(card)
            n = _norm(c.get("name") or card.title)
            known.add(n)
            names.setdefault(n, []).append(card.id)
            for a in c.get("aliases") or []:
                aliases.setdefault(_norm(a), []).append(card.id)
                known.add(_norm(a))
    for n, ids in names.items():
        if len(ids) > 1:
            findings.append({"kind": "duplicate_entity", "severity": "high", "name": n, "card_ids": ids})
    for a, ids in aliases.items():
        if a in names and set(ids) - set(names[a]):
            findings.append({"kind": "alias_collision", "severity": "high", "alias": a, "card_ids": ids + names[a]})
    # Unresolved references in ledgers.
    for t, keys in (("Relationship Arc", ("character_a", "character_b")), ("Plot Thread", ("participants",)), ("Promise Payoff", ("participants",)), ("Timeline Event", ("participants",))):
        for card in bible.cards_of_type(project_id, t):
            c = _c(card)
            for k in keys:
                vals = c.get(k)
                vals = vals if isinstance(vals, list) else [vals]
                for val in vals:
                    if val and _norm(val) not in known:
                        findings.append({"kind": "unresolved_reference", "severity": "medium", "card_id": card.id, "card_type": t, "field": k, "value": str(val)})
    # Knowledge Fact knowers.
    for card in bible.cards_of_type(project_id, "Knowledge Fact"):
        for k in _c(card).get("knowers") or []:
            if isinstance(k, dict) and k.get("entity") and _norm(k["entity"]) not in known and _norm(k["entity"]) != "reader":
                findings.append({"kind": "unresolved_reference", "severity": "medium", "card_id": card.id, "card_type": "Knowledge Fact", "field": "knowers", "value": str(k["entity"])})
    # Chapter ranges and timeline ordering.
    outlines = sorted((int(_c(c).get("chapter_number") or 0), c.id) for c in bible.cards_of_type(project_id, "Chapter Outline"))
    nums = [n for n, _ in outlines]
    if nums and nums != list(range(min(nums), max(nums) + 1)):
        findings.append({"kind": "invalid_chapter_range", "severity": "high", "detail": f"Chapter Outline numbers are not contiguous: {nums[:40]}"})
    dup = {n for n in nums if nums.count(n) > 1}
    if dup:
        findings.append({"kind": "invalid_chapter_range", "severity": "high", "detail": f"Duplicate chapter numbers: {sorted(dup)}"})
    events = [(int(_c(c).get("chapter_number") or 0), int(_c(c).get("order_index") or 0), c.id) for c in bible.cards_of_type(project_id, "Timeline Event") if isinstance(_c(c).get("chapter_number"), int)]
    events.sort(key=lambda e: e[1])
    for (c1, _, id1), (c2, _, id2) in zip(events, events[1:]):
        if c2 < c1:
            findings.append({"kind": "timeline_inversion", "severity": "medium", "card_ids": [id1, id2], "detail": f"order_index increases while chapter goes {c1} -> {c2}"})
    for card in bible.cards_of_type(project_id, "Promise Payoff"):
        rng = _c(card).get("target_payoff_range")
        if isinstance(rng, list) and len(rng) == 2 and all(isinstance(x, int) for x in rng) and rng[0] > rng[1]:
            findings.append({"kind": "invalid_chapter_range", "severity": "medium", "card_id": card.id, "detail": f"payoff range {rng} is inverted"})
    # Missing evidence on canon entries.
    for t in ("Plot Thread", "Promise Payoff", "Knowledge Fact", "Timeline Event", "Relationship Arc", "World Rule"):
        for card in bible.cards_of_type(project_id, t):
            c = _c(card)
            if c.get("truth_status") == "canon" and not c.get("evidence"):
                findings.append({"kind": "missing_evidence", "severity": "low", "card_id": card.id, "card_type": t})
    # Orphans / never consumed / stale.
    all_types = {getattr(c.card_type, "name", "") for c in session.exec(__import__("sqlmodel").select(Card).where(Card.project_id == project_id)).all()}
    for t in sorted(all_types):
        if t and t not in CARD_CONSUMERS and t not in ("Folder", "General Text", "Work Tags", "Special Ability", "One Sentence Summary", "Story Outline", "Worldview Setting", "Writing Guide", "Content Review Card", "Concept Card"):
            findings.append({"kind": "card_type_without_consumer", "severity": "medium", "card_type": t})
    for s in provenance.stale_artifacts(session, project_id):
        findings.append({"kind": "stale_artifact", "severity": "high" if s["artifact_kind"] in provenance.MANDATORY_FOR_GENERATION else "medium", **s})
    stale_packets = [c.id for c in bible.cards_of_type(project_id, "Chapter State Packet") if int(_c(c).get("chapter_number") or 0) > provenance.get_manifest(session, project_id, create=True).latest_committed_chapter]
    if stale_packets:
        findings.append({"kind": "outdated_context_snapshot", "severity": "high", "card_ids": stale_packets})
    isolation = isolation_report(session, project_id)
    for p in isolation["problems"]:
        findings.append({"kind": "source_original_contamination", "severity": "critical", **p})
    return {"project_id": project_id, "findings": findings, "counts": _counts(findings), "isolation": isolation, "manifest": provenance.manifest_dict(session, project_id), "card_consumers": CARD_CONSUMERS}


def _counts(findings: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for f in findings:
        out[f["kind"]] = out.get(f["kind"], 0) + 1
    return out


__all__ = ["CARD_CONSUMERS", "audit_project"]
