"""
InvestigationReport model
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, ForeignKey, Text, JSON
from sqlalchemy.orm import relationship
from backend.app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class InvestigationReport(Base):
    __tablename__ = "investigation_reports"

    id = Column(String(36), primary_key=True, default=lambda: f"REP-{uuid.uuid4().hex[:6].upper()}")
    investigation_id = Column(String(36), ForeignKey("investigations.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(255), nullable=False)
    summary = Column(Text, nullable=False)
    root_cause = Column(Text, nullable=False)
    affected_components = Column(JSON, default=list)
    supporting_evidence = Column(JSON, default=list)
    hypotheses_evaluated = Column(JSON, default=list)
    hypotheses_rejected = Column(JSON, default=list)
    proposed_correction = Column(JSON, default=dict)
    validation_summary = Column(JSON, default=dict)
    remaining_risks = Column(Text, nullable=True)
    next_steps = Column(JSON, default=list)
    markdown_content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utcnow, nullable=False)

    investigation = relationship("Investigation", back_populates="reports")

