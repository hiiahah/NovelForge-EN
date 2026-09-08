"""Story Memory: per-chapter digests, rolling recap and continuity checks.

The Novel Bible answers *what is true about the world*; Story Memory answers
*what has actually happened on the page so far*. Every written chapter gets a
compact ``ChapterDigest`` (LLM-extracted once, then reused deterministically).
The ``StorySoFar`` compiler folds all digests into a tiered, budgeted recap and
a carry-forward state snapshot that is injected into chapter generation, so a
chapter 300 continuation still knows what chapter 12 established.

Digests are stored as ``Chapter Digest`` cards so they reuse the editor, DSL
and export paths like every other Bible object.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.bible import Evidence, RewardType, SceneFunction

HookType = Literal["question", "threat", "promise", "mystery", "deadline", "cliffhanger", "emotional", "reveal_pending", "other"]
StateKind = Literal["location", "possession", "injury", "power", "resource", "status", "goal", "belief", "relationship", "knowledge", "alive_dead", "other"]
Significance = Literal["minor", "notable", "major", "pivotal"]


class DigestEvent(BaseModel):
    """One thing that happened, in order."""

    summary: str = Field(description="What happened, one sentence, concrete")
    participants: List[str] = Field(default_factory=list, description="Entities involved (canonical names)")
    location: str = Field(default="", description="Where it happened")
    significance: Significance = Field(default="notable", description="How much later chapters must respect this")
    consequence: str = Field(default="", description="Direct consequence that later chapters must honour")


class EntityStateChange(BaseModel):
    """A persistent change to an entity that later chapters must respect."""

    entity: str = Field(description="Canonical entity name")
    kind: StateKind = Field(default="other", description="Kind of state")
    before: str = Field(default="", description="State before this chapter")
    after: str = Field(description="State after this chapter (this is what carries forward)")
    permanent: bool = Field(default=True, description="False for temporary states (e.g. a mood) that should expire")


class OpenHook(BaseModel):
    """A question, threat or promise the chapter leaves dangling."""

    hook: str = Field(description="The dangling element as the reader perceives it")
    hook_type: HookType = Field(default="question", description="Hook type")
    raised_by: str = Field(default="", description="Entity or event that raised it")
    expected_payoff_window: str = Field(default="", description="When the reader expects resolution (e.g. 'next chapter', 'within the arc', 'end of volume')")
    strength: Literal["weak", "medium", "strong"] = Field(default="medium", description="How much the reader will notice if it is dropped")


class ClosedHook(BaseModel):
    """A previously open hook this chapter resolved or advanced."""

    hook: str = Field(description="The hook that was addressed (quote or paraphrase of the original)")
    resolution: str = Field(description="How it was resolved / advanced")
    complete: bool = Field(default=True, description="True if fully resolved, False if only advanced")


class KnowledgeDelta(BaseModel):
    """Who learned what in this chapter (drives the prohibited-information list)."""

    entity: str = Field(description="Who learned (canonical name or 'reader')")
    learned: str = Field(description="What they learned")
    how: str = Field(default="", description="How they learned it")
    is_false_belief: bool = Field(default=False, description="True when they now believe something untrue")


class QuotableLine(BaseModel):
    """A line worth calling back later (verbatim, short)."""

    speaker: str = Field(default="", description="Speaker, if dialogue")
    line: str = Field(description="Verbatim line, <= 160 characters")
    why_memorable: str = Field(default="", description="Why it may be worth a callback")


class ChapterDigest(BaseModel):
    """Compact structured memory of one written chapter."""

    chapter_number: int = Field(description="Book-wide chapter number")
    volume_number: Optional[int] = Field(default=None, description="Volume number if known")
    title: str = Field(default="", description="Chapter title")
    pov: str = Field(default="", description="POV character (canonical name)")
    participants: List[str] = Field(default_factory=list, description="Entities present (canonical names)")
    locations: List[str] = Field(default_factory=list, description="Locations visited, in order")
    story_time: str = Field(default="", description="In-story time span covered (e.g. 'dawn to noon, day 3 of the siege')")
    time_elapsed: str = Field(default="", description="How much story time passed since the previous chapter")

    one_line: str = Field(description="The chapter in one sentence (<= 200 chars)")
    summary: str = Field(description="Dense summary, 120-250 words: what happens, what changes, how it ends")
    opening_state: str = Field(default="", description="Situation at the start")
    ending_state: str = Field(default="", description="Exact situation at the end: where everyone is, what is in motion, what the last beat was")
    last_paragraph_gist: str = Field(default="", description="Gist of the final paragraph so the next chapter can continue seamlessly")

    events: List[DigestEvent] = Field(default_factory=list, description="Events in order (5-15)")
    state_changes: List[EntityStateChange] = Field(default_factory=list, description="Persistent entity state changes")
    knowledge_deltas: List[KnowledgeDelta] = Field(default_factory=list, description="Who learned what")
    relationship_shifts: List[str] = Field(default_factory=list, description="Relationship changes, phrased 'A -> B: shift (why)'")
    hooks_opened: List[OpenHook] = Field(default_factory=list, description="Hooks left dangling at the end of this chapter")
    hooks_closed: List[ClosedHook] = Field(default_factory=list, description="Earlier hooks resolved or advanced")
    promises_made: List[str] = Field(default_factory=list, description="Explicit promises, vows, deals or deadlines made by characters")
    objects_introduced: List[str] = Field(default_factory=list, description="Named objects/items that appeared for the first time")
    named_extras: List[str] = Field(default_factory=list, description="Minor named characters introduced (to prevent silent re-invention)")
    quotable_lines: List[QuotableLine] = Field(default_factory=list, description="Up to 3 lines worth a callback")

    dominant_function: SceneFunction = Field(default="setup", description="Dominant chapter function")
    rewards_delivered: List[RewardType] = Field(default_factory=list, description="Reader rewards actually delivered")
    tension_start: int = Field(default=5, ge=0, le=10, description="Tension at the start, 0-10")
    tension_end: int = Field(default=5, ge=0, le=10, description="Tension at the end, 0-10")
    hook_strength: int = Field(default=5, ge=0, le=10, description="How strongly the ending pulls the reader forward")
    style_notes: List[str] = Field(default_factory=list, description="Notable voice/rhythm features to keep consistent (abstract, no quotes)")
    continuity_risks: List[str] = Field(default_factory=list, description="Things a careless next chapter would get wrong (e.g. 'Mira is unarmed', 'it is still night')")
    evidence: List[Evidence] = Field(default_factory=list, description="Evidence for key conclusions")

    # System fields, never generated by the model.
    word_count: int = Field(default=0, description="Word count of the digested text", json_schema_extra={"x-ai-exclude": True})
    source_hash: str = Field(default="", description="Hash of the chapter text this digest was built from", json_schema_extra={"x-ai-exclude": True})
    chapter_card_id: Optional[int] = Field(default=None, description="Chapter Text card id", json_schema_extra={"x-ai-exclude": True})
    digested_at: str = Field(default="", description="ISO timestamp", json_schema_extra={"x-ai-exclude": True})
    llm_config_id: Optional[int] = Field(default=None, description="Model used", json_schema_extra={"x-ai-exclude": True})
    stale: bool = Field(default=False, description="True when the chapter text changed after digestion", json_schema_extra={"x-ai-exclude": True})


# ---------------------------------------------------------------------------
# Story So Far (deterministic compilation output)
# ---------------------------------------------------------------------------

class CarryForwardEntity(BaseModel):
    entity: str
    location: str = ""
    states: Dict[str, str] = Field(default_factory=dict, description="kind -> latest 'after' value")
    last_seen_chapter: Optional[int] = None
    alive: bool = True


class DanglingHook(BaseModel):
    hook: str
    hook_type: str = "question"
    opened_chapter: int
    strength: str = "medium"
    expected_payoff_window: str = ""
    chapters_open: int = 0
    overdue: bool = False


class StorySoFarTier(BaseModel):
    name: Literal["recent", "mid", "distant"]
    chapter_range: List[int] = Field(default_factory=list)
    text: str = ""


class StorySoFar(BaseModel):
    """Budgeted rolling recap produced without an LLM from the digests."""

    project_id: int
    through_chapter: int
    next_chapter: int
    digested_chapters: List[int] = Field(default_factory=list)
    missing_chapters: List[int] = Field(default_factory=list, description="Written chapters with no digest")
    stale_chapters: List[int] = Field(default_factory=list, description="Digests older than their chapter text")
    tiers: List[StorySoFarTier] = Field(default_factory=list)
    carry_forward: List[CarryForwardEntity] = Field(default_factory=list)
    dangling_hooks: List[DanglingHook] = Field(default_factory=list)
    recent_knowledge: List[str] = Field(default_factory=list, description="Knowledge deltas from the recent window")
    last_ending_state: str = ""
    last_paragraph_gist: str = ""
    story_clock: str = Field(default="", description="Latest story time")
    text: str = Field(default="", description="Prompt-ready text")
    budget_chars: int = 0
    used_chars: int = 0


# ---------------------------------------------------------------------------
# Continuity Guard
# ---------------------------------------------------------------------------

class ContinuityIssue(BaseModel):
    code: str = Field(description="Stable code, e.g. prohibited_reveal, dead_entity, teleport, unknown_entity, dropped_hook")
    severity: Literal["info", "low", "medium", "high", "critical"]
    message: str
    excerpt: str = Field(default="", description="Offending text excerpt, if located")
    span: Optional[List[int]] = Field(default=None, description="[start, end] offsets in the draft")
    suggestion: str = ""
    card_id: Optional[int] = None
    source: Literal["deterministic", "llm"] = "deterministic"


class ContinuityReport(BaseModel):
    project_id: int
    chapter_number: int
    checked_chars: int
    issues: List[ContinuityIssue] = Field(default_factory=list)
    score: int = Field(default=100, ge=0, le=100, description="100 = no issues; each severity subtracts")
    verdict: Literal["clean", "review", "block"] = "clean"
    checks_run: List[str] = Field(default_factory=list)


class LlmContinuityFindings(BaseModel):
    """Structured output of the optional LLM continuity pass."""

    issues: List[ContinuityIssue] = Field(default_factory=list, description="Contradictions between the draft and the provided memory; be conservative and cite the draft text")
    honoured_hooks: List[str] = Field(default_factory=list, description="Dangling hooks this draft addresses")
    notes: List[str] = Field(default_factory=list, description="Short observations that are not contradictions")


# ---------------------------------------------------------------------------
# Next Chapter Brief (planner)
# ---------------------------------------------------------------------------

class BriefItem(BaseModel):
    kind: str
    priority: int = Field(description="Lower = more important")
    text: str
    card_id: Optional[int] = None
    reason: str = ""


class NextChapterBrief(BaseModel):
    project_id: int
    chapter_number: int
    must_address: List[BriefItem] = Field(default_factory=list)
    should_consider: List[BriefItem] = Field(default_factory=list)
    avoid: List[BriefItem] = Field(default_factory=list)
    rhythm_advice: List[str] = Field(default_factory=list)
    text: str = Field(default="", description="Prompt-ready text")


# ---------------------------------------------------------------------------
# Bible health
# ---------------------------------------------------------------------------

class HealthDimension(BaseModel):
    key: str
    label: str
    score: int = Field(ge=0, le=100)
    weight: float
    detail: str = ""
    fix_hint: str = ""


class BibleHealth(BaseModel):
    project_id: int
    score: int = Field(ge=0, le=100)
    grade: Literal["A", "B", "C", "D", "F"]
    dimensions: List[HealthDimension] = Field(default_factory=list)
    summary: str = ""


# ---------------------------------------------------------------------------
# Settings (stored as a singleton "Story Memory Settings" card, no migration)
# ---------------------------------------------------------------------------

class StoryMemorySettings(BaseModel):
    auto_digest_on_save: bool = Field(default=True, description="Digest a chapter automatically when its text is saved with enough content")
    auto_digest_min_words: int = Field(default=400, ge=50, description="Minimum words before auto-digest runs")
    digest_llm_config_id: Optional[int] = Field(default=None, description="Model for digests; falls back to the chapter's model")
    recent_window: int = Field(default=3, ge=1, le=12, description="Chapters kept at full digest detail")
    mid_window: int = Field(default=12, ge=0, le=60, description="Chapters kept as compressed one-liners after the recent window")
    recap_budget_chars: int = Field(default=7000, ge=1000, le=60000, description="Character budget for the Story So Far block")
    inject_into_continuation: bool = Field(default=True, description="Inject Story So Far into chapter continuation automatically")
    inject_brief_into_continuation: bool = Field(default=True, description="Inject the Next Chapter Brief into continuation automatically")
    guard_before_digest: bool = Field(default=False, description="Run the continuity guard before digesting a saved chapter")
    hook_overdue_chapters: int = Field(default=8, ge=1, le=100, description="Chapters after which an open hook counts as overdue")


__all__ = [
    "HookType", "StateKind", "Significance",
    "DigestEvent", "EntityStateChange", "OpenHook", "ClosedHook", "KnowledgeDelta", "QuotableLine", "ChapterDigest",
    "CarryForwardEntity", "DanglingHook", "StorySoFarTier", "StorySoFar",
    "ContinuityIssue", "ContinuityReport", "LlmContinuityFindings",
    "BriefItem", "NextChapterBrief",
    "HealthDimension", "BibleHealth",
    "StoryMemorySettings",
]
