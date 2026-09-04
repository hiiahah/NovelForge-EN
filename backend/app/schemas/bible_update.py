"""Living Bible update proposals.

After a chapter is written or imported, the "Bible Update Extraction" prompt
produces a ``BibleUpdateProposal``. Nothing in it is applied automatically:
the user reviews each change in the Bible Update Review UI and accepts,
rejects, edits or postpones it. Accepted changes are written to Bible cards
with value history preserved.
"""

from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from app.schemas.bible import Evidence, TruthStatus


ChangeKind = Literal[
    "new_fact",
    "character_state",
    "goal_change",
    "belief_change",
    "relationship_change",
    "new_entity",
    "world_rule",
    "timeline_event",
    "knowledge_change",
    "thread_opened",
    "thread_advanced",
    "thread_resolved",
    "promise_planted",
    "promise_reinforced",
    "payoff_delivered",
    "contradiction",
    "plan_deviation",
    "style_drift",
]

CHANGE_KINDS: List[str] = [
    "new_fact", "character_state", "goal_change", "belief_change", "relationship_change", "new_entity",
    "world_rule", "timeline_event", "knowledge_change", "thread_opened", "thread_advanced", "thread_resolved",
    "promise_planted", "promise_reinforced", "payoff_delivered", "contradiction", "plan_deviation", "style_drift",
]

TargetCardType = Literal[
    "Character Card", "Relationship Arc", "World Rule", "Power System", "Plot Thread", "Promise Payoff",
    "Knowledge Fact", "Timeline Event", "Scene Card", "Organization Card", "Item Card", "Concept Card",
    "Style Profile", "Story Foundation", "none",
]

Risk = Literal["low", "medium", "high"]


class ProposedChange(BaseModel):
    """One proposed change to the Bible. ``id`` is assigned by the extractor so the UI can address it."""

    id: str = Field(default="", description="Stable id assigned by the system (leave empty)", json_schema_extra={"x-ai-exclude": True})
    kind: ChangeKind = Field(description="Change kind")
    summary: str = Field(description="One-line human readable summary, e.g. 'Mira learned the gate requires royal blood'")
    target_card_type: TargetCardType = Field(default="none", description="Card type this change writes to")
    target_title: str = Field(default="", description="Title of the existing target card (canonical entity name / thread name / setup text). Empty if a new card should be created")
    field_path: str = Field(default="", description="Field path inside the target card content, e.g. 'core_drive', 'dramatic_design.external_goal', 'trust', 'status'")
    previous_value: Any = Field(default=None, description="Previous value as known from the Bible, if any")
    new_value: Any = Field(default=None, description="Proposed new value. For new cards this is the full card content dict")
    truth_status: TruthStatus = Field(default="inferred", description="canon if stated on the page, believed if a character asserts it, inferred if deduced")
    explicit: bool = Field(default=True, description="True if explicitly stated in the chapter text; False if inferred")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0, description="Confidence 0..1")
    risk: Risk = Field(default="low", description="Risk of accepting wrongly: low = additive detail; medium = changes an existing value; high = contradiction or canon rewrite")
    evidence: List[Evidence] = Field(default_factory=list, description="Evidence with short quotes")
    conflicting_evidence: List[Evidence] = Field(default_factory=list, description="Evidence that contradicts this change, if any")
    related_entities: List[str] = Field(default_factory=list, description="Entities involved")
    note: str = Field(default="", description="Extra explanation for the reviewer")


class BibleUpdateProposal(BaseModel):
    """Output of the Bible Update Extraction prompt."""

    chapter_number: Optional[int] = Field(default=None, description="Chapter analysed")
    volume_number: Optional[int] = Field(default=None, description="Volume")
    extraction_thinking: str = Field(default="", description="Brief reasoning: what changed in the story state and what did not happen that was planned")
    changes: List[ProposedChange] = Field(default_factory=list, description="Proposed changes")
    unplanned_events: List[str] = Field(default_factory=list, description="Notable events not in the outline")
    planned_but_missing: List[str] = Field(default_factory=list, description="Outline events that did not occur")


# ---------------------------------------------------------------------------
# API request / response models
# ---------------------------------------------------------------------------

class BibleUpdateExtractRequest(BaseModel):
    project_id: int
    llm_config_id: int
    text: str = Field(description="Chapter text")
    chapter_card_id: Optional[int] = Field(default=None, description="Chapter Text card id, for provenance")
    volume_number: Optional[int] = None
    chapter_number: Optional[int] = None
    participants: List[str] = Field(default_factory=list, description="Participant entity names (priority focus, not a hard limit)")
    outline_text: Optional[str] = Field(default=None, description="Chapter outline, used to detect planned-vs-actual deviations")
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None


class BibleUpdateReviewRead(BaseModel):
    id: int
    project_id: int
    chapter_card_id: Optional[int]
    volume_number: Optional[int]
    chapter_number: Optional[int]
    status: str
    proposal: BibleUpdateProposal
    decisions: Dict[str, Any]
    created_at: str
    updated_at: str


class BibleUpdateReviewListResponse(BaseModel):
    items: List[BibleUpdateReviewRead]


DecisionAction = Literal[
    "accept", "reject", "postpone", "intentional_contradiction", "unreliable_narration", "mark_planned",
]


class ChangeDecision(BaseModel):
    change_id: str
    action: DecisionAction
    edited_value: Any = Field(default=None, description="If the user edited the value before accepting")
    note: Optional[str] = None


class BibleUpdateDecideRequest(BaseModel):
    decisions: List[ChangeDecision]


class BibleUpdateApplyResult(BaseModel):
    review_id: int
    applied: int
    rejected: int
    postponed: int
    created_cards: List[int] = Field(default_factory=list)
    updated_cards: List[int] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    skipped: int = Field(default=0, description="Changes already decided earlier (idempotent retry)")
    status: str


__all__ = [
    "ChangeKind", "CHANGE_KINDS", "TargetCardType", "Risk", "ProposedChange", "BibleUpdateProposal",
    "BibleUpdateExtractRequest", "BibleUpdateReviewRead", "BibleUpdateReviewListResponse",
    "DecisionAction", "ChangeDecision", "BibleUpdateDecideRequest", "BibleUpdateApplyResult",
]
