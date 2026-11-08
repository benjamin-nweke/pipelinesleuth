"""
Diagnostic Tool: test_hypothesis
Synthesizes and evaluates evidence against a specific hypothesis,
weighing supporting facts, contradictory facts, and residual uncertainty.
"""
import time
from typing import Dict, Any, List, Optional
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class TestHypothesisTool(BaseDiagnosticTool):
    name = "test_hypothesis"
    description = (
        "Evaluates a specific failure hypothesis by comparing supporting vs contradicting evidence, "
        "assessing causal sufficiency, and determining if the hypothesis is confirmed, rejected, or needs more evidence."
    )
    permission_level = "read_only"
    timeout_seconds = 15

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="hypothesis_id",
                    type="string",
                    description="The identifier of the hypothesis being tested (e.g. HYP-01).",
                    required=True
                ),
                ToolParameter(
                    name="hypothesis_statement",
                    type="string",
                    description="The core claim of the hypothesis.",
                    required=True
                ),
                ToolParameter(
                    name="supporting_evidence_ids",
                    type="array",
                    description="List of evidence record IDs that support this hypothesis.",
                    required=True
                ),
                ToolParameter(
                    name="contradicting_evidence_ids",
                    type="array",
                    description="List of evidence record IDs that contradict or undermine this hypothesis.",
                    required=False,
                    default=[]
                ),
                ToolParameter(
                    name="evaluation_reasoning",
                    type="string",
                    description="Analytical evaluation of why the evidence confirms, refutes, or leaves uncertainty.",
                    required=True
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        hyp_id = arguments.get("hypothesis_id", "")
        statement = arguments.get("hypothesis_statement", "")
        supporting = arguments.get("supporting_evidence_ids", [])
        contradicting = arguments.get("contradicting_evidence_ids", [])
        reasoning = arguments.get("evaluation_reasoning", "")

        sup_count = len(supporting)
        contra_count = len(contradicting)

        if contra_count > 0:
            conclusion = "rejected"
            verdict_desc = f"Hypothesis {hyp_id} is CONTRADICTED by {contra_count} evidence item(s)."
        elif sup_count >= 2:
            conclusion = "confirmed"
            verdict_desc = f"Hypothesis {hyp_id} is CONFIRMED with {sup_count} supporting evidence item(s)."
        elif sup_count == 1:
            conclusion = "inconclusive"
            verdict_desc = f"Hypothesis {hyp_id} has preliminary support (1 item), but needs further corroboration."
        else:
            conclusion = "inconclusive"
            verdict_desc = f"Hypothesis {hyp_id} lacks direct evidence."

        summary = f"{verdict_desc} Analysis: {reasoning}"

        return ToolResult(
            success=True,
            data={
                "hypothesis_id": hyp_id,
                "hypothesis_statement": statement,
                "supporting_count": sup_count,
                "contradicting_count": contra_count,
                "conclusion": conclusion,
                "verdict_description": verdict_desc,
                "reasoning": reasoning
            },
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

