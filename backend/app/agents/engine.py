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
from backend.app.models.report import InvestigationReport
from backend.app.models.project import Project, ProjectFile
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

        if candidate_rel is None:
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
    - "inference": evidence supports a plausible interpretation but does not establish exact claim.
    - "unverified": available evidence does not establish the claim.
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

    for te in candidate_execs:
        if not te or not te.output_result:
            continue

        out_data = te.output_result
        tool_name = getattr(te, "tool_name", "")

        # 4. Corroborate strictly by structured tool outputs

        # =====================================================================
        # 1.2 EXCEPTION EVIDENCE & TRACEBACKS (analyze_logs)
        # =====================================================================
        if tool_name == "analyze_logs" or "tracebacks" in out_data or "fatal_error" in out_data:
            frames: List[Dict[str, Any]] = []
            if "tracebacks" in out_data and isinstance(out_data["tracebacks"], list):
                for tb in out_data["tracebacks"]:
                    frames.extend(tb.get("frames", []))
            
            exc_types: List[str] = []
            exc_messages: List[str] = []
            if "fatal_error" in out_data and out_data["fatal_error"]:
                lf = out_data["fatal_error"].get("last_frame")
                if lf:
                    frames.append(lf)
                if out_data["fatal_error"].get("exception_type"):
                    exc_types.append(out_data["fatal_error"]["exception_type"])
                if out_data["fatal_error"].get("message"):
                    exc_messages.append(out_data["fatal_error"]["message"])
            if "tracebacks" in out_data and isinstance(out_data["tracebacks"], list):
                for tb in out_data["tracebacks"]:
                    if tb.get("exception_type"):
                        exc_types.append(tb["exception_type"])
                    if tb.get("message"):
                        exc_messages.append(tb["message"])

            # Verify line number strictly on matching file using strict path resolution
            line_matched = False
            for fr in frames:
                resolved_fr = resolve_frame_to_project_relative(fr.get("file", ""), project_paths, workspace_dir=workspace_dir)
                if resolved_fr and resolved_fr == src_norm:
                    if ev_input.line_number is None or fr.get("line") == ev_input.line_number:
                        line_matched = True
                        break

            if not line_matched:
                continue

            # Exception verification: generic words alone NEVER independently verify
            generic_words = {"error", "missing", "failed", "invalid", "exception", "bug", "crash", "issue", "problem", "terminat"}
            words_in_obs = set(re.findall(r'[a-zA-Z0-9_]{3,}', obs_lower))
            non_generic_words = words_in_obs - generic_words

            # 1. Check exception type corroboration
            matched_exc_type = any(et.lower() in obs_lower for et in exc_types)
            
            # Check if observation asserts a conflicting exception type (e.g. claims ValueError when error is KeyError)
            known_py_exceptions = {"keyerror", "valueerror", "typeerror", "filenotfounderror", "zerodivisionerror", "indexerror", "attributeerror", "importerror"}
            mentioned_known_exceptions = words_in_obs & known_py_exceptions
            if mentioned_known_exceptions:
                actual_exc_types_lower = {et.lower() for et in exc_types}
                if not (mentioned_known_exceptions & actual_exc_types_lower):
                    # Conflicting exception type asserted!
                    return "unverified", False, None

            # 2. Check specific message corroboration (distinct tokens, column names, values)
            matched_exc_msg = False
            for msg in exc_messages:
                msg_tokens = [
                    w.lower() for w in re.findall(r'[a-zA-Z0-9_]{3,}', msg)
                    if w.lower() not in generic_words
                ]
                if any(tok in obs_lower for tok in msg_tokens):
                    matched_exc_msg = True
                    break

            # Both exception type OR specific error token must be present, plus non-generic content
            if (matched_exc_type or matched_exc_msg) and non_generic_words:
                return "verified", False, te.id
            else:
                # Uncorroborated by exception type or error tokens
                return "unverified", False, None

        # =====================================================================
        # 1.3 DATASET PROFILING EVIDENCE (profile_dataset)
        # =====================================================================
        elif tool_name == "profile_dataset" or "anomalies" in out_data:
            anomalies = out_data.get("anomalies", [])
            columns_data = out_data.get("columns", {}) if isinstance(out_data.get("columns"), dict) else {}
            dataset_stats = out_data.get("dataset", {}) if isinstance(out_data.get("dataset"), dict) else {}
            col_name = ev_input.column_name or ""
            col_lower = col_name.lower()

            col_profile = columns_data.get(col_name, {}) if col_name else {}
            matching_anomalies = [
                a for a in anomalies
                if isinstance(a, dict) and a.get("column", "").lower() == col_lower
            ] if col_lower else []

            # Identify specific claim type
            is_null_claim = any(kw in obs_lower for kw in ("null", "nan", "missing value", "empty value"))
            is_dup_claim = any(kw in obs_lower for kw in ("duplicate", "repeated row", "repeated record"))
            is_row_count_claim = any(kw in obs_lower for kw in ("row count", "total rows", "records loaded", "rows in dataset"))
            is_type_claim = any(kw in obs_lower for kw in ("data type", "datatype", "type is", "dtype", "inferred type"))

            claimed_numbers = re.findall(r'\b\d+(?:\.\d+)?%?\b', obs_lower)

            if is_null_claim and col_lower:
                actual_null_count = col_profile.get("null_count", 0)
                anomaly_null_count = sum(a.get("null_count", 0) for a in matching_anomalies if "null" in str(a.get("type", "")).lower())
                recorded_nulls = max(actual_null_count, anomaly_null_count)

                if recorded_nulls == 0:
                    # Column has 0 nulls, claim that it has nulls is rejected!
                    return "unverified", False, None

                # If specific number claimed, must match recorded null count or null percentage
                if claimed_numbers:
                    recorded_nums = {
                        str(recorded_nulls),
                        f"{col_profile.get('null_percentage', -1):.1f}%",
                        f"{col_profile.get('null_percentage', -1):.0f}%",
                        str(col_profile.get('null_percentage', -1))
                    }
                    if not any(num in recorded_nums for num in claimed_numbers):
                        return "unverified", False, None  # Invented statistic

                return "verified", False, te.id

            elif is_dup_claim:
                recorded_dups = dataset_stats.get("duplicate_rows", 0)
                if recorded_dups == 0:
                    return "unverified", False, None
                if claimed_numbers and not any(str(recorded_dups) == num for num in claimed_numbers):
                    return "unverified", False, None
                return "verified", False, te.id

            elif is_row_count_claim:
                recorded_rows = dataset_stats.get("total_rows", 0)
                if claimed_numbers and not any(str(recorded_rows) == num for num in claimed_numbers):
                    return "unverified", False, None
                return "verified", False, te.id

            elif is_type_claim and col_lower:
                recorded_type = str(col_profile.get("dtype") or col_profile.get("inferred_type", "")).lower()
                if recorded_type and recorded_type in obs_lower:
                    return "verified", False, te.id
                return "unverified", False, None

            elif matching_anomalies:
                # Anomaly claim for this column
                telemetry_str = json.dumps(matching_anomalies) + " " + json.dumps(col_profile)
                if claimed_numbers:
                    if not all(num.rstrip('%') in telemetry_str for num in claimed_numbers):
                        return "unverified", False, None
                return "verified", False, te.id

            else:
                # Unrelated semantic claim not proven by dataset profile
                return "inference", True, te.id

        # =====================================================================
        # 1.4 AST & CODE INSPECTION EVIDENCE (inspect_code)
        # =====================================================================
        elif tool_name == "inspect_code" or "functions" in out_data:
            fns = out_data.get("functions", [])
            classes = out_data.get("classes", [])
            imports = out_data.get("imports", [])

            fn_dict = {f.get("name", "").lower(): f for f in fns if isinstance(f, dict) and f.get("name")}
            class_dict = {c.get("name", "").lower(): c for c in classes if isinstance(c, dict) and c.get("name")}
            import_names = {str(imp).lower() for imp in imports}

            # Runtime behavioral keywords that CANNOT be established by AST syntax alone
            behavioral_keywords = {
                "drop", "drops", "dropping",
                "fail", "fails", "failing",
                "crash", "crashes", "crashing",
                "corrupt", "corrupts", "corrupting",
                "bypass", "bypasses", "bypassing",
                "leak", "leaks", "leaking",
                "skip", "skips", "skipping",
                "improper", "incorrectly", "erroneous"
            }
            is_behavioral_claim = any(bw in obs_lower for bw in behavioral_keywords)
            if is_behavioral_claim:
                # Do NOT infer runtime behavior from syntax alone!
                return "inference", True, te.id

            line_matched = False
            if ev_input.line_number is not None:
                for fn in fns:
                    if fn.get("lineno") == ev_input.line_number or fn.get("end_lineno") == ev_input.line_number:
                        line_matched = True
                        break
                for cl in classes:
                    if cl.get("lineno") == ev_input.line_number:
                        line_matched = True
                        break

            # Syntactic facts: function existence, signature, class, or imports
            claimed_fn = any(fn in obs_lower for fn in fn_dict.keys() if len(fn) > 2)
            claimed_cls = any(cl in obs_lower for cl in class_dict.keys() if len(cl) > 2)
            claimed_import = any(imp in obs_lower for imp in import_names if len(imp) > 2)

            if (claimed_fn or claimed_cls or claimed_import) and (ev_input.line_number is None or line_matched):
                return "verified", False, te.id

        # =====================================================================
        # 1.5 DEPENDENCY EVIDENCE (trace_dependencies)
        # =====================================================================
        elif tool_name == "trace_dependencies":
            direct_deps = {str(d).lower() for d in out_data.get("dependencies", [])}
            reverse_deps = {str(d).lower() for d in (out_data.get("reverse_dependencies") or out_data.get("dependents") or [])}
            imports = {str(imp).lower() for imp in out_data.get("imports", [])}
            packages = {str(pkg).lower() for pkg in out_data.get("packages", [])}

            is_direct_claim = bool(
                re.search(r'\bdepends\b(?:\s+\w+)?\s+on\b', obs_lower)
                or any(kw in obs_lower for kw in ("requires", "upstream dependency", "direct dependency"))
            )
            is_reverse_claim = any(kw in obs_lower for kw in ("is used by", "depended on by", "downstream consumer", "reverse dependency"))
            is_import_claim = any(kw in obs_lower for kw in ("imports", "imported from", "import statement"))
            is_package_claim = any(kw in obs_lower for kw in ("declares package", "dependency package", "installed package", "package requirement"))

            if is_direct_claim:
                if any(dep in obs_lower for dep in direct_deps if len(dep) > 2):
                    return "verified", False, te.id
                elif any(dep in obs_lower for dep in reverse_deps if len(dep) > 2):
                    # Reverse dependency mistakenly claimed as direct dependency!
                    return "unverified", False, None

            elif is_reverse_claim:
                if any(dep in obs_lower for dep in reverse_deps if len(dep) > 2):
                    return "verified", False, te.id

            elif is_import_claim:
                if any(imp in obs_lower for imp in imports if len(imp) > 2):
                    return "verified", False, te.id

            elif is_package_claim:
                if any(pkg in obs_lower for pkg in packages if len(pkg) > 2):
                    return "verified", False, te.id

            else:
                all_known = direct_deps | reverse_deps | imports | packages
                if any(k in obs_lower for k in all_known if len(k) > 2):
                    return "inference", True, te.id

        # =====================================================================
        # SCHEMA DRIFT (inspect_schema)
        # =====================================================================
        elif tool_name == "inspect_schema" or "drift_columns" in out_data or "actual_schema" in out_data:
            drift_cols = {c.lower() for c in out_data.get("drift_columns", [])}
            missing_cols = {c.lower() for c in out_data.get("missing_columns", [])}
            new_cols = {c.lower() for c in out_data.get("new_columns", [])}
            type_mismatches = out_data.get("type_mismatches", [])
            type_mismatch_cols = set()
            for tm in type_mismatches:
                if isinstance(tm, dict) and "column" in tm:
                    type_mismatch_cols.add(tm["column"].lower())
                elif isinstance(tm, str):
                    type_mismatch_cols.add(tm.lower())

            col_lower = ev_input.column_name.lower() if ev_input.column_name else ""
            if col_lower:
                is_missing_claim = any(kw in obs_lower for kw in ("missing", "dropped", "removed", "not found", "lacking", "omitted"))
                is_type_claim = any(kw in obs_lower for kw in ("type mismatch", "wrong type", "incompatible type", "expected type", "cast error"))
                is_new_claim = any(kw in obs_lower for kw in ("added", "new column", "unexpected column", "renamed to", "extra column"))

                if is_missing_claim:
                    if col_lower in missing_cols or col_lower in drift_cols:
                        return "verified", False, te.id
                    else:
                        return "unverified", False, None
                elif is_type_claim:
                    if col_lower in type_mismatch_cols:
                        return "verified", False, te.id
                    else:
                        return "unverified", False, None
                elif is_new_claim:
                    if col_lower in new_cols or col_lower in drift_cols:
                        return "verified", False, te.id
                    else:
                        return "unverified", False, None

        # =====================================================================
        # HYPOTHESIS TESTING (test_hypothesis)
        # =====================================================================
        elif tool_name == "test_hypothesis":
            result_data = out_data.get("result", {}) if isinstance(out_data.get("result"), dict) else out_data
            supported = result_data.get("supported") or out_data.get("supported")
            if supported is True and any(kw in obs_lower for kw in ("support", "confirm", "pass", "valid")):
                return "verified", False, te.id
            elif supported is False and any(kw in obs_lower for kw in ("contradict", "reject", "fail", "refut")):
                return "verified", False, te.id

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
                    p_data = patch_tool_res.data
                    proposal = PatchProposal(
                        investigation_id=self.investigation_id,
                        target_file=p_data["target_file"],
                        original_code_context=p_data["original_code_context"],
                        proposed_code=p_data["proposed_code"],
                        unified_diff=p_data["unified_diff"],
                        explanation=p_data["explanation"],
                        supporting_evidence_ids=p_data["supporting_evidence_ids"],
                        expected_impact=p_data["expected_impact"],
                        potential_risks=p_data["potential_risks"],
                        status="proposed"
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
                            "unified_diff": proposal.unified_diff
                        }
                    )
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
                is_success = (action == "conclude_success" and investigation.confidence_score >= 0.7)

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

    async def _build_final_report(self, rep_input: Optional[Any], is_success: bool) -> None:
        """Constructs and stores the final markdown investigation report."""
        inv_result = await self.db.execute(
            select(Investigation).where(Investigation.id == self.investigation_id)
        )
        inv = inv_result.scalar_one()

        ev_result = await self.db.execute(
            select(EvidenceRecord).where(EvidenceRecord.investigation_id == self.investigation_id)
        )
        evidence = ev_result.scalars().all()

        hyp_result = await self.db.execute(
            select(Hypothesis).where(Hypothesis.investigation_id == self.investigation_id)
        )
        hypotheses = hyp_result.scalars().all()

        confirmed_hyp = [h for h in hypotheses if h.status == "confirmed"]
        rejected_hyp = [h for h in hypotheses if h.status == "rejected"]

        title = getattr(rep_input, "title", None) or f"Investigation Report: {inv.objective[:50]}"
        summary = getattr(rep_input, "summary", None) or (
            f"Investigation concluded with verdict: {'CONFIRMED ROOT CAUSE' if is_success else 'INCONCLUSIVE'}. "
            f"Analyzed {len(evidence)} evidence records across {len(hypotheses)} hypotheses."
        )
        root_cause = getattr(rep_input, "root_cause", None) or (
            confirmed_hyp[0].description if confirmed_hyp else "No singular root cause could be verified with necessary confidence."
        )
        components = getattr(rep_input, "affected_components", []) or list(set(e.source_file for e in evidence))
        risks = getattr(rep_input, "remaining_risks", None) or "None recorded."
        next_steps = getattr(rep_input, "next_steps", []) or ["Review evidence records", "Deploy approved fixes"]

        md_lines = [
            f"# {title}\n",
            f"**Status:** {'COMPLETED' if is_success else 'INCONCLUSIVE'} | **Confidence:** {int(inv.confidence_score * 100)}%\n",
            f"### 1. Investigation Objective\n{inv.objective}\n",
            f"### 2. Summary of Failure\n{summary}\n",
            f"### 3. Affected Pipeline Components\n" + "\n".join([f"- `{c}`" for c in components]) + "\n",
            f"### 4. Root Cause Assessment\n{root_cause}\n",
            f"**Confidence Justification:** {inv.confidence_justification or 'Based on collected runtime evidence'}\n",
            f"### 5. Supporting Traceable Evidence\n"
        ]
        for e in evidence:
            md_lines.append(f"- **[{e.id}] ({e.evidence_type})** `{e.source_file}`: {e.observation}")
        
        md_lines.append(f"\n### 6. Hypotheses Evaluated\n")
        for h in hypotheses:
            md_lines.append(f"- **{h.title}** (`{h.status}`, confidence: {h.confidence}): {h.reasoning or h.description}")

        md_lines.append(f"\n### 7. Remaining Risks\n{risks}\n")
        md_lines.append(f"### 8. Recommended Next Steps\n" + "\n".join([f"1. {s}" for s in next_steps]))

        markdown_report = "\n".join(md_lines)

        report = InvestigationReport(
            investigation_id=self.investigation_id,
            title=title,
            summary=summary,
            root_cause=root_cause,
            affected_components=components,
            supporting_evidence=[{"id": e.id, "type": e.evidence_type, "obs": e.observation} for e in evidence],
            hypotheses_evaluated=[{"id": h.id, "title": h.title, "status": h.status} for h in hypotheses],
            hypotheses_rejected=[{"id": h.id, "title": h.title} for h in rejected_hyp],
            remaining_risks=risks,
            next_steps=next_steps,
            markdown_content=markdown_report
        )
        self.db.add(report)
        await self.db.commit()

        await self.emit_event(
            "report_ready",
            f"Final Report Generated: {title}",
            {"report_id": report.id, "root_cause": root_cause, "is_success": is_success}
        )

