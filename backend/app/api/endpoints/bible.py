from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db.session import get_session
from app.schemas.bible_update import (
    BibleUpdateApplyResult,
    BibleUpdateDecideRequest,
    BibleUpdateExtractRequest,
    BibleUpdateReviewListResponse,
    BibleUpdateReviewRead,
)
from app.schemas.card import CardRead
from app.services.bible import BibleService, CharacterDeepeningService, ContextCompiler, LivingBibleService

router = APIRouter()


class CharacterDeepenRequest(BaseModel):
    project_id: int
    card_id: int
    llm_config_id: int
    user_notes: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    timeout: Optional[float] = None


class BibleDashboardResponse(BaseModel):
    project_id: int
    current_chapter: int
    sections: Dict[str, Any]
    audits: Dict[str, Any]


class BibleAuditResponse(BaseModel):
    current_chapter: int
    warnings: List[Dict[str, Any]]


class RelationshipMatrixResponse(BaseModel):
    arcs: List[Dict[str, Any]]


class KnowledgeMatrixResponse(BaseModel):
    entities: List[str]
    rows: List[Dict[str, Any]]


class CompileContextRequest(BaseModel):
    project_id: int
    chapter_number: Optional[int] = None
    participants: List[str] = Field(default_factory=list)
    pov: Optional[str] = None
    budget_chars: int = Field(default=6000, ge=500, le=40000)
    chapter_goal: Optional[str] = None


class CompiledBlockRead(BaseModel):
    section: str
    title: str
    text: str
    reason: str
    truth_status: Optional[str] = None
    confidence: Optional[float] = None
    card_id: Optional[int] = None
    priority: int


class CompileContextResponse(BaseModel):
    text: str
    blocks: List[CompiledBlockRead]
    prohibited: List[str]
    budget_chars: int
    used_chars: int
    dropped: int


@router.get("/dashboard", response_model=BibleDashboardResponse, summary="Novel Bible dashboard: sections, counts and audits")
def dashboard(project_id: int, session: Session = Depends(get_session)):
    return BibleService(session).dashboard(project_id)


@router.get("/audit", response_model=BibleAuditResponse, summary="Run deterministic continuity audits over the Bible ledgers")
def audit(project_id: int, current_chapter: Optional[int] = None, session: Session = Depends(get_session)):
    return BibleService(session).audit(project_id, current_chapter)


@router.get("/relationships", response_model=RelationshipMatrixResponse, summary="Relationship arcs with milestones and history")
def relationships(project_id: int, session: Session = Depends(get_session)):
    return BibleService(session).relationship_matrix(project_id)


@router.get("/knowledge-matrix", response_model=KnowledgeMatrixResponse, summary="Information-reveal matrix (who knows what)")
def knowledge_matrix(project_id: int, session: Session = Depends(get_session)):
    return BibleService(session).knowledge_matrix(project_id)


@router.post("/compile-context", response_model=CompileContextResponse, summary="Compile the minimum relevant Bible slice for a chapter")
def compile_context(req: CompileContextRequest, session: Session = Depends(get_session)):
    compiled = ContextCompiler(session).compile(
        project_id=req.project_id,
        chapter_number=req.chapter_number,
        participants=req.participants,
        pov=req.pov,
        budget_chars=req.budget_chars,
        chapter_goal=req.chapter_goal,
    )
    return compiled.as_dict()


# ---------------------------------------------------------------- Living Bible

@router.post("/characters/deepen", response_model=CardRead, summary="Deepen one Character Card into a Character Bible entry")
async def deepen_character(req: CharacterDeepenRequest, session: Session = Depends(get_session)):
    svc = CharacterDeepeningService(session)
    try:
        card = await svc.deepen(
            project_id=req.project_id,
            card_id=req.card_id,
            llm_config_id=req.llm_config_id,
            user_notes=req.user_notes,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            timeout=req.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Character deepening failed: {e}")
    return card

@router.post("/updates/extract", response_model=BibleUpdateReviewRead, summary="Extract proposed Bible updates from a chapter (nothing is applied)")
async def extract_updates(req: BibleUpdateExtractRequest, session: Session = Depends(get_session)):
    svc = LivingBibleService(session)
    try:
        review = await svc.extract(
            project_id=req.project_id,
            llm_config_id=req.llm_config_id,
            text=req.text,
            chapter_card_id=req.chapter_card_id,
            volume_number=req.volume_number,
            chapter_number=req.chapter_number,
            participants=req.participants,
            outline_text=req.outline_text,
            temperature=req.temperature,
            max_tokens=req.max_tokens,
            timeout=req.timeout,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Bible update extraction failed: {e}")
    return svc.to_read(review)


@router.get("/updates", response_model=BibleUpdateReviewListResponse, summary="List Bible update reviews for a project")
def list_updates(project_id: int, status: Optional[str] = None, session: Session = Depends(get_session)):
    svc = LivingBibleService(session)
    return BibleUpdateReviewListResponse(items=[svc.to_read(r) for r in svc.list_reviews(project_id, status)])


@router.get("/updates/{review_id}", response_model=BibleUpdateReviewRead, summary="Get one Bible update review")
def get_update(review_id: int, session: Session = Depends(get_session)):
    svc = LivingBibleService(session)
    review = svc.get_review(review_id)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return svc.to_read(review)


@router.post("/updates/{review_id}/decide", response_model=BibleUpdateApplyResult, summary="Accept / reject / edit / postpone proposed changes")
def decide(review_id: int, req: BibleUpdateDecideRequest, session: Session = Depends(get_session)):
    svc = LivingBibleService(session)
    try:
        return svc.decide(review_id, req.decisions)
    except ValueError as e:
        message = str(e)
        status_code = 409 if ("already" in message or "dismissed" in message) else 404
        raise HTTPException(status_code=status_code, detail=message)
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/updates/{review_id}", summary="Dismiss a Bible update review")
def delete_update(review_id: int, session: Session = Depends(get_session)):
    ok = LivingBibleService(session).delete_review(review_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Review not found")
    return {"success": True}
