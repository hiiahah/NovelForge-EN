"""Living Bible: propose -> review -> apply.

Extraction builds a compact Bible digest for the participants, asks the LLM for
a ``BibleUpdateProposal``, assigns stable change ids and stores the proposal in
``BibleUpdateReview``. Nothing is written to Bible cards until the user decides.

Apply semantics:
- ``accept`` writes the value (or the user's edited value) to the target card,
  appends a ``HistoryEntry`` and an ``Evidence`` item, and sets truth_status.
- ``mark_planned`` writes the value with truth_status "planned".
- ``intentional_contradiction`` / ``unreliable_narration`` record a history note
  on the target card without changing the value.
- ``reject`` / ``postpone`` only record the decision.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import BibleUpdateReview, Card, CardType
from app.schemas.bible import Evidence, HistoryEntry
from app.schemas.bible_update import (
    BibleUpdateApplyResult,
    BibleUpdateProposal,
    BibleUpdateReviewRead,
    ChangeDecision,
    ProposedChange,
)
from app.schemas.card import CardCreate
from app.services import prompt_service
from app.services.ai.core import llm_service
from app.services.card_service import CardService
from app.services.bible.bible_service import BibleService

PROMPT_NAME = "Bible Update Extraction"

# Fields on Character Cards that the legacy dynamic_info extractor already owns.
_DYNAMIC_INFO_PREFIX = "dynamic_info."

_ENTITY_TYPE_BY_CARD_TYPE = {
    "Character Card": "character",
    "Scene Card": "scene",
    "Organization Card": "organization",
    "Item Card": "item",
    "Concept Card": "concept",
}

_NEW_CARD_REQUIRED_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "Character Card": {"life_span": "Long Term", "role_type": "Supporting Character", "born_scene": "", "description": "", "personality": "", "core_drive": "", "character_arc": ""},
    "Scene Card": {"life_span": "Long Term", "description": "", "function_in_story": ""},
    "Organization Card": {"life_span": "Long Term", "description": ""},
    "Item Card": {"life_span": "Long Term"},
    "Concept Card": {"life_span": "Long Term"},
}


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


class LivingBibleService:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)

    # ------------------------------------------------------------ digest build
    def build_bible_digest(self, project_id: int, participants: List[str], max_chars: int = 9000) -> str:
        """Compact, relevance-filtered Bible snapshot given to the extractor as reference."""
        parts: List[str] = []
        names = {p.strip().lower() for p in participants if p and p.strip()}

        def relevant(entities: Any) -> bool:
            if not names:
                return True
            if not isinstance(entities, list):
                return False
            return any(str(e).strip().lower() in names for e in entities)

        for card in self.bible.cards_of_type(project_id, "Character Card"):
            c = card.content if isinstance(card.content, dict) else {}
            if names and str(c.get("name") or card.title).strip().lower() not in names:
                continue
            dd = c.get("dramatic_design") or {}
            rules = c.get("consistency_rules") or {}
            parts.append(
                f"[Character] {card.title}: core_drive={_trim(c.get('core_drive'), 200)}; "
                f"external_goal={_trim(dd.get('external_goal'), 160)}; false_belief={_trim(dd.get('false_belief'), 160)}; "
                f"knowledge_restrictions={_trim(rules.get('knowledge_restrictions'), 240)}; "
                f"thoughts={_trim((c.get('dynamic_info') or {}).get('Thoughts / Goal Snapshot'), 240)}"
            )

        for card in self.bible.cards_of_type(project_id, "Relationship Arc"):
            c = card.content if isinstance(card.content, dict) else {}
            if names and not ({str(c.get("character_a", "")).lower(), str(c.get("character_b", "")).lower()} & names):
                continue
            parts.append(
                f"[Relationship] {card.title}: trust={c.get('trust')} affection={c.get('affection')} fear={c.get('fear')} "
                f"dependency={c.get('dependency')} resentment={c.get('resentment')}; private={_trim(c.get('private_relationship'), 160)}; "
                f"tension={_trim(c.get('unresolved_tension'), 160)}; planned_next={_trim(c.get('planned_next_shift'), 160)}"
            )

        for card in self.bible.cards_of_type(project_id, "Plot Thread"):
            c = card.content if isinstance(card.content, dict) else {}
            if c.get("status") in ("resolved", "abandoned"):
                continue
            if not relevant(c.get("participants")):
                continue
            parts.append(
                f"[Thread] {card.title} ({c.get('thread_type')}, {c.get('status')}, urgency={c.get('urgency')}): "
                f"question={_trim(c.get('central_question'), 160)}; last_advanced={c.get('last_advanced_chapter')}; "
                f"planned_resolution={_trim(c.get('planned_resolution'), 160)}"
            )

        for card in self.bible.cards_of_type(project_id, "Promise Payoff"):
            c = card.content if isinstance(card.content, dict) else {}
            if c.get("status") in ("paid_off", "subverted", "intentionally_abandoned"):
                continue
            if not relevant(c.get("participants")):
                continue
            parts.append(
                f"[Promise] {card.title} ({c.get('promise_type')}, {c.get('status')}): planned_payoff={_trim(c.get('planned_payoff'), 160)}; "
                f"target_range={c.get('target_payoff_range')}"
            )

        for card in self.bible.cards_of_type(project_id, "Knowledge Fact"):
            c = card.content if isinstance(card.content, dict) else {}
            knowers = c.get("knowers") or []
            if names and not any(str(k.get("entity", "")).lower() in names for k in knowers if isinstance(k, dict)):
                continue
            states = ", ".join(f"{k.get('entity')}={k.get('state')}" for k in knowers if isinstance(k, dict))
            parts.append(f"[Secret] {_trim(c.get('fact') or card.title, 160)}: reader={c.get('reader_state')}; {states}")

        for card in self.bible.cards_of_type(project_id, "World Rule"):
            c = card.content if isinstance(card.content, dict) else {}
            parts.append(f"[Rule/{c.get('domain')}] {_trim(c.get('rule') or card.title, 200)}")

        style = self.bible.singleton(project_id, "Style Profile")
        if style and isinstance(style.content, dict):
            s = style.content
            parts.append(
                f"[Style] pov={s.get('pov_mode')} tense={s.get('tense')} endings={_trim(s.get('chapter_ending_style'), 120)}; "
                f"forbidden={_trim(s.get('forbidden_cliches'), 200)}; unwanted_ai={_trim(s.get('unwanted_ai_patterns'), 200)}"
            )

        digest = "\n".join(parts)
        return digest if len(digest) <= max_chars else digest[:max_chars] + "\n…(digest truncated)"

    # -------------------------------------------------------------- extraction
    async def extract(
        self,
        *,
        project_id: int,
        llm_config_id: int,
        text: str,
        chapter_card_id: Optional[int],
        volume_number: Optional[int],
        chapter_number: Optional[int],
        participants: List[str],
        outline_text: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        timeout: Optional[float],
    ) -> BibleUpdateReview:
        prompt = prompt_service.get_prompt_by_name(self.session, PROMPT_NAME)
        if not prompt or not prompt.template:
            raise ValueError(f"Prompt not found: {PROMPT_NAME}")

        system_prompt = prompt.template + (
            "\n\nPlease strictly output in the following JSON Schema format:\n"
            + json.dumps(BibleUpdateProposal.model_json_schema(), ensure_ascii=False)
        )
        digest = self.build_bible_digest(project_id, participants)
        user_parts = [
            f"Chapter: volume {volume_number if volume_number is not None else '?'}, chapter {chapter_number if chapter_number is not None else '?'}",
            f"Participants (priority focus): {', '.join(participants) if participants else '(none provided)'}",
            "",
            "[Current Bible snapshot (reference; report changes relative to this)]",
            digest or "(empty Bible)",
        ]
        if outline_text:
            user_parts += ["", "[Chapter outline (planned)]", outline_text.strip()]
        user_parts += ["", "[Chapter text]", text]
        user_prompt = "\n".join(user_parts)

        logger.info(f"[LivingBible] extracting proposal project={project_id} chapter={chapter_number} participants={len(participants)}")
        result = await llm_service.generate_structured(
            session=self.session,
            llm_config_id=llm_config_id,
            user_prompt=user_prompt,
            output_type=BibleUpdateProposal,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if not isinstance(result, BibleUpdateProposal):
            result = BibleUpdateProposal.model_validate(result)

        result.chapter_number = chapter_number if chapter_number is not None else result.chapter_number
        result.volume_number = volume_number if volume_number is not None else result.volume_number
        for change in result.changes:
            change.id = change.id or uuid.uuid4().hex[:12]
            for ev in change.evidence:
                if ev.chapter_number is None:
                    ev.chapter_number = chapter_number
                if ev.quote:
                    ev.quote = _trim(ev.quote, 200)
            self._fill_previous_value(project_id, change)

        review = BibleUpdateReview(
            project_id=project_id,
            chapter_card_id=chapter_card_id,
            volume_number=volume_number,
            chapter_number=chapter_number,
            status="pending",
            proposal_json=result.model_dump(mode="json"),
            decisions_json={},
        )
        self.session.add(review)
        self.session.commit()
        self.session.refresh(review)
        return review

    def _fill_previous_value(self, project_id: int, change: ProposedChange) -> None:
        """Populate previous_value from the actual card when the extractor left it empty."""
        if change.previous_value is not None or not change.target_title or not change.field_path:
            return
        card = self.bible.find_card_by_content_name(project_id, change.target_card_type, change.target_title)
        if not card or not isinstance(card.content, dict):
            return
        value: Any = card.content
        for part in change.field_path.split("."):
            if isinstance(value, dict) and part in value:
                value = value[part]
            else:
                return
        if isinstance(value, list) and len(json.dumps(value, ensure_ascii=False)) > 2000:
            return
        change.previous_value = value

    # ----------------------------------------------------------------- reviews
    def list_reviews(self, project_id: int, status: Optional[str] = None) -> List[BibleUpdateReview]:
        stmt = select(BibleUpdateReview).where(BibleUpdateReview.project_id == project_id)
        if status:
            stmt = stmt.where(BibleUpdateReview.status == status)
        stmt = stmt.order_by(BibleUpdateReview.created_at.desc())
        return list(self.session.exec(stmt).all())

    def get_review(self, review_id: int) -> Optional[BibleUpdateReview]:
        return self.session.get(BibleUpdateReview, review_id)

    def delete_review(self, review_id: int) -> bool:
        review = self.get_review(review_id)
        if not review:
            return False
        self.session.delete(review)
        self.session.commit()
        return True

    @staticmethod
    def to_read(review: BibleUpdateReview) -> BibleUpdateReviewRead:
        return BibleUpdateReviewRead(
            id=review.id,
            project_id=review.project_id,
            chapter_card_id=review.chapter_card_id,
            volume_number=review.volume_number,
            chapter_number=review.chapter_number,
            status=review.status,
            proposal=BibleUpdateProposal.model_validate(review.proposal_json or {}),
            decisions=dict(review.decisions_json or {}),
            created_at=review.created_at.isoformat(),
            updated_at=review.updated_at.isoformat(),
        )

    # ------------------------------------------------------------------- apply
    def decide(self, review_id: int, decisions: List[ChangeDecision]) -> BibleUpdateApplyResult:
        """Apply a batch of decisions atomically.

        Either every decision in the batch is persisted together with the
        card changes it produced, or nothing is (the transaction is rolled
        back and the error re-raised). A change that was already accepted or
        rejected is skipped so retrying the same request cannot duplicate
        cards or history entries; postponed changes may be re-decided.
        """
        review = self.get_review(review_id)
        if not review:
            raise ValueError("Review not found")
        if review.status == "applied":
            raise ValueError("This review is already fully applied")
        if review.status == "dismissed":
            raise ValueError("This review was dismissed")
        proposal = BibleUpdateProposal.model_validate(review.proposal_json or {})
        by_id = {c.id: c for c in proposal.changes}
        stored: Dict[str, Any] = dict(review.decisions_json or {})

        applied = rejected = postponed = skipped = 0
        created: List[int] = []
        updated: List[int] = []
        errors: List[str] = []

        try:
            for decision in decisions:
                change = by_id.get(decision.change_id)
                if not change:
                    errors.append(f"Unknown change id: {decision.change_id}")
                    continue
                prior = stored.get(decision.change_id)
                if isinstance(prior, dict) and prior.get("action") not in (None, "postpone") and not prior.get("error"):
                    skipped += 1
                    continue
                record = {"action": decision.action, "note": decision.note, "decided_at": _now_iso()}
                if decision.action == "reject":
                    rejected += 1
                elif decision.action == "postpone":
                    postponed += 1
                else:
                    card_id, is_new = self._apply_change(review, change, decision)
                    if card_id is not None:
                        (created if is_new else updated).append(card_id)
                        record["card_id"] = card_id
                    applied += 1
                stored[decision.change_id] = record

            review.decisions_json = stored
            flag_modified(review, "decisions_json")
            decided_ids = {cid for cid, rec in stored.items() if rec.get("action") != "postpone"}
            if decided_ids >= set(by_id.keys()):
                review.status = "applied"
            elif stored:
                review.status = "partially_applied"
            review.updated_at = datetime.now()
            self.session.add(review)
            self.session.commit()
        except Exception as exc:
            self.session.rollback()
            logger.exception(f"[LivingBible] decide failed for review {review_id}; rolled back")
            raise RuntimeError(f"Living Bible apply failed and was rolled back: {exc}") from exc

        return BibleUpdateApplyResult(
            review_id=review_id,
            applied=applied,
            rejected=rejected,
            postponed=postponed,
            created_cards=created,
            updated_cards=updated,
            errors=errors,
            skipped=skipped,
            status=review.status,
        )

    def _apply_change(self, review: BibleUpdateReview, change: ProposedChange, decision: ChangeDecision) -> Tuple[Optional[int], bool]:
        value = decision.edited_value if decision.edited_value is not None else change.new_value
        truth_status = {
            "accept": change.truth_status if change.truth_status in ("canon", "believed", "inferred") else "canon",
            "mark_planned": "planned",
            "intentional_contradiction": None,
            "unreliable_narration": None,
        }.get(decision.action, "canon")

        if change.target_card_type == "none":
            return None, False

        card = None
        if change.target_title:
            card = self.bible.find_card_by_content_name(review.project_id, change.target_card_type, change.target_title)

        # Annotation-only actions: record history without changing the value.
        if decision.action in ("intentional_contradiction", "unreliable_narration"):
            if not card:
                raise ValueError(f"Target card not found: {change.target_card_type} '{change.target_title}'")
            self._append_history(card, change, previous=change.previous_value, new=change.previous_value,
                                 reason=f"{decision.action}: {change.summary}" + (f" ({decision.note})" if decision.note else ""))
            self._append_evidence(card, change)
            if decision.action == "intentional_contradiction" and isinstance(card.content, dict) and "truth_status" in card.content:
                card.content["truth_status"] = "disputed" if card.content.get("truth_status") == "canon" else card.content.get("truth_status")
            self._save_card(card)
            return card.id, False

        if card is None:
            if not isinstance(value, dict):
                raise ValueError(f"Target card not found for '{change.target_title}' and new_value is not a card content dict")
            new_card = self._create_card(review.project_id, change, value, truth_status)
            return new_card.id, True

        if not change.field_path:
            if isinstance(value, dict):
                previous = dict(card.content or {})
                merged = {**previous, **value}
                card.content = merged
                self._append_history(card, change, previous="(whole card)", new="(merged fields: " + ", ".join(value.keys()) + ")", reason=change.summary)
            else:
                raise ValueError("A field_path is required when new_value is not a dict")
        else:
            previous = self._set_path(card, change.field_path, value, append=self._is_append_field(change))
            self._append_history(card, change, previous=previous, new=value, reason=change.summary)

        if truth_status and isinstance(card.content, dict) and "truth_status" in card.content and change.kind != "style_drift":
            if card.content.get("truth_status") in ("planned", "inferred", "believed", None) or change.kind == "contradiction":
                card.content["truth_status"] = truth_status if change.kind != "contradiction" else "disputed"
        self._append_evidence(card, change)
        self._touch_ledger_fields(card, change, review.chapter_number)
        self._save_card(card)
        return card.id, False

    @staticmethod
    def _is_append_field(change: ProposedChange) -> bool:
        tail = change.field_path.split(".")[-1]
        return tail in ("milestones", "knowers", "exceptions", "reinforcement_chapters", "secrets", "contradictions",
                        "knowledge_restrictions", "behavioral_rules", "moral_restrictions", "voice_restrictions",
                        "secrets_between", "aliases", "important_events", "dynamic_state") or change.field_path.startswith(_DYNAMIC_INFO_PREFIX)

    def _set_path(self, card: Card, path: str, value: Any, append: bool) -> Any:
        content = dict(card.content or {})
        parts = path.split(".")
        node = content
        for part in parts[:-1]:
            nxt = node.get(part)
            if not isinstance(nxt, dict):
                nxt = {}
                node[part] = nxt
            node = nxt
        leaf = parts[-1]
        previous = node.get(leaf)
        if append:
            existing = list(previous) if isinstance(previous, list) else ([] if previous in (None, "") else [previous])
            if path.startswith(_DYNAMIC_INFO_PREFIX):
                # dynamic_info items are {id, info}; assign the next id.
                next_id = max([int(it.get("id") or 0) for it in existing if isinstance(it, dict)] + [0]) + 1
                info = value.get("info") if isinstance(value, dict) else str(value)
                existing.append({"id": next_id, "info": info})
            else:
                new_items = value if isinstance(value, list) else [value]
                for item in new_items:
                    if leaf == "knowers" and isinstance(item, dict) and item.get("entity"):
                        # One knowledge state per entity: replace the stale entry.
                        existing = [e for e in existing if not (isinstance(e, dict) and str(e.get("entity", "")).lower() == str(item["entity"]).lower())]
                    if item not in existing:
                        existing.append(item)
            node[leaf] = existing
        else:
            node[leaf] = value
        card.content = content
        return previous

    def _append_history(self, card: Card, change: ProposedChange, previous: Any, new: Any, reason: str) -> None:
        content = dict(card.content or {})
        history = list(content.get("history") or [])
        history.append(HistoryEntry(
            field=change.field_path or "(card)",
            previous=previous,
            new=new,
            chapter_number=(change.evidence[0].chapter_number if change.evidence else None),
            reason=reason,
            changed_at=_now_iso(),
            accepted_by="user",
        ).model_dump(mode="json"))
        content["history"] = history[-200:]
        card.content = content

    def _append_evidence(self, card: Card, change: ProposedChange) -> None:
        if not change.evidence or not isinstance(card.content, dict):
            return
        # Only ledger/Bible cards carry a top-level evidence list.
        if "evidence" not in card.content and "truth_status" not in card.content:
            return
        content = dict(card.content)
        evidence = list(content.get("evidence") or [])
        for ev in change.evidence[:3]:
            dumped = Evidence.model_validate(ev.model_dump()).model_dump(mode="json")
            if dumped not in evidence:
                evidence.append(dumped)
        content["evidence"] = evidence[-60:]
        if isinstance(change.confidence, (int, float)) and "confidence" in content:
            content["confidence"] = round(max(0.0, min(1.0, float(change.confidence))), 2) if content.get("truth_status") != "canon" else content.get("confidence", 1.0)
        card.content = content

    @staticmethod
    def _touch_ledger_fields(card: Card, change: ProposedChange, chapter_number: Optional[int]) -> None:
        if not isinstance(card.content, dict) or chapter_number is None:
            return
        content = card.content
        if change.kind in ("thread_advanced", "thread_opened", "thread_resolved") and "last_advanced_chapter" in content:
            content["last_advanced_chapter"] = chapter_number
            if change.kind == "thread_resolved":
                content["status"] = "resolved"
            elif change.kind == "thread_opened" and content.get("status") == "planned":
                content["status"] = "active"
                content.setdefault("opening_chapter", chapter_number)
        if change.kind == "payoff_delivered" and "status" in content and change.field_path in ("", "status", "actual_payoff"):
            content["status"] = "paid_off"
            content["payoff_chapter"] = chapter_number
        if change.kind == "promise_reinforced" and "reinforcement_chapters" in content:
            chs = list(content.get("reinforcement_chapters") or [])
            if chapter_number not in chs:
                chs.append(chapter_number)
            content["reinforcement_chapters"] = chs
            if content.get("status") == "planted":
                content["status"] = "reinforced"
        if change.kind == "world_rule" and "last_verified_chapter" in content:
            content["last_verified_chapter"] = chapter_number

    def _create_card(self, project_id: int, change: ProposedChange, value: Dict[str, Any], truth_status: Optional[str]) -> Card:
        card_type = self.session.exec(select(CardType).where(CardType.name == change.target_card_type)).first()
        if not card_type:
            raise ValueError(f"Card type not found: {change.target_card_type}")
        content = dict(value)
        defaults = _NEW_CARD_REQUIRED_DEFAULTS.get(change.target_card_type, {})
        for k, v in defaults.items():
            content.setdefault(k, v)
        entity_type = _ENTITY_TYPE_BY_CARD_TYPE.get(change.target_card_type)
        if entity_type:
            content["entity_type"] = entity_type
            content.setdefault("name", change.target_title or change.summary[:60])
        if isinstance(card_type.json_schema, dict) and "truth_status" in (card_type.json_schema.get("properties") or {}):
            content["truth_status"] = truth_status or "inferred"
            content.setdefault("confidence", change.confidence)
            content["evidence"] = [ev.model_dump(mode="json") for ev in change.evidence[:5]]
            content["history"] = [HistoryEntry(field="(card)", previous=None, new="created", chapter_number=(change.evidence[0].chapter_number if change.evidence else None), reason=change.summary, changed_at=_now_iso(), accepted_by="user").model_dump(mode="json")]
        title = change.target_title or self._title_for_new_card(change, content)
        service = CardService(self.session)
        card = service.create(CardCreate(title=title, content=content, card_type_id=card_type.id, parent_id=None), project_id, commit=False)
        return card

    @staticmethod
    def _title_for_new_card(change: ProposedChange, content: Dict[str, Any]) -> str:
        for key in ("name", "title", "fact", "setup", "rule"):
            if content.get(key):
                return _trim(content[key], 120)
        if change.target_card_type == "Relationship Arc" and content.get("character_a") and content.get("character_b"):
            a, b = sorted([str(content["character_a"]), str(content["character_b"])])
            return f"{a} ↔ {b}"
        return _trim(change.summary, 120)

    def _save_card(self, card: Card) -> None:
        card.ai_modified = True
        card.last_modified_by = "ai"
        flag_modified(card, "content")
        self.session.add(card)
        self.session.flush()
