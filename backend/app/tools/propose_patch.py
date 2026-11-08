"""
Diagnostic Tool: propose_patch
Constructs and syntactically validates candidate code corrections without modifying original files.
Generates genuine unified diffs and risk impact assessments.
"""
import ast
import difflib
import time
from pathlib import Path
from typing import Dict, Any, List
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class ProposePatchTool(BaseDiagnosticTool):
    name = "propose_patch"
    description = (
        "Prepares a syntactically validated code correction and unified diff for an identified root cause. "
        "Strictly does NOT modify the original file."
    )
    permission_level = "patch_prep"
    timeout_seconds = 20

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="target_file",
                    type="string",
                    description="Relative path to the Python file requiring correction.",
                    required=True
                ),
                ToolParameter(
                    name="original_snippet",
                    type="string",
                    description="The exact snippet of original code to be replaced.",
                    required=True
                ),
                ToolParameter(
                    name="replacement_code",
                    type="string",
                    description="The corrected code to replace the original snippet.",
                    required=True
                ),
                ToolParameter(
                    name="explanation",
                    type="string",
                    description="Detailed explanation of how this correction solves the root cause.",
                    required=True
                ),
                ToolParameter(
                    name="supporting_evidence_ids",
                    type="array",
                    description="List of evidence record IDs justifying this patch.",
                    required=False,
                    default=[]
                ),
                ToolParameter(
                    name="expected_impact",
                    type="string",
                    description="Expected pipeline behavior and data output after applying this patch.",
                    required=False,
                    default="Pipeline executes without failure."
                ),
                ToolParameter(
                    name="potential_risks",
                    type="string",
                    description="Potential risks, edge cases, or trade-offs introduced by this change.",
                    required=False,
                    default="None identified."
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        target_file_rel = arguments.get("target_file", "")
        original_snippet = arguments.get("original_snippet", "")
        replacement_code = arguments.get("replacement_code", "")
        explanation = arguments.get("explanation", "")
        evidence_ids = arguments.get("supporting_evidence_ids", [])
        expected_impact = arguments.get("expected_impact", "")
        potential_risks = arguments.get("potential_risks", "")

        target_file = (base_dir / target_file_rel).resolve()
        if not str(target_file).startswith(str(base_dir)):
            return ToolResult(
                success=False,
                summary="Security error: Path traversal detected.",
                error="Path traversal attempted.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        if not target_file.exists():
            return ToolResult(
                success=False,
                summary=f"Target file '{target_file_rel}' does not exist.",
                error="File not found.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        original_content = target_file.read_text(encoding="utf-8")

        # Check if original_snippet exists in the file
        if original_snippet not in original_content:
            # Try normalized whitespace comparison
            normalized_orig = "\n".join(line.strip() for line in original_content.splitlines() if line.strip())
            normalized_snip = "\n".join(line.strip() for line in original_snippet.splitlines() if line.strip())
            if normalized_snip not in normalized_orig:
                return ToolResult(
                    success=False,
                    summary=f"Could not locate the exact original code snippet in '{target_file_rel}'.",
                    error="Snippet mismatch: The target code does not match original file contents.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

        # Generate candidate patched content
        candidate_content = original_content.replace(original_snippet, replacement_code, 1)

        # Syntax check via AST parse
        try:
            ast.parse(candidate_content, filename=target_file_rel)
        except SyntaxError as se:
            return ToolResult(
                success=False,
                summary=f"Proposed patch creates invalid Python syntax in '{target_file_rel}': {str(se)} at line {se.lineno}",
                error=f"SyntaxError in proposed code: {str(se)}",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        # Generate real unified diff
        diff_lines = list(difflib.unified_diff(
            original_content.splitlines(keepends=True),
            candidate_content.splitlines(keepends=True),
            fromfile=f"a/{target_file_rel}",
            tofile=f"b/{target_file_rel}"
        ))
        unified_diff = "".join(diff_lines)

        summary = (
            f"Successfully formulated syntactically valid patch for '{target_file_rel}'. "
            f"Diff contains {len(diff_lines)} lines of changes. Requires human review and approval."
        )

        return ToolResult(
            success=True,
            data={
                "target_file": target_file_rel,
                "original_code_context": original_snippet,
                "proposed_code": replacement_code,
                "unified_diff": unified_diff,
                "explanation": explanation,
                "supporting_evidence_ids": evidence_ids,
                "expected_impact": expected_impact,
                "potential_risks": potential_risks,
                "syntax_valid": True,
                "candidate_full_content": candidate_content
            },
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

