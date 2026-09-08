from __future__ import annotations

from typing import Dict, Any

# Centrally export all response/nested models that need to be exposed in OpenAPI
from app.schemas.wizard import (
    Text,
	WorldBuilding, Blueprint,
	VolumeOutline, ChapterOutline,
	SpecialAbilityResponse, OneSentence, ParagraphOverview,
	CharacterCard, SceneCard, StoryLine, StageLine, 
	Tags, WorldviewTemplate, Chapter,
  WritingGuide, ReviewResultCardContent
)
from app.schemas.entity import ConceptCard, ItemCard, OrganizationCard
from app.schemas.workflow_models import BookStageChunkPlan, BookStageFinalPlan
from app.schemas.bible import (
	StoryFoundation, ReaderContract, ThemeMap, CharacterBibleDeepening,
	RelationshipArc, WorldRule, PowerSystem,
	PlotThread, PromisePayoff, KnowledgeFact, TimelineEvent, NarrativeArchitecture,
	StyleProfile, EmotionalRhythm,
	ChapterAnalysis, LocalArcPlan, StoryStructureMap, EntityResolutionPlan,
	NarrativeGenome, OriginalityTransformation,
	Evidence, HistoryEntry, SceneAnalysis, ChapterEmotion,
)
from app.schemas.bible_update import BibleUpdateProposal
from app.schemas.story_memory import ChapterDigest, LlmContinuityFindings, StoryMemorySettings

RESPONSE_MODEL_MAP: Dict[str, Any] = {
    "Text": Text,
	'Tags': Tags,
	'SpecialAbilityResponse': SpecialAbilityResponse,
	'OneSentence': OneSentence,
	'ParagraphOverview': ParagraphOverview,
	'WorldBuilding': WorldBuilding,
	'WorldviewTemplate': WorldviewTemplate,
	'Blueprint': Blueprint,
	# Use unwrapped models
	'VolumeOutline': VolumeOutline,
 	'WritingGuide': WritingGuide,
    'ReviewResultCardContent': ReviewResultCardContent,
	'ChapterOutline': ChapterOutline,
	'Chapter': Chapter,
	# Base schema, auto-included in OpenAPI
	'CharacterCard': CharacterCard,
	'SceneCard': SceneCard,
	'OrganizationCard': OrganizationCard,
	'ItemCard': ItemCard,
	'ConceptCard': ConceptCard,
	# Explicitly export nested types for frontend field tree parsing
	'StageLine': StageLine,
	'StoryLine': StoryLine,
	# Workflow-specific structural models
	'BookStageChunkPlan': BookStageChunkPlan,
	'BookStageFinalPlan': BookStageFinalPlan,
	# --- Novel Bible 2.0 ---
	'StoryFoundation': StoryFoundation,
	'ReaderContract': ReaderContract,
	'ThemeMap': ThemeMap,
	'CharacterBibleDeepening': CharacterBibleDeepening,
	'RelationshipArc': RelationshipArc,
	'WorldRule': WorldRule,
	'PowerSystem': PowerSystem,
	'PlotThread': PlotThread,
	'PromisePayoff': PromisePayoff,
	'KnowledgeFact': KnowledgeFact,
	'TimelineEvent': TimelineEvent,
	'NarrativeArchitecture': NarrativeArchitecture,
	'StyleProfile': StyleProfile,
	'EmotionalRhythm': EmotionalRhythm,
	# --- Living Bible ---
	'BibleUpdateProposal': BibleUpdateProposal,
	# --- Reverse-engineering lab ---
	'ChapterAnalysis': ChapterAnalysis,
	'LocalArcPlan': LocalArcPlan,
	'StoryStructureMap': StoryStructureMap,
	'EntityResolutionPlan': EntityResolutionPlan,
	'NarrativeGenome': NarrativeGenome,
	'OriginalityTransformation': OriginalityTransformation,
	# --- Story Memory ---
	'ChapterDigest': ChapterDigest,
	'LlmContinuityFindings': LlmContinuityFindings,
	'StoryMemorySettings': StoryMemorySettings,
	# Nested types exported for frontend $ref resolution
	'Evidence': Evidence,
	'HistoryEntry': HistoryEntry,
	'SceneAnalysis': SceneAnalysis,
	'ChapterEmotion': ChapterEmotion,
}
