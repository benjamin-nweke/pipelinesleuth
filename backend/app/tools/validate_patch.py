"""
Diagnostic Tool: validate_patch
Executes candidate patch in a separate, isolated workspace copy.
Executes pytest and entrypoint scripts to verify fix and check for regressions.
Returns genuine process exit code, stdout, stderr, and test summary.
"""
import time
import uuid
import re
import os
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult
from backend.app.sandbox.manager import sandbox_manager


def validate_and_resolve_target_file(
    workspace_root: Path,
    target_file_rel: str
) -> Tuple[bool, Optional[Path], Optional[str]]:
    """
    Rigorously validates and resolves a target file path within the candidate workspace.
    Guarantees:
    - Path must be non-empty and non-blank
    - Rejects absolute paths (e.g. /etc/passwd)
    - Rejects parent directory traversal (..)
    - Rejects null bytes
    - Normalizes the path
    - Resolves within workspace_root and confirms strict containment
    - Rejects any symlinks traversing outside the workspace
    - Ensures the file exists and is a regular file
    """
    if not target_file_rel or not isinstance(target_file_rel, str) or not target_file_rel.strip():
        return False, None, "Target file path must be a non-empty string."

    if "\x00" in target_file_rel:
        return False, None, "Security error: Null byte detected in file path."

    raw_path = Path(target_file_rel)

    # 1. Reject absolute paths
    if raw_path.is_absolute() or target_file_rel.startswith(("/", "\\")):
        return False, None, f"Security error: Absolute target paths are forbidden: '{target_file_rel}'"

    # 2. Reject '..' traversal anywhere in the path components
    if ".." in raw_path.parts:
        return False, None, f"Security error: Path traversal ('..') is strictly forbidden: '{target_file_rel}'"

    # 3. Clean and normalize
    clean_rel = os.path.normpath(target_file_rel)
    if clean_rel.startswith("..") or clean_rel == ".":
        return False, None, f"Security error: Invalid normalized target path: '{target_file_rel}'"

    ws_resolved = workspace_root.resolve()
    candidate_path = (ws_resolved / clean_rel).resolve()

    # 4. Strict workspace containment check
    try:
        candidate_path.relative_to(ws_resolved)
    except ValueError:
        return False, None, f"Security error: Target file resolves outside candidate workspace: '{target_file_rel}'"

    if not str(candidate_path).startswith(str(ws_resolved) + os.sep) and candidate_path != ws_resolved:
        return False, None, f"Security error: Target path escapes candidate workspace: '{target_file_rel}'"

    # 5. Check for symlink traversal
    curr = ws_resolved
    for part in Path(clean_rel).parts:
        curr = curr / part
        if curr.is_symlink():
            resolved_link = curr.resolve()
            try:
                resolved_link.relative_to(ws_resolved)
            except ValueError:
                return False, None, f"Security error: Symlink traversal detected in target path: '{target_file_rel}'"

    # 6. Check existence
    if not candidate_path.exists():
        return False, None, f"Target file '{clean_rel}' not found in candidate workspace."

    # 7. Verify regular file (reject directories, devices, etc.)
    if not candidate_path.is_file():
        return False, None, f"Security error: Target path '{clean_rel}' is not a regular file."

    return True, candidate_path, None


class ValidatePatchTool(BaseDiagnosticTool):
    name = "validate_patch"
    description = (
        "Validates an approved patch by creating an isolated candidate workspace copy, applying the patch, "
        "and running test suites and pipeline execution. Reports actual exit codes, stdout, stderr, and regressions."
    )
    permission_level = "human_approval_required"
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

        if not isinstance(original_snippet, str) or not original_snippet.strip():
            return ToolResult(
                success=False,
                data={"patch_applied": False},
                summary="Patch validation failed: original_snippet must be a non-empty string.",
                error="Missing or empty original_snippet.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        if not isinstance(replacement_code, str) or not replacement_code.strip():
            return ToolResult(
                success=False,
                data={"patch_applied": False},
                summary="Patch validation failed: replacement_code must be a non-empty string.",
                error="Missing or empty replacement_code.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        if not isinstance(test_cmd, list) or not test_cmd or not all(isinstance(arg, str) and arg for arg in test_cmd):
            return ToolResult(
                success=False,
                data={"patch_applied": False},
                summary="Patch validation failed: test_command must be a non-empty list of strings.",
                error="Invalid test_command argument.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        # 1. Create separate candidate workspace copy
        candidate_ws = sandbox_manager.create_isolated_workspace(
            source_project_path=project_path,
            candidate_name=f"cand_val_{uuid.uuid4().hex[:12]}"
        )

        try:
            # 2. Securely validate and resolve target path in candidate workspace
            is_valid, candidate_file, path_err = validate_and_resolve_target_file(
                Path(candidate_ws), target_file_rel
            )
            if not is_valid or candidate_file is None:
                return ToolResult(
                    success=False,
                    data={"patch_applied": False},
                    summary=f"Path validation failed: {path_err}",
                    error=path_err,
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

            content = candidate_file.read_text(encoding="utf-8")
            if original_snippet not in content:
                return ToolResult(
                    success=False,
                    data={"patch_applied": False},
                    summary="Original snippet could not be matched in candidate workspace file.",
                    error="Snippet mismatch.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

            new_content = content.replace(original_snippet, replacement_code, 1)
            if new_content == content:
                return ToolResult(
                    success=False,
                    data={"patch_applied": False},
                    summary="Patch replacement produced no changes in candidate workspace file.",
                    error="No-op patch replacement.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

            candidate_file.write_text(new_content, encoding="utf-8")
            if candidate_file.read_text(encoding="utf-8") == content:
                return ToolResult(
                    success=False,
                    data={"patch_applied": False},
                    summary="Candidate file write verification failed: content remained unchanged.",
                    error="Patch write verification failed.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )

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
                    "patch_applied": True,
                    "target_file": target_file_rel,
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

