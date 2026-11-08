"""
Investigation, InvestigationEvent, EvidenceRecord, Hypothesis, and ToolExecution models
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, Float, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from backend.app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Investigation(Base):
    __tablename__ = "investigations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False)
    objective = Column(Text, nullable=False)
    status = Column(String(50), default="created", nullable=False)
    stage = Column(String(50), default="context_collection", nullable=False)
    confidence_score = Column(Float, default=0.0)
    confidence_justification = Column(Text, nullable=True)
    remaining_budget = Column(Integer, default=12)
    root_cause_summary = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    project = relationship("Project", back_populates="investigations")
    events = relationship("InvestigationEvent", back_populates="investigation", cascade="all, delete-orphan", order_by="InvestigationEvent.created_at")
    evidence_records = relationship("EvidenceRecord", back_populates="investigation", cascade="all, delete-orphan", order_by="EvidenceRecord.timestamp")
    hypotheses = relationship("Hypothesis", back_populates="investigation", cascade="all, delete-orphan")
    tool_executions = relationship("ToolExecution", back_populates="investigation", cascade="all, delete-orphan", order_by="ToolExecution.created_at")
    patches = relationship("PatchProposal", back_populates="investigation", cascade="all, delete-orphan")
    reports = relationship("InvestigationReport", back_populates="investigation", cascade="all, delete-orphan")


class InvestigationEvent(Base):
    __tablename__ = "investigation_events"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    title = Column(String(255), nullable=False)
    payload = Column(JSON, default=dict)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="events")


class EvidenceRecord(Base):
    __tablename__ = "evidence_records"

    id = Column(String(36), primary_key=True, default=lambda: f"EVD-{uuid.uuid4().hex[:8].upper()}")
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    source_file = Column(String(500), nullable=False)
    evidence_type = Column(String(50), nullable=False)
    line_number = Column(Integer, nullable=True)
    column_name = Column(String(255), nullable=True)
    execution_stage = Column(String(100), nullable=True)
    observation = Column(Text, nullable=False)
    associated_hypothesis_id = Column(String(36), ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True)
    verification_status = Column(String(50), default="verified", nullable=False)
    timestamp = Column(DateTime, default=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="evidence_records")
    hypothesis = relationship("Hypothesis", back_populates="evidence_items")


class Hypothesis(Base):
    __tablename__ = "hypotheses"

    id = Column(String(36), primary_key=True, default=lambda: f"HYP-{uuid.uuid4().hex[:6].upper()}")
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    status = Column(String(50), default="formulated", nullable=False)
    confidence = Column(Float, default=0.5)
    reasoning = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utcnow, nullable=False)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="hypotheses")
    evidence_items = relationship("EvidenceRecord", back_populates="hypothesis")


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    tool_name = Column(String(100), nullable=False)
    input_arguments = Column(JSON, default=dict)
    output_result = Column(JSON, default=dict)
    error_message = Column(Text, nullable=True)
    execution_time_ms = Column(Integer, default=0)
    status = Column(String(50), default="success", nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="tool_executions")

