"""
Dashboard statistics and summary metrics calculated strictly from persisted DB data
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from backend.app.database.session import get_db
from backend.app.models.investigation import Investigation, InvestigationEvent
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.project import Project

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/stats")
async def get_dashboard_stats(db: AsyncSession = Depends(get_db)):
    # 1. Total investigations
    total_inv_res = await db.execute(select(func.count(Investigation.id)))
    total_investigations = total_inv_res.scalar() or 0

    # 2. Active investigations
    active_inv_res = await db.execute(
        select(func.count(Investigation.id)).where(Investigation.status.in_(["investigating", "awaiting_patch_approval", "validating"]))
    )
    active_investigations = active_inv_res.scalar() or 0

    # 3. Confirmed diagnoses (completed investigations with high confidence)
    confirmed_res = await db.execute(
        select(func.count(Investigation.id)).where(Investigation.status == "completed")
    )
    confirmed_diagnoses = confirmed_res.scalar() or 0

    # 4. Validated fixes
    val_fixes_res = await db.execute(
        select(func.count(ValidationRun.id)).where(ValidationRun.status == "passed")
    )
    validated_fixes = val_fixes_res.scalar() or 0

    # 5. Status distribution
    status_query = await db.execute(
        select(Investigation.status, func.count(Investigation.id)).group_by(Investigation.status)
    )
    status_distribution = {status: count for status, count in status_query.all()}

    # 6. Recent investigations
    recent_inv_query = await db.execute(
        select(Investigation).order_by(Investigation.created_at.desc()).limit(5)
    )
    recent_investigations = [
        {
            "id": inv.id,
            "project_id": inv.project_id,
            "objective": inv.objective,
            "status": inv.status,
            "stage": inv.stage,
            "confidence_score": inv.confidence_score,
            "created_at": inv.created_at.isoformat()
        }
        for inv in recent_inv_query.scalars().all()
    ]

    # 7. Recent projects
    recent_proj_query = await db.execute(
        select(Project).order_by(Project.created_at.desc()).limit(5)
    )
    recent_projects = [
        {
            "id": p.id,
            "name": p.name,
            "pipeline_type": p.pipeline_type,
            "entry_point": p.entry_point,
            "created_at": p.created_at.isoformat()
        }
        for p in recent_proj_query.scalars().all()
    ]

    # 8. Recent events
    recent_events_query = await db.execute(
        select(InvestigationEvent).order_by(InvestigationEvent.created_at.desc()).limit(10)
    )
    recent_events = [
        {
            "id": ev.id,
            "investigation_id": ev.investigation_id,
            "event_type": ev.event_type,
            "title": ev.title,
            "created_at": ev.created_at.isoformat()
        }
        for ev in recent_events_query.scalars().all()
    ]

    return {
        "metrics": {
            "total_investigations": total_investigations,
            "active_investigations": active_investigations,
            "confirmed_diagnoses": confirmed_diagnoses,
            "validated_fixes": validated_fixes,
        },
        "status_distribution": status_distribution,
        "recent_investigations": recent_investigations,
        "recent_projects": recent_projects,
        "recent_events": recent_events
    }

