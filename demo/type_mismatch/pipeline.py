"""
Revenue Calculation Pipeline - Type Mismatch Scenario
Converts raw string amount values to float and computes gross revenue.
"""
import sys
import pandas as pd
from pathlib import Path


def load_raw_transactions(file_path: str) -> pd.DataFrame:
    print(f"[STAGE: INGEST] Loading transaction batch from {file_path}")
    df = pd.read_csv(file_path, dtype={"order_id": str, "amount": str})
    return df


def parse_and_compute_revenue(df: pd.DataFrame) -> float:
    print("[STAGE: TYPE_CONVERSION] Casting transaction amounts to float64")
    
    # Incompatible string values ('pending', 'N/A') cause ValueError: could not convert string to float
    amounts_numeric = df["amount"].astype(float)
    
    total_rev = amounts_numeric.sum()
    print(f"[STAGE: SUMMARY] Gross computed revenue: ${total_rev:,.2f}")
    return float(total_rev)


def run_pipeline():
    data_file = Path("data/transactions.csv")
    if not data_file.exists():
        data_file = Path("transactions.csv")

    try:
        raw_data = load_raw_transactions(str(data_file))
        gross = parse_and_compute_revenue(raw_data)
        print(f"[INFO] Pipeline executed successfully. Total: {gross}")
    except Exception as e:
        print(f"[FATAL ERROR] Type conversion failed: {type(e).__name__}: {str(e)}", file=sys.stderr)
        raise


if __name__ == "__main__":
    run_pipeline()

