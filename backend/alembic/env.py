import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
if config.config_file_name is not None and not config.get_main_option("skip_logging_config"):
    fileConfig(config.config_file_name)

# Make the backend package importable (alembic/ lives directly under backend/).
BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from app.db import models  # noqa: E402  (registers every SQLModel table)

target_metadata = models.SQLModel.metadata


def _database_url() -> str:
    """Prefer the application's configured database (NOVELFORGE_DB_PATH) over alembic.ini."""
    override = config.get_main_option("app_database_url")
    if override:
        return override
    try:
        from app.core.config import settings

        return settings.database.get_database_url()
    except Exception:
        return config.get_main_option("sqlalchemy.url")


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = config.attributes.get("connection")
    if connectable is not None:
        # Called from the application with an existing connection.
        context.configure(connection=connectable, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return

    section = config.get_section(config.config_ini_section, {}) or {}
    section["sqlalchemy.url"] = _database_url()
    engine = engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
