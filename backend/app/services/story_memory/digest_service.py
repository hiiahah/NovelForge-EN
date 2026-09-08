"""Chapter Digest extraction and storage.

A digest is extracted once per chapter text (keyed by a content hash) with a
single structured LLM call and stored as a ``Chapter Digest`` card under a
``Story Memory`` folder. Re-digesting the same text is a no-op unless forced;
editing the chapter text marks the digest ``stale`` (see ``events.py``).
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.db.models import Card, CardType
from app.schemas.card import CardCreate
from app.schemas.story_memory import ChapterDigest
from app.services import prompt_service
from app.services.ai.core import llm_service
from app.services.bible.bible_service import BibleService
from app.services.card_service import CardService
from app.services.forge.textmetrics import count_units, sha256_text
from app.utils.schema_utils import filter_schema_for_ai

DIGEST_TYPE = "Chapter Digest"
FOLDER_TITLE = "Story Memory"
PROMPT_NAME = "Chapter Digest Extraction"


def _content(card: Card) -> Dict[str, Any]:
    return card.content if isinstance(card.content, dict) else {}


def _trim(text: Any, limit: int) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[: limit - 1] + "…"


def text_hash(text: Any) -> str:
    """Hash used to detect chapter edits; whitespace at the ends never counts as a change."""
    return sha256_text(str(text or "").strip())


class DigestService:
    def __init__(self, session: Session):
        self.session = session
        self.bible = BibleService(session)

    # ------------------------------------------------------------------ lookup
    def _type(self, name: str) -> CardType:
        ct = self.session.exec(select(CardType).where(CardType.name == name)).first()
        if not ct:
            raise ValueError(f"Card type not found: {name}")
        return ct

    def folder(self, project_id: int, *, create: bool = True) -> Optional[Card]:
        folder_type = self._type("Folder")
        existing = self.session.exec(
            select(Card).where(Card.project_id == project_id, Card.card_type_id == folder_type.id, Card.title == FOLDER_TITLE, Card.parent_id.is_(None))
        ).first()
        if existing or not create:
            return existing
        return CardService(self.session).create(CardCreate(title=FOLDER_TITLE, content={}, card_type_id=folder_type.id, parent_id=None), project_id, commit=False)

    def digest_cards(self, project_id: int) -> List[Card]:
        return self.bible.cards_of_type(project_id, DIGEST_TYPE)

    def digests(self, project_id: int) -> List[ChapterDigest]:
        out: List[ChapterDigest] = []
        for card in self.digest_cards(project_id):
            d = self.to_digest(card)
            if d:
                out.append(d)
        out.sort(key=lambda d: d.chapter_number)
        return out

    def fresh_digests(self, project_id: int) -> List[ChapterDigest]:
        """Only memory still bound to its chapter text; legacy hand-authored digests remain usable."""
        texts = {int(_content(c).get("chapter_number") or 0): c for c in self.chapter_text_cards(project_id)}
        result = []
        for card in self.digest_cards(project_id):
            digest = self.to_digest(card)
            if digest is None or digest.stale:
                continue
            text_card = texts.get(digest.chapter_number)
            if digest.source_hash and text_card and digest.source_hash != text_hash(_content(text_card).get("content")):
                continue
            if _content(card).get("receipt_version"):
                if text_card is None or _content(text_card).get("sync_status") != "synchronized" or digest.chapter_card_id != text_card.id:
                    continue
            result.append(digest)
        return sorted(result, key=lambda d: d.chapter_number)

    def ledger_as_of(self, project_id: int, chapter: int) -> Dict[int, Dict[str, Any]]:
        """Read earlier ledger projections without mutating today's cards."""
        result: Dict[int, Dict[str, Any]] = {}
        cards = sorted(self.digest_cards(project_id), key=lambda c: int(_content(c).get("chapter_number") or 0), reverse=True)
        for card in cards:
            c = _content(card)
            if c.get("receipt_version") and int(c.get("chapter_number") or 0) > chapter:
                for entry in c.get("ledger_snapshots") or []:
                    result[entry["card_id"]] = entry["before"]
        return result

    @staticmethod
    def to_digest(card: Card) -> Optional[ChapterDigest]:
        c = _content(card)
        if not c or "chapter_number" not in c:
            return None
        try:
            return ChapterDigest.model_validate(c)
        except Exception:
            # Tolerate hand-edited cards: keep whatever validates field by field.
            try:
                safe = {k: v for k, v in c.items() if k in ChapterDigest.model_fields}
                safe.setdefault("one_line", str(c.get("one_line") or c.get("summary") or "")[:200])
                safe.setdefault("summary", str(c.get("summary") or ""))
                return ChapterDigest.model_validate(safe)
            except Exception:
                return None

    def find_digest_card(self, project_id: int, chapter_number: int) -> Optional[Card]:
        for card in self.digest_cards(project_id):
            c = _content(card)
            try:
                if int(c.get("chapter_number") or -1) == int(chapter_number):
                    return card
            except (TypeError, ValueError):
                continue
        return None

    def chapter_text_cards(self, project_id: int) -> List[Card]:
        """Chapter Text cards with non-empty content, sorted by chapter number."""
        cards = []
        for card in self.bible.cards_of_type(project_id, "Chapter Text"):
            c = _content(card)
            if not str(c.get("content") or "").strip():
                continue
            try:
                n = int(c.get("chapter_number") or 0)
            except (TypeError, ValueError):
                continue
            if n > 0:
                cards.append((n, card))
        cards.sort(key=lambda t: (t[0], t[1].id or 0))
        return [c for _, c in cards]

    def coverage(self, project_id: int) -> Dict[str, Any]:
        """Which written chapters have (fresh) digests."""
        digests = {d.chapter_number: d for d in self.digests(project_id)}
        written: List[int] = []
        missing: List[int] = []
        stale: List[int] = []
        for card in self.chapter_text_cards(project_id):
            c = _content(card)
            n = int(c.get("chapter_number"))
            written.append(n)
            d = digests.get(n)
            if not d:
                missing.append(n)
            elif d.stale or (d.source_hash and d.source_hash != text_hash(c.get("content"))):
                stale.append(n)
        return {"written": written, "digested": sorted(digests), "missing": missing, "stale": stale}

    # ------------------------------------------------------------- extraction
    def _reference_digest(self, project_id: int, participants: List[str]) -> str:
        """Compact Bible facts so the extractor uses canonical names and knows the ledgers."""
        names = {p.lower() for p in participants}
        lines: List[str] = []
        for card in self.bible.cards_of_type(project_id, "Character Card"):
            c = _content(card)
            nm = str(c.get("name") or card.title)
            aliases = [str(a) for a in (c.get("aliases") or []) if str(a).strip()]
            if names and nm.lower() not in names and not any(a.lower() in names for a in aliases):
                continue
            lines.append(f"- character: {nm}" + (f" (aliases: {', '.join(aliases[:5])})" if aliases else ""))
        for t in ("Scene Card", "Organization Card", "Item Card"):
            for card in self.bible.cards_of_type(project_id, t)[:40]:
                c = _content(card)
                lines.append(f"- {t.replace(' Card', '').lower()}: {c.get('name') or card.title}")
        for card in self.bible.cards_of_type(project_id, "Promise Payoff"):
            c = _content(card)
            if c.get("status") in ("paid_off", "subverted", "intentionally_abandoned", "contradicted"):
                continue
            lines.append(f"- open promise: {_trim(c.get('setup') or card.title, 140)}")
        for card in self.bible.cards_of_type(project_id, "Plot Thread"):
            c = _content(card)
            if c.get("status") in ("resolved", "abandoned"):
                continue
            lines.append(f"- active thread: {card.title} — {_trim(c.get('central_question'), 120)}")
        return "\n".join(lines[:160])

    def _previous_digest_text(self, project_id: int, chapter_number: int) -> str:
        prev = None
        for d in self.digests(project_id):
            if d.chapter_number < chapter_number and (prev is None or d.chapter_number > prev.chapter_number):
                prev = d
        if not prev:
            return ""
        hooks = "; ".join(h.hook for h in prev.hooks_opened[:8])
        return (
            f"Chapter {prev.chapter_number} ending state: {prev.ending_state}\n"
            f"Open hooks after ch.{prev.chapter_number}: {hooks or '(none recorded)'}\n"
            f"Story time: {prev.story_time}"
        )

    async def digest_chapter(
        self,
        *,
        project_id: int,
        llm_config_id: int,
        text: str,
        chapter_number: int,
        volume_number: Optional[int] = None,
        title: str = "",
        chapter_card_id: Optional[int] = None,
        participants: Optional[List[str]] = None,
        force: bool = False,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        timeout: Optional[float] = None,
        commit: bool = True,
    ) -> Card:
        text = (text or "").strip()
        if not text:
            raise ValueError("Chapter text is empty")
        source_hash = text_hash(text)
        existing = self.find_digest_card(project_id, chapter_number)
        if existing and not force:
            c = _content(existing)
            if c.get("source_hash") == source_hash and not c.get("stale"):
                logger.info(f"[StoryMemory] digest for ch.{chapter_number} is fresh; skipping")
                return existing

        prompt = prompt_service.get_prompt_by_name(self.session, PROMPT_NAME)
        if not prompt or not prompt.template:
            raise ValueError(f"Prompt not found: {PROMPT_NAME}")
        schema = filter_schema_for_ai(ChapterDigest.model_json_schema())
        system_prompt = prompt_service.inject_knowledge(self.session, str(prompt.template)) + (
            "\n\nOutput strictly as JSON matching this schema:\n" + json.dumps(schema, ensure_ascii=False)
        )
        parts = [
            f"Chapter number: {chapter_number}" + (f" (volume {volume_number})" if volume_number is not None else ""),
            f"Chapter title: {title or '(untitled)'}",
            f"Known participants: {', '.join(participants) if participants else '(infer from text)'}",
            "",
            "[Canonical entities and open ledgers — use these exact names]",
            self._reference_digest(project_id, participants or []) or "(empty Bible)",
        ]
        prev = self._previous_digest_text(project_id, chapter_number)
        if prev:
            parts += ["", "[Previous chapter memory]", prev]
        parts += ["", "[Chapter text]", text]
        user_prompt = "\n".join(parts)

        logger.info(f"[StoryMemory] digesting project={project_id} ch.{chapter_number} words={count_units(text)}")
        result = await llm_service.generate_structured(
            session=self.session,
            llm_config_id=llm_config_id,
            user_prompt=user_prompt,
            output_type=ChapterDigest,
            system_prompt=system_prompt,
            temperature=temperature if temperature is not None else 0.2,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        digest = result if isinstance(result, ChapterDigest) else ChapterDigest.model_validate(result)
        digest.chapter_number = chapter_number
        if volume_number is not None:
            digest.volume_number = volume_number
        if title and not digest.title:
            digest.title = title
        digest.word_count = count_units(text)
        digest.source_hash = source_hash
        digest.chapter_card_id = chapter_card_id
        digest.digested_at = datetime.now().isoformat(timespec="seconds")
        digest.llm_config_id = llm_config_id
        digest.stale = False
        for ev in digest.evidence:
            if ev.chapter_number is None:
                ev.chapter_number = chapter_number
            if ev.quote:
                ev.quote = _trim(ev.quote, 200)
        for q in digest.quotable_lines:
            q.line = _trim(q.line, 160)
        return self.save_digest(project_id, digest, existing=existing, commit=commit)

    def save_digest(self, project_id: int, digest: ChapterDigest, *, existing: Optional[Card] = None, commit: bool = True) -> Card:
        payload = digest.model_dump(mode="json")
        card_title = f"Ch {digest.chapter_number:04d} · {digest.title or digest.one_line}"[:200]
        if existing is None:
            existing = self.find_digest_card(project_id, digest.chapter_number)
        if existing:
            existing.title = card_title
            existing.content = payload
            existing.ai_modified = True
            existing.last_modified_by = "ai"
            flag_modified(existing, "content")
            self.session.add(existing)
            card = existing
        else:
            folder = self.folder(project_id)
            card = CardService(self.session).create(
                CardCreate(title=card_title, content=payload, card_type_id=self._type(DIGEST_TYPE).id, parent_id=folder.id if folder else None),
                project_id,
                commit=False,
            )
            card.display_order = int(digest.chapter_number)
            card.ai_modified = True
            card.last_modified_by = "ai"
            self.session.add(card)
        if commit:
            self.session.commit()
            self.session.refresh(card)
        else:
            self.session.flush()
        return card

    def mark_stale(self, project_id: int, chapter_number: int, new_hash: str) -> bool:
        card = self.find_digest_card(project_id, chapter_number)
        if not card:
            return False
        c = dict(_content(card))
        if c.get("source_hash") == new_hash or c.get("stale"):
            return False
        c["stale"] = True
        card.content = c
        flag_modified(card, "content")
        self.session.add(card)
        return True

    def delete_digest(self, project_id: int, chapter_number: int) -> bool:
        card = self.find_digest_card(project_id, chapter_number)
        if not card:
            return False
        self.session.delete(card)
        self.session.commit()
        return True
