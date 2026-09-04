"""Application startup and shutdown

Handles database initialization, schema migration (auto-add-column), event discovery, and bootstrap.
"""

from loguru import logger
from sqlmodel import Session

from app.db.session import engine
from app.core.events import discover_event_handlers
from app.bootstrap import discover_and_run_initializers


def startup():
    """Application startup sequence.

    1. Apply Alembic migrations (creates tables on a fresh database)
    2. Adopt legacy databases at the baseline revision
    3. Discover event handlers
    4. Run bootstrap initializers (prompts, card types, knowledge base, workflows, etc.)
    """
    logger.info("=" * 60)
    logger.info("Novel Forge backend starting up...")
    logger.info("=" * 60)

    # 1/2. Schema: Alembic revisions own the schema. Legacy databases (created
    # before Alembic was wired in) are adopted at the baseline revision after a
    # conservative add-missing-columns pass, then upgraded to head.
    logger.info("[Startup] Applying database migrations...")
    from app.db.migrations import upgrade_database

    upgrade_database(engine)

    # 3. Discover event handlers
    logger.info("[Startup] Discovering event handlers...")
    discover_event_handlers()

    # 4. Run bootstrap initializers
    logger.info("[Startup] Running bootstrap initializers...")
    with Session(engine) as session:
        discover_and_run_initializers(session)

    logger.info("[Startup] Startup complete.")


def shutdown():
    """Application shutdown sequence."""
    logger.info("=" * 60)
    logger.info("Novel Forge backend shutting down...")
    logger.info("=" * 60)

    try:
        engine.dispose()
    except Exception as e:
        logger.error(f"[Shutdown] Error disposing engine: {e}")

    logger.info("[Shutdown] Shutdown complete.")
