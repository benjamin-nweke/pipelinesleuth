"""
Structured Pydantic models for Agent Actions, Reasoning, and Decision Making
"""
from typing import Optional, List, Dict, Any, Literal
from pydantic import BaseModel, Field, model_validator


class ToolCallRequest(BaseModel):
    tool_name: str = Field(..., description="The name of the registered diagnostic tool to call")
    arguments: Dict[str, Any] = Field(default_factory=dict, description="Arguments to pass to the tool")


class AgentEvidenceInput(BaseModel):
    source_file: str = Field(..., description="File path from which evidence was observed")
    evidence_type: str = Field(..., description="Type of evidence e.g. schema_diff, dataset_anomaly, log_stacktrace, ast_pattern, diagnostic_output")
    line_number: Optional[int] = Field(None, description="Relevant line number if applicable")
    column_name: Optional[str] = Field(None, description="Relevant column name if applicable")
    execution_stage: Optional[str] = Field(None, description="Pipeline stage name")
    observation: str = Field(..., description="Factual observation derived directly from tool output")
    associated_hypothesis_id: Optional[str] = Field(None, description="Hypothesis ID this evidence supports or addresses")
    tool_execution_id: Optional[str] = Field(None, description="Traceable identifier of the tool execution yielding this evidence")
    is_inference: bool = Field(False, description="Whether this is an inferential interpretation rather than a direct empirical observation")


class AgentHypothesisInput(BaseModel):
    id: Optional[str] = Field(None, description="Hypothesis ID if updating an existing one, or leave null for new")
    title: str = Field(..., description="Concise statement of the failure hypothesis")
    description: str = Field(..., description="Detailed explanation of how the failure happens")
    status: Literal["formulated", "testing", "confirmed", "rejected", "inconclusive"] = Field("formulated")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score between 0.0 and 1.0")
    reasoning: str = Field(..., description="Explicit justification for confidence and status based on evidence")


class AgentPatchInput(BaseModel):
    target_file: str = Field(..., description="Path to the file that requires modification")
    original_code_context: str = Field(..., description="Original snippet of code being replaced")
    proposed_code: str = Field(..., description="Replacement code snippet")
    unified_diff: str = Field(..., description="Unified diff formatted representation of the patch")
    explanation: str = Field(..., description="Detailed technical explanation of why this fix addresses root cause")
    supporting_evidence_ids: List[str] = Field(default_factory=list, description="List of evidence IDs justifying this fix")
    expected_impact: str = Field(..., description="Expected behavior of pipeline after fix")
    potential_risks: str = Field(..., description="Potential edge cases, regressions or side effects")


class AgentReportInput(BaseModel):
    title: str = Field(..., description="Title of final diagnostic report")
    summary: str = Field(..., description="Executive summary of the failure and investigation")
    root_cause: str = Field(..., description="Definitive evidence-backed root cause statement")
    affected_components: List[str] = Field(default_factory=list, description="Pipeline files and stages affected")
    remaining_risks: Optional[str] = Field(None, description="Remaining uncertainty or operational risks")
    next_steps: List[str] = Field(default_factory=list, description="Recommended production actions or monitoring")


class InvestigationSummary(BaseModel):
    investigation_focus: str = Field(..., description="What specific failure mechanism, artifact, or stage the agent is actively investigating")
    tool_selection_reason: Optional[str] = Field(None, description="Concise rationale for why the selected diagnostic tool was chosen")
    finding_summary: Optional[str] = Field(None, description="Concise summary of what recent diagnostic outputs or observations revealed")
    hypothesis_impact: Optional[str] = Field(None, description="How current findings support, refute, or shape hypotheses")
    next_steps: Optional[str] = Field(None, description="What the agent intends to investigate next")
    conclusion_rationale: Optional[str] = Field(None, description="Concise justification for final diagnosis or inconclusive finding")


class AgentDecision(BaseModel):
    """
    Every reasoning turn of the agent outputs a structured AgentDecision.
    The agent can:
    1. Formulate / update hypotheses
    2. Record traceable evidence
    3. Call a diagnostic tool
    4. Propose a patch
    5. Conclude investigation with report or inconclusive verdict
    """
    investigation_summary: InvestigationSummary = Field(
        ...,
        description="Concise user-facing investigation summary explaining focus, rationale, findings, and plan"
    )
    thinking: Optional[str] = Field(
        None,
        description="Legacy internal thinking field (mirrored from investigation_summary.investigation_focus)"
    )
    current_stage: Literal[
        "context_collection",
        "artifact_inspection",
        "failure_localization",
        "hypothesis_generation",
        "diagnostic_selection",
        "evidence_collection",
        "hypothesis_testing",
        "root_cause_assessment",
        "patch_generation",
        "awaiting_approval",
        "validation",
        "regression_testing",
        "report_generation"
    ] = Field(..., description="Current stage in the investigation workflow")
    
    hypotheses: List[AgentHypothesisInput] = Field(default_factory=list, description="New or updated hypotheses")
    new_evidence: List[AgentEvidenceInput] = Field(default_factory=list, description="Evidence facts to record from recent tool outputs")
    
    # Action selection: exactly one primary action per step
    action_type: Literal["call_tool", "propose_patch", "conclude_success", "conclude_inconclusive", "request_info"] = Field(
        ..., description="The next action to take"
    )
    
    tool_call: Optional[ToolCallRequest] = Field(None, description="Tool to execute if action_type == 'call_tool'")
    patch_proposal: Optional[AgentPatchInput] = Field(None, description="Patch details if action_type == 'propose_patch'")
    final_report: Optional[AgentReportInput] = Field(None, description="Final report if concluding")
    
    confidence_score: float = Field(0.0, ge=0.0, le=1.0, description="Overall confidence in root cause diagnosis")
    confidence_justification: str = Field("", description="Why this confidence score is warranted by collected evidence")

    @model_validator(mode="before")
    @classmethod
    def reconcile_summary_and_thinking(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # If investigation_summary is missing but thinking is provided, populate investigation_summary
            if "investigation_summary" not in data or not data["investigation_summary"]:
                thinking_val = data.get("thinking") or "Investigating pipeline failure"
                data["investigation_summary"] = {
                    "investigation_focus": thinking_val,
                    "tool_selection_reason": None,
                    "finding_summary": None,
                    "hypothesis_impact": None,
                    "next_steps": None,
                    "conclusion_rationale": None
                }
            if not data.get("thinking"):
                inv_sum = data.get("investigation_summary")
                if isinstance(inv_sum, dict):
                    data["thinking"] = inv_sum.get("investigation_focus", "")
                elif hasattr(inv_sum, "investigation_focus"):
                    data["thinking"] = getattr(inv_sum, "investigation_focus", "")
        return data

