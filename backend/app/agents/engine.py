"""
Autonomous AI Investigation Engine
Orchestrates bounded investigation loop, state transitions, tool execution,
evidence collection, hypothesis testing, loop prevention, and event broadcasting.
"""
import asyncio
import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.models.investigation import (
    Investigation, InvestigationEvent, EvidenceRecord, Hypothesis, ToolExecution
)
from backend.app.models.patch import PatchProposal
from backend.app.models.project import Project, ProjectFile
from backend.app.services.report_service import generate_or_update_investigation_report
from backend.app.tools.registry import tool_registry
from backend.app.agents.provider import AIProvider, ModelProviderError
from backend.app.agents.prompts import INVESTIGATION_SYSTEM_PROMPT
from backend.app.schemas.agent import AgentEvidenceInput


RECOGNIZED_CONTAINER_ROOTS = (
    "/app",
    "/workspace",
    "/candidate_workspace",
    "/candidate",
)

CAUSAL_PHRASES = (
    "caused by",
    "due to",
    "because of",
    "as a result of",
    "resulting in",
    "results in",
    "leads to",
    "leading to",
    "triggered by",
    "triggers",
    "originates from",
    "attributed to",
    "stems from",
    "reason for",
    "reason being",
    "root cause",
    "consequence of",
)

BEHAVIORAL_KEYWORDS = frozenset({
    "drop", "drops", "dropping",
    "fail", "fails", "failing",
    "crash", "crashes", "crashing",
    "corrupt", "corrupts", "corrupting",
    "bypass", "bypasses", "bypassing",
    "leak", "leaks", "leaking",
    "skip", "skips", "skipping",
    "improper", "incorrectly", "erroneous",
    "remove", "removes", "removing",
    "detect", "detects", "detecting",
    "prevent", "prevents", "preventing",
    "filter", "filters", "filtering",
    "sanitize", "sanitizes", "sanitizing",
    "truncate", "truncates", "truncating",
    "mutate", "mutates", "mutating",
    "handle", "handles", "handling"
})

COMMON_OBSERVATIONAL_TERMS = frozenset({
    "the", "a", "an", "in", "on", "at", "to", "for", "of", "with", "by", "from",
    "is", "was", "are", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "and", "or", "but", "if", "then", "else", "when", "where", "which",
    "that", "this", "these", "those", "it", "its", "as", "into", "during", "after", "before",
    "while", "whereas", "not", "out", "any", "all", "zero", "none", "only", "both",
    "file", "files", "path", "line", "lines", "lineno", "code", "source", "module", "script",
    "raised", "threw", "thrown", "occurred", "found", "detected", "observed", "reported",
    "shows", "recorded", "indicates", "contains", "column", "columns", "row", "rows",
    "dataset", "data", "dataframe", "df", "table", "field", "fields", "value", "values",
    "record", "records", "count", "total", "percentage", "percent", "type", "dtype", "inferred",
    "null", "nulls", "nan", "missing", "duplicate", "duplicates", "empty", "distinct", "unique",
    "function", "functions", "class", "classes", "method", "methods", "def", "import", "imports",
    "package", "packages", "dependency", "dependencies", "depends", "depended", "requires", "required",
    "statement", "statements", "declares", "installed", "requirement", "used", "consumed", "consumer", "consumers",
    "pattern", "parameter", "parameters", "argument", "arguments", "signature", "reads", "writes", "stage", "stages",
    "node", "nodes", "edge", "edges",
    "traceback", "stacktrace", "frame", "frames", "error", "errors", "exception", "exceptions",
    "failure", "fail", "failed", "fatal", "log", "logs", "output", "result", "results",
    "status", "test", "tests", "check", "checks", "syntax", "ast", "inspect", "profile",
    "pipeline", "number", "actual", "expected", "present", "defined", "definition", "call", "calls",
    "drift", "schema", "diff", "added", "dropped", "removed", "new", "unexpected", "incompatible", "mismatch",
    "directly", "upstream", "downstream"
})


def contains_causal_explanation(text: str) -> bool:
    """Returns True if the text asserts an explanatory or causal relationship."""
    t_lower = text.lower()
    return any(p in t_lower for p in CAUSAL_PHRASES)


def contains_behavioral_claim(text: str) -> bool:
    """Returns True if the text asserts runtime behavior, filtering, mutation, or handling actions."""
    words = set(re.findall(r'[a-zA-Z0-9_]+', text.lower()))
    return bool(words & BEHAVIORAL_KEYWORDS)


def _has_uncorroborated_words(obs_lower: str, allowed_terms: set[str]) -> bool:
    """
    Checks whether an observation contains substantive factual tokens outside of
    common observational terms and the explicitly corroborated entities/fields/values
    for the specific structured claim.
    """
    words_in_obs = set(re.findall(r'[a-zA-Z0-9_]{3,}', obs_lower))
    generic_words = {"error", "missing", "failed", "invalid", "exception", "bug", "crash", "issue", "problem", "terminat"}
    expanded_allowed = set(allowed_terms)
    for t in allowed_terms:
        expanded_allowed.update(re.findall(r'[a-zA-Z0-9_]{2,}', str(t).lower()))
    substantive = words_in_obs - COMMON_OBSERVATIONAL_TERMS - generic_words - expanded_allowed
    return bool(substantive)


def _verify_log_telemetry(
    ev_input: AgentEvidenceInput,
    src_norm: str,
    te: ToolExecution,
    out_data: Dict[str, Any],
    project_paths: set[str],
    workspace_dir: Optional[Path] = None
) -> Tuple[str, bool, Optional[str]]:
    """
    Verifies exception & log evidence preserving relationships between:
    - source file path and frame location
    - exact line number in that frame
    - exception type and exception message tied to that specific traceback
    """
    obs_lower = ev_input.observation.lower()

    tracebacks = out_data.get("tracebacks", [])
    fatal_error = out_data.get("fatal_error")

    if not tracebacks and not fatal_error:
        return "unverified", False, None

    tb_entries: List[Dict[str, Any]] = []
    if isinstance(tracebacks, list):
        tb_entries.extend(tb for tb in tracebacks if isinstance(tb, dict))
    if fatal_error and isinstance(fatal_error, dict):
        tb_entries.append(fatal_error)

    target_line = ev_input.line_number
    m_line = re.search(r'\bline\s+(\d+)\b', obs_lower) or re.search(r'\.py:(\d+)\b', obs_lower)
    obs_line = int(m_line.group(1)) if m_line else None
    claimed_line = target_line if target_line is not None else obs_line

    # Collect all (tb, frame) pairs matching the source file and claimed line
    matching_pairs: List[Tuple[Dict[str, Any], Dict[str, Any]]] = []
    for tb in tb_entries:
        frames = tb.get("frames", [])
        if not frames and tb.get("last_frame"):
            frames = [tb["last_frame"]]

        for fr in frames:
            if not isinstance(fr, dict):
                continue
            fr_file = fr.get("file", "")
            resolved_fr = resolve_frame_to_project_relative(fr_file, project_paths, workspace_dir=workspace_dir)
            if resolved_fr and resolved_fr == src_norm:
                fr_line = fr.get("line")
                if claimed_line is None or fr_line == claimed_line:
                    # Also enforce obs_line if both target_line and obs_line were given
                    if obs_line is None or fr_line == obs_line:
                        matching_pairs.append((tb, fr))

    if not matching_pairs:
        return "unverified", False, None

    generic_words = {"error", "missing", "failed", "invalid", "exception", "bug", "crash", "issue", "problem", "terminat"}
    words_in_obs = set(re.findall(r'[a-zA-Z0-9_]{3,}', obs_lower))
    non_generic_words = words_in_obs - generic_words
    known_py_exceptions = {
        "keyerror", "valueerror", "typeerror", "filenotfounderror",
        "zerodivisionerror", "indexerror", "attributeerror", "importerror",
        "modulenotfounderror", "runtimeerror", "memoryerror", "assertionerror",
        "timeouterror", "permissionerror", "oserror", "ioerror", "syntaxerror",
        "nameerror", "lookuperror", "overflowerror", "recursionerror", "eoferror",
        "connectionerror", "unicodeerror", "unicodedecodeerror", "jsondecodeerror"
    }
    mentioned_known_exceptions = words_in_obs & known_py_exceptions
    claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', obs_lower)

    best_log_res: Optional[Tuple[str, bool, Optional[str]]] = None

    for matching_tb, matching_frame in matching_pairs:
        exc_type = str(matching_tb.get("exception_type", "")).lower()
        exc_msg = str(matching_tb.get("exception_message") or matching_tb.get("message") or "").lower()
        code_line = str(matching_frame.get("code_line", "")).lower()

        if mentioned_known_exceptions and (not exc_type or exc_type not in mentioned_known_exceptions):
            continue

        # If column_name was explicitly asserted on this log evidence, it must appear in THIS traceback's message or code line
        if ev_input.column_name:
            col_low = ev_input.column_name.lower()
            if col_low not in exc_msg and col_low not in code_line:
                continue

        matched_exc_type = bool(exc_type and exc_type in obs_lower)
        msg_tokens = [
            w.lower() for w in re.findall(r'[a-zA-Z0-9_]{3,}', exc_msg)
            if w.lower() not in generic_words
        ]
        code_tokens = [
            w.lower() for w in re.findall(r'[a-zA-Z0-9_]{3,}', code_line)
            if w.lower() not in generic_words
        ]
        matched_exc_msg = any(tok in obs_lower for tok in msg_tokens)

        if not ((matched_exc_type or matched_exc_msg) and non_generic_words):
            continue

        # Reject any numerical claim that contradicts THIS matching traceback record
        valid_nums = set()
        if matching_frame.get("line") is not None:
            valid_nums.add(str(matching_frame["line"]))
        valid_nums.update(re.findall(r'\b\d+(?:\.\d+)?%?\b', exc_msg))
        if claimed_numbers and any(num not in valid_nums for num in claimed_numbers):
            continue

        if contains_causal_explanation(obs_lower) or contains_behavioral_claim(obs_lower):
            if best_log_res is None:
                best_log_res = ("inference", True, te.id)
            continue

        allowed_terms = {exc_type} | set(msg_tokens) | set(code_tokens) | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower()))
        if matching_frame.get("function"):
            allowed_terms.add(str(matching_frame["function"]).lower())
        allowed_terms.update(valid_nums)

        if _has_uncorroborated_words(obs_lower, allowed_terms):
            if best_log_res is None:
                best_log_res = ("inference", True, te.id)
            continue

        return "verified", False, te.id

    if best_log_res is not None:
        return best_log_res
    return "unverified", False, None


def _verify_single_dataset_clause(
    clause_lower: str,
    target_col: Optional[str],
    col_profiles: Dict[str, Any],
    anomalies: List[Dict[str, Any]],
    all_known_cols: set[str],
    total_rows: Any,
    duplicate_rows: Any,
    src_norm: str,
    te_id: str,
    extra_allowed_cols: Optional[set[str]] = None
) -> Tuple[str, bool, Optional[str]]:
    """Verifies a single dataset-related claim clause bound strictly to its target column/measurement."""
    is_null_claim = any(kw in clause_lower for kw in (
        "null", "nan", "missing value", "missing values", "empty value", "empty values", "none value", "none values"
    ))
    is_dup_claim = any(kw in clause_lower for kw in ("duplicate", "repeated row", "repeated record"))
    is_row_count_claim = any(kw in clause_lower for kw in ("row count", "total rows", "records loaded", "rows in dataset"))
    is_type_claim = any(kw in clause_lower for kw in ("data type", "datatype", "type is", "dtype", "inferred type"))
    is_distinct_claim = any(kw in clause_lower for kw in ("distinct count", "distinct values", "unique values", "distinct_count"))
    is_numeric_stat_claim = any(
        re.search(r'\b' + kw + r'\b', clause_lower)
        for kw in ("min", "minimum", "max", "maximum", "mean", "average", "median", "std", "standard deviation")
    )
    claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', clause_lower)

    # 1. Null / Missing Value Claim
    if is_null_claim:
        if not target_col or target_col not in all_known_cols:
            return "unverified", False, None

        prof = col_profiles.get(target_col, {})
        col_anomalies = [a for a in anomalies if isinstance(a, dict) and str(a.get("column", "")).lower() == target_col]

        # Must actually have null telemetry for this column (never default missing field to 0)
        null_from_prof = prof.get("null_count")
        null_from_anom = [a.get("null_count") for a in col_anomalies if "null" in str(a.get("type", "")).lower() and a.get("null_count") is not None]
        if null_from_prof is None and not null_from_anom:
            return "unverified", False, None

        actual_null_count = max([v for v in ([null_from_prof] + null_from_anom) if v is not None])
        actual_null_pct = prof.get("null_percentage")
        if actual_null_pct is None:
            for a in col_anomalies:
                if a.get("null_percentage") is not None:
                    actual_null_pct = a["null_percentage"]
                    break

        claims_zero_nulls = bool(
            re.search(r'\b(?:0|zero|no|without)\s+(?:null|nulls|nan|missing|empty)\b', clause_lower)
            or re.search(r'\bnull(?:_|\s)?(?:count|values?|percentage)\s*(?:is|are|=|:|of)?\s*(?:0|0\.0%?)\b', clause_lower)
            or re.search(r'\bmissing\s+(?:values?|count)\s*(?:is|are|=|:|of)?\s*(?:0|0\.0%?)\b', clause_lower)
        )
        if claims_zero_nulls:
            if actual_null_count != 0:
                return "unverified", False, None
        elif actual_null_count == 0:
            # Column has 0 nulls; claiming it has null values is FALSE
            return "unverified", False, None

        valid_count_strs = {str(actual_null_count)}
        valid_pct_with_sign: set[str] = set()
        valid_pct_bare: set[str] = set()
        if actual_null_pct is not None:
            valid_pct_bare.update({
                str(actual_null_pct),
                f"{actual_null_pct:.1f}",
                f"{actual_null_pct:.2f}",
                f"{actual_null_pct:.0f}",
                f"{actual_null_pct:g}",
            })
            valid_pct_with_sign = {f"{p}%" for p in valid_pct_bare}

        has_pct_word = bool(re.search(r'\b(?:percent|percentage)\b', clause_lower))
        if claimed_numbers:
            for num in claimed_numbers:
                if num.endswith("%"):
                    if num not in valid_pct_with_sign:
                        return "unverified", False, None
                else:
                    if num in valid_count_strs:
                        continue
                    if has_pct_word and num in valid_pct_bare:
                        continue
                    # Do NOT allow null_percentage bare numbers or unrelated telemetry numbers to masquerade as null_count!
                    return "unverified", False, None

        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id

        allowed_terms = {target_col} | valid_count_strs | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        if valid_pct_with_sign or (has_pct_word and valid_pct_bare):
            allowed_terms.update(valid_pct_with_sign)
            allowed_terms.update(valid_pct_bare)
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id

        return "verified", False, te_id

    # 2. Duplicate rows claim
    elif is_dup_claim:
        if duplicate_rows is None:
            return "unverified", False, None
        claims_zero_dups = bool(
            re.search(r'\b(?:0|zero|no|without)\s+duplicate', clause_lower)
            or re.search(r'\bduplicate_?(?:rows|count)?\s*(?:is|are|=|:)?\s*0\b', clause_lower)
        )
        if claims_zero_dups:
            if duplicate_rows != 0:
                return "unverified", False, None
        elif duplicate_rows == 0:
            return "unverified", False, None

        if claimed_numbers and any(num != str(duplicate_rows) for num in claimed_numbers):
            return "unverified", False, None

        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id

        allowed_terms = {str(duplicate_rows)} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        if target_col:
            allowed_terms.add(target_col)
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id

        return "verified", False, te_id

    # 3. Total rows / Row count claim
    elif is_row_count_claim:
        if total_rows is None:
            return "unverified", False, None
        if claimed_numbers and any(num != str(total_rows) for num in claimed_numbers):
            return "unverified", False, None

        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id

        allowed_terms = {str(total_rows)} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        if target_col:
            allowed_terms.add(target_col)
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id

        return "verified", False, te_id

    # 4. Data type / Dtype claim
    elif is_type_claim and target_col:
        prof = col_profiles.get(target_col, {})
        actual_dtype = str(prof.get("dtype") or prof.get("inferred_type", "")).lower()
        if not actual_dtype or actual_dtype not in clause_lower:
            return "unverified", False, None
        if claimed_numbers:
            # Numbers unrelated to dtype strings must not sneak in unverified
            return "unverified", False, None
        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id
        allowed_terms = {target_col, actual_dtype} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id
        return "verified", False, te_id

    # 5. Distinct count claim for a specific column
    elif is_distinct_claim and target_col:
        prof = col_profiles.get(target_col, {})
        distinct_count = prof.get("distinct_count")
        if distinct_count is None:
            return "unverified", False, None
        if not claimed_numbers or any(num != str(distinct_count) for num in claimed_numbers):
            return "unverified", False, None
        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id
        allowed_terms = {target_col, str(distinct_count), "distinct", "unique"} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id
        return "verified", False, te_id

    # 6. Numeric stats claim (min, max, mean, median, std) for a specific column
    elif is_numeric_stat_claim and target_col:
        prof = col_profiles.get(target_col, {})
        num_stats = prof.get("numeric_stats") if isinstance(prof.get("numeric_stats"), dict) else {}
        if not num_stats:
            return "unverified", False, None

        stat_key_map = {
            "min": "min", "minimum": "min",
            "max": "max", "maximum": "max",
            "mean": "mean", "average": "mean",
            "median": "median",
            "std": "std", "standard deviation": "std",
        }
        valid_stat_nums: set[str] = set()
        allowed_terms = {target_col} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
        stat_vals_by_key: Dict[str, set[str]] = {}
        for phrase, stat_k in stat_key_map.items():
            if re.search(r'\b' + phrase + r'\b', clause_lower):
                val = num_stats.get(stat_k)
                if val is None:
                    # Claimed a numeric stat not present in this column's telemetry
                    return "unverified", False, None
                svals = {str(val)}
                if isinstance(val, (int, float)) and float(val).is_integer():
                    svals.add(str(int(val)))
                stat_vals_by_key[stat_k] = svals
                valid_stat_nums.update(svals)
                allowed_terms.update(phrase.split())
                allowed_terms.add(stat_k)

        # Enforce pairwise binding when a number explicitly follows a stat keyword (prevents swapping min/max)
        for m_pair in re.finditer(
            r'\b(min|minimum|max|maximum|mean|average|median|std|standard deviation)\b(?:\s+(?:is|of|=|:))?\s+(\d+(?:\.\d+)?)\b',
            clause_lower
        ):
            sk = stat_key_map[m_pair.group(1)]
            claimed_val = m_pair.group(2)
            if claimed_val not in stat_vals_by_key.get(sk, set()):
                return "unverified", False, None

        if not valid_stat_nums or not claimed_numbers or any(num not in valid_stat_nums for num in claimed_numbers):
            return "unverified", False, None
        if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
            return "inference", True, te_id
        allowed_terms.update(valid_stat_nums)
        if _has_uncorroborated_words(clause_lower, allowed_terms):
            return "inference", True, te_id
        return "verified", False, te_id

    # 7. Column-specific non-null anomalies or ungrounded semantic claims
    elif target_col and target_col in all_known_cols:
        prof = col_profiles.get(target_col, {})
        col_anomalies = [a for a in anomalies if isinstance(a, dict) and str(a.get("column", "")).lower() == target_col]
        if col_anomalies:
            allowed_nums: set[str] = set()
            allowed_terms = {target_col} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | (extra_allowed_cols or set())
            for a in col_anomalies:
                if a.get("type"):
                    allowed_terms.update(str(a["type"]).lower().split("_"))
                for k in ("null_count", "unparsable_count", "numeric_parsable_count"):
                    if k in a and a[k] is not None:
                        allowed_nums.add(str(a[k]))
                if a.get("null_percentage") is not None:
                    pct = a["null_percentage"]
                    allowed_nums.update({f"{pct}%", f"{pct:.1f}%", f"{pct:.2f}%", f"{pct:.0f}%"})
            if claimed_numbers and any(num not in allowed_nums for num in claimed_numbers):
                return "unverified", False, None
            if contains_causal_explanation(clause_lower) or contains_behavioral_claim(clause_lower):
                return "inference", True, te_id
            allowed_terms.update(allowed_nums)
            if _has_uncorroborated_words(clause_lower, allowed_terms):
                return "inference", True, te_id
            return "verified", False, te_id
        else:
            # No anomalies for this column; must have actual profile fields (incomplete profile cannot support even inference)
            if not prof:
                return "unverified", False, None
            # Reject invented numbers when claiming ungrounded semantic attributes
            if claimed_numbers:
                return "unverified", False, None
            return "inference", True, te_id

    return "unverified", False, None


def _verify_dataset_telemetry(
    ev_input: AgentEvidenceInput,
    src_norm: str,
    te: ToolExecution,
    out_data: Dict[str, Any]
) -> Tuple[str, bool, Optional[str]]:
    """
    Verifies dataset profiling evidence strictly preserving associations between:
    - dataset column name and its own individual profile statistics
    - numerical values and the specific measurement (e.g. null_count vs total_rows)
    """
    obs_lower = ev_input.observation.lower()

    raw_col_profiles = out_data.get("column_profiles") or out_data.get("columns") or {}
    col_profiles: Dict[str, Any] = {}
    if isinstance(raw_col_profiles, dict):
        for k, v in raw_col_profiles.items():
            if isinstance(v, dict):
                col_profiles[k.lower()] = v

    anomalies: List[Dict[str, Any]] = out_data.get("anomalies") or []
    dataset_stats = out_data.get("dataset") if isinstance(out_data.get("dataset"), dict) else {}
    total_rows = out_data.get("total_rows") if out_data.get("total_rows") is not None else dataset_stats.get("total_rows")
    duplicate_rows = out_data.get("duplicate_rows") if out_data.get("duplicate_rows") is not None else dataset_stats.get("duplicate_rows")

    if not col_profiles and not anomalies and total_rows is None and duplicate_rows is None:
        return "unverified", False, None

    all_known_cols = set(col_profiles.keys())
    for a in anomalies:
        if isinstance(a, dict) and "column" in a:
            all_known_cols.add(str(a["column"]).lower())
    all_known_cols_sorted = sorted(all_known_cols)

    explicit_col = ev_input.column_name.lower() if ev_input.column_name else None
    words_in_obs = set(re.findall(r'[a-zA-Z0-9_]+', obs_lower))
    mentioned_cols = [c for c in all_known_cols_sorted if c in words_in_obs]

    # Reject structured column_name spoofing when observation targets a completely different column
    if explicit_col and mentioned_cols and explicit_col not in mentioned_cols:
        return "unverified", False, None

    global_metric_kws = (
        "duplicate", "repeated row", "repeated record",
        "row count", "total rows", "records loaded", "rows in dataset"
    )
    metric_claim_kws = (
        "null", "nan", "missing", "empty", "duplicate", "repeated row",
        "row count", "total rows", "records loaded", "dtype", "data type", "type is",
        "distinct", "unique", "min", "max", "mean", "median", "std", "mismatch", "anomaly"
    )
    clauses = [c.strip() for c in re.split(r'\.(?=\s|$)|[;]|\b(?:while|whereas)\b', obs_lower) if c.strip()]
    expanded_clauses: List[str] = []
    for cl in clauses:
        sub = [s.strip() for s in re.split(r',\s*(?:and\s+)?|\band\b', cl) if s.strip()]
        if len(sub) > 1 and all(
            (
                (any(re.search(r'\b' + re.escape(col) + r'\b', s) for col in all_known_cols_sorted) and any(kw in s for kw in metric_claim_kws))
                or any(gkw in s for gkw in global_metric_kws)
            )
            for s in sub
        ):
            expanded_clauses.extend(sub)
        else:
            expanded_clauses.append(cl)

    overall_status = "verified"
    overall_inf = False
    for cl in expanded_clauses:
        cl_cols = [c for c in all_known_cols_sorted if re.search(r'\b' + re.escape(c) + r'\b', cl)]
        cols_to_check = cl_cols if cl_cols else [explicit_col]
        extra_cols = set(cl_cols) if len(cl_cols) > 1 else None
        for t_col in cols_to_check:
            s, _, _ = _verify_single_dataset_clause(
                cl,
                t_col,
                col_profiles,
                anomalies,
                all_known_cols,
                total_rows,
                duplicate_rows,
                src_norm,
                te.id,
                extra_allowed_cols=extra_cols
            )
            if s == "unverified":
                return "unverified", False, None
            elif s == "inference":
                overall_status = "inference"
                overall_inf = True

    return overall_status, overall_inf, te.id


def _verify_code_ast_telemetry(
    ev_input: AgentEvidenceInput,
    src_norm: str,
    te: ToolExecution,
    out_data: Dict[str, Any],
    project_paths: Optional[set[str]] = None,
    workspace_dir: Optional[Path] = None
) -> Tuple[str, bool, Optional[str]]:
    """
    Verifies AST code inspection facts preserving associations between:
    - source file path
    - function/class definitions and line ranges
    Strictly prohibits deriving runtime behavior or performance optimizations from AST syntax alone.
    """
    obs_lower = ev_input.observation.lower()

    telemetry_file = out_data.get("file")
    if telemetry_file:
        tel_str = str(telemetry_file).strip()
        if os.path.isabs(tel_str) and project_paths:
            resolved_tel = resolve_frame_to_project_relative(tel_str, project_paths, workspace_dir=workspace_dir)
            if resolved_tel != src_norm:
                return "unverified", False, None
        else:
            norm_tel_file = os.path.normpath(tel_str).lstrip("/\\")
            if norm_tel_file != src_norm:
                return "unverified", False, None

    fns = out_data.get("functions", [])
    classes = out_data.get("classes", [])
    imports = out_data.get("imports", [])

    if not fns and not classes and not imports:
        return "unverified", False, None

    fn_dict = {f["name"].lower(): f for f in fns if isinstance(f, dict) and f.get("name")}
    class_dict = {c["name"].lower(): c for c in classes if isinstance(c, dict) and c.get("name")}
    import_names = {str(imp).lower() for imp in imports}

    # Check explicitly named function/method before falling back to any function name in observation
    stop_after_kw = COMMON_OBSERVATIONAL_TERMS | BEHAVIORAL_KEYWORDS | {
        "removes", "detects", "prevents", "filters", "sanitizes", "drops", "bypasses",
        "validates", "decrypts", "encrypts", "optimizes", "computes", "transforms",
        "executes", "processes", "modifies", "takes", "returns", "uses", "calls",
        "can", "will", "should", "may", "might", "could", "named", "called"
    }
    claimed_fn_name = None
    m_fn_before = re.search(r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s+(?:function|method)\b', obs_lower)
    m_fn_after = re.search(r'\b(?:function|def|method)\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', obs_lower)
    if m_fn_before and m_fn_before.group(1).lower() not in stop_after_kw:
        claimed_fn_name = m_fn_before.group(1).lower()
    elif m_fn_after and m_fn_after.group(1).lower() not in stop_after_kw:
        claimed_fn_name = m_fn_after.group(1).lower()

    if claimed_fn_name and claimed_fn_name not in fn_dict:
        # Explicitly claimed function does NOT exist in AST telemetry
        return "unverified", False, None

    matched_fn = fn_dict.get(claimed_fn_name) if claimed_fn_name else None
    if not matched_fn:
        for fn_name, f in fn_dict.items():
            if len(fn_name) > 2 and re.search(r'\b' + re.escape(fn_name) + r'\b', obs_lower):
                matched_fn = f
                break

    matched_cls = None
    if not matched_fn:
        for cls_name, c in class_dict.items():
            if len(cls_name) > 2 and re.search(r'\b' + re.escape(cls_name) + r'\b', obs_lower):
                matched_cls = c
                break
        if not matched_cls:
            m_cls_name = re.search(r'\bclass\s+([a-zA-Z_][a-zA-Z0-9_]*)\b', obs_lower)
            if m_cls_name and m_cls_name.group(1).lower() not in COMMON_OBSERVATIONAL_TERMS:
                if m_cls_name.group(1).lower() not in class_dict:
                    return "unverified", False, None

    matched_import = None
    if not matched_fn and not matched_cls:
        for imp in import_names:
            if len(imp) > 2 and re.search(r'\b' + re.escape(imp) + r'\b', obs_lower):
                matched_import = imp
                break

    if not matched_fn and not matched_cls and not matched_import:
        return "unverified", False, None

    # Validate explicit column_name against referenced columns or function args if provided
    if ev_input.column_name:
        col_low = ev_input.column_name.lower()
        col_refs = {str(cr.get("column", "")).lower() for cr in out_data.get("column_references", []) if isinstance(cr, dict)}
        fn_args = {str(a).lower() for a in ((matched_fn.get("arguments") or matched_fn.get("args") or []) if matched_fn else [])}
        if col_low not in col_refs and col_low not in fn_args:
            return "unverified", False, None

    # Line number & numeric verification strictly against the matched entity
    m_line = re.search(r'\bline\s+(\d+)\b', obs_lower)
    obs_line = int(m_line.group(1)) if m_line else None
    check_line = ev_input.line_number if ev_input.line_number is not None else obs_line

    valid_ast_nums: set[str] = set()
    if matched_fn:
        start_l = matched_fn.get("line_start", matched_fn.get("lineno"))
        end_l = matched_fn.get("line_end", matched_fn.get("end_lineno", start_l))
        if start_l is not None:
            valid_ast_nums.add(str(start_l))
            if end_l is not None:
                valid_ast_nums.add(str(end_l))
            if check_line is not None and not (start_l <= check_line <= (end_l or start_l) or start_l == check_line):
                return "unverified", False, None
            if obs_line is not None and not (start_l <= obs_line <= (end_l or start_l) or start_l == obs_line):
                return "unverified", False, None
    elif matched_cls:
        start_l = matched_cls.get("lineno")
        if start_l is not None:
            valid_ast_nums.add(str(start_l))
            if check_line is not None and start_l != check_line:
                return "unverified", False, None
            if obs_line is not None and start_l != obs_line:
                return "unverified", False, None

    if ev_input.line_number is not None:
        valid_ast_nums.add(str(ev_input.line_number))

    claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', obs_lower)
    if claimed_numbers and any(num not in valid_ast_nums for num in claimed_numbers):
        return "unverified", False, None

    # Enforce Rule 5: AST establishes static syntax, NEVER runtime behavior, data mutations, or optimizations
    # Match whole identifier tokens (excluding matched entity names like transform_data)
    obs_tokens = set(re.findall(r'[a-zA-Z0-9_]+', obs_lower))
    excluded_names = set()
    if matched_fn:
        excluded_names.add(matched_fn["name"].lower())
    if matched_cls:
        excluded_names.add(matched_cls["name"].lower())
    obs_verb_tokens = obs_tokens - excluded_names

    ast_runtime_keywords = BEHAVIORAL_KEYWORDS | {
        "optimizes", "optimize", "optimizing", "cache", "caches", "caching",
        "throughput", "runtime", "execution", "executes", "execute", "executing",
        "validates", "validate", "validating", "decrypts", "decrypt", "decrypting",
        "encrypts", "encrypt", "encrypting", "processes", "process",
        "modifies", "modify", "modifying", "computes", "compute", "computing",
        "transforms", "transform", "transforming", "authenticates", "verifies",
        "parses", "serializes", "sends", "writes", "persists"
    }
    is_runtime_or_behavioral = (
        bool(obs_verb_tokens & ast_runtime_keywords)
        or contains_causal_explanation(obs_lower)
    )
    if is_runtime_or_behavioral:
        return "inference", True, te.id

    allowed_terms = set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower())) | valid_ast_nums
    if matched_fn:
        allowed_terms.add(matched_fn["name"].lower())
        for arg in (matched_fn.get("arguments") or matched_fn.get("args") or []):
            allowed_terms.add(str(arg).lower())
    if matched_cls:
        allowed_terms.add(matched_cls["name"].lower())
    if matched_import:
        allowed_terms.add(matched_import)

    if _has_uncorroborated_words(obs_lower, allowed_terms):
        return "inference", True, te.id

    return "verified", False, te.id


def _verify_dependency_telemetry(
    ev_input: AgentEvidenceInput,
    src_norm: str,
    te: ToolExecution,
    out_data: Dict[str, Any]
) -> Tuple[str, bool, Optional[str]]:
    """
    Verifies dependency evidence preserving source/destination nodes and edge direction.
    Rejects reversed dependencies and unsupported claims.
    """
    obs_lower = ev_input.observation.lower()

    src_lower = os.path.normpath(src_norm).lstrip("/\\").lower()
    src_base = os.path.basename(src_lower)

    # Directed edges stored as (upstream_node, downstream_node) normalized lowercase
    directed_edges: set[Tuple[str, str]] = set()
    upstream_deps: set[str] = set()
    downstream_deps: set[str] = set()
    src_imports: set[str] = set()
    packages: set[str] = set()

    for d in out_data.get("dependencies", []):
        dl = str(d).lower()
        upstream_deps.add(dl)
        directed_edges.add((dl, src_lower))
    for d in (out_data.get("reverse_dependencies") or out_data.get("dependents") or []):
        dl = str(d).lower()
        downstream_deps.add(dl)
        directed_edges.add((src_lower, dl))
    for imp in out_data.get("imports", []):
        src_imports.add(str(imp).lower())
    for pkg in out_data.get("packages", []):
        packages.add(str(pkg).lower())

    stages = out_data.get("stages", {})
    if isinstance(stages, dict):
        for st_key, stage_data in stages.items():
            if not isinstance(stage_data, dict):
                continue
            st_norm = os.path.normpath(str(st_key)).lstrip("/\\").lower()
            st_inputs = [str(inp).lower() for inp in stage_data.get("inputs", [])]
            st_outputs = [str(out).lower() for out in stage_data.get("outputs", [])]
            st_imports = [str(imp).lower() for imp in stage_data.get("imports", [])]
            for inp in st_inputs:
                directed_edges.add((inp, st_norm))
            for out in st_outputs:
                directed_edges.add((st_norm, out))
            if st_norm == src_lower:
                upstream_deps.update(st_inputs)
                downstream_deps.update(st_outputs)
                src_imports.update(st_imports)

    edges = out_data.get("edges", [])
    if isinstance(edges, list):
        for edge in edges:
            if not isinstance(edge, dict):
                continue
            efrom = os.path.normpath(str(edge.get("from", ""))).lstrip("/\\").lower()
            eto = os.path.normpath(str(edge.get("to", ""))).lstrip("/\\").lower()
            rel = str(edge.get("relationship", "")).lower().strip()
            if not efrom or not eto:
                continue
            if rel in ("depends_on", "imports", "requires", "uses"):
                # efrom depends on eto -> eto is upstream of efrom
                up_node, down_node = eto, efrom
            else:
                # Dataflow convention in trace_dependencies ("reads", "writes", ""): efrom feeds eto
                up_node, down_node = efrom, eto
            directed_edges.add((up_node, down_node))
            if down_node == src_lower:
                upstream_deps.add(up_node)
            if up_node == src_lower:
                downstream_deps.add(down_node)

    target_analysis = out_data.get("target_analysis")
    if isinstance(target_analysis, dict):
        tgt = target_analysis.get("target")
        if tgt:
            tgt_norm = os.path.normpath(str(tgt)).lstrip("/\\").lower()
            ta_up = [str(u).lower() for u in target_analysis.get("upstream", [])]
            ta_down = [str(d).lower() for d in target_analysis.get("downstream", [])]
            for u in ta_up:
                directed_edges.add((u, tgt_norm))
            for d in ta_down:
                directed_edges.add((tgt_norm, d))
            if tgt_norm == src_lower:
                upstream_deps.update(ta_up)
                downstream_deps.update(ta_down)

    if not directed_edges and not upstream_deps and not downstream_deps and not src_imports and not packages:
        return "unverified", False, None

    def _matches_node(claim_node: str, tel_node: str) -> bool:
        c = claim_node.rstrip(".,;:!?\"'")
        t = tel_node.rstrip(".,;:!?\"'")
        if c == t:
            return True
        # Only match src_base when claim_node is just filename and tel_node is src_lower
        if t == src_lower and c == src_base:
            return True
        return False

    def _has_directed_edge(up_claim: str, down_claim: str) -> bool:
        up_c = up_claim.rstrip(".,;:!?\"'")
        down_c = down_claim.rstrip(".,;:!?\"'")
        for up_t, down_t in directed_edges:
            if _matches_node(up_c, up_t) and _matches_node(down_c, down_t):
                return True
        return False

    # Parse explicit directional binary assertions
    m_depends = re.search(r'([\w\./\-]+)\s+(?:depends\s+(?:directly\s+)?on|directly\s+depends\s+on|requires|reads|consumes)\s+([\w\./\-]+)', obs_lower)
    m_is_used_by = re.search(r'([\w\./\-]+)\s+(?:is\s+used\s+by|is\s+depended\s+on\s+by|is\s+consumed\s+by|is\s+read\s+by)\s+([\w\./\-]+)', obs_lower)
    m_is_upstream_of = re.search(r'([\w\./\-]+)\s+is\s+(?:an?\s+)?upstream(?:\s+dependency)?\s+of\s+([\w\./\-]+)', obs_lower)
    m_is_downstream_of = re.search(r'([\w\./\-]+)\s+is\s+(?:a\s+)?downstream(?:\s+consumer)?\s+of\s+([\w\./\-]+)', obs_lower)

    is_direct_claim = bool(
        m_depends
        or m_is_upstream_of
        or re.search(r'\bdepends\b(?:\s+\w+)?\s+on\b', obs_lower)
        or any(kw in obs_lower for kw in ("requires", "upstream dependency", "direct dependency"))
    )
    is_reverse_claim = bool(
        m_is_used_by
        or m_is_downstream_of
        or any(kw in obs_lower for kw in ("is used by", "depended on by", "downstream consumer", "reverse dependency"))
    )
    is_import_claim = any(kw in obs_lower for kw in ("imports", "imported from", "import statement"))
    is_package_claim = any(kw in obs_lower for kw in ("declares package", "dependency package", "installed package", "package requirement"))

    matched = False
    matched_category = "edge"

    if m_depends:
        sub = m_depends.group(1).lower().strip(".,;:!?\"'")
        obj = m_depends.group(2).lower().strip(".,;:!?\"'")
        # "<sub > depends on <obj>" => obj is upstream of sub
        if _has_directed_edge(obj, sub):
            matched = True
        else:
            return "unverified", False, None

    elif m_is_upstream_of:
        sub = m_is_upstream_of.group(1).lower().strip(".,;:!?\"'")
        obj = m_is_upstream_of.group(2).lower().strip(".,;:!?\"'")
        # "<sub > is upstream of <obj>" => sub is upstream of obj
        if _has_directed_edge(sub, obj):
            matched = True
        else:
            return "unverified", False, None

    elif m_is_used_by:
        sub = m_is_used_by.group(1).lower().strip(".,;:!?\"'")
        obj = m_is_used_by.group(2).lower().strip(".,;:!?\"'")
        # "<sub > is used by <obj>" => sub is upstream of obj
        if _has_directed_edge(sub, obj):
            matched = True
        else:
            return "unverified", False, None

    elif m_is_downstream_of:
        sub = m_is_downstream_of.group(1).lower().strip(".,;:!?\"'")
        obj = m_is_downstream_of.group(2).lower().strip(".,;:!?\"'")
        # "<sub > is downstream of <obj>" => obj is upstream of sub
        if _has_directed_edge(obj, sub):
            matched = True
        else:
            return "unverified", False, None

    elif is_direct_claim:
        found_up = [d for d in upstream_deps if len(d) > 2 and re.search(r'\b' + re.escape(d) + r'\b', obs_lower)]
        found_down = [d for d in downstream_deps if len(d) > 2 and re.search(r'\b' + re.escape(d) + r'\b', obs_lower)]
        if found_down and not found_up:
            return "unverified", False, None
        elif found_up:
            matched = True
            matched_category = "upstream"
        else:
            return "unverified", False, None

    elif is_reverse_claim:
        found_down = [d for d in downstream_deps if len(d) > 2 and re.search(r'\b' + re.escape(d) + r'\b', obs_lower)]
        found_up = [d for d in upstream_deps if len(d) > 2 and re.search(r'\b' + re.escape(d) + r'\b', obs_lower)]
        if found_up and not found_down:
            return "unverified", False, None
        elif found_down:
            matched = True
            matched_category = "downstream"
        else:
            return "unverified", False, None

    elif is_import_claim:
        if any(re.search(r'\b' + re.escape(imp) + r'\b', obs_lower) for imp in src_imports if len(imp) > 2):
            matched = True
            matched_category = "imports"
        else:
            return "unverified", False, None

    elif is_package_claim:
        if any(pkg in obs_lower for pkg in packages if len(pkg) > 2):
            matched = True
            matched_category = "packages"
        else:
            return "unverified", False, None

    else:
        all_known = upstream_deps | downstream_deps | src_imports | packages
        if any(k in obs_lower for k in all_known if len(k) > 2):
            return "inference", True, te.id
        return "unverified", False, None

    if matched:
        # Validate any claimed numerical counts against the matching dependency category
        claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', obs_lower)
        valid_dep_nums: set[str] = set()
        if matched_category == "upstream":
            valid_dep_nums.add(str(len(upstream_deps)))
        elif matched_category == "downstream":
            valid_dep_nums.add(str(len(downstream_deps)))
        elif matched_category == "imports":
            valid_dep_nums.add(str(len(src_imports)))
        elif matched_category == "packages":
            for p in packages:
                valid_dep_nums.update(re.findall(r'\b\d+(?:\.\d+)?\b', p))
        else:
            valid_dep_nums.add(str(len(directed_edges)))
        if claimed_numbers and any(num not in valid_dep_nums for num in claimed_numbers):
            return "unverified", False, None

        if contains_causal_explanation(obs_lower) or contains_behavioral_claim(obs_lower):
            return "inference", True, te.id

        allowed_terms = upstream_deps | downstream_deps | src_imports | packages | {src_base} | valid_dep_nums
        allowed_terms.update(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower()))
        for up_t, down_t in directed_edges:
            allowed_terms.add(up_t)
            allowed_terms.add(down_t)
        if _has_uncorroborated_words(obs_lower, allowed_terms):
            return "inference", True, te.id
        return "verified", False, te.id

    return "unverified", False, None


def _extract_schema_dtype_claims(
    obs_lower: str,
    all_known_dtypes: set[str],
    exclude_col_tokens: set[str]
) -> Optional[Tuple[List[str], List[str], set[str]]]:
    """
    Extracts role-bound expected and actual dtype claims plus any unbound dtype mentions.
    Returns None if the observation contains explicit negation or multi-option ambiguity.
    """
    if (
        re.search(r'\b(?:no|not|never)\s+(?:a\s+)?(?:type|dtype)\s+mismatch\b', obs_lower)
        or re.search(r'\b(?:expected|actual|observed|current)(?:\s+(?:data\s+)?)?(?:dtype|type)?\s+(?:is|was|are)\s+not\b', obs_lower)
        or re.search(r'\b(?:expected_dtype|actual_dtype)\s+(?:is|was|are)\s+not\b', obs_lower)
        or re.search(r'\bnot\s+(?:expected|actual|observed)\b', obs_lower)
        or re.search(r'\b(?:either|neither)\b', obs_lower)
    ):
        return None

    non_dtype_stopwords = {
        "type", "types", "dtype", "dtypes", "data", "column", "columns", "field", "fields",
        "mismatch", "mismatches", "conflict", "drift", "error", "incompatibility",
        "is", "was", "are", "be", "to", "not", "of", "as", "in", "on", "for", "from", "with",
        "and", "or", "but", "vs", "versus", "the", "a", "an", "instead", "rather", "than",
        "actual", "actually", "actual_dtype", "expected", "expected_dtype", "observed",
        "current", "baseline", "found", "got", "received", "schema", "value", "values",
        "between", "involving"
    } | exclude_col_tokens

    dtype_tok_re = r"([a-zA-Z][a-zA-Z0-9_]*(?:\[[a-zA-Z0-9_]+\])?)(?![a-zA-Z0-9_\[])"

    # Reject ambiguous disjunctions like "float64 or int64" / "object / string"
    for m_or in re.finditer(dtype_tok_re + r"[)'\"`]?\s*(?:\bor\b|/)\s*['\"`]?" + dtype_tok_re, obs_lower):
        t1, t2 = m_or.group(1).lower(), m_or.group(2).lower()
        if (t1 in all_known_dtypes or t2 in all_known_dtypes) and t1 not in non_dtype_stopwords and t2 not in non_dtype_stopwords:
            return None

    bound_spans: List[Tuple[int, int]] = []
    claimed_expected: List[str] = []
    claimed_actual: List[Tuple[str, int, int]] = []

    def _span_overlaps(start: int, end: int) -> bool:
        return any(not (end <= bs or start >= be) for bs, be in bound_spans)

    # 1. Explicit expected role markers
    exp_pattern = re.compile(
        r"\b(?:expected(?:\s+(?:data\s+)?)?(?:dtype|type)|expected_dtype|expected"
        r"|baseline(?:\s+(?:data\s+)?)?(?:dtype|type)|baseline_dtype|should\s+be)"
        r"(?:\s*[:=]\s*|\s+(?:(?:is|was|are|of|as|to\s+be)\s+)?)[('\"`]?"
        + dtype_tok_re
    )
    for m in exp_pattern.finditer(obs_lower):
        tok = m.group(1).lower()
        s, e = m.span(1)
        if tok in non_dtype_stopwords or _span_overlaps(s, e):
            continue
        bound_spans.append((s, e))
        claimed_expected.append(tok)

    # 2. Explicit actual role markers
    act_pattern = re.compile(
        r"\b(actual(?:ly)?(?:\s+(?:data\s+)?)?(?:dtype|type)|actual_dtype|actual"
        r"|observed(?:\s+(?:data\s+)?)?(?:dtype|type)|observed_dtype"
        r"|current(?:\s+(?:data\s+)?)?(?:dtype|type)|current_dtype"
        r"|(?:got|received)(?:\s+(?:data\s+)?(?:dtype|type))?"
        r"|found(?:\s+(?:data\s+)?(?:dtype|type))?)"
        r"(?:\s*[:=]\s*|\s+(?:(?:is|was|are|of|as|to\s+be)\s+)?)[('\"`]?"
        + dtype_tok_re
    )
    for m in act_pattern.finditer(obs_lower):
        prefix = m.group(1).lower()
        tok = m.group(2).lower()
        s, e = m.span(2)
        if tok in non_dtype_stopwords or _span_overlaps(s, e):
            continue
        if prefix.startswith("found"):
            pre_ctx = obs_lower[max(0, m.start(1) - 8):m.start(1)]
            if re.search(r'\bnot\s+$', pre_ctx):
                continue
            if "dtype" not in prefix and "type" not in prefix and tok not in all_known_dtypes:
                continue
        bound_spans.append((s, e))
        claimed_actual.append((tok, s, e))

    # 3. Post-positional role labels: "float64 expected, object actual"
    post_pattern = re.compile(dtype_tok_re + r"[)'\"`]?\s+(?:is\s+)?(expected|actual)\b")
    for m in post_pattern.finditer(obs_lower):
        tok = m.group(1).lower()
        role = m.group(2).lower()
        s, e = m.span(1)
        if tok in non_dtype_stopwords or _span_overlaps(s, e):
            continue
        bound_spans.append((s, e))
        if role == "expected":
            claimed_expected.append(tok)
        else:
            claimed_actual.append((tok, s, e))

    # 4. Comparative form: "<actual> instead of / rather than <expected>"
    actual_spans = {(s, e) for _, s, e in claimed_actual}
    comp_pattern = re.compile(
        dtype_tok_re + r"[)'\"`]?\s+(?:instead\s+of|rather\s+than)\s+['\"`]?" + dtype_tok_re
    )
    for m in comp_pattern.finditer(obs_lower):
        tok_l, tok_r = m.group(1).lower(), m.group(2).lower()
        sl, el = m.span(1)
        sr, er = m.span(2)
        if tok_l in non_dtype_stopwords or tok_r in non_dtype_stopwords:
            continue
        if not _span_overlaps(sl, el) and not _span_overlaps(sr, er):
            bound_spans.extend([(sl, el), (sr, er)])
            claimed_actual.append((tok_l, sl, el))
            claimed_expected.append(tok_r)
        elif (sl, el) in actual_spans and not _span_overlaps(sr, er):
            bound_spans.append((sr, er))
            claimed_expected.append(tok_r)

    # 5. Collect any remaining dtype tokens not explicitly bound to expected or actual
    unbound_dtypes: set[str] = set()
    for m in re.finditer(r"(?<![a-zA-Z0-9_])" + dtype_tok_re, obs_lower):
        tok = m.group(1).lower()
        s, e = m.span(1)
        if _span_overlaps(s, e):
            continue
        if tok in all_known_dtypes and tok not in exclude_col_tokens:
            unbound_dtypes.add(tok)

    return claimed_expected, [tok for tok, _, _ in claimed_actual], unbound_dtypes


def _verify_schema_telemetry(
    ev_input: AgentEvidenceInput,
    src_norm: str,
    te: ToolExecution,
    out_data: Dict[str, Any]
) -> Tuple[str, bool, Optional[str]]:
    """
    Verifies schema inspection evidence binding columns to their actual
    missing, added, renamed, or type-mismatched status in the schema diff.
    Preserves exact expected_dtype vs actual_dtype role mapping.
    """
    obs_lower = ev_input.observation.lower()

    differences = out_data.get("differences") if isinstance(out_data.get("differences"), dict) else out_data
    target_schema = out_data.get("target_schema") if isinstance(out_data.get("target_schema"), dict) else {}
    actual_schema = out_data.get("actual_schema")

    missing_cols = {c.lower() for c in (differences.get("missing_columns") or out_data.get("missing_columns") or [])}
    new_cols = {c.lower() for c in (differences.get("unexpected_columns") or out_data.get("new_columns") or [])}
    drift_cols = {c.lower() for c in (out_data.get("drift_columns") or [])} | missing_cols | new_cols

    type_mismatches = differences.get("type_mismatches") or out_data.get("type_mismatches") or []
    type_mismatch_map: Dict[str, Dict[str, str]] = {}
    if isinstance(type_mismatches, list):
        for tm in type_mismatches:
            if isinstance(tm, dict) and tm.get("column"):
                exp_val = tm.get("expected_dtype")
                if exp_val is None:
                    exp_val = tm.get("expected")
                act_val = tm.get("actual_dtype")
                if act_val is None:
                    act_val = tm.get("actual")
                type_mismatch_map[str(tm["column"]).lower()] = {
                    "expected": str(exp_val).strip().lower() if exp_val else "",
                    "actual": str(act_val).strip().lower() if act_val else "",
                }
            elif isinstance(tm, str) and tm.strip():
                type_mismatch_map[tm.strip().lower()] = {"expected": "", "actual": ""}

    renamed_candidates = differences.get("renamed_candidates") or []
    renamed_pairs: List[Tuple[str, str]] = []
    for rc in renamed_candidates:
        if isinstance(rc, dict) and rc.get("original") and rc.get("current"):
            renamed_pairs.append((str(rc["original"]).lower(), str(rc["current"]).lower()))

    present_cols: Dict[str, str] = {}
    if target_schema:
        for c in target_schema.get("columns", []):
            present_cols.setdefault(str(c).lower(), "")
        if isinstance(target_schema.get("fields"), dict):
            for c, fmeta in target_schema["fields"].items():
                dtype_str = str(fmeta.get("dtype", "")).lower() if isinstance(fmeta, dict) and fmeta.get("dtype") else ""
                present_cols[str(c).lower()] = dtype_str
    if actual_schema:
        if isinstance(actual_schema, dict):
            for c, dtype_val in actual_schema.items():
                present_cols[str(c).lower()] = str(dtype_val).lower() if dtype_val else ""
        elif isinstance(actual_schema, list):
            for c in actual_schema:
                present_cols.setdefault(str(c).lower(), "")

    if not (missing_cols or new_cols or drift_cols or type_mismatch_map or renamed_pairs or present_cols):
        return "unverified", False, None

    all_cols = missing_cols | new_cols | drift_cols | set(type_mismatch_map.keys()) | set(present_cols.keys())
    all_cols_sorted = sorted(all_cols)
    words_in_obs = set(re.findall(r'[a-zA-Z0-9_]+', obs_lower))
    mentioned_cols = [c for c in all_cols_sorted if re.search(r'\b' + re.escape(c) + r'\b', obs_lower)]

    explicit_col = ev_input.column_name.lower() if ev_input.column_name else None
    if explicit_col and mentioned_cols and explicit_col not in mentioned_cols:
        return "unverified", False, None

    claimed_col = mentioned_cols[0] if mentioned_cols else explicit_col
    if not claimed_col:
        return "unverified", False, None

    base_known_dtypes = {
        "int64", "int32", "int16", "int8", "uint64", "uint32", "uint16", "uint8", "int", "integer", "bigint", "smallint",
        "float64", "float32", "float16", "float", "double", "decimal", "numeric", "number",
        "object", "string", "str", "varchar", "char", "text",
        "bool", "boolean",
        "datetime", "datetime64", "datetime64[ns]", "timestamp", "date", "time", "timedelta", "timedelta64",
        "category", "categorical", "json", "bytes", "binary",
    }

    is_missing_claim = any(kw in obs_lower for kw in ("missing", "dropped", "removed", "not found", "lacking", "omitted"))
    is_rename_claim = any(kw in obs_lower for kw in ("renamed", "rename"))
    is_new_claim = any(kw in obs_lower for kw in ("added", "new column", "unexpected column", "extra column"))
    is_explicit_mismatch_phrase = any(kw in obs_lower for kw in (
        "type mismatch", "dtype mismatch", "mismatched type", "mismatched dtype",
        "wrong type", "wrong dtype", "incompatible type", "incompatible dtype",
        "type incompatibility", "type conflict", "type drift", "cast error",
        "expected_dtype", "actual_dtype", "expected type", "expected dtype",
        "actual type", "actual dtype", "observed type", "observed dtype",
        "current type", "current dtype"
    ))
    has_role_dtype_pair = bool(re.search(r'\bexpected\b.+\bactual\b|\bactual\b.+\bexpected\b', obs_lower))
    is_type_claim = bool(
        is_explicit_mismatch_phrase
        or has_role_dtype_pair
        or (
            not is_missing_claim and not is_new_claim and not is_rename_claim
            and (
                re.search(r'\b(?:expected|actual|observed)(?:_?(?:dtype|type))?\b', obs_lower)
                or (
                    (
                        re.search(r'\b(?:dtype|data\s*type)\b', obs_lower)
                        or bool(words_in_obs & (base_known_dtypes - all_cols))
                    )
                    and claimed_col in type_mismatch_map
                )
            )
        )
    )

    allowed_terms = {claimed_col} | set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower()))
    matched = False
    valid_schema_nums: set[str] = set()

    if is_rename_claim and renamed_pairs:
        for orig_c, curr_c in renamed_pairs:
            if orig_c in words_in_obs and curr_c in words_in_obs:
                allowed_terms.update({orig_c, curr_c, "renamed", "rename", "candidate"})
                matched = True
                break
        if not matched:
            return "unverified", False, None

    elif is_missing_claim and not is_explicit_mismatch_phrase:
        if claimed_col in present_cols and claimed_col not in missing_cols:
            return "unverified", False, None
        if claimed_col in missing_cols or claimed_col in drift_cols:
            matched = True
            valid_schema_nums.add(str(len(missing_cols)))
        else:
            return "unverified", False, None

    elif is_type_claim:
        target_type_cols = mentioned_cols if mentioned_cols else [claimed_col]
        all_known_dtypes = set(base_known_dtypes)
        for col_c in target_type_cols:
            tm_info = type_mismatch_map.get(col_c)
            if not tm_info:
                return "unverified", False, None
            if tm_info.get("expected"):
                all_known_dtypes.add(tm_info["expected"])
            if tm_info.get("actual"):
                all_known_dtypes.add(tm_info["actual"])

        extracted = _extract_schema_dtype_claims(obs_lower, all_known_dtypes, all_cols)
        if extracted is None:
            return "unverified", False, None
        claimed_expected, claimed_actual, unbound_dtypes = extracted

        # Any dtype mentioned without a clear expected/actual role is ambiguous -> fail closed
        if unbound_dtypes:
            return "unverified", False, None

        for col_c in target_type_cols:
            tm_info = type_mismatch_map[col_c]
            expected_t = tm_info.get("expected", "")
            actual_t = tm_info.get("actual", "")
            # Fail closed if type mismatch telemetry is incomplete or non-distinct
            if not expected_t or not actual_t or expected_t == actual_t:
                return "unverified", False, None
            if any(ce != expected_t for ce in claimed_expected):
                return "unverified", False, None
            if any(ca != actual_t for ca in claimed_actual):
                return "unverified", False, None

            allowed_terms.update({col_c, expected_t, actual_t})

        allowed_terms.update({"expected_dtype", "actual_dtype", "got", "received", "baseline", "current", "instead", "rather", "than"})
        matched = True
        valid_schema_nums.add(str(len(type_mismatch_map)))

    elif is_new_claim:
        if claimed_col in new_cols or claimed_col in drift_cols:
            matched = True
            valid_schema_nums.add(str(len(new_cols)))
        else:
            return "unverified", False, None

    else:
        if claimed_col in (missing_cols | new_cols | drift_cols):
            matched = True
        elif claimed_col in type_mismatch_map:
            tm_info = type_mismatch_map[claimed_col]
            if not tm_info.get("expected") or not tm_info.get("actual"):
                return "unverified", False, None
            matched = True
        elif claimed_col in present_cols and present_cols[claimed_col] and present_cols[claimed_col] in obs_lower:
            col_dtype = present_cols[claimed_col]
            extra_dtypes = (words_in_obs & (base_known_dtypes - all_cols)) - {col_dtype}
            if extra_dtypes:
                return "unverified", False, None
            allowed_terms.add(col_dtype)
            matched = True
        else:
            return "unverified", False, None

    if matched:
        claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', obs_lower)
        if claimed_numbers and any(num not in valid_schema_nums for num in claimed_numbers):
            return "unverified", False, None
        if contains_causal_explanation(obs_lower) or contains_behavioral_claim(obs_lower):
            return "inference", True, te.id
        allowed_terms.update(valid_schema_nums)
        if _has_uncorroborated_words(obs_lower, allowed_terms):
            return "inference", True, te.id
        return "verified", False, te.id

    return "unverified", False, None


def resolve_frame_to_project_relative(
    frame_file: str,
    project_paths: set[str],
    workspace_dir: Optional[Path] = None
) -> Optional[str]:
    """
    Strictly and safely resolves a traceback frame file path (project-relative, recognized container root,
    or recognized workspace root) to an authorized project-relative path.
    Enforces:
    - Rejection of traversal attempts ('..')
    - Rejection of arbitrary absolute paths not mapped to a known recognized root (no suffix matching!)
    - Rejection of symlinks pointing outside the project workspace
    - Verification that the target exists and is a regular file when workspace_dir is available
    """
    if not frame_file or not isinstance(frame_file, str):
        return None

    raw_path_str = frame_file.strip()
    if not raw_path_str:
        return None

    # Reject traversal sequences immediately
    if ".." in Path(raw_path_str).parts:
        return None

    is_absolute = os.path.isabs(raw_path_str)
    candidate_rel: Optional[str] = None

    if not is_absolute:
        candidate_rel = os.path.normpath(raw_path_str).lstrip("/\\")
    else:
        # Check recognized container roots (e.g. /app/src/pipeline.py -> src/pipeline.py)
        for c_root in RECOGNIZED_CONTAINER_ROOTS:
            if raw_path_str == c_root or raw_path_str.startswith(c_root + "/"):
                candidate_rel = os.path.relpath(raw_path_str, c_root).lstrip("/\\")
                break

        # Check recognized host roots
        if candidate_rel is None and workspace_dir:
            try:
                ws_resolved = Path(workspace_dir).resolve()
                frame_resolved = Path(raw_path_str).resolve()
                if frame_resolved.is_relative_to(ws_resolved):
                    candidate_rel = str(frame_resolved.relative_to(ws_resolved))
            except Exception:
                pass

        if candidate_rel is None and workspace_dir:
            for host_root in [settings.WORKSPACES_DIR, settings.UPLOADS_DIR]:
                try:
                    hr_resolved = Path(host_root).resolve()
                    frame_path = Path(raw_path_str).resolve()
                    if frame_path.is_relative_to(hr_resolved):
                        rel_to_hr = frame_path.relative_to(hr_resolved)
                        if len(rel_to_hr.parts) > 1:
                            sub = str(Path(*rel_to_hr.parts[1:]))
                            if sub in project_paths:
                                candidate_rel = sub
                                break
                except Exception:
                    pass

    # If candidate_rel is still None, arbitrary absolute path is REJECTED (no suffix matching!)
    if not candidate_rel:
        return None

    # Traversal check on resulting candidate
    if ".." in Path(candidate_rel).parts:
        return None

    candidate_norm = os.path.normpath(candidate_rel).lstrip("/\\")
    if candidate_norm not in project_paths:
        return None

    # If workspace_dir is provided, verify on-disk resolution and symlink containment
    if workspace_dir:
        disk_path = Path(workspace_dir) / candidate_norm
        try:
            ws_resolved = Path(workspace_dir).resolve()
            if not disk_path.exists():
                return None
            resolved_disk_path = disk_path.resolve()
            # Symlinks must not escape the workspace
            if not resolved_disk_path.is_relative_to(ws_resolved):
                return None
            if not resolved_disk_path.is_file():
                return None
        except Exception:
            return None

    return candidate_norm


def verify_evidence_against_telemetry(
    ev_input: AgentEvidenceInput,
    project_files: List[Any],
    recent_tool_execs: List[ToolExecution],
    investigation_id: Optional[str] = None,
    workspace_dir: Optional[Path] = None
) -> Tuple[str, bool, Optional[str]]:
    """
    Rigorously verifies model-submitted evidence against ground-truth project artifacts
    and empirical tool execution telemetry.
    Status rules:
    - "verified": exact claim is supported by authoritative, structured evidence.
    - "inference": evidence supports a plausible interpretation or contains causal explanations / behavioral claims,
                  but does not establish exact empirical claim.
    - "unverified": available evidence does not establish the claim or violates path containment.
    When uncertain, downgrades to "inference" or "unverified".
    """
    # 1. Explicit inference or speculative hypothesis
    if ev_input.is_inference or "inference" in ev_input.evidence_type.lower() or "hypothesis" in ev_input.evidence_type.lower():
        return "inference", True, None

    # 2. Verify source file existence in project artifacts using exact normalized relative paths (no basename collisions)
    src = ev_input.source_file.strip() if ev_input.source_file else ""
    if not src:
        return "unverified", False, None

    # Traversal sequence in source_file is immediately unverified
    if ".." in Path(src).parts:
        return "unverified", False, None

    # Reject absolute source path if workspace_dir is missing
    if os.path.isabs(src) and workspace_dir is None:
        return "unverified", False, None

    src_norm = os.path.normpath(src).lstrip("/\\")
    project_paths = {os.path.normpath(f.relative_path).lstrip("/\\") for f in project_files if hasattr(f, "relative_path")}
    if src_norm not in project_paths:
        return "unverified", False, None

    # Verify symlink and disk existence if workspace_dir is provided
    if workspace_dir:
        disk_path = Path(workspace_dir) / src_norm
        try:
            ws_res = Path(workspace_dir).resolve()
            if not disk_path.exists() or not disk_path.resolve().is_relative_to(ws_res) or not disk_path.resolve().is_file():
                return "unverified", False, None
        except Exception:
            return "unverified", False, None

    # 3. Match against tool execution telemetry
    matched_exec: Optional[ToolExecution] = None
    if ev_input.tool_execution_id:
        for te in recent_tool_execs:
            if te.id == ev_input.tool_execution_id:
                if investigation_id and te.investigation_id != investigation_id:
                    return "unverified", False, None
                matched_exec = te
                break
        if not matched_exec:
            return "unverified", False, None

    if matched_exec:
        candidate_execs = [matched_exec]
    else:
        candidate_execs = [
            te for te in recent_tool_execs
            if not investigation_id or te.investigation_id == investigation_id
        ]

    obs_lower = ev_input.observation.lower()
    best_inference: Optional[Tuple[str, bool, Optional[str]]] = None

    for te in candidate_execs:
        if not te or not te.output_result or not isinstance(te.output_result, dict):
            continue

        out_data = te.output_result
        tool_name = (getattr(te, "tool_name", "") or "").strip()

        res: Tuple[str, bool, Optional[str]] = ("unverified", False, None)

        if tool_name == "analyze_logs" or (not tool_name and ("tracebacks" in out_data or "fatal_error" in out_data)):
            res = _verify_log_telemetry(ev_input, src_norm, te, out_data, project_paths, workspace_dir=workspace_dir)

        elif tool_name == "profile_dataset" or (
            not tool_name and ("column_profiles" in out_data or "columns" in out_data or "anomalies" in out_data)
        ):
            res = _verify_dataset_telemetry(ev_input, src_norm, te, out_data)

        elif tool_name == "inspect_code" or (
            not tool_name and ("functions" in out_data or "data_loadings" in out_data or "column_references" in out_data)
        ):
            res = _verify_code_ast_telemetry(
                ev_input, src_norm, te, out_data, project_paths=project_paths, workspace_dir=workspace_dir
            )

        elif tool_name == "trace_dependencies" or (
            not tool_name and ("stages" in out_data or "edges" in out_data or "dependencies" in out_data)
        ):
            res = _verify_dependency_telemetry(ev_input, src_norm, te, out_data)

        elif tool_name == "inspect_schema" or (
            not tool_name and (
                "differences" in out_data or "target_schema" in out_data
                or "drift_columns" in out_data or "actual_schema" in out_data
            )
        ):
            res = _verify_schema_telemetry(ev_input, src_norm, te, out_data)

        elif tool_name == "test_hypothesis":
            result_data = out_data.get("result", {}) if isinstance(out_data.get("result"), dict) else out_data
            supported = result_data.get("supported") if "supported" in result_data else out_data.get("supported")
            conclusion = str(result_data.get("conclusion") or out_data.get("conclusion") or "").lower()
            hyp_id = str(result_data.get("hypothesis_id") or out_data.get("hypothesis_id") or "").lower()
            allowed_terms = set(re.findall(r'[a-zA-Z0-9_]{2,}', src_norm.lower()))
            if hyp_id:
                allowed_terms.add(hyp_id)

            if (supported is True or conclusion == "confirmed") and any(kw in obs_lower for kw in ("support", "confirm", "pass", "valid")):
                allowed_terms.update({"confirm", "confirmed", "support", "supported", "supports", "pass", "valid"})
                if contains_causal_explanation(obs_lower) or contains_behavioral_claim(obs_lower) or _has_uncorroborated_words(obs_lower, allowed_terms):
                    res = ("inference", True, te.id)
                else:
                    res = ("verified", False, te.id)
            elif (supported is False or conclusion == "rejected") and any(kw in obs_lower for kw in ("contradict", "reject", "fail", "refut")):
                allowed_terms.update({"contradict", "contradicted", "reject", "rejected", "refute", "refuted"})
                if contains_causal_explanation(obs_lower) or contains_behavioral_claim(obs_lower) or _has_uncorroborated_words(obs_lower, allowed_terms):
                    res = ("inference", True, te.id)
                else:
                    res = ("verified", False, te.id)

        if res[0] == "verified":
            return res
        if res[0] == "inference" and best_inference is None:
            best_inference = res

    if best_inference is not None:
        return best_inference

    return "unverified", False, None


class InvestigationEngine:
    def __init__(
        self,
        db: AsyncSession,
        investigation_id: str,
        ai_provider: Optional[AIProvider] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        self.db = db
        self.investigation_id = investigation_id
        self.provider = ai_provider or AIProvider()
        self.event_callback = event_callback
        self.max_tool_calls = settings.MAX_TOOL_CALLS
        self.max_repeated_tool = settings.MAX_REPEATED_TOOL_CALLS
        self.tool_call_counts: Dict[str, int] = {}
        self.last_tool_signature: Optional[str] = None

    async def emit_event(self, event_type: str, title: str, payload: Dict[str, Any]) -> InvestigationEvent:
        """Persists and broadcasts an investigation event."""
        event = InvestigationEvent(
            investigation_id=self.investigation_id,
            event_type=event_type,
            title=title,
            payload=payload
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(event)

        if self.event_callback:
            try:
                self.event_callback({
                    "id": event.id,
                    "investigation_id": self.investigation_id,
                    "event_type": event_type,
                    "title": title,
                    "payload": payload,
                    "created_at": event.created_at.isoformat()
                })
            except Exception:
                pass

        return event

    async def run(self) -> None:
        """
        Executes the bounded autonomous investigative reasoning loop.
        """
        # Load investigation and project details
        inv_result = await self.db.execute(
            select(Investigation).where(Investigation.id == self.investigation_id)
        )
        investigation = inv_result.scalar_one_or_none()
        if not investigation:
            return

        proj_result = await self.db.execute(
            select(Project).where(Project.id == investigation.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if not project:
            await self.emit_event("error", "Project Not Found", {"error": "Project record missing."})
            return

        files_result = await self.db.execute(
            select(ProjectFile).where(ProjectFile.project_id == project.id)
        )
        project_files = files_result.scalars().all()
        project_storage_dir = str(settings.UPLOADS_DIR / project.id)

        investigation.status = "investigating"
        investigation.stage = "context_collection"
        await self.db.commit()

        await self.emit_event(
            "stage_change",
            "Investigation Started",
            {"stage": "context_collection", "objective": investigation.objective}
        )

        # Prepare agent tool definitions summary (excluding human-approval-only tools)
        tool_defs = tool_registry.list_agent_tool_definitions()
        tool_defs_str = "\n".join([
            f"- `{t.name}`: {t.description} (params: {[p.name for p in t.parameters]})"
            for t in tool_defs
        ])

        project_files_summary = ", ".join([f.relative_path for f in project_files]) or "No files uploaded"

        consecutive_duplicate_count = 0
        loop_counter = 0

        while loop_counter < self.max_tool_calls:
            loop_counter += 1
            investigation.remaining_budget = self.max_tool_calls - loop_counter
            await self.db.commit()

            # Retrieve active hypotheses and evidence records from DB
            hyp_result = await self.db.execute(
                select(Hypothesis).where(Hypothesis.investigation_id == self.investigation_id)
            )
            active_hypotheses = hyp_result.scalars().all()
            hyp_summary = [
                f"[{h.id}] {h.title} (Status: {h.status}, Confidence: {h.confidence})"
                for h in active_hypotheses
            ]

            evd_result = await self.db.execute(
                select(EvidenceRecord).where(EvidenceRecord.investigation_id == self.investigation_id)
            )
            collected_evidence = evd_result.scalars().all()
            evd_summary = [
                f"[{e.id}] ({e.evidence_type}) {e.observation} in {e.source_file}"
                for e in collected_evidence
            ]

            # Retrieve recent tool execution results to feed empirical feedback to the model
            tool_exec_result = await self.db.execute(
                select(ToolExecution)
                .where(ToolExecution.investigation_id == self.investigation_id)
                .order_by(ToolExecution.created_at.desc())
                .limit(6)
            )
            recent_tool_execs = list(tool_exec_result.scalars().all())
            recent_tool_execs.reverse()
            recent_tools_summary = []
            for te in recent_tool_execs:
                output_str = json.dumps(te.output_result) if te.output_result else ""
                if len(output_str) > 1200:
                    output_str = output_str[:1200] + "... [truncated]"
                recent_tools_summary.append({
                    "tool": te.tool_name,
                    "arguments": te.input_arguments,
                    "status": te.status,
                    "error": te.error_message,
                    "result_summary": output_str
                })

            # Construct agent prompt
            prompt_content = f"""
Current Investigation Context:
- Project ID: {project.id}
- Project Name: {project.name}
- Pipeline Objective: {investigation.objective}
- Project Files: {project_files_summary}
- Remaining Budget: {investigation.remaining_budget} steps
- Current Stage: {investigation.stage}
- Hypotheses Formulated: {json.dumps(hyp_summary)}
- Evidence Records Logged: {json.dumps(evd_summary)}
- Recent Diagnostic Tool Outputs: {json.dumps(recent_tools_summary, indent=2)}

Inspect the available data, empirical tool findings, and decide whether to call a diagnostic tool,
formulate/test hypotheses, propose a patch, or conclude the investigation. Output pure JSON.
"""

            system_instruction = INVESTIGATION_SYSTEM_PROMPT.format(
                tool_definitions=tool_defs_str,
                investigation_id=self.investigation_id,
                objective=investigation.objective,
                project_files=project_files_summary,
                current_stage=investigation.stage,
                remaining_budget=investigation.remaining_budget,
                active_hypotheses=json.dumps(hyp_summary),
                collected_evidence=json.dumps(evd_summary),
                recent_tool_history=json.dumps(recent_tools_summary, indent=2)
            )

            # Call AI model provider
            await self.emit_event(
                "thought",
                "Analyzing Evidence and Planning Action",
                {"step": loop_counter, "stage": investigation.stage}
            )

            try:
                decision = await self.provider.generate_decision(
                    prompt=prompt_content,
                    system_instruction=system_instruction
                )
            except ModelProviderError as mpe:
                investigation.status = "failed"
                await self.db.commit()
                await self.emit_event(
                    "error",
                    "AI Model Unavailable",
                    {"error": str(mpe), "provider": self.provider.provider_type}
                )
                return

            # Update stage and confidence from decision
            investigation.stage = decision.current_stage
            investigation.confidence_score = decision.confidence_score
            investigation.confidence_justification = decision.confidence_justification
            await self.db.commit()

            # Record user-facing investigation event
            inv_sum = decision.investigation_summary
            await self.emit_event(
                "thought",
                f"Investigation Focus: {inv_sum.investigation_focus}",
                {
                    "focus": inv_sum.investigation_focus,
                    "tool_reason": inv_sum.tool_selection_reason,
                    "finding_summary": inv_sum.finding_summary,
                    "hypothesis_impact": inv_sum.hypothesis_impact,
                    "next_steps": inv_sum.next_steps,
                    "conclusion_rationale": inv_sum.conclusion_rationale,
                    "confidence_score": decision.confidence_score,
                    "stage": decision.current_stage
                }
            )

            # Process new or updated hypotheses
            for h_input in decision.hypotheses:
                existing_hyp = None
                if h_input.id:
                    for h in active_hypotheses:
                        if h.id == h_input.id:
                            existing_hyp = h
                            break

                if existing_hyp:
                    existing_hyp.title = h_input.title
                    existing_hyp.description = h_input.description
                    existing_hyp.status = h_input.status
                    existing_hyp.confidence = h_input.confidence
                    existing_hyp.reasoning = h_input.reasoning
                    await self.emit_event(
                        "hypothesis_update",
                        f"Updated Hypothesis: {existing_hyp.title}",
                        {"hypothesis_id": existing_hyp.id, "status": existing_hyp.status, "confidence": existing_hyp.confidence}
                    )
                else:
                    new_hyp = Hypothesis(
                        investigation_id=self.investigation_id,
                        title=h_input.title,
                        description=h_input.description,
                        status=h_input.status,
                        confidence=h_input.confidence,
                        reasoning=h_input.reasoning
                    )
                    self.db.add(new_hyp)
                    await self.db.commit()
                    await self.db.refresh(new_hyp)
                    await self.emit_event(
                        "hypothesis_update",
                        f"Formulated Hypothesis: {new_hyp.title}",
                        {"hypothesis_id": new_hyp.id, "status": new_hyp.status, "confidence": new_hyp.confidence}
                    )

            # Process new evidence records with independent verification
            for ev_input in decision.new_evidence:
                v_status, is_inf, matched_tid = verify_evidence_against_telemetry(
                    ev_input=ev_input,
                    project_files=project_files,
                    recent_tool_execs=recent_tool_execs,
                    investigation_id=self.investigation_id,
                    workspace_dir=Path(project_storage_dir) if os.path.exists(project_storage_dir) else None
                )
                ev_rec = EvidenceRecord(
                    investigation_id=self.investigation_id,
                    source_file=ev_input.source_file,
                    evidence_type=ev_input.evidence_type,
                    line_number=ev_input.line_number,
                    column_name=ev_input.column_name,
                    execution_stage=ev_input.execution_stage,
                    observation=ev_input.observation,
                    associated_hypothesis_id=ev_input.associated_hypothesis_id,
                    tool_execution_id=matched_tid,
                    is_inference=is_inf,
                    verification_status=v_status
                )
                self.db.add(ev_rec)
                await self.db.commit()
                await self.db.refresh(ev_rec)
                await self.emit_event(
                    "evidence_found",
                    f"Evidence Logged: {ev_rec.evidence_type} ({v_status.upper()})",
                    {
                        "evidence_id": ev_rec.id,
                        "source": ev_rec.source_file,
                        "observation": ev_rec.observation,
                        "line": ev_rec.line_number,
                        "column": ev_rec.column_name,
                        "verification_status": v_status,
                        "is_inference": is_inf,
                        "tool_execution_id": matched_tid
                    }
                )

            # Handle Action Types
            action = decision.action_type

            if action == "call_tool" and decision.tool_call:
                tool_req = decision.tool_call
                tool_name = tool_req.tool_name
                args = tool_req.arguments

                # Loop prevention & duplicate detection
                current_sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                if current_sig == self.last_tool_signature:
                    consecutive_duplicate_count += 1
                    if consecutive_duplicate_count >= self.max_repeated_tool:
                        await self.emit_event(
                            "thought",
                            "Loop Prevention Guard Triggered",
                            {
                                "message": f"Repeated tool call '{tool_name}' detected. Forcing hypothesis synthesis or report.",
                                "repeated_tool": tool_name
                            }
                        )
                        # Switch to synthesizing report
                        action = "conclude_inconclusive"
                else:
                    consecutive_duplicate_count = 0
                    self.last_tool_signature = current_sig

                self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1

                if action == "call_tool":
                    await self.emit_event(
                        "tool_call",
                        f"Invoking Diagnostic: {tool_name}",
                        {"tool_name": tool_name, "arguments": args}
                    )

                    tool_res = await tool_registry.execute_tool(
                        tool_name=tool_name,
                        project_path=project_storage_dir,
                        arguments=args,
                        investigation_id=self.investigation_id,
                        caller_role="agent",
                        remaining_budget=investigation.remaining_budget
                    )

                    # Persist ToolExecution record with traceable execution ID
                    exec_id = f"EXEC-{uuid.uuid4().hex[:8].upper()}"
                    t_exec = ToolExecution(
                        id=exec_id,
                        investigation_id=self.investigation_id,
                        tool_name=tool_name,
                        input_arguments=args,
                        output_result=tool_res.data,
                        error_message=tool_res.error,
                        execution_time_ms=tool_res.execution_time_ms,
                        status="success" if tool_res.success else "error"
                    )
                    self.db.add(t_exec)
                    await self.db.commit()

                    await self.emit_event(
                        "tool_result",
                        f"Diagnostic Result: {tool_name}",
                        {
                            "tool_name": tool_name,
                            "success": tool_res.success,
                            "summary": tool_res.summary,
                            "duration_ms": tool_res.execution_time_ms
                        }
                    )
                    if tool_name == "propose_patch" and tool_res.success and isinstance(tool_res.data, dict):
                        await self._persist_proposed_patch(investigation, tool_res.data)
                        return
                    continue

            if action == "propose_patch" and decision.patch_proposal:
                p_in = decision.patch_proposal
                # Call propose_patch tool for unified diff and syntax verification
                patch_tool_res = await tool_registry.execute_tool(
                    tool_name="propose_patch",
                    project_path=project_storage_dir,
                    arguments={
                        "target_file": p_in.target_file,
                        "original_snippet": p_in.original_code_context,
                        "replacement_code": p_in.proposed_code,
                        "explanation": p_in.explanation,
                        "supporting_evidence_ids": p_in.supporting_evidence_ids,
                        "expected_impact": p_in.expected_impact,
                        "potential_risks": p_in.potential_risks
                    },
                    investigation_id=self.investigation_id
                )

                if patch_tool_res.success:
                    await self._persist_proposed_patch(investigation, patch_tool_res.data)
                    # Await human review and approval
                    return
                else:
                    await self.emit_event(
                        "error",
                        "Patch Generation Failed Syntax Check",
                        {"error": patch_tool_res.error, "summary": patch_tool_res.summary}
                    )
                    continue

            if action in ("conclude_success", "conclude_inconclusive") or loop_counter >= self.max_tool_calls:
                final_rep = decision.final_report
                patch_check_res = await self.db.execute(
                    select(PatchProposal).where(PatchProposal.investigation_id == self.investigation_id)
                )
                existing_patches = patch_check_res.scalars().all()
                has_unvalidated_patch = any(p.status != "validated" for p in existing_patches)
                is_success = (
                    action == "conclude_success"
                    and investigation.confidence_score >= 0.7
                    and not has_unvalidated_patch
                )

                investigation.status = "completed" if is_success else "inconclusive"
                investigation.stage = "report_generation"
                investigation.root_cause_summary = final_rep.root_cause if final_rep else "Insufficient evidence to isolate a singular root cause."
                await self.db.commit()

                # Generate comprehensive final report
                await self._build_final_report(final_rep, is_success)
                return

        # Fallback if loop finishes without explicit conclusion
        investigation.status = "inconclusive"
        investigation.stage = "report_generation"
        await self.db.commit()
        await self._build_final_report(None, False)

    async def _persist_proposed_patch(
        self,
        investigation: Investigation,
        p_data: Dict[str, Any],
    ) -> None:
        """Persists a verified PatchProposal and pauses investigation for human approval."""
        proposal = PatchProposal(
            investigation_id=self.investigation_id,
            target_file=p_data["target_file"],
            original_code_context=p_data["original_code_context"],
            proposed_code=p_data["proposed_code"],
            unified_diff=p_data["unified_diff"],
            explanation=p_data["explanation"],
            supporting_evidence_ids=p_data.get("supporting_evidence_ids", []),
            expected_impact=p_data["expected_impact"],
            potential_risks=p_data["potential_risks"],
            status="proposed",
        )
        self.db.add(proposal)
        investigation.status = "awaiting_patch_approval"
        investigation.stage = "awaiting_approval"
        await self.db.commit()
        await self.db.refresh(proposal)

        await self.emit_event(
            "patch_generated",
            f"Proposed Code Correction: {proposal.target_file}",
            {
                "patch_id": proposal.id,
                "target_file": proposal.target_file,
                "explanation": proposal.explanation,
                "unified_diff": proposal.unified_diff,
            },
        )

    async def _build_final_report(self, rep_input: Optional[Any], is_success: bool) -> None:
        """Constructs and stores the final markdown investigation report."""
        report = await generate_or_update_investigation_report(
            db=self.db,
            investigation_id=self.investigation_id,
            rep_input=rep_input,
            is_success_hint=is_success,
        )
        if report is None:
            return

        effective_success = (
            bool((report.validation_summary or {}).get("validated", False))
            if (report.proposed_correction or {})
            else is_success
        )

        await self.emit_event(
            "report_ready",
            f"Final Report Generated: {report.title}",
            {"report_id": report.id, "root_cause": report.root_cause, "is_success": effective_success}
        )

