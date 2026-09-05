"""Job-level budget enforcement: reserve before a provider call, reconcile after.

Limits live in ``AutonomousNovelJob.budget`` (0 or missing = unlimited):
``max_calls``, ``max_input_tokens``, ``max_output_tokens``, ``max_total_tokens``,
``max_repair_calls``, ``max_cost_usd`` (only enforced when a price table is
configured), plus optional ``stage_limits: {stage: {max_calls}}`` and
``chapter_limits: {max_calls_per_chapter}``.

Reservation is a single conditional UPDATE on the job row (compare-and-set on
the reserved counters), so two concurrent workers cannot both fit under the
ceiling. ``reconcile`` releases the reservation and adds actual usage; retries,
verifiers and fallbacks each reserve/reconcile individually so nothing is
double-counted.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from sqlalchemy import update
from sqlmodel import Session, select

from app.core.config import settings
from app.db.models import AutonomousNovelJob, ModelInvocation
from app.services.autonomous import failures as fail

REPAIR_ROLES = ("repair_editor", "whole_novel_editor")


class BudgetExceeded(fail.StageFailure):
    def __init__(self, message: str, detail: Optional[Dict[str, Any]] = None):
        super().__init__(fail.BUDGET_EXCEEDED, message, detail=detail)


@dataclass
class Reservation:
    job_id: int
    calls: int
    tokens: int
    role: str
    stage: str
    released: bool = False


def effective_budget(job: AutonomousNovelJob) -> Dict[str, Any]:
    b = dict(job.budget or {})
    d = settings.autonomous
    b.setdefault("max_calls", d.default_max_calls)
    b.setdefault("max_total_tokens", d.default_max_total_tokens)
    b.setdefault("max_repair_calls", d.default_max_repair_calls)
    return b


def usage_snapshot(session: Session, job: AutonomousNovelJob) -> Dict[str, Any]:
    b = effective_budget(job)
    return {
        "calls": {"used": int(job.model_calls), "reserved": int(job.reserved_calls), "limit": int(b.get("max_calls") or 0)},
        "input_tokens": {"used": int(job.input_tokens), "limit": int(b.get("max_input_tokens") or 0)},
        "output_tokens": {"used": int(job.output_tokens), "limit": int(b.get("max_output_tokens") or 0)},
        "total_tokens": {"used": int(job.input_tokens) + int(job.output_tokens), "reserved": int(job.reserved_tokens), "limit": int(b.get("max_total_tokens") or 0)},
        "repair_calls": {"used": int(job.repair_calls), "limit": int(b.get("max_repair_calls") or 0)},
        "estimated_cost_usd": estimate_cost(job),
    }


def estimate_cost(job: AutonomousNovelJob) -> Optional[float]:
    """Cost only when the job budget carries an explicit price table; otherwise unknown (None)."""
    prices = (job.budget or {}).get("price_per_million") or {}
    if not prices:
        return None
    return round(int(job.input_tokens) / 1e6 * float(prices.get("input", 0)) + int(job.output_tokens) / 1e6 * float(prices.get("output", 0)), 6)


def reserve(session: Session, job_id: int, *, role: str, stage: str, estimated_tokens: int) -> Reservation:
    """Atomically reserve one call and ``estimated_tokens``; raises ``BudgetExceeded`` when a hard limit would be crossed."""
    job = session.get(AutonomousNovelJob, job_id)
    if job is None:
        raise fail.StageFailure(fail.INTERNAL_ERROR, f"job {job_id} not found")
    session.refresh(job)
    b = effective_budget(job)
    est = max(0, int(estimated_tokens))
    problems = []
    max_calls = int(b.get("max_calls") or 0)
    if max_calls and job.model_calls + job.reserved_calls + 1 > max_calls:
        problems.append(f"max_calls {max_calls} reached ({job.model_calls} used, {job.reserved_calls} reserved)")
    max_total = int(b.get("max_total_tokens") or 0)
    if max_total and job.input_tokens + job.output_tokens + job.reserved_tokens + est > max_total:
        problems.append(f"max_total_tokens {max_total} would be exceeded ({job.input_tokens + job.output_tokens} used, {job.reserved_tokens} reserved, {est} requested)")
    max_in = int(b.get("max_input_tokens") or 0)
    if max_in and job.input_tokens + est > max_in:
        problems.append(f"max_input_tokens {max_in} would be exceeded")
    max_out = int(b.get("max_output_tokens") or 0)
    if max_out and job.output_tokens >= max_out:
        problems.append(f"max_output_tokens {max_out} reached")
    max_repair = int(b.get("max_repair_calls") or 0)
    if max_repair and role in REPAIR_ROLES and job.repair_calls + 1 > max_repair:
        problems.append(f"max_repair_calls {max_repair} reached")
    stage_key = stage.split(":")[0]
    stage_limit = int(((b.get("stage_limits") or {}).get(stage_key) or {}).get("max_calls") or 0)
    if stage_limit:
        used = session.exec(select(ModelInvocation).where(ModelInvocation.job_id == job_id, ModelInvocation.stage.like(f"{stage_key}%"))).all()
        if len(used) + 1 > stage_limit:
            problems.append(f"stage {stage_key} max_calls {stage_limit} reached")
    per_chapter = int((b.get("chapter_limits") or {}).get("max_calls_per_chapter") or 0)
    if per_chapter and ":ch" in stage:
        used = session.exec(select(ModelInvocation).where(ModelInvocation.job_id == job_id, ModelInvocation.stage == stage)).all()
        if len(used) + 1 > per_chapter:
            problems.append(f"chapter limit {per_chapter} calls reached for {stage}")
    max_cost = float(b.get("max_cost_usd") or 0)
    cost = estimate_cost(job)
    if max_cost and cost is not None and cost >= max_cost:
        problems.append(f"max_cost_usd {max_cost} reached (estimated {cost})")
    if problems:
        raise BudgetExceeded("Budget exhausted: " + "; ".join(problems), detail={"usage": usage_snapshot(session, job), "role": role, "stage": stage})
    t = AutonomousNovelJob.__table__
    # CAS on the reserved counters so a concurrent reservation cannot slip under the ceiling.
    stmt = update(t).where(t.c.id == job_id, t.c.reserved_calls == job.reserved_calls, t.c.reserved_tokens == job.reserved_tokens).values(reserved_calls=t.c.reserved_calls + 1, reserved_tokens=t.c.reserved_tokens + est)
    if session.execute(stmt).rowcount != 1:
        session.rollback()
        return reserve(session, job_id, role=role, stage=stage, estimated_tokens=estimated_tokens)
    session.commit()
    return Reservation(job_id=job_id, calls=1, tokens=est, role=role, stage=stage)


def reconcile(session: Session, res: Reservation, *, input_tokens: int, output_tokens: int, succeeded: bool) -> None:
    """Release the reservation and add actual usage (a failed call still counts as a call and its tokens)."""
    if res.released:
        return
    t = AutonomousNovelJob.__table__
    values: Dict[str, Any] = {
        "reserved_calls": t.c.reserved_calls - res.calls, "reserved_tokens": t.c.reserved_tokens - res.tokens,
        "model_calls": t.c.model_calls + 1, "input_tokens": t.c.input_tokens + max(0, int(input_tokens)), "output_tokens": t.c.output_tokens + max(0, int(output_tokens)),
    }
    if res.role in REPAIR_ROLES:
        values["repair_calls"] = t.c.repair_calls + 1
    session.execute(update(t).where(t.c.id == res.job_id).values(**values))
    # Never let the reserved counters go negative after a crash/restart mismatch.
    session.execute(update(t).where(t.c.id == res.job_id, t.c.reserved_calls < 0).values(reserved_calls=0))
    session.execute(update(t).where(t.c.id == res.job_id, t.c.reserved_tokens < 0).values(reserved_tokens=0))
    session.commit()
    res.released = True


def clear_reservations(session: Session, job_id: int) -> None:
    """Startup recovery: reservations of a dead worker are abandoned; actual usage is already recorded."""
    t = AutonomousNovelJob.__table__
    session.execute(update(t).where(t.c.id == job_id).values(reserved_calls=0, reserved_tokens=0))
    session.commit()


__all__ = ["BudgetExceeded", "REPAIR_ROLES", "Reservation", "clear_reservations", "effective_budget", "estimate_cost", "reconcile", "reserve", "usage_snapshot"]
