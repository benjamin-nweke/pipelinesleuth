"""
Customer Transaction Pipeline - Data Quality (Null Values) Scenario
Computes financial ledger totals and metrics per customer.
"""
import sys
import pandas as pd
from pathlib import Path


def load_transactions(data_path: str) -> pd.DataFrame:
    print(f"[STAGE: INGESTION] Loading transaction records from {data_path}")
    df = pd.read_csv(data_path)
    return df


def calculate_customer_aggregates(df: pd.DataFrame) -> pd.DataFrame:
    print("[STAGE: AGGREGATION] Calculating totals and mean transaction values")
    
    # Financial integrity validation: strict accounting rule
    null_amounts = df["amount"].isnull().sum()
    if null_amounts > 0:
        raise ValueError(
            f"Data quality check failed: Found {null_amounts} NULL values in critical financial field 'amount'. "
            f"Ledger reconciliation cannot proceed without explicit handling policy."
        )

    # Aggregation
    result = df.groupby("customer_id").agg(
        total_amount=("amount", "sum"),
        avg_amount=("amount", "mean"),
        tx_count=("transaction_id", "count")
    ).reset_index()

    return result


def run_pipeline():
    data_file = Path("data/transactions.csv")
    if not data_file.exists():
        data_file = Path("transactions.csv")
        
    try:
        raw_df = load_transactions(str(data_file))
        summary = calculate_customer_aggregates(raw_df)
        summary.to_csv("customer_ledger_summary.csv", index=False)
        print("[STAGE: EXPORT] Customer ledger summary generated successfully.")
    except Exception as e:
        print(f"[FATAL ERROR] Ledger pipeline failed: {type(e).__name__}: {str(e)}", file=sys.stderr)
        raise


if __name__ == "__main__":
    run_pipeline()

