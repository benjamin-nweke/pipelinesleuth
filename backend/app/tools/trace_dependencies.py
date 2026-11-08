"""
Diagnostic Tool: trace_dependencies
Constructs pipeline dependency and data lineage graph connecting data inputs,
processing stages, intermediate outputs, and downstream consumers.
"""
import ast
import os
import re
import time
from pathlib import Path
from typing import Dict, Any, List, Set
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class TraceDependenciesTool(BaseDiagnosticTool):
    name = "trace_dependencies"
    description = (
        "Constructs a dependency graph of pipeline stages, inputs, and outputs to evaluate upstream and downstream impact."
    )
    permission_level = "read_only"
    timeout_seconds = 20

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="target_component",
                    type="string",
                    description="Optional specific script, stage or dataset to analyze impact for. If omitted, graphs the entire project.",
                    required=False,
                    default=None
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    def _extract_io(self, py_file: Path) -> Dict[str, Any]:
        inputs: Set[str] = set()
        outputs: Set[str] = set()
        imports: Set[str] = set()

        try:
            content = py_file.read_text(encoding="utf-8", errors="ignore")
            tree = ast.parse(content)

            for node in ast.walk(tree):
                # Check imports
                if isinstance(node, ast.Import):
                    for a in node.names:
                        imports.add(a.name)
                elif isinstance(node, ast.ImportFrom):
                    if node.module:
                        imports.add(node.module)

                # Check string literals passed to read/write calls
                if isinstance(node, ast.Call):
                    func_name = ""
                    if isinstance(node.func, ast.Attribute):
                        func_name = node.func.attr
                    elif isinstance(node.func, ast.Name):
                        func_name = node.func.id

                    if func_name.startswith("read_") or func_name == "load":
                        if node.args and isinstance(node.args[0], ast.Constant):
                            inputs.add(str(node.args[0].value))
                    elif func_name.startswith("to_") or func_name in ("save", "dump", "write"):
                        if node.args and isinstance(node.args[0], ast.Constant):
                            outputs.add(str(node.args[0].value))

            # Also regex scan for common data file patterns e.g. "data/....csv"
            matches = re.findall(r'["\']([^"\']+\.(?:csv|parquet|json|jsonl|sqlite|db))["\']', content)
            for m in matches:
                if any(kw in content[max(0, content.find(m)-50):content.find(m)] for kw in ("read", "load", "input", "source", "from")):
                    inputs.add(m)
                elif any(kw in content[max(0, content.find(m)-50):content.find(m)] for kw in ("to_", "save", "write", "dest", "output")):
                    outputs.add(m)
                else:
                    inputs.add(m)
        except Exception:
            pass

        return {
            "inputs": sorted(list(inputs)),
            "outputs": sorted(list(outputs)),
            "imports": sorted(list(imports))
        }

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        target = arguments.get("target_component")

        stages: Dict[str, Any] = {}
        all_py_files = list(base_dir.rglob("*.py"))

        for f in all_py_files:
            rel = str(f.relative_to(base_dir))
            if any(part.startswith(".") or part in ("venv", ".venv", "__pycache__", "tests") for part in f.parts):
                continue
            stages[rel] = self._extract_io(f)

        # Build graph edges
        nodes = []
        edges = []

        for stage, data in stages.items():
            nodes.append({"id": stage, "type": "stage", "label": stage})
            for inp in data["inputs"]:
                nodes.append({"id": inp, "type": "dataset", "label": inp})
                edges.append({"from": inp, "to": stage, "relationship": "reads"})
            for out in data["outputs"]:
                nodes.append({"id": out, "type": "dataset", "label": out})
                edges.append({"from": stage, "to": out, "relationship": "writes"})

        # Deduplicate nodes
        unique_nodes = {n["id"]: n for n in nodes}.values()

        # Upstream / downstream impact if target is provided
        upstream = []
        downstream = []
        if target:
            for edge in edges:
                if edge["to"] == target:
                    upstream.append(edge["from"])
                if edge["from"] == target:
                    downstream.append(edge["to"])

        summary = (
            f"Dependency analysis traced {len(stages)} pipeline stages and {len(edges)} data flow connections. "
        )
        if target:
            summary += f"Target '{target}' has {len(upstream)} upstream dependencies ({upstream}) and {len(downstream)} downstream consumers ({downstream})."

        return ToolResult(
            success=True,
            data={
                "stages": stages,
                "nodes": list(unique_nodes),
                "edges": edges,
                "target_analysis": {
                    "target": target,
                    "upstream": upstream,
                    "downstream": downstream
                } if target else None
            },
            summary=summary,
            execution_time_ms=int((time.time() - start_time) * 1000)
        )
