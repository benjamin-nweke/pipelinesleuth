"""
Regression tests for Customer Transaction Pipeline (Schema Drift)
"""
import pytest
import pandas as pd
from pipeline import transform_and_aggregate


def test_transform_and_aggregate_with_legacy_and_drifted_columns():
    # If the fix maps client_id -> customer_id or handles client_id
    df_drifted = pd.DataFrame({
        "client_id": ["C1", "C2", "C1"],
        "amount": [10.0, 20.0, 15.0]
    })
    
    # Should work without raising KeyError
    summary = transform_and_aggregate(df_drifted)
    assert len(summary) == 2
    # Check spend
    c1_spend = summary[summary["customer_id"] == "C1"]["total_spend"].values[0]
    assert c1_spend == 25.0
