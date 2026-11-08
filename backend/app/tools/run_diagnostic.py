"""
Diagnostic Tool: run_diagnostic
Executes controlled diagnostic operations inside the isolated sandbox environment.
"""
import time
from typing import Dict, Any, List
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult
from backend.app.sandbox.manager import sandbox_manager

ALLOWED_COMMAND_PREFIXES = ["python", "python3", "pytest"]


class RunDiagnosticTool(BaseDiagnosticTool):
    name = "run_diagnostic"
    description = (
        "Executes a controlled diagnostic command (e.g., executing a script with diagnostic flags or running a test) "
        "inside the isolated sandbox container. Returns actual stdout, stderr, and exit codes."
    )
    permission_level = "sandboxed_exec"
    timeout_seconds = 45

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="command",
                    type="array",
                    description="List of command tokens, e.g. ['python', 'pipeline.py'] or ['pytest', 'tests/test_pipeline.py'].",
                    required=True
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        command = arguments.get("command", [])

        if not command or not isinstance(command, list):
            return ToolResult(
                success=False,
                summary="Command must be a non-empty list of command tokens.",
                error="Invalid command format.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        cmd_binary = command[0]
        if cmd_binary not in ALLOWED_COMMAND_PREFIXES:
            return ToolResult(
                success=False,
                summary=f"Disallowed command binary '{cmd_binary}'. Only controlled commands ({ALLOWED_COMMAND_PREFIXES}) are permitted.",
                error="Disallowed command binary.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        # Run inside sandbox
        res = await sandbox_manager.run_in_sandbox(
            workspace_path=project_path,
            command=command,
            timeout_seconds=self.timeout_seconds
        )

        exit_code = res.get("exit_code")
        stdout = res.get("stdout", "")
        stderr = res.get("stderr", "")

        summary = f"Command {' '.join(command)} exited with code {exit_code}."
        if not res.get("docker_available"):
            summary += " Note: Docker container isolation was unavailable."
        elif exit_code != 0:
            err_line = stderr.strip().splitlines()[-1] if stderr.strip() else "Process returned non-zero exit code."
            summary += f" Failure snippet: {err_line}"

        return ToolResult(
            success=res.get("success", False),
            data={
                "command": command,
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "docker_available": res.get("docker_available", False),
            },
            summary=summary,
            error=res.get("error"),
            execution_time_ms=res.get("execution_time_ms", int((time.time() - start_time) * 1000))
        )
