"""
Project management and file upload handling service
"""
import os
import shutil
from pathlib import Path
from typing import List, Dict, Any, Optional
from fastapi import UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.models.project import Project, ProjectFile


class ProjectService:
    @staticmethod
    async def create_project(
        db: AsyncSession,
        name: str,
        description: Optional[str] = None,
        pipeline_type: str = "python_batch",
        entry_point: Optional[str] = None
    ) -> Project:
        project = Project(
            name=name,
            description=description,
            pipeline_type=pipeline_type,
            entry_point=entry_point
        )
        db.add(project)
        await db.commit()
        await db.refresh(project)

        # Create project upload directory
        proj_dir = settings.UPLOADS_DIR / project.id
        proj_dir.mkdir(parents=True, exist_ok=True)
        return project

    @staticmethod
    async def upload_files(
        db: AsyncSession,
        project_id: str,
        files: List[UploadFile]
    ) -> List[ProjectFile]:
        # Validate project exists
        res = await db.execute(select(Project).where(Project.id == project_id))
        project = res.scalar_one_or_none()
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        proj_dir = settings.UPLOADS_DIR / project_id
        saved_files: List[ProjectFile] = []

        for upload in files:
            filename = upload.filename or "unnamed_file"
            
            # Security: Path traversal sanitization
            safe_filename = Path(filename).name
            ext = Path(safe_filename).suffix.lower()

            if ext not in settings.ALLOWED_EXTENSIONS:
                raise ValueError(f"Disallowed file extension '{ext}'. Allowed: {settings.ALLOWED_EXTENSIONS}")

            dest_path = proj_dir / safe_filename
            content = await upload.read()

            if len(content) > settings.MAX_UPLOAD_SIZE_BYTES:
                raise ValueError(f"File '{safe_filename}' exceeds 50MB limit.")

            dest_path.write_bytes(content)

            # Categorize file
            category = "code"
            if ext in (".csv", ".parquet", ".json", ".jsonl"):
                category = "data"
            elif ext in (".log", ".txt") and ("log" in safe_filename.lower() or "err" in safe_filename.lower()):
                category = "log"
            elif ext in (".yaml", ".yml", ".ini", ".conf", ".toml"):
                category = "config"
            elif safe_filename.startswith("test_") or safe_filename.endswith("_test.py"):
                category = "test"

            # Check if entry point candidate
            if ext == ".py" and not project.entry_point and ("main" in safe_filename or "pipeline" in safe_filename or "run" in safe_filename):
                project.entry_point = safe_filename

            project_file = ProjectFile(
                project_id=project_id,
                relative_path=safe_filename,
                file_type=category,
                size_bytes=len(content),
                storage_path=str(dest_path),
                metadata_json={"original_name": filename}
            )
            db.add(project_file)
            saved_files.append(project_file)

        await db.commit()
        for f in saved_files:
            await db.refresh(f)
        return saved_files
