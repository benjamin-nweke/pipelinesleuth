"""
Projects API router
"""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.database.session import get_db
from backend.app.models.project import Project, ProjectFile
from backend.app.schemas.project import ProjectRead, ProjectFileRead
from backend.app.services.project_service import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectRead)
async def create_project(
    name: str = Form(...),
    description: Optional[str] = Form(None),
    pipeline_type: str = Form("python_batch"),
    entry_point: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db)
):
    project = await ProjectService.create_project(
        db=db,
        name=name,
        description=description,
        pipeline_type=pipeline_type,
        entry_point=entry_point
    )
    return project


@router.get("", response_model=List[ProjectRead])
async def list_projects(db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Project).options(selectinload(Project.files)).order_by(Project.created_at.desc())
    )
    return res.scalars().all()


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project(project_id: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(
        select(Project).options(selectinload(Project.files)).where(Project.id == project_id)
    )
    project = res.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


@router.post("/{project_id}/files", response_model=List[ProjectFileRead])
async def upload_project_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db)
):
    try:
        saved = await ProjectService.upload_files(db, project_id, files)
        return saved
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload error: {str(e)}")

