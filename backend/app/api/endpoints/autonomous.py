"""Autonomous novel API: upload an EPUB -> pick a storyline + chapter count -> download the novel.

Only three inputs are mandatory across the whole flow: the file, the selected
storyline and the chapter count. Everything else has defaults.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db.models import AutonomousNovelJob, ExportArtifact, LLMConfig, ModelInvocation, StorylineCandidate
from app.db.session import get_session
from app.services.autonomous import runner as runner_mod
from app.services.autonomous.storylines import candidate_dict
from app.services.autonomous.worker import autonomous_worker
from app.services.forge.models import validate_lab_llm_config

router = APIRouter()
MAX_UPLOAD_BYTES = 60 * 1024 * 1024


class CreateJobRequest(BaseModel):
    filename: str
    content_base64: str = Field(description="EPUB (or TXT/DOCX/Markdown) bytes, base64 encoded; only files the user has the right to analyse")
    llm_config_id: int = Field(description="Kimi K3 / AuthND configuration used for every role unless overridden")
    mode: str = Field(default="fully_automatic", description="fully_automatic | approval_gates | manual")
    role_llm_config_ids: Dict[str, int] = Field(default_factory=dict, description="Optional per-role LLM configuration overrides")
    # Optional preferences (all default sensibly)
    title: Optional[str] = None
    author: Optional[str] = None
    genre: Optional[str] = None
    genre_intensity: Optional[str] = None
    content_rating: Optional[str] = None
    ending_preference: Optional[str] = None
    romance_level: Optional[str] = None
    words_per_chapter: Optional[int] = Field(default=None, ge=300, le=20000)
    total_words: Optional[int] = Field(default=None, ge=1000)
    quality_preset: str = Field(default="balanced", description="economy | balanced | quality")
    storyline_count: int = Field(default=7, ge=5, le=10)
    fallback_llm_config_id: Optional[int] = None
    notes: Optional[str] = None


class SelectStorylineRequest(BaseModel):
    storyline_id: int
    chapter_count: int = Field(ge=1, le=400)
    words_per_chapter: Optional[int] = Field(default=None, ge=300, le=20000)
    title: Optional[str] = None


class JobResponse(BaseModel):
    job: Dict[str, Any]
    active: bool


def _decode(content_base64: str) -> bytes:
    raw = (content_base64 or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="content_base64 is empty")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (limit 60 MB)")
    return data


def _job(session: Session, job_id: int) -> AutonomousNovelJob:
    job = session.get(AutonomousNovelJob, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


def _response(session: Session, job: AutonomousNovelJob) -> JobResponse:
    return JobResponse(job=runner_mod.job_dict(session, job), active=autonomous_worker.is_active(int(job.id)))


def _quality_options(preset: str) -> Dict[str, Any]:
    return {"economy": {"max_repairs": 1, "analysis_concurrency": 6}, "quality": {"max_repairs": 3, "analysis_concurrency": 2}}.get(preset, {"max_repairs": 2, "analysis_concurrency": 4})


@router.post("/jobs", response_model=JobResponse, summary="Create Novel from EPUB: start the autonomous pipeline (ingest -> analysis -> fingerprint -> storyline options)")
async def create_job(req: CreateJobRequest, session: Session = Depends(get_session)):
    cfg = session.get(LLMConfig, req.llm_config_id)
    if cfg is None:
        raise HTTPException(status_code=400, detail=f"LLM configuration {req.llm_config_id} not found")
    ok, reason = validate_lab_llm_config(cfg)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    if req.mode not in runner_mod.MODES:
        raise HTTPException(status_code=400, detail=f"mode must be one of {runner_mod.MODES}")
    data = _decode(req.content_base64)
    options = {k: v for k, v in req.model_dump(exclude={"filename", "content_base64", "llm_config_id", "mode", "role_llm_config_ids"}).items() if v not in (None, "", {})}
    options.update(_quality_options(req.quality_preset))
    try:
        job = runner_mod.create_job(session, filename=req.filename, data=data, llm_config_id=req.llm_config_id, mode=req.mode, options=options, role_llm_config_ids=req.role_llm_config_ids)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    autonomous_worker.start(int(job.id))
    return _response(session, job)


@router.get("/jobs", response_model=List[Dict[str, Any]], summary="List autonomous jobs (newest first)")
def list_jobs(limit: int = 20, session: Session = Depends(get_session)):
    rows = session.exec(select(AutonomousNovelJob).order_by(AutonomousNovelJob.id.desc()).limit(max(1, min(limit, 100)))).all()
    return [{**runner_mod.job_dict(session, j), "attempts": [], "active": autonomous_worker.is_active(int(j.id))} for j in rows]


@router.get("/jobs/{job_id}", response_model=JobResponse, summary="Job status: stage, progress, cost, warnings, waiting_for")
def get_job(job_id: int, session: Session = Depends(get_session)):
    return _response(session, _job(session, job_id))


@router.get("/jobs/{job_id}/storylines", response_model=List[Dict[str, Any]], summary="Generated storyline options with originality and similarity data")
def list_storylines(job_id: int, include_rejected: bool = False, session: Session = Depends(get_session)):
    _job(session, job_id)
    rows = session.exec(select(StorylineCandidate).where(StorylineCandidate.job_id == job_id).order_by(StorylineCandidate.option_index)).all()
    return [candidate_dict(r) for r in rows if include_rejected or not r.rejected]


@router.post("/jobs/{job_id}/select", response_model=JobResponse, summary="Select a storyline and the chapter count; the pipeline continues automatically")
async def select_storyline(job_id: int, req: SelectStorylineRequest, session: Session = Depends(get_session)):
    job = _job(session, job_id)
    try:
        opts = {k: v for k, v in {"words_per_chapter": req.words_per_chapter, "title": req.title}.items() if v}
        job = runner_mod.select_storyline(session, job, storyline_id=req.storyline_id, chapter_count=req.chapter_count, options=opts)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    autonomous_worker.start(int(job.id))
    return _response(session, job)


@router.post("/jobs/{job_id}/approve", response_model=JobResponse, summary="Approve the current gate (approval_gates mode)")
async def approve(job_id: int, session: Session = Depends(get_session)):
    job = _job(session, job_id)
    try:
        job = runner_mod.approve(session, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    autonomous_worker.start(int(job.id))
    return _response(session, job)


@router.post("/jobs/{job_id}/pause", response_model=JobResponse, summary="Pause after the current stage step")
async def pause(job_id: int, session: Session = Depends(get_session)):
    job = _job(session, job_id)
    autonomous_worker.stop(int(job.id))
    job = runner_mod.request_pause(session, job)
    return _response(session, job)


@router.post("/jobs/{job_id}/resume", response_model=JobResponse, summary="Resume a paused or recovered job from its persisted stage")
async def resume(job_id: int, session: Session = Depends(get_session)):
    job = _job(session, job_id)
    if autonomous_worker.is_active(int(job.id)):
        raise HTTPException(status_code=409, detail="Job is already running")
    try:
        job = runner_mod.resume(session, job)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    autonomous_worker.start(int(job.id))
    return _response(session, job)


@router.post("/jobs/{job_id}/cancel", response_model=JobResponse, summary="Cancel the job")
async def cancel(job_id: int, session: Session = Depends(get_session)):
    job = _job(session, job_id)
    autonomous_worker.stop(int(job.id))
    return _response(session, runner_mod.cancel(session, job))


@router.get("/jobs/{job_id}/chapters", response_model=List[Dict[str, Any]], summary="Live chapter preview: committed chapters with summaries and validation status")
def chapters(job_id: int, session: Session = Depends(get_session)):
    from app.services.autonomous.audit import chapter_texts

    job = _job(session, job_id)
    if not job.original_project_id:
        return []
    out = []
    for n, card, text in chapter_texts(session, int(job.original_project_id)):
        c = card.content if isinstance(card.content, dict) else {}
        out.append({"chapter_number": n, "title": c.get("title"), "words": len(text.split()), "summary": c.get("summary"), "sync_status": c.get("sync_status"), "validation_passed": c.get("validation_passed"), "card_id": card.id, "preview": text[:600]})
    return out


@router.get("/jobs/{job_id}/invocations", response_model=List[Dict[str, Any]], summary="Model invocations (role, prompt version, usage, latency, retries, validation)")
def invocations(job_id: int, limit: int = 200, session: Session = Depends(get_session)):
    _job(session, job_id)
    rows = session.exec(select(ModelInvocation).where(ModelInvocation.job_id == job_id).order_by(ModelInvocation.id.desc()).limit(max(1, min(limit, 2000)))).all()
    return [{k: getattr(r, k) for k in ("id", "stage", "role", "llm_config_id", "model_name", "prompt_version", "schema_name", "temperature", "input_tokens_estimate", "input_tokens", "output_tokens", "latency_ms", "retries", "validation_status", "error")} | {"created_at": r.created_at.isoformat()} for r in rows]


@router.get("/jobs/{job_id}/artifacts", response_model=List[Dict[str, Any]], summary="Export artifacts available for download")
def artifacts(job_id: int, session: Session = Depends(get_session)):
    _job(session, job_id)
    rows = session.exec(select(ExportArtifact).where(ExportArtifact.job_id == job_id).order_by(ExportArtifact.id)).all()
    return [{"id": r.id, "kind": r.kind, "filename": r.filename, "media_type": r.media_type, "size_bytes": r.size_bytes, "content_hash": r.content_hash, "created_at": r.created_at.isoformat()} for r in rows]


@router.get("/artifacts/{artifact_id}/download", summary="Download one export artifact")
def download(artifact_id: int, session: Session = Depends(get_session)):
    row = session.get(ExportArtifact, artifact_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Artifact not found")
    return Response(content=row.data, media_type=row.media_type, headers={"Content-Disposition": f'attachment; filename="{row.filename}"'})


@router.get("/jobs/{job_id}/report", response_model=Dict[str, Any], summary="Quality / originality / cost report of a finished (or in-progress) job")
def report(job_id: int, session: Session = Depends(get_session)):
    from app.services.autonomous.export import run_summary

    job = _job(session, job_id)
    results = job.stage_results or {}
    audit = (results.get("GLOBAL_REPAIR") or {}).get("audit") or results.get("WHOLE_NOVEL_AUDIT") or {}
    return {"job_id": job.id, "status": job.status, "stage": job.stage, "audit": {k: v for k, v in audit.items() if k != "findings"}, "findings": (audit.get("findings") or [])[:200], "ingestion": (results.get("INGEST") or {}).get("quality"), "storylines": results.get("STORYLINE_GENERATION"), "architecture": results.get("NOVEL_ARCHITECTURE"), "run": run_summary(session, job)}


__all__ = ["router"]
