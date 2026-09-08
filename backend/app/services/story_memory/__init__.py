"""Story Memory: chapter digests, rolling recap, continuity guard, planner, health.

Public surface:
- ``DigestService``      LLM-extract and store ``Chapter Digest`` cards
- ``StorySoFarCompiler`` deterministic tiered recap + carry-forward state
- ``ContinuityGuard``    deterministic (+ optional LLM) checks on a draft
- ``NextChapterPlanner`` "what the next chapter must address" brief
- ``bible_health``       deterministic Bible coverage score
- ``settings``           per-project Story Memory settings (singleton card)
"""

from app.services.story_memory import events  # noqa: F401  (registers card.saved handler)
from app.services.story_memory.continuity_guard import ContinuityGuard  # noqa: F401
from app.services.story_memory.digest_service import DigestService  # noqa: F401
from app.services.story_memory.health import bible_health  # noqa: F401
from app.services.story_memory.planner import NextChapterPlanner  # noqa: F401
from app.services.story_memory.settings import get_settings, save_settings  # noqa: F401
from app.services.story_memory.story_so_far import StorySoFarCompiler  # noqa: F401
