"""Write the FastAPI OpenAPI document to a file without starting a server.

Usage: python scripts/export_openapi.py [output_path]
Used by `npm run gen:types:file` and the CI generated-types consistency check.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, BACKEND_DIR)
os.environ.setdefault("NOVELFORGE_DB_PATH", os.path.join(tempfile.gettempdir(), "novelforge_openapi_export.db"))


def main() -> int:
    from main import app

    out = sys.argv[1] if len(sys.argv) > 1 else os.path.join(BACKEND_DIR, "openapi.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(app.openapi(), f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
