"""Card type initialization

Initialize default card types and their schema definitions.
"""

from typing import Any, Dict

from sqlmodel import Session, select
from loguru import logger

from app.core.config import settings
from app.db.models import Card, CardType, LLMConfig
from app.schemas.response_registry import RESPONSE_MODEL_MAP
from .registry import initializer


# Card types whose stored schema is upgraded additively on startup: new top-level
# properties and $defs from the code model are appended, but user edits to existing
# fields are kept. This is how legacy Character Cards gain the Bible 2.0 groups
# without BOOTSTRAP_OVERWRITE_CARD_SCHEMAS.
ADDITIVE_SCHEMA_UPGRADE_TYPES = {"Character Card"}


def _merge_schema_additively(existing: Dict[str, Any], incoming: Dict[str, Any]) -> Dict[str, Any] | None:
    if not isinstance(existing, dict) or not isinstance(incoming, dict):
        return None
    existing_props = existing.get("properties")
    incoming_props = incoming.get("properties")
    if not isinstance(existing_props, dict) or not isinstance(incoming_props, dict):
        return None
    changed = False
    merged = dict(existing)
    merged_props = dict(existing_props)
    for key, prop_schema in incoming_props.items():
        if key not in merged_props:
            merged_props[key] = prop_schema
            changed = True
    merged["properties"] = merged_props
    incoming_defs = incoming.get("$defs") or {}
    if isinstance(incoming_defs, dict) and incoming_defs:
        merged_defs = dict(existing.get("$defs") or {})
        for key, def_schema in incoming_defs.items():
            if key not in merged_defs:
                merged_defs[key] = def_schema
                changed = True
        merged["$defs"] = merged_defs
    return merged if changed else None


@initializer(name="Card Types", order=20)
def create_default_card_types(session: Session) -> None:
    """Initialize default card types

    Create all built-in card types with their schemas, AI parameter presets, etc.

    Args:
        session: database session
    """
    stage_review_context_template = (
        "Worldview Setting: @Worldview Setting.content.world_view\n"
        "Organization/Faction Setting:@type:Organization Card[previous:global].{content.name,content.entity_type,content.life_span,content.description,content.influence,content.relationship}\n"
        "Volume Main Plot:@parent.content.main_target\n"
        "Volume Side Plot:@parent.content.branch_line\n"
        "Character Card Info:@type:Character Card[previous:global].{content.name,content.life_span,content.role_type,content.born_scene,content.description,content.personality,content.core_drive,content.character_arc}\n"
        "Map/Scene Card Info:@type:Scene Card[previous].{content.name,content.description}\n"
        "Previous Stage Story Outline:@type:Stage Outline[previous:global:1].{content.stage_name,content.reference_chapter,content.analysis,content.overview,content.entity_snapshot}\n"
        "Previous Chapter Outline Summary:@type:Chapter Outline[previous:global:1].{content.title,content.overview,content.entity_list}\n"
        "Total StageCount for this volume: @parent.content.stage_count\n"
        "End-of-volume entity state snapshot:@parent.content.entity_snapshot\n"
    )

    chapter_review_context_template = (
        "Worldview Setting: @Worldview Setting.content\n"
        "Organization/Faction Setting:@type:Organization Card[index=filter:content.name in $self.content.entity_list].{content.name,content.description,content.influence,content.relationship,content.dynamic_state}\n"
        "Scene Card:@type:Scene Card[index=filter:content.name in $self.content.entity_list].{content.name,content.description,content.dynamic_state}\n"
        "Current Stage Story Outline: @parent.content.overview\n"
        "Character Card:@type:Character Card[index=filter:content.name in $self.content.entity_list].{content.name,content.role_type,content.born_scene,content.description,content.personality,content.core_drive,content.character_arc,content.dynamic_info}\n"
        "Item Card:@type:Item Card[index=filter:content.name in $self.content.entity_list].{content.name,content.category,content.description,content.current_state,content.power_or_effect}\n"
        "Concept Card:@type:Concept Card[index=filter:content.name in $self.content.entity_list].{content.name,content.category,content.description,content.rule_definition,content.mastery_hint}\n"
        "Most recent chapter text:@type:Chapter Text[previous:1].{content.title,content.chapter_number,content.content}\n"
        "Participant entity list:@self.content.entity_list\n"
        "Current chapter outline:@type:Chapter Outline[index=filter:content.volume_number = $self.content.volume_number&&content.stage_number= $self.content.stage_number&&content.chapter_number= $self.content.chapter_number].{content.title,content.overview,content.entity_list}\n"
        "Next chapter outline:@type:Chapter Outline[index=filter:content.volume_number = $self.content.volume_number && content.chapter_number = $self.content.chapter_number+1].{content.title,content.overview,content.entity_list}\n"
    )

    default_types = {
        "General Text": {"editor_component": "MarkdownTextEditor", "is_singleton": False, "is_ai_enabled": False, "default_ai_context_template": None},
        "Work Tags": {"editor_component": "TagsEditor", "is_singleton": True, "is_ai_enabled": False, "default_ai_context_template": None},
        "Special Ability": {"is_singleton": True, "default_ai_context_template": "Work Tags: @Work Tags.content"},
        "One Sentence Summary": {"is_singleton": True, "default_ai_context_template": "Work Tags: @Work Tags.content\nSpecial Ability: @Special Ability.content.special_abilities"},
        "Story Outline": {"is_singleton": True, "default_ai_context_template": "Work Tags: @Work Tags.content\nSpecial Ability: @Special Ability.content.special_abilities\nStory Summary: @One Sentence Summary.content.one_sentence"},
        "Worldview Setting": {"is_singleton": True, "default_ai_context_template": "Work Tags: @Work Tags.content\nSpecial Ability: @Special Ability.content.special_abilities\nStory Outline: @Story Outline.content.overview"},
        "Core Blueprint": {"is_singleton": True, "default_ai_context_template": "Work Tags: @Work Tags.content\nSpecial Ability: @Special Ability.content.special_abilities\nStory Outline: @Story Outline.content.overview\nWorldview Setting: @Worldview Setting.content\nOrganization/Faction Setting:@type:Organization Card[previous:global].{content.name,content.description,content.influence,content.relationship}"},
        "Volume Outline": {"default_ai_context_template": (
            "Total volumes:@Core Blueprint.content.volume_count\n"
            "Story Outline:@Story Outline.content.overview\n"
            "Work Tags:@Work Tags.content\n"
            "Worldview Setting: @Worldview Setting.content.world_view\n"
            "Organization/Faction Setting:@type:Organization Card[previous:global].{content.name,content.description,content.influence,content.relationship}\n"
            "character_card:@type:Character Card[previous]\n"
            "scene_card:@type:Scene Card[previous]\n"
            "Previous volume info: @type:Volume Outline[index=$current.volumeNumber-1].content\n"
            "Now please create the detailed outline for Volume @self.content.volume_number\n"
        )},
        "Writing Guide": {
            "is_singleton": False,
            "default_ai_context_template": (
                "Worldview Setting: @Worldview Setting.content.world_view\n"
                "Organization/Faction Setting:@type:Organization Card[previous:global].{content.name,content.entity_type,content.life_span,content.description,content.influence,content.relationship}\n"
                "Current volume main plot:@parent.content.main_target\n"
                "Current volume side plot:@parent.content.branch_line\n"
                "Stage count and end-of-volume entity state snapshot for this volume:@parent.{content.stage_count,content.entity_snapshot}\n"
                "Character Card Info:@type:Character Card[previous]\n"
                "Map/Scene Card Info:@type:Scene Card[previous]\n"
                "Please generate a writing guide for Volume @self.content.volume_number."
            )
        },
        "Stage Outline": {"default_ai_context_template": (
            "Worldview Setting: @Worldview Setting.content.world_view\n"
            "Organization/Faction Setting:@type:Organization Card[previous:global].{content.name,content.entity_type,content.life_span,content.description,content.influence,content.relationship}\n"
            "Volume main plot:@parent.content.main_target\n"
            "Volume side plot:@parent.content.branch_line\n"
            "Character Card Info:@type:Character Card[previous:global].{content.name,content.life_span,content.role_type,content.born_scene,content.description,content.personality,content.core_drive,content.character_arc}\n"
            "Map/Scene Card Info:@type:Scene Card[previous]\n"
            "Character action summary for this volume:@parent.content.character_action_list\n"
            "Previous stage story outline, ensure chapter range and plot continuity:@type:Stage Outline[previous:global:1].{content.stage_name,content.reference_chapter,content.analysis,content.overview,content.entity_snapshot}\n"
            "Previous chapter outline summary, ensure plot continuity:@type:Chapter Outline[previous:global:1].{content.overview}\n"
            "Total StageCount for this volume: @parent.content.stage_count\n"
            "Note: please make sure to converge the story along the volume main plot within @parent.content.stage_count stages, and reach the end-of-volume entity snapshot state:@parent.content.entity_snapshot\n"
            "Writing notes for this volume:@type:Writing Guide[sibling].content.content \n"
            "Now please create the detailed story outline for Stage @self.content.stage_number."
        ), "default_ai_context_template_review": stage_review_context_template},
        "Chapter Outline": {"default_ai_context_template": (
            "word_view: @Worldview Setting.content\n"
            "volume_number: @self.content.volume_number\n"
            "volume_main_target: @type:Volume Outline[index=$current.volumeNumber].content.main_target\n"
            "volume_branch_line: @type:Volume Outline[index=$current.volumeNumber].content.branch_line\n"
            "Entity action list for this volume: @parent.content.entity_action_list\n"
            "Current stage story outline: @stage:current.overview\n"
            "Current stage chapter range: @stage:current.reference_chapter\n"
            "Previous chapter outlines: @type:Chapter Outline[sibling].{content.chapter_number,content.overview}\n"
            "Please start creating the outline for Chapter @self.content.chapter_number, ensuring continuity"
        )},
        "Chapter Text": {"editor_component": "CodeMirrorEditor", "is_ai_enabled": False, "default_ai_context_template": (
            "Worldview Setting: @Worldview Setting.content\n"
            "Organization/Faction Setting:@type:Organization Card[index=filter:content.name in $self.content.entity_list].{content.name,content.description,content.influence,content.relationship,content.dynamic_state}\n"
            "Scene Card:@type:Scene Card[index=filter:content.name in $self.content.entity_list].{content.name,content.description,content.dynamic_state}\n"
            "Current stage story outline: @parent.content.overview\n"
            "Character Card:@type:Character Card[index=filter:content.name in $self.content.entity_list].{content.name,content.role_type,content.born_scene,content.description,content.personality,content.core_drive,content.character_arc,content.dynamic_info}\n"
            "Item Card:@type:Item Card[index=filter:content.name in $self.content.entity_list].{content.name,content.category,content.description,content.current_state,content.power_or_effect}\n"
            "Concept Card:@type:Concept Card[index=filter:content.name in $self.content.entity_list].{content.name,content.category,content.description,content.rule_definition,content.mastery_hint}\n"
            "Most recent chapter text, ensure plot continuity:@type:Chapter Text[previous:1].{content.title,content.chapter_number,content.content}\n"
            "Participant entity list, ensure only these entities appear in the generated content:@self.content.entity_list\n"
            "Based on Chapter @self.content.chapter_number: @self.content.title 's outline @type:Chapter Outline[index=filter:content.volume_number = $self.content.volume_number&&content.stage_number= $self.content.stage_number&&content.chapter_number= $self.content.chapter_number].{content.overview} create the chapter text content. You may appropriately expand and design plot details that do not conflict with the outline. You do not need to repeat the title in the text: @self.content.title \n"
            "Note: when writing, you must ensure the ending plot does not conflict with the next chapter's outline, and does not prematurely involve the next chapter's plot (if it exists):@type:Chapter Outline[index=filter:content.volume_number = $self.content.volume_number && content.chapter_number = $self.content.chapter_number+1].{content.title,content.overview}\n"
            "When writing, please follow the writing guide requirements:@type:Writing Guide[index=filter:content.volume_number = $self.content.volume_number].{content.content}\n"
            ), "default_ai_context_template_review": chapter_review_context_template},
        "Content Review Card": {
            "editor_component": "ReviewResultCardEditor",
            "is_ai_enabled": False,
            "default_ai_context_template": None,
            "default_ai_context_template_review": None,
        },
        "Character Card": {"default_ai_context_template": None},
        "Scene Card": {"default_ai_context_template": None},
        "Organization Card": {"default_ai_context_template": None},
        "Item Card": {"default_ai_context_template": None, "is_ai_enabled": False},
        "Concept Card": {"default_ai_context_template": None, "is_ai_enabled": False},
        "Folder": {"is_singleton": False, "is_ai_enabled": False, "default_ai_context_template": None},

        # ---------------- Novel Bible 2.0 (Novel Intelligence Studio) ----------------
        # Foundation layer: singletons that every later generation reads.
        "Story Foundation": {
            "is_singleton": True,
            "description": "Premise engine + genre contract with stress test and premise variants",
            "default_ai_context_template": (
                "Work Tags: @Work Tags.content\n"
                "Special Ability: @Special Ability.content.special_abilities\n"
                "One Sentence Summary: @One Sentence Summary.content.one_sentence\n"
                "Story Outline: @Story Outline.content.overview\n"
                "Raw idea / user notes: @self.content.raw_idea\n"
            ),
        },
        "Reader Contract": {
            "is_singleton": True,
            "description": "What the story explicitly promises readers: fantasy, rewards, cadence, violations",
            "default_ai_context_template": (
                "Work Tags: @Work Tags.content\n"
                "Story Foundation: @Story Foundation.content.{core_premise,story_promise,reader_fantasy,central_dramatic_question,unique_mechanism,emotional_core,expected_ending_experience,genre}\n"
            ),
        },
        "Theme Map": {
            "is_singleton": True,
            "description": "Theme as a dramatic argument tied to arcs, decisions and costs",
            "default_ai_context_template": (
                "Story Foundation: @Story Foundation.content.{core_premise,central_dramatic_question,thematic_argument,counter_argument,emotional_core,protagonist,main_opposition}\n"
                "Reader Contract: @Reader Contract.content.{primary_fantasy,primary_emotional_reward,expected_ending}\n"
                "Character Cards: @type:Character Card[previous:global].{content.name,content.role_type,content.core_drive,content.character_arc}\n"
            ),
        },
        "Power System": {
            "is_singleton": False,
            "description": "Structured power/magic/tech system with exploit stress tests",
            "default_ai_context_template": (
                "Work Tags: @Work Tags.content\n"
                "Special Ability: @Special Ability.content.special_abilities\n"
                "Worldview Setting: @Worldview Setting.content.world_view\n"
                "Story Foundation: @Story Foundation.content.{core_premise,unique_mechanism,stakes,escalation_potential}\n"
                "Concept Cards: @type:Concept Card[previous:global].{content.name,content.category,content.rule_definition,content.cost}\n"
            ),
        },
        "Style Profile": {
            "is_singleton": True,
            "description": "Measurable narrative voice profile used by chapter generation and review",
            "default_ai_context_template": (
                "Work Tags: @Work Tags.content\n"
                "Story Foundation: @Story Foundation.content.{core_premise,emotional_core,genre}\n"
                "Reader Contract: @Reader Contract.content.{expected_tone,primary_emotional_reward}\n"
            ),
        },
        # Narrative architecture layer: planning card that fans out into ledgers.
        "Narrative Architecture": {
            "is_singleton": True,
            "description": "Planning card: plot threads, promises, secrets, timeline and relationship arcs; fans out into ledger cards on save",
            "default_ai_context_template": (
                "Story Foundation: @Story Foundation.content.{core_premise,central_dramatic_question,protagonist_goal,main_opposition,stakes,unique_mechanism,escalation_potential}\n"
                "Reader Contract: @Reader Contract.content\n"
                "Theme Map: @Theme Map.content.{theme_question,protagonist_initial_belief,antagonist_belief,planned_movement,final_answer}\n"
                "Story Outline: @Story Outline.content.overview\n"
                "Worldview Setting: @Worldview Setting.content.world_view\n"
                "Character Cards: @type:Character Card[previous:global].{content.name,content.role_type,content.description,content.core_drive,content.character_arc,content.dramatic_design}\n"
                "Organization Cards: @type:Organization Card[previous:global].{content.name,content.description,content.relationship}\n"
                "Total volumes: @Core Blueprint.content.volume_count\n"
            ),
        },
        # Ledger card types (many per project). Not AI-generated one-by-one; created by
        # the architecture fan-out workflow, the Living Bible or the reverse-engineering lab.
        "Plot Thread": {"is_singleton": False, "is_ai_enabled": False, "description": "Trackable plot thread with milestones, urgency and neglect detection", "default_ai_context_template": None},
        "Promise Payoff": {"is_singleton": False, "is_ai_enabled": False, "description": "Promise / setup / payoff ledger entry", "default_ai_context_template": None},
        "Knowledge Fact": {"is_singleton": False, "is_ai_enabled": False, "description": "Information-reveal matrix row: who knows what and when", "default_ai_context_template": None},
        "Timeline Event": {"is_singleton": False, "is_ai_enabled": False, "description": "Timeline and causality event", "default_ai_context_template": None},
        "Relationship Arc": {"is_singleton": False, "is_ai_enabled": False, "description": "Evolving relationship state between two characters", "default_ai_context_template": None},
        "World Rule": {"is_singleton": False, "is_ai_enabled": False, "description": "One world rule with exceptions, costs and known-by list", "default_ai_context_template": None},
        # Reverse-engineering lab outputs.
        "Chapter Analysis": {"is_singleton": False, "is_ai_enabled": False, "description": "Structured analysis of one chapter of an imported novel", "default_ai_context_template": None},
        "Story Structure Map": {"is_singleton": False, "is_ai_enabled": False, "description": "Globally reconciled stage structure of an analysed novel", "default_ai_context_template": None},
        "Emotional Rhythm": {"is_singleton": False, "is_ai_enabled": False, "description": "Chapter-level emotional values and reader rewards", "default_ai_context_template": None},
        "Narrative Genome": {"is_singleton": False, "is_ai_enabled": False, "description": "Reusable story mechanisms extracted from an analysed novel", "default_ai_context_template": None},
        "Originality Transformation": {"is_singleton": False, "is_ai_enabled": False, "description": "Original premise candidates derived from abstract patterns with similarity review", "default_ai_context_template": None},
        # Forge (reverse-engineering / generation control) card types.
        "Narrative Fingerprint": {"is_singleton": True, "is_ai_enabled": False, "description": "20-layer measurable narrative fingerprint built from the imported manuscript; consumed by the chapter context compiler and style validation", "default_ai_context_template": None},
        "Abstract Mechanism": {"is_singleton": False, "is_ai_enabled": False, "description": "Entity-free narrative mechanism transferred from a source Narrative Genome after passing the Originality Firewall", "default_ai_context_template": None},
        "Chapter State Packet": {"is_singleton": False, "is_ai_enabled": False, "description": "Next Chapter State Packet written by automatic synchronization after a chapter is committed", "default_ai_context_template": None},
        # Autonomous novel pipeline.
        "Novel Architecture": {"is_singleton": True, "is_ai_enabled": False, "description": "Complete original architecture (contract, cast, world, plot graph, timeline, setup/payoff schedule, chapter allocation) generated from the selected storyline", "default_ai_context_template": None},
        "Source Analysis Record": {"is_singleton": False, "is_ai_enabled": False, "description": "Intermediate source-analysis record (entity resolution, Bible reconstruction digest) kept in the source project for provenance", "default_ai_context_template": None},
        # Story Memory (per-chapter digests + settings).
        "Chapter Digest": {"is_singleton": False, "is_ai_enabled": False, "description": "Compact structured memory of one written chapter: events, state changes, hooks, knowledge deltas and ending state; compiled into the Story So Far block for generation", "default_ai_context_template": None},
        "Story Memory Settings": {"is_singleton": True, "is_ai_enabled": False, "description": "Per-project Story Memory settings: auto-digest, recap windows and budgets, continuation injection", "default_ai_context_template": None},
        "Creative Compass": {"is_singleton": True, "is_ai_enabled": False, "description": "Author-owned original English voice, source-adaptation intent and serial direction; not story canon", "default_ai_context_template": None},
    }

    # Default AI parameter presets per type (does not include llm_config_id)
    DEFAULT_AI_PARAMS = {
        "Special Ability": {"prompt_name": "Special Ability Generation", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "One Sentence Summary": {"prompt_name": "One Sentence Summary", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Story Outline": {"prompt_name": "Paragraph Overview", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Worldview Setting": {"prompt_name": "Worldview Setting", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Core Blueprint": {"prompt_name": "Core Blueprint", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Volume Outline": {"prompt_name": "Volume Outline", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Writing Guide": {"prompt_name": "Writing Guide", "temperature": 0.6, "max_tokens": 65536, "timeout": 240},
        "Stage Outline": {"prompt_name": "Stage Outline", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Chapter Outline": {"prompt_name": "Chapter Outline", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Chapter Text": {"prompt_name": "Content Generation", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Content Review Card": None,
        "Character Card": {"prompt_name": "Character Dynamic Info Extraction", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Scene Card": {"prompt_name": "Content Generation", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Organization Card": {"prompt_name": "Relation Extraction", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Item Card": None,
        "Concept Card": None,
        # Novel Bible 2.0
        "Story Foundation": {"prompt_name": "Story Foundation", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Reader Contract": {"prompt_name": "Reader Contract", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Theme Map": {"prompt_name": "Theme Map", "temperature": 0.7, "max_tokens": 4096, "timeout": 120},
        "Power System": {"prompt_name": "Power System", "temperature": 0.6, "max_tokens": 65536, "timeout": 240},
        "Style Profile": {"prompt_name": "Style Profile", "temperature": 0.6, "max_tokens": 4096, "timeout": 120},
        "Narrative Architecture": {"prompt_name": "Narrative Architecture", "temperature": 0.7, "max_tokens": 65536, "timeout": 240},
        "Plot Thread": None,
        "Promise Payoff": None,
        "Knowledge Fact": None,
        "Timeline Event": None,
        "Relationship Arc": None,
        "World Rule": None,
        "Chapter Analysis": None,
        "Story Structure Map": None,
        "Emotional Rhythm": None,
        "Narrative Genome": None,
        "Originality Transformation": None,
        "Narrative Fingerprint": None,
        "Abstract Mechanism": None,
        "Chapter State Packet": None,
        "Novel Architecture": None,
        "Source Analysis Record": None,
        "Chapter Digest": None,
        "Story Memory Settings": None,
        "Creative Compass": None,
    }

    # Mapping from type name to built-in response model (used directly to generate json_schema)
    TYPE_TO_MODEL_KEY = {
        "General Text": "Text",
        "Work Tags": "Tags",
        "Special Ability": "SpecialAbilityResponse",
        "One Sentence Summary": "OneSentence",
        "Story Outline": "ParagraphOverview",
        "Worldview Setting": "WorldBuilding",
        "Core Blueprint": "Blueprint",
        "Volume Outline": "VolumeOutline",
        "Writing Guide": "WritingGuide",
        "Stage Outline": "StageLine",
        "Chapter Outline": "ChapterOutline",
        "Chapter Text": "Chapter",
        "Content Review Card": "ReviewResultCardContent",
        "Character Card": "CharacterCard",
        "Scene Card": "SceneCard",
        "Organization Card": "OrganizationCard",
        "Item Card": "ItemCard",
        "Concept Card": "ConceptCard",
        "Folder": "Text",
        # Novel Bible 2.0
        "Story Foundation": "StoryFoundation",
        "Reader Contract": "ReaderContract",
        "Theme Map": "ThemeMap",
        "Power System": "PowerSystem",
        "Style Profile": "StyleProfile",
        "Narrative Architecture": "NarrativeArchitecture",
        "Plot Thread": "PlotThread",
        "Promise Payoff": "PromisePayoff",
        "Knowledge Fact": "KnowledgeFact",
        "Timeline Event": "TimelineEvent",
        "Relationship Arc": "RelationshipArc",
        "World Rule": "WorldRule",
        "Chapter Analysis": "ChapterAnalysis",
        "Story Structure Map": "StoryStructureMap",
        "Emotional Rhythm": "EmotionalRhythm",
        "Narrative Genome": "NarrativeGenome",
        "Originality Transformation": "OriginalityTransformation",
        # Story Memory
        "Chapter Digest": "ChapterDigest",
        "Story Memory Settings": "StoryMemorySettings",
        "Creative Compass": "CreativeCompass",
    }

    overwrite_card_schemas = settings.bootstrap.should_overwrite_card_schemas

    existing_types = session.exec(select(CardType)).all()
    existing_type_names = {ct.name for ct in existing_types}
    existing_type_by_name = {ct.name: ct for ct in existing_types}

    # Default llm_config_id: use the first available LLM config (if any)
    default_llm = session.exec(select(LLMConfig)).first()

    for name, details in default_types.items():
        if name not in existing_type_names:
            # Store structure (json_schema) directly on the card type
            schema = None
            try:
                model_class = RESPONSE_MODEL_MAP.get(TYPE_TO_MODEL_KEY.get(name))
                if model_class:
                    schema = model_class.model_json_schema(ref_template="#/$defs/{model}")
            except Exception:
                schema = None
            # AI parameter preset (llm_config_id is selected by frontend, not specified here)
            ai_params = DEFAULT_AI_PARAMS.get(name)
            if ai_params is not None:
                # If a default LLM is available, write its ID; avoid writing 0 which the frontend can't recognize
                ai_params = {**ai_params, "llm_config_id": (default_llm.id if default_llm else None)}
            card_type = CardType(
                name=name,
                model_name=TYPE_TO_MODEL_KEY.get(name, name),
                description=details.get("description", f"Default card type for {name}"),
                json_schema=schema,
                ai_params=ai_params,
                editor_component=details.get("editor_component"),
                is_ai_enabled=details.get("is_ai_enabled", True),
                is_singleton=details.get("is_singleton", False),
                default_ai_context_template=details.get("default_ai_context_template"),
                default_ai_context_template_review=details.get("default_ai_context_template_review"),
                built_in=True,
            )
            session.add(card_type)
            logger.info(f"Created default card type: {name}")
        else:
            # Incremental update: refresh type structure and metadata
            ct = existing_type_by_name[name]
            try:
                model_class = RESPONSE_MODEL_MAP.get(TYPE_TO_MODEL_KEY.get(name))
                if model_class:
                    schema = model_class.model_json_schema(ref_template="#/$defs/{model}")
                    if ct.json_schema is None or overwrite_card_schemas:
                        ct.json_schema = schema
                    elif name in ADDITIVE_SCHEMA_UPGRADE_TYPES:
                        merged = _merge_schema_additively(ct.json_schema, schema)
                        if merged is not None:
                            ct.json_schema = merged
                            logger.info(f"Additively upgraded schema for card type: {name}")
            except Exception:
                pass
            # If ai_params is missing, fill from preset (don't overwrite user-set values)
            if getattr(ct, 'ai_params', None) is None:
                preset = DEFAULT_AI_PARAMS.get(name)
                if preset is not None:
                    ct.ai_params = {**preset, "llm_config_id": (default_llm.id if default_llm else None)}
            # If model_name is missing, fill from mapping
            if not getattr(ct, 'model_name', None):
                ct.model_name = TYPE_TO_MODEL_KEY.get(name, name)
            ct.editor_component = details.get("editor_component")
            ct.is_ai_enabled = details.get("is_ai_enabled", True)
            ct.is_singleton = details.get("is_singleton", False)
            ct.description = details.get("description", f"Default card type for {name}")
            ct.default_ai_context_template = details.get("default_ai_context_template")
            ct.default_ai_context_template_review = details.get("default_ai_context_template_review")
            ct.built_in = True

    session.flush()

    all_cards = session.exec(select(Card)).all()
    for card in all_cards:
        card_type = existing_type_by_name.get(getattr(card.card_type, "name", ""))
        if not card_type and getattr(card, "card_type_id", None):
            card_type = session.get(CardType, card.card_type_id)
        if not card_type:
            continue
        if getattr(card, "ai_context_template", None) is None:
            card.ai_context_template = getattr(card_type, "default_ai_context_template", None)
        if getattr(card, "ai_context_template_review", None) is None:
            card.ai_context_template_review = getattr(card_type, "default_ai_context_template_review", None)

    session.commit()
    logger.info(f"Default card types committed. overwrite_card_schemas={overwrite_card_schemas}")
