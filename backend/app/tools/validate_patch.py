"""
Diagnostic Tool: validate_patch
Executes candidate patch in a separate, isolated workspace copy.
Executes pytest and entrypoint scripts to verify fix and check for regressions.
Returns genuine process exit code, stdout, stderr, and test summary.
"""
import time
import re
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult
from backend.app.sandbox.manager import sandbox_manager


class ValidatePatchTool(BaseDiagnosticTool):
    name = "validate_patch"
    description = (
        "Validates an approved patch by creating an isolated candidate workspace copy, applying the patch, "
        "and running test suites and pipeline execution. Reports actual exit codes, stdout, stderr, and regressions."
    )
    permission_level = "sandboxed_exec"
    timeout_seconds = 60

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="target_file",
                    type="string",
                    description="Relative path to the file to patch.",
                    required=True
                ),
                ToolParameter(
                    name="replacement_code",
                    type="string",
                    description="The candidate replacement code.",
                    required=True
                ),
                ToolParameter(
                    name="original_snippet",
                    type="string",
                    description="The original snippet being replaced.",
                    required=True
                ),
                ToolParameter(
                    name="test_command",
                    type="array",
                    description="Command to run in sandbox to validate fix (e.g. ['pytest'] or ['python', 'pipeline.py']).",
                    required=False,
                    default=["pytest"]
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    def _parse_test_summary(self, stdout: str, stderr: str) -> Dict[str, Any]:
        combined = stdout + "\n" + stderr
        # Pytest summary parsing: e.g. "3 passed, 1 failed in 0.12s"
        summary = {
            "passed": 0,
            "failed": 0,
            "errors": 0,
            "total": 0,
            "details": []
        }
        passed_m = re.search(r'(\d+)\s+passed', combined)
        failed_m = re.search(r'(\d+)\s+failed', combined)
        error_m = re.search(r'(\d+)\s+error', combined)

        if passed_m:
            summary["passed"] = int(passed_m.group(1))
        if failed_m:
            summary["failed"] = int(failed_m.group(1))
        if error_m:
            summary["errors"] = int(error_m.group(1))
        summary["total"] = summary["passed"] + summary["failed"] + summary["errors"]
        return summary

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        target_file_rel = arguments.get("target_file", "")
        original_snippet = arguments.get("original_snippet", "")
        replacement_code = arguments.get("replacement_code", "")
        test_cmd = arguments.get("test_command", ["pytest"])

        # 1. Create separate candidate workspace copy
        candidate_ws = sandbox_manager.create_isolated_workspace(
            source_project_path=project_path,
            candidate_name=f"cand_val_{int(start_time)}"
        )

        try:
            # 2. Apply patch in candidate workspace
            candidate_file = Path(candidate_ws) / target_file_rel
            if not candidate_file.exists():
                return ToolResult(
                    success=False,
                    summary=f"Candidate file '{target_file_rel}' not found in isolated workspace.",
                    error="File missing in workspace copy.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

            content = candidate_file.read_text(encoding="utf-8")
            if original_snippet not in content:
                return ToolResult(
                    success=False,
                    summary="Original snippet could not be matched in candidate workspace file.",
                    error="Snippet mismatch.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

            new_content = content.replace(original_snippet, replacement_code, 1)
            candidate_file.write_text(new_content, encoding="utf-8")

            # 3. Execute validation command inside sandbox
            run_res = await sandbox_manager.run_in_sandbox(
                workspace_path=candidate_ws,
                command=test_cmd,
                timeout_seconds=self.timeout_seconds
            )

            exit_code = run_res.get("exit_code")
            stdout = run_res.get("stdout", "")
            stderr = run_res.get("stderr", "")
            docker_avail = run_res.get("docker_available", False)

            test_summary = self._parse_test_summary(stdout, stderr)
            passed = (exit_code == 0)

            if not docker_avail:
                validation_status = "skipped"
                summary = "Validation could not execute in container because Docker is unavailable."
            elif passed:
                validation_status = "passed"
                summary = (
                    f"Candidate validation PASSED! Exit code 0. "
                    f"Tests passed: {test_summary['passed']}. No errors or regressions detected."
                )
            else:
                validation_status = "failed"
                summary = (
                    f"Candidate validation FAILED! Exit code {exit_code}. "
                    f"Tests failed: {test_summary['failed']}, errors: {test_summary['errors']}."
                )

            return ToolResult(
                success=passed,
                data={
                    "validation_status": validation_status,
                    "exit_code": exit_code,
                    "docker_available": docker_avail,
                    "test_summary": test_summary,
                    "stdout": stdout,
                    "stderr": stderr,
                    "test_command": test_cmd
                },
                summary=summary,
                error=run_res.get("error"),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )
        finally:
            # 4. Clean up candidate workspace copy
            sandbox_manager.cleanup_workspace(candidate_ws)
