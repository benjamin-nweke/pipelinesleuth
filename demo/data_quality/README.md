# Scenario B: Data Quality Failure

## Problem Description
The pipeline aggregates financial transactions per customer.
Due to an upstream data capture bug, 2 records in `amount` contain null values.
The pipeline contains a quality assertion that halts execution when null amounts are detected.

## How to Reproduce
Run:
```bash
python pipeline.py
```
Expected output:
```
ValueError: Data quality check failed: Found 2 NULL values in critical financial field 'amount'. Ledger reconciliation cannot proceed without explicit handling policy.
```

## Expected Resolution
The agent should profile the dataset using `profile_dataset` to determine the exact null percentage (33.3%), inspect the transformation logic via `inspect_code`, propose an explicit null-handling policy (e.g. imputing 0.0 or isolating null records into an audit log while calculating valid totals), and validate that `test_pipeline.py` passes.

