"""Per-project Story Memory settings, stored as a singleton card (no migration)."""

from __future__ import annotations

from typing import Optional

from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType
from app.schemas.card import CardCreate
from app.schemas.story_memory import StoryMemorySettings

SETTINGS_TYPE = "Story Memory Settings"
SETTINGS_TITLE = "Story Memory Settings"


def _settings_card(session: Session, project_id: int) -> Optional[Card]:
    ct = session.exec(select(CardType).where(CardType.name == SETTINGS_TYPE)).first()
    if not ct:
        return None
    return session.exec(select(Card).where(Card.project_id == project_id, Card.card_type_id == ct.id)).first()


def get_settings(session: Session, project_id: int) -> StoryMemorySettings:
    card = _settings_card(session, project_id)
    if not card or not isinstance(card.content, dict):
        return StoryMemorySettings()
    try:
        return StoryMemorySettings.model_validate(card.content)
    except Exception:
        return StoryMemorySettings()


def save_settings(session: Session, project_id: int, settings: StoryMemorySettings, *, commit: bool = True) -> StoryMemorySettings:
    from app.services.card_service import CardService

    card = _settings_card(session, project_id)
    payload = settings.model_dump(mode="json")
    if card:
        card.content = payload
        flag_modified(card, "content")
        session.add(card)
    else:
        ct = session.exec(select(CardType).where(CardType.name == SETTINGS_TYPE)).first()
        if not ct:
            raise ValueError(f"Card type not found: {SETTINGS_TYPE}")
        CardService(session).create(CardCreate(title=SETTINGS_TITLE, content=payload, card_type_id=ct.id, parent_id=None), project_id, commit=False)
    if commit:
        session.commit()
    else:
        session.flush()
    return settings
