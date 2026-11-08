"""
Customer Transaction Pipeline - Schema Drift Scenario
Reads transaction records and aggregates monthly spending per customer.
"""
import sys
import pandas as pd
from pathlib import Path


def load_transactions(data_path: str) -> pd.DataFrame:
    print(f"[STAGE: INGESTION] Loading transaction records from {data_path}")
    df = pd.read_csv(data_path)
    print(f"[INFO] Ingested {len(df)} records. Columns: {list(df.columns)}")
    return df


def transform_and_aggregate(df: pd.DataFrame) -> pd.DataFrame:
    print("[STAGE: TRANSFORMATION] Aggregating spend by customer_id")
    # This line fails reproducibly if 'customer_id' was renamed to 'client_id' by upstream drift
    if "customer_id" not in df.columns:
        raise KeyError(
            f"Required column 'customer_id' missing from transaction dataset! "
            f"Available columns: {list(df.columns)}"
        )
    
    summary = df.groupby("customer_id")["amount"].sum().reset_index()
    summary.rename(columns={"amount": "total_spend"}, inplace=True)
    return summary


def run_pipeline():
    data_file = Path("data/transactions.csv")
    if not data_file.exists():
        data_file = Path("transactions.csv")
    
    try:
        raw_df = load_transactions(str(data_file))
        summary_df = transform_and_aggregate(raw_df)
        output_file = Path("output_summary.csv")
        summary_df.to_csv(output_file, index=False)
        print(f"[STAGE: EXPORT] Successfully wrote customer summary to {output_file}")
    except Exception as e:
        print(f"[FATAL ERROR] Pipeline terminated unexpectedly: {type(e).__name__}: {str(e)}", file=sys.stderr)
        raise


if __name__ == "__main__":
    run_pipeline()

