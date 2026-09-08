"""Narrative Reverse-Engineering Lab API: manuscript import wizard + workflow launch.

Analysis itself runs through the "Narrative Reverse-Engineering Lab" workflow so
it benefits from background execution, node progress and checkpoint resume.
"""

from __future__ import annotations

import base64
import binascii
import re
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from loguru import logger
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.db.models import LLMConfig, Project, Workflow, WorkflowRun
from app.db.session import get_session
from app.services.lab.manuscript_import import (
    CHAPTER_PATTERN_CANDIDATES,
    DEFAULT_MIN_CHAPTER_WORDS,
    MAIN_STORY_TYPES,
    SECTION_TYPES,
    SUPPORTED_EXTENSIONS,
    VOLUME_PATTERN_DEFAULT,
    DetectedChapter,
    DetectionResult,
    ManuscriptImportService,
    apply_corrections,
    detect_chapters,
    estimate_input_tokens,
    included_chapters,
)
from app.services.workflow.engine.run_manager import RunManager
from app.services.workflow.engine.runtime import workflow_runtime

router = APIRouter()

LAB_WORKFLOW_NAME = "Narrative Reverse-Engineering Lab"
MAX_UPLOAD_BYTES = 60 * 1024 * 1024


class ManuscriptPreviewRequest(BaseModel):
    filename: str
    content_base64: str = Field(description="File bytes, base64 encoded (user-supplied file only)")
    encoding: Optional[str] = Field(default=None, description="Text encoding for TXT/MD; auto-detect when empty")
    chapter_pattern: Optional[str] = Field(default=None, description="Chapter heading regex; auto-detected when empty")
    volume_pattern: Optional[str] = Field(default=None, description="Volume heading regex")
    exclude_front_matter: bool = True
    exclude_afterword: bool = True
    min_chapter_words: int = Field(default=DEFAULT_MIN_CHAPTER_WORDS, ge=1, le=20000, description="Sections shorter than this are flagged very_short / treated as front matter")
    corrections: List[Dict[str, Any]] = Field(default_factory=list, description="split/merge/exclude/include/rename/set_type ops addressed by section_id")
    preview_chars: int = Field(default=400, ge=0, le=4000)


class ChapterPreview(BaseModel):
    index: int
    number: Optional[int]
    title: str
    volume: str
    word_count: int
    flags: List[str]
    preview: str
    section_id: str
    source_path: str
    spine_index: int
    source_label: str
    section_type: str
    is_main_story: bool
    included: bool
    exclusion_reason: str
    classification_confidence: float
    classification_evidence: List[str]
    manually_overridden: bool


class ManuscriptPreviewResponse(BaseModel):
    chapter_pattern: str
    pattern_name: str
    volume_pattern: str
    chapters: List[ChapterPreview]
    volumes: List[str]
    warnings: List[str]
    total_words: int
    total_chapters: int
    included_chapters: int
    estimated_input_tokens: int
    pattern_candidates: List[Dict[str, str]]
    supported_extensions: List[str]
    section_types: List[str]
    min_chapter_words: int
    spine_item_count: int
    main_story_end_index: Optional[int]
    main_story_end_title: Optional[str]
    excluded_side_story_count: int
    excluded_bonus_extra_count: int
    excluded_other_count: int
    uncertain_count: int
    excluded_word_count: int
    excluded_side_story_word_count: int
    book_meta: Dict[str, Any]


class ManuscriptImportRequest(ManuscriptPreviewRequest):
    project_id: int
    book_title: str = ""
    author: str = ""
    genre: str = ""
    language: str = ""
    replace_existing: bool = True


class ManuscriptImportResponse(BaseModel):
    folder_card_id: int
    chapter_card_ids: List[int]
    chapter_count: int
    total_words: int
    excluded_count: int = 0
    excluded_words: int = 0
    manuscript_id: str = ""
    unchanged: bool = False
    invalidated: int = 0


class ManuscriptListResponse(BaseModel):
    folder_card_id: Optional[int]
    meta: Dict[str, Any]
    chapters: List[Dict[str, Any]]


class LabRunRequest(BaseModel):
    project_id: int
    llm_config_id: int
    analysis_concurrency: int = Field(default=2, ge=1, le=32)
    window_size: int = Field(default=40, ge=5, le=200)
    max_stage_count: int = Field(default=24, ge=3, le=60)
    # Analysis scope (normalized chapter numbers). Defaults: whole manuscript, missing only.
    start_chapter: int = Field(default=0, ge=0, description="First chapter to analyse (0 = from the start)")
    end_chapter: int = Field(default=0, ge=0, description="Last chapter to analyse, inclusive (0 = to the end)")
    include_chapters: List[int] = Field(default_factory=list, description="Explicit chapter numbers to analyse (overrides the range)")
    exclude_chapters: List[int] = Field(default_factory=list, description="Chapter numbers to skip")
    only_missing: bool = Field(default=True, description="Skip chapters whose analysis is already done")
    only_stale: bool = Field(default=False, description="Also re-analyse done chapters whose source text or prompt version changed")


class LabRunPlan(BaseModel):
    """Cost preview for a Lab run: what would be sent to the model, without sending anything."""
    project_id: int
    chapters_total: int
    chapters_done: int
    chapters_failed: int
    chapters_selected: int
    selected_chapter_numbers: List[int]
    estimated_input_tokens: int
    estimated_model_calls: int
    manuscript_id: str = ""


class LabRunStatus(BaseModel):
    run_id: int
    workflow_id: int
    project_id: int
    status: str
    percent: float = 0.0
    current_node: Optional[str] = None
    current_message: Optional[str] = None
    chapters_total: int = 0
    chapters_done: int = 0
    chapters_failed: int = 0
    error: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    nodes: List[Dict[str, Any]] = Field(default_factory=list)


def _decode_upload(content_base64: str) -> bytes:
    raw = (content_base64 or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="content_base64 is empty")
    if len(raw) > MAX_UPLOAD_BYTES * 4 // 3 + 16:
        raise HTTPException(status_code=413, detail="File too large (limit 60 MB)")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="content_base64 is not valid base64")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (limit 60 MB)")
    return data


def _detect(req: ManuscriptPreviewRequest) -> Tuple[List[DetectedChapter], DetectionResult]:
    """One canonical detection used by both preview and import."""
    data = _decode_upload(req.content_base64)
    try:
        result = detect_chapters(
            req.filename,
            data,
            chapter_pattern=req.chapter_pattern or None,
            volume_pattern=req.volume_pattern or None,
            exclude_front_matter=req.exclude_front_matter,
            exclude_afterword=req.exclude_afterword,
            min_chapter_words=req.min_chapter_words,
            encoding=req.encoding,
        )
    except re.error as e:
        raise HTTPException(status_code=400, detail=f"Invalid chapter/volume regex: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {e}")
    chapters = apply_corrections(result.chapters, req.corrections) if req.corrections else result.chapters
    return chapters, result


def _preview_response(req: ManuscriptPreviewRequest, chapters: List[DetectedChapter], result: DetectionResult) -> ManuscriptPreviewResponse:
    included = included_chapters(chapters)
    excluded = [c for c in chapters if not c.included]
    side = [c for c in excluded if c.section_type == "side_story"]
    bonus_extra = [c for c in excluded if c.section_type in ("bonus_story", "extra")]
    uncertain = [c for c in chapters if c.section_type == "unknown"]
    end_title = None
    if result.main_story_end_index:
        end_chapter = next((c for c in result.chapters if c.index == result.main_story_end_index), None)
        end_title = end_chapter.title if end_chapter else None
    return ManuscriptPreviewResponse(
        chapter_pattern=result.chapter_pattern,
        pattern_name=result.pattern_name,
        volume_pattern=result.volume_pattern,
        chapters=[
            ChapterPreview(
                index=c.index, number=c.number, title=c.title, volume=c.volume, word_count=c.word_count, flags=c.flags,
                preview=c.text[: req.preview_chars], section_id=c.section_id, source_path=c.source_path, spine_index=c.spine_index,
                source_label=c.source_label, section_type=c.section_type, is_main_story=c.is_main_story, included=c.included,
                exclusion_reason=c.exclusion_reason, classification_confidence=c.classification_confidence,
                classification_evidence=c.classification_evidence, manually_overridden=c.manually_overridden,
            )
            for c in chapters
        ],
        volumes=result.volumes,
        warnings=result.warnings,
        total_words=sum(c.word_count for c in included),
        total_chapters=len(chapters),
        included_chapters=len(included),
        estimated_input_tokens=estimate_input_tokens(chapters),
        pattern_candidates=[{"name": n, "pattern": p} for n, p in CHAPTER_PATTERN_CANDIDATES],
        supported_extensions=list(SUPPORTED_EXTENSIONS),
        section_types=list(SECTION_TYPES),
        min_chapter_words=req.min_chapter_words,
        spine_item_count=result.spine_item_count,
        main_story_end_index=result.main_story_end_index,
        main_story_end_title=end_title,
        excluded_side_story_count=len(side),
        excluded_bonus_extra_count=len(bonus_extra),
        excluded_other_count=len(excluded) - len(side) - len(bonus_extra),
        uncertain_count=len(uncertain),
        excluded_word_count=sum(c.word_count for c in excluded),
        excluded_side_story_word_count=sum(c.word_count for c in side + bonus_extra),
        book_meta=result.book_meta,
    )


@router.post("/manuscript/preview", response_model=ManuscriptPreviewResponse, summary="Parse a user-supplied manuscript and preview section detection / classification")
def preview_manuscript(req: ManuscriptPreviewRequest):
    chapters, result = _detect(req)
    return _preview_response(req, chapters, result)


@router.post("/manuscript/import", response_model=ManuscriptImportResponse, summary="Store the corrected, included chapters as Chapter Analysis cards")
def import_manuscript(req: ManuscriptImportRequest, session: Session = Depends(get_session)):
    chapters, result = _detect(req)
    if not session.get(Project, req.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    svc = ManuscriptImportService(session)
    try:
        stored = svc.store_manuscript(
            project_id=req.project_id,
            title=req.book_title or result.book_meta.get("title") or req.filename,
            author=req.author or result.book_meta.get("creator") or "",
            genre=req.genre,
            language=req.language or result.book_meta.get("language") or "",
            chapters=chapters,
            replace_existing=req.replace_existing,
            source_filename=req.filename,
            source_bytes=_decode_upload(req.content_base64),
            corrections=req.corrections,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return ManuscriptImportResponse(**stored)


@router.get("/manuscript", response_model=ManuscriptListResponse, summary="List the imported manuscript chapters and their analysis status")
def list_manuscript(project_id: int, session: Session = Depends(get_session)):
    return ManuscriptImportService(session).list_manuscript(project_id)


@router.get("/manuscript/defaults", summary="Default detection patterns")
def manuscript_defaults():
    return {
        "volume_pattern": VOLUME_PATTERN_DEFAULT,
        "pattern_candidates": [{"name": n, "pattern": p} for n, p in CHAPTER_PATTERN_CANDIDATES],
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        "section_types": list(SECTION_TYPES),
        "main_story_types": sorted(MAIN_STORY_TYPES),
        "min_chapter_words": DEFAULT_MIN_CHAPTER_WORDS,
    }


# ------------------------------------------------------------------ workflow
def _lab_workflow_code(base_code: str, *, project_id: int, llm_config_id: int, concurrency: int, window_size: int, max_stage_count: int, scope: Optional[Dict[str, Any]] = None) -> str:
    code = re.sub(r"Logic\.SelectProject\([^)]*\)", f"Logic.SelectProject(project_id={int(project_id)})", base_code, count=1)
    code = re.sub(r"Logic\.SelectLLM\([^)]*\)", f"Logic.SelectLLM(llm_config_id={int(llm_config_id)})", code, count=1)
    code = re.sub(r"'window_size':\s*\d+", f"'window_size': {int(window_size)}", code, count=1)
    code = re.sub(r"'max_stage_count':\s*\d+", f"'max_stage_count': {int(max_stage_count)}", code, count=1)
    code = re.sub(r"'analysis_concurrency':\s*\d+", f"'analysis_concurrency': {int(concurrency)}", code, count=1)
    if scope:
        code = re.sub(r"'start_chapter':\s*\d+", f"'start_chapter': {int(scope.get('start_chapter') or 0)}", code, count=1)
        code = re.sub(r"'end_chapter':\s*\d+", f"'end_chapter': {int(scope.get('end_chapter') or 0)}", code, count=1)
        code = re.sub(r"'include_chapters':\s*\[[^\]]*\]", f"'include_chapters': {sorted({int(x) for x in scope.get('include_chapters') or []})}", code, count=1)
        code = re.sub(r"'exclude_chapters':\s*\[[^\]]*\]", f"'exclude_chapters': {sorted({int(x) for x in scope.get('exclude_chapters') or []})}", code, count=1)
        code = re.sub(r"'only_missing':\s*(True|False)", f"'only_missing': {bool(scope.get('only_missing', True))}", code, count=1)
        code = re.sub(r"'only_stale':\s*(True|False)", f"'only_stale': {bool(scope.get('only_stale', False))}", code, count=1)
    return code


def _scope_of(req: LabRunRequest) -> Dict[str, Any]:
    return {"start_chapter": req.start_chapter, "end_chapter": req.end_chapter, "include_chapters": req.include_chapters, "exclude_chapters": req.exclude_chapters, "only_missing": req.only_missing, "only_stale": req.only_stale}


def _plan_run(session: Session, req: LabRunRequest) -> LabRunPlan:
    from app.services.forge.corpus import load_source_chapters
    from app.services.lab.lab_helpers import ANALYSIS_PROMPT_VERSION, fn_lab_chapter_items

    chapters = load_source_chapters(session, req.project_id)
    cards = [{"id": ch.card_id, "content": {**(ch.analysis or {}), "source_text": ch.text, "chapter_number": ch.chapter_number, "source_text_hash": ch.text_hash}} for ch in chapters]
    items = fn_lab_chapter_items(cards, prompt_version=ANALYSIS_PROMPT_VERSION, **_scope_of(req))
    words = sum(int(it.get("word_count") or 0) for it in items)
    return LabRunPlan(
        project_id=req.project_id,
        chapters_total=len(chapters),
        chapters_done=sum(1 for ch in chapters if (ch.analysis or {}).get("analysis_status") == "done"),
        chapters_failed=sum(1 for ch in chapters if (ch.analysis or {}).get("analysis_status") == "failed"),
        chapters_selected=len(items),
        selected_chapter_numbers=[int(it["chapter_no"]) for it in items],
        estimated_input_tokens=int(words * 1.4) + len(items) * 1500,
        estimated_model_calls=len(items),
        manuscript_id=chapters[0].manuscript_id if chapters else "",
    )


def _builtin_lab_workflow_code() -> Optional[str]:
    from app.bootstrap.workflows import get_all_workflow_files

    try:
        return (get_all_workflow_files().get(LAB_WORKFLOW_NAME) or {}).get("code") or None
    except Exception as exc:  # pragma: no cover - filesystem problems fall back to the DB row
        logger.warning(f"[Lab] could not read built-in workflow file: {exc}")
        return None


def _project_lab_workflow(session: Session, req: LabRunRequest) -> Workflow:
    """Return the project-scoped copy of the built-in Lab workflow (created/updated on demand)."""
    # The shipped .wf file is the source of truth: built-in rows are only refreshed
    # at startup when BOOTSTRAP_OVERWRITE is set, and a stale row would silently
    # ignore the analysis scope (and analyse the whole manuscript).
    base_code = _builtin_lab_workflow_code()
    if not base_code:
        base = session.exec(select(Workflow).where(Workflow.name == LAB_WORKFLOW_NAME)).first()
        base_code = base.definition_code if base else None
    if not base_code:
        raise HTTPException(status_code=500, detail=f"Built-in workflow '{LAB_WORKFLOW_NAME}' is missing")
    name = f"{LAB_WORKFLOW_NAME} · project {req.project_id}"
    wf = session.exec(select(Workflow).where(Workflow.name == name)).first()
    code = _lab_workflow_code(
        base_code, project_id=req.project_id, llm_config_id=req.llm_config_id,
        concurrency=req.analysis_concurrency, window_size=req.window_size, max_stage_count=req.max_stage_count,
        scope=_scope_of(req),
    )
    if wf is None:
        wf = Workflow(name=name, description=f"Project-scoped Lab run for project {req.project_id}", is_built_in=False, is_active=True, dsl_version=2, definition_code=code, keep_run_history=True)
        session.add(wf)
        session.commit()
        session.refresh(wf)
    elif wf.definition_code != code:
        active = _active_run(session, wf.id)
        if active:
            raise HTTPException(status_code=409, detail=f"A Lab run (id={active.id}, status={active.status}) is already active for this project")
        wf.definition_code = code
        wf.is_active = True
        wf.updated_at = datetime.now()
        session.add(wf)
        session.commit()
        session.refresh(wf)
    return wf


def _active_run(session: Session, workflow_id: int) -> Optional[WorkflowRun]:
    return session.exec(
        select(WorkflowRun).where(WorkflowRun.workflow_id == workflow_id, WorkflowRun.status.in_(["queued", "running"])).order_by(WorkflowRun.id.desc())
    ).first()


def _project_id_of_workflow(wf: Workflow) -> Optional[int]:
    m = re.search(r"project (\d+)$", wf.name or "")
    return int(m.group(1)) if m else None


def _run_status(session: Session, run: WorkflowRun) -> LabRunStatus:
    manager = RunManager(session)
    info = manager.get_run_status(run.id) or {}
    nodes = info.get("nodes") or []
    total = done = failed = 0
    current_node = None
    current_message = None
    percent = 0.0
    if nodes:
        finished = sum(1 for n in nodes if n.get("status") in ("success", "skipped"))
        percent = round(100.0 * finished / max(1, len(nodes)), 1)
    for n in nodes:
        if n.get("status") == "running" and current_node is None:
            current_node = n.get("node_id")
            cp = n.get("checkpoint") or {}
            current_message = cp.get("message")
        if n.get("node_id") == "analysis_results":
            cp = (n.get("checkpoint") or {}).get("data") or {}
            done = len(cp.get("processed_indices") or [])
            failed = len(cp.get("failed_indices") or [])
            current_message = current_message or (n.get("checkpoint") or {}).get("message")
    try:
        from app.services.lab.manuscript_import import ManuscriptImportService
        pid = _project_id_of_workflow(run.workflow) or 0
        total = len(ManuscriptImportService(session).list_manuscript(pid).get("chapters") or []) if pid else 0
    except Exception:
        total = 0
    err = run.error_json
    if isinstance(err, dict):
        err = err.get("message") or err.get("error") or str(err)
    return LabRunStatus(
        run_id=run.id, workflow_id=run.workflow_id, project_id=_project_id_of_workflow(run.workflow) or 0, status=run.status,
        percent=percent, current_node=current_node, current_message=current_message,
        chapters_total=total, chapters_done=done, chapters_failed=failed, error=str(err) if err else None,
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        nodes=[{"node_id": n.get("node_id"), "status": n.get("status"), "progress": n.get("progress"), "error": n.get("error")} for n in nodes],
    )


@router.post("/workflow/plan", response_model=LabRunPlan, summary="Cost preview for a Lab run: which chapters the scope selects and the estimated model calls / input tokens (no model call)")
def plan_lab_workflow(req: LabRunRequest, session: Session = Depends(get_session)):
    if not session.get(Project, req.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    return _plan_run(session, req)


@router.post("/workflow/run", response_model=LabRunStatus, summary="Start the Lab reverse-engineering workflow for a project (idempotent while a run is active)")
async def start_lab_workflow(req: LabRunRequest, session: Session = Depends(get_session)):
    if not session.get(Project, req.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    llm_cfg = session.get(LLMConfig, req.llm_config_id)
    if not llm_cfg:
        raise HTTPException(status_code=400, detail=f"LLM configuration {req.llm_config_id} not found")
    from app.services.forge.models import validate_lab_llm_config

    ok, reason = validate_lab_llm_config(llm_cfg)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)
    manuscript = ManuscriptImportService(session).list_manuscript(req.project_id)
    if not manuscript.get("chapters"):
        raise HTTPException(status_code=400, detail="No imported manuscript in this project. Import chapters first.")
    plan = _plan_run(session, req)
    if plan.chapters_selected == 0:
        raise HTTPException(status_code=400, detail="The requested analysis scope selects no chapters (all selected chapters are already analysed, or the range is empty). Use only_stale / only_missing=false to re-analyse.")
    wf = _project_lab_workflow(session, req)
    existing = _active_run(session, wf.id)
    if existing:
        # Duplicate launch: return the active run instead of starting another.
        return _run_status(session, existing)
    manager = RunManager(session)
    run = manager.create_run(
        workflow_id=wf.id,
        params={"project_id": req.project_id, "llm_config_id": req.llm_config_id},
        idempotency_key=f"lab:{req.project_id}",
    )
    await manager.start_run(run.id)
    session.refresh(run)
    logger.info(f"[Lab] started run {run.id} for project {req.project_id}")
    return _run_status(session, run)


@router.get("/workflow/runs", response_model=List[LabRunStatus], summary="List Lab runs for a project (newest first)")
def list_lab_runs(project_id: int, limit: int = 10, session: Session = Depends(get_session)):
    name = f"{LAB_WORKFLOW_NAME} · project {project_id}"
    wf = session.exec(select(Workflow).where(Workflow.name == name)).first()
    if not wf:
        return []
    runs = session.exec(select(WorkflowRun).where(WorkflowRun.workflow_id == wf.id).order_by(WorkflowRun.id.desc()).limit(max(1, min(limit, 50)))).all()
    return [_run_status(session, r) for r in runs]


@router.get("/workflow/runs/{run_id}", response_model=LabRunStatus, summary="Lab run status (progress, current node, errors)")
def get_lab_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return _run_status(session, run)


@router.post("/workflow/runs/{run_id}/cancel", response_model=LabRunStatus, summary="Cancel a Lab run")
async def cancel_lab_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    manager = RunManager(session)
    await manager.cancel_run(run_id)
    session.refresh(run)
    if run.status in ("queued", "running"):
        run.status = "cancelled"
        run.finished_at = datetime.now()
        session.add(run)
        session.commit()
        session.refresh(run)
    return _run_status(session, run)


@router.post("/workflow/runs/{run_id}/resume", response_model=LabRunStatus, summary="Resume / retry a paused, failed, cancelled or timed-out Lab run from its checkpoints")
async def resume_lab_run(run_id: int, session: Session = Depends(get_session)):
    run = session.get(WorkflowRun, run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status in ("queued", "running") or workflow_runtime.is_active(run_id):
        raise HTTPException(status_code=409, detail="Run is already active")
    if run.status == "succeeded":
        raise HTTPException(status_code=400, detail="Run already succeeded")
    other = _active_run(session, run.workflow_id)
    if other and other.id != run.id:
        raise HTTPException(status_code=409, detail=f"Another run (id={other.id}) is active for this project")
    # Completed node states stay in place; the executor skips them and retries the rest.
    run.status = "queued"
    run.error_json = None
    run.finished_at = None
    session.add(run)
    session.commit()
    manager = RunManager(session)
    await manager.start_run(run_id)
    session.refresh(run)
    return _run_status(session, run)
