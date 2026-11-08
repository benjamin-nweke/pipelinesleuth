"""
Unit tests for Diagnostic Tools
"""
import pytest
from pathlib import Path
from backend.app.tools.registry import tool_registry
from backend.app.tools.inspect_project import InspectProjectTool
from backend.app.tools.inspect_schema import InspectSchemaTool
from backend.app.tools.profile_dataset import ProfileDatasetTool
from backend.app.tools.analyze_logs import AnalyzeLogsTool
from backend.app.tools.inspect_code import InspectCodeTool
from backend.app.tools.trace_dependencies import TraceDependenciesTool
from backend.app.tools.propose_patch import ProposePatchTool
from backend.app.tools.test_hypothesis import TestHypothesisTool


@pytest.fixture
def demo_drift_path():
    return str(Path(__file__).parent.parent.parent / "demo" / "schema_drift")


@pytest.fixture
def demo_quality_path():
    return str(Path(__file__).parent.parent.parent / "demo" / "data_quality")


@pytest.fixture
def demo_mismatch_path():
    return str(Path(__file__).parent.parent.parent / "demo" / "type_mismatch")


@pytest.mark.asyncio
async def test_inspect_project(demo_drift_path):
    tool = InspectProjectTool()
    res = await tool.execute(demo_drift_path, {})
    assert res.success is True
    assert "pipeline.py" in res.data["pipeline_stages"]
    assert "data/transactions.csv" in res.data["data_files"]
    assert len(res.data["test_files"]) >= 1


@pytest.mark.asyncio
async def test_inspect_schema_drift(demo_drift_path):
    tool = InspectSchemaTool()
    res = await tool.execute(demo_drift_path, {
        "dataset_path": "data/transactions.csv",
        "baseline_path": "data/baseline_schema.csv"
    })
    assert res.success is True
    diffs = res.data["differences"]
    assert diffs["has_drift"] is True
    assert "customer_id" in diffs["missing_columns"]
    assert "client_id" in diffs["unexpected_columns"]


@pytest.mark.asyncio
async def test_profile_dataset_quality(demo_quality_path):
    tool = ProfileDatasetTool()
    res = await tool.execute(demo_quality_path, {
        "dataset_path": "data/transactions.csv"
    })
    assert res.success is True
    assert res.data["total_rows"] == 6
    # Should detect null values in 'amount'
    null_anomalies = [a for a in res.data["anomalies"] if a["column"] == "amount" and a["type"] == "null_values_detected"]
    assert len(null_anomalies) == 1
    assert null_anomalies[0]["null_count"] == 2


@pytest.mark.asyncio
async def test_profile_dataset_type_mismatch(demo_mismatch_path):
    tool = ProfileDatasetTool()
    res = await tool.execute(demo_mismatch_path, {
        "dataset_path": "data/transactions.csv"
    })
    assert res.success is True
    # Column 'amount' contains 'pending' and 'N/A'
    anomalies = [a for a in res.data["anomalies"] if a["column"] == "amount"]
    assert len(anomalies) >= 1
    assert any(a["type"] == "mixed_type_or_dirty_strings" for a in anomalies)


@pytest.mark.asyncio
async def test_analyze_logs(demo_drift_path):
    tool = AnalyzeLogsTool()
    res = await tool.execute(demo_drift_path, {
        "log_path": "logs/pipeline.log"
    })
    assert res.success is True
    fatal = res.data["fatal_error"]
    assert fatal is not None
    assert fatal["exception_type"] == "KeyError"
    assert "customer_id" in fatal["exception_message"]


@pytest.mark.asyncio
async def test_analyze_logs_credential_scrubbing():
    tool = AnalyzeLogsTool()
    raw_log = """
2026-11-08 12:00:00 [WARNING] Connecting to db with password="superSecretPassword123"
2026-11-08 12:00:01 [WARNING] Auth header: Bearer ya29.a0AfH6SMB_someLongSecretTokenHere12345
2026-11-08 12:00:02 [WARNING] S3 failed with AKIAIOSFODNN7EXAMPLE
Traceback (most recent call last):
  File "pipeline.py", line 12, in connect
    raise ConnectionError("Auth failed for api_key=sk_live_abcdef1234567890")
ConnectionError: Auth failed for api_key=sk_live_abcdef1234567890
"""
    res = await tool.execute(".", {"raw_log_text": raw_log})
    assert res.success is True
    fatal = res.data["fatal_error"]
    assert fatal is not None
    assert "sk_live" not in fatal["exception_message"]
    assert "[REDACTED_SECRET]" in fatal["exception_message"]
    # Verify warnings / raw frame doesn't leak secrets
    assert "superSecretPassword123" not in str(res.data)
    assert "ya29" not in str(res.data)
    assert "AKIAIOSFODNN7EXAMPLE" not in str(res.data)
    assert "[REDACTED_AWS_KEY]" in str(res.data)


@pytest.mark.asyncio
async def test_inspect_code_ast(demo_drift_path):
    tool = InspectCodeTool()
    res = await tool.execute(demo_drift_path, {
        "file_path": "pipeline.py",
        "focus_line": 18
    })
    assert res.success is True
    assert len(res.data["functions"]) >= 2
    # Should detect columns referenced like customer_id, amount
    cols = [r["column"] for r in res.data["column_references"]]
    assert "customer_id" in cols or "amount" in cols


@pytest.mark.asyncio
async def test_trace_dependencies(demo_drift_path):
    tool = TraceDependenciesTool()
    res = await tool.execute(demo_drift_path, {})
    assert res.success is True
    assert len(res.data["nodes"]) >= 1


@pytest.mark.asyncio
async def test_propose_patch_syntax_validation(demo_drift_path):
    tool = ProposePatchTool()
    # Test valid python syntax
    valid_res = await tool.execute(demo_drift_path, {
        "target_file": "pipeline.py",
        "original_snippet": 'if "customer_id" not in df.columns:',
        "replacement_code": 'if "client_id" in df.columns:\n        df = df.rename(columns={"client_id": "customer_id"})\n    if "customer_id" not in df.columns:',
        "explanation": "Map client_id to customer_id"
    })
    assert valid_res.success is True
    assert "unified_diff" in valid_res.data
    assert "--- a/pipeline.py" in valid_res.data["unified_diff"]

    # Test invalid syntax rejection
    invalid_res = await tool.execute(demo_drift_path, {
        "target_file": "pipeline.py",
        "original_snippet": 'if "customer_id" not in df.columns:',
        "replacement_code": 'def invalid syntax (({{{',
        "explanation": "Bad syntax"
    })
    assert invalid_res.success is False
    assert "SyntaxError" in invalid_res.error


@pytest.mark.asyncio
async def test_test_hypothesis():
    tool = TestHypothesisTool()
    res = await tool.execute(".", {
        "hypothesis_id": "HYP-01",
        "hypothesis_statement": "Upstream column renaming caused KeyError",
        "supporting_evidence_ids": ["EVD-1", "EVD-2"],
        "contradicting_evidence_ids": [],
        "evaluation_reasoning": "Schema diff and stack trace confirm customer_id was replaced by client_id."
    })
    assert res.success is True
    assert res.data["conclusion"] == "confirmed"
