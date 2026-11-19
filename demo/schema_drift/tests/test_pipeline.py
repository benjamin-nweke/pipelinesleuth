"""
Regression tests for Customer Transaction Pipeline (Schema Drift)
"""
import pytest
import pandas as pd
from pathlib import Path
from pipeline import transform_and_aggregate, run_pipeline


def test_transform_and_aggregate_with_legacy_and_drifted_columns():
    """Drifted schema: 'client_id' is normalized to 'customer_id' and aggregates correctly."""
    df_drifted = pd.DataFrame({
        "client_id": ["C1", "C2", "C1"],
        "amount": [10.0, 20.0, 15.0]
    })
    
    summary = transform_and_aggregate(df_drifted)
    assert len(summary) == 2
    c1_spend = summary[summary["customer_id"] == "C1"]["total_spend"].values[0]
    assert c1_spend == 25.0


def test_transform_and_aggregate_with_legacy_schema():
    """Legacy schema: 'customer_id' continues working unchanged."""
    df_legacy = pd.DataFrame({
        "customer_id": ["C1", "C2", "C1"],
        "amount": [10.0, 20.0, 15.0]
    })
    
    summary = transform_and_aggregate(df_legacy)
    assert len(summary) == 2
    c1_spend = summary[summary["customer_id"] == "C1"]["total_spend"].values[0]
    assert c1_spend == 25.0


def test_transform_and_aggregate_ambiguous_identifiers_raises_value_error():
    """Ambiguity check: having both 'customer_id' and 'client_id' raises an explicit error."""
    df_ambiguous = pd.DataFrame({
        "customer_id": ["C1"],
        "client_id": ["CLI_99"],
        "amount": [50.0]
    })
    
    with pytest.raises(ValueError, match="Ambiguous dataset schema"):
        transform_and_aggregate(df_ambiguous)


def test_transform_and_aggregate_missing_both_identifiers_raises_key_error():
    """Missing identifiers: lacking both customer_id and client_id raises an informative KeyError."""
    df_missing = pd.DataFrame({
        "account_id": ["A1"],
        "amount": [50.0]
    })
    
    with pytest.raises(KeyError, match="missing from transaction dataset"):
        transform_and_aggregate(df_missing)


def test_run_pipeline_end_to_end(tmp_path, monkeypatch):
    """End-to-end execution of run_pipeline produces the expected aggregation summary."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    sample_csv = data_dir / "transactions.csv"
    sample_csv.write_text(
        "client_id,amount,transaction_date,status\n"
        "C1,100.0,2026-10-01,completed\n"
        "C2,200.0,2026-10-02,completed\n"
        "C1,50.0,2026-10-03,completed\n"
    )
    
    run_pipeline()
    
    output_file = tmp_path / "output_summary.csv"
    assert output_file.exists()
    out_df = pd.read_csv(output_file)
    assert "customer_id" in out_df.columns
    assert "total_spend" in out_df.columns
    c1_row = out_df[out_df["customer_id"] == "C1"]
    assert c1_row["total_spend"].values[0] == 150.0
