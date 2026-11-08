"""
Diagnostic Tool: inspect_project
Inventories project files, stages, entry points, configs, requirements, and tests.
"""
import os
import ast
import time
from pathlib import Path
from typing import Dict, Any, List
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult

UNSAFE_EXTENSIONS = {".exe", ".bin", ".dll", ".so", ".dylib", ".class", ".jar"}


class InspectProjectTool(BaseDiagnosticTool):
    name = "inspect_project"
    description = (
        "Builds a comprehensive inventory of project files, detects entry points, pipeline stages, "
        "configuration files, requirements, input/output data locations, and existing tests."
    )
    permission_level = "read_only"
    timeout_seconds = 15

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="subdirectory",
                    type="string",
                    description="Optional subdirectory within the project to inspect. Defaults to root.",
                    required=False,
                    default=""
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        subdir = arguments.get("subdirectory", "")
        target_dir = (base_dir / subdir).resolve()

        # Path traversal guard
        if not str(target_dir).startswith(str(base_dir)):
            return ToolResult(
                success=False,
                summary="Security error: Path traversal detected.",
                error="Path traversal attempted outside project boundary.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        if not target_dir.exists():
            return ToolResult(
                success=False,
                summary=f"Directory '{subdir}' not found in project.",
                error="Target directory does not exist.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        inventory: List[Dict[str, Any]] = []
        entry_points: List[str] = []
        pipeline_stages: List[str] = []
        config_files: List[str] = []
        requirements_files: List[str] = []
        test_files: List[str] = []
        data_files: List[str] = []
        log_files: List[str] = []
        rejected_files: List[str] = []

        for root, dirs, files in os.walk(target_dir):
            # Skip hidden dirs or virtualenvs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("__pycache__", "venv", ".venv", "node_modules")]
            for file in files:
                file_path = Path(root) / file
                rel_path = str(file_path.relative_to(base_dir))
                ext = file_path.suffix.lower()

                if ext in UNSAFE_EXTENSIONS:
                    rejected_files.append(rel_path)
                    continue

                size = file_path.stat().st_size
                item = {
                    "path": rel_path,
                    "filename": file,
                    "extension": ext,
                    "size_bytes": size,
                    "category": "unknown"
                }

                if ext == ".py":
                    if file.startswith("test_") or file.endswith("_test.py"):
                        item["category"] = "test"
                        test_files.append(rel_path)
                    else:
                        item["category"] = "code"
                        # Check for main entry point or pipeline stage
                        try:
                            content = file_path.read_text(encoding="utf-8", errors="ignore")
                            tree = ast.parse(content)
                            has_main = False
                            for node in ast.walk(tree):
                                if isinstance(node, ast.If):
                                    if isinstance(node.test, ast.Compare):
                                        # check __name__ == '__main__'
                                        left = getattr(node.test.left, "id", None)
                                        if left == "__name__":
                                            has_main = True
                                elif isinstance(node, ast.FunctionDef):
                                    if node.name in ("main", "run", "pipeline", "execute", "process"):
                                        has_main = True
                            if has_main:
                                entry_points.append(rel_path)
                            pipeline_stages.append(rel_path)
                        except Exception:
                            pass
                elif ext in (".csv", ".parquet", ".json", ".jsonl"):
                    item["category"] = "data"
                    data_files.append(rel_path)
                elif ext in (".log", ".txt") and ("log" in file.lower() or "error" in file.lower()):
                    item["category"] = "log"
                    log_files.append(rel_path)
                elif ext in (".yaml", ".yml", ".ini", ".conf", ".toml") or file.startswith(".env"):
                    item["category"] = "config"
                    config_files.append(rel_path)
                elif "requirement" in file.lower() or file == "Pipfile" or file == "pyproject.toml":
                    item["category"] = "requirements"
                    requirements_files.append(rel_path)
                else:
                    item["category"] = "other"

                inventory.append(item)

        data = {
            "total_files": len(inventory),
            "entry_points": entry_points,
            "pipeline_stages": pipeline_stages,
            "test_files": test_files,
            "data_files": data_files,
            "log_files": log_files,
            "config_files": config_files,
            "requirements_files": requirements_files,
            "rejected_unsafe_files": rejected_files,
            "inventory": inventory[:50],  # Limit to 50 for summary
        }

        summary = (
            f"Project inventory discovered {len(inventory)} files: {len(pipeline_stages)} code files, "
            f"{len(data_files)} datasets, {len(log_files)} logs, {len(test_files)} tests. "
            f"Candidate entry points: {', '.join(entry_points) or 'None detected'}."
        )

        return ToolResult(
            success=True,
            data=data,
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )
