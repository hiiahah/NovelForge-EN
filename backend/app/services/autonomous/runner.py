"""Durable, resumable state machine for one autonomous novel job.

The runner executes one stage at a time. Before a stage runs, the job row is
committed with ``status='running'`` and a lease; after it succeeds, the row is
committed with the next stage. A process restart therefore resumes exactly at
the persisted ``stage``; every stage function is idempotent on its own
artifacts (keyed by manuscript id, chapter number or card title), so a stage
that was interrupted midway re-runs without duplicating work.

Stage failures are classified (``failures.classify_exception``) and recovered
through the category's ladder; when the ladder is exhausted the job pauses
(``status='paused'``) with the classified error so a later ``resume`` retries
from the same stage.

Human gates: ``STORYLINE_SELECTION`` always waits for the user (that is the
product's single mandatory creative intervention); ``approval_gates`` mode also
waits after ``CHAPTER_PLAN_BUILD`` (final plan) and after ``GLOBAL_REPAIR``
(final manuscript). ``manual`` mode stops after ``EXAMPLE_LIBRARY_BUILD`` and
leaves the granular Forge controls to the user.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import uuid
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional

from loguru import logger
from sqlmodel import Session, select

from app.db.models import AutonomousNovelJob, JobStageAttempt, Project, StorylineCandidate
from app.schemas.project import ProjectCreate
from app.services import project_service
from app.services.autonomous import architecture as arch_mod
from app.services.autonomous import audit as audit_mod
from app.services.autonomous import chapter_loop
from app.services.autonomous import chapter_plan
from app.services.autonomous import export as export_mod
from app.services.autonomous import failures as fail
from app.services.autonomous import source_stages as src
from app.services.autonomous import storylines as story_mod
from app.services.autonomous.model_client import InvocationRecorder, LLMModelClient, ModelClient
from app.services.forge import transfer

STAGES: List[str] = [
    "INGEST", "SOURCE_ANALYSIS", "ANALYSIS_VERIFICATION", "BOOK_STRUCTURE", "FINGERPRINT_BUILD", "EXAMPLE_LIBRARY_BUILD",
    "STORYLINE_GENERATION", "STORYLINE_SELECTION", "NOVEL_ARCHITECTURE", "BIBLE_BUILD", "CHAPTER_PLAN_BUILD", "NOVEL_PREFLIGHT",
    "CHAPTER_GENERATION_LOOP", "WHOLE_NOVEL_AUDIT", "GLOBAL_REPAIR", "EXPORT", "DONE",
]
# Progress weight of each stage (sums to 100); the chapter loop interpolates within its weight.
STAGE_WEIGHTS: Dict[str, float] = {
    "INGEST": 2, "SOURCE_ANALYSIS": 14, "ANALYSIS_VERIFICATION": 1, "BOOK_STRUCTURE": 6, "FINGERPRINT_BUILD": 1, "EXAMPLE_LIBRARY_BUILD": 1,
    "STORYLINE_GENERATION": 5, "STORYLINE_SELECTION": 0, "NOVEL_ARCHITECTURE": 4, "BIBLE_BUILD": 1, "CHAPTER_PLAN_BUILD": 5, "NOVEL_PREFLIGHT": 0,
    "CHAPTER_GENERATION_LOOP": 52, "WHOLE_NOVEL_AUDIT": 2, "GLOBAL_REPAIR": 4, "EXPORT": 2, "DONE": 0,
}
MODES = ("fully_automatic", "approval_gates", "manual")
GATES = {"fully_automatic": {"STORYLINE_SELECTION"}, "approval_gates": {"STORYLINE_SELECTION", "NOVEL_PREFLIGHT", "EXPORT"}, "manual": {"STORYLINE_GENERATION", "STORYLINE_SELECTION", "NOVEL_PREFLIGHT", "EXPORT"}}
LEASE_SECONDS = 300
TERMINAL = ("completed", "failed", "cancelled")

ClientFactory = Callable[[Session, AutonomousNovelJob, InvocationRecorder], ModelClient]


def default_client_factory(session: Session, job: AutonomousNovelJob, recorder: InvocationRecorder) -> ModelClient:
    return LLMModelClient(session, default_llm_config_id=job.llm_config_id, role_llm_config_ids=job.role_llm_config_ids or {}, fallback_llm_config_id=(job.options or {}).get("fallback_llm_config_id"), recorder=recorder)


def progress_percent(job: AutonomousNovelJob, within: float = 0.0) -> float:
    done = 0.0
    for s in STAGES:
        if s == job.stage:
            done += STAGE_WEIGHTS.get(s, 0) * max(0.0, min(1.0, within))
            break
        done += STAGE_WEIGHTS.get(s, 0)
    return round(min(100.0, done), 1)


def job_dict(session: Session, job: AutonomousNovelJob) -> Dict[str, Any]:
    attempts = session.exec(select(JobStageAttempt).where(JobStageAttempt.job_id == job.id).order_by(JobStageAttempt.id.desc()).limit(30)).all()
    return {
        "id": job.id, "status": job.status, "stage": job.stage, "mode": job.mode, "source_project_id": job.source_project_id, "original_project_id": job.original_project_id,
        "llm_config_id": job.llm_config_id, "source_filename": job.source_filename, "options": job.options, "selected_storyline_id": job.selected_storyline_id, "chapter_count": job.chapter_count,
        "chapters_committed": job.chapters_committed, "progress_percent": job.progress_percent, "progress_message": job.progress_message, "stage_results": job.stage_results, "warnings": job.warnings,
        "error": job.error, "model_calls": job.model_calls, "input_tokens": job.input_tokens, "output_tokens": job.output_tokens, "waiting_for": waiting_for(job),
        "created_at": job.created_at.isoformat() if job.created_at else None, "updated_at": job.updated_at.isoformat() if job.updated_at else None, "started_at": job.started_at.isoformat() if job.started_at else None, "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "attempts": [{"stage": a.stage, "attempt": a.attempt, "status": a.status, "failure_category": a.failure_category, "recovery_action": a.recovery_action, "started_at": a.started_at.isoformat(), "finished_at": a.finished_at.isoformat() if a.finished_at else None} for a in attempts],
        "stages": STAGES,
    }


def waiting_for(job: AutonomousNovelJob) -> Optional[str]:
    if job.status != "waiting_for_user":
        return None
    return {"STORYLINE_SELECTION": "storyline_selection", "NOVEL_PREFLIGHT": "plan_approval", "EXPORT": "manuscript_approval", "STORYLINE_GENERATION": "manual_mode"}.get(job.stage, "approval")


class JobRunner:
    """Executes stages for one job. One runner instance per job process."""

    def __init__(self, session: Session, job_id: int, *, client_factory: ClientFactory = default_client_factory, owner: Optional[str] = None):
        self.session = session
        self.job_id = job_id
        self.client_factory = client_factory
        self.owner = owner or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"

    # ---------------------------------------------------------------- utils
    @property
    def job(self) -> AutonomousNovelJob:
        job = self.session.get(AutonomousNovelJob, self.job_id)
        if job is None:
            raise RuntimeError(f"Job {self.job_id} not found")
        return job

    def _save(self, job: AutonomousNovelJob, **fields: Any) -> None:
        for k, val in fields.items():
            setattr(job, k, val)
        job.updated_at = datetime.now()
        job.heartbeat_at = datetime.now()
        self.session.add(job)
        self.session.commit()

    def _progress(self, job: AutonomousNovelJob, message: str, within: float) -> None:
        job.progress_message = message[:400]
        job.progress_percent = progress_percent(job, within)
        job.heartbeat_at = datetime.now()
        job.lease_expires_at = datetime.now() + timedelta(seconds=LEASE_SECONDS)
        self.session.add(job)
        self.session.commit()

    def _acquire(self, job: AutonomousNovelJob) -> bool:
        now = datetime.now()
        if job.lease_owner and job.lease_owner != self.owner and job.lease_expires_at and job.lease_expires_at > now and job.status == "running":
            return False
        self._save(job, lease_owner=self.owner, lease_expires_at=now + timedelta(seconds=LEASE_SECONDS))
        return True

    def _client(self, job: AutonomousNovelJob) -> tuple[ModelClient, InvocationRecorder]:
        recorder = InvocationRecorder(self.session, job_id=job.id, project_id=job.original_project_id or job.source_project_id)
        return self.client_factory(self.session, job, recorder), recorder

    def _source_ctx(self, job: AutonomousNovelJob, client: ModelClient) -> src.SourceContext:
        opts = job.options or {}
        return src.SourceContext(source_project_id=int(job.source_project_id), filename=job.source_filename, data=job.source_bytes or b"", client=client, options=opts, progress=lambda m, p: self._progress(self.job, m, p), analysis_concurrency=int(opts.get("analysis_concurrency") or 4), window_size=int(opts.get("window_size") or 40), max_stage_count=int(opts.get("max_stage_count") or 24))

    def _storyline(self, job: AutonomousNovelJob) -> Dict[str, Any]:
        row = self.session.get(StorylineCandidate, int(job.selected_storyline_id or 0))
        if row is None:
            raise fail.StageFailure(fail.USER_INPUT_REQUIRED, "No storyline selected")
        return dict(row.content or {})

    def _preferences(self, job: AutonomousNovelJob) -> Dict[str, Any]:
        opts = job.options or {}
        return {k: opts.get(k) for k in ("genre_intensity", "content_rating", "ending_preference", "romance_level", "genre", "notes") if opts.get(k)}

    # --------------------------------------------------------------- stages
    async def _run_stage(self, job: AutonomousNovelJob, stage: str) -> Dict[str, Any]:
        client, recorder = self._client(job)
        opts = job.options or {}
        if stage == "INGEST":
            if not job.source_project_id:
                name = f"Reference · {job.source_filename or 'manuscript'} · {job.source_file_hash[:8]}"
                existing = self.session.exec(select(Project).where(Project.name == name)).first()
                if existing is None:
                    existing, _ = project_service.create_project(self.session, ProjectCreate(name=name, description="Reference manuscript analysed by the autonomous novel pipeline", template=None))
                self._save(job, source_project_id=existing.id)
            return src.stage_ingest(self.session, self._source_ctx(job, client))
        if stage == "SOURCE_ANALYSIS":
            return await src.stage_source_analysis(self.session, self._source_ctx(job, client))
        if stage == "ANALYSIS_VERIFICATION":
            return src.stage_analysis_verification(self.session, self._source_ctx(job, client))
        if stage == "BOOK_STRUCTURE":
            return await src.stage_book_structure(self.session, self._source_ctx(job, client))
        if stage == "FINGERPRINT_BUILD":
            return src.stage_fingerprint(self.session, self._source_ctx(job, client))
        if stage == "EXAMPLE_LIBRARY_BUILD":
            return src.stage_example_library(self.session, self._source_ctx(job, client))
        if stage == "STORYLINE_GENERATION":
            return await story_mod.stage_storyline_generation(self.session, job_id=job.id, source_project_id=int(job.source_project_id), client=client, preferences=self._preferences(job), count=int(opts.get("storyline_count") or story_mod.TARGET_OPTIONS))
        if stage == "STORYLINE_SELECTION":
            if not job.selected_storyline_id or not job.chapter_count:
                raise fail.StageFailure(fail.USER_INPUT_REQUIRED, "Select a storyline and a chapter count")
            return {"selected_storyline_id": job.selected_storyline_id, "chapter_count": job.chapter_count}
        if stage == "NOVEL_ARCHITECTURE":
            if not job.original_project_id:
                storyline = self._storyline(job)
                name = str(opts.get("title") or storyline.get("title") or "Original Novel")[:80] + f" · {job.source_file_hash[:6]}-{job.id}"
                result = transfer.create_original_project(self.session, source_project_id=int(job.source_project_id), name=name, description=str(storyline.get("hook") or ""), template=None)
                self._save(job, original_project_id=result.project_id)
            profile = story_mod.source_profile(self.session, int(job.source_project_id))
            return await arch_mod.stage_novel_architecture(self.session, original_project_id=int(job.original_project_id), source_project_id=int(job.source_project_id), storyline=self._storyline(job), chapter_count=job.chapter_count, client=client, brief=story_mod.source_brief(self.session, int(job.source_project_id), preferences=self._preferences(job)), preferences=self._preferences(job), profile=profile)
        if stage == "BIBLE_BUILD":
            return arch_mod.stage_bible_build(self.session, original_project_id=int(job.original_project_id), chapter_count=job.chapter_count, storyline=self._storyline(job))
        if stage == "CHAPTER_PLAN_BUILD":
            return await chapter_plan.stage_chapter_plan(self.session, project_id=int(job.original_project_id), chapter_count=job.chapter_count, client=client, options=opts, progress=lambda m, p: self._progress(self.job, m, p))
        if stage == "NOVEL_PREFLIGHT":
            return chapter_plan.stage_preflight(self.session, project_id=int(job.original_project_id), chapter_count=job.chapter_count)
        if stage == "CHAPTER_GENERATION_LOOP":
            word_target = chapter_plan.words_per_chapter(opts, job.chapter_count)
            return await chapter_loop.generate_next_chapter(self.session, project_id=int(job.original_project_id), chapter_count=job.chapter_count, client=client, options=opts, word_target=word_target)
        if stage == "WHOLE_NOVEL_AUDIT":
            return audit_mod.whole_novel_audit(self.session, int(job.original_project_id), job.chapter_count)
        if stage == "GLOBAL_REPAIR":
            audit = (job.stage_results or {}).get("WHOLE_NOVEL_AUDIT") or audit_mod.whole_novel_audit(self.session, int(job.original_project_id), job.chapter_count)
            return await audit_mod.global_repair(self.session, project_id=int(job.original_project_id), chapter_count=job.chapter_count, client=client, audit=audit)
        if stage == "EXPORT":
            audit = ((job.stage_results or {}).get("GLOBAL_REPAIR") or {}).get("audit") or (job.stage_results or {}).get("WHOLE_NOVEL_AUDIT") or {}
            return export_mod.stage_export(self.session, job=job, project_id=int(job.original_project_id), audit=audit)
        raise fail.StageFailure(fail.INTERNAL_ERROR, f"Unknown stage {stage}")

    def _recover(self, job: AutonomousNovelJob, stage: str, exc: fail.StageFailure, attempt_no: int) -> str:
        """Apply the recovery ladder; return the action taken."""
        policy = fail.POLICIES[exc.category]
        action = policy.action_for(attempt_no)
        if action == fail.REANALYZE_UNIT and stage in ("ANALYSIS_VERIFICATION", "FINGERPRINT_BUILD") and job.source_project_id:
            targets = list((exc.detail or {}).get("failed_chapters") or []) + list((exc.detail or {}).get("low_confidence") or [])
            if targets:
                src.mark_chapters_for_reanalysis(self.session, int(job.source_project_id), targets)
                job.stage = "SOURCE_ANALYSIS"
        elif action == fail.REDUCE_SCOPE and stage == "SOURCE_ANALYSIS":
            job.options = {**(job.options or {}), "analysis_concurrency": 1}
        elif action == fail.REDUCE_SCOPE and stage == "CHAPTER_GENERATION_LOOP":
            job.options = {**(job.options or {}), "max_repairs": int((job.options or {}).get("max_repairs") or 2) + 1}
        elif action == fail.REBUILD_DOWNSTREAM and stage in ("NOVEL_PREFLIGHT", "CHAPTER_GENERATION_LOOP"):
            job.stage = "CHAPTER_PLAN_BUILD"
        elif action == fail.REPAIR_ARTIFACT and stage == "CHAPTER_GENERATION_LOOP" and exc.category == fail.CONTINUITY_VIOLATION:
            job.stage = "CHAPTER_PLAN_BUILD"  # replan from the failing chapter with the committed state
        elif action == fail.REPAIR_ARTIFACT and stage == "STORYLINE_GENERATION":
            pass  # the stage itself re-ideates with the rejects as anti-examples
        return action

    # ------------------------------------------------------------------ run
    async def step(self) -> AutonomousNovelJob:
        """Execute exactly one stage transition (one chapter for the loop). Returns the refreshed job."""
        job = self.job
        if job.status in TERMINAL or job.status == "waiting_for_user":
            return job
        if not self._acquire(job):
            raise RuntimeError(f"Job {job.id} is leased by {job.lease_owner}")
        stage = job.stage
        if stage == "DONE":
            self._save(job, status="completed", finished_at=datetime.now(), progress_percent=100.0, progress_message="Finished")
            return job
        gates = GATES.get(job.mode, GATES["fully_automatic"])
        if stage in gates and not (job.stage_results or {}).get(f"approved:{stage}"):
            if stage == "STORYLINE_SELECTION" and job.selected_storyline_id and job.chapter_count:
                pass  # selection already made (e.g. resume)
            else:
                self._save(job, status="waiting_for_user", progress_message={"STORYLINE_SELECTION": "Choose a storyline and chapter count", "NOVEL_PREFLIGHT": "Review the novel plan", "EXPORT": "Review the finished manuscript", "STORYLINE_GENERATION": "Manual mode: use the Forge controls to continue"}.get(stage, "Waiting for approval"))
                return job
        attempts = self.session.exec(select(JobStageAttempt).where(JobStageAttempt.job_id == job.id, JobStageAttempt.stage == stage, JobStageAttempt.status == "failed")).all()
        attempt_no = len(attempts) + 1
        attempt = JobStageAttempt(job_id=job.id, stage=stage, attempt=attempt_no, status="running")
        self.session.add(attempt)
        self._save(job, status="running", started_at=job.started_at or datetime.now(), error=None, progress_message=f"{stage.replace('_', ' ').title()}…", progress_percent=progress_percent(job))
        attempt_id = attempt.id
        try:
            result = await self._run_stage(job, stage)
            job = self.job
            results = dict(job.stage_results or {})
            if stage == "CHAPTER_GENERATION_LOOP":
                loop = dict(results.get(stage) or {})
                if result.get("chapter"):
                    loop[str(result["chapter"])] = {k: v for k, v in result.items() if k not in ("complete",)}
                results[stage] = loop
                job.chapters_committed = int(result.get("chapter") or job.chapters_committed)
                if result.get("deviation", {}).get("accepted_warnings"):
                    job.warnings = (job.warnings or []) + [{"stage": stage, "chapter": result["chapter"], "warnings": result["deviation"]["accepted_warnings"]}]
                next_stage = STAGES[STAGES.index(stage) + 1] if result.get("complete") else stage
                within = job.chapters_committed / max(1, job.chapter_count)
            else:
                results[stage] = result
                next_stage = STAGES[STAGES.index(stage) + 1]
                within = 1.0
                if stage == "WHOLE_NOVEL_AUDIT" and result.get("passed"):
                    next_stage = "EXPORT"  # nothing to repair
            attempt = self.session.get(JobStageAttempt, attempt_id) or attempt
            attempt.status = "succeeded"
            attempt.finished_at = datetime.now()
            attempt.detail = {"summary": {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))}}
            self.session.add(attempt)
            job.stage_results = results
            job.stage = next_stage
            self._refresh_usage(job)
            job.progress_percent = progress_percent(job, 0.0 if next_stage != stage else within)
            self._save(job, status="queued" if next_stage != "DONE" else "running")
            if next_stage == "DONE":
                self._save(job, status="completed", finished_at=datetime.now(), progress_percent=100.0, progress_message="Finished")
            return job
        except asyncio.CancelledError:
            self.session.rollback()
            attempt = self.session.get(JobStageAttempt, attempt_id)
            if attempt is not None:
                attempt.status = "paused"
                attempt.finished_at = datetime.now()
                self.session.add(attempt)
            self._save(self.job, status="paused", progress_message="Cancelled by request")
            raise
        except Exception as exc:  # noqa: BLE001 - classified into the ladder
            self.session.rollback()
            job = self.job
            failure = exc if isinstance(exc, fail.StageFailure) else fail.StageFailure(fail.classify_exception(exc), f"{type(exc).__name__}: {exc}")
            logger.warning(f"[Autonomous] job {job.id} stage {stage} attempt {attempt_no} failed ({failure.category}): {failure}")
            attempt = self.session.get(JobStageAttempt, attempt_id) or JobStageAttempt(job_id=job.id, stage=stage, attempt=attempt_no)
            attempt.status = "failed"
            attempt.failure_category = failure.category
            attempt.detail = failure.as_dict()
            attempt.finished_at = datetime.now()
            if failure.category == fail.USER_INPUT_REQUIRED:
                attempt.recovery_action = fail.PAUSE
                self.session.add(attempt)
                self._save(job, status="waiting_for_user", error=None, progress_message=str(failure))
                return job
            action = self._recover(job, stage, failure, attempt_no)
            attempt.recovery_action = action
            self.session.add(attempt)
            self._refresh_usage(job)
            if action == fail.PAUSE:
                self._save(job, status="paused", error=failure.as_dict(), progress_message=f"Paused after {attempt_no} attempt(s) at {stage}: {failure}")
            else:
                job.warnings = (job.warnings or []) + [{"stage": stage, "attempt": attempt_no, "category": failure.category, "message": str(failure)[:300], "recovery": action}]
                self._save(job, status="queued", error=None, progress_message=f"Recovering from {failure.category} at {stage} ({action})")
                backoff = fail.POLICIES[failure.category].backoff_seconds
                if backoff:
                    await asyncio.sleep(backoff * attempt_no)
            return job

    def _refresh_usage(self, job: AutonomousNovelJob) -> None:
        from app.db.models import ModelInvocation

        rows = self.session.exec(select(ModelInvocation).where(ModelInvocation.job_id == job.id)).all()
        job.model_calls = len(rows)
        job.input_tokens = sum(int(r.input_tokens or r.input_tokens_estimate or 0) for r in rows)
        job.output_tokens = sum(int(r.output_tokens or 0) for r in rows)

    async def run(self, *, max_steps: int = 10_000, until_stage: Optional[str] = None) -> AutonomousNovelJob:
        """Run stages until the job waits for the user, pauses, completes, or reaches ``until_stage``."""
        job = self.job
        for _ in range(max_steps):
            job = await self.step()
            if job.status in TERMINAL or job.status in ("waiting_for_user", "paused"):
                break
            if until_stage and job.stage == until_stage:
                break
        return job


# ----------------------------------------------------------------- service

def create_job(session: Session, *, filename: str, data: bytes, llm_config_id: int, mode: str = "fully_automatic", options: Optional[Dict[str, Any]] = None, role_llm_config_ids: Optional[Dict[str, int]] = None) -> AutonomousNovelJob:
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    file_hash = hashlib.sha256(data).hexdigest()
    key = hashlib.sha256(f"{file_hash}|{llm_config_id}|{mode}|{json.dumps(options or {}, sort_keys=True)}".encode()).hexdigest()[:32]
    existing = session.exec(select(AutonomousNovelJob).where(AutonomousNovelJob.idempotency_key == key, AutonomousNovelJob.status.notin_(list(TERMINAL)))).first()
    if existing:
        return existing
    job = AutonomousNovelJob(idempotency_key=key, status="queued", stage="INGEST", mode=mode, llm_config_id=int(llm_config_id), role_llm_config_ids=role_llm_config_ids or {}, source_filename=filename, source_file_hash=file_hash, source_bytes=data, options=options or {})
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def select_storyline(session: Session, job: AutonomousNovelJob, *, storyline_id: int, chapter_count: int, options: Optional[Dict[str, Any]] = None) -> AutonomousNovelJob:
    if job.stage not in ("STORYLINE_SELECTION",) or job.status not in ("waiting_for_user", "queued", "paused"):
        raise ValueError(f"Job is at stage {job.stage} ({job.status}); storyline selection is not pending")
    row = session.get(StorylineCandidate, int(storyline_id))
    if row is None or row.job_id != job.id:
        raise ValueError("Storyline does not belong to this job")
    if row.rejected:
        raise ValueError(f"Storyline was rejected: {row.rejection_reason}")
    if chapter_count < 1 or chapter_count > 400:
        raise ValueError("chapter_count must be between 1 and 400")
    for other in session.exec(select(StorylineCandidate).where(StorylineCandidate.job_id == job.id)).all():
        other.selected = other.id == row.id
        session.add(other)
    job.selected_storyline_id = row.id
    job.chapter_count = int(chapter_count)
    job.options = {**(job.options or {}), **(options or {})}
    job.stage_results = {**(job.stage_results or {}), "approved:STORYLINE_SELECTION": True}
    job.status = "queued"
    job.error = None
    job.updated_at = datetime.now()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def approve(session: Session, job: AutonomousNovelJob) -> AutonomousNovelJob:
    if job.status != "waiting_for_user":
        raise ValueError("Job is not waiting for approval")
    job.stage_results = {**(job.stage_results or {}), f"approved:{job.stage}": True}
    job.status = "queued"
    job.updated_at = datetime.now()
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def request_pause(session: Session, job: AutonomousNovelJob) -> AutonomousNovelJob:
    if job.status in TERMINAL:
        return job
    job.status = "paused"
    job.progress_message = "Paused by user"
    job.updated_at = datetime.now()
    session.add(job)
    session.commit()
    return job


def resume(session: Session, job: AutonomousNovelJob) -> AutonomousNovelJob:
    if job.status in TERMINAL:
        raise ValueError(f"Job is {job.status}")
    job.status = "queued"
    job.error = None
    job.lease_owner = None
    job.updated_at = datetime.now()
    session.add(job)
    session.commit()
    return job


def cancel(session: Session, job: AutonomousNovelJob) -> AutonomousNovelJob:
    job.status = "cancelled"
    job.finished_at = datetime.now()
    job.updated_at = datetime.now()
    session.add(job)
    session.commit()
    return job


def recover_stale_leases(session: Session) -> int:
    """On startup: jobs left 'running' by a dead process go back to 'queued' (their stage is durable)."""
    n = 0
    for job in session.exec(select(AutonomousNovelJob).where(AutonomousNovelJob.status == "running")).all():
        job.status = "queued"
        job.lease_owner = None
        job.progress_message = "Recovered after restart"
        session.add(job)
        n += 1
    session.commit()
    return n


__all__ = ["GATES", "JobRunner", "LEASE_SECONDS", "MODES", "STAGES", "STAGE_WEIGHTS", "TERMINAL", "approve", "cancel", "create_job", "default_client_factory", "job_dict", "progress_percent", "recover_stale_leases", "request_pause", "resume", "select_storyline", "waiting_for"]
