"""
Project and ProjectFile models
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from backend.app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Project(Base):
    __tablename__ = "projects"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    pipeline_type = Column(String(100), default="python_batch")
    entry_point = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    files = relationship("ProjectFile", back_populates="project", cascade="all, delete-orphan")
    investigations = relationship("Investigation", back_populates="project", cascade="all, delete-orphan")


class ProjectFile(Base):
    __tablename__ = "project_files"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    relative_path = Column(String(500), nullable=False)
    file_type = Column(String(50), nullable=False)  # 'code', 'data', 'log', 'config', 'test'
    size_bytes = Column(Integer, nullable=False)
    storage_path = Column(String(1000), nullable=False)
    metadata_json = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    project = relationship("Project", back_populates="files")

