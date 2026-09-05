"""In-process background worker for autonomous jobs.

One asyncio task per active job, each with its own Session (never the request
session). The registry mirrors ``workflow_runtime``: the API starts a task on
create/resume, ``is_active`` prevents duplicate execution, and
``recover_on_startup`` requeues jobs a dead process left running so the state
machine continues from its persisted stage.
"""

from __future__ import annotations

import asyncio
from typing import Dict, Optional

from loguru import logger
from sqlmodel import Session, select

from app.db.models import AutonomousNovelJob
from app.services.autonomous.runner import ClientFactory, JobRunner, default_client_factory, recover_stale_leases


class AutonomousWorker:
    def __init__(self, *, client_factory: ClientFactory = default_client_factory):
        self._tasks: Dict[int, asyncio.Task] = {}
        self.client_factory = client_factory

    def is_active(self, job_id: int) -> bool:
        t = self._tasks.get(job_id)
        return t is not None and not t.done()

    def active_jobs(self) -> list[int]:
        return [jid for jid, t in self._tasks.items() if not t.done()]

    async def _execute(self, job_id: int) -> None:
        from app.db.session import engine

        with Session(engine) as session:
            runner = JobRunner(session, job_id, client_factory=self.client_factory)
            try:
                job = await runner.run()
                logger.info(f"[Autonomous] job {job_id} stopped at {job.stage} ({job.status})")
            except asyncio.CancelledError:
                logger.info(f"[Autonomous] job {job_id} task cancelled")
            except Exception:  # noqa: BLE001
                logger.exception(f"[Autonomous] job {job_id} worker crashed")
                job = session.get(AutonomousNovelJob, job_id)
                if job is not None and job.status == "running":
                    job.status = "paused"
                    job.progress_message = "Worker crashed; resume to continue"
                    session.add(job)
                    session.commit()

    def start(self, job_id: int) -> bool:
        if self.is_active(job_id):
            return False
        self._tasks[job_id] = asyncio.create_task(self._execute(job_id))
        return True

    def stop(self, job_id: int) -> bool:
        t = self._tasks.get(job_id)
        if t is None or t.done():
            return False
        t.cancel()
        return True

    def recover_on_startup(self, session: Session) -> int:
        n = recover_stale_leases(session)
        for job in session.exec(select(AutonomousNovelJob).where(AutonomousNovelJob.status == "queued")).all():
            try:
                self.start(int(job.id))
            except RuntimeError:  # no running loop (tests)
                pass
        return n


autonomous_worker = AutonomousWorker()

__all__ = ["AutonomousWorker", "autonomous_worker"]
