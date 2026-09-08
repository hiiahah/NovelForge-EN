"""Prose Craft API: grade any chapter text without a model, inspect subtext packets and scene plans.

Model-driven passes run inside the Forge pipeline (``/api/forge/chapters/run``
with ``craft_preset``) and the autonomous job runner; this router exposes the
deterministic analyzers so the studio can show a live craft grade while
writing, and the reference presets so the UI does not hard-code them.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.models import Card, Project
from app.db.session import get_session
from app.schemas.craft import CriticReport, HookAnalysis, ScenePlan, SubtextPacket
from app.services.bible.bible_service import BibleService
from app.services.forge.craft import CraftOptions
from app.services.forge.craft import critic as critic_mod
from app.services.forge.craft import hooks as hooks_mod
from app.services.forge.craft import scenes as scenes_mod
from app.services.forge.craft import subtext as subtext_mod
from app.services.forge.craft import tics as tics_mod
from app.services.forge.textmetrics import count_units, detect_language

router = APIRouter()


def _project(session: Session, project_id: int) -> Project:
    p = session.get(Project, project_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


def _c(card: Optional[Card]) -> Dict[str, Any]:
    return card.content if card is not None and isinstance(card.content, dict) else {}


# ------------------------------------------------------------------- grade
class GradeRequest(BaseModel):
    text: str = Field(min_length=1)
    pov: str = Field(default="")
    closing_hook: str = Field(default="", description="Planned closing hook (from the outline), used to suggest a hook type")
    word_target: Optional[int] = Field(default=None, ge=100)
    language: Optional[str] = None


class GradeResponse(BaseModel):
    critic: CriticReport
    hook: HookAnalysis
    tics: List[Dict[str, Any]]
    tic_summary: Dict[str, Any]
    words: int
    language: str
    needs_polish: bool


@router.post("/grade", response_model=GradeResponse, summary="Deterministic webnovel critic grade for any chapter text (no model call)")
def grade(req: GradeRequest) -> GradeResponse:
    lang = req.language or detect_language(req.text)
    hook = hooks_mod.analyze_hook(req.text, closing_hook_plan=req.closing_hook, language=lang)
    report = critic_mod.deterministic_critic(req.text, hook=hook, language=lang, pov_name=req.pov, word_target=req.word_target)
    hits = tics_mod.find_tics(req.text)
    return GradeResponse(critic=report, hook=hook, tics=[h.as_dict() for h in hits], tic_summary=tics_mod.tic_summary(hits), words=count_units(req.text, lang), language=lang, needs_polish=critic_mod.needs_polish(report))


class GradeCardRequest(BaseModel):
    project_id: int
    card_id: int


@router.post("/grade/card", response_model=GradeResponse, summary="Grade a Chapter Text card by id")
def grade_card(req: GradeCardRequest, session: Session = Depends(get_session)) -> GradeResponse:
    _project(session, req.project_id)
    card = session.get(Card, req.card_id)
    if card is None or card.project_id != req.project_id:
        raise HTTPException(status_code=404, detail="Card not found")
    c = _c(card)
    text = str(c.get("content") or "")
    if not text.strip():
        raise HTTPException(status_code=400, detail="Card has no chapter text")
    closing = ""
    n = c.get("chapter_number")
    if n:
        for o in BibleService(session).cards_of_type(req.project_id, "Chapter Outline"):
            oc = _c(o)
            if int(oc.get("chapter_number") or 0) == int(n):
                closing = str(oc.get("closing_hook") or "")
                break
    return grade(GradeRequest(text=text, pov=str(c.get("pov") or ""), closing_hook=closing, word_target=c.get("word_target")))


# --------------------------------------------------------------- scene plan
class ScenePlanRequest(BaseModel):
    project_id: int
    chapter_number: int
    outline_card_id: Optional[int] = None


class ScenePlanResponse(BaseModel):
    chapter_number: int
    pov: str
    participants: List[str]
    scenes: List[ScenePlan]
    subtext: List[SubtextPacket]
    voice: str


def _resolve_outline(session: Session, project_id: int, chapter_number: int, outline_card_id: Optional[int]) -> Card:
    if outline_card_id:
        card = session.get(Card, outline_card_id)
        if card is None or card.project_id != project_id:
            raise HTTPException(status_code=404, detail="Outline card not found")
        return card
    for o in BibleService(session).cards_of_type(project_id, "Chapter Outline"):
        if int(_c(o).get("chapter_number") or 0) == chapter_number:
            return o
    raise HTTPException(status_code=404, detail=f"No Chapter Outline for chapter {chapter_number}")


@router.post("/scene-plan", response_model=ScenePlanResponse, summary="Deterministic scene decomposition + subtext packets for a chapter outline (no model call)")
def scene_plan(req: ScenePlanRequest, session: Session = Depends(get_session)) -> ScenePlanResponse:
    _project(session, req.project_id)
    outline = _resolve_outline(session, req.project_id, req.chapter_number, req.outline_card_id)
    oc = _c(outline)
    beats = [b for b in (oc.get("beats") or []) if isinstance(b, dict)]
    if not beats:
        raise HTTPException(status_code=409, detail="Outline has no ordered beats")
    pov = str(oc.get("pov") or "")
    participants = [str(p) for p in (oc.get("participants") or oc.get("entity_list") or [])]
    if pov and pov not in participants:
        participants.insert(0, pov)
    bible = BibleService(session)
    cards_by_name: Dict[str, Dict[str, Any]] = {}
    for card in bible.cards_of_type(req.project_id, "Character Card"):
        c = dict(_c(card))
        c.setdefault("name", card.title)
        cards_by_name[str(c["name"]).strip().lower()] = c
    relationships: Dict[str, Dict[str, Any]] = {}
    for card in bible.cards_of_type(req.project_id, "Relationship Arc"):
        c = _c(card)
        a, b = str(c.get("character_a") or ""), str(c.get("character_b") or "")
        if a.lower() == pov.lower() and b:
            relationships[b.lower()] = c
        elif b.lower() == pov.lower() and a:
            relationships[a.lower()] = c
    word_target = int(oc.get("word_target") or 2500)
    scenes = scenes_mod.plan_scenes(beats, participants=participants, pov=pov, word_target=word_target, closing_hook=str(oc.get("closing_hook") or ""))
    packets = [subtext_mod.build_packet(s, pov_name=pov, cards_by_name=cards_by_name, relationships=relationships) for s in scenes]
    voice = subtext_mod.render_voice(subtext_mod.voice_from_card(cards_by_name.get(pov.lower()) or {"name": pov}), pov) if pov else ""
    return ScenePlanResponse(chapter_number=req.chapter_number, pov=pov, participants=participants, scenes=scenes, subtext=packets, voice=voice)


# ------------------------------------------------------------------ presets
class PresetInfo(BaseModel):
    name: str
    label: str
    description: str
    options: Dict[str, Any]
    estimated_calls: str


PRESETS: List[PresetInfo] = [
    PresetInfo(name="off", label="Legacy single-shot", description="One drafting call, deterministic validators only. Fastest; lowest quality.", options=CraftOptions.off().as_dict(), estimated_calls="1"),
    PresetInfo(name="economy", label="Single-shot + critic", description="One drafting call with the protagonist voice injected; deterministic critic grades the result but does not rewrite.", options=CraftOptions.preset("economy").as_dict(), estimated_calls="1"),
    PresetInfo(name="balanced", label="Scenes + polish", description="Deterministic scene plan, one drafting call per scene with subtext packets, model critic, up to 2 polish passes, hook sharpening.", options=CraftOptions.preset("balanced").as_dict(), estimated_calls="≈ scenes + 4-6"),
    PresetInfo(name="full", label="Elite multi-pass", description="Model scene plan, scene-by-scene drafting with subtext packets, adversarial model critic, up to 3 surgical polish passes, hook sharpening. Maximum quality; no token economy.", options=CraftOptions.full().as_dict(), estimated_calls="≈ scenes + 6-9"),
]


@router.get("/presets", response_model=List[PresetInfo], summary="Craft presets available to the Forge run and autonomous jobs")
def presets() -> List[PresetInfo]:
    return PRESETS


@router.get("/tics/catalogue", summary="The AI-tic catalogue the critic enforces")
def tic_catalogue() -> List[Dict[str, Any]]:
    return [{"code": r.code, "family": r.family, "severity": r.severity, "message": r.message, "hint": r.hint, "tolerated": r.max_per_chapter} for r in tics_mod.RULES]


__all__ = ["router"]
