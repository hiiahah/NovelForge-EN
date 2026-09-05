"""Event hooks: card saves propagate staleness through the dependency graph."""

from __future__ import annotations

from loguru import logger

from app.core.events import Event, on_event
from app.services.forge import provenance


@on_event("card.saved")
def handle_card_saved_provenance(event: Event) -> None:
    session = event.data.get("session")
    card = event.data.get("card")
    if not session or card is None or event.data.get("is_created"):
        return
    try:
        old = event.data.get("old_content")
        if old is not None and old == (card.content if isinstance(card.content, dict) else {}):
            return
        info = provenance.mark_card_changed(session, card)
        session.commit()
        if info["stale_count"]:
            logger.info(f"[Forge] card {card.id} ({info['kind']}) changed: {info['stale_count']} stale artifact(s), affected chapters {info['affected_chapters'][:10]}")
    except Exception:
        session.rollback()
        logger.exception("[Forge] stale propagation failed")
