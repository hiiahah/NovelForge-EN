"""Story Memory API: chapter digests, Story So Far, continuity guard, planner, health, settings."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.models import Card
from app.db.session import get_session
from app.schemas.card import CardRead
from app.schemas.story_memory import (
    BibleHealth,
    ChapterDigest,
    ContinuityReport,
    NextChapterBrief,
    StoryMemorySettings,
    StorySoFar,
)
from app.services.story_memory import (
    ContinuityGuard,
    DigestService,
    NextChapterPlanner,
    StorySoFarCompiler,
    bible_health,
    get_settings,
    save_settings,
)
from app.services.story_memory.events import handle_chapter_saved  # noqa: F401  ensure handler import

router = APIRouter()


# ------------------------------------------------------------------ schemas

class DigestChapterRequest(BaseModel):
    project_id: int
    llm_config_id: int
    chapter_number: int
    text: Optional[str] = Field(default=None, description="Chapter text; when omitted the Chapter Text card is read")
    chapter_card_id: Optional[int] = None
    volume_number: Optional[int] = None
    title: Optional[str] = None
    participants: List[str] = Field(default_factory=list)
    force: bool = Field(default=False, description="Re-digest even when the text hash is unchanged")
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None


class DigestBatchRequest(BaseModel):
    project_id: int
    llm_config_id: int
    chapters: Optional[List[int]] = Field(default=None, description="Chapter numbers; default = every written chapter missing or stale")
    force: bool = False
    max_chapters: Optional[int] = Field(default=50, ge=1, le=1000)
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None


class DigestBatchResult(BaseModel):
    digested: List[int] = Field(default_factory=list)
    skipped: List[int] = Field(default_factory=list)
    failed: Dict[int, str] = Field(default_factory=dict)


class DigestListItem(BaseModel):
    card_id: int
    chapter_number: int
    volume_number: Optional[int] = None
    title: str = ""
    one_line: str = ""
    pov: str = ""
    word_count: int = 0
    stale: bool = False
    hooks_opened: int = 0
    hooks_closed: int = 0
    state_changes: int = 0
    tension_end: int = 0
    hook_strength: int = 0
    dominant_function: str = ""
    rewards_delivered: List[str] = Field(default_factory=list)
    digested_at: str = ""


class DigestListResponse(BaseModel):
    items: List[DigestListItem]
    coverage: Dict[str, Any]


class StorySoFarRequest(BaseModel):
    project_id: int
    next_chapter: Optional[int] = None
    budget_chars: Optional[int] = Field(default=None, ge=500, le=120000)


class ContinuityCheckRequest(BaseModel):
    project_id: int
    draft: str
    chapter_number: Optional[int] = None
    participants: List[str] = Field(default_factory=list)
    pov: Optional[str] = None
    outline: Optional[Dict[str, Any]] = None
    use_llm: bool = False
    llm_config_id: Optional[int] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None


class BriefRequest(BaseModel):
    project_id: int
    chapter_number: Optional[int] = None
    participants: List[str] = Field(default_factory=list)
    pov: Optional[str] = None


class SettingsUpdateRequest(BaseModel):
    project_id: int
    settings: StoryMemorySettings


class SettingsResponse(BaseModel):
    project_id: int
    settings: StoryMemorySettings


# ------------------------------------------------------------------ digests

def _chapter_card(session: Session, project_id: int, chapter_number: int, card_id: Optional[int]) -> Optional[Card]:
    if card_id:
        card = session.get(Card, card_id)
        if card and card.project_id == project_id:
            return card
    for card in DigestService(session).chapter_text_cards(project_id):
        c = card.content if isinstance(card.content, dict) else {}
        try:
            if int(c.get("chapter_number") or -1) == chapter_number:
                return card
        except (TypeError, ValueError):
            continue
    return None


@router.get("/digests", response_model=DigestListResponse, summary="List chapter digests with coverage of written chapters")
def list_digests(project_id: int, session: Session = Depends(get_session)):
    svc = DigestService(session)
    items: List[DigestListItem] = []
    for card in svc.digest_cards(project_id):
        d = svc.to_digest(card)
        if not d:
            continue
        items.append(DigestListItem(
            card_id=card.id, chapter_number=d.chapter_number, volume_number=d.volume_number, title=d.title, one_line=d.one_line, pov=d.pov,
            word_count=d.word_count, stale=d.stale, hooks_opened=len(d.hooks_opened), hooks_closed=len(d.hooks_closed), state_changes=len(d.state_changes),
            tension_end=d.tension_end, hook_strength=d.hook_strength, dominant_function=d.dominant_function, rewards_delivered=list(d.rewards_delivered), digested_at=d.digested_at,
        ))
    items.sort(key=lambda i: i.chapter_number)
    return DigestListResponse(items=items, coverage=svc.coverage(project_id))


@router.get("/digests/{chapter_number}", response_model=ChapterDigest, summary="Get one chapter digest")
def get_digest(chapter_number: int, project_id: int, session: Session = Depends(get_session)):
    svc = DigestService(session)
    card = svc.find_digest_card(project_id, chapter_number)
    d = svc.to_digest(card) if card else None
    if not d:
        raise HTTPException(status_code=404, detail="Digest not found")
    return d


@router.post("/digests", response_model=CardRead, summary="Digest one chapter (LLM) into a Chapter Digest card")
async def digest_chapter(req: DigestChapterRequest, session: Session = Depends(get_session)):
    text = req.text
    title = req.title or ""
    volume = req.volume_number
    card_id = req.chapter_card_id
    participants = list(req.participants)
    if not text:
        card = _chapter_card(session, req.project_id, req.chapter_number, req.chapter_card_id)
        if not card:
            raise HTTPException(status_code=404, detail=f"Chapter {req.chapter_number} has no Chapter Text card")
        c = card.content if isinstance(card.content, dict) else {}
        text = str(c.get("content") or "")
        title = title or str(c.get("title") or card.title)
        volume = volume if volume is not None else c.get("volume_number")
        card_id = card.id
        if not participants:
            participants = [x if isinstance(x, str) else (x or {}).get("name", "") for x in (c.get("entity_list") or [])]
    try:
        return await DigestService(session).digest_chapter(
            project_id=req.project_id, llm_config_id=req.llm_config_id, text=text or "", chapter_number=req.chapter_number, volume_number=volume,
            title=title, chapter_card_id=card_id, participants=[p for p in participants if p], force=req.force,
            temperature=req.temperature, max_tokens=req.max_tokens, timeout=req.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Chapter digest failed: {e}")


@router.post("/digests/batch", response_model=DigestBatchResult, summary="Digest many chapters (missing/stale by default)")
async def digest_batch(req: DigestBatchRequest, session: Session = Depends(get_session)):
    svc = DigestService(session)
    coverage = svc.coverage(req.project_id)
    targets = req.chapters if req.chapters else sorted(set(coverage["missing"]) | set(coverage["stale"]))
    if req.force and not req.chapters:
        targets = list(coverage["written"])
    result = DigestBatchResult()
    for n in targets[: (req.max_chapters or 50)]:
        card = _chapter_card(session, req.project_id, n, None)
        if not card:
            result.skipped.append(n)
            continue
        c = card.content if isinstance(card.content, dict) else {}
        try:
            await svc.digest_chapter(
                project_id=req.project_id, llm_config_id=req.llm_config_id, text=str(c.get("content") or ""), chapter_number=n,
                volume_number=c.get("volume_number"), title=str(c.get("title") or card.title), chapter_card_id=card.id,
                participants=[x if isinstance(x, str) else (x or {}).get("name", "") for x in (c.get("entity_list") or []) if x],
                force=req.force, temperature=req.temperature, max_tokens=req.max_tokens, timeout=req.timeout,
            )
            result.digested.append(n)
        except Exception as e:  # keep going; report per chapter
            result.failed[n] = str(e)[:300]
    return result


@router.put("/digests/{chapter_number}", response_model=CardRead, summary="Save a hand-edited digest")
def put_digest(chapter_number: int, project_id: int, digest: ChapterDigest, session: Session = Depends(get_session)):
    digest.chapter_number = chapter_number
    return DigestService(session).save_digest(project_id, digest)


@router.delete("/digests/{chapter_number}", summary="Delete a chapter digest")
def delete_digest(chapter_number: int, project_id: int, session: Session = Depends(get_session)):
    if not DigestService(session).delete_digest(project_id, chapter_number):
        raise HTTPException(status_code=404, detail="Digest not found")
    return {"success": True}


# ------------------------------------------------------------- story so far

@router.post("/story-so-far", response_model=StorySoFar, summary="Compile the tiered Story So Far recap and carry-forward state")
def story_so_far(req: StorySoFarRequest, session: Session = Depends(get_session)):
    return StorySoFarCompiler(session).compile(req.project_id, next_chapter=req.next_chapter, budget_chars=req.budget_chars)


# ---------------------------------------------------------- continuity guard

@router.post("/continuity/check", response_model=ContinuityReport, summary="Check a draft against the Bible and Story Memory")
async def continuity_check(req: ContinuityCheckRequest, session: Session = Depends(get_session)):
    guard = ContinuityGuard(session)
    if not req.draft.strip():
        raise HTTPException(status_code=400, detail="draft is empty")
    try:
        if req.use_llm:
            if not req.llm_config_id:
                raise HTTPException(status_code=400, detail="llm_config_id is required when use_llm is true")
            return await guard.check_with_llm(
                project_id=req.project_id, draft=req.draft, llm_config_id=req.llm_config_id, chapter_number=req.chapter_number,
                participants=req.participants, pov=req.pov, outline=req.outline, temperature=req.temperature, max_tokens=req.max_tokens, timeout=req.timeout,
            )
        return guard.check(project_id=req.project_id, draft=req.draft, chapter_number=req.chapter_number, participants=req.participants, pov=req.pov, outline=req.outline)
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Continuity check failed: {e}")


# ------------------------------------------------------------------ planner

@router.post("/brief", response_model=NextChapterBrief, summary="Next Chapter Brief: must address / should consider / avoid")
def next_chapter_brief(req: BriefRequest, session: Session = Depends(get_session)):
    return NextChapterPlanner(session).brief(req.project_id, chapter_number=req.chapter_number, participants=req.participants, pov=req.pov)


# ------------------------------------------------------------------- health

@router.get("/health", response_model=BibleHealth, summary="Bible health score with dimensions")
def health(project_id: int, session: Session = Depends(get_session)):
    return bible_health(session, project_id)


# ----------------------------------------------------------------- settings

@router.get("/settings", response_model=SettingsResponse, summary="Story Memory settings for a project")
def read_settings(project_id: int, session: Session = Depends(get_session)):
    return SettingsResponse(project_id=project_id, settings=get_settings(session, project_id))


@router.put("/settings", response_model=SettingsResponse, summary="Update Story Memory settings for a project")
def write_settings(req: SettingsUpdateRequest, session: Session = Depends(get_session)):
    try:
        saved = save_settings(session, req.project_id, req.settings)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return SettingsResponse(project_id=req.project_id, settings=saved)
