"""
Regression tests for Revenue Calculation Pipeline (Type Mismatch)
"""
import pytest
import pandas as pd
from pipeline import parse_and_compute_revenue


def test_parse_and_compute_revenue_with_non_numeric_strings():
    # Test that invalid strings like 'pending' or 'N/A' are safely handled via pd.to_numeric(errors='coerce')
    df = pd.DataFrame({
        "order_id": ["O1", "O2", "O3"],
        "amount": ["100.0", "pending", "50.0"]
    })
    
    # After patch, gross revenue should be 150.0 (safely ignoring or zeroing unparsed strings)
    total = parse_and_compute_revenue(df)
    assert total == 150.0
