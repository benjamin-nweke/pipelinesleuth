"""
Diagnostic Tool: inspect_schema
Compares datasets, inspects column names, types, nullability, and detects schema drift.
"""
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class InspectSchemaTool(BaseDiagnosticTool):
    name = "inspect_schema"
    description = (
        "Inspects the schema of a CSV or Parquet dataset. When a baseline dataset or expected schema "
        "is provided, compares schemas to identify missing columns, unexpected new columns, renamed fields, "
        "type incompatibilities, and unexpected nullable fields."
    )
    permission_level = "read_only"
    timeout_seconds = 20

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="dataset_path",
                    type="string",
                    description="Relative path to the target dataset within the project (CSV or Parquet).",
                    required=True
                ),
                ToolParameter(
                    name="baseline_path",
                    type="string",
                    description="Optional relative path to baseline/reference dataset to compare against.",
                    required=False,
                    default=None
                ),
                ToolParameter(
                    name="expected_columns",
                    type="array",
                    description="Optional list of expected column names.",
                    required=False,
                    default=None
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    def _read_schema(self, full_path: Path) -> Dict[str, Any]:
        ext = full_path.suffix.lower()
        if ext == ".csv":
            # Read first 50 rows to infer schema quickly
            df = pd.read_csv(full_path, nrows=50)
        elif ext == ".parquet":
            df = pd.read_parquet(full_path)
        elif ext in (".json", ".jsonl"):
            df = pd.read_json(full_path, lines=(ext == ".jsonl"), nrows=50)
        else:
            raise ValueError(f"Unsupported file format: {ext}")

        schema = {}
        for col in df.columns:
            schema[str(col)] = {
                "dtype": str(df[col].dtype),
                "nullable": bool(df[col].isnull().any())
            }
        return {"columns": list(df.columns), "fields": schema, "total_columns": len(df.columns)}

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        dataset_rel = arguments.get("dataset_path", "")
        baseline_rel = arguments.get("baseline_path")
        expected_cols = arguments.get("expected_columns")

        target_file = (base_dir / dataset_rel).resolve()
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
                summary=f"Dataset file '{dataset_rel}' does not exist.",
                error="File not found.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        try:
            target_schema = self._read_schema(target_file)
        except Exception as e:
            return ToolResult(
                success=False,
                summary=f"Failed to read schema from '{dataset_rel}': {str(e)}",
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        differences: Dict[str, Any] = {
            "has_drift": False,
            "missing_columns": [],
            "unexpected_columns": [],
            "type_mismatches": [],
            "renamed_candidates": []
        }

        # Compare with baseline file if provided
        if baseline_rel:
            baseline_file = (base_dir / baseline_rel).resolve()
            if baseline_file.exists() and str(baseline_file).startswith(str(base_dir)):
                try:
                    baseline_schema = self._read_schema(baseline_file)
                    base_cols = set(baseline_schema["columns"])
                    target_cols = set(target_schema["columns"])

                    missing = list(base_cols - target_cols)
                    unexpected = list(target_cols - base_cols)

                    differences["missing_columns"] = missing
                    differences["unexpected_columns"] = unexpected

                    # Detect possible renames (e.g. customer_id vs client_id)
                    for m in missing:
                        for u in unexpected:
                            if m.lower() in u.lower() or u.lower() in m.lower() or ("id" in m and "id" in u):
                                differences["renamed_candidates"].append({
                                    "original": m,
                                    "current": u,
                                    "likelihood": "high" if "id" in m and "id" in u else "medium"
                                })

                    # Compare common types
                    common = base_cols.intersection(target_cols)
                    for c in common:
                        b_type = baseline_schema["fields"][c]["dtype"]
                        t_type = target_schema["fields"][c]["dtype"]
                        if b_type != t_type:
                            differences["type_mismatches"].append({
                                "column": c,
                                "expected_dtype": b_type,
                                "actual_dtype": t_type
                            })

                    differences["has_drift"] = bool(missing or unexpected or differences["type_mismatches"])
                except Exception as e:
                    differences["comparison_error"] = str(e)

        # Compare with explicit expected columns list if provided
        if expected_cols:
            target_cols = set(target_schema["columns"])
            exp_set = set(expected_cols)
            missing = list(exp_set - target_cols)
            unexpected = list(target_cols - exp_set)
            if missing:
                differences["missing_columns"] = list(set(differences["missing_columns"] + missing))
                differences["has_drift"] = True
            if unexpected:
                differences["unexpected_columns"] = list(set(differences["unexpected_columns"] + unexpected))
                differences["has_drift"] = True

        summary_parts = [f"Dataset '{dataset_rel}' has {target_schema['total_columns']} columns: {list(target_schema['fields'].keys())}."]
        if differences["has_drift"]:
            summary_parts.append("SCHEMA DRIFT DETECTED!")
            if differences["missing_columns"]:
                summary_parts.append(f"Missing columns: {differences['missing_columns']}.")
            if differences["unexpected_columns"]:
                summary_parts.append(f"New/unexpected columns: {differences['unexpected_columns']}.")
            if differences["renamed_candidates"]:
                summary_parts.append(f"Candidate renamed columns: {differences['renamed_candidates']}.")
            if differences["type_mismatches"]:
                summary_parts.append(f"Type mismatches: {differences['type_mismatches']}.")
        else:
            summary_parts.append("No schema drift detected.")

        return ToolResult(
            success=True,
            data={
                "target_schema": target_schema,
                "differences": differences
            },
            summary=" ".join(summary_parts),
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

