"""Drive an autonomous job through the pipeline with the deterministic test model (no live model calls).

Usage (from backend/):  ./venv/bin/python scripts/demo_autonomous_job.py [--until STAGE] [--job ID]

Creates (or resumes) a job on the development database using the synthetic
fixture EPUB and the FakeClient from the test-suite, so the UI can be
exercised end to end without model credentials. The resulting job is clearly
labelled as a demo in its options.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND)
os.environ.setdefault("AUTHND_TOKEN_MODE", "pool")
os.environ.setdefault("AUTHND_TOKEN_POOL_URL", "http://127.0.0.1:9/never")

from sqlmodel import Session, select  # noqa: E402

from app.db.models import LLMConfig  # noqa: E402
from app.db.session import engine  # noqa: E402
from app.services.autonomous import runner as runner_mod  # noqa: E402
from tests.test_autonomous_pipeline import CHAPTERS, FakeClient, build_source_epub  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default=None, help="stop when this stage is reached")
    ap.add_argument("--job", type=int, default=None, help="resume an existing job id")
    ap.add_argument("--select", action="store_true", help="select the first accepted storyline with the demo chapter count")
    args = ap.parse_args()
    fake = FakeClient()
    with Session(engine) as s:
        if args.job:
            job = s.get(runner_mod.AutonomousNovelJob, args.job)
        else:
            cfg = s.exec(select(LLMConfig)).first()
            job = runner_mod.create_job(s, filename="ledger-of-the-lantern-ward.epub", data=build_source_epub(), llm_config_id=cfg.id, options={"genre": "mystery", "words_per_chapter": 260, "demo": True})
        if args.select:
            from app.db.models import StorylineCandidate

            row = s.exec(select(StorylineCandidate).where(StorylineCandidate.job_id == job.id, StorylineCandidate.rejected == False)).first()  # noqa: E712
            job = runner_mod.select_storyline(s, job, storyline_id=row.id, chapter_count=CHAPTERS)
        runner = runner_mod.JobRunner(s, job.id, client_factory=lambda a, b, c: fake, owner="demo")
        job = asyncio.run(runner.run(until_stage=args.until))
        print(f"job {job.id}: stage={job.stage} status={job.status} progress={job.progress_percent}% msg={job.progress_message}")


if __name__ == "__main__":
    main()
