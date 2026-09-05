"""Temporal canon store.

Producer: ``sync.synchronize_chapter`` (append-only writes inside one transaction)
and ``seed_from_bible`` (chapter-0 facts from approved Bible cards).
Consumers: ``compiler`` (state as-of chapter N-1), ``validators`` (locked canon),
``sync`` (contradiction detection), tests.

Every fact is (subject, attribute) -> value valid from ``valid_from_chapter``.
Reading "as of chapter N" returns, for each (subject, attribute), the latest row
whose ``valid_from_chapter <= N``. Rows created by later chapters are simply
invisible, which is what makes regeneration of an earlier chapter safe.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple

from sqlmodel import Session, select

from app.db.models import CanonFact
from app.services.forge.textmetrics import stable_id

SUPPORT_LEVELS = ("explicit", "strongly_entailed", "weakly_inferred", "unsupported")
COMMITTABLE_SUPPORT = ("explicit", "strongly_entailed")

# Attributes that describe stable identity and must never be rewritten from prose.
IDENTITY_ATTRIBUTES = {"name", "aliases", "role_type", "birth", "species", "gender", "age_at_start", "origin"}
# Attributes whose value is a set/list and accumulates instead of replacing.
LIST_ATTRIBUTES = {"knows", "possesses", "injuries", "conditions", "memberships", "abilities", "limitations", "open_questions"}


@dataclass
class FactView:
    subject: str
    subject_kind: str
    attribute: str
    value: Any
    valid_from_chapter: int
    support: str
    fact_id: str
    canon_revision: int
    evidence: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _key(subject: str, attribute: str) -> Tuple[str, str]:
    return subject.strip().lower(), attribute.strip().lower()


def state_as_of(session: Session, project_id: int, chapter: int, *, subjects: Optional[Iterable[str]] = None, canon_revision: Optional[int] = None) -> Dict[Tuple[str, str], FactView]:
    """Latest fact per (subject, attribute) valid through ``chapter`` (inclusive)."""
    stmt = select(CanonFact).where(CanonFact.project_id == project_id, CanonFact.valid_from_chapter <= int(chapter))
    if canon_revision is not None:
        stmt = stmt.where(CanonFact.canon_revision <= int(canon_revision))
    rows = session.exec(stmt.order_by(CanonFact.valid_from_chapter, CanonFact.id)).all()
    wanted = {s.strip().lower() for s in subjects} if subjects else None
    out: Dict[Tuple[str, str], FactView] = {}
    for r in rows:
        k = _key(r.subject, r.attribute)
        if wanted is not None and k[0] not in wanted:
            continue
        prev = out.get(k)
        if r.attribute.lower() in LIST_ATTRIBUTES:
            merged = list(prev.value or []) if prev is not None and isinstance(prev.value, list) else ([prev.value] if prev is not None and prev.value is not None else [])
            incoming = r.value if isinstance(r.value, list) else [r.value]
            if isinstance(r.value, dict) and ("add" in r.value or "remove" in r.value):
                merged = [v for v in merged if v not in (r.value.get("remove") or [])]
                incoming = list(r.value.get("add") or [])
            for v in incoming:
                if v not in merged:
                    merged.append(v)
            value: Any = merged
        else:
            value = r.value
        out[k] = FactView(subject=r.subject, subject_kind=r.subject_kind, attribute=r.attribute, value=value, valid_from_chapter=r.valid_from_chapter, support=r.support, fact_id=r.fact_id, canon_revision=r.canon_revision, evidence=list(r.evidence or []))
    return out


def subject_state(session: Session, project_id: int, chapter: int, subject: str) -> Dict[str, Any]:
    facts = state_as_of(session, project_id, chapter, subjects=[subject])
    return {attr: fv.value for (_, attr), fv in facts.items()}


def subjects_as_of(session: Session, project_id: int, chapter: int, kind: Optional[str] = None) -> List[str]:
    facts = state_as_of(session, project_id, chapter)
    names: Dict[str, str] = {}
    for (subj, _), fv in facts.items():
        if kind and fv.subject_kind != kind:
            continue
        names.setdefault(subj, fv.subject)
    return sorted(names.values())


def add_fact(
    session: Session,
    *,
    project_id: int,
    subject: str,
    attribute: str,
    value: Any,
    valid_from_chapter: int,
    canon_revision: int,
    support: str = "explicit",
    subject_kind: str = "character",
    evidence: Optional[List[Dict[str, Any]]] = None,
    source: str = "sync",
    chapter_card_id: Optional[int] = None,
) -> CanonFact:
    """Append one fact (no commit). Rejects non-committable support levels."""
    if support not in COMMITTABLE_SUPPORT:
        raise ValueError(f"support level '{support}' cannot enter canon")
    if attribute.strip().lower() in IDENTITY_ATTRIBUTES and source == "sync":
        raise ValueError(f"identity attribute '{attribute}' cannot be rewritten by chapter synchronization")
    fact = CanonFact(
        project_id=project_id, fact_id=stable_id(project_id, subject.lower(), attribute.lower(), valid_from_chapter, canon_revision, str(value)),
        subject=subject.strip(), subject_kind=subject_kind, attribute=attribute.strip(), value=value, valid_from_chapter=int(valid_from_chapter),
        canon_revision=int(canon_revision), support=support, evidence=list(evidence or []), source=source, chapter_card_id=chapter_card_id,
    )
    # Mark the previously current row for this key as superseded (bookkeeping only; reads are temporal).
    prev = session.exec(select(CanonFact).where(CanonFact.project_id == project_id, CanonFact.subject == fact.subject, CanonFact.attribute == fact.attribute, CanonFact.superseded_by_id.is_(None)).order_by(CanonFact.valid_from_chapter.desc(), CanonFact.id.desc())).first()
    session.add(fact)
    session.flush()
    if prev is not None and prev.valid_from_chapter <= fact.valid_from_chapter and prev.id != fact.id:
        prev.superseded_by_id = fact.id
        session.add(prev)
    return fact


def delete_facts_from_chapter(session: Session, project_id: int, chapter: int) -> int:
    """Remove facts introduced by chapter >= ``chapter`` (used when a chapter is regenerated). No commit."""
    rows = session.exec(select(CanonFact).where(CanonFact.project_id == project_id, CanonFact.valid_from_chapter >= int(chapter), CanonFact.source == "sync")).all()
    ids = {r.id for r in rows}
    for r in rows:
        session.delete(r)
    session.flush()
    # Un-supersede rows that pointed at deleted facts.
    for r in session.exec(select(CanonFact).where(CanonFact.project_id == project_id, CanonFact.superseded_by_id.in_(ids))).all() if ids else []:
        r.superseded_by_id = None
        session.add(r)
    return len(rows)


def contradicts(existing: Optional[FactView], attribute: str, new_value: Any) -> bool:
    """A new value contradicts locked canon when the attribute is scalar and differs."""
    if existing is None:
        return False
    if attribute.strip().lower() in LIST_ATTRIBUTES:
        return False
    return existing.value != new_value


def seed_from_bible(session: Session, project_id: int, cards: Iterable[Any], *, canon_revision: int) -> int:
    """Chapter-0 facts from approved original Bible cards (Character, Relationship, Knowledge, Item, Scene). No commit."""
    n = 0
    for card in cards:
        content = card.content if isinstance(card.content, dict) else {}
        kind = getattr(card.card_type, "name", "")
        title = str(content.get("name") or card.title)
        if kind == "Character Card":
            facts = {
                "location": content.get("born_scene") or content.get("current_location") or "",
                "goal": (content.get("dramatic_design") or {}).get("external_goal") or content.get("core_drive") or "",
                "status": content.get("status") or "alive",
            }
            di = content.get("dynamic_info") or {}
            if isinstance(di, dict):
                for key, attr in (("Possessions", "possesses"), ("Injuries", "injuries"), ("Abilities", "abilities")):
                    vals = di.get(key)
                    if isinstance(vals, list):
                        facts[attr] = [str(v.get("info") if isinstance(v, dict) else v) for v in vals]
            for attr, val in facts.items():
                if val in ("", None, []):
                    continue
                add_fact(session, project_id=project_id, subject=title, attribute=attr, value=val, valid_from_chapter=0, canon_revision=canon_revision, support="explicit", subject_kind="character", source="bible", evidence=[{"card_id": card.id}])
                n += 1
        elif kind == "Relationship Arc":
            a, b = str(content.get("character_a") or ""), str(content.get("character_b") or "")
            if a and b:
                subj = f"{a} ↔ {b}"
                for attr in ("trust", "affection", "fear", "dependency", "resentment"):
                    if attr in content:
                        add_fact(session, project_id=project_id, subject=subj, attribute=attr, value=content.get(attr), valid_from_chapter=0, canon_revision=canon_revision, subject_kind="relationship", source="bible", evidence=[{"card_id": card.id}])
                        n += 1
                if content.get("private_relationship"):
                    add_fact(session, project_id=project_id, subject=subj, attribute="private_relationship", value=content.get("private_relationship"), valid_from_chapter=0, canon_revision=canon_revision, subject_kind="relationship", source="bible", evidence=[{"card_id": card.id}])
                    n += 1
        elif kind == "Knowledge Fact":
            fact_text = str(content.get("fact") or card.title)
            for k in content.get("knowers") or []:
                if isinstance(k, dict) and k.get("entity") and k.get("state") in ("knows", "suspects", "false_belief"):
                    learned = k.get("learned_chapter")
                    add_fact(session, project_id=project_id, subject=str(k["entity"]), attribute="knows", value=[f"[{k.get('state')}] {fact_text}"], valid_from_chapter=int(learned) if isinstance(learned, int) else 0, canon_revision=canon_revision, subject_kind="character", source="bible", evidence=[{"card_id": card.id}])
                    n += 1
        elif kind == "Item Card":
            owner = content.get("owner_hint") or content.get("owner")
            if owner:
                add_fact(session, project_id=project_id, subject=str(owner), attribute="possesses", value=[title], valid_from_chapter=0, canon_revision=canon_revision, subject_kind="character", source="bible", evidence=[{"card_id": card.id}])
                n += 1
    return n


__all__ = [
    "COMMITTABLE_SUPPORT", "IDENTITY_ATTRIBUTES", "LIST_ATTRIBUTES", "SUPPORT_LEVELS", "FactView", "add_fact", "contradicts",
    "delete_facts_from_chapter", "seed_from_bible", "state_as_of", "subject_state", "subjects_as_of",
]
