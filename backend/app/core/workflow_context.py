from contextvars import ContextVar
from typing import List, Optional

# Context variable holding the workflow run IDs triggered by the current request.
# The default is None (never a shared mutable list); each request initializes a
# fresh list via the middleware.
_workflow_runs_ctx: ContextVar[Optional[List[int]]] = ContextVar("workflow_runs_ctx", default=None)

def init_workflow_context():
    """Initialize the context (called at the start of each request)"""
    _workflow_runs_ctx.set([])

def add_triggered_run_id(run_id: int):
    """Add a triggered run ID"""
    current_list = _workflow_runs_ctx.get()
    if current_list is None:
        # Middleware did not initialize; create a per-call list instead of
        # mutating a shared default.
        current_list = []
        _workflow_runs_ctx.set(current_list)
    current_list.append(run_id)

def get_triggered_run_ids() -> List[int]:
    """Get all triggered run IDs"""
    return _workflow_runs_ctx.get() or []

def clear_workflow_context():
    """Clear the context"""
    _workflow_runs_ctx.set([])
