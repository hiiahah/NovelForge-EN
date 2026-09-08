"""Atomic job leases, fencing and the independent heartbeat.

Durability contract: at-least-once stage execution with idempotent artifact
publication and fenced state-machine commits. Model requests may run more than
once after a network or process failure; job *state* is only ever advanced by
the worker holding the current lease generation.

Acquisition is a single conditional ``UPDATE ... WHERE`` (compare-and-set) that
succeeds only when the job is eligible and the lease is absent, expired or
already held by the caller. SQLite serialises writers, so ``rowcount == 1`` is
authoritative there as on PostgreSQL. Every publication re-checks
``(id, lease_owner, lease_generation)`` in the same conditional-UPDATE form; a
zero rowcount raises ``JobLeaseLost`` which the runner treats separately from
stage failures (it never consumes the stage's retry budget).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional

from loguru import logger
from sqlalchemy import update
from sqlmodel import Session

from app.core.config import settings
from app.db.models import AutonomousNovelJob

ELIGIBLE_STATUSES = ("queued", "running")


class JobLeaseLost(RuntimeError):
    """The caller no longer holds the job lease; nothing it did after losing it may be published."""

    def __init__(self, job_id: int, owner: str, generation: int, message: str = ""):
        super().__init__(message or f"job {job_id}: lease generation {generation} owned by {owner} is no longer current")
        self.job_id = job_id
        self.owner = owner
        self.generation = generation


@dataclass(frozen=True)
class Lease:
    job_id: int
    owner: str
    generation: int
    expires_at: datetime


def lease_seconds() -> int:
    return max(10, int(settings.autonomous.lease_seconds))


def heartbeat_seconds() -> int:
    return max(1, min(int(settings.autonomous.heartbeat_seconds), lease_seconds() // 2))


def acquire(session: Session, job_id: int, owner: str, *, now: Optional[datetime] = None, ttl: Optional[int] = None) -> Optional[Lease]:
    """Compare-and-set acquisition. Returns the lease (with its new generation) or None."""
    now = now or datetime.now()
    expires = now + timedelta(seconds=ttl or lease_seconds())
    t = AutonomousNovelJob.__table__
    stmt = (
        update(t)
        .where(t.c.id == job_id)
        .where(t.c.status.in_(ELIGIBLE_STATUSES))
        .where((t.c.lease_owner.is_(None)) | (t.c.lease_owner == owner) | (t.c.lease_expires_at.is_(None)) | (t.c.lease_expires_at <= now))
        .values(lease_owner=owner, lease_expires_at=expires, lease_generation=t.c.lease_generation + 1, heartbeat_at=now, updated_at=now)
    )
    result = session.execute(stmt)
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    job = session.get(AutonomousNovelJob, job_id)
    session.refresh(job)
    return Lease(job_id=job_id, owner=owner, generation=int(job.lease_generation), expires_at=expires)


def fenced_update(session: Session, lease: Lease, values: Dict[str, Any], *, commit: bool = True) -> None:
    """Publish job fields only if the lease is still ours. Raises ``JobLeaseLost`` otherwise."""
    t = AutonomousNovelJob.__table__
    now = datetime.now()
    stmt = (
        update(t)
        .where(t.c.id == lease.job_id, t.c.lease_owner == lease.owner, t.c.lease_generation == lease.generation)
        .values(**values, updated_at=now)
    )
    result = session.execute(stmt)
    if result.rowcount != 1:
        session.rollback()
        raise JobLeaseLost(lease.job_id, lease.owner, lease.generation)
    if commit:
        session.commit()


def renew(session: Session, lease: Lease, *, ttl: Optional[int] = None) -> datetime:
    """Heartbeat: extend the lease if still ours; raises ``JobLeaseLost`` if replaced."""
    expires = datetime.now() + timedelta(seconds=ttl or lease_seconds())
    fenced_update(session, lease, {"lease_expires_at": expires, "heartbeat_at": datetime.now()})
    return expires


def check(session: Session, lease: Lease) -> None:
    """Cheap ownership check (read) before expensive work; raises ``JobLeaseLost``."""
    job = session.get(AutonomousNovelJob, lease.job_id)
    if job is not None:
        session.refresh(job)
    if job is None or job.lease_owner != lease.owner or int(job.lease_generation) != lease.generation:
        raise JobLeaseLost(lease.job_id, lease.owner, lease.generation)


def release(session: Session, lease: Lease) -> bool:
    """Drop the lease if still ours (used when parking a job for the user). Never raises."""
    try:
        fenced_update(session, lease, {"lease_owner": None, "lease_expires_at": None})
        return True
    except JobLeaseLost:
        return False


class Heartbeat:
    """Renews the lease on its own session and interval until stopped or the lease is lost.

    Independent of model progress: a long provider call cannot starve it. On
    lease loss it sets ``lost`` so the runner aborts before publishing anything.
    """

    def __init__(self, lease: Lease, *, session_factory: Callable[[], Session], interval: Optional[float] = None):
        self.lease = lease
        self.session_factory = session_factory
        self.interval = float(interval or heartbeat_seconds())
        self.lost = False
        self.renewals = 0
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()

    async def _loop(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=self.interval)
                    return
                except asyncio.TimeoutError:
                    pass
                try:
                    with self.session_factory() as s:
                        renew(s, self.lease)
                    self.renewals += 1
                except JobLeaseLost:
                    self.lost = True
                    logger.warning(f"[Autonomous] heartbeat: job {self.lease.job_id} lease generation {self.lease.generation} lost")
                    return
                except Exception as exc:  # noqa: BLE001 - transient DB error: keep trying until the lease expires
                    logger.warning(f"[Autonomous] heartbeat renew failed for job {self.lease.job_id}: {type(exc).__name__}")
        except asyncio.CancelledError:
            return

    def start(self) -> "Heartbeat":
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
        return self

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()
            self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()


__all__ = ["ELIGIBLE_STATUSES", "Heartbeat", "JobLeaseLost", "Lease", "acquire", "check", "fenced_update", "heartbeat_seconds", "lease_seconds", "release", "renew"]
