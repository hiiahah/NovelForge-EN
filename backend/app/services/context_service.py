from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from loguru import logger
from sqlmodel import Session, select

from app.db.models import Card
from app.schemas.context import ConceptSummary, FactsStructured, ItemSummary
from app.schemas.relation_extract import CN_TO_EN_KIND
from app.services.kg_provider import get_provider
from app.utils.text_utils import truncate_text


@dataclass
class ContextAssembleParams:
	project_id: Optional[int]
	volume_number: Optional[int]
	chapter_number: Optional[int]
	participants: Optional[List[str]]
	current_draft_tail: Optional[str]
	recent_chapters_window: Optional[int] = None
	chapter_id: Optional[int] = None
	pov: Optional[str] = None
	facts_quota_chars: Optional[int] = None
	bible_quota_chars: Optional[int] = None
	relation_radius: Optional[int] = None
	edge_type_whitelist: Optional[List[str]] = None
	max_chapter_id: Optional[int] = None
	include_story_memory: bool = True
	include_chapter_brief: bool = False
	story_memory_quota_chars: Optional[int] = None


class ContextAssemblyError(RuntimeError):
	"""Raised when a context provider fails; callers must not treat it as empty context."""


@dataclass
class AssembledContext:
	facts_subgraph: str
	budget_stats: Dict[str, Any]
	facts_structured: Optional[Dict[str, Any]] = None
	# Compiled Novel Bible slice (see services/bible/context_compiler.py)
	bible_context: Optional[Dict[str, Any]] = None
	# Story So Far recap + Next Chapter Brief (see services/story_memory)
	story_memory: Optional[Dict[str, Any]] = None
	chapter_brief: Optional[Dict[str, Any]] = None

	def to_system_prompt_block(self) -> str:
		parts: List[str] = []
		if self.facts_subgraph:
			parts.append(f"[Facts Subgraph]\n{self.facts_subgraph}")
		bible_text = (self.bible_context or {}).get("text") if isinstance(self.bible_context, dict) else None
		if bible_text:
			parts.append(f"[Novel Bible]\n{bible_text}")
		memory_text = (self.story_memory or {}).get("text") if isinstance(self.story_memory, dict) else None
		if memory_text:
			parts.append(memory_text)
		brief_text = (self.chapter_brief or {}).get("text") if isinstance(self.chapter_brief, dict) else None
		if brief_text:
			parts.append(brief_text)
		return "\n\n".join(parts)


def _compose_facts_subgraph_stub() -> str:
	return "Key facts: none yet (not yet collected)"


def _clean_text(value: Any) -> str:
	if value is None:
		return ""
	return str(value).strip()


def _clean_list(value: Any) -> List[str]:
	if not isinstance(value, list):
		return []
	items: List[str] = []
	for item in value:
		text = _clean_text(item)
		if text:
			items.append(text)
	return items


def _card_entity_type(card: Card) -> str:
	content = card.content if isinstance(card.content, dict) else {}
	entity_type = _clean_text(content.get("entity_type"))
	if entity_type:
		return entity_type

	card_type_name = _clean_text(getattr(card.card_type, "name", ""))
	if "Item" in card_type_name:
		return "item"
	if "Concept" in card_type_name:
		return "concept"

	model_name = _clean_text(getattr(card, "model_name", "") or getattr(card.card_type, "model_name", ""))
	if model_name == "ItemCard":
		return "item"
	if model_name == "ConceptCard":
		return "concept"
	return ""


def _card_name(card: Card) -> str:
	content = card.content if isinstance(card.content, dict) else {}
	return _clean_text(content.get("name")) or _clean_text(card.title)


def _collect_referenced_entity_cards(
	session: Session,
	project_id: Optional[int],
	participants: List[str],
	entity_type: str,
) -> List[Card]:
	if not project_id or not participants:
		return []

	normalized_participants = {_clean_text(name).lower() for name in participants if _clean_text(name)}
	if not normalized_participants:
		return []

	cards = session.exec(select(Card).where(Card.project_id == project_id)).all()
	matched: List[Card] = []
	for card in cards:
		if _card_entity_type(card) != entity_type:
			continue
		card_name = _card_name(card)
		if card_name and card_name.lower() in normalized_participants:
			matched.append(card)

	matched.sort(key=lambda card: (card.display_order, card.id or 0))
	return matched


def _build_item_summaries(session: Session, project_id: Optional[int], participants: List[str]) -> List[Dict[str, Any]]:
	items = _collect_referenced_entity_cards(session, project_id, participants, "item")
	summaries: List[Dict[str, Any]] = []
	for card in items:
		content = card.content if isinstance(card.content, dict) else {}
		summary = ItemSummary(
			name=_card_name(card),
			category=_clean_text(content.get("category")),
			description=_clean_text(content.get("description")),
			owner_hint=_clean_text(content.get("owner_hint")) or None,
			current_state=_clean_text(content.get("current_state")) or None,
			power_or_effect=_clean_text(content.get("power_or_effect")) or None,
			constraints=_clean_text(content.get("constraints")) or None,
			important_events=_clean_list(content.get("important_events")),
		)
		summaries.append(summary.model_dump())
	return summaries


def _build_concept_summaries(session: Session, project_id: Optional[int], participants: List[str]) -> List[Dict[str, Any]]:
	concepts = _collect_referenced_entity_cards(session, project_id, participants, "concept")
	summaries: List[Dict[str, Any]] = []
	for card in concepts:
		content = card.content if isinstance(card.content, dict) else {}
		summary = ConceptSummary(
			name=_card_name(card),
			category=_clean_text(content.get("category")),
			description=_clean_text(content.get("description")),
			rule_definition=_clean_text(content.get("rule_definition")),
			cost=_clean_text(content.get("cost")) or None,
			mastery_hint=_clean_text(content.get("mastery_hint")) or None,
			known_by=_clean_list(content.get("known_by")),
			counter_relations=_clean_list(content.get("counter_relations")),
		)
		summaries.append(summary.model_dump())
	return summaries


def _character_alias_map(session: Session, project_id: Optional[int]) -> Dict[str, str]:
	"""alias/name (lowercase) -> canonical display name, from Character Cards."""
	if not project_id:
		return {}
	mapping: Dict[str, str] = {}
	cards = session.exec(select(Card).where(Card.project_id == project_id)).all()
	for card in cards:
		if _clean_text(getattr(card.card_type, "name", "")) != "Character Card":
			continue
		content = card.content if isinstance(card.content, dict) else {}
		canonical = _clean_text(content.get("name")) or _clean_text(card.title)
		if not canonical:
			continue
		mapping.setdefault(canonical.lower(), canonical)
		for alias in _clean_list(content.get("aliases")):
			mapping.setdefault(alias.lower(), canonical)
	return mapping


def _resolve_participants(session: Session, params: ContextAssembleParams) -> tuple[List[str], Optional[str], Dict[str, str]]:
	alias_map = _character_alias_map(session, params.project_id)

	def canon(name: Any) -> str:
		key = _clean_text(name)
		return alias_map.get(key.lower(), key)

	resolved: List[str] = []
	seen = set()
	for raw in params.participants or []:
		name = canon(raw)
		if name and name.lower() not in seen:
			seen.add(name.lower())
			resolved.append(name)
	pov = canon(params.pov) if params.pov else None
	if pov and pov.lower() not in seen:
		# The POV character is always part of its own chapter.
		resolved.insert(0, pov)
		seen.add(pov.lower())
	return resolved, pov, alias_map


def assemble_context(session: Session, params: ContextAssembleParams) -> AssembledContext:
	from app.core.config import settings

	cfg = settings.context
	facts_quota = int(params.facts_quota_chars or cfg.facts_quota_chars)
	bible_quota = int(params.bible_quota_chars or cfg.bible_quota_chars)
	radius = int(params.relation_radius or cfg.relation_radius)

	eff_participants, pov, alias_map = _resolve_participants(session, params)
	participant_set = {name.lower() for name in eff_participants}

	facts_text = _compose_facts_subgraph_stub()
	facts_structured: Optional[Dict[str, Any]] = None
	item_summaries = _build_item_summaries(session, params.project_id, eff_participants)
	concept_summaries = _build_concept_summaries(session, params.project_id, eff_participants)
	filtered_relation_items: List[Dict[str, Any]] = []
	fact_summaries: List[str] = []

	if eff_participants:
		try:
			provider = get_provider()
			if alias_map:
				grouped: Dict[str, List[str]] = {}
				for alias, canonical in alias_map.items():
					if alias != canonical.lower():
						grouped.setdefault(canonical, []).append(alias)
				if grouped:
					provider.ingest_aliases(params.project_id or -1, grouped)
			est_top_k = max(5, min(100, facts_quota // 100))
			sub_struct = provider.query_subgraph(
				project_id=params.project_id or -1,
				participants=eff_participants,
				radius=radius,
				edge_type_whitelist=params.edge_type_whitelist,
				top_k=est_top_k,
				max_chapter_id=params.max_chapter_id,
			)
		except Exception as exc:
			logger.error("[Context] knowledge graph query failed: {}", exc)
			raise ContextAssemblyError(f"Knowledge graph query failed: {exc}") from exc

		raw_relation_items = [it for it in (sub_struct.get("relation_summaries") or []) if isinstance(it, dict)]
		if len(eff_participants) == 1:
			# A single participant still needs their adjacent relationships.
			filtered_relation_items = [
				it for it in raw_relation_items
				if str(it.get("a", "")).lower() in participant_set or str(it.get("b", "")).lower() in participant_set
			]
		else:
			filtered_relation_items = [
				it for it in raw_relation_items
				if str(it.get("a", "")).lower() in participant_set and str(it.get("b", "")).lower() in participant_set
			]
		fact_summaries = [str(f) for f in (sub_struct.get("fact_summaries") or [])]
		if filtered_relation_items:
			lines: List[str] = ["Key facts:"]
			for it in filtered_relation_items:
				kind_cn = str(it.get("kind") or "Other")
				pred_en = CN_TO_EN_KIND.get(kind_cn, kind_cn)
				lines.append(f"- {it.get('a')} {pred_en} {it.get('b')}")
			facts_text = "\n".join(lines)
		elif fact_summaries:
			facts_text = "Key facts:\n" + "\n".join(f"- {f}" for f in fact_summaries)

	facts = truncate_text(facts_text, facts_quota, suffix="\n...[truncated]")

	# Structured facts share the facts budget: trim relation/fact lists until the
	# serialized structure fits rather than bypassing the limit.
	def _structured(rel_items: List[Dict[str, Any]], facts_list: List[str]) -> Dict[str, Any]:
		try:
			return FactsStructured(
				fact_summaries=facts_list,
				relation_summaries=[
					{
						"a": it.get("a"),
						"b": it.get("b"),
						"kind": it.get("kind"),
						"description": it.get("description"),
						"a_to_b_addressing": it.get("a_to_b_addressing"),
						"b_to_a_addressing": it.get("b_to_a_addressing"),
						"recent_dialogues": it.get("recent_dialogues") or [],
						"recent_event_summaries": it.get("recent_event_summaries") or [],
						"stance": it.get("stance"),
					}
					for it in rel_items
				],
				item_summaries=item_summaries,
				concept_summaries=concept_summaries,
			).model_dump()
		except Exception:
			return {
				"fact_summaries": facts_list,
				"relation_summaries": rel_items,
				"item_summaries": item_summaries,
				"concept_summaries": concept_summaries,
			}

	import json as _json

	rel_items = list(filtered_relation_items)
	facts_list = list(fact_summaries)
	if rel_items or facts_list or item_summaries or concept_summaries:
		facts_structured = _structured(rel_items, facts_list)
		while len(_json.dumps(facts_structured, ensure_ascii=False)) > facts_quota and (rel_items or facts_list):
			if facts_list:
				facts_list.pop()
			else:
				rel_items.pop()
			facts_structured = _structured(rel_items, facts_list)

	bible_context: Optional[Dict[str, Any]] = None
	if params.project_id:
		try:
			from app.services.bible.context_compiler import ContextCompiler
			compiled = ContextCompiler(session).compile(
				project_id=params.project_id,
				chapter_number=params.chapter_number,
				participants=eff_participants,
				pov=pov,
				budget_chars=bible_quota,
			)
			if compiled.blocks or compiled.prohibited:
				bible_context = compiled.as_dict()
		except Exception as exc:
			logger.error("[Context] Bible context compilation failed: {}", exc)
			raise ContextAssemblyError(f"Bible context compilation failed: {exc}") from exc

	# Story Memory: rolling recap of every digested chapter + next-chapter brief.
	# Degradable: a failure here logs and continues without memory rather than
	# blocking generation, because the Bible slice above is still authoritative.
	story_memory: Optional[Dict[str, Any]] = None
	chapter_brief: Optional[Dict[str, Any]] = None
	if params.project_id and params.include_story_memory:
		try:
			from app.services.story_memory.planner import NextChapterPlanner
			from app.services.story_memory.settings import get_settings
			from app.services.story_memory.story_so_far import StorySoFarCompiler

			sm_cfg = get_settings(session, params.project_id)
			recap = StorySoFarCompiler(session).compile(
				params.project_id,
				next_chapter=params.chapter_number,
				budget_chars=params.story_memory_quota_chars or sm_cfg.recap_budget_chars,
				settings=sm_cfg,
			)
			if recap.text:
				story_memory = recap.model_dump(mode="json")
			if sm_cfg.inject_brief_into_continuation or params.include_chapter_brief:
				brief = NextChapterPlanner(session).brief(params.project_id, chapter_number=params.chapter_number, participants=eff_participants, pov=pov)
				if brief.text:
					chapter_brief = brief.model_dump(mode="json")
		except Exception as exc:
			logger.warning("[Context] Story Memory compilation failed (continuing without it): {}", exc)

	return AssembledContext(
		facts_subgraph=facts,
		budget_stats={
			"facts_quota": facts_quota,
			"facts_used": len(facts),
			"bible_quota": bible_quota,
			"bible_used": (bible_context or {}).get("used_chars", 0),
			"story_memory_used": (story_memory or {}).get("used_chars", 0),
			"relation_radius": radius,
			"participants": eff_participants,
			"pov": pov,
		},
		facts_structured=facts_structured,
		bible_context=bible_context,
		story_memory=story_memory,
		chapter_brief=chapter_brief,
	)
