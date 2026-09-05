"""Executable recovery ladder: action -> handler -> structured, persisted result.

Every handler receives the failure context and mutates the job (options, stage,
artifacts) so the *next* attempt behaves differently. Each execution is stored
as a ``RecoveryAction`` row with parameters before/after, models, invalidations
and outcome. Handlers never call a provider themselves; they change what the
next stage attempt will do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from loguru import logger
from sqlmodel import Session, select

from app.db.models import AutonomousNovelJob, LLMConfig, RecoveryAction
from app.services.autonomous import failures as fail


@dataclass
class RecoveryContext:
    session: Session
    job: AutonomousNovelJob
    stage: str
    stage_attempt: int
    failure: fail.StageFailure
    action: str


@dataclass
class RecoveryOutcome:
    action: str
    success: bool
    reason: str
    parameters_before: Dict[str, Any] = field(default_factory=dict)
    parameters_after: Dict[str, Any] = field(default_factory=dict)
    next_stage: Optional[str] = None
    input_artifact: Optional[str] = None
    output_artifact: Optional[str] = None
    original_model: str = ""
    selected_model: str = ""
    downstream_invalidations: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)
    pause: bool = False
    backoff_seconds: float = 0.0


Handler = Callable[[RecoveryContext], RecoveryOutcome]
HANDLERS: Dict[str, Handler] = {}


def register(action: str) -> Callable[[Handler], Handler]:
    def deco(fn: Handler) -> Handler:
        HANDLERS[action] = fn
        return fn
    return deco


def _opts(ctx: RecoveryContext) -> Dict[str, Any]:
    return dict(ctx.job.options or {})


def _model_name(session: Session, cid: Optional[int]) -> str:
    cfg = session.get(LLMConfig, int(cid)) if cid else None
    return cfg.model_name if cfg else ""


@register(fail.RETRY)
def _retry(ctx: RecoveryContext) -> RecoveryOutcome:
    backoff = fail.POLICIES[ctx.failure.category].backoff_seconds * ctx.stage_attempt
    return RecoveryOutcome(fail.RETRY, True, "re-run the stage with identical parameters; artifacts are idempotent", backoff_seconds=backoff, parameters_before=_opts(ctx), parameters_after=_opts(ctx))


@register(fail.RETRY_CLARIFIED)
def _retry_clarified(ctx: RecoveryContext) -> RecoveryOutcome:
    before = _opts(ctx)
    after = {**before, "schema_feedback": True, "schema_feedback_errors": str(ctx.failure.detail.get("problems") or ctx.failure)[:400]}
    ctx.job.options = after
    return RecoveryOutcome(fail.RETRY_CLARIFIED, True, "next attempt appends concise schema-validation feedback to the prompt", parameters_before=before, parameters_after=after, detail={"validation": ctx.failure.detail})


@register(fail.REANALYZE_UNIT)
def _reanalyze(ctx: RecoveryContext) -> RecoveryOutcome:
    from app.services.autonomous import source_stages as src

    targets = sorted({int(x) for x in (list(ctx.failure.detail.get("failed_chapters") or []) + list(ctx.failure.detail.get("low_confidence") or []))})
    if not targets or not ctx.job.source_project_id:
        return RecoveryOutcome(fail.REANALYZE_UNIT, False, "no failed/low-confidence units identified; falling through to retry")
    n = src.mark_chapters_for_reanalysis(ctx.session, int(ctx.job.source_project_id), targets)
    return RecoveryOutcome(fail.REANALYZE_UNIT, n > 0, f"re-analyse only chapters {targets[:20]}", next_stage="SOURCE_ANALYSIS", input_artifact=f"chapters:{targets[:20]}", downstream_invalidations=[f"Chapter Analysis ch{t}" for t in targets[:50]], detail={"chapters": targets})


@register(fail.INDEPENDENT_VERIFY)
def _verify(ctx: RecoveryContext) -> RecoveryOutcome:
    before = _opts(ctx)
    roles = dict(ctx.job.role_llm_config_ids or {})
    verifier_cid = roles.get("independent_verifier") or before.get("fallback_llm_config_id") or ctx.job.llm_config_id
    after = {**before, "independent_verification": True, "verifier_llm_config_id": int(verifier_cid)}
    ctx.job.options = after
    distinct = int(verifier_cid) != int(ctx.job.llm_config_id)
    return RecoveryOutcome(fail.INDEPENDENT_VERIFY, True, "next attempt runs an independent verifier pass over the disputed claims" + ("" if distinct else " (same model, distinct role/prompt: no separate verifier model configured)"), parameters_before=before, parameters_after=after, original_model=_model_name(ctx.session, ctx.job.llm_config_id), selected_model=_model_name(ctx.session, verifier_cid), detail={"distinct_model": distinct})


@register(fail.REPAIR_ARTIFACT)
def _repair(ctx: RecoveryContext) -> RecoveryOutcome:
    from app.services.forge import provenance

    if ctx.stage == "CHAPTER_GENERATION_LOOP":
        manifest = provenance.get_manifest(ctx.session, int(ctx.job.original_project_id), create=True)
        failing = int(manifest.latest_committed_chapter) + 1
        return RecoveryOutcome(fail.REPAIR_ARTIFACT, True, f"replan chapter {failing}+ from the committed state, then redraft", next_stage="CHAPTER_PLAN_BUILD", input_artifact=f"Chapter Outline ch{failing}", output_artifact=f"Chapter Outline ch{failing}+ (new revision)", downstream_invalidations=[f"chapter_context ch{failing}+"], detail={"from_chapter": failing})
    if ctx.stage in ("NOVEL_ARCHITECTURE", "CHAPTER_PLAN_BUILD", "STORYLINE_GENERATION", "BIBLE_BUILD"):
        return RecoveryOutcome(fail.REPAIR_ARTIFACT, True, f"{ctx.stage} repairs its own artifact on the next attempt using the recorded problems", input_artifact=ctx.stage, output_artifact=f"{ctx.stage} (new revision)", detail={"problems": (ctx.failure.detail or {}).get("problems", [])[:20]})
    if ctx.stage == "GLOBAL_REPAIR":
        return RecoveryOutcome(fail.REPAIR_ARTIFACT, True, "re-run global repair over the remaining blocking findings")
    return RecoveryOutcome(fail.REPAIR_ARTIFACT, False, f"no artifact repair defined for stage {ctx.stage}; retrying")


@register(fail.REBUILD_DOWNSTREAM)
def _rebuild(ctx: RecoveryContext) -> RecoveryOutcome:
    from app.services.forge import provenance

    if ctx.stage in ("NOVEL_PREFLIGHT", "CHAPTER_GENERATION_LOOP", "WHOLE_NOVEL_AUDIT", "GLOBAL_REPAIR", "EXPORT"):
        stale = provenance.stale_artifacts(ctx.session, int(ctx.job.original_project_id)) if ctx.job.original_project_id else []
        kinds = sorted({s["artifact_kind"] for s in stale})
        target = "CHAPTER_PLAN_BUILD" if any(k in ("Chapter Outline", "chapter_context") for k in kinds) or not kinds else "BIBLE_BUILD"
        return RecoveryOutcome(fail.REBUILD_DOWNSTREAM, True, f"rebuild stale downstream artifacts from {target}", next_stage=target, downstream_invalidations=[f"{s['artifact_kind']}:{s['artifact_key']}" for s in stale[:50]], detail={"stale_kinds": kinds})
    if ctx.stage in ("FINGERPRINT_BUILD", "EXAMPLE_LIBRARY_BUILD", "STORYLINE_GENERATION"):
        return RecoveryOutcome(fail.REBUILD_DOWNSTREAM, True, "rebuild from analysis verification", next_stage="ANALYSIS_VERIFICATION")
    return RecoveryOutcome(fail.REBUILD_DOWNSTREAM, False, "nothing stale to rebuild")


@register(fail.REDUCE_SCOPE)
def _reduce(ctx: RecoveryContext) -> RecoveryOutcome:
    before = _opts(ctx)
    after = dict(before)
    if ctx.stage == "SOURCE_ANALYSIS":
        after["analysis_concurrency"] = max(1, int(before.get("analysis_concurrency") or 4) // 2)
    elif ctx.stage == "BOOK_STRUCTURE":
        after["window_size"] = max(8, int(before.get("window_size") or 40) // 2)
    elif ctx.stage == "CHAPTER_PLAN_BUILD":
        after["plan_window"] = max(2, int(before.get("plan_window") or 8) // 2)
    elif ctx.stage == "CHAPTER_GENERATION_LOOP":
        after["budget_chars"] = max(6000, int(before.get("budget_chars") or 16000) * 3 // 4)
        after["max_repairs"] = int(before.get("max_repairs") or 2) + 1
    elif ctx.stage in ("STORYLINE_GENERATION",):
        after["storyline_count"] = max(5, int(before.get("storyline_count") or 7) - 1)
    else:
        after["context_budget_scale"] = round(float(before.get("context_budget_scale") or 1.0) * 0.75, 2)
    if ctx.failure.category == fail.TOKEN_OVERFLOW:
        after["max_tokens_scale"] = round(float(before.get("max_tokens_scale") or 1.0) * 0.75, 2)
    ctx.job.options = after
    changed = {k: (before.get(k), after.get(k)) for k in after if before.get(k) != after.get(k)}
    return RecoveryOutcome(fail.REDUCE_SCOPE, bool(changed), f"measurable scope reduction: {changed}", parameters_before=before, parameters_after=after, detail={"changed": {k: list(v) for k, v in changed.items()}})


@register(fail.FALLBACK_MODEL)
def _fallback(ctx: RecoveryContext) -> RecoveryOutcome:
    before = _opts(ctx)
    fallback = before.get("fallback_llm_config_id")
    if not fallback or int(fallback) == int(ctx.job.llm_config_id):
        return RecoveryOutcome(fail.FALLBACK_MODEL, False, "no distinct fallback model configured", original_model=_model_name(ctx.session, ctx.job.llm_config_id))
    if ctx.session.get(LLMConfig, int(fallback)) is None:
        return RecoveryOutcome(fail.FALLBACK_MODEL, False, f"fallback LLM configuration {fallback} does not exist")
    after = {**before, "force_fallback": True}
    ctx.job.options = after
    return RecoveryOutcome(fail.FALLBACK_MODEL, True, "next attempt routes every role to the fallback model; usage is charged to the same job budget", parameters_before=before, parameters_after=after, original_model=_model_name(ctx.session, ctx.job.llm_config_id), selected_model=_model_name(ctx.session, fallback))


@register(fail.PAUSE)
def _pause(ctx: RecoveryContext) -> RecoveryOutcome:
    what = "retry budget" if ctx.failure.category != fail.BUDGET_EXCEEDED else "job budget"
    return RecoveryOutcome(fail.PAUSE, True, f"{what} exhausted at {ctx.stage}: job paused in a resumable state; no further provider calls", pause=True, detail=ctx.failure.detail or {})


def execute(ctx: RecoveryContext) -> RecoveryOutcome:
    """Run the handler for ``ctx.action``, persist a ``RecoveryAction`` row, return the outcome."""
    handler = HANDLERS.get(ctx.action) or HANDLERS[fail.RETRY]
    row = RecoveryAction(job_id=int(ctx.job.id), stage=ctx.stage, stage_attempt=ctx.stage_attempt, failure_category=ctx.failure.category, action=ctx.action, reason=str(ctx.failure)[:500], parameters_before=_opts(ctx), original_model=_model_name(ctx.session, ctx.job.llm_config_id))
    try:
        out = handler(ctx)
    except Exception as exc:  # noqa: BLE001 - a broken handler must not mask the original failure
        logger.exception(f"[Autonomous] recovery handler {ctx.action} crashed for job {ctx.job.id}")
        out = RecoveryOutcome(ctx.action, False, f"handler crashed: {type(exc).__name__}")
    if not out.success and ctx.action not in (fail.PAUSE, fail.RETRY):
        # Fall through to a plain retry so the ladder never dead-ends on an inapplicable rung.
        out.reason += " -> retrying"
    row.reason = out.reason[:500]
    row.parameters_after = out.parameters_after or _opts(ctx)
    row.input_artifact = out.input_artifact
    row.output_artifact = out.output_artifact
    row.selected_model = out.selected_model
    row.downstream_invalidations = out.downstream_invalidations
    row.success = out.success
    row.detail = out.detail
    row.finished_at = datetime.now()
    ctx.session.add(row)
    if out.next_stage:
        ctx.job.stage = out.next_stage
    return out


def history(session: Session, job_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    rows = session.exec(select(RecoveryAction).where(RecoveryAction.job_id == job_id).order_by(RecoveryAction.id.desc()).limit(limit)).all()
    return [{"id": r.id, "stage": r.stage, "stage_attempt": r.stage_attempt, "failure_category": r.failure_category, "action": r.action, "reason": r.reason, "original_model": r.original_model, "selected_model": r.selected_model, "success": r.success, "parameters_changed": {k: v for k, v in (r.parameters_after or {}).items() if (r.parameters_before or {}).get(k) != v}, "downstream_invalidations": (r.downstream_invalidations or [])[:10], "started_at": r.started_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None} for r in rows]


__all__ = ["HANDLERS", "RecoveryContext", "RecoveryOutcome", "execute", "history", "register"]
