"""
Pydantic schemas for Investigations, Events, Evidence, Hypotheses, and Tool Executions
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ToolExecutionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    tool_name: str
    input_arguments: Dict[str, Any] = Field(default_factory=dict)
    output_result: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    execution_time_ms: int = 0
    status: str
    created_at: datetime


class EvidenceRecordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    source_file: str
    evidence_type: str
    line_number: Optional[int] = None
    column_name: Optional[str] = None
    execution_stage: Optional[str] = None
    observation: str
    associated_hypothesis_id: Optional[str] = None
    tool_execution_id: Optional[str] = None
    is_inference: bool = False
    verification_status: str
    timestamp: datetime


class HypothesisRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    title: str
    description: str
    status: str
    confidence: float
    reasoning: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InvestigationEventRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    event_type: str
    title: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class InvestigationCreate(BaseModel):
    project_id: str
    objective: str = Field(..., min_length=5)


class InvestigationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    objective: str
    status: str
    stage: str
    confidence_score: float
    confidence_justification: Optional[str] = None
    remaining_budget: int
    root_cause_summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class InvestigationDetailRead(InvestigationRead):
    events: List[InvestigationEventRead] = Field(default_factory=list)
    evidence_records: List[EvidenceRecordRead] = Field(default_factory=list)
    hypotheses: List[HypothesisRead] = Field(default_factory=list)
    tool_executions: List[ToolExecutionRead] = Field(default_factory=list)
