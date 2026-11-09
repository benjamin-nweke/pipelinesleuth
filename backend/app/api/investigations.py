"""
Investigations API router with Server-Sent Events (SSE) streaming
"""
import json
import asyncio
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.database.session import get_db
from backend.app.models.investigation import (
    Investigation, InvestigationEvent, EvidenceRecord, Hypothesis, ToolExecution
)
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.report import InvestigationReport
from backend.app.schemas.investigation import (
    InvestigationCreate, InvestigationRead, InvestigationDetailRead,
    InvestigationEventRead, EvidenceRecordRead, HypothesisRead, ToolExecutionRead
)
from backend.app.schemas.patch import PatchProposalRead, PatchActionRequest, ValidationRunRead
from backend.app.core.auth import AuthenticatedPrincipal, require_operator_principal
from backend.app.schemas.report import InvestigationReportRead
from backend.app.services.investigation_service import investigation_service

router = APIRouter(prefix="/investigations", tags=["investigations"])


@router.post("", response_model=InvestigationRead)
async def create_investigation(
    req: InvestigationCreate,
    db: AsyncSession = Depends(get_db)
):
    inv = await investigation_service.start_investigation(
        db=db,
        project_id=req.project_id,
        objective=req.objective
    )
    return inv


@router.get("", response_model=List[InvestigationRead])
async def list_investigations(
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db)
):
    query = select(Investigation).order_by(Investigation.created_at.desc())
    if project_id:
        query = query.where(Investigation.project_id == project_id)
    if status:
        query = query.where(Investigation.status == status)

    res = await db.execute(query)
    return res.scalars().all()


@router.get("/{investigation_id}", response_model=InvestigationDetailRead)
async def get_investigation_detail(
    investigation_id: str,
    db: AsyncSession = Depends(get_db)
):
    res = await db.execute(
        select(Investigation)
        .options(
            selectinload(Investigation.events),
            selectinload(Investigation.evidence_records),
            selectinload(Investigation.hypotheses),
            selectinload(Investigation.tool_executions),
        )
        .where(Investigation.id == investigation_id)
    )
    inv = res.scalar_one_or_none()
    if not inv:
        raise HTTPException(status_code=404, detail="Investigation not found")
    return inv


@router.get("/{investigation_id}/events/stream")
async def stream_investigation_events(investigation_id: str):
    """
    Server-Sent Events (SSE) endpoint for real-time investigation updates.
    """
    queue = investigation_service.subscribe(investigation_id)

    async def event_generator():
        try:
            # Yield initial connection heartbeat
            yield f"event: ping\ndata: {json.dumps({'status': 'connected'})}\n\n"

            while True:
                try:
                    event_data = await asyncio.wait_for(queue.get(), timeout=15.0)
                    event_type = event_data.get("event_type", "update")
                    yield f"event: {event_type}\ndata: {json.dumps(event_data)}\n\n"
                except asyncio.TimeoutError:
                    # Keep-alive heartbeat comment
                    yield ": keep-alive\n\n"
        finally:
            investigation_service.unsubscribe(investigation_id, queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/{investigation_id}/evidence", response_model=List[EvidenceRecordRead])
async def get_evidence(investigation_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(EvidenceRecord)
        .where(EvidenceRecord.investigation_id == investigation_id)
        .order_by(EvidenceRecord.timestamp.asc())
    )
    return res.scalars().all()


@router.get("/{investigation_id}/hypotheses", response_model=List[HypothesisRead])
async def get_hypotheses(investigation_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Hypothesis)
        .where(Hypothesis.investigation_id == investigation_id)
        .order_by(Hypothesis.created_at.asc())
    )
    return res.scalars().all()


@router.get("/{investigation_id}/patches", response_model=List[PatchProposalRead])
async def get_patches(investigation_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(PatchProposal)
        .options(selectinload(PatchProposal.validation_runs))
        .where(PatchProposal.investigation_id == investigation_id)
        .order_by(PatchProposal.created_at.desc())
    )
    return res.scalars().all()


@router.post("/{investigation_id}/patches/{patch_id}/action", response_model=PatchProposalRead)
async def handle_patch_action(
    investigation_id: str,
    patch_id: str,
    req: PatchActionRequest,
    principal: AuthenticatedPrincipal = Depends(require_operator_principal),
    db: AsyncSession = Depends(get_db)
):
    try:
        updated = await investigation_service.handle_patch_action(
            db=db,
            investigation_id=investigation_id,
            patch_id=patch_id,
            action=req.action,
            principal=principal,
            reason=req.reason
        )
        return updated
    except PermissionError as pe:
        raise HTTPException(status_code=403, detail=str(pe))
    except ValueError as ve:
        err = str(ve).lower()
        if "not found" in err:
            status_code = 404
        elif "already" in err or "cannot be transitioned" in err or "conflict" in err:
            status_code = 409
        elif "security violation" in err or "unauthorized" in err or "forbidden" in err:
            status_code = 403
        else:
            status_code = 400
        raise HTTPException(status_code=status_code, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{investigation_id}/validation", response_model=List[ValidationRunRead])
async def get_validation_runs(investigation_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(ValidationRun)
        .where(ValidationRun.investigation_id == investigation_id)
        .order_by(ValidationRun.created_at.desc())
    )
    return res.scalars().all()


@router.get("/{investigation_id}/report", response_model=InvestigationReportRead)
async def get_report(investigation_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(InvestigationReport)
        .where(InvestigationReport.investigation_id == investigation_id)
        .order_by(InvestigationReport.created_at.desc())
    )
    report = res.scalars().first()
    if not report:
        raise HTTPException(status_code=404, detail="Investigation report not generated yet.")
    return report

