"""Novel Bible 2.0 structured models.

Every Bible entry shares the same provenance vocabulary so that generation,
extraction and review can reason about *how true* a fact is and *where it
came from*:

- ``truth_status`` distinguishes canon / believed / planned / inferred /
  disputed / obsolete facts. Without it, AI systems turn rumours into truth
  and plans into events.
- ``evidence`` links a conclusion back to a chapter (and optionally a short
  quote) so the user can inspect why the system believes something.
- ``history`` preserves previous values instead of destructively replacing
  evolving facts (e.g. a character's goal across volumes).

The models here are exposed as card types (see ``bootstrap/card_types.py``)
so they reuse the existing editor, context DSL, workflows and export.
"""

from __future__ import annotations

from typing import Any, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from app.schemas.craft import ProtagonistVoice


# ---------------------------------------------------------------------------
# Shared provenance vocabulary
# ---------------------------------------------------------------------------

TruthStatus = Literal["canon", "believed", "planned", "inferred", "disputed", "obsolete"]

TRUTH_STATUSES: List[str] = ["canon", "believed", "planned", "inferred", "disputed", "obsolete"]


class Evidence(BaseModel):
    """A pointer from a Bible conclusion back to the text that supports it."""

    chapter_number: Optional[int] = Field(default=None, description="Source chapter number (book-wide)")
    volume_number: Optional[int] = Field(default=None, description="Source volume number, if known")
    scene: Optional[str] = Field(default=None, description="Scene or paragraph hint inside the chapter")
    quote: Optional[str] = Field(default=None, description="Short supporting quotation (<= 200 characters, never a large excerpt)")
    note: Optional[str] = Field(default=None, description="Why this passage supports the conclusion")


class HistoryEntry(BaseModel):
    """One accepted change to an evolving field. Older values are never deleted."""

    field: str = Field(description="Field path that changed, e.g. 'core_drive' or 'trust'")
    previous: Any = Field(default=None, description="Previous value")
    new: Any = Field(default=None, description="New value")
    chapter_number: Optional[int] = Field(default=None, description="Chapter after which the change applies")
    reason: Optional[str] = Field(default=None, description="Why the value changed")
    changed_at: Optional[str] = Field(default=None, description="ISO timestamp")
    accepted_by: Literal["user", "ai", "import"] = Field(default="user", description="Who accepted the change")


class BibleEntryMixin(BaseModel):
    """Provenance fields shared by every Bible entry."""

    truth_status: TruthStatus = Field(default="planned", description="canon = objectively true in-story; believed = a character/group believes it; planned = author intent not yet written; inferred = AI inference; disputed = conflicting evidence; obsolete = superseded")
    confidence: float = Field(default=1.0, ge=0.0, le=1.0, description="Confidence 0..1 that this entry is correct")
    evidence: List[Evidence] = Field(default_factory=list, description="Supporting evidence (chapter references and short quotes)")
    history: List[HistoryEntry] = Field(
        default_factory=list,
        description="Value history, maintained by the system",
        json_schema_extra={"x-ai-exclude": True},
    )


# ---------------------------------------------------------------------------
# Story Foundation layer
# ---------------------------------------------------------------------------

class GenreContract(BaseModel):
    primary_genre: str = Field(default="", description="Primary genre")
    secondary_genres: List[str] = Field(default_factory=list, description="Secondary genres / blends")
    conventions_honored: List[str] = Field(default_factory=list, description="Mandatory reader expectations this story will honour")
    conventions_subverted: List[str] = Field(default_factory=list, description="Conventions intentionally subverted, and why")
    tone_boundaries: List[str] = Field(default_factory=list, description="Tone boundaries (what the story never becomes)")
    content_limits: List[str] = Field(default_factory=list, description="Content limits")
    ending_expectations: str = Field(default="", description="What kind of ending the genre audience expects")
    comparable_works: List[str] = Field(default_factory=list, description="Comparable works (titles only)")
    borrow_abstractly: List[str] = Field(default_factory=list, description="What may be borrowed from comparables at an abstract level (pacing, reward rhythm, role arrangement)")
    must_stay_original: List[str] = Field(default_factory=list, description="What must remain original (names, terminology, events, signature scenes, twists)")


class PremiseVariant(BaseModel):
    title: str = Field(description="Short label for this variant")
    premise: str = Field(description="The variant premise in 2-4 sentences")
    what_changes: str = Field(default="", description="What this variant changes compared to the base premise")
    strengths: List[str] = Field(default_factory=list, description="Why this variant might be stronger")
    risks: List[str] = Field(default_factory=list, description="What could go wrong with this variant")


class PremiseStressTest(BaseModel):
    protagonist_agency: str = Field(default="", description="Is the protagonist active? Who drives the plot?")
    escalation_capacity: str = Field(default="", description="Can the conflict escalate for the planned length?")
    length_support: str = Field(default="", description="Does the premise support the intended word count / volume count?")
    repeatable_conflict: str = Field(default="", description="Does the premise produce repeatable, non-identical conflict?")
    mechanism_plot_link: str = Field(default="", description="Is the unique mechanism structurally connected to the plot, or decorative?")
    reader_expectation: str = Field(default="", description="What expectation does the premise create in the first chapters?")
    abandonment_risks: List[str] = Field(default_factory=list, description="What would make a reader abandon this story?")
    generic_elements: List[str] = Field(default_factory=list, description="Elements that are currently too generic")
    hard_to_sustain: List[str] = Field(default_factory=list, description="Elements that will be difficult to sustain")
    verdict: Literal["strong", "workable", "weak"] = Field(default="workable", description="Overall verdict")
    score: int = Field(default=5, ge=1, le=10, description="Overall premise score 1-10")


class StoryFoundation(BibleEntryMixin):
    """Premise engine + genre contract: the foundation every later generation reads."""

    premise_thinking: str = Field(default="", description="Reasoning from the raw idea to the premise: what the story is really about and why it can sustain a long novel")
    raw_idea: str = Field(default="", description="The user's raw idea, kept verbatim")
    core_premise: str = Field(default="", description="Core premise in 2-4 sentences")
    story_promise: str = Field(default="", description="What the story promises the reader will experience")
    reader_fantasy: str = Field(default="", description="The reader fantasy being served")
    central_dramatic_question: str = Field(default="", description="The central dramatic question the ending must answer")
    protagonist: str = Field(default="", description="Protagonist in one line")
    protagonist_goal: str = Field(default="", description="Protagonist's concrete goal")
    main_opposition: str = Field(default="", description="Main opposition / antagonistic force")
    external_conflict: str = Field(default="", description="External conflict")
    internal_conflict: str = Field(default="", description="Internal conflict")
    stakes: str = Field(default="", description="What is lost if the protagonist fails")
    unique_mechanism: str = Field(default="", description="The unique story mechanism (system, ability, situation) and how it generates plot")
    emotional_core: str = Field(default="", description="Emotional core of the story")
    thematic_argument: str = Field(default="", description="The thematic argument the story makes")
    counter_argument: str = Field(default="", description="The contradictory thematic argument the story takes seriously")
    escalation_potential: str = Field(default="", description="How the conflict escalates across volumes")
    series_longevity: str = Field(default="", description="Series longevity potential and what sustains it")
    expected_ending_experience: str = Field(default="", description="What the ending should feel like")
    genre: GenreContract = Field(default_factory=GenreContract, description="Genre contract")
    premise_variants: List[PremiseVariant] = Field(default_factory=list, description="2-3 alternative premise variants for side-by-side comparison")
    stress_test: PremiseStressTest = Field(default_factory=PremiseStressTest, description="Premise stress test")
    originality_constraints: List[str] = Field(default_factory=list, description="Explicit originality constraints (what must not resemble known works)")


RewardType = Literal[
    "competence", "victory", "reversal", "revelation", "romance_progress", "power_gain", "status_gain",
    "revenge", "mystery_answer", "emotional_catharsis", "exploration", "humor", "intimacy", "horror_revelation",
]

REWARD_TYPES: List[str] = [
    "competence", "victory", "reversal", "revelation", "romance_progress", "power_gain", "status_gain",
    "revenge", "mystery_answer", "emotional_catharsis", "exploration", "humor", "intimacy", "horror_revelation",
]


class RewardFrequency(BaseModel):
    small_reward_every_chapters: str = Field(default="1-2", description="Expected cadence of small rewards, e.g. '1-2'")
    medium_reversal_every_chapters: str = Field(default="5-8", description="Expected cadence of medium reversals")
    major_payoff_every_chapters: str = Field(default="20-30", description="Expected cadence of major payoffs")


class ReaderContract(BibleEntryMixin):
    """What the story explicitly promises its readers. Used by outline review and the context compiler."""

    primary_fantasy: str = Field(default="", description="Primary reader fantasy")
    primary_emotional_reward: str = Field(default="", description="Primary emotional reward")
    secondary_rewards: List[str] = Field(default_factory=list, description="Secondary rewards")
    reward_types_priority: List[RewardType] = Field(default_factory=list, description="Reward types in priority order")
    reward_frequency: RewardFrequency = Field(default_factory=RewardFrequency, description="Expected reward frequency")
    progression_type: str = Field(default="", description="Expected progression type (power, status, mystery, relationship...)")
    expected_tone: str = Field(default="", description="Expected tone")
    expected_protagonist_behavior: List[str] = Field(default_factory=list, description="How the protagonist is expected to behave")
    expected_ending: str = Field(default="", description="Expected ending shape")
    violations: List[str] = Field(default_factory=list, description="Things that would violate reader expectations")


class ThematicDecision(BaseModel):
    chapter_hint: str = Field(default="", description="Where in the story this decision happens (volume/stage/chapter hint)")
    decision: str = Field(description="The decision a character makes")
    cost: str = Field(default="", description="The cost of the decision")
    thematic_meaning: str = Field(default="", description="What the decision argues thematically")


class ThemeMap(BibleEntryMixin):
    """Theme as a dramatic argument rather than a single word."""

    theme_question: str = Field(default="", description="The theme phrased as a question")
    protagonist_initial_belief: str = Field(default="", description="What the protagonist believes at the start")
    antagonist_belief: str = Field(default="", description="What the antagonist believes")
    counter_character_belief: str = Field(default="", description="What the counter-character (mentor/friend/foil) believes")
    planned_movement: str = Field(default="", description="How the protagonist's belief is planned to move")
    final_answer: str = Field(default="", description="The story's final thematic answer")
    tied_arcs: List[str] = Field(default_factory=list, description="Character arcs and plot threads that carry the theme")
    thematic_decisions: List[ThematicDecision] = Field(default_factory=list, description="Key decisions that test the theme")
    motifs: List[str] = Field(default_factory=list, description="Recurring motifs and images")


# ---------------------------------------------------------------------------
# Character Bible 2.0 (nested groups added to CharacterCard, all optional)
# ---------------------------------------------------------------------------

class CharacterDramaticDesign(BaseModel):
    external_goal: str = Field(default="", description="What the character wants (external, concrete)")
    internal_need: str = Field(default="", description="What the character actually needs")
    false_belief: str = Field(default="", description="The lie the character believes about themselves or the world")
    core_wound: str = Field(default="", description="The wound that created the false belief")
    greatest_fear: str = Field(default="", description="What they are afraid will happen")
    greatest_desire: str = Field(default="", description="What they most desire")
    secret_desire: str = Field(default="", description="A desire they would not admit")
    moral_boundary: str = Field(default="", description="The line they will not cross")
    boundary_shift_condition: str = Field(default="", description="When and why that line could move")
    breaking_point: str = Field(default="", description="What would break them")
    contradictions: List[str] = Field(default_factory=list, description="Internal contradictions that make them human")
    coping_mechanism: str = Field(default="", description="Primary coping mechanism under pressure")
    self_image: str = Field(default="", description="How they see themselves")
    public_image: str = Field(default="", description="How others see them")
    secrets: List[str] = Field(default_factory=list, description="Secrets they are hiding")
    agency_source: str = Field(default="", description="Where their power to act comes from")


class CharacterVoice(BaseModel):
    vocabulary_level: str = Field(default="", description="Vocabulary level")
    sentence_tendency: str = Field(default="", description="Sentence length and rhythm tendency")
    formality: str = Field(default="", description="Formality level and when it changes")
    rhetorical_habits: List[str] = Field(default_factory=list, description="Favourite rhetorical habits")
    topics_avoided: List[str] = Field(default_factory=list, description="Topics they avoid")
    verbal_tells: List[str] = Field(default_factory=list, description="Verbal tells")
    forms_of_address: List[str] = Field(default_factory=list, description="How they address others (and how they refuse to)")
    humor_style: str = Field(default="", description="Humour style")
    anger_style: str = Field(default="", description="How anger shows")
    deception_style: str = Field(default="", description="How they lie")
    example_lines: List[str] = Field(default_factory=list, description="2-4 short example lines of dialogue (original, not copied)")
    forbidden_speech: List[str] = Field(default_factory=list, description="Out-of-character speech that must never appear")


class CharacterCompetence(BaseModel):
    strengths: List[str] = Field(default_factory=list, description="Strengths")
    weaknesses: List[str] = Field(default_factory=list, description="Weaknesses")
    skills: List[str] = Field(default_factory=list, description="Skills")
    knowledge: List[str] = Field(default_factory=list, description="Knowledge domains")
    blind_spots: List[str] = Field(default_factory=list, description="Blind spots")
    resources: List[str] = Field(default_factory=list, description="Resources they can call upon")
    social_power: str = Field(default="", description="Social power")
    physical_power: str = Field(default="", description="Physical power")
    political_power: str = Field(default="", description="Political power")
    limits_and_costs: List[str] = Field(default_factory=list, description="Limits and costs of their power")


class ArcMilestone(BaseModel):
    stage: Literal[
        "starting_state", "inciting_pressure", "first_adaptation", "midpoint_realization",
        "regression", "crisis_choice", "transformation", "ending_state", "custom",
    ] = Field(default="custom", description="Arc beat")
    description: str = Field(description="What happens to the character at this beat")
    chapter_hint: str = Field(default="", description="Planned volume/stage/chapter hint")
    actual_chapter: Optional[int] = Field(default=None, description="Chapter where it actually happened, once written")
    status: Literal["planned", "in_progress", "done", "deviated"] = Field(default="planned", description="Planned vs actual state")


class CharacterConsistencyRules(BaseModel):
    behavioral_rules: List[str] = Field(default_factory=list, description="Behavioural rules, e.g. 'Never trusts a stranger without testing them'")
    knowledge_restrictions: List[str] = Field(default_factory=list, description="Knowledge restrictions, e.g. 'Does not know B is the traitor before chapter 48'")
    moral_restrictions: List[str] = Field(default_factory=list, description="Moral restrictions")
    voice_restrictions: List[str] = Field(default_factory=list, description="Voice restrictions")


class CharacterBibleDeepening(BaseModel):
    """Output model for the 'Character Bible Deepening' prompt (writes into an existing Character Card)."""

    name: str = Field(description="Character name, must match the existing card")
    deepening_thinking: str = Field(default="", description="How the wound, false belief, want/need and arc reinforce each other")
    dramatic_design: CharacterDramaticDesign = Field(default_factory=CharacterDramaticDesign)
    voice: CharacterVoice = Field(default_factory=CharacterVoice)
    competence: CharacterCompetence = Field(default_factory=CharacterCompetence)
    arc_milestones: List[ArcMilestone] = Field(default_factory=list, description="Planned arc milestones in order")
    consistency_rules: CharacterConsistencyRules = Field(default_factory=CharacterConsistencyRules)
    aliases: List[str] = Field(default_factory=list, description="Aliases, titles and nicknames")
    protagonist_voice: ProtagonistVoice = Field(default_factory=ProtagonistVoice, description="Inner-monologue register for when this character narrates: archetype, private register vs. spoken composure, what they notice first, private humor, self-deception, calculation style, signature interior moves, forbidden interior tics")


# ---------------------------------------------------------------------------
# Relationship Bible
# ---------------------------------------------------------------------------

class RelationshipMilestone(BaseModel):
    chapter_number: Optional[int] = Field(default=None, description="Chapter number (None if planned without a chapter)")
    event: str = Field(description="What happened between them")
    effect: str = Field(default="", description="How it shifted trust/affection/fear/dependency/power")
    planned: bool = Field(default=False, description="True if this is a planned (not yet written) milestone")


class RelationshipArc(BibleEntryMixin):
    """A relationship as an evolving state instead of a static label."""

    character_a: str = Field(description="Character A (canonical name)")
    character_b: str = Field(description="Character B (canonical name)")
    public_relationship: str = Field(default="", description="How the relationship looks to others")
    private_relationship: str = Field(default="", description="What the relationship actually is")
    a_belief_about_b: str = Field(default="", description="A's current belief about B")
    b_belief_about_a: str = Field(default="", description="B's current belief about A")
    trust: int = Field(default=50, ge=0, le=100, description="Trust 0-100 (A toward B)")
    affection: int = Field(default=50, ge=0, le=100, description="Affection 0-100")
    fear: int = Field(default=0, ge=0, le=100, description="Fear 0-100")
    dependency: int = Field(default=0, ge=0, le=100, description="Dependency 0-100")
    resentment: int = Field(default=0, ge=0, le=100, description="Resentment 0-100")
    power_balance: str = Field(default="", description="Who currently holds the advantage and why")
    unresolved_tension: str = Field(default="", description="Unresolved tension between them")
    secrets_between: List[str] = Field(default_factory=list, description="Secrets one keeps from the other")
    milestones: List[RelationshipMilestone] = Field(default_factory=list, description="Relationship milestones in story order")
    planned_next_shift: str = Field(default="", description="The next planned shift")
    actual_next_shift: str = Field(default="", description="What actually happened (filled by the living Bible)")


# ---------------------------------------------------------------------------
# World Bible 2.0 & Power System
# ---------------------------------------------------------------------------

WorldDomain = Literal[
    "geography", "politics", "social_hierarchy", "economy", "technology", "magic_power", "religion", "law",
    "history", "culture", "language", "education", "communication", "transportation", "warfare", "medicine",
    "daily_life", "customs", "taboos", "calendar", "myths", "naming", "other",
]

WORLD_DOMAINS: List[str] = [
    "geography", "politics", "social_hierarchy", "economy", "technology", "magic_power", "religion", "law",
    "history", "culture", "language", "education", "communication", "transportation", "warfare", "medicine",
    "daily_life", "customs", "taboos", "calendar", "myths", "naming", "other",
]


class WorldRule(BibleEntryMixin):
    """One rule of the world, with exceptions, costs and who knows it."""

    domain: WorldDomain = Field(default="other", description="World domain")
    rule: str = Field(description="Rule statement")
    explanation: str = Field(default="", description="Why the rule exists / how it works")
    exceptions: List[str] = Field(default_factory=list, description="Known exceptions")
    costs: str = Field(default="", description="Cost of using or obeying the rule")
    consequences_if_broken: str = Field(default="", description="What happens when it is broken")
    public_knowledge: str = Field(default="", description="What the public believes about it")
    secret_truth: str = Field(default="", description="The hidden truth, if different from public knowledge")
    known_by: List[str] = Field(default_factory=list, description="Entities that know the true rule")
    first_reveal_chapter: Optional[int] = Field(default=None, description="Chapter where the rule is first revealed to the reader")
    last_verified_chapter: Optional[int] = Field(default=None, description="Last chapter where the rule was confirmed in text")
    story_purpose: str = Field(default="", description="What the rule does for the story")
    contradictions: List[str] = Field(default_factory=list, description="Detected contradictions")


class PowerLevel(BaseModel):
    level: int = Field(description="Level index, starting at 1")
    name: str = Field(description="Level name")
    capabilities: List[str] = Field(default_factory=list, description="Capabilities at this level")
    costs: str = Field(default="", description="Costs at this level")
    typical_holders: str = Field(default="", description="Who typically holds this level")


class ExploitTest(BaseModel):
    exploit: str = Field(description="A way the power could trivially solve a major conflict")
    why_it_fails: str = Field(description="Why the exploit fails or is limited in-story")
    severity: Literal["low", "medium", "high"] = Field(default="medium", description="How badly this would break tension if unaddressed")


class DiscoveryStep(BaseModel):
    chapter_hint: str = Field(default="", description="Volume/stage/chapter hint")
    reveal: str = Field(description="What the reader/protagonist learns about the system")


class PowerSystem(BibleEntryMixin):
    """Structured power/magic/technology system with a logic stress test."""

    name: str = Field(default="", description="System name")
    source: str = Field(default="", description="Source of power")
    acquisition: str = Field(default="", description="How power is acquired")
    progression_levels: List[PowerLevel] = Field(default_factory=list, description="Progression levels")
    costs: str = Field(default="", description="General costs")
    restrictions: List[str] = Field(default_factory=list, description="Restrictions")
    counters: List[str] = Field(default_factory=list, description="Counters")
    failure_conditions: List[str] = Field(default_factory=list, description="Failure conditions")
    known_exceptions: List[str] = Field(default_factory=list, description="Known exceptions")
    false_public_beliefs: List[str] = Field(default_factory=list, description="What the public wrongly believes")
    hidden_truths: List[str] = Field(default_factory=list, description="Hidden truths about the system")
    maximum_ceiling: str = Field(default="", description="Maximum ceiling")
    social_effects: str = Field(default="", description="Social consequences")
    economic_effects: str = Field(default="", description="Economic consequences")
    political_effects: str = Field(default="", description="Political consequences")
    discovery_schedule: List[DiscoveryStep] = Field(default_factory=list, description="Discovery schedule")
    protagonist_advantage: str = Field(default="", description="Protagonist-specific advantage")
    tension_preserving_limits: List[str] = Field(default_factory=list, description="Why the advantage does not remove tension")
    exploit_tests: List[ExploitTest] = Field(default_factory=list, description="Exploit stress tests")


# ---------------------------------------------------------------------------
# Narrative Architecture layer
# ---------------------------------------------------------------------------

ThreadType = Literal[
    "main_plot", "character_arc", "relationship_arc", "mystery", "romance", "political_conflict",
    "faction_conflict", "training_progression", "survival", "comic_subplot", "thematic", "subplot",
]

ThreadStatus = Literal["planned", "active", "dormant", "resolved", "abandoned"]
Urgency = Literal["low", "medium", "high", "critical"]


class ThreadMilestone(BaseModel):
    description: str = Field(description="What happens in this milestone")
    chapter_number: Optional[int] = Field(default=None, description="Chapter (actual or planned)")
    planned: bool = Field(default=True, description="True until written")
    status: Literal["planned", "done", "skipped", "deviated"] = Field(default="planned")


class PlotThread(BibleEntryMixin):
    """A trackable plot thread object."""

    name: str = Field(description="Thread name")
    thread_type: ThreadType = Field(default="subplot", description="Thread type")
    central_question: str = Field(default="", description="The story question this thread asks")
    participants: List[str] = Field(default_factory=list, description="Participating entities")
    stakes: str = Field(default="", description="What is at stake")
    opening_chapter: Optional[int] = Field(default=None, description="Chapter where the thread opens")
    milestones: List[ThreadMilestone] = Field(default_factory=list, description="Planned and actual milestones")
    dependencies: List[str] = Field(default_factory=list, description="Other threads this depends on")
    last_advanced_chapter: Optional[int] = Field(default=None, description="Last chapter that advanced the thread")
    next_expected_chapter: Optional[int] = Field(default=None, description="Chapter by which the thread should advance next")
    planned_resolution: str = Field(default="", description="Planned resolution")
    actual_resolution: str = Field(default="", description="Actual resolution once written")
    status: ThreadStatus = Field(default="planned", description="Current status")
    urgency: Urgency = Field(default="medium", description="Urgency")
    reader_knowledge: str = Field(default="", description="What the reader knows about this thread")
    character_knowledge: str = Field(default="", description="What the participants know")


PromiseType = Literal[
    "promise", "foreshadowing", "clue", "chekhovs_gun", "question", "secret", "prophecy", "threat",
    "deal", "debt", "vow", "unresolved_emotion", "mystery",
]

PromiseStatus = Literal[
    "planted", "reinforced", "partially_answered", "misdirected", "paid_off", "subverted",
    "intentionally_abandoned", "forgotten", "contradicted",
]


class PromisePayoff(BibleEntryMixin):
    """Promise / setup / payoff ledger entry."""

    setup: str = Field(description="The setup as the reader sees it")
    promise_type: PromiseType = Field(default="foreshadowing", description="Type")
    source_chapter: Optional[int] = Field(default=None, description="Chapter where it was planted")
    participants: List[str] = Field(default_factory=list, description="Characters involved")
    reader_visibility: Literal["explicit", "subtle", "hidden"] = Field(default="subtle", description="How visible the setup is to the reader")
    intended_interpretation: str = Field(default="", description="What the reader is meant to think")
    false_interpretation: str = Field(default="", description="A misdirection the reader may follow")
    planned_payoff: str = Field(default="", description="Planned payoff")
    actual_payoff: str = Field(default="", description="Actual payoff once written")
    payoff_chapter: Optional[int] = Field(default=None, description="Chapter where it paid off")
    target_payoff_range: Optional[Tuple[int, int]] = Field(default=None, description="Target payoff chapter range [start, end]")
    reinforcement_chapters: List[int] = Field(default_factory=list, description="Chapters that reinforced the setup")
    status: PromiseStatus = Field(default="planted", description="Status")
    strength: Literal["weak", "medium", "strong"] = Field(default="medium", description="How strongly the setup binds the author")


KnowledgeStateKind = Literal["knows", "suspects", "unaware", "false_belief"]


class KnowledgeState(BaseModel):
    entity: str = Field(description="Character, faction or 'reader'")
    state: KnowledgeStateKind = Field(default="unaware", description="Knowledge state")
    false_belief: str = Field(default="", description="What they wrongly believe, if state is false_belief")
    learned_chapter: Optional[int] = Field(default=None, description="Chapter where they learned it")
    how_learned: str = Field(default="", description="How they learned it")
    knows_others_know: List[str] = Field(default_factory=list, description="Whom they know also knows")


class KnowledgeFact(BibleEntryMixin):
    """Information-reveal matrix row: who knows what, and when."""

    fact: str = Field(description="The objective fact")
    public_belief: str = Field(default="", description="What the public believes")
    reader_state: KnowledgeStateKind = Field(default="unaware", description="Reader knowledge state")
    knowers: List[KnowledgeState] = Field(default_factory=list, description="Per-entity knowledge states")
    planned_reveal_chapter: Optional[int] = Field(default=None, description="Planned reveal chapter")
    reveal_consequences: str = Field(default="", description="Consequences of the reveal")
    sensitivity: Literal["low", "medium", "high"] = Field(default="medium", description="How dangerous it is to leak this fact early")


class TimelineEvent(BibleEntryMixin):
    """Timeline and causality graph node."""

    title: str = Field(description="Event title")
    story_time: str = Field(default="", description="In-story date/time")
    order_index: int = Field(default=0, description="Sort key in story chronology")
    chapter_number: Optional[int] = Field(default=None, description="Chapter where it is narrated")
    volume_number: Optional[int] = Field(default=None, description="Volume")
    location: str = Field(default="", description="Location")
    duration: str = Field(default="", description="Duration")
    participants: List[str] = Field(default_factory=list, description="Participants")
    preconditions: List[str] = Field(default_factory=list, description="Preconditions")
    cause: str = Field(default="", description="Cause")
    action: str = Field(default="", description="What happens")
    immediate_effect: str = Field(default="", description="Immediate effect")
    delayed_effect: str = Field(default="", description="Delayed effect")
    travel_time: str = Field(default="", description="Travel time implied")
    concurrent_events: List[str] = Field(default_factory=list, description="Concurrent events")
    public_result: str = Field(default="", description="Publicly known result")
    hidden_result: str = Field(default="", description="Secret result")


class NarrativeArchitecture(BaseModel):
    """Planning output: fanned out into Plot Thread / Promise / Knowledge / Timeline / Relationship cards by workflow."""

    architecture_thinking: str = Field(default="", description="How the threads, promises, secrets and relationships interlock to deliver the reader contract")
    plot_threads: List[PlotThread] = Field(default_factory=list, description="Plot threads")
    promises: List[PromisePayoff] = Field(default_factory=list, description="Promise/payoff ledger entries")
    knowledge_facts: List[KnowledgeFact] = Field(default_factory=list, description="Secrets and knowledge facts")
    timeline_events: List[TimelineEvent] = Field(default_factory=list, description="Key timeline events")
    relationship_arcs: List[RelationshipArc] = Field(default_factory=list, description="Relationship arcs between major characters")


# ---------------------------------------------------------------------------
# Scene / chapter function map, style, emotional rhythm
# ---------------------------------------------------------------------------

SceneFunction = Literal[
    "setup", "escalation", "discovery", "reversal", "decision", "confrontation", "payoff", "recovery",
    "transition", "character_bonding", "world_revelation", "false_victory", "disaster",
]

SCENE_FUNCTIONS: List[str] = [
    "setup", "escalation", "discovery", "reversal", "decision", "confrontation", "payoff", "recovery",
    "transition", "character_bonding", "world_revelation", "false_victory", "disaster",
]


class SceneAnalysis(BaseModel):
    index: int = Field(description="Scene index within the chapter, starting at 1")
    pov: str = Field(default="", description="POV character")
    location: str = Field(default="", description="Location")
    time: str = Field(default="", description="Story time")
    goal: str = Field(default="", description="Scene goal")
    conflict: str = Field(default="", description="Conflict")
    obstacle: str = Field(default="", description="Obstacle")
    turn: str = Field(default="", description="The turn")
    outcome: str = Field(default="", description="Outcome")
    new_question: str = Field(default="", description="New question raised")
    emotional_start: str = Field(default="", description="Emotional value at start")
    emotional_end: str = Field(default="", description="Emotional value at end")
    information_revealed: List[str] = Field(default_factory=list, description="Information revealed")
    threads_advanced: List[str] = Field(default_factory=list, description="Threads advanced")
    promises_planted: List[str] = Field(default_factory=list, description="Promises planted")
    payoffs_delivered: List[str] = Field(default_factory=list, description="Payoffs delivered")
    character_state_changes: List[str] = Field(default_factory=list, description="Character state changes")
    reader_reward: List[RewardType] = Field(default_factory=list, description="Reader rewards")
    function: SceneFunction = Field(default="setup", description="Scene function")
    evidence: List[Evidence] = Field(default_factory=list, description="Evidence")


class StyleProfile(BibleEntryMixin):
    """Measurable style characteristics. Abstract technique, never imitation of a specific author."""

    pov_mode: str = Field(default="", description="POV type (first/third limited/omniscient/rotating)")
    pov_distance: str = Field(default="", description="POV distance (deep/close/medium/far)")
    tense: str = Field(default="", description="Tense")
    narrator_personality: str = Field(default="", description="Narrator personality")
    sentence_tendency: str = Field(default="", description="Average sentence tendency")
    paragraph_density: str = Field(default="", description="Paragraph density tendency")
    dialogue_balance: str = Field(default="", description="Dialogue-to-narration balance")
    description_density: str = Field(default="", description="Description density")
    introspection_density: str = Field(default="", description="Introspection density")
    action_density: str = Field(default="", description="Action density")
    humor_frequency: str = Field(default="", description="Humour frequency")
    metaphor_style: str = Field(default="", description="Metaphor style")
    sensory_priorities: List[str] = Field(default_factory=list, description="Sensory priorities in order")
    vocabulary_range: str = Field(default="", description="Vocabulary range")
    profanity_level: str = Field(default="", description="Profanity level")
    exposition_method: str = Field(default="", description="How exposition is delivered")
    chapter_ending_style: str = Field(default="", description="Chapter-ending style")
    signature_techniques: List[str] = Field(default_factory=list, description="Abstract, transferable techniques (e.g. 'short paragraph blocks during confrontation')")
    forbidden_cliches: List[str] = Field(default_factory=list, description="Forbidden clichés")
    unwanted_ai_patterns: List[str] = Field(default_factory=list, description="Undesired AI-writing patterns to avoid")


class ChapterEmotion(BaseModel):
    chapter_number: int = Field(description="Chapter number")
    tension: int = Field(default=0, ge=0, le=10)
    hope: int = Field(default=0, ge=0, le=10)
    fear: int = Field(default=0, ge=0, le=10)
    curiosity: int = Field(default=0, ge=0, le=10)
    satisfaction: int = Field(default=0, ge=0, le=10)
    intimacy: int = Field(default=0, ge=0, le=10)
    humor: int = Field(default=0, ge=0, le=10)
    wonder: int = Field(default=0, ge=0, le=10)
    grief: int = Field(default=0, ge=0, le=10)
    anger: int = Field(default=0, ge=0, le=10)
    rewards: List[RewardType] = Field(default_factory=list, description="Rewards delivered")
    dominant_function: SceneFunction = Field(default="setup", description="Dominant chapter function")
    note: str = Field(default="", description="Short note")


class EmotionalRhythm(BaseModel):
    """Chapter-level emotional rhythm and reader reward schedule."""

    chapters: List[ChapterEmotion] = Field(default_factory=list, description="Per-chapter emotional values")
    observations: List[str] = Field(default_factory=list, description="Observations about rhythm (flat stretches, climax placement, unrewarded buildup)")


# ---------------------------------------------------------------------------
# Reverse-engineering lab outputs
# ---------------------------------------------------------------------------

class CausalLink(BaseModel):
    cause: str = Field(description="Cause")
    effect: str = Field(description="Effect")


class StateChange(BaseModel):
    entity: str = Field(description="Entity")
    before: str = Field(default="", description="State before")
    after: str = Field(default="", description="State after")
    kind: Literal["status", "power", "resource", "goal", "belief", "relationship", "location", "knowledge", "other"] = Field(default="other")


class ChapterAnalysis(BaseModel):
    """Structured record of one chapter of an existing novel (replaces the 400-600 word summary)."""

    chapter_number: int = Field(description="Book-wide chapter number")
    title: str = Field(default="", description="Chapter title")
    volume: str = Field(default="", description="Volume label")
    word_count: int = Field(default=0, description="Word count", json_schema_extra={"x-ai-exclude": True})
    summary: str = Field(default="", description="Concise summary (150-300 words)")
    pov: str = Field(default="", description="POV character(s)")
    locations: List[str] = Field(default_factory=list, description="Locations")
    story_time: str = Field(default="", description="Story time")
    scenes: List[SceneAnalysis] = Field(default_factory=list, description="Scene list")
    opening_state: str = Field(default="", description="Opening state")
    chapter_goal: str = Field(default="", description="Chapter goal")
    main_conflict: str = Field(default="", description="Main conflict")
    turning_point: str = Field(default="", description="Turning point")
    ending_state: str = Field(default="", description="Ending state")
    hook: str = Field(default="", description="Ending hook")
    hook_type: str = Field(default="", description="Hook type (question, threat, reveal, decision, cliffhanger...)")
    events: List[str] = Field(default_factory=list, description="Events in order")
    causal_links: List[CausalLink] = Field(default_factory=list, description="Cause-and-effect links")
    participants: List[str] = Field(default_factory=list, description="Participating entities")
    state_changes: List[StateChange] = Field(default_factory=list, description="Character/entity state changes")
    knowledge_changes: List[str] = Field(default_factory=list, description="Who learned what")
    relationship_changes: List[str] = Field(default_factory=list, description="Relationship changes")
    threads_advanced: List[str] = Field(default_factory=list, description="Threads advanced")
    setups: List[str] = Field(default_factory=list, description="Setups / promises planted")
    payoffs: List[str] = Field(default_factory=list, description="Payoffs delivered")
    reveals: List[str] = Field(default_factory=list, description="Revelations")
    questions_opened: List[str] = Field(default_factory=list, description="Questions opened")
    questions_closed: List[str] = Field(default_factory=list, description="Questions closed")
    emotion: ChapterEmotion = Field(default_factory=lambda: ChapterEmotion(chapter_number=0), description="Emotional values")
    techniques: List[str] = Field(default_factory=list, description="Notable techniques (scene, pacing, dialogue, exposition), phrased abstractly")
    evidence: List[Evidence] = Field(default_factory=list, description="Evidence locations for key conclusions")
    # Import-time fields, never generated by the model.
    source_text: str = Field(default="", description="Imported chapter text (user-supplied manuscript)", json_schema_extra={"x-ai-exclude": True})
    source_chapter_label: Optional[int] = Field(default=None, description="Chapter number as labelled in the source file", json_schema_extra={"x-ai-exclude": True})
    analysis_status: Literal["pending", "done", "failed"] = Field(default="pending", description="Analysis status", json_schema_extra={"x-ai-exclude": True})
    card_id: Optional[int] = Field(default=None, description="Owning card id (system)", json_schema_extra={"x-ai-exclude": True})
    card_title: str = Field(default="", description="Card title (system)", json_schema_extra={"x-ai-exclude": True})


class DetectedStage(BaseModel):
    stage_number: int = Field(description="Stage index starting at 1")
    name: str = Field(description="Stage name")
    chapter_start: int = Field(description="First chapter", ge=1)
    chapter_end: int = Field(description="Last chapter", ge=1)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Boundary confidence")
    boundary_reasons: List[str] = Field(default_factory=list, description="Why the stage ends here (goal change, location change, antagonist change, status change...)")
    goal: str = Field(default="", description="Stage goal")
    conflict: str = Field(default="", description="Main conflict")
    turning_point: str = Field(default="", description="Key turning point")
    outcome: str = Field(default="", description="Outcome")
    hook: str = Field(default="", description="Hook into the next stage")
    overview: str = Field(default="", description="Stage overview (300-600 words)")


class StoryStructureMap(BaseModel):
    """Globally reconciled stage structure (chunk boundaries are never narrative boundaries)."""

    reconciliation_thinking: str = Field(default="", description="How local arc candidates were reconciled into global stages")
    stages: List[DetectedStage] = Field(default_factory=list, description="Stages covering chapters 1..N contiguously")
    volume_hints: List[str] = Field(default_factory=list, description="Suggested volume groupings of stages")


class LocalArcCandidate(BaseModel):
    name: str = Field(description="Arc name")
    chapter_start: int = Field(ge=1)
    chapter_end: int = Field(ge=1)
    boundary_signals: List[str] = Field(default_factory=list, description="Signals observed at the end boundary")
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    summary: str = Field(default="", description="Arc summary")
    open_at_end: bool = Field(default=False, description="True if the arc clearly continues past this chunk")


class LocalArcPlan(BaseModel):
    arcs: List[LocalArcCandidate] = Field(default_factory=list, description="Arc candidates within the chunk; the last may be open")


class EntityAlias(BaseModel):
    canonical: str = Field(description="Suggested canonical name")
    entity_type: Literal["character", "organization", "scene", "item", "concept"] = Field(default="character")
    aliases: List[str] = Field(default_factory=list, description="Detected aliases (titles, nicknames, translated variants)")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    chapters: List[int] = Field(default_factory=list, description="Chapters where found")
    hidden_identity: bool = Field(default=False, description="True if an alias is a secret identity revealed later")
    note: str = Field(default="", description="Reasoning")


class EntityResolutionPlan(BaseModel):
    entities: List[EntityAlias] = Field(default_factory=list, description="Canonical entities with aliases")


class GenomePattern(BaseModel):
    dimension: Literal[
        "protagonist_engine", "conflict_engine", "escalation_engine", "reward_engine", "progression_loop",
        "mystery_engine", "relationship_engine", "world_expansion_loop", "chapter_hook_engine",
        "antagonist_pattern", "arc_length_pattern", "reveal_frequency", "tension_release_pattern",
        "character_retention_engine",
    ] = Field(description="Genome dimension")
    description: str = Field(description="Pattern description, mechanism-level, never vague")
    typical_sequence: List[str] = Field(default_factory=list, description="Typical step sequence")
    average_cycle_chapters: str = Field(default="", description="Average cycle length in chapters")
    conditions: List[str] = Field(default_factory=list, description="Conditions under which the pattern is used")
    variations: List[str] = Field(default_factory=list, description="Observed variations")
    evidence: List[Evidence] = Field(default_factory=list, description="Evidence")
    why_it_works: str = Field(default="", description="Why it works for readers")
    risks: List[str] = Field(default_factory=list, description="Risks of overuse or misuse")
    transferable_abstraction: str = Field(default="", description="The abstraction that can be transferred without copying")


class NarrativeGenome(BaseModel):
    """Reusable story mechanisms of an analysed novel."""

    genome_thinking: str = Field(default="", description="How the engines interlock")
    patterns: List[GenomePattern] = Field(default_factory=list, description="Patterns by dimension")
    source_title: str = Field(default="", description="Analysed source title")


class SimilarityRisk(BaseModel):
    dimension: Literal[
        "sequence", "character_role", "twist", "world_rule", "relationship", "scene_function", "terminology", "setting",
    ] = Field(description="Similarity dimension")
    severity: Literal["low", "medium", "high"] = Field(default="medium")
    detail: str = Field(description="What still resembles the source")
    recommendation: str = Field(default="", description="What to change")


class TransformedCandidate(BaseModel):
    title: str = Field(description="Candidate title")
    premise: str = Field(description="Original premise")
    retained_principles: List[str] = Field(default_factory=list, description="Abstract principles retained")
    transformed_elements: List[str] = Field(default_factory=list, description="Surface elements replaced")
    protagonist_engine: str = Field(default="", description="Transformed protagonist engine")
    conflict_engine: str = Field(default="", description="Transformed conflict engine")
    progression_system: str = Field(default="", description="Original progression system")
    world_rules: List[str] = Field(default_factory=list, description="Original world rules")
    character_roles: List[str] = Field(default_factory=list, description="Original character role system")
    volume_stage_architecture: List[str] = Field(default_factory=list, description="Volume/stage architecture")
    reader_contract: str = Field(default="", description="Reader contract summary")
    theme_map: str = Field(default="", description="Theme map summary")
    similarity_risks: List[SimilarityRisk] = Field(default_factory=list, description="Remaining similarity risks")
    originality_score: int = Field(default=5, ge=1, le=10, description="Originality score 1-10")


class OriginalityTransformation(BaseModel):
    """Output of the Originality Transformation Studio."""

    transformation_thinking: str = Field(default="", description="How abstract patterns were preserved while surfaces were replaced")
    candidates: List[TransformedCandidate] = Field(default_factory=list, description="At least three original candidates")
    global_warnings: List[str] = Field(default_factory=list, description="Originality warnings that apply to all candidates")


__all__ = [
    "TruthStatus", "TRUTH_STATUSES", "Evidence", "HistoryEntry", "BibleEntryMixin",
    "GenreContract", "PremiseVariant", "PremiseStressTest", "StoryFoundation",
    "RewardType", "REWARD_TYPES", "RewardFrequency", "ReaderContract", "ThematicDecision", "ThemeMap",
    "CharacterDramaticDesign", "CharacterVoice", "CharacterCompetence", "ArcMilestone",
    "CharacterConsistencyRules", "CharacterBibleDeepening",
    "RelationshipMilestone", "RelationshipArc",
    "WorldDomain", "WORLD_DOMAINS", "WorldRule", "PowerLevel", "ExploitTest", "DiscoveryStep", "PowerSystem",
    "ThreadType", "ThreadStatus", "Urgency", "ThreadMilestone", "PlotThread",
    "PromiseType", "PromiseStatus", "PromisePayoff",
    "KnowledgeStateKind", "KnowledgeState", "KnowledgeFact", "TimelineEvent", "NarrativeArchitecture",
    "SceneFunction", "SCENE_FUNCTIONS", "SceneAnalysis", "StyleProfile", "ChapterEmotion", "EmotionalRhythm",
    "CausalLink", "StateChange", "ChapterAnalysis", "DetectedStage", "StoryStructureMap",
    "LocalArcCandidate", "LocalArcPlan", "EntityAlias", "EntityResolutionPlan",
    "GenomePattern", "NarrativeGenome", "SimilarityRisk", "TransformedCandidate", "OriginalityTransformation",
]
