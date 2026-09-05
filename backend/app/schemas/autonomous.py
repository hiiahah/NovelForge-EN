"""Structured-output schemas for the autonomous novel pipeline.

These are the contracts between the orchestrator and the model roles
(storyline ideator, novel architect, chapter planner). Every schema is
versioned through ``AUTONOMOUS_SCHEMA_VERSION`` so persisted artifacts record
which shape produced them.
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

AUTONOMOUS_SCHEMA_VERSION = "autonomous-schemas-1"


# --------------------------------------------------------------- storylines

class StorylineActBeat(BaseModel):
    act: str = Field(description="Act label (e.g. 'Act I', 'Midpoint', 'Act III')")
    summary: str = Field(description="What happens in this act, concretely, 2-4 sentences")


class StorylineOption(BaseModel):
    """One original storyline option derived from the source's structural qualities only."""

    title: str = Field(description="Original working title")
    genre: str = Field(default="", description="Genre / subgenre")
    hook: str = Field(default="", description="One-sentence hook")
    premise: str = Field(default="", description="Detailed premise (120-250 words)")
    protagonist: str = Field(default="", description="Protagonist: original name, occupation/role, defining trait")
    central_desire: str = Field(default="")
    internal_flaw: str = Field(default="", description="Internal flaw or wound")
    antagonist: str = Field(default="", description="Antagonist or opposing force and its mechanism")
    setting: str = Field(default="", description="Original setting (place, era, social system)")
    central_conflict: str = Field(default="")
    stakes: str = Field(default="")
    acts: List[StorylineActBeat] = Field(default_factory=list, description="Major act progression including midpoint, crisis, climax")
    midpoint: str = Field(default="")
    crisis: str = Field(default="")
    climax: str = Field(default="", description="Climax mechanism (how the final confrontation is resolved)")
    ending: str = Field(default="")
    ending_type: str = Field(default="", description="e.g. triumphant, bittersweet, tragic, open")
    major_subplot: str = Field(default="")
    relationship_arc: str = Field(default="")
    central_mystery: str = Field(default="", description="Central question the reader wants answered")
    thematic_question: str = Field(default="")
    main_cast: List[str] = Field(default_factory=list, description="4-7 original character names with one-phrase roles")
    tone: str = Field(default="")
    pov_plan: str = Field(default="", description="POV and tense plan")
    chapter_suitability_min: int = Field(default=12, ge=3, description="Minimum chapter count that fits")
    chapter_suitability_max: int = Field(default=40, ge=3, description="Maximum chapter count that fits")
    fingerprint_usage: str = Field(default="", description="How this option uses the source fingerprint (pacing, hooks, reveal cadence) without its content")
    originality_notes: str = Field(default="", description="What was deliberately made different from the source")


class StorylineOptionSet(BaseModel):
    ideation_thinking: str = Field(default="", description="How diversity across options was ensured")
    options: List[StorylineOption] = Field(default_factory=list)


# ------------------------------------------------------------- architecture

class StoryContract(BaseModel):
    premise: str = Field(default="")
    genre_promise: str = Field(default="")
    target_audience: str = Field(default="")
    pov: str = Field(default="", description="POV system, e.g. 'first person, single narrator' or 'third limited, two alternating POVs'")
    tense: str = Field(default="past")
    tone: str = Field(default="")
    thematic_question: str = Field(default="")
    ending_contract: str = Field(default="", description="What the ending must deliver")
    prohibited_deviations: List[str] = Field(default_factory=list)
    primary_fantasy: str = Field(default="", description="Reader fantasy the book serves")
    primary_emotional_reward: str = Field(default="")
    expected_protagonist_behavior: List[str] = Field(default_factory=list)
    violations: List[str] = Field(default_factory=list, description="Things that would break the reader contract")


class ArcPhase(BaseModel):
    phase: Literal["start", "mid", "end"]
    state: str = Field(description="Character state in this phase")
    chapter_hint: int = Field(default=0, description="Approximate chapter where this phase is reached")


class CharacterSpec(BaseModel):
    name: str
    aliases: List[str] = Field(default_factory=list)
    role: str = Field(default="Supporting Character", description="Protagonist | Deuteragonist | Antagonist | Supporting Character | Minor")
    identity: str = Field(default="", description="Who they are: age, occupation, position")
    goal: str = Field(default="")
    motivation: str = Field(default="")
    fear: str = Field(default="")
    flaw: str = Field(default="")
    wound: str = Field(default="")
    secret: str = Field(default="")
    knowledge_boundaries: List[str] = Field(default_factory=list, description="Facts this character must NOT know at the start")
    voice_sentence_tendency: str = Field(default="")
    voice_tells: List[str] = Field(default_factory=list)
    forbidden_speech: List[str] = Field(default_factory=list)
    capabilities: List[str] = Field(default_factory=list)
    limitations: List[str] = Field(default_factory=list)
    possessions: List[str] = Field(default_factory=list)
    appearance: str = Field(default="")
    home_location: str = Field(default="", description="Location where the character starts (must be a listed location)")
    arc: List[ArcPhase] = Field(default_factory=list)
    introduction_chapter: int = Field(default=1)
    exit_chapter: int = Field(default=0, description="0 = present until the end")


class LocationSpec(BaseModel):
    name: str
    description: str = Field(default="")
    function_in_story: str = Field(default="")


class FactionSpec(BaseModel):
    name: str
    description: str = Field(default="")
    goal: str = Field(default="")


class ItemSpec(BaseModel):
    name: str
    description: str = Field(default="")
    owner: str = Field(default="", description="Initial owner (character name) or empty")
    significance: str = Field(default="")


class WorldRuleSpec(BaseModel):
    rule: str
    domain: str = Field(default="social", description="magic | technology | social | political | economic | physical | other")
    cost: str = Field(default="")
    limits: str = Field(default="")
    known_by: List[str] = Field(default_factory=list)


class KnowledgeFactSpec(BaseModel):
    fact: str
    is_true: bool = Field(default=True)
    knowers_at_start: List[str] = Field(default_factory=list, description="Characters who know it at chapter 1")
    reader_reveal_chapter: int = Field(default=0, description="Chapter where the reader learns it (0 = never / already known)")
    sensitivity: str = Field(default="medium", description="low | medium | high")


class RelationshipSpec(BaseModel):
    character_a: str
    character_b: str
    trust: int = Field(default=50, ge=0, le=100)
    affection: int = Field(default=50, ge=0, le=100)
    hostility: int = Field(default=0, ge=0, le=100)
    power_balance: str = Field(default="equal", description="Who holds power and why")
    private_relationship: str = Field(default="")
    unresolved_tension: str = Field(default="")
    arc_summary: str = Field(default="", description="How it should change by the end")


class PlotThreadSpec(BaseModel):
    name: str
    thread_type: str = Field(default="subplot", description="main_plot | subplot | mystery | romance | character_arc")
    central_question: str = Field(default="")
    participants: List[str] = Field(default_factory=list)
    opening_chapter: int = Field(default=1)
    resolution_chapter: int = Field(default=0)


class SetupPayoffSpec(BaseModel):
    setup: str = Field(description="What is planted")
    setup_chapter: int = Field(default=1)
    payoff: str = Field(description="How it pays off")
    payoff_chapter: int = Field(default=0)
    promise_type: str = Field(default="chekhovs_gun", description="chekhovs_gun | mystery | foreshadowing | relationship | ability")


class TimelineEventSpec(BaseModel):
    title: str
    story_time: str = Field(default="", description="Chronological position (e.g. 'Day 1, morning', 'three years before chapter 1')")
    chapter: int = Field(default=0, description="Chapter where it is narrated (0 = backstory)")
    participants: List[str] = Field(default_factory=list)
    location: str = Field(default="")
    summary: str = Field(default="")


class NovelArchitecture(BaseModel):
    """Complete original architecture generated from a selected storyline."""

    architecture_thinking: str = Field(default="", description="How the storyline was expanded and how the chapter count shaped it")
    contract: StoryContract = Field(default_factory=StoryContract)
    characters: List[CharacterSpec] = Field(default_factory=list)
    locations: List[LocationSpec] = Field(default_factory=list)
    factions: List[FactionSpec] = Field(default_factory=list)
    items: List[ItemSpec] = Field(default_factory=list)
    world_rules: List[WorldRuleSpec] = Field(default_factory=list)
    knowledge_facts: List[KnowledgeFactSpec] = Field(default_factory=list)
    relationships: List[RelationshipSpec] = Field(default_factory=list)
    plot_threads: List[PlotThreadSpec] = Field(default_factory=list)
    setups_payoffs: List[SetupPayoffSpec] = Field(default_factory=list)
    timeline: List[TimelineEventSpec] = Field(default_factory=list)
    act_plan: List[str] = Field(default_factory=list, description="One line per act stating its dramatic function and chapter range")


# ---------------------------------------------------------- chapter planning

class BlueprintBeat(BaseModel):
    function: str = Field(description="Beat function tag from the allowed list")
    description: str = Field(description="What happens, concretely, in order")
    keywords: List[str] = Field(default_factory=list, description="2-6 distinctive words a validator can find in the prose")


class ChapterBlueprint(BaseModel):
    chapter_number: int
    title: str = Field(default="")
    purpose: str = Field(default="", description="Why this chapter exists in the architecture")
    pov: str = Field(description="POV character name (must be a listed character)")
    location: str = Field(default="")
    story_time: str = Field(default="")
    opening_state: str = Field(default="")
    goal: str = Field(default="")
    conflict: str = Field(default="")
    participants: List[str] = Field(default_factory=list, description="Characters present (listed characters only)")
    beats: List[BlueprintBeat] = Field(default_factory=list, description="4-8 ordered beats")
    reveals: List[str] = Field(default_factory=list, description="Facts revealed to the reader in this chapter")
    setups: List[str] = Field(default_factory=list)
    payoffs: List[str] = Field(default_factory=list)
    character_state_transition: str = Field(default="")
    relationship_transition: str = Field(default="")
    knowledge_transition: str = Field(default="", description="Who learns what in this chapter")
    target_tension: int = Field(default=5, ge=0, le=10)
    emotional_movement: str = Field(default="", description="e.g. 'dread -> relief -> doubt'")
    closing_hook: str = Field(default="")
    allowed_outcomes: List[str] = Field(default_factory=list, description="Persistent facts this chapter may establish")
    forbidden_outcomes: List[str] = Field(default_factory=list, description="Reserved for later chapters")
    overview: str = Field(default="", description="Detailed outline paragraph (>= 100 words)")


class ChapterBlueprintBatch(BaseModel):
    planning_thinking: str = Field(default="")
    chapters: List[ChapterBlueprint] = Field(default_factory=list)


# ------------------------------------------------------------- global repair

class RepairedChapter(BaseModel):
    prose: str = Field(description="Complete corrected chapter body, prose only")
    changes: List[str] = Field(default_factory=list, description="What was changed and why")


__all__ = [
    "AUTONOMOUS_SCHEMA_VERSION", "ArcPhase", "BlueprintBeat", "ChapterBlueprint", "ChapterBlueprintBatch", "CharacterSpec",
    "FactionSpec", "ItemSpec", "KnowledgeFactSpec", "LocationSpec", "NovelArchitecture", "PlotThreadSpec", "RelationshipSpec",
    "RepairedChapter", "SetupPayoffSpec", "StoryContract", "StorylineActBeat", "StorylineOption", "StorylineOptionSet",
    "TimelineEventSpec", "WorldRuleSpec",
]
