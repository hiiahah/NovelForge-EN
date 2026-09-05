from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session

from app.db.session import get_session
from app.services.context_service import ContextAssemblyError, assemble_context, ContextAssembleParams
from app.schemas.context import AssembleContextRequest, AssembleContextResponse

router = APIRouter()

@router.post("/assemble", response_model=AssembleContextResponse, summary="Assemble writing context (fact subgraph)")
def assemble(req: AssembleContextRequest, session: Session = Depends(get_session)):
    params = ContextAssembleParams(
        project_id=req.project_id,
        volume_number=req.volume_number,
        chapter_number=req.chapter_number,
        chapter_id=req.chapter_id,
        participants=req.participants,
        current_draft_tail=req.current_draft_tail,
        recent_chapters_window=req.recent_chapters_window,
        pov=req.pov,
        facts_quota_chars=req.facts_quota_chars,
        bible_quota_chars=req.bible_quota_chars,
        relation_radius=req.relation_radius,
        edge_type_whitelist=req.edge_type_whitelist,
        max_chapter_id=req.max_chapter_id,
    )
    try:
        ctx = assemble_context(session, params)
    except ContextAssemblyError as exc:
        # A provider failure must surface, not masquerade as "no context".
        raise HTTPException(status_code=503, detail=str(exc))
    return AssembleContextResponse(**ctx.__dict__)
