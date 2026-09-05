from sqlalchemy import event
from sqlmodel import create_engine, Session
from app.core.config import settings

# Get database URL from config
DATABASE_URL = settings.database.get_database_url()

# Create the database engine (SQLite needs this parameter to allow multi-threaded access)
engine = create_engine(
    DATABASE_URL,
    echo=settings.database.echo,
    connect_args={"check_same_thread": False}
)


def apply_sqlite_pragmas(dbapi_connection, connection_record=None) -> None:
    """Per-connection SQLite settings.

    - WAL + busy_timeout: the API and background workflow threads write
      concurrently and otherwise hit "database is locked" on the default
      rollback journal.
    - foreign_keys: SQLite ignores FK constraints unless enabled per connection.
    """
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


if DATABASE_URL.startswith("sqlite"):
    event.listens_for(engine, "connect")(apply_sqlite_pragmas)


def get_session():
    """
    FastAPI dependency that provides a transactional database session.
    It ensures that the session is committed on success and rolled back on error.
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
