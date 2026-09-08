"""Fresh database -> alembic head, then assert no model/DB drift.

Usage: NOVELFORGE_DB_PATH=/tmp/smoke.db python scripts/migration_smoke.py
"""

from __future__ import annotations

import os
import sys
import tempfile

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_DIR)
os.environ.setdefault("NOVELFORGE_DB_PATH", os.path.join(tempfile.gettempdir(), "novelforge_migration_smoke.db"))


def main() -> int:
    from app.db.migrations import check_schema_drift, current_revision, head_revision, upgrade_database
    from app.db.session import engine

    info = upgrade_database(engine)
    if current_revision(engine) != head_revision():
        print(f"FAIL: current={current_revision(engine)} head={head_revision()} info={info}")
        return 1
    drift = check_schema_drift(engine)
    if drift:
        print(f"FAIL: model/DB drift without a revision: {drift}")
        return 1
    print("migration smoke ok", info)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
