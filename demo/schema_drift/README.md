# Scenario A: Schema Drift

## Problem Description
The upstream transaction ingest system renamed the unique identifier column from `customer_id` to `client_id`.
When the transformation stage executes, it looks specifically for `df['customer_id']`, leading to a fatal `KeyError`.

## How to Reproduce
Run:
```bash
python pipeline.py
```
Expected output:
```
KeyError: "Required column 'customer_id' missing from transaction dataset! Available columns: ['client_id', 'amount', 'transaction_date', 'status']"
```

## Expected Resolution
The agent should identify the missing `customer_id` and the presence of `client_id` through `inspect_schema` or `analyze_logs`, propose mapping or normalizing `client_id` -> `customer_id` in `pipeline.py`, and validate that `test_pipeline.py` passes.
