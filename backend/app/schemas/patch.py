"""
Pydantic schemas for Patch Proposals and Validation Runs
"""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ValidationRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    patch_id: Optional[str] = None
    status: str
    exit_code: int
    stdout: str
    stderr: str
    test_summary: Dict[str, Any] = Field(default_factory=dict)
    regression_results: Dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: int
    created_at: datetime


class PatchProposalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    investigation_id: str
    target_file: str
    original_code_context: str
    proposed_code: str
    unified_diff: str
    explanation: str
    supporting_evidence_ids: List[str] = Field(default_factory=list)
    expected_impact: Optional[str] = None
    potential_risks: Optional[str] = None
    status: str
    created_at: datetime
    validation_runs: List[ValidationRunRead] = Field(default_factory=list)


class PatchActionRequest(BaseModel):
    action: str = Field(..., pattern="^(approve|reject)$")
    reason: Optional[str] = None
