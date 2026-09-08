"""Story Memory event hooks.

On ``card.saved`` for a Chapter Text card:
1. mark the existing digest stale when the text changed (synchronous, cheap);
2. when the project's settings enable auto-digest and the chapter has enough
   words, schedule a background digest so the user never has to remember to
   click. The request thread is never blocked by an LLM call.
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Dict, Optional, Set

from loguru import logger
from sqlmodel import Session

from app.core.events import Event, on_event
from app.db.session import engine as db_engine
from app.services.forge.textmetrics import count_units

# Chapter cards with a digest in flight (project_id, chapter_number) so a burst
# of saves does not fan out into duplicate LLM calls.
_in_flight: Set[tuple] = set()
_lock = threading.Lock()


def _content(card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _resolve_llm_config_id(session: Session, card, project_id: int) -> Optional[int]:
    from app.services.story_memory.settings import get_settings

    cfg = get_settings(session, project_id)
    if cfg.digest_llm_config_id:
        return int(cfg.digest_llm_config_id)
    params = getattr(card, "ai_params", None) or {}
    if isinstance(params, dict) and params.get("llm_config_id"):
        return int(params["llm_config_id"])
    ct = getattr(card, "card_type", None)
    type_params = getattr(ct, "ai_params", None) or {}
    if isinstance(type_params, dict) and type_params.get("llm_config_id"):
        return int(type_params["llm_config_id"])
    return None


def _run_background_digest(project_id: int, card_id: int, chapter_number: int, llm_config_id: int) -> None:
    from app.db.models import Card
    from app.services.story_memory.digest_service import DigestService

    key = (project_id, chapter_number)

    async def _job():
        session = Session(db_engine)
        try:
            card = session.get(Card, card_id)
            if not card:
                return
            c = _content(card)
            text = str(c.get("content") or "")
            participants = [x if isinstance(x, str) else (x or {}).get("name", "") for x in (c.get("entity_list") or [])]
            await DigestService(session).digest_chapter(
                project_id=project_id, llm_config_id=llm_config_id, text=text, chapter_number=chapter_number,
                volume_number=c.get("volume_number"), title=str(c.get("title") or card.title), chapter_card_id=card.id,
                participants=[p for p in participants if p],
            )
            logger.info(f"[StoryMemory] auto-digested project={project_id} ch.{chapter_number}")
        except Exception as exc:
            logger.warning(f"[StoryMemory] auto-digest failed project={project_id} ch.{chapter_number}: {exc}")
        finally:
            session.close()
            with _lock:
                _in_flight.discard(key)

    threading.Thread(target=lambda: asyncio.run(_job()), name=f"story-memory-digest-{project_id}-{chapter_number}", daemon=True).start()


@on_event("card.saved")
def handle_chapter_saved(event: Event) -> None:
    session: Optional[Session] = event.data.get("session")
    card = event.data.get("card")
    if not session or card is None:
        return
    if (event.data.get("card_type") or getattr(getattr(card, "card_type", None), "name", None)) != "Chapter Text":
        return
    c = _content(card)
    text = str(c.get("content") or "").strip()
    try:
        chapter_number = int(c.get("chapter_number") or 0)
    except (TypeError, ValueError):
        return
    if chapter_number <= 0:
        return
    project_id = int(card.project_id)

    try:
        from app.services.story_memory.digest_service import DigestService, text_hash
        from app.services.story_memory.settings import get_settings

        old = event.data.get("old_content")
        changed = not isinstance(old, dict) or str(old.get("content") or "").strip() != text
        if changed and text:
            if DigestService(session).mark_stale(project_id, chapter_number, text_hash(text)):
                session.commit()

        cfg = get_settings(session, project_id)
        if not cfg.auto_digest_on_save or not text or not changed:
            return
        if count_units(text) < cfg.auto_digest_min_words:
            return
        # Only digest content the user has settled on: skip AI drafts awaiting confirmation.
        if getattr(card, "needs_confirmation", False):
            return
        llm_config_id = _resolve_llm_config_id(session, card, project_id)
        if not llm_config_id:
            logger.debug(f"[StoryMemory] no model configured for auto-digest (project={project_id})")
            return
        key = (project_id, chapter_number)
        with _lock:
            if key in _in_flight:
                return
            _in_flight.add(key)
        _run_background_digest(project_id, int(card.id), chapter_number, llm_config_id)
    except Exception:
        session.rollback()
        logger.exception("[StoryMemory] card.saved handler failed")
