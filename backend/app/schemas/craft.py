"""Prose Craft schemas: scene plans, subtext packets, the webnovel critic and hook analysis.

These are the structured contracts between the Forge chapter pipeline and the
craft passes in ``app.services.forge.craft``. Model-facing schemas keep system
bookkeeping out of the LLM schema with ``x-ai-exclude``.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

SYSTEM = {"x-ai-exclude": True}


# --------------------------------------------------------------------- scenes
class ScenePlan(BaseModel):
    """One narrative scene: a contiguous group of outline beats that share a place, time and dramatic question."""

    index: int = Field(description="1-based scene index")
    title: str = Field(default="", description="Working title, e.g. 'Confrontation at the gate'")
    beat_indexes: List[int] = Field(description="1-based indexes of the outline beats this scene covers, in order")
    location: str = Field(default="", description="Where the scene takes place (must be an allowed location or neutral)")
    story_time: str = Field(default="", description="Time of day / elapsed time relative to the previous scene")
    present: List[str] = Field(default_factory=list, description="Characters physically present (allowed participants only)")
    dramatic_question: str = Field(default="", description="The single question the reader wants answered by the end of the scene")
    entry_state: str = Field(default="", description="Emotional and physical state the POV enters with")
    exit_state: str = Field(default="", description="Emotional and physical state the POV leaves with; must set up the next scene")
    turn: str = Field(default="", description="What changes: a decision, revelation, reversal or escalation. Every scene must turn.")
    interiority_focus: str = Field(default="", description="What the protagonist is privately calculating, resenting, noticing or misreading during this scene")
    micro_payoff: str = Field(default="", description="The small win, deduction, verbal victory or comic beat the scene delivers (empty if none planned)")
    word_share: float = Field(default=0.0, ge=0.0, le=1.0, description="Fraction of the chapter word target this scene should take")


class SceneDecomposition(BaseModel):
    """Chapter outline expanded into 2-5 scenes."""

    planning_thinking: str = Field(default="", description="How beats were grouped and why the scene turns escalate")
    scenes: List[ScenePlan] = Field(default_factory=list)


class SceneHandoff(BaseModel):
    """Exact state carried from one drafted scene into the next (extracted deterministically from the scene prose)."""

    ending_lines: str = Field(default="", description="Last ~3 paragraphs of the previous scene, verbatim")
    last_speaker: str = Field(default="")
    open_dialogue: str = Field(default="", description="Last spoken line, if the scene ended mid-exchange")
    physical_positions: str = Field(default="", description="Who is where, holding what, facing whom")
    carried_tension: str = Field(default="", description="Unanswered question or emotion the next scene must pick up")


# -------------------------------------------------------------------- subtext
class CharacterAgenda(BaseModel):
    """What one character wants and hides in this scene; compiled from the Bible before dialogue is written."""

    name: str
    wants_from_pov: str = Field(default="", description="What this character wants from the protagonist in this scene, concretely")
    suppressing: str = Field(default="", description="The emotion or secret they are holding back")
    leverage: str = Field(default="", description="What they believe gives them power in this exchange")
    fear_in_scene: str = Field(default="", description="What they are afraid the scene will cost them")
    tactic: str = Field(default="", description="How they pursue the want: flattery, threat, silence, deflection, bargaining…")
    tell_when_lying: str = Field(default="", description="Physical or verbal tell when deceiving")
    speech: str = Field(default="", description="Cadence, sentence length, formality, honorifics, banter style")
    address_pov_as: str = Field(default="", description="How they address the protagonist")
    never_says: List[str] = Field(default_factory=list)
    relationship_now: str = Field(default="", description="Private relationship state with the protagonist as of the previous chapter")


class SubtextPacket(BaseModel):
    scene_index: int
    pov_private_agenda: str = Field(default="", description="What the protagonist wants from this scene and will not say aloud")
    pov_reads_wrong: str = Field(default="", description="Something the protagonist misreads about another character here (dramatic irony fuel)")
    agendas: List[CharacterAgenda] = Field(default_factory=list)


# --------------------------------------------------------------- voice profile
class ProtagonistVoice(BaseModel):
    """Inner-monologue register for a POV character; stored on the Character Card and injected into every scene."""

    archetype: str = Field(default="", description="e.g. cynical pragmatist, deadpan observer, reluctant genius, survivalist, scheming optimist")
    inner_register: str = Field(default="", description="How the private narration sounds vs. the spoken voice: dry, clipped, associative, mock-formal…")
    notices_first: List[str] = Field(default_factory=list, description="What this character clocks first in any room: exits, hierarchy, prices, lies, hands")
    private_humor: str = Field(default="", description="What amuses them privately that they would never say aloud")
    self_deception: str = Field(default="", description="The thing they tell themselves that the reader can see through")
    calculation_style: str = Field(default="", description="How they think through problems on the page: odds, lists, worst-case, precedent, people")
    composure_mask: str = Field(default="", description="What their outer manner shows while the inside is doing something else")
    signature_moves: List[str] = Field(default_factory=list, description="2-4 recurring interior gestures: a running tally, naming things wrong on purpose, grading people…")
    forbidden_interior: List[str] = Field(default_factory=list, description="Interior tics that would break the voice (e.g. earnest self-pity, therapy vocabulary)")


# --------------------------------------------------------------------- critic
CriticDimension = Literal["authenticity", "voice", "interiority", "dialogue", "pacing", "sensory", "hook", "payoff", "ai_tics"]


class CriticFinding(BaseModel):
    dimension: CriticDimension
    severity: Literal["critical", "high", "medium", "low"] = "medium"
    quote: str = Field(default="", description="Short verbatim quote (<= 25 words) locating the problem")
    problem: str = Field(description="What is wrong, in editor language")
    fix: str = Field(default="", description="Concrete rewrite instruction (not a rewrite)")


class CriticReport(BaseModel):
    """Adversarial webnovel editor verdict on one chapter draft."""

    scores: Dict[str, int] = Field(default_factory=dict, description="1-10 per dimension: authenticity, voice, interiority, dialogue, pacing, sensory, hook, payoff")
    overall: float = Field(default=0.0, ge=0.0, le=10.0)
    verdict: Literal["accept", "polish", "rewrite"] = "polish"
    strongest_moment: str = Field(default="", description="The best passage; protect it during polish")
    findings: List[CriticFinding] = Field(default_factory=list)
    # System-only
    source: Literal["deterministic", "model", "merged"] = Field(default="deterministic", json_schema_extra=SYSTEM)
    tic_count: int = Field(default=0, json_schema_extra=SYSTEM)


# ---------------------------------------------------------------------- hooks
HookType = Literal["crisis", "revelation", "decision", "threat_arrival", "reversal", "question", "none"]


class HookAnalysis(BaseModel):
    ending_class: str = Field(default="", description="classify_ending() result: question_hook, dialogue_hook, suspended_hook, punch_line, emotional_close, image_close, quiet_close")
    hook_type: HookType = "none"
    strength: int = Field(default=0, ge=0, le=10)
    is_soft: bool = Field(default=False, description="True when the ending fades out instead of pulling the reader forward")
    tail_excerpt: str = Field(default="")
    suggested_hook: str = Field(default="", description="Which hook type the outline's closing_hook / next chapter implies")
    micro_payoffs: List[str] = Field(default_factory=list, description="Detected wins in the chapter: deduction, tactical, verbal, comedic, status")
    payoff_missing: bool = Field(default=False)


# --------------------------------------------------------------------- report
class CraftPassRecord(BaseModel):
    name: str
    model_calls: int = 0
    changed: bool = False
    note: str = ""


class CraftReport(BaseModel):
    """Everything the craft layer did to one chapter; stored on the pipeline run."""

    version: str = "craft-1"
    mode: Literal["single_shot", "scene_by_scene"] = "single_shot"
    scenes: List[ScenePlan] = Field(default_factory=list)
    subtext: List[SubtextPacket] = Field(default_factory=list)
    critic_before: Optional[CriticReport] = None
    critic_after: Optional[CriticReport] = None
    hook_before: Optional[HookAnalysis] = None
    hook_after: Optional[HookAnalysis] = None
    passes: List[CraftPassRecord] = Field(default_factory=list)
    model_calls: int = 0
    accepted: bool = True
    reasons: List[str] = Field(default_factory=list)


__all__ = [
    "CharacterAgenda", "CraftPassRecord", "CraftReport", "CriticDimension", "CriticFinding", "CriticReport", "HookAnalysis", "HookType",
    "ProtagonistVoice", "SceneDecomposition", "SceneHandoff", "ScenePlan", "SubtextPacket",
]
