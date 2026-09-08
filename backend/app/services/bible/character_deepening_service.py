"""Character Bible Deepening: extend one Character Card with Bible 2.0 groups.

The existing fields (description, personality, core_drive, character_arc,
dynamic_info) are preserved; only the new optional groups are written, each
change recorded in the card's ``history``.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session

from app.db.models import Card
from app.schemas.bible import CharacterBibleDeepening, HistoryEntry
from app.schemas.entity import CharacterCard
from app.services import prompt_service
from app.services.ai.core import llm_service
from app.services.bible.bible_service import BibleService

PROMPT_NAME = "Character Bible Deepening"

_GROUP_FIELDS = ("dramatic_design", "voice", "competence", "arc_milestones", "consistency_rules", "aliases", "protagonist_voice")


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


class CharacterDeepeningService:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)

    def _context_digest(self, project_id: int, card: Card) -> str:
        parts: List[str] = []
        for type_name, fields in (
            ("Work Tags", None),
            ("Story Foundation", ["core_premise", "central_dramatic_question", "protagonist", "main_opposition", "stakes", "unique_mechanism", "thematic_argument", "counter_argument"]),
            ("Theme Map", ["theme_question", "protagonist_initial_belief", "antagonist_belief", "counter_character_belief", "planned_movement"]),
            ("Reader Contract", ["primary_fantasy", "expected_protagonist_behavior", "violations"]),
            ("Story Outline", ["overview"]),
        ):
            c = self.bible.singleton(project_id, type_name)
            if not c or not isinstance(c.content, dict):
                continue
            content = c.content if fields is None else {k: c.content.get(k) for k in fields if c.content.get(k)}
            if content:
                parts.append(f"[{type_name}]\n{_trim(json.dumps(content, ensure_ascii=False), 3000)}")
        world = self.bible.singleton(project_id, "Worldview Setting")
        if world and isinstance(world.content, dict):
            wv = world.content.get("world_view") or {}
            parts.append(f"[Worldview]\n{_trim(json.dumps({k: wv.get(k) for k in ('world_name', 'core_conflict', 'power_systems') if wv.get(k)}, ensure_ascii=False), 2000)}")
        others = []
        for other in self.bible.cards_of_type(project_id, "Character Card"):
            if other.id == card.id or not isinstance(other.content, dict):
                continue
            oc = other.content
            others.append(f"- {other.title} ({oc.get('role_type')}): {_trim(oc.get('description'), 160)}; drive: {_trim(oc.get('core_drive'), 120)}")
        if others:
            parts.append("[Other characters]\n" + "\n".join(others[:20]))
        return "\n\n".join(parts)

    async def deepen(
        self,
        *,
        project_id: int,
        card_id: int,
        llm_config_id: int,
        user_notes: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> Card:
        card = self.session.get(Card, card_id)
        if not card or card.project_id != project_id:
            raise ValueError("Character card not found in project")
        if not card.card_type or card.card_type.name != "Character Card":
            raise ValueError("Card is not a Character Card")

        prompt = prompt_service.get_prompt_by_name(self.session, PROMPT_NAME)
        if not prompt or not prompt.template:
            raise ValueError(f"Prompt not found: {PROMPT_NAME}")

        system_prompt = prompt_service.inject_knowledge(self.session, prompt.template) + (
            "\n\nPlease strictly output in the following JSON Schema format:\n"
            + json.dumps(CharacterBibleDeepening.model_json_schema(), ensure_ascii=False)
        )
        existing = {k: v for k, v in (card.content or {}).items() if k not in ("dynamic_info", "history")}
        user_prompt = "\n\n".join(filter(None, [
            f"[Existing Character Card]\n{json.dumps(existing, ensure_ascii=False)}",
            self._context_digest(project_id, card),
            f"[User notes]\n{user_notes.strip()}" if user_notes and user_notes.strip() else "",
            f"Deepen the character named exactly: {card.content.get('name') if isinstance(card.content, dict) and card.content.get('name') else card.title}",
        ]))

        logger.info(f"[CharacterDeepening] project={project_id} card={card_id} llm={llm_config_id}")
        result = await llm_service.generate_structured(
            session=self.session,
            llm_config_id=llm_config_id,
            user_prompt=user_prompt,
            output_type=CharacterBibleDeepening,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if not isinstance(result, CharacterBibleDeepening):
            result = CharacterBibleDeepening.model_validate(result)

        return self.apply(card, result)

    def apply(self, card: Card, deepening: CharacterBibleDeepening) -> Card:
        content: Dict[str, Any] = dict(card.content or {})
        history: List[Dict[str, Any]] = list(content.get("history") or [])
        now = datetime.now().isoformat(timespec="seconds")
        payload = deepening.model_dump(mode="json")
        for field in _GROUP_FIELDS:
            new_value = payload.get(field)
            if new_value in (None, [], {}):
                continue
            previous = content.get(field)
            if previous == new_value:
                continue
            content[field] = new_value
            history.append(HistoryEntry(
                field=field,
                previous="(set)" if previous not in (None, [], {}) else None,
                new="(deepened)",
                reason="Character Bible Deepening",
                changed_at=now,
                accepted_by="ai",
            ).model_dump(mode="json"))
        content["history"] = history[-200:]
        # Validate against the card model so legacy required fields stay intact.
        CharacterCard.model_validate(content)
        card.content = content
        card.ai_modified = True
        card.last_modified_by = "ai"
        flag_modified(card, "content")
        self.session.add(card)
        self.session.commit()
        self.session.refresh(card)
        return card
