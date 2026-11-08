"""
Diagnostic Tool: analyze_logs
Parses log files and runtime output to extract exceptions, stack traces,
file locations, line numbers, execution stages, and preceding warning events.
"""
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class AnalyzeLogsTool(BaseDiagnosticTool):
    name = "analyze_logs"
    description = (
        "Parses log files or runtime output text to extract Python exceptions, full tracebacks, "
        "file references, line numbers, error messages, pipeline execution stages, and preceding warning events."
    )
    permission_level = "read_only"
    timeout_seconds = 20

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="log_path",
                    type="string",
                    description="Relative path to log file within the project. If not provided, raw_log_text must be supplied.",
                    required=False,
                    default=None
                ),
                ToolParameter(
                    name="raw_log_text",
                    type="string",
                    description="Direct log text to parse if no log file path is given.",
                    required=False,
                    default=None
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    def _scrub_sensitive_data(self, text: str) -> str:
        """Redacts sensitive tokens, API keys, passwords, and AWS credentials from logs."""
        # Redact Bearer tokens
        text = re.sub(r'(Bearer\s+)[A-Za-z0-9_\-\.]{15,}', r'\1[REDACTED_TOKEN]', text, flags=re.IGNORECASE)
        # Redact AWS Access Key IDs
        text = re.sub(r'\b(AKIA[0-9A-Z]{16})\b', r'[REDACTED_AWS_KEY]', text)
        # Redact API keys, tokens, passwords in key=val or key: val patterns
        text = re.sub(
            r'((?:api[_-]?key|secret|password|passwd|token|auth)\s*[:=]\s*["\']?)[a-zA-Z0-9_\-\.]{8,}(["\']?)',
            r'\1[REDACTED_SECRET]\2',
            text,
            flags=re.IGNORECASE
        )
        return text

    def _parse_content(self, text: str) -> Dict[str, Any]:
        lines = text.splitlines()
        
        # Regex for Python traceback line: File "...", line 123, in func
        tb_frame_regex = re.compile(r'File\s+["\']([^"\']+)["\'],\s+line\s+(\d+),\s+in\s+([a-zA-Z0-9_<>\.]+)')
        # Regex for Exception class: ExceptionName: message
        exception_regex = re.compile(r'^([a-zA-Z0-9_\.]*(?:Error|Exception|Interrupt|Exit|Fault|Warning))(?::\s*(.*))?$')
        # Regex for stage headers e.g. [STAGE: ...] or [INFO] Running stage ...
        stage_regex = re.compile(r'\[?(?:STAGE|Step|Phase)\]?[:\s]+([a-zA-Z0-9_\-\s]+)', re.IGNORECASE)
        # Regex for timestamp
        timestamp_regex = re.compile(r'\d{4}-\d{2}-\d{2}[T\s]\d{2}:\d{2}:\d{2}(?:\.\d+)?')

        tracebacks: List[Dict[str, Any]] = []
        current_traceback: Optional[Dict[str, Any]] = None
        current_frames: List[Dict[str, Any]] = []
        stages_detected: List[str] = []
        preceding_warnings: List[str] = []
        fatal_error: Optional[Dict[str, Any]] = None

        for idx, line in enumerate(lines):
            # Check stage indicators
            stage_match = stage_regex.search(line)
            if stage_match:
                stages_detected.append(stage_match.group(1).strip())

            # Check warnings
            if "warning" in line.lower() and not current_traceback:
                preceding_warnings.append(line.strip())

            # Detect start of Traceback
            if "Traceback (most recent call last):" in line:
                if current_traceback:
                    current_traceback["frames"] = current_frames
                    tracebacks.append(current_traceback)
                current_traceback = {
                    "start_line": idx + 1,
                    "frames": [],
                    "exception_type": "Unknown",
                    "exception_message": "",
                }
                current_frames = []
                continue

            if current_traceback:
                frame_match = tb_frame_regex.search(line)
                if frame_match:
                    current_frames.append({
                        "file": frame_match.group(1),
                        "line": int(frame_match.group(2)),
                        "function": frame_match.group(3),
                        "code_line": lines[idx + 1].strip() if idx + 1 < len(lines) else ""
                    })
                else:
                    exc_match = exception_regex.match(line.strip())
                    if exc_match and not line.strip().startswith("File "):
                        current_traceback["exception_type"] = exc_match.group(1)
                        current_traceback["exception_message"] = exc_match.group(2) or ""
                        current_traceback["frames"] = current_frames
                        tracebacks.append(current_traceback)
                        fatal_error = {
                            "exception_type": current_traceback["exception_type"],
                            "exception_message": current_traceback["exception_message"],
                            "last_frame": current_frames[-1] if current_frames else None
                        }
                        current_traceback = None
                        current_frames = []

        if current_traceback and not fatal_error:
            current_traceback["frames"] = current_frames
            tracebacks.append(current_traceback)
            if current_frames:
                fatal_error = {
                    "exception_type": current_traceback["exception_type"],
                    "exception_message": current_traceback["exception_message"],
                    "last_frame": current_frames[-1]
                }

        # If no standard traceback was found, search for generic error lines
        if not fatal_error:
            for line in lines:
                exc_match = exception_regex.match(line.strip())
                if exc_match:
                    fatal_error = {
                        "exception_type": exc_match.group(1),
                        "exception_message": exc_match.group(2) or "",
                        "last_frame": None
                    }
                    break

        return {
            "total_lines": len(lines),
            "stages_detected": list(dict.fromkeys(stages_detected)),
            "preceding_warnings": preceding_warnings[-5:],
            "tracebacks": tracebacks,
            "fatal_error": fatal_error
        }

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        log_rel = arguments.get("log_path")
        raw_text = arguments.get("raw_log_text")

        content = ""
        source_desc = ""
        if log_rel:
            log_file = (base_dir / log_rel).resolve()
            if not str(log_file).startswith(str(base_dir)):
                return ToolResult(
                    success=False,
                    summary="Security error: Path traversal detected.",
                    error="Path traversal attempted.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )
            if not log_file.exists():
                return ToolResult(
                    success=False,
                    summary=f"Log file '{log_rel}' not found.",
                    error="File not found.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )
            content = log_file.read_text(encoding="utf-8", errors="ignore")
            source_desc = f"file '{log_rel}'"
        elif raw_text:
            content = raw_text
            source_desc = "raw log buffer"
        else:
            return ToolResult(
                success=False,
                summary="Neither 'log_path' nor 'raw_log_text' was provided.",
                error="Missing required parameter.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        scrubbed_content = self._scrub_sensitive_data(content)
        parsed = self._parse_content(scrubbed_content)
        fatal = parsed.get("fatal_error")

        if fatal:
            frame_info = ""
            if fatal.get("last_frame"):
                lf = fatal["last_frame"]
                frame_info = f" at {lf['file']}:{lf['line']} in {lf['function']}() -> '{lf['code_line']}'"
            summary = (
                f"Log analysis of {source_desc} identified fatal error: {fatal['exception_type']}: "
                f"'{fatal['exception_message']}'{frame_info}. "
                f"Stages found: {parsed['stages_detected'] or 'None'}."
            )
        else:
            summary = f"Log analysis of {source_desc} did not find explicit Python exceptions in {parsed['total_lines']} lines."

        return ToolResult(
            success=True,
            data=parsed,
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

