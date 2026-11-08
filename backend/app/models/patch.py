"""
PatchProposal and ValidationRun models
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from backend.app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PatchProposal(Base):
    __tablename__ = "patch_proposals"

    id = Column(String(36), primary_key=True, default=lambda: f"PATCH-{uuid.uuid4().hex[:6].upper()}")
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    target_file = Column(String(500), nullable=False)
    original_code_context = Column(Text, nullable=False)
    proposed_code = Column(Text, nullable=False)
    unified_diff = Column(Text, nullable=False)
    explanation = Column(Text, nullable=False)
    supporting_evidence_ids = Column(JSON, default=list)
    expected_impact = Column(Text, nullable=True)
    potential_risks = Column(Text, nullable=True)
    status = Column(String(50), default="proposed", nullable=False)  # proposed, approved, rejected, validated
    created_at = Column(DateTime, default=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="patches")
    validation_runs = relationship("ValidationRun", back_populates="patch", cascade="all, delete-orphan")


class ValidationRun(Base):
    __tablename__ = "validation_runs"

    id = Column(String(36), primary_key=True, default=lambda: f"VAL-{uuid.uuid4().hex[:6].upper()}")
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    patch_id = Column(String(36), ForeignKey("patch_proposals.id", ondelete="CASCADE"), nullable=True)
    status = Column(String(50), default="passed", nullable=False)  # passed, failed, error, skipped
    exit_code = Column(Integer, default=0)
    stdout = Column(Text, default="")
    stderr = Column(Text, default="")
    test_summary = Column(JSON, default=dict)
    regression_results = Column(JSON, default=dict)
    execution_time_ms = Column(Integer, default=0)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    patch = relationship("PatchProposal", back_populates="validation_runs")
