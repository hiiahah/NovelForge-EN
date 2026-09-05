"""Failure categories and the deterministic recovery ladder.

"Fail closed" here means: classify, then apply the category's recovery policy
(retry / reduce / rebuild / pause) instead of stopping and asking the user.
The ladder is data, not control flow, so tests can assert it and operators
can read it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

PROVIDER_FAILURE = "provider_failure"
MALFORMED_OUTPUT = "malformed_output"
INSUFFICIENT_EVIDENCE = "insufficient_evidence"
INTERNAL_CONTRADICTION = "internal_contradiction"
CONTINUITY_VIOLATION = "continuity_violation"
ORIGINALITY_VIOLATION = "originality_violation"
PLANNING_IMPOSSIBILITY = "planning_impossibility"
STALE_DEPENDENCY = "stale_dependency"
TOKEN_OVERFLOW = "token_overflow"
BUDGET_EXCEEDED = "budget_exceeded"
USER_INPUT_REQUIRED = "user_input_required"
INTERNAL_ERROR = "internal_error"

CATEGORIES: Tuple[str, ...] = (
    PROVIDER_FAILURE, MALFORMED_OUTPUT, INSUFFICIENT_EVIDENCE, INTERNAL_CONTRADICTION, CONTINUITY_VIOLATION,
    ORIGINALITY_VIOLATION, PLANNING_IMPOSSIBILITY, STALE_DEPENDENCY, TOKEN_OVERFLOW, BUDGET_EXCEEDED, USER_INPUT_REQUIRED, INTERNAL_ERROR,
)

# Recovery actions (ordered ladder; a policy lists which rungs apply).
RETRY = "retry"
RETRY_CLARIFIED = "retry_with_clarified_schema"
REANALYZE_UNIT = "reanalyze_smallest_failed_unit"
INDEPENDENT_VERIFY = "independent_verifier"
REPAIR_ARTIFACT = "repair_artifact"
REBUILD_DOWNSTREAM = "rebuild_stale_downstream"
REDUCE_SCOPE = "reduce_scope_and_retry"
FALLBACK_MODEL = "switch_to_fallback_model"
PAUSE = "pause_after_budget_exhausted"


@dataclass(frozen=True)
class RecoveryPolicy:
    category: str
    max_attempts: int
    ladder: Tuple[str, ...]
    backoff_seconds: float = 0.0

    def action_for(self, attempt: int) -> str:
        """Action for the given 1-based attempt number; PAUSE once the ladder is exhausted."""
        if attempt > self.max_attempts or attempt > len(self.ladder):
            return PAUSE
        return self.ladder[attempt - 1]


POLICIES: Dict[str, RecoveryPolicy] = {
    PROVIDER_FAILURE: RecoveryPolicy(PROVIDER_FAILURE, 4, (RETRY, RETRY, FALLBACK_MODEL, RETRY), backoff_seconds=2.0),
    MALFORMED_OUTPUT: RecoveryPolicy(MALFORMED_OUTPUT, 4, (RETRY, RETRY_CLARIFIED, REDUCE_SCOPE, FALLBACK_MODEL)),
    INSUFFICIENT_EVIDENCE: RecoveryPolicy(INSUFFICIENT_EVIDENCE, 3, (REANALYZE_UNIT, INDEPENDENT_VERIFY, REDUCE_SCOPE)),
    INTERNAL_CONTRADICTION: RecoveryPolicy(INTERNAL_CONTRADICTION, 3, (INDEPENDENT_VERIFY, REANALYZE_UNIT, REPAIR_ARTIFACT)),
    CONTINUITY_VIOLATION: RecoveryPolicy(CONTINUITY_VIOLATION, 3, (REPAIR_ARTIFACT, REPAIR_ARTIFACT, REDUCE_SCOPE)),
    ORIGINALITY_VIOLATION: RecoveryPolicy(ORIGINALITY_VIOLATION, 3, (REPAIR_ARTIFACT, REDUCE_SCOPE, REPAIR_ARTIFACT)),
    PLANNING_IMPOSSIBILITY: RecoveryPolicy(PLANNING_IMPOSSIBILITY, 3, (REPAIR_ARTIFACT, REDUCE_SCOPE, RETRY_CLARIFIED)),
    STALE_DEPENDENCY: RecoveryPolicy(STALE_DEPENDENCY, 2, (REBUILD_DOWNSTREAM, REBUILD_DOWNSTREAM)),
    TOKEN_OVERFLOW: RecoveryPolicy(TOKEN_OVERFLOW, 3, (REDUCE_SCOPE, REDUCE_SCOPE, REDUCE_SCOPE)),
    BUDGET_EXCEEDED: RecoveryPolicy(BUDGET_EXCEEDED, 0, ()),
    USER_INPUT_REQUIRED: RecoveryPolicy(USER_INPUT_REQUIRED, 0, ()),
    INTERNAL_ERROR: RecoveryPolicy(INTERNAL_ERROR, 2, (RETRY, RETRY)),
}


class StageFailure(Exception):
    """A classified stage failure. ``retryable`` is derived from the policy."""

    def __init__(self, category: str, message: str, *, detail: Optional[dict] = None):
        super().__init__(message)
        self.category = category if category in POLICIES else INTERNAL_ERROR
        self.detail = detail or {}

    def as_dict(self) -> dict:
        return {"category": self.category, "message": str(self), "detail": self.detail}


def classify_exception(exc: BaseException) -> str:
    """Map an arbitrary exception to a failure category (conservative)."""
    if isinstance(exc, StageFailure):
        return exc.category
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(k in text for k in ("structured output invalid", "validation error", "json", "parsing", "empty response", "did not return a usable result")):
        return MALFORMED_OUTPUT
    if any(k in text for k in ("context length", "maximum context", "too many tokens", "token limit", "context_length_exceeded")):
        return TOKEN_OVERFLOW
    if any(k in text for k in ("quota", "budget")):
        return BUDGET_EXCEEDED
    if any(k in text for k in ("timeout", "timed out", "connection", "rate limit", "429", "502", "503", "504", "unavailable", "reset by peer", "network", "authnd", "token pool")):
        return PROVIDER_FAILURE
    if "stale" in text:
        return STALE_DEPENDENCY
    return INTERNAL_ERROR


__all__ = [
    "BUDGET_EXCEEDED", "CATEGORIES", "CONTINUITY_VIOLATION", "FALLBACK_MODEL", "INDEPENDENT_VERIFY", "INSUFFICIENT_EVIDENCE",
    "INTERNAL_CONTRADICTION", "INTERNAL_ERROR", "MALFORMED_OUTPUT", "ORIGINALITY_VIOLATION", "PAUSE", "PLANNING_IMPOSSIBILITY",
    "POLICIES", "PROVIDER_FAILURE", "REANALYZE_UNIT", "REBUILD_DOWNSTREAM", "REDUCE_SCOPE", "REPAIR_ARTIFACT", "RETRY",
    "RETRY_CLARIFIED", "RecoveryPolicy", "STALE_DEPENDENCY", "StageFailure", "TOKEN_OVERFLOW", "USER_INPUT_REQUIRED", "classify_exception",
]
