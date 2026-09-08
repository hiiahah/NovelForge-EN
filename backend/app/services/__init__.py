# To avoid circular dependencies, submodules are not imported here. This only serves as a namespace package.

# Import the workflow module to trigger decorator registration (including nodes and triggers)
from . import workflow  # noqa: F401
# Forge event hooks (dependency-graph stale propagation on card save)
from .forge import events as _forge_events  # noqa: F401
# Story Memory event hooks (digest staleness + auto-digest on chapter save)
from .story_memory import events as _story_memory_events  # noqa: F401