"""
Diagnostic Tool: profile_dataset
Computes statistical profiles, data distributions, null counts, duplicate records,
and identifies data quality anomalies without dumping huge datasets to the LLM.
"""
import time
from pathlib import Path
from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult


class ProfileDatasetTool(BaseDiagnosticTool):
    name = "profile_dataset"
    description = (
        "Profiles a CSV or Parquet dataset to compute row and column counts, dtypes, null counts/percentages, "
        "duplicate records, distinct value counts, numeric distributions, and anomalous values (e.g. unexpected strings in numbers)."
    )
    permission_level = "read_only"
    timeout_seconds = 30

    def get_definition(self) -> DiagnosticToolDefinition:
        return DiagnosticToolDefinition(
            name=self.name,
            description=self.description,
            parameters=[
                ToolParameter(
                    name="dataset_path",
                    type="string",
                    description="Relative path to the CSV or Parquet dataset within the project.",
                    required=True
                ),
                ToolParameter(
                    name="target_columns",
                    type="array",
                    description="Optional list of specific columns to focus on. If omitted, profiles all columns.",
                    required=False,
                    default=None
                ),
                ToolParameter(
                    name="max_rows",
                    type="integer",
                    description="Maximum number of rows to sample for profiling. Default 50000.",
                    required=False,
                    default=50000
                )
            ],
            permission_level=self.permission_level,
            timeout_seconds=self.timeout_seconds
        )

    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        base_dir = Path(project_path)
        dataset_rel = arguments.get("dataset_path", "")
        target_columns = arguments.get("target_columns")
        max_rows = arguments.get("max_rows", 50000)

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
                summary=f"Dataset file '{dataset_rel}' not found.",
                error="File not found.",
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        try:
            ext = target_file.suffix.lower()
            if ext == ".csv":
                df = pd.read_csv(target_file, nrows=max_rows)
            elif ext == ".parquet":
                df = pd.read_parquet(target_file)
                if len(df) > max_rows:
                    df = df.head(max_rows)
            elif ext in (".json", ".jsonl"):
                df = pd.read_json(target_file, lines=(ext == ".jsonl"), nrows=max_rows)
            else:
                return ToolResult(
                    success=False,
                    summary=f"Unsupported dataset format '{ext}'.",
                    error="Format not supported.",
                    execution_time_ms=int((time.time() - start_time) * 1000)
                )
        except Exception as e:
            return ToolResult(
                success=False,
                summary=f"Failed to read dataset '{dataset_rel}': {str(e)}",
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        total_rows = len(df)
        total_cols = len(df.columns)
        duplicate_rows = int(df.duplicated().sum())

        cols_to_profile = target_columns if target_columns else list(df.columns)
        column_profiles: Dict[str, Any] = {}
        anomalies: List[Dict[str, Any]] = []

        for col in cols_to_profile:
            if col not in df.columns:
                anomalies.append({
                    "column": col,
                    "type": "column_not_found",
                    "description": f"Requested column '{col}' does not exist in dataset."
                })
                continue

            series = df[col]
            null_count = int(series.isnull().sum())
            null_pct = round((null_count / total_rows * 100), 2) if total_rows > 0 else 0.0
            distinct_count = int(series.nunique(dropna=True))

            profile: Dict[str, Any] = {
                "dtype": str(series.dtype),
                "null_count": null_count,
                "null_percentage": null_pct,
                "distinct_count": distinct_count,
            }

            # Check for high null anomaly
            if null_count > 0:
                anomalies.append({
                    "column": col,
                    "type": "null_values_detected",
                    "null_count": null_count,
                    "null_percentage": null_pct,
                    "description": f"Column '{col}' has {null_count} null/missing values ({null_pct}% of total records)."
                })

            # Numeric distribution profiling
            if pd.api.types.is_numeric_dtype(series):
                valid_nums = series.dropna()
                if not valid_nums.empty:
                    profile["numeric_stats"] = {
                        "min": float(valid_nums.min()),
                        "max": float(valid_nums.max()),
                        "mean": round(float(valid_nums.mean()), 3),
                        "median": round(float(valid_nums.median()), 3),
                        "std": round(float(valid_nums.std()), 3) if len(valid_nums) > 1 else 0.0
                    }
            elif series.dtype == object or pd.api.types.is_string_dtype(series):
                # Check for type mismatch: strings that look numeric or contain dirty strings
                non_null = series.dropna().astype(str)
                sample_values = non_null.head(5).tolist()
                profile["sample_values"] = sample_values
                
                # Test how many values can/cannot be converted to numeric
                numeric_converted = pd.to_numeric(non_null, errors='coerce')
                num_failures = int(numeric_converted.isnull().sum())
                num_successes = int(numeric_converted.notnull().sum())
                
                if num_successes > 0 and num_failures > 0:
                    dirty_samples = non_null[numeric_converted.isnull()].unique()[:5].tolist()
                    anomalies.append({
                        "column": col,
                        "type": "mixed_type_or_dirty_strings",
                        "numeric_parsable_count": num_successes,
                        "unparsable_count": num_failures,
                        "examples": dirty_samples,
                        "description": f"Column '{col}' contains mixed types: {num_failures} values cannot be parsed as numeric (e.g. {dirty_samples})."
                    })

            column_profiles[col] = profile

        summary_parts = [
            f"Dataset '{dataset_rel}': {total_rows} rows, {total_cols} columns, {duplicate_rows} duplicate rows."
        ]
        if anomalies:
            summary_parts.append(f"Detected {len(anomalies)} potential data anomalies:")
            for a in anomalies[:3]:
                summary_parts.append(f"[{a['column']}] {a['description']}")
            if len(anomalies) > 3:
                summary_parts.append(f"... and {len(anomalies) - 3} more.")
        else:
            summary_parts.append("No critical data quality anomalies detected.")

        return ToolResult(
            success=True,
            data={
                "dataset_path": dataset_rel,
                "total_rows": total_rows,
                "total_columns": total_cols,
                "duplicate_rows": duplicate_rows,
                "column_profiles": column_profiles,
                "anomalies": anomalies
            },
            summary=" ".join(summary_parts),
            execution_time_ms=int((time.time() - start_time) * 1000)
        )

