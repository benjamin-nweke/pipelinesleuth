"""
Pydantic schemas for Project and ProjectFile
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ProjectFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    relative_path: str
    file_type: str
    size_bytes: int
    storage_path: str
    metadata_json: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class ProjectBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    pipeline_type: str = "python_batch"
    entry_point: Optional[str] = None


class ProjectCreate(ProjectBase):
    pass


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    created_at: datetime
    updated_at: datetime
    files: List[ProjectFileRead] = Field(default_factory=list)
