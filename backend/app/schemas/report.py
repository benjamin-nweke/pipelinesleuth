"""
Pydantic schemas for Investigation Reports
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class InvestigationReportRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    title: str
    summary: str
    root_cause: str
    affected_components: List[str] = Field(default_factory=list)
    supporting_evidence: List[Dict[str, Any]] = Field(default_factory=list)
    hypotheses_evaluated: List[Dict[str, Any]] = Field(default_factory=list)
    hypotheses_rejected: List[Dict[str, Any]] = Field(default_factory=list)
    proposed_correction: Dict[str, Any] = Field(default_factory=dict)
    validation_summary: Dict[str, Any] = Field(default_factory=dict)
    remaining_risks: Optional[str] = None
    next_steps: List[str] = Field(default_factory=list)
    markdown_content: str
    created_at: datetime
