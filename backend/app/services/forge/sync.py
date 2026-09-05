"""Automatic post-chapter synchronization.

Producer: ``pipeline`` after a validated draft is committed as Chapter Text.
Consumers: ``canon`` (facts), ledger cards (Plot Thread / Promise Payoff /
Relationship Arc / Knowledge Fact / Timeline Event), Chapter State Packet card,
Project Manifest revisions.

Policy (Phase 11):
- Each proposed change carries evidence spans and a support level.
- Only ``explicit`` and ``strongly_entailed`` changes that do not contradict
  locked canon are committed; a change authorized by the outline's allowed
  outcomes is committed even if only strongly entailed.
- ``weakly_inferred`` changes are stored as noncanonical observations on the
  packet; ``unsupported`` changes are rejected and listed.
- Identity attributes are never rewritten.
- All updates run in one transaction; any critical failure rolls everything back
  and the canon revision does not advance.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType, ProjectManifest
from app.schemas.card import CardCreate
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge import canon as canon_store
from app.services.forge import provenance
from app.services.forge.claims import ChapterClaims, Claim
from app.services.forge.textmetrics import split_paragraphs, split_sentences

SYNC_VERSION = "sync-1"
STATE_PACKET_TYPE = "Chapter State Packet"


class SyncError(RuntimeError):
    pass


@dataclass
class ProposedUpdate:
    subject: str
    subject_kind: str
    attribute: str
    value: Any
    support: str
    evidence: List[Dict[str, Any]]
    outline_authorized: bool = False
    decision: str = "pending"  # committed | observation | rejected
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return dict(self.__dict__)


def _norm(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def proposals_from_claims(claims: Sequence[Claim], *, participants: Iterable[str], allowed_outcomes: Iterable[str]) -> List[ProposedUpdate]:
    parts = {_norm(p): p for p in participants}
    # First-name resolution, only when unambiguous among participants.
    firsts: Dict[str, List[str]] = {}
    for p in participants:
        firsts.setdefault(_norm(p).split(" ")[0], []).append(p)
    for first, names in firsts.items():
        if len(names) == 1 and first not in parts:
            parts[first] = names[0]
    outcomes = " ".join(_norm(o) for o in allowed_outcomes)
    out: List[ProposedUpdate] = []
    for c in claims:
        ev = [{"span": list(c.span), "text": c.evidence[:200]}]
        authorized = bool(str(c.value)) and _norm(c.value) in outcomes
        if c.kind == "relationship_changed":
            # Subject is "A ↔ B" (or "A and B"); both sides must be participants.
            sides = [parts.get(_norm(x)) for x in re.split(r"\s*(?:↔|<->|&|\band\b)\s*", str(c.subject)) if x.strip()]
            if len(sides) == 2 and all(sides):
                out.append(ProposedUpdate(f"{sides[0]} ↔ {sides[1]}", "relationship", "private_relationship", str(c.value), c.support, ev, authorized))
            continue
        subj = parts.get(_norm(c.subject))
        if subj is None:
            continue  # entity validation already handled unknown subjects
        if c.kind == "possession_gained":
            out.append(ProposedUpdate(subj, "character", "possesses", {"add": [str(c.value)]}, c.support, ev, authorized))
        elif c.kind == "possession_lost":
            out.append(ProposedUpdate(subj, "character", "possesses", {"remove": [str(c.value)]}, c.support, ev, authorized))
        elif c.kind == "location_changed":
            out.append(ProposedUpdate(subj, "character", "location", str(c.value), c.support, ev, authorized))
        elif c.kind == "injury":
            out.append(ProposedUpdate(subj, "character", "injuries", {"add": [str(c.value)]}, c.support, ev, authorized))
        elif c.kind == "knowledge_gained":
            out.append(ProposedUpdate(subj, "character", "knows", {"add": [str(c.value)]}, c.support, ev, authorized))
        elif c.kind == "death":
            out.append(ProposedUpdate(subj, "character", "status", "dead", c.support, ev, "die" in outcomes or "death" in outcomes or "dead" in outcomes))
        elif c.kind == "membership_changed":
            out.append(ProposedUpdate(subj, "character", "memberships", {"add": [str(c.value)]}, c.support, ev, authorized))
        elif c.kind == "promise_made":
            out.append(ProposedUpdate(subj, "character", "open_questions", {"add": [f"promised: {c.value}"]}, c.support, ev, authorized))
    return out


def decide(proposals: List[ProposedUpdate], locked: Dict[Tuple[str, str], canon_store.FactView]) -> List[ProposedUpdate]:
    for p in proposals:
        key = (_norm(p.subject), _norm(p.attribute))
        existing = locked.get(key)
        if p.support == "unsupported":
            p.decision, p.reason = "rejected", "no evidence in the chapter text"
        elif p.support == "weakly_inferred":
            p.decision, p.reason = "observation", "weak inference is stored as a noncanonical observation"
        elif p.support == "explicit" or p.outline_authorized:
            if canon_store.contradicts(existing, p.attribute, p.value) and not p.outline_authorized and p.attribute not in ("location",):
                p.decision, p.reason = "rejected", f"contradicts locked canon ({existing.value!r}) without outline authorization"
            else:
                p.decision, p.reason = "committed", "explicit evidence" if p.support == "explicit" else "authorized by outline"
        elif p.support == "strongly_entailed":
            if canon_store.contradicts(existing, p.attribute, p.value):
                p.decision, p.reason = "rejected", f"strongly entailed change contradicts locked canon ({existing.value!r})"
            else:
                p.decision, p.reason = "committed", "strongly entailed, no conflict"
        else:
            p.decision, p.reason = "rejected", f"unknown support level {p.support}"
    return proposals


def _summary(prose: str, max_chars: int = 900) -> str:
    """Deterministic extractive summary: first sentence of each paragraph, capped."""
    parts: List[str] = []
    total = 0
    for p in split_paragraphs(prose):
        sents = split_sentences(p)
        if not sents:
            continue
        s = sents[0]
        if total + len(s) > max_chars:
            break
        parts.append(s)
        total += len(s) + 1
    return " ".join(parts)


def _ensure_type(session: Session, name: str) -> CardType:
    ct = session.exec(select(CardType).where(CardType.name == name)).first()
    if ct is None:
        raise SyncError(f"Card type '{name}' is not bootstrapped")
    return ct


def build_state_packet(
    *,
    project_id: int,
    chapter_number: int,
    pov: str,
    participants: List[str],
    prose: str,
    model_claims: Optional[ChapterClaims],
    committed: List[ProposedUpdate],
    observations: List[ProposedUpdate],
    canon_revision: int,
    state_after: Dict[Tuple[str, str], canon_store.FactView],
    next_outline: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    ending_location = (model_claims.ending_location if model_claims else "") or next((str(p.value) for p in committed if p.attribute == "location"), "") or next((str(fv.value) for (s, a), fv in state_after.items() if a == "location" and s == pov.lower()), "")
    physical = {n: {a: fv.value for (s, a), fv in state_after.items() if s == n.lower() and a in ("injuries", "conditions", "status")} for n in participants}
    emotional = {}
    for p in observations:
        if p.attribute in ("emotional_state",):
            emotional[p.subject] = p.value
    return {
        "chapter_number": chapter_number,
        "canon_revision": canon_revision,
        "summary": (model_claims.summary if model_claims and model_claims.summary else "") or _summary(prose),
        "scene_state": {
            "ending_location": ending_location,
            "current_time": model_claims.current_time if model_claims else "",
            "active_pov": pov,
            "participants": participants,
            "physical_states": physical,
            "emotional_states": emotional,
            "possessions": {n: fv.value for (s, a), fv in state_after.items() for n in participants if s == n.lower() and a == "possesses"},
            "knowledge_changes": [p.as_dict() for p in committed if p.attribute == "knows"],
            "relationship_changes": [p.as_dict() for p in committed if p.subject_kind == "relationship"],
            "unresolved_immediate_action": model_claims.unresolved_immediate_action if model_claims else "",
            "open_dialogue_obligation": model_claims.open_dialogue_obligation if model_claims else "",
        },
        "setup_payoff_state": [p.as_dict() for p in committed if p.attribute == "open_questions"],
        "next_chapter_constraints": [f"Chapter {chapter_number + 1} starts from: {ending_location or 'the location where chapter %d ended' % chapter_number}"] + ([f"Next outline title: {next_outline.get('title')}"] if next_outline else []),
        "noncanonical_observations": [p.as_dict() for p in observations],
        "sync_version": SYNC_VERSION,
    }


def synchronize_chapter(
    session: Session,
    *,
    project_id: int,
    chapter_number: int,
    chapter_card_id: int,
    pov: str,
    participants: List[str],
    prose: str,
    claims: Sequence[Claim],
    model_claims: Optional[ChapterClaims],
    allowed_outcomes: Iterable[str],
    outline_card_id: Optional[int] = None,
    fail_on: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    """Apply all persistent changes of chapter N in one transaction.

    Raises ``SyncError`` (after rollback) when a critical update fails; the
    canon revision is unchanged in that case.
    """
    bible = BibleService(session)
    manifest = provenance.get_manifest(session, project_id, create=True)
    before_rev = int(manifest.canon_revision)
    new_rev = before_rev + 1
    fail_set = set(fail_on or ())
    try:
        locked = canon_store.state_as_of(session, project_id, chapter_number - 1, canon_revision=before_rev)
        proposals = decide(proposals_from_claims(claims, participants=participants, allowed_outcomes=allowed_outcomes), locked)
        committed = [p for p in proposals if p.decision == "committed"]
        observations = [p for p in proposals if p.decision == "observation"]
        rejected = [p for p in proposals if p.decision == "rejected"]

        # Regeneration: facts from this chapter onward are replaced.
        canon_store.delete_facts_from_chapter(session, project_id, chapter_number)
        for p in committed:
            if p.attribute in fail_set:
                raise SyncError(f"simulated failure applying {p.attribute}")
            canon_store.add_fact(session, project_id=project_id, subject=p.subject, attribute=p.attribute, value=p.value, valid_from_chapter=chapter_number, canon_revision=new_rev, support=p.support if p.support in canon_store.COMMITTABLE_SUPPORT else "strongly_entailed", subject_kind=p.subject_kind, evidence=p.evidence, source="sync", chapter_card_id=chapter_card_id)

        # Ledger cards: deterministic, idempotent field updates with history entries.
        now = datetime.now().isoformat(timespec="seconds")
        touched: List[int] = []
        for card in bible.cards_of_type(project_id, "Plot Thread"):
            c = card.content if isinstance(card.content, dict) else {}
            parts = {_norm(x) for x in (c.get("participants") or [])}
            if c.get("status") in ("active", "planned") and parts & {_norm(p) for p in participants}:
                if int(c.get("last_advanced_chapter") or 0) < chapter_number:
                    c["last_advanced_chapter"] = chapter_number
                    c.setdefault("history", []).append({"field": "last_advanced_chapter", "new": chapter_number, "chapter_number": chapter_number, "changed_at": now, "accepted_by": "ai", "reason": "participants appeared in the chapter"})
                    card.content = c
                    flag_modified(card, "content")
                    session.add(card)
                    touched.append(card.id)
        for card in bible.cards_of_type(project_id, "Promise Payoff"):
            c = card.content if isinstance(card.content, dict) else {}
            payoff = _norm(c.get("planned_payoff"))
            if c.get("status") in ("planted", "active", "open", "planned") and payoff and payoff in " ".join(_norm(o) for o in allowed_outcomes):
                c["status"] = "paid_off"
                c["payoff_chapter"] = chapter_number
                c.setdefault("history", []).append({"field": "status", "previous": "planted", "new": "paid_off", "chapter_number": chapter_number, "changed_at": now, "accepted_by": "ai", "reason": "planned payoff listed among this chapter's allowed outcomes"})
                card.content = c
                flag_modified(card, "content")
                session.add(card)
                touched.append(card.id)
        for p in committed:
            if p.subject_kind != "relationship":
                continue
            for card in bible.cards_of_type(project_id, "Relationship Arc"):
                c = card.content if isinstance(card.content, dict) else {}
                if _norm(f"{c.get('character_a')} ↔ {c.get('character_b')}") == _norm(p.subject) or _norm(card.title) == _norm(p.subject):
                    prev = c.get(p.attribute)
                    c[p.attribute] = p.value
                    c.setdefault("history", []).append({"field": p.attribute, "previous": prev, "new": p.value, "chapter_number": chapter_number, "changed_at": now, "accepted_by": "ai", "reason": p.reason})
                    card.content = c
                    flag_modified(card, "content")
                    session.add(card)
                    touched.append(card.id)
        for p in committed:
            if p.attribute != "knows":
                continue
            for card in bible.cards_of_type(project_id, "Knowledge Fact"):
                c = card.content if isinstance(card.content, dict) else {}
                fact = _norm(c.get("fact"))
                for learned in (p.value.get("add") if isinstance(p.value, dict) else [p.value]):
                    if fact and (fact in _norm(learned) or _norm(learned) in fact):
                        knowers = c.setdefault("knowers", [])
                        entry = next((k for k in knowers if isinstance(k, dict) and _norm(k.get("entity")) == _norm(p.subject)), None)
                        if entry is None:
                            entry = {"entity": p.subject}
                            knowers.append(entry)
                        if entry.get("state") != "knows":
                            entry.update({"state": "knows", "learned_chapter": chapter_number, "how_learned": "chapter text"})
                            card.content = c
                            flag_modified(card, "content")
                            session.add(card)
                            touched.append(card.id)
        # Timeline event for the chapter (one per chapter, idempotent by title).
        tl_type = _ensure_type(session, "Timeline Event")
        title = f"Chapter {chapter_number} events"
        existing_tl = bible.find_card(project_id, "Timeline Event", title)
        tl_content = {"title": title, "chapter_number": chapter_number, "order_index": chapter_number * 10, "participants": participants, "action": _summary(prose, 300), "location": next((str(p.value) for p in committed if p.attribute == "location"), ""), "truth_status": "canon", "confidence": 1.0, "evidence": [{"chapter_number": chapter_number, "note": "synchronized from committed chapter"}]}
        if existing_tl is None:
            tl_card = CardService(session).create(CardCreate(title=title, content=tl_content, card_type_id=tl_type.id), project_id, commit=False)
            touched.append(tl_card.id)
        else:
            existing_tl.content = {**(existing_tl.content or {}), **tl_content}
            flag_modified(existing_tl, "content")
            session.add(existing_tl)
            touched.append(existing_tl.id)

        # Next Chapter State Packet card (idempotent by chapter number).
        state_after = canon_store.state_as_of(session, project_id, chapter_number, canon_revision=new_rev)
        next_outline = next((card.content for card in bible.cards_of_type(project_id, "Chapter Outline") if int((card.content or {}).get("chapter_number") or 0) == chapter_number + 1), None)
        packet = build_state_packet(project_id=project_id, chapter_number=chapter_number, pov=pov, participants=participants, prose=prose, model_claims=model_claims, committed=committed, observations=observations, canon_revision=new_rev, state_after=state_after, next_outline=next_outline if isinstance(next_outline, dict) else None)
        packet_type = _ensure_type(session, STATE_PACKET_TYPE)
        packet_title = f"State after chapter {chapter_number:04d}"
        existing_packet = bible.find_card(project_id, STATE_PACKET_TYPE, packet_title)
        if existing_packet is None:
            packet_card = CardService(session).create(CardCreate(title=packet_title, content=packet, card_type_id=packet_type.id), project_id, commit=False)
        else:
            existing_packet.content = packet
            flag_modified(existing_packet, "content")
            session.add(existing_packet)
            packet_card = existing_packet
        # Drop packets of later chapters (they describe a future that no longer exists after regeneration).
        for card in bible.cards_of_type(project_id, STATE_PACKET_TYPE):
            if int((card.content or {}).get("chapter_number") or 0) > chapter_number:
                session.delete(card)
        # Chapter summary on the Chapter Text card.
        text_card = session.get(Card, chapter_card_id)
        if text_card is None:
            raise SyncError(f"Chapter Text card {chapter_card_id} missing")
        tc = text_card.content if isinstance(text_card.content, dict) else {}
        tc.update({"summary": packet["summary"], "canon_revision": new_rev, "sync_status": "synchronized", "synced_at": now, "pov": pov, "participants": participants})
        text_card.content = tc
        flag_modified(text_card, "content")
        session.add(text_card)

        provenance.record(session, project_id=project_id, artifact_kind="chapter_state_packet", artifact_key=str(chapter_number), content=packet, upstream=[provenance.Upstream("Chapter Text", str(chapter_card_id), provenance.card_hash(text_card))], producer=SYNC_VERSION, schema_version=SYNC_VERSION, card_id=packet_card.id)

        manifest.canon_revision = new_rev
        manifest.latest_committed_chapter = chapter_number
        manifest.next_allowed_chapter = chapter_number + 1
        manifest.last_sync_status = "ok"
        manifest.last_sync_chapter = chapter_number
        manifest.updated_at = datetime.now()
        session.add(manifest)
        session.commit()
    except Exception as exc:
        session.rollback()
        m = provenance.get_manifest(session, project_id, create=True)
        m.last_sync_status = f"failed: {str(exc)[:200]}"
        m.updated_at = datetime.now()
        session.add(m)
        session.commit()
        raise SyncError(str(exc)) from exc
    return {
        "sync_version": SYNC_VERSION,
        "canon_revision_before": before_rev,
        "canon_revision_after": new_rev,
        "committed": [p.as_dict() for p in committed],
        "observations": [p.as_dict() for p in observations],
        "rejected": [p.as_dict() for p in rejected],
        "touched_card_ids": sorted(set(touched)),
        "state_packet_card_id": packet_card.id,
        "state_packet": packet,
    }


__all__ = ["STATE_PACKET_TYPE", "SYNC_VERSION", "ProposedUpdate", "SyncError", "build_state_packet", "decide", "proposals_from_claims", "synchronize_chapter"]
