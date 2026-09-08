"""Source-analysis orchestration for a source project.

- ``verify_all_analyses``: re-verifies every stored ChapterAnalysis against the
  exact chapter text, writes observations back, and reports per-chapter
  failures without claiming completeness.
- ``build_fingerprint_card``: builds the Narrative Fingerprint card from
  measured chapters + verified observations, with provenance.
- ``build_example_library``: segments and tags the manuscript into the
  Reference Example Library (entity names replaced by role tokens).
- ``analysis_status``: completeness, evidence coverage and failed chapters.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType
from app.schemas.card import CardCreate
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge import evidence as ev
from app.services.forge import examples as ex
from app.services.forge import provenance
from app.services.forge.corpus import integrity_report, load_source_chapters, manuscript_meta
from app.services.forge.fingerprint import build_fingerprint, validate_fingerprint

from app.services.lab.lab_helpers import ANALYSIS_PROMPT_VERSION


def _c(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _type(session: Session, name: str) -> CardType:
    ct = session.exec(select(CardType).where(CardType.name == name)).first()
    if ct is None:
        raise ValueError(f"Card type '{name}' is not bootstrapped")
    return ct


def _source_roles(session: Session, project_id: int) -> Dict[str, str]:
    roles: Dict[str, str] = {}
    bible = BibleService(session)
    for card in bible.cards_of_type(project_id, "Character Card"):
        c = _c(card)
        name = str(c.get("name") or card.title)
        role = str(c.get("role_type") or "character").lower().replace(" ", "_")
        roles[name] = role
        for a in c.get("aliases") or []:
            roles[str(a)] = role
    for type_name, role in (("Organization Card", "organization"), ("Scene Card", "place"), ("Item Card", "item")):
        for card in bible.cards_of_type(project_id, type_name):
            roles[str(_c(card).get("name") or card.title)] = role
    # Participants named in analyses but without cards are still redacted.
    for ch in load_source_chapters(session, project_id):
        for p in (ch.analysis or {}).get("participants") or []:
            if isinstance(p, str) and p.strip() and p not in roles:
                roles[p.strip()] = "character"
    return roles


def verify_all_analyses(session: Session, project_id: int, *, extraction_model: str = "", prompt_version: str = ANALYSIS_PROMPT_VERSION) -> Dict[str, Any]:
    chapters = load_source_chapters(session, project_id)
    verified = failed = pending = 0
    per_chapter: List[Dict[str, Any]] = []
    for ch in chapters:
        card = session.get(Card, ch.card_id)
        content = _c(card)
        if not (content.get("scenes") or content.get("evidence") or content.get("summary")):
            if content.get("analysis_status") == "failed":
                failed += 1
                per_chapter.append({"chapter": ch.chapter_number, "status": "failed", "error": content.get("analysis_error")})
            else:
                pending += 1
                per_chapter.append({"chapter": ch.chapter_number, "status": "pending"})
            continue
        result = ev.verify_chapter_analysis(content, ch.text, manuscript_id=ch.manuscript_id, chapter_id=ch.chapter_id, chapter_number=ch.chapter_number, extraction_model=extraction_model or str(content.get("extraction_model") or ""), prompt_version=prompt_version, chapter_excluded=not ch.is_main_story)
        if result.get("analysis_status") == "failed":
            failed += 1
        else:
            result["analysis_status"] = "done"
            verified += 1
        result["source_text"] = ch.text
        result["verified_at"] = datetime.now().isoformat(timespec="seconds")
        card.content = result
        flag_modified(card, "content")
        session.add(card)
        per_chapter.append({"chapter": ch.chapter_number, "status": result.get("analysis_status"), "evidence_verified": result.get("evidence_verified"), "evidence_total": result.get("evidence_total"), "error": result.get("analysis_error")})
    session.commit()
    return {"chapters": len(chapters), "verified": verified, "failed": failed, "pending": pending, "complete": pending == 0 and failed == 0 and len(chapters) > 0, "per_chapter": per_chapter}


def analysis_status(session: Session, project_id: int) -> Dict[str, Any]:
    chapters = load_source_chapters(session, project_id)
    meta = manuscript_meta(session, project_id)
    done = [ch for ch in chapters if (ch.analysis or {}).get("analysis_status") == "done"]
    failed = [ch.chapter_number for ch in chapters if (ch.analysis or {}).get("analysis_status") == "failed"]
    total_ev = sum(int((ch.analysis or {}).get("evidence_total") or 0) for ch in chapters)
    ver_ev = sum(int((ch.analysis or {}).get("evidence_verified") or 0) for ch in chapters)
    bible = BibleService(session)
    fp = bible.singleton(project_id, "Narrative Fingerprint")
    return {
        "project_id": project_id,
        "manuscript_id": meta.get("manuscript_id"),
        "language": meta.get("language") or (chapters[0].language if chapters else None),
        "chapters": len(chapters),
        "analysed": len(done),
        "failed_chapters": failed,
        "completeness": round(len(done) / len(chapters), 3) if chapters else 0.0,
        "evidence_total": total_ev,
        "evidence_verified": ver_ev,
        "evidence_coverage": round(ver_ev / total_ev, 3) if total_ev else 0.0,
        "integrity": integrity_report(chapters),
        "fingerprint": {"card_id": fp.id, "version": _c(fp).get("version"), "dependency_hash": _c(fp).get("dependency_hash"), "stale": bool(_c(fp).get("stale")), "chapters_measured": _c(fp).get("chapters_measured")} if fp else None,
        "example_library": ex.library_status(session, project_id),
    }


def build_fingerprint_card(session: Session, project_id: int) -> Card:
    chapters = load_source_chapters(session, project_id)
    if not chapters:
        raise ValueError("No imported manuscript in this project")
    analyses = {ch.chapter_number: ch.analysis for ch in chapters if (ch.analysis or {}).get("analysis_status") == "done"}
    roles = _source_roles(session, project_id)
    fp = build_fingerprint(chapters, manuscript_id=chapters[0].manuscript_id, analyses=analyses, character_roles=roles)
    errors = validate_fingerprint(fp)
    if errors:
        raise ValueError(f"Fingerprint validation failed: {errors}")
    bible = BibleService(session)
    card = bible.singleton(project_id, "Narrative Fingerprint")
    fp["built_at"] = datetime.now().isoformat(timespec="seconds")
    if card is None:
        card = CardService(session).create(CardCreate(title="Narrative Fingerprint", content=fp, card_type_id=_type(session, "Narrative Fingerprint").id), project_id, commit=False)
    else:
        card.content = fp
        flag_modified(card, "content")
        session.add(card)
    session.flush()
    ups = [provenance.Upstream("manuscript", chapters[0].manuscript_id, chapters[0].manuscript_id)] + [provenance.Upstream("Chapter Analysis", str(ch.card_id), provenance.card_hash(session.get(Card, ch.card_id))) for ch in chapters]
    provenance.record(session, project_id=project_id, artifact_kind="narrative_fingerprint", artifact_key="fingerprint", content=fp, upstream=ups, producer="forge.analysis.build_fingerprint_card", schema_version=fp["version"], card_id=card.id)
    manifest = provenance.get_manifest(session, project_id, create=True)
    manifest.project_role = "source"
    manifest.source_manuscript_id = chapters[0].manuscript_id
    manifest.fingerprint_revision += 1
    manifest.updated_at = datetime.now()
    session.add(manifest)
    session.commit()
    session.refresh(card)
    return card


def build_example_library(session: Session, project_id: int) -> Dict[str, Any]:
    chapters = load_source_chapters(session, project_id)
    if not chapters:
        raise ValueError("No imported manuscript in this project")
    roles = _source_roles(session, project_id)
    candidates: List[ex.ExampleCandidate] = []
    for ch in chapters:
        an = ch.analysis or {}
        spans = []
        for s in an.get("scenes") or []:
            if not isinstance(s, dict):
                continue
            # Locate the scene through its verified evidence quotes.
            for o in an.get("observations") or []:
                if isinstance(o, dict) and o.get("verification_status") == "verified" and str(o.get("category") or "").startswith("scene:") and o.get("span_start", -1) >= 0:
                    fn = str(o["category"]).split(":", 1)[1]
                    spans.append((max(0, int(o["span_start"]) - 400), int(o["span_end"]) + 400, fn))
        candidates += ex.build_candidates(chapter_card_id=ch.card_id, chapter_number=ch.chapter_number, text=ch.text, manuscript_id=ch.manuscript_id, roles=roles, scene_functions=spans, hook_type=an.get("hook_type"), language=ch.language)
    n = ex.store_examples(session, project_id, chapters[0].manuscript_id, candidates, replace=True)
    provenance.record(session, project_id=project_id, artifact_kind="reference_example_index", artifact_key=chapters[0].manuscript_id, content={"count": n}, upstream=[provenance.Upstream("manuscript", chapters[0].manuscript_id, chapters[0].manuscript_id)], producer="forge.analysis.build_example_library", schema_version=ex.EXAMPLES_VERSION)
    session.commit()
    return {"examples": n, **ex.library_status(session, project_id)}


__all__ = ["ANALYSIS_PROMPT_VERSION", "analysis_status", "build_example_library", "build_fingerprint_card", "verify_all_analyses"]
