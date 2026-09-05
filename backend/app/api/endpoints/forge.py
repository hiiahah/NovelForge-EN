"""Forge API: the automated reverse-engineering -> original-generation flow.

Steps exposed to the UI (Phase 18):
1. Import reference novel      -> /api/lab/manuscript/*  (existing)
2. Analyze reference           -> /api/lab/workflow/run  (existing) + /forge/source/verify
3. Build Narrative Fingerprint -> /forge/source/fingerprint + /forge/source/examples
4. Build original foundation   -> /forge/original/create
5. Compile original Bible      -> existing Bible workflows + /forge/original/seed-canon
6. Generate outlines           -> existing outline workflows (Chapter Outline now carries pov/beats)
7. Generate chapters           -> /forge/chapters/run (compile -> draft -> validate -> repair -> commit -> sync)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db.models import ChapterPipelineRun, Project
from app.db.session import get_session
from app.services.bible.bible_service import BibleService
from app.services.forge import analysis as forge_analysis
from app.services.forge import audits as forge_audits
from app.services.forge import canon as canon_store
from app.services.forge import models as forge_models
from app.services.forge import provenance
from app.services.forge import transfer
from app.services.forge.compiler import ChapterContextCompiler, ContextCompileError
from app.services.forge.pipeline import LLMDrafter, PipelineOptions, run_chapter

router = APIRouter()


def _project(session: Session, project_id: int) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


# ------------------------------------------------------------------ source side

@router.get("/source/status", summary="Source analysis completeness, evidence coverage, failed chapters, fingerprint and example index status")
def source_status(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    return forge_analysis.analysis_status(session, project_id)


@router.post("/source/verify", summary="Re-verify every stored chapter analysis against the imported chapter text")
def source_verify(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    return forge_analysis.verify_all_analyses(session, project_id)


@router.post("/source/fingerprint", summary="Build / rebuild the 20-layer Narrative Fingerprint from measured chapters and verified observations")
def source_fingerprint(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    try:
        card = forge_analysis.build_fingerprint_card(session, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    content = card.content if isinstance(card.content, dict) else {}
    return {"card_id": card.id, "version": content.get("version"), "dependency_hash": content.get("dependency_hash"), "chapters_measured": content.get("chapters_measured"), "layers": list((content.get("layers") or {}).keys())}


@router.post("/source/examples", summary="Build / rebuild the function-tagged Reference Example Library")
def source_examples(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    try:
        return forge_analysis.build_example_library(session, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


# ---------------------------------------------------------------- original side

class CreateOriginalRequest(BaseModel):
    source_project_id: int
    name: str
    description: str = ""
    template: Optional[str] = Field(default="bible", description="Project template used to scaffold the original Bible chain")


@router.post("/original/create", summary="Create a strictly separated original project (fingerprint + abstract mechanisms only)")
def original_create(req: CreateOriginalRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, req.source_project_id)
    try:
        result = transfer.create_original_project(session, source_project_id=req.source_project_id, name=req.name, description=req.description, template=req.template)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result.__dict__


@router.get("/original/isolation", summary="Source/original separation report (firewall over every Bible card)")
def original_isolation(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    return transfer.isolation_report(session, project_id)


@router.post("/original/seed-canon", summary="Seed chapter-0 canon facts from the approved original Bible cards")
def original_seed_canon(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    iso = transfer.isolation_report(session, project_id)
    if not iso["isolated"]:
        raise HTTPException(status_code=409, detail={"message": "Original project is contaminated with source content", "problems": iso["problems"]})
    bible = BibleService(session)
    manifest = provenance.get_manifest(session, project_id, create=True)
    cards = []
    for t in ("Character Card", "Relationship Arc", "Knowledge Fact", "Item Card"):
        cards += bible.cards_of_type(project_id, t)
    n = canon_store.seed_from_bible(session, project_id, cards, canon_revision=manifest.canon_revision)
    session.commit()
    return {"facts_seeded": n, "canon_revision": manifest.canon_revision}


@router.get("/manifest", summary="Project Narrative Manifest (revisions, next allowed chapter, stale dependencies, unresolved errors)")
def manifest(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    return provenance.manifest_dict(session, project_id)


@router.get("/audit", summary="Card-quality audits: duplicates, alias collisions, unresolved references, stale cards, contamination")
def audit(project_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    return forge_audits.audit_project(session, project_id)


# ------------------------------------------------------------------- chapters

class CompileRequest(BaseModel):
    project_id: int
    chapter_number: int
    outline_card_id: Optional[int] = None
    pov: Optional[str] = None
    participants: Optional[List[str]] = None
    expected_canon_revision: Optional[int] = None
    budget_chars: int = Field(default=16000, ge=4000, le=60000)
    regenerate: bool = False


@router.post("/chapters/compile", summary="Compile the authoritative chapter context (fail-closed; no model call)")
def compile_chapter(req: CompileRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, req.project_id)
    try:
        ctx = ChapterContextCompiler(session).compile(project_id=req.project_id, chapter_number=req.chapter_number, outline_card_id=req.outline_card_id, pov=req.pov, participants=req.participants, expected_canon_revision=req.expected_canon_revision, budget_chars=req.budget_chars, regenerate=req.regenerate)
        session.commit()
    except ContextCompileError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=exc.as_dict())
    return ctx.as_dict()


class RunChapterRequest(BaseModel):
    project_id: int
    chapter_number: int
    llm_config_id: int = Field(description="Drafting model; other roles default to it")
    role_llm_config_ids: Dict[str, int] = Field(default_factory=dict, description="Optional per-role overrides: planning, validator, repair, evaluator")
    outline_card_id: Optional[int] = None
    pov: Optional[str] = None
    participants: Optional[List[str]] = None
    expected_canon_revision: Optional[int] = None
    max_repairs: int = Field(default=2, ge=0, le=5)
    budget_chars: int = Field(default=16000, ge=4000, le=60000)
    word_target: Optional[int] = None
    regenerate: bool = False
    temperature: float = 0.7
    max_tokens: int = 8192
    timeout: float = 240.0


@router.post("/chapters/run", summary="Run compile -> draft -> validate -> repair -> commit -> synchronize for one chapter")
async def run_chapter_endpoint(req: RunChapterRequest, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, req.project_id)
    try:
        roles = forge_models.resolve_roles(session, dict(req.role_llm_config_ids), default_llm_config_id=req.llm_config_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    drafter = LLMDrafter(session, {r: res.llm_config_id for r, res in roles.items()}, temperature=req.temperature, max_tokens=req.max_tokens, timeout=req.timeout)
    result = await run_chapter(session, project_id=req.project_id, chapter_number=req.chapter_number, drafter=drafter, outline_card_id=req.outline_card_id, pov=req.pov, participants=req.participants, expected_canon_revision=req.expected_canon_revision, options=PipelineOptions(max_repairs=req.max_repairs, budget_chars=req.budget_chars, word_target=req.word_target, regenerate=req.regenerate))
    out = result.as_dict()
    out["model_roles"] = {r: res.as_dict() for r, res in roles.items()}
    if result.context:
        out["context"] = {k: v for k, v in result.context.items() if k != "text"}
    return out


@router.get("/chapters/runs", summary="Pipeline runs for a project (newest first)")
def list_runs(project_id: int, limit: int = 20, session: Session = Depends(get_session)) -> List[Dict[str, Any]]:
    _project(session, project_id)
    rows = session.exec(select(ChapterPipelineRun).where(ChapterPipelineRun.project_id == project_id).order_by(ChapterPipelineRun.id.desc()).limit(max(1, min(limit, 100)))).all()
    return [{
        "run_id": r.id, "chapter_number": r.chapter_number, "status": r.status, "stage": r.stage, "chapter_card_id": r.chapter_card_id,
        "canon_revision_before": r.canon_revision_before, "canon_revision_after": r.canon_revision_after, "context_hash": r.context_hash,
        "validation_passed": (r.validation_report or {}).get("passed"), "blocking_issues": (r.validation_report or {}).get("blocking"),
        "style_score": (r.style_report or {}).get("adherence_score"), "originality_passed": ((r.validation_report or {}).get("originality") or {}).get("passed"),
        "repair_attempts": r.repair_attempts, "model_calls": r.model_calls, "error": r.error, "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]


@router.get("/chapters/runs/{run_id}", summary="Full report of one pipeline run")
def get_run(run_id: int, session: Session = Depends(get_session)) -> Dict[str, Any]:
    r = session.get(ChapterPipelineRun, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return {k: getattr(r, k) for k in ("id", "project_id", "chapter_number", "chapter_card_id", "outline_card_id", "status", "stage", "canon_revision_before", "canon_revision_after", "context_hash", "context_manifest", "validation_report", "style_report", "sync_report", "repair_attempts", "model_calls", "error")}


@router.get("/canon/state", summary="Canon state as-of a chapter (temporal snapshot)")
def canon_state(project_id: int, chapter: int, subject: Optional[str] = None, session: Session = Depends(get_session)) -> Dict[str, Any]:
    _project(session, project_id)
    facts = canon_store.state_as_of(session, project_id, chapter, subjects=[subject] if subject else None)
    return {"project_id": project_id, "as_of_chapter": chapter, "facts": [fv.as_dict() for fv in facts.values()]}
