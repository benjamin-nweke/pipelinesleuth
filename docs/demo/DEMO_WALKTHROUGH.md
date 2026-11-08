# PipelineSleuth Demonstration Scenarios & Walkthrough

PipelineSleuth includes three reproducible failure scenarios designed to demonstrate autonomous AI investigation capabilities.

---

## Scenario A: Upstream Schema Drift (Customer ID vs. Client ID)

### Symptom & Context
A customer transaction pipeline aggregates monthly spending by customer. The upstream ingestion process changed its schema, renaming `customer_id` to `client_id`. When `pipeline.py` executes, it halts with:
```
KeyError: "Required column 'customer_id' missing from transaction dataset! Available columns: ['client_id', 'amount', 'transaction_date', 'status']"
```

### How to Run Demo
1. On the PipelineSleuth Dashboard, locate **Scenario A: Schema Drift** in the Reproducible Test Scenarios card.
2. Click **Investigate**.
3. Watch the live investigation stream:
   - Tool `analyze_logs` extracts the `KeyError: 'customer_id'` at `pipeline.py:18`.
   - Tool `inspect_schema` compares `data/transactions.csv` against `data/baseline_schema.csv` and discovers that `customer_id` is missing while `client_id` is present.
   - The agent rejects any hypothesis of data corruption, confirms the hypothesis of upstream column renaming, and formulates a patch in `pipeline.py`:
     ```python
     if "client_id" in df.columns:
         df = df.rename(columns={"client_id": "customer_id"})
     ```
4. Navigate to **Patch Review** to inspect the unified diff.
5. Click **Approve & Validate Fix** to run the candidate patch in isolation.
6. View the **Final Report** with exportable Markdown and HTML.

---

## Scenario B: Data Quality Failure (Unexpected Null Amounts)

### Symptom & Context
A financial ledger pipeline requires 100% record integrity on transactional amounts. Upstream ingest errors introduced null values into 2 records. A strict data quality check aborts execution with:
```
ValueError: Data quality check failed: Found 2 NULL values in critical financial field 'amount'.
```

### How to Run Demo
1. Click **Scenario B: Data Quality Failure** on the Dashboard.
2. The agent calls:
   - `analyze_logs`: localizes the `ValueError` at line 18 in `calculate_customer_aggregates`.
   - `profile_dataset`: discovers that `amount` has 2 nulls (33.3% missing rate).
   - `inspect_code`: analyzes the AST aggregation logic around `df.groupby("customer_id")`.
3. The agent proposes an explicit null-handling policy preserving financial ledger accuracy (e.g. imputing `0.0` or filtering invalid records with an audit trail).
4. Review and validate the patch in the candidate workspace.

---

## Scenario C: Type Mismatch (Dirty Strings in Numeric Field)

### Symptom & Context
A revenue pipeline casts transaction amounts to `float64`. The incoming dataset contains dirty non-numeric strings (`"pending"` and `"N/A"`), crashing `df['amount'].astype(float)` with:
```
ValueError: could not convert string to float: 'pending'
```

### How to Run Demo
1. Click **Scenario C: Type Mismatch** on the Dashboard.
2. The agent calls:
   - `analyze_logs`: identifies the `ValueError` at `pipeline.py:18`.
   - `profile_dataset`: detects `mixed_type_or_dirty_strings` with samples `['pending', 'N/A']`.
   - `inspect_code`: locates `.astype(float)`.
3. The agent formulates a patch replacing `.astype(float)` with robust numeric parsing:
   ```python
   amounts_numeric = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
   ```
4. The user approves the patch; tests pass cleanly with exit code 0.

