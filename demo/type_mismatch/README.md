# Scenario C: Type Mismatch

## Problem Description
The pipeline parses orders and expects all `amount` records to be convertable to numeric floats.
However, dirty string values such as `'pending'` and `'N/A'` were passed in the raw data, causing `.astype(float)` to crash with `ValueError: could not convert string to float: 'pending'`.

## How to Reproduce
Run:
```bash
python pipeline.py
```
Expected output:
```
ValueError: could not convert string to float: 'pending'
```

## Expected Resolution
The agent should inspect the log with `analyze_logs`, profile the dataset with `profile_dataset` to detect `mixed_type_or_dirty_strings` in column `amount`, inspect the AST with `inspect_code`, propose replacing `.astype(float)` with `pd.to_numeric(df['amount'], errors='coerce').fillna(0.0)`, and validate that `test_pipeline.py` passes.

