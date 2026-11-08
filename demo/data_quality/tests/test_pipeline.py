"""
Regression tests for Financial Ledger Pipeline (Data Quality)
"""
import pytest
import pandas as pd
from pipeline import calculate_customer_aggregates


def test_calculate_customer_aggregates_with_null_handling():
    # Test that when null amounts are imputed with 0.0 or explicitly filtered/handled according to policy,
    # the ledger aggregation computes correctly
    test_df = pd.DataFrame({
        "transaction_id": ["TX-1", "TX-2", "TX-3"],
        "customer_id": ["C1", "C1", "C2"],
        "amount": [100.0, None, 50.0]
    })
    
    # After proper patch (e.g. df = df.copy(); df['amount'] = df['amount'].fillna(0.0)),
    # aggregation must produce accurate customer counts and sums
    res = calculate_customer_aggregates(test_df)
    assert len(res) == 2
    c1_row = res[res["customer_id"] == "C1"].iloc[0]
    assert c1_row["total_amount"] == 100.0
