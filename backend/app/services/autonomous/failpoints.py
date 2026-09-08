"""Test-only failpoints around durable boundaries.

Disabled unless ``AUTONOMOUS_FAILPOINTS`` (settings) names them; that setting is
read from the process environment only, never from a request, so failpoints
cannot be triggered remotely. ``hit(name)`` raises ``FailpointTriggered`` (a
handled error) or, for ``name!kill``, terminates the process with ``os._exit``
to simulate a real crash before the surrounding transaction commits.
"""

from __future__ import annotations

import os
from typing import Dict, Set

from app.core.config import settings

NAMES: Set[str] = {
    "before_artifact_write", "after_artifact_write", "before_commit", "after_commit", "before_stage_advance", "after_stage_advance",
    "after_model_response", "after_chapter_commit", "before_canon_sync", "during_canon_sync", "before_export_create", "after_export_create", "before_export_register", "after_stage_work",
}


class FailpointTriggered(RuntimeError):
    pass


_hits: Dict[str, int] = {}


def _enabled() -> Dict[str, str]:
    raw = os.environ.get("AUTONOMOUS_FAILPOINTS", settings.autonomous.failpoints) or ""
    out: Dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, mode = item.partition("!")
        out[name] = mode or "raise"
    return out


def hit(name: str, *, once: bool = True) -> None:
    """Trigger ``name`` if enabled. ``once`` makes a failpoint fire only on its first hit per process."""
    enabled = _enabled()
    if name not in enabled:
        return
    _hits[name] = _hits.get(name, 0) + 1
    if once and _hits[name] > 1:
        return
    mode = enabled[name]
    if mode == "kill":
        os._exit(137)  # simulate SIGKILL: no finally blocks, no commits
    raise FailpointTriggered(f"failpoint {name}")


def reset() -> None:
    _hits.clear()


__all__ = ["FailpointTriggered", "NAMES", "hit", "reset"]
