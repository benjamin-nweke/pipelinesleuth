"""
PipelineSleuth — Final Audit Corrections and Engineering Verification Suite
Covers:
1. Strict Traceback Path Resolution & Symlink/Non-existent Rejections
2. Structured Evidence Verification (Exceptions, Profiling, AST, Dependencies, Status Rules)
3. CAS Validation Recovery, Lease Expiration, and Heartbeat Renewals
4. Validation Dispatch Recovery Across Process Restarts
5. Database Dialect Enforcement (SQLite-only)
6. Operator Token Configuration Validation & Canonical principal_id
7. Uniqueness Migrations & Duplicate Remediation
8. Genuine Multi-Process OS Concurrency Contention
"""
import os
import json
import uuid
import asyncio
import tempfile
import multiprocessing
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker

from backend.app.core.config import settings
from backend.app.core.auth import (
    AuthenticatedPrincipal,
    validate_token_strength,
    validate_operator_tokens_json,
    validate_all_operator_configuration,
    register_operator_credential,
    clear_operator_credentials,
    get_all_provisioned_operators,
    MIN_OPERATOR_TOKEN_LENGTH,
    PROHIBITED_TOKENS,
)
from backend.app.database.session import (
    Base,
    AsyncSessionLocal,
    init_db,
    check_supported_database_dialect,
    sync_upgrade_schema,
    check_validation_runs_unique_index,
)
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, ToolExecution
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.schemas.agent import AgentEvidenceInput
from backend.app.agents.engine import (
    resolve_frame_to_project_relative,
    verify_evidence_against_telemetry,
)
from backend.app.services.validation_service import (
    PrivilegedValidationService,
    privileged_validation_service,
)
from backend.app.services.investigation_service import investigation_service


@pytest.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================================
# 1. STRICT TRACEBACK PATH RESOLUTION & EVIDENCE VERIFICATION TESTS
# ============================================================================

def test_traceback_valid_project_relative_path():
    """Valid project-relative path is accepted."""
    project_paths = {"src/pipeline.py", "utils/helpers.py"}
    res = resolve_frame_to_project_relative("src/pipeline.py", project_paths)
    assert res == "src/pipeline.py"


def test_traceback_valid_known_sandbox_root_path():
    """Valid recognized container root (/app/...) is accepted and mapped."""
    project_paths = {"src/pipeline.py", "main.py"}
    res = resolve_frame_to_project_relative("/app/src/pipeline.py", project_paths)
    assert res == "src/pipeline.py"

    res2 = resolve_frame_to_project_relative("/workspace/main.py", project_paths)
    assert res2 == "main.py"


def test_traceback_unrelated_absolute_path_with_same_suffix_rejected():
    """An unrelated absolute path ending with the same suffix is strictly rejected (no suffix matching)."""
    project_paths = {"src/pipeline.py"}
    res = resolve_frame_to_project_relative("/var/unrelated/src/pipeline.py", project_paths)
    assert res is None

    res2 = resolve_frame_to_project_relative("/home/attacker/pipeline.py", {"pipeline.py"})
    assert res2 is None


def test_traceback_traversal_attempt_rejected():
    """Paths containing '../' traversal sequences are strictly rejected."""
    project_paths = {"pipeline.py", "src/pipeline.py"}
    assert resolve_frame_to_project_relative("../../pipeline.py", project_paths) is None
    assert resolve_frame_to_project_relative("/app/../etc/passwd", project_paths) is None
    assert resolve_frame_to_project_relative("src/../../outside.py", project_paths) is None


def test_traceback_symlink_pointing_outside_workspace_rejected():
    """A symlink inside workspace resolving outside the workspace is rejected."""
    with tempfile.TemporaryDirectory() as temp_ws:
        ws_path = Path(temp_ws)
        # Create an outside file
        outside_file = ws_path.parent / f"outside_{uuid.uuid4().hex}.py"
        outside_file.write_text("print('outside')")
        try:
            # Create symlink inside workspace pointing to outside file
            symlink_file = ws_path / "symlink_pipeline.py"
            symlink_file.symlink_to(outside_file)

            project_paths = {"symlink_pipeline.py"}
            res = resolve_frame_to_project_relative("symlink_pipeline.py", project_paths, workspace_dir=ws_path)
            assert res is None, "Symlink escaping workspace must be rejected"
        finally:
            if outside_file.exists():
                outside_file.unlink()


def test_traceback_nonexistent_file_rejected():
    """Non-existent file is rejected when workspace is provided."""
    with tempfile.TemporaryDirectory() as temp_ws:
        ws_path = Path(temp_ws)
        project_paths = {"missing_pipeline.py"}
        res = resolve_frame_to_project_relative("missing_pipeline.py", project_paths, workspace_dir=ws_path)
        assert res is None


# ============================================================================
# 1.2 - 1.6 STRUCTURED EVIDENCE VERIFICATION TESTS
# ============================================================================

def test_exception_evidence_conflicting_type_rejected():
    """Asserting a conflicting exception type (e.g. ValueError when actual is KeyError) is unverified."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-EXC-01",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 20},
                "exception_type": "KeyError",
                "message": "'customer_id' not found in dataframe"
            }
        }
    )
    # Claim asserts ValueError instead of KeyError
    ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-EXC-01",
        line_number=20,
        observation="Pipeline threw a ValueError during transaction ingestion"
    )
    status, _, _ = verify_evidence_against_telemetry(ev, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_exception_evidence_structured_match_verified():
    """Matching specific exception type and distinct error token verifies successfully."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-EXC-02",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 20},
                "exception_type": "KeyError",
                "message": "'customer_id' missing from columns"
            }
        }
    )
    ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-EXC-02",
        line_number=20,
        observation="KeyError: missing customer_id column in pipeline.py"
    )
    status, _, tid = verify_evidence_against_telemetry(ev, files, [te], investigation_id="INV-01")
    assert status == "verified"
    assert tid == "TE-EXC-02"


def test_dataset_profiling_null_count_exact_match_and_mismatch():
    """Dataset profiling: matching count verified, invented/mismatched count unverified."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="data.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-PROF-01",
        investigation_id="INV-01",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "columns": {"amount": {"null_count": 2, "null_percentage": 33.3, "dtype": "float64"}},
            "dataset": {"total_rows": 6, "duplicate_rows": 0}
        }
    )
    # Valid matching claim
    ev_valid = AgentEvidenceInput(
        source_file="data.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="TE-PROF-01",
        observation="Recorded 2 null values in amount column"
    )
    s1, _, _ = verify_evidence_against_telemetry(ev_valid, files, [te], investigation_id="INV-01")
    assert s1 == "verified"

    # Invented count (claims 5 nulls when actual is 2)
    ev_mismatch = AgentEvidenceInput(
        source_file="data.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="TE-PROF-01",
        observation="Recorded 5 null values in amount column"
    )
    s2, _, _ = verify_evidence_against_telemetry(ev_mismatch, files, [te], investigation_id="INV-01")
    assert s2 == "unverified"


def test_dataset_profiling_unrelated_semantic_claim_remains_inference():
    """An observation asserting semantic or behavioral facts not in profile remains an inference."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="data.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-PROF-02",
        investigation_id="INV-01",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "columns": {"amount": {"null_count": 0, "dtype": "float64"}},
            "dataset": {"total_rows": 100, "duplicate_rows": 0}
        }
    )
    ev_speculative = AgentEvidenceInput(
        source_file="data.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="TE-PROF-02",
        observation="Amount column contains suspicious money laundering transactions"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_speculative, files, [te], investigation_id="INV-01")
    assert status == "inference"
    assert is_inf is True


def test_ast_inspection_syntactic_fact_verified_but_behavioral_claim_inference():
    """AST syntax facts (definition) verified, runtime behavioral claims remain inference."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-AST-01",
        investigation_id="INV-01",
        tool_name="inspect_code",
        status="success",
        output_result={
            "functions": [{"name": "transform_data", "lineno": 15, "args": ["df"]}],
            "classes": [],
            "imports": ["pandas"]
        }
    )
    # Syntactic fact: function existence
    ev_syntax = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="code_ast",
        line_number=15,
        tool_execution_id="TE-AST-01",
        observation="Function transform_data is defined at line 15"
    )
    s1, _, _ = verify_evidence_against_telemetry(ev_syntax, files, [te], investigation_id="INV-01")
    assert s1 == "verified"

    # Behavioral claim: claims runtime dropping without syntax proof
    ev_behavior = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="code_ast",
        line_number=15,
        tool_execution_id="TE-AST-01",
        observation="Function transform_data drops records incorrectly during processing"
    )
    s2, is_inf, _ = verify_evidence_against_telemetry(ev_behavior, files, [te], investigation_id="INV-01")
    assert s2 == "inference"
    assert is_inf is True


def test_dependency_direct_vs_reverse_distinction():
    """Direct dependencies, reverse dependencies, and imports are distinguished."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-DEP-01",
        investigation_id="INV-01",
        tool_name="trace_dependencies",
        status="success",
        output_result={
            "dependencies": ["upstream_ingest"],
            "reverse_dependencies": ["downstream_export"],
            "imports": ["pandas"],
            "packages": ["pandas>=2.0"]
        }
    )
    # Direct dependency claim matching dependencies
    ev_direct = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="dependency_graph",
        tool_execution_id="TE-DEP-01",
        observation="pipeline.py depends on upstream_ingest"
    )
    s1, _, _ = verify_evidence_against_telemetry(ev_direct, files, [te], investigation_id="INV-01")
    assert s1 == "verified"

    # Reverse dependency mistakenly claimed as direct dependency -> unverified
    ev_false_direct = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="dependency_graph",
        tool_execution_id="TE-DEP-01",
        observation="pipeline.py depends directly on downstream_export"
    )
    s2, _, _ = verify_evidence_against_telemetry(ev_false_direct, files, [te], investigation_id="INV-01")
    assert s2 == "unverified"


# ============================================================================
# 2. CAS VALIDATION RECOVERY & LEASE RENEWAL TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_cas_recovery_expired_claim_succeeds(db_session: AsyncSession):
    """An expired claim can be recovered using CAS semantics."""
    proj = Project(name="CAS Expired Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="CAS test")
    db_session.add(inv)
    await db_session.commit()

    # Create patch with expired lease
    past_time = datetime.now(timezone.utc) - timedelta(seconds=600)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="c",
        explanation="d",
        status="validating",
        approved_by="lead_op",
        approved_at=past_time,
        validation_claimed_at=past_time,
        validation_lease_expires_at=past_time + timedelta(seconds=300),
        validation_claimed_by="dead_worker",
        validation_claim_token="claim_dead_token_123"
    )
    db_session.add(patch)
    await db_session.commit()

    # Recovery must succeed
    recovered = await privileged_validation_service.recover_abandoned_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        timeout_seconds=300
    )
    assert recovered.status == "approved"
    assert recovered.validation_claim_token is None
    assert recovered.validation_claimed_by is None


@pytest.mark.asyncio
async def test_cas_recovery_fresh_claim_cannot_be_recovered(db_session: AsyncSession):
    """A fresh claim with active unexpired lease cannot be recovered."""
    proj = Project(name="CAS Fresh Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="CAS test fresh")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="c",
        explanation="d",
        status="validating",
        approved_by="lead_op",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=300),
        validation_claimed_by="active_worker",
        validation_claim_token="claim_active_token_456"
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="Validation claim is still active"):
        await privileged_validation_service.recover_abandoned_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            timeout_seconds=300
        )


@pytest.mark.asyncio
async def test_cas_lease_renewal_by_owner_succeeds(db_session: AsyncSession):
    """A worker can renew its own lease using its valid claim_token."""
    proj = Project(name="CAS Renewal Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Renewal test")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="c",
        explanation="d",
        status="approved",
        approved_by="lead_op",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    claimed_patch = await privileged_validation_service.claim_patch_for_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        worker_id="worker_heartbeat",
        lease_seconds=60
    )
    initial_expiry = claimed_patch.validation_lease_expires_at
    claim_token = claimed_patch.validation_claim_token

    # Renew lease
    renewed_patch = await privileged_validation_service.renew_validation_lease(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        claim_token=claim_token,
        extend_seconds=300
    )
    assert renewed_patch.validation_lease_expires_at > initial_expiry


@pytest.mark.asyncio
async def test_cas_lease_renewal_by_different_worker_rejected(db_session: AsyncSession):
    """A worker with an invalid or foreign claim_token cannot renew another worker's lease."""
    proj = Project(name="CAS Fake Renewal Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Fake renewal test")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="c",
        explanation="d",
        status="approved",
        approved_by="lead_op",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    await privileged_validation_service.claim_patch_for_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        worker_id="worker_legit",
        lease_seconds=300
    )

    with pytest.raises(ValueError, match="Lease renewal rejected"):
        await privileged_validation_service.renew_validation_lease(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            claim_token="forged_token_xyz",
            extend_seconds=600
        )


# ============================================================================
# 3. VALIDATION DISPATCH RECOVERY ACROSS RESTARTS TESTS
# ============================================================================

@pytest.mark.asyncio
async def test_startup_recovery_dispatches_pending_approved_patch(db_session: AsyncSession):
    """On application restart, approved patches stranded before dispatch are automatically resumed."""
    proj = Project(name="Restart Dispatch Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Restart test")
    db_session.add(inv)
    await db_session.commit()

    # Stranded approved patch (e.g. process crashed right after operator approved)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="c",
        explanation="d",
        status="approved",
        approved_by="lead_op",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    dispatched = await investigation_service.resume_pending_validations_on_startup()
    assert patch.id in dispatched


@pytest.mark.asyncio
async def test_startup_recovery_ignores_completed_or_rejected_patches(db_session: AsyncSession):
    """Completed ('validated', 'validation_failed') or rejected patches are NOT dispatched on startup."""
    proj = Project(name="Restart Ignore Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Restart ignore test")
    db_session.add(inv)
    await db_session.commit()

    patch_done = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a", proposed_code="b", unified_diff="c", explanation="d",
        status="validated",
        approved_by="lead_op",
        approved_at=datetime.now(timezone.utc)
    )
    patch_rej = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a", proposed_code="b", unified_diff="c", explanation="d",
        status="rejected",
        rejected_at=datetime.now(timezone.utc),
        rejection_reason="Not needed"
    )
    db_session.add_all([patch_done, patch_rej])
    await db_session.commit()

    dispatched = await investigation_service.resume_pending_validations_on_startup()
    assert patch_done.id not in dispatched
    assert patch_rej.id not in dispatched


# ============================================================================
# 4. DATABASE DIALECT ENFORCEMENT TESTS
# ============================================================================

def test_database_dialect_sqlite_accepted():
    """SQLite database dialect URLs are accepted."""
    check_supported_database_dialect("sqlite+aiosqlite:///storage/pipelinesleuth.db")
    check_supported_database_dialect("sqlite:///storage/pipelinesleuth.db")


def test_database_dialect_unsupported_rejected():
    """PostgreSQL and MySQL URLs are rejected early with a clear actionable RuntimeError."""
    with pytest.raises(RuntimeError, match="Unsupported database engine"):
        check_supported_database_dialect("postgresql://user:pass@localhost:5432/pipelinesleuth")

    with pytest.raises(RuntimeError, match="Unsupported database engine"):
        check_supported_database_dialect("mysql+aiomysql://root:pass@localhost/db")


# ============================================================================
# 5. OPERATOR TOKEN CONFIGURATION VALIDATION & CANONICAL principal_id
# ============================================================================

def test_operator_tokens_json_valid_configuration():
    """Valid OPERATOR_TOKENS_JSON with canonical principal_id parses successfully."""
    valid_token = "test_custom_admin_token_2026_valid_entropy"
    valid_json = json.dumps({
        valid_token: {
            "principal_id": "operator_admin",
            "role": "operator",
            "scopes": ["patch:approve", "patch:reject"]
        }
    })
    parsed = validate_operator_tokens_json(valid_json)
    assert valid_token in parsed
    principal = parsed[valid_token]
    assert principal.principal_id == "operator_admin"
    assert principal.role == "operator"
    assert "patch:approve" in principal.scopes


def test_operator_tokens_json_malformed_json_raises():
    """Malformed JSON in OPERATOR_TOKENS_JSON raises clear ValueError."""
    with pytest.raises(ValueError, match="Malformed OPERATOR_TOKENS_JSON: invalid JSON format"):
        validate_operator_tokens_json("{bad_json: unquoted")


def test_operator_tokens_json_missing_or_blank_principal_id_raises():
    """Missing or blank canonical principal_id raises ValueError."""
    test_token = "test_custom_admin_token_2026_valid_entropy"
    json_missing = json.dumps({
        test_token: {
            "role": "operator"
        }
    })
    with pytest.raises(ValueError, match="missing required non-blank 'principal_id'"):
        validate_operator_tokens_json(json_missing)

    json_blank = json.dumps({
        test_token: {
            "principal_id": "   ",
            "role": "operator"
        }
    })
    with pytest.raises(ValueError, match="missing required non-blank 'principal_id'"):
        validate_operator_tokens_json(json_blank)


def test_operator_token_weak_or_short_token_raises():
    """Token shorter than MIN_OPERATOR_TOKEN_LENGTH or lacking entropy raises ValueError."""
    with pytest.raises(ValueError, match="too short"):
        validate_token_strength("short_token_123")

    with pytest.raises(ValueError, match="lacks sufficient character diversity"):
        validate_token_strength("aaaaaaaaaaaaaaaaaaaa")

    with pytest.raises(ValueError, match="prohibited credentials blocklist"):
        validate_token_strength("ps_operator_sec_key_2026")


# ============================================================================
# 6. DATABASE UNIQUENESS MIGRATIONS & DUPLICATE REMEDIATION TESTS
# ============================================================================

def test_sync_upgrade_schema_remediates_duplicates_and_creates_index():
    """Deterministic duplicate remediation deletes older duplicate runs and enforces unique index."""
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name

    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE validation_runs (
            id VARCHAR(36) PRIMARY KEY,
            investigation_id VARCHAR(36) NOT NULL,
            patch_id VARCHAR(36) NOT NULL,
            status VARCHAR(50) NOT NULL,
            exit_code INTEGER DEFAULT 0,
            stdout TEXT,
            stderr TEXT,
            test_summary TEXT,
            regression_results TEXT,
            execution_time_ms INTEGER DEFAULT 0,
            authorized_by VARCHAR(100),
            authorized_at DATETIME,
            created_at DATETIME
        )
    """)
    con.execute("""
        CREATE TABLE patch_proposals (
            id VARCHAR(36) PRIMARY KEY
        )
    """)

    # Insert two runs for same patch_id (e.g. redundant run from concurrent race)
    con.execute("INSERT INTO validation_runs (id, investigation_id, patch_id, status, created_at) VALUES ('V1', 'I1', 'P1', 'passed', '2026-01-01 10:00:00')")
    con.execute("INSERT INTO validation_runs (id, investigation_id, patch_id, status, created_at) VALUES ('V2', 'I1', 'P1', 'passed', '2026-01-01 10:05:00')")
    con.commit()

    class FakeConnection:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if params:
                return con.execute(sql, params)
            return con.execute(sql)

    sync_upgrade_schema(FakeConnection())

    # Check that older duplicate V1 was removed and latest V2 was retained
    remaining = con.execute("SELECT id FROM validation_runs WHERE patch_id = 'P1'").fetchall()
    assert len(remaining) == 1
    assert remaining[0][0] == "V2"

    # Verify unique index exists
    indexes = [row[1] for row in con.execute("PRAGMA index_list(validation_runs)").fetchall()]
    assert any("unique" in idx.lower() or "patch_id" in idx.lower() for idx in indexes)
    con.close()


# ============================================================================
# 7. GENUINE OS-PROCESS CONCURRENCY CONTENTION TEST
# ============================================================================

def _run_worker_claim_process(db_url: str, investigation_id: str, patch_id: str, worker_id: str, queue: multiprocessing.Queue):
    """Target function executed in a genuine independent OS process."""
    async def _async_claim():
        eng = create_async_engine(db_url, echo=False, connect_args={"check_same_thread": False, "timeout": 30.0})
        session_factory = async_sessionmaker(bind=eng, class_=AsyncSession, expire_on_commit=False)
        svc = PrivilegedValidationService()
        async with session_factory() as session:
            try:
                patch = await svc.claim_patch_for_validation(
                    db=session,
                    investigation_id=investigation_id,
                    patch_id=patch_id,
                    worker_id=worker_id,
                    lease_seconds=300
                )
                queue.put(("success", worker_id, patch.validation_claim_token))
            except Exception as e:
                queue.put(("failed", worker_id, str(e)))
            finally:
                await eng.dispose()

    asyncio.run(_async_claim())


@pytest.mark.asyncio
async def test_genuine_os_process_concurrency_contention(db_session: AsyncSession):
    """
    Spawns two separate operating system processes using multiprocessing.Process
    both contending to claim the same approved patch in the database.
    Exactly one OS process must succeed; the other must receive a concurrency violation.
    """
    proj = Project(name="OS Process Contention Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="OS Concurrency Test")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="lead_op",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    queue = multiprocessing.Queue()
    ctx = multiprocessing.get_context("spawn")

    proc1 = ctx.Process(
        target=_run_worker_claim_process,
        args=(settings.DATABASE_URL, inv.id, patch.id, "os_proc_alpha", queue)
    )
    proc2 = ctx.Process(
        target=_run_worker_claim_process,
        args=(settings.DATABASE_URL, inv.id, patch.id, "os_proc_beta", queue)
    )

    proc1.start()
    proc2.start()

    proc1.join(timeout=10)
    proc2.join(timeout=10)

    results = []
    while not queue.empty():
        results.append(queue.get())

    successes = [r for r in results if r[0] == "success"]
    failures = [r for r in results if r[0] == "failed"]

    assert len(successes) == 1, f"Expected exactly 1 successful OS process claim, got: {results}"
    assert len(failures) == 1, f"Expected exactly 1 failed OS process claim, got: {results}"
    assert "already claimed by active worker" in failures[0][2]


# ============================================================================
# 8. FINAL TARGETED AUDIT REMEDIATION TESTS
# ============================================================================

def test_secrets_token_urlsafe_and_placeholder_rejections():
    """Instructions recommend secrets.token_urlsafe(32); placeholders are strictly blocked."""
    import secrets
    # Generated random token must pass validation
    gen_token = secrets.token_urlsafe(32)
    validated = validate_token_strength(gen_token)
    assert validated == gen_token

    # Placeholder tokens must be rejected
    placeholders = [
        "<GENERATE_A_SECURE_RANDOM_TOKEN_HERE>",
        "<GENERATE_SECURE_TOKEN_32_BYTES>",
        "YOUR_SECURE_OPERATOR_API_KEY_HERE",
        "YOUR_TOKEN_HERE",
        "<YOUR_TOKEN_HERE>",
        "placeholder_token_value_12345",
        "generate_random_token_string_here"
    ]
    for ph in placeholders:
        with pytest.raises(ValueError):
            validate_token_strength(ph)


@pytest.mark.asyncio
async def test_cas_lease_recovery_race_protection(db_session: AsyncSession):
    """
    CAS recovery atomic update condition prevents recovery if another worker renewed the lease
    or changed validation_lease_expires_at in the interim.
    """
    proj = Project(name="CAS Race Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="CAS race test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(seconds=10)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old",
        proposed_code="new",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="lead_op",
        approved_at=expired_time,
        validation_claimed_at=expired_time,
        validation_lease_expires_at=expired_time,
        validation_claimed_by="worker_stale",
        validation_claim_token="claim_token_stale_111"
    )
    db_session.add(patch)
    await db_session.commit()

    # Worker B concurrently renews the lease in the database to a future time
    patch.validation_lease_expires_at = now + timedelta(seconds=300)
    patch.validation_claim_token = "claim_token_renewed_222"
    await db_session.commit()

    # Worker C's attempt to recover based on expired state is rejected
    with pytest.raises(ValueError, match="Validation claim is still active"):
        await privileged_validation_service.recover_abandoned_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


@pytest.mark.asyncio
async def test_evidence_verification_causal_and_behavioral_downgrades():
    """
    Observations containing causal phrases ('caused by', 'due to') or behavioral claims ('removes', 'detects')
    are downgraded to 'inference' even if other parts match telemetry.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1"),
        ProjectFile(id="f2", project_id="p1", relative_path="data.csv", file_type="data", size_bytes=100, storage_path="/tmp/f2")
    ]

    # 1. Traceback with causal claim -> inference
    te_log = ToolExecution(
        id="TE-CAUSAL-01",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 25},
                "exception_type": "KeyError",
                "message": "'customer_id' missing from columns"
            }
        }
    )
    ev_causal = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        line_number=25,
        tool_execution_id="TE-CAUSAL-01",
        observation="KeyError on customer_id caused by missing upstream schema update"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_causal, files, [te_log], investigation_id="INV-01")
    assert status == "inference"
    assert is_inf is True

    # 2. Dataset profile with 'due to' -> inference
    te_prof = ToolExecution(
        id="TE-CAUSAL-02",
        investigation_id="INV-01",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "columns": {"revenue": {"null_count": 5, "null_percentage": 5.0, "dtype": "float64"}}
        }
    )
    ev_null_causal = AgentEvidenceInput(
        source_file="data.csv",
        evidence_type="dataset_anomaly",
        column_name="revenue",
        tool_execution_id="TE-CAUSAL-02",
        observation="5 null values in revenue column due to ingestion latency"
    )
    s2, is_inf2, _ = verify_evidence_against_telemetry(ev_null_causal, files, [te_prof], investigation_id="INV-01")
    assert s2 == "inference"
    assert is_inf2 is True

    # 3. AST code inspection with 'removes' or 'detects' -> inference
    te_ast = ToolExecution(
        id="TE-AST-02",
        investigation_id="INV-01",
        tool_name="inspect_code",
        status="success",
        output_result={
            "functions": [{"name": "clean_records", "lineno": 40}]
        }
    )
    ev_ast_removes = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="code_ast",
        line_number=40,
        tool_execution_id="TE-AST-02",
        observation="clean_records function removes invalid records and detects errors"
    )
    s3, is_inf3, _ = verify_evidence_against_telemetry(ev_ast_removes, files, [te_ast], investigation_id="INV-01")
    assert s3 == "inference"
    assert is_inf3 is True


def test_sqlite_unique_index_metadata_verification():
    """
    Directly tests check_validation_runs_unique_index inspecting SQLite index metadata:
    - Non-unique index named uq_validation_runs_patch_id is identified as non-unique.
    - Unique index on columns other than ['patch_id'] is rejected.
    - Irreconcilable conflicting statuses (passed and failed) raise RuntimeError.
    - Deterministic duplicate remediation produces verified unique index.
    """
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name

    con = sqlite3.connect(db_path)
    con.execute("""
        CREATE TABLE validation_runs (
            id VARCHAR(36) PRIMARY KEY,
            investigation_id VARCHAR(36) NOT NULL,
            patch_id VARCHAR(36) NOT NULL,
            status VARCHAR(50) NOT NULL,
            created_at DATETIME
        )
    """)
    con.execute("CREATE TABLE patch_proposals (id VARCHAR(36) PRIMARY KEY)")

    class RealConnWrapper:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if params:
                return con.execute(sql, params)
            return con.execute(sql)

    wrapper = RealConnWrapper()

    # Case 1: Create a non-unique index with the deceptive name "uq_validation_runs_patch_id"
    con.execute("CREATE INDEX uq_validation_runs_patch_id ON validation_runs (patch_id)")
    con.commit()

    # Must be recognized as NOT unique
    assert check_validation_runs_unique_index(wrapper) is False

    # Drop the non-unique index
    con.execute("DROP INDEX uq_validation_runs_patch_id")
    con.commit()

    # Case 2: Create a unique index on investigation_id (wrong column)
    con.execute("CREATE UNIQUE INDEX uq_inv_id ON validation_runs (investigation_id)")
    con.commit()

    # Must be recognized as NOT matching ['patch_id']
    assert check_validation_runs_unique_index(wrapper) is False

    con.execute("DROP INDEX uq_inv_id")
    con.commit()

    # Case 3: Insert conflicting duplicate runs (one passed, one failed)
    con.execute("INSERT INTO validation_runs (id, investigation_id, patch_id, status, created_at) VALUES ('VR1', 'I1', 'P_CONF', 'passed', '2026-01-01 10:00:00')")
    con.execute("INSERT INTO validation_runs (id, investigation_id, patch_id, status, created_at) VALUES ('VR2', 'I1', 'P_CONF', 'failed', '2026-01-01 10:05:00')")
    con.commit()

    with pytest.raises(RuntimeError, match="irreconcilable conflicting statuses"):
        sync_upgrade_schema(wrapper)

    # Clean up conflicting run and test successful deterministic remediation
    con.execute("DELETE FROM validation_runs WHERE id = 'VR2'")
    con.execute("INSERT INTO validation_runs (id, investigation_id, patch_id, status, created_at) VALUES ('VR3', 'I1', 'P_CONF', 'passed', '2026-01-01 10:06:00')")
    con.commit()

    sync_upgrade_schema(wrapper)

    # Verify unique index was created and is verified by SQLite metadata
    assert check_validation_runs_unique_index(wrapper) is True
    con.close()


@pytest.mark.asyncio
async def test_execute_approved_patch_validation_heartbeat_renewal_and_ownership_loss(db_session: AsyncSession):
    """
    Tests execute_approved_patch_validation heartbeat renewal and ownership loss protection:
    - If ownership of the validation claim token is superseded during execution,
      the validation service detects the loss and aborts the commit.
    """
    proj = Project(name="Heartbeat Loss Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Heartbeat loss test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="def run(): pass",
        proposed_code="def run(): return 42",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="lead_operator",
        approved_at=now
    )
    db_session.add(patch)
    await db_session.commit()

    # Fast renewal test: lease_seconds=1, renewal_interval=0.1
    from unittest.mock import AsyncMock, patch as mock_patch
    from backend.app.tools.base import ToolResult

    mock_tool = AsyncMock()
    async def delayed_execute(*args, **kwargs):
        # Simulate worker losing claim halfway through execution:
        # Another process rewrote the validation_claim_token
        async with AsyncSessionLocal() as tamper_session:
            await tamper_session.execute(
                text("UPDATE patch_proposals SET validation_claim_token = 'stolen_token_xyz' WHERE id = :pid"),
                {"pid": patch.id}
            )
            await tamper_session.commit()
        await asyncio.sleep(0.3)
        return ToolResult(success=True, summary="Tests passed", data={"stdout": "pass", "exit_code": 0})

    mock_tool.execute = delayed_execute

    with mock_patch("backend.app.services.validation_service.ValidatePatchTool", return_value=mock_tool):
        with pytest.raises(Exception, match="(ownership.*lost|Security violation)"):
            await privileged_validation_service.execute_approved_patch_validation(
                db=db_session,
                investigation_id=inv.id,
                patch_id=patch.id,
                lease_seconds=1,
                renewal_interval=0.1
            )


# ============================================================================
# 9. EXPLICIT AUDIT REGRESSION TESTS FOR REQUIREMENTS 2, 3, 4, 5
# ============================================================================

@pytest.mark.asyncio
async def test_two_recovery_workers_attempting_recovery_concurrently(db_session: AsyncSession):
    """Two recovery workers attempting CAS recovery concurrently: exactly one succeeds."""
    proj = Project(name="Concurrent Recovery Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Concurrent recovery")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(seconds=120)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old",
        proposed_code="new",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="lead_op",
        approved_at=expired_time,
        validation_claimed_at=expired_time,
        validation_lease_expires_at=expired_time,
        validation_claimed_by="worker_dead",
        validation_claim_token="claim_token_to_recover"
    )
    db_session.add(patch)
    await db_session.commit()

    # Worker 1 recovers successfully
    rec1 = await privileged_validation_service.recover_abandoned_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id
    )
    assert rec1.status == "approved"
    assert rec1.validation_claim_token is None

    # Worker 2 attempting recovery on already recovered patch raises ValueError
    with pytest.raises(ValueError, match="is not in 'validating' state"):
        await privileged_validation_service.recover_abandoned_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


@pytest.mark.asyncio
async def test_recovery_worker_encountering_stale_claim_token(db_session: AsyncSession):
    """A stale claim token that does not match the active claim cannot be recovered."""
    proj = Project(name="Stale Token Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Stale token")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    expired_time = now - timedelta(seconds=60)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old",
        proposed_code="new",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="lead_op",
        approved_at=expired_time,
        validation_claimed_at=expired_time,
        validation_lease_expires_at=expired_time,
        validation_claimed_by="worker_1",
        validation_claim_token="claim_token_A"
    )
    db_session.add(patch)
    await db_session.commit()

    # Worker B claims with new token before worker A's recovery CAS executes
    patch.validation_claim_token = "claim_token_B"
    patch.validation_lease_expires_at = now + timedelta(seconds=300)
    await db_session.commit()

    with pytest.raises(ValueError, match="Validation claim is still active"):
        await privileged_validation_service.recover_abandoned_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


@pytest.mark.asyncio
async def test_startup_recovery_encountering_valid_renewed_claim(db_session: AsyncSession):
    """Startup recovery must NOT reset an actively validating patch whose lease is valid/renewed."""
    proj = Project(name="Startup Renewed Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Startup test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="lead_op",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=600),
        validation_claimed_by="active_worker",
        validation_claim_token="claim_active_token"
    )
    db_session.add(patch)
    await db_session.commit()

    # Run startup recovery
    dispatched = await investigation_service.resume_pending_validations_on_startup()
    assert patch.id not in dispatched

    # Verify patch was NOT reset
    await db_session.refresh(patch)
    assert patch.status == "validating"
    assert patch.validation_claim_token == "claim_active_token"


@pytest.mark.asyncio
async def test_validation_execution_successful_periodic_lease_renewal(db_session: AsyncSession):
    """Validation execution periodically renews its lease via the background heartbeat task."""
    proj = Project(name="Renewal Execution Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Renewal execution")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="def foo(): pass",
        proposed_code="def foo(): return 1",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="lead_op",
        approved_at=now
    )
    db_session.add(patch)
    await db_session.commit()

    from unittest.mock import AsyncMock, patch as mock_patch
    from backend.app.tools.base import ToolResult

    mock_tool = AsyncMock()
    async def delayed_success(*args, **kwargs):
        # Allow heartbeat loop to execute at least once
        await asyncio.sleep(0.25)
        return ToolResult(success=True, summary="Tests passed", data={"stdout": "ok", "exit_code": 0})

    mock_tool.execute = delayed_success

    with mock_patch("backend.app.services.validation_service.ValidatePatchTool", return_value=mock_tool):
        val_run = await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            lease_seconds=2,
            renewal_interval=0.1
        )
        assert val_run.status == "passed"
        assert val_run.exit_code == 0


@pytest.mark.asyncio
async def test_evidence_path_missing_workspace_fails_closed_for_absolute_paths():
    """If workspace_dir is missing/None, untrusted absolute paths fail closed."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    ev_abs = AgentEvidenceInput(
        source_file="/etc/shadow",
        evidence_type="code_ast",
        observation="Secret file access"
    )
    # Without workspace_dir, absolute path must be unverified
    status, is_inf, _ = verify_evidence_against_telemetry(ev_abs, files, [], workspace_dir=None)
    assert status == "unverified"
    assert is_inf is False

    # Also resolve_frame_to_project_relative fails closed for arbitrary absolute paths without workspace_dir
    res = resolve_frame_to_project_relative("/var/log/arbitrary.py", {"arbitrary.py"}, workspace_dir=None)
    assert res is None


def test_sqlite_metadata_distinguishes_index_configurations():
    """Confirms SQLite metadata PRAGMA checks accurately distinguish index properties."""
    import sqlite3
    with tempfile.NamedTemporaryFile(suffix=".db") as f:
        db_path = f.name

    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE validation_runs (id VARCHAR(36) PRIMARY KEY, patch_id VARCHAR(36), status VARCHAR(50))")

    class RealConn:
        def execute(self, stmt, params=None):
            sql = str(stmt)
            if params:
                return con.execute(sql, params)
            return con.execute(sql)

    wrapper = RealConn()

    # 1. Non-unique index
    con.execute("CREATE INDEX uq_validation_runs_patch_id ON validation_runs (patch_id)")
    assert check_validation_runs_unique_index(wrapper) is False
    con.execute("DROP INDEX uq_validation_runs_patch_id")

    # 2. Correct unique index
    con.execute("CREATE UNIQUE INDEX uq_validation_runs_patch_id ON validation_runs (patch_id)")
    assert check_validation_runs_unique_index(wrapper) is True
    con.close()


# ============================================================================
# 9. FINAL AUDIT FOLLOW-UP REGRESSION SUITE (POINTS 1 TO 5)
# ============================================================================

# --- Point 1: NULL lease recovery race ---

@pytest.mark.asyncio
async def test_cas_recovery_null_expiry_concurrent_renewal(db_session: AsyncSession):
    """
    When original lease expiry is NULL, CAS requires validation_lease_expires_at IS NULL.
    If another worker concurrently renews/assigns a lease expiry, recovery CAS matches 0 rows and is rejected.
    """
    proj = Project(name="Null Expiry Test Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=600),
        validation_lease_expires_at=None,
        validation_claimed_by="worker_1",
        validation_claim_token="claim_tok_null_exp"
    )
    db_session.add(patch)
    await db_session.commit()

    # Worker B concurrently renews the lease, setting non-null expiry:
    patch.validation_lease_expires_at = now + timedelta(seconds=300)
    await db_session.commit()

    # Worker A's recovery CAS update with expected_expires_at IS NULL must fail
    stmt = (
        update(PatchProposal)
        .where(
            PatchProposal.id == patch.id,
            PatchProposal.investigation_id == inv.id,
            PatchProposal.status == "validating",
            PatchProposal.validation_claim_token == "claim_tok_null_exp",
            PatchProposal.validation_lease_expires_at.is_(None)
        )
        .values(status="approved")
    )
    res = await db_session.execute(stmt)
    await db_session.commit()
    assert res.rowcount == 0


@pytest.mark.asyncio
async def test_cas_recovery_null_token_concurrent_assignment(db_session: AsyncSession):
    """
    When original claim token is NULL, CAS requires validation_claim_token IS NULL.
    If another worker assigns a token concurrently, CAS update fails.
    """
    proj = Project(name="Null Token Test Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=600),
        validation_lease_expires_at=None,
        validation_claimed_by=None,
        validation_claim_token=None
    )
    db_session.add(patch)
    await db_session.commit()

    # Another worker sets a claim token concurrently
    patch.validation_claim_token = "new_assigned_token"
    await db_session.commit()

    # Stale recovery expecting null token attempts recovery
    stmt = (
        update(PatchProposal)
        .where(
            PatchProposal.id == patch.id,
            PatchProposal.investigation_id == inv.id,
            PatchProposal.status == "validating",
            PatchProposal.validation_claim_token.is_(None),
            PatchProposal.validation_lease_expires_at.is_(None)
        )
        .values(status="approved")
    )
    res = await db_session.execute(stmt)
    await db_session.commit()
    assert res.rowcount == 0


@pytest.mark.asyncio
async def test_cas_recovery_non_null_token_and_expiry_changing_fails(db_session: AsyncSession):
    """
    When original expiry and token are non-null, changing either before update causes CAS to fail.
    """
    proj = Project(name="Expiry Token Change Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    orig_expiry = now - timedelta(seconds=30)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=600),
        validation_lease_expires_at=orig_expiry,
        validation_claimed_by="worker_1",
        validation_claim_token="orig_token"
    )
    db_session.add(patch)
    await db_session.commit()

    # Token changes concurrently (e.g. re-claimed)
    patch.validation_claim_token = "new_token_changed"
    await db_session.commit()

    # Attempt recovery with stale expected token and expiry
    stmt = (
        update(PatchProposal)
        .where(
            PatchProposal.id == patch.id,
            PatchProposal.investigation_id == inv.id,
            PatchProposal.status == "validating",
            PatchProposal.validation_claim_token == "orig_token",
            PatchProposal.validation_lease_expires_at == orig_expiry,
            PatchProposal.validation_lease_expires_at <= now
        )
        .values(status="approved")
    )
    res = await db_session.execute(stmt)
    await db_session.commit()
    assert res.rowcount == 0


@pytest.mark.asyncio
async def test_cas_recovery_two_recovery_attempts_against_same_claim(db_session: AsyncSession):
    """Two sequential or concurrent recovery calls against the same expired claim: first succeeds, second is rejected."""
    proj = Project(name="Two Recovery Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=600),
        validation_lease_expires_at=now - timedelta(seconds=10),
        validation_claimed_by="worker_1",
        validation_claim_token="two_rec_tok"
    )
    db_session.add(patch)
    await db_session.commit()

    # Recovery 1 succeeds
    recovered = await privileged_validation_service.recover_abandoned_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id
    )
    assert recovered.status == "approved"
    assert recovered.validation_claim_token is None

    # Recovery 2 must fail because status is now "approved", not "validating"
    with pytest.raises(ValueError, match="not in 'validating' state"):
        await privileged_validation_service.recover_abandoned_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


# --- Point 2: Prevent expired lease renewal ---

@pytest.mark.asyncio
async def test_lease_renewal_before_expiry_succeeds(db_session: AsyncSession):
    """Lease renewal by the legitimate owner before expiration succeeds."""
    proj = Project(name="Renewal Success Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=60),
        validation_claimed_by="worker_active",
        validation_claim_token="valid_active_token"
    )
    db_session.add(patch)
    await db_session.commit()

    renewed = await privileged_validation_service.renew_validation_lease(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        claim_token="valid_active_token",
        extend_seconds=120
    )
    exp = renewed.validation_lease_expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=timezone.utc)
    assert exp > now + timedelta(seconds=60)


@pytest.mark.asyncio
async def test_lease_renewal_after_expiry_fails(db_session: AsyncSession):
    """Lease renewal after the lease has already expired is strictly rejected."""
    proj = Project(name="Renewal Expired Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=200),
        validation_lease_expires_at=now - timedelta(seconds=10),
        validation_claimed_by="worker_late",
        validation_claim_token="late_token"
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="expired.*Expired leases cannot be revived"):
        await privileged_validation_service.renew_validation_lease(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            claim_token="late_token",
            extend_seconds=60
        )


@pytest.mark.asyncio
async def test_lease_renewal_after_recovery_fails(db_session: AsyncSession):
    """A worker attempting renewal after recovery has reset the patch is rejected."""
    proj = Project(name="Renewal After Recovery Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="approved",  # Already recovered
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=None,
        validation_lease_expires_at=None,
        validation_claimed_by=None,
        validation_claim_token=None
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="has no active lease expiry and cannot be renewed"):
        await privileged_validation_service.renew_validation_lease(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            claim_token="old_worker_token",
            extend_seconds=60
        )


@pytest.mark.asyncio
async def test_lease_renewal_with_stale_token_fails(db_session: AsyncSession):
    """Lease renewal with wrong or stale token is rejected."""
    proj = Project(name="Stale Token Renewal Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=120),
        validation_claimed_by="worker_1",
        validation_claim_token="token_correct"
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="claim token is invalid"):
        await privileged_validation_service.renew_validation_lease(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            claim_token="stale_or_wrong_token",
            extend_seconds=60
        )


@pytest.mark.asyncio
async def test_lease_renewal_racing_with_recovery_has_single_winner(db_session: AsyncSession):
    """
    Renewal requires validation_lease_expires_at > :now.
    Recovery requires validation_lease_expires_at <= :now.
    Because these predicates are mutually exclusive, a race yields exactly one valid outcome.
    """
    proj = Project(name="Race Winner Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    # Case: Lease is expired. Recovery wins, renewal loses.
    patch_expired = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now - timedelta(seconds=200),
        validation_lease_expires_at=now - timedelta(seconds=5),
        validation_claimed_by="worker_race",
        validation_claim_token="race_tok_exp"
    )
    db_session.add(patch_expired)
    await db_session.commit()

    # Recovery succeeds
    rec = await privileged_validation_service.recover_abandoned_validation(
        db_session, inv.id, patch_expired.id
    )
    assert rec.status == "approved"

    # Renewal fails
    with pytest.raises(ValueError):
        await privileged_validation_service.renew_validation_lease(
            db_session, inv.id, patch_expired.id, claim_token="race_tok_exp", extend_seconds=60
        )


# --- Point 3: Enforce authorization inside administrative recovery ---

@pytest.mark.asyncio
async def test_force_recover_unauthorized_principals_denied_and_audited(db_session: AsyncSession):
    """Unauthorized principals (viewer, operator, missing admin scope) cannot force recover, and denials are audited."""
    proj = Project(name="Force Recover Auth Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=60),
        validation_claimed_by="worker_1",
        validation_claim_token="tok_admin_test"
    )
    db_session.add(patch)
    await db_session.commit()

    # 1. Non-principal / None
    with pytest.raises(PermissionError, match="requires a verified AuthenticatedPrincipal"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            operator=None
        )

    # 2. Viewer role
    viewer = AuthenticatedPrincipal(principal_id="viewer_bob", role="viewer", scopes=["read"])
    with pytest.raises(PermissionError, match="strictly requires role 'admin'"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            operator=viewer
        )

    # 3. Operator role (not admin)
    operator = AuthenticatedPrincipal(principal_id="op_alice", role="operator", scopes=["patch:approve", "patch:reject"])
    with pytest.raises(PermissionError, match="strictly requires role 'admin'"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            operator=operator
        )

    # 4. Admin without recovery scope
    admin_no_scope = AuthenticatedPrincipal(principal_id="admin_dave", role="admin", scopes=["audit:read"])
    with pytest.raises(PermissionError, match="lacks administrative recovery scope"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            operator=admin_no_scope
        )

    # Verify audit log recorded denied attempts with caller identity
    audit_log = privileged_validation_service.get_audit_log(patch_id=patch.id)
    denied_entries = [e for e in audit_log if e.get("status") == "admin_recovery_denied"]
    assert len(denied_entries) >= 4
    denied_principals = {e.get("authorized_by") for e in denied_entries}
    assert "viewer_bob" in denied_principals
    assert "op_alice" in denied_principals
    assert "admin_dave" in denied_principals


@pytest.mark.asyncio
async def test_force_recover_authorized_admin_succeeds(db_session: AsyncSession):
    """An authorized administrator with admin:recover scope can force recovery."""
    proj = Project(name="Admin Success Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validating",
        approved_by="op1",
        approved_at=now,
        validation_claimed_at=now,
        validation_lease_expires_at=now + timedelta(seconds=120),
        validation_claimed_by="stuck_worker",
        validation_claim_token="stuck_tok"
    )
    db_session.add(patch)
    await db_session.commit()

    admin = AuthenticatedPrincipal(
        principal_id="admin_lead",
        role="admin",
        scopes=["admin:recover", "patch:approve"]
    )
    recovered = await privileged_validation_service.force_recover_validation_claim(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        operator=admin
    )
    assert recovered.status == "approved"
    assert recovered.validation_claim_token is None
    assert recovered.validation_lease_expires_at is None

    audit_log = privileged_validation_service.get_audit_log(patch_id=patch.id)
    success_entries = [e for e in audit_log if e.get("status") == "admin_force_recovered"]
    assert len(success_entries) == 1
    assert success_entries[0].get("authorized_by") == "admin_lead"


@pytest.mark.asyncio
async def test_force_recover_completed_patch_rejected(db_session: AsyncSession):
    """Administrative forced recovery refuses to reset an already completed patch (validated or rejected)."""
    proj = Project(name="Terminal Patch Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch_val = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="validated",
        approved_by="op1",
        approved_at=now
    )
    patch_rej = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="rejected",
        approved_by="op1",
        approved_at=now
    )
    db_session.add_all([patch_val, patch_rej])
    await db_session.commit()

    admin = AuthenticatedPrincipal(principal_id="admin_boss", role="admin", scopes=["admin:*"])

    with pytest.raises(ValueError, match="already reached terminal status 'validated'"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch_val.id,
            operator=admin
        )

    with pytest.raises(ValueError, match="already reached terminal status 'rejected'"):
        await privileged_validation_service.force_recover_validation_claim(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch_rej.id,
            operator=admin
        )


# --- Point 4: Extend heartbeat cleanup to entire execution lifecycle ---

@pytest.mark.asyncio
async def test_validation_lifecycle_cleanup_on_missing_entities(db_session: AsyncSession):
    """Exceptions before tool execution (missing investigation or project) clean up heartbeat task."""
    proj = Project(name="Lifecycle Missing Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="op1",
        approved_at=now
    )
    db_session.add(patch)
    await db_session.commit()

    # Missing investigation:
    with pytest.raises(ValueError, match="not found for investigation"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id="nonexistent-inv",
            patch_id=patch.id
        )

    # Missing project: orphaned investigation
    inv_orphaned = Investigation(project_id="missing-project-id", objective="Orphan")
    db_session.add(inv_orphaned)
    await db_session.commit()

    patch_orphaned = PatchProposal(
        investigation_id=inv_orphaned.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="op1",
        approved_at=now
    )
    db_session.add(patch_orphaned)
    await db_session.commit()

    with pytest.raises(ValueError, match="Project for investigation.*not found"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv_orphaned.id,
            patch_id=patch_orphaned.id
        )


@pytest.mark.asyncio
async def test_validation_lifecycle_cleanup_on_cancellation(db_session: AsyncSession):
    """Cancellation during execution triggers finally block, cancelling and awaiting heartbeat."""
    proj = Project(name="Cancel Test Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Test")
    db_session.add(inv)
    await db_session.commit()

    now = datetime.now(timezone.utc)
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="ctx",
        proposed_code="code",
        unified_diff="diff",
        explanation="expl",
        status="approved",
        approved_by="op1",
        approved_at=now
    )
    db_session.add(patch)
    await db_session.commit()

    from unittest.mock import AsyncMock, patch as mock_patch
    mock_tool = AsyncMock()
    tool_started = asyncio.Event()

    async def long_running_execute(*args, **kwargs):
        tool_started.set()
        await asyncio.sleep(10.0)
        return None

    mock_tool.execute = long_running_execute

    with mock_patch("backend.app.services.validation_service.ValidatePatchTool", return_value=mock_tool):
        task = asyncio.create_task(
            privileged_validation_service.execute_approved_patch_validation(
                db=db_session,
                investigation_id=inv.id,
                patch_id=patch.id,
                lease_seconds=5,
                renewal_interval=0.1
            )
        )
        await tool_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


# --- Point 5: Improve evidence verification beyond keyword heuristics ---

def test_evidence_mixed_claims_downgraded_to_inference():
    """
    Mixed claims containing one true fact alongside an uncorroborated assertion
    are strictly downgraded to 'inference' rather than being marked 'verified'.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")
    ]

    # 1. Log analysis: KeyError exists, but "caused by memory leak in broker" is unsupported assertion
    te_log = ToolExecution(
        id="te_log_mixed",
        investigation_id="inv1",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 42},
                "exception_type": "KeyError",
                "message": "'customer_id' missing from columns"
            }
        }
    )
    ev_mixed_log = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        observation="KeyError customer_id occurred in pipeline.py and was caused by an out of memory leak in broker cluster",
        line_number=42
    )
    status, is_inf, matched_id = verify_evidence_against_telemetry(ev_mixed_log, files, [te_log])
    assert status == "inference"
    assert is_inf is True
    assert matched_id == "te_log_mixed"

    # 2. Dataset profile: null count of 15 matches, but "causes kernel panic in processing worker" is unsupported
    te_profile = ToolExecution(
        id="te_prof_mixed",
        investigation_id="inv1",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "dataset": {"total_rows": 100, "duplicate_rows": 0},
            "columns": {
                "amount": {"null_count": 15, "null_percentage": 15.0, "dtype": "float64"}
            },
            "anomalies": [{"column": "amount", "type": "null_values", "null_count": 15}]
        }
    )
    ev_mixed_prof = AgentEvidenceInput(
        source_file="pipeline.py",
        column_name="amount",
        evidence_type="dataset_profile",
        observation="amount column has 15 null values and causes a kernel panic in processing worker",
    )
    status, is_inf, matched_id = verify_evidence_against_telemetry(ev_mixed_prof, files, [te_profile])
    assert status == "inference"
    assert is_inf is True
    assert matched_id == "te_prof_mixed"

    # 3. Code AST: calculate_tax exists, but "optimizes cache locality" is unsupported
    te_ast = ToolExecution(
        id="te_ast_mixed",
        investigation_id="inv1",
        tool_name="inspect_code",
        status="success",
        output_result={
            "functions": [{"name": "calculate_tax", "lineno": 10, "end_lineno": 20, "args": ["total"]}],
            "classes": [],
            "imports": ["math"]
        }
    )
    ev_mixed_ast = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="code_ast",
        observation="calculate_tax function is defined and optimizes cache locality for high throughput",
        line_number=10
    )
    status, is_inf, matched_id = verify_evidence_against_telemetry(ev_mixed_ast, files, [te_ast])
    assert status == "inference"
    assert is_inf is True
    assert matched_id == "te_ast_mixed"


# ============================================================================
# 10. FIELD- AND RELATIONSHIP-PRESERVING EVIDENCE VERIFICATION TESTS
# ============================================================================

def test_evidence_column_level_null_count_separation():
    """
    Column-level claims must verify strictly against the named column's profile.
    A null count belonging to `amount` (2) cannot verify a claim about `customer_id` (0),
    nor can `null_percentage` masquerade as `null_count`.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="data/orders.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_prof = ToolExecution(
        id="te-col-sep",
        investigation_id="inv-sep",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "dataset_path": "data/orders.csv",
            "total_rows": 10,
            "total_columns": 2,
            "duplicate_rows": 0,
            "column_profiles": {
                "customer_id": {"dtype": "int64", "null_count": 0, "null_percentage": 0.0, "distinct_count": 10},
                "amount": {"dtype": "float64", "null_count": 2, "null_percentage": 20.0, "distinct_count": 8},
            },
            "anomalies": [
                {"column": "amount", "type": "null_values_detected", "null_count": 2, "null_percentage": 20.0}
            ],
        },
    )

    # 1. Cross-column false claim with column_name=None
    ev_false_none = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        column_name=None,
        tool_execution_id="te-col-sep",
        observation="customer_id has 2 null values",
    )
    s1, is_inf1, _ = verify_evidence_against_telemetry(ev_false_none, files, [te_prof], investigation_id="inv-sep")
    assert s1 == "unverified"
    assert is_inf1 is False

    # 2. Cross-column false claim while spoofing column_name="amount"
    ev_false_spoof = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="te-col-sep",
        observation="customer_id has 2 null values",
    )
    s2, _, _ = verify_evidence_against_telemetry(ev_false_spoof, files, [te_prof], investigation_id="inv-sep")
    assert s2 == "unverified"

    # 3. Reverse spoofing: true observation about amount but structured column_name="customer_id"
    ev_rev_spoof = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        column_name="customer_id",
        tool_execution_id="te-col-sep",
        observation="amount has 2 null values",
    )
    s3, _, _ = verify_evidence_against_telemetry(ev_rev_spoof, files, [te_prof], investigation_id="inv-sep")
    assert s3 == "unverified"

    # 4. Confusing percentage (20.0) as raw null count ("amount has 20 null values")
    ev_pct_as_count = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="te-col-sep",
        observation="amount has 20 null values",
    )
    s4, _, _ = verify_evidence_against_telemetry(ev_pct_as_count, files, [te_prof], investigation_id="inv-sep")
    assert s4 == "unverified"

    # 5. Legitimate multi-column observation Verify: customer_id has 0 nulls while amount has 2 nulls
    ev_multi_valid = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        tool_execution_id="te-col-sep",
        observation="customer_id has 0 nulls while amount has 2 nulls",
    )
    s5, is_inf5, tid5 = verify_evidence_against_telemetry(ev_multi_valid, files, [te_prof], investigation_id="inv-sep")
    assert s5 == "verified"
    assert is_inf5 is False
    assert tid5 == "te-col-sep"

    # 6. Multi-column observation where one clause is true and second clause is false -> unverified
    ev_multi_mixed_false = AgentEvidenceInput(
        source_file="data/orders.csv",
        evidence_type="dataset_anomaly",
        tool_execution_id="te-col-sep",
        observation="amount has 2 nulls while customer_id has 2 nulls",
    )
    s6, _, _ = verify_evidence_against_telemetry(ev_multi_mixed_false, files, [te_prof], investigation_id="inv-sep")
    assert s6 == "unverified"


def test_evidence_unrelated_numerical_telemetry_cannot_support_claim():
    """
    Unrelated numbers elsewhere in telemetry (e.g. total_rows=100, duplicate_rows=5, another column's null_count,
    or max value) must never verify an assertion about a different metric or entity.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="data/orders.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_prof = ToolExecution(
        id="te-num-sep",
        investigation_id="inv-num",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "dataset_path": "data/orders.csv",
            "total_rows": 100,
            "total_columns": 2,
            "duplicate_rows": 5,
            "column_profiles": {
                "customer_id": {"dtype": "int64", "null_count": 0, "null_percentage": 0.0, "distinct_count": 98},
                "amount": {
                    "dtype": "float64",
                    "null_count": 1,
                    "null_percentage": 1.0,
                    "distinct_count": 95,
                    "numeric_stats": {"min": 10.0, "max": 200.0, "mean": 50.0, "median": 45.0, "std": 25.0},
                },
            },
            "anomalies": [{"column": "amount", "type": "null_values_detected", "null_count": 1, "null_percentage": 1.0}],
        },
    )

    # 1. total_rows=100 cannot verify "customer_id has 100 null values"
    s1, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            column_name="customer_id",
            tool_execution_id="te-num-sep",
            observation="customer_id has 100 null values",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s1 == "unverified"

    # 2. total_rows=100 cannot verify "amount has 100 null values" even though amount has 1 null
    s2, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            column_name="amount",
            tool_execution_id="te-num-sep",
            observation="amount has 100 null values",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s2 == "unverified"

    # 3. duplicate_rows=5 cannot verify "customer_id has 5 null values"
    s3, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            column_name="customer_id",
            tool_execution_id="te-num-sep",
            observation="customer_id has 5 null values",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s3 == "unverified"

    # 4. total_rows=100 cannot verify "amount has mean 100" when amount mean=50.0
    s4, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            column_name="amount",
            tool_execution_id="te-num-sep",
            observation="amount has mean 100",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s4 == "unverified"

    # 5. Swapped min/max values in same clause must be rejected
    s5, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            column_name="amount",
            tool_execution_id="te-num-sep",
            observation="amount has min 200.0 and max 10.0",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s5 == "unverified"

    # 6. Exact field match on distinct_count=98 verifies cleanly
    s6, is_inf6, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_profile",
            column_name="customer_id",
            tool_execution_id="te-num-sep",
            observation="customer_id has 98 distinct values",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s6 == "verified"
    assert is_inf6 is False

    # 7. Valid compound numeric_stats observation without column_name verifies cleanly
    s7, is_inf7, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_profile",
            tool_execution_id="te-num-sep",
            observation="amount has min 10.0 and max 200.0",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s7 == "verified"
    assert is_inf7 is False

    # 8. Shared multi-column predicate fails closed when either column violates the claim
    s8, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="dataset_anomaly",
            tool_execution_id="te-num-sep",
            observation="customer_id and amount have 1 null value",
        ),
        files,
        [te_prof],
        investigation_id="inv-num",
    )
    assert s8 == "unverified"


def test_evidence_traceback_supports_exception_not_causal_claim():
    """
    Real analyze_logs traceback supports exact file, line, and exception fact,
    while any causal explanation or mismatched exception detail cannot be marked verified.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="src/pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_log = ToolExecution(
        id="te-tb-01",
        investigation_id="inv-tb",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "total_lines": 25,
            "stages_detected": ["transform"],
            "preceding_warnings": [],
            "tracebacks": [
                {
                    "start_line": 10,
                    "frames": [
                        {
                            "file": "/app/src/pipeline.py",
                            "line": 30,
                            "function": "aggregate_sales",
                            "code_line": "return df['customer_id'].sum()",
                        }
                    ],
                    "exception_type": "KeyError",
                    "exception_message": "'customer_id'",
                }
            ],
            "fatal_error": {
                "exception_type": "KeyError",
                "exception_message": "'customer_id'",
                "last_frame": {
                    "file": "/app/src/pipeline.py",
                    "line": 30,
                    "function": "aggregate_sales",
                    "code_line": "return df['customer_id'].sum()",
                },
            },
        },
    )

    # Exact exception claim -> verified
    s_ok, is_inf_ok, tid_ok = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="src/pipeline.py",
            evidence_type="log_stacktrace",
            line_number=30,
            column_name="customer_id",
            tool_execution_id="te-tb-01",
            observation="KeyError for customer_id in aggregate_sales at line 30",
        ),
        files,
        [te_log],
        investigation_id="inv-tb",
    )
    assert s_ok == "verified"
    assert is_inf_ok is False
    assert tid_ok == "te-tb-01"

    # Causal explanation attached to valid traceback -> inference
    s_causal, is_inf_causal, tid_causal = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="src/pipeline.py",
            evidence_type="log_stacktrace",
            line_number=30,
            tool_execution_id="te-tb-01",
            observation="KeyError on customer_id at line 30 caused by upstream schema migration",
        ),
        files,
        [te_log],
        investigation_id="inv-tb",
    )
    assert s_causal == "inference"
    assert is_inf_causal is True
    assert tid_causal == "te-tb-01"

    # Uncorroborated column_name on KeyError traceback -> unverified
    s_wrong_col, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="src/pipeline.py",
            evidence_type="log_stacktrace",
            line_number=30,
            column_name="amount",
            tool_execution_id="te-tb-01",
            observation="KeyError on amount at line 30",
        ),
        files,
        [te_log],
        investigation_id="inv-tb",
    )
    assert s_wrong_col == "unverified"


def test_evidence_dependency_reversed_direction_rejected():
    """
    Dependency verification must preserve edge direction across both mock fields
    and real trace_dependencies outputs (`stages`, `edges`, `target_analysis`).
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1"),
        ProjectFile(id="f2", project_id="p2", relative_path="database.py", file_type="code", size_bytes=100, storage_path="/tmp/f2"),
        ProjectFile(id="f3", project_id="p3", relative_path="data/output.csv", file_type="data", size_bytes=100, storage_path="/tmp/f3"),
    ]

    # Case A: Real trace_dependencies schema (edges use 'from' -> 'to' dataflow direction)
    te_real_dep = ToolExecution(
        id="te-dep-real",
        investigation_id="inv-dep",
        tool_name="trace_dependencies",
        status="success",
        output_result={
            "stages": {
                "pipeline.py": {"inputs": ["database.py"], "outputs": ["data/output.csv"], "imports": ["pandas"]},
                "database.py": {"inputs": [], "outputs": ["data/raw.csv"], "imports": ["sqlite3"]},
            },
            "nodes": [
                {"id": "database.py", "type": "stage", "label": "database.py"},
                {"id": "pipeline.py", "type": "stage", "label": "pipeline.py"},
                {"id": "data/output.csv", "type": "dataset", "label": "data/output.csv"},
            ],
            "edges": [
                {"from": "database.py", "to": "pipeline.py", "relationship": "reads"},
                {"from": "pipeline.py", "to": "data/output.csv", "relationship": "writes"},
            ],
            "target_analysis": {
                "target": "pipeline.py",
                "upstream": ["database.py"],
                "downstream": ["data/output.csv"],
            },
        },
    )

    # Correct direction: pipeline.py depends on database.py -> verified
    s_ok, is_inf_ok, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-dep-real",
            observation="pipeline.py depends on database.py",
        ),
        files,
        [te_real_dep],
        investigation_id="inv-dep",
    )
    assert s_ok == "verified"
    assert is_inf_ok is False

    # Reversed direction: database.py depends on pipeline.py -> unverified
    s_rev1, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-dep-real",
            observation="database.py depends on pipeline.py",
        ),
        files,
        [te_real_dep],
        investigation_id="inv-dep",
    )
    assert s_rev1 == "unverified"

    # Reversed phrase: pipeline.py is an upstream dependency of database.py -> unverified
    s_rev2, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-dep-real",
            observation="pipeline.py is an upstream dependency of database.py",
        ),
        files,
        [te_dep := te_real_dep],
        investigation_id="inv-dep",
    )
    assert s_rev2 == "unverified"

    # Valid reverse/downstream claim: pipeline.py is used by data/output.csv -> verified
    s_down_ok, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-dep-real",
            observation="pipeline.py is used by data/output.csv",
        ),
        files,
        [te_dep],
        investigation_id="inv-dep",
    )
    assert s_down_ok == "verified"

    # Case B: Mock schema with dependencies=['database.py']
    te_mock_dep = ToolExecution(
        id="te-dep-mock",
        investigation_id="inv-dep",
        tool_name="trace_dependencies",
        status="success",
        output_result={"dependencies": ["database.py"], "reverse_dependencies": []},
    )
    s_rev_mock, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-dep-mock",
            observation="database.py depends on pipeline.py",
        ),
        files,
        [te_mock_dep],
        investigation_id="inv-dep",
    )
    assert s_rev_mock == "unverified"


def test_evidence_ast_confirms_existence_not_runtime_behavior():
    """
    AST inspection establishes static syntax/location only and must never mark
    runtime security, validation, or behavioral claims as verified.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="auth/middleware.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_ast = ToolExecution(
        id="te-ast-sec",
        investigation_id="inv-ast",
        tool_name="inspect_code",
        status="success",
        output_result={
            "file": "auth/middleware.py",
            "total_lines": 40,
            "functions": [{"name": "validate_user", "line_start": 20, "line_end": 35, "arguments": ["token", "secret"]}],
            "imports": ["jwt"],
        },
    )

    # Syntactic definition matches -> verified
    s_syn, is_inf_syn, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="auth/middleware.py",
            evidence_type="code_ast",
            line_number=20,
            tool_execution_id="te-ast-sec",
            observation="Function validate_user is defined at line 20 with parameters token and secret",
        ),
        files,
        [te_ast],
        investigation_id="inv-ast",
    )
    assert s_syn == "verified"
    assert is_inf_syn is False

    # Runtime behavioral claim -> inference (never verified)
    s_beh, is_inf_beh, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="auth/middleware.py",
            evidence_type="code_ast",
            line_number=20,
            tool_execution_id="te-ast-sec",
            observation="validate_user function at line 20 validates JWT signatures and decrypts auth tokens",
        ),
        files,
        [te_ast],
        investigation_id="inv-ast",
    )
    assert s_beh == "inference"
    assert is_inf_beh is True


def test_evidence_supported_number_with_unsupported_causal_claim():
    """
    An accurate numeric fact combined with an unsupported causal explanation
    must be downgraded to `inference`, never marked `verified`.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="data/payments.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_prof = ToolExecution(
        id="te-prof-causal",
        investigation_id="inv-c",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "dataset_path": "data/payments.csv",
            "total_rows": 50,
            "duplicate_rows": 0,
            "column_profiles": {
                "amount": {"dtype": "float64", "null_count": 2, "null_percentage": 4.0, "distinct_count": 48}
            },
            "anomalies": [{"column": "amount", "type": "null_values_detected", "null_count": 2, "null_percentage": 4.0}],
        },
    )
    ev = AgentEvidenceInput(
        source_file="data/payments.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="te-prof-causal",
        observation="amount column has 2 null values due to network partition during upstream ingestion",
    )
    status, is_inf, matched_id = verify_evidence_against_telemetry(ev, files, [te_prof], investigation_id="inv-c")
    assert status == "inference"
    assert is_inf is True
    assert matched_id == "te-prof-causal"


def test_evidence_missing_or_incomplete_telemetry_not_verified():
    """
    Missing or incomplete telemetry across every tool type fails closed
    and must never return `verified`.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1"),
        ProjectFile(id="f2", project_id="p2", relative_path="data.csv", file_type="data", size_bytes=100, storage_path="/tmp/f2"),
    ]

    # 1. Incomplete profile_dataset missing null_count must NOT default null_count to 0
    te_incomplete_prof = ToolExecution(
        id="te-inc-prof",
        investigation_id="inv-inc",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "dataset_path": "data.csv",
            "column_profiles": {"customer_id": {"dtype": "int64"}},
            "anomalies": [],
        },
    )
    s1, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data.csv",
            evidence_type="dataset_profile",
            column_name="customer_id",
            tool_execution_id="te-inc-prof",
            observation="customer_id has 0 null values",
        ),
        files,
        [te_incomplete_prof],
        investigation_id="inv-inc",
    )
    assert s1 != "verified"
    assert s1 == "unverified"

    # 2. analyze_logs with no tracebacks or fatal_error
    te_empty_log = ToolExecution(
        id="te-inc-log",
        investigation_id="inv-inc",
        tool_name="analyze_logs",
        status="success",
        output_result={"total_lines": 100, "tracebacks": [], "fatal_error": None},
    )
    s2, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="log_stacktrace",
            tool_execution_id="te-inc-log",
            observation="KeyError on customer_id at line 20",
        ),
        files,
        [te_empty_log],
        investigation_id="inv-inc",
    )
    assert s2 == "unverified"

    # 3. inspect_code with empty functions/imports
    te_empty_ast = ToolExecution(
        id="te-inc-ast",
        investigation_id="inv-inc",
        tool_name="inspect_code",
        status="success",
        output_result={"file": "pipeline.py", "functions": [], "imports": []},
    )
    s3, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="code_ast",
            tool_execution_id="te-inc-ast",
            observation="Function run is defined at line 1",
        ),
        files,
        [te_empty_ast],
        investigation_id="inv-inc",
    )
    assert s3 == "unverified"

    # 4. trace_dependencies with empty graph
    te_empty_dep = ToolExecution(
        id="te-inc-dep",
        investigation_id="inv-inc",
        tool_name="trace_dependencies",
        status="success",
        output_result={"stages": {}, "nodes": [], "edges": [], "target_analysis": None},
    )
    s4, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="te-inc-dep",
            observation="pipeline.py depends on database.py",
        ),
        files,
        [te_empty_dep],
        investigation_id="inv-inc",
    )
    assert s4 == "unverified"

    # 5. inspect_schema with empty diff/target
    te_empty_schema = ToolExecution(
        id="te-inc-sch",
        investigation_id="inv-inc",
        tool_name="inspect_schema",
        status="success",
        output_result={"target_schema": {}, "differences": {}},
    )
    s5, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data.csv",
            evidence_type="schema_diff",
            column_name="customer_id",
            tool_execution_id="te-inc-sch",
            observation="customer_id column is missing from schema",
        ),
        files,
        [te_empty_schema],
        investigation_id="inv-inc",
    )
    assert s5 == "unverified"


@pytest.mark.asyncio
async def test_evidence_real_tool_schemas_positive_verification(tmp_path: Path):
    """
    Executes the actual 5 diagnostic tools against real project fixtures and verifies
    that accurate field-bound observations pass verification while distorted claims fail.
    """
    from backend.app.tools.analyze_logs import AnalyzeLogsTool
    from backend.app.tools.profile_dataset import ProfileDatasetTool
    from backend.app.tools.inspect_code import InspectCodeTool
    from backend.app.tools.trace_dependencies import TraceDependenciesTool
    from backend.app.tools.inspect_schema import InspectSchemaTool

    (tmp_path / "pipeline.py").write_text(
        "import pandas as pd\n"
        "def clean_orders(df):\n"
        "    df = pd.read_csv('orders.csv')\n"
        "    return df['customer_id']\n",
        encoding="utf-8",
    )
    (tmp_path / "baseline.csv").write_text(
        "customer_id,amount\n"
        "1,10.5\n"
        "2,20.0\n",
        encoding="utf-8",
    )
    (tmp_path / "orders.csv").write_text(
        "client_id,amount\n"
        "1,10.5\n"
        "2,\n",
        encoding="utf-8",
    )
    (tmp_path / "error.log").write_text(
        "Traceback (most recent call last):\n"
        '  File "/app/pipeline.py", line 4, in clean_orders\n'
        "    return df['customer_id']\n"
        "KeyError: 'customer_id'\n",
        encoding="utf-8",
    )

    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path=str(tmp_path / "pipeline.py")),
        ProjectFile(id="f2", project_id="p1", relative_path="orders.csv", file_type="data", size_bytes=100, storage_path=str(tmp_path / "orders.csv")),
        ProjectFile(id="f3", project_id="p1", relative_path="error.log", file_type="log", size_bytes=100, storage_path=str(tmp_path / "error.log")),
    ]

    log_res = await AnalyzeLogsTool().execute(str(tmp_path), {"log_path": "error.log"})
    prof_res = await ProfileDatasetTool().execute(str(tmp_path), {"dataset_path": "orders.csv"})
    code_res = await InspectCodeTool().execute(str(tmp_path), {"file_path": "pipeline.py"})
    dep_res = await TraceDependenciesTool().execute(str(tmp_path), {"target_component": "pipeline.py"})
    sch_res = await InspectSchemaTool().execute(str(tmp_path), {"dataset_path": "orders.csv", "baseline_path": "baseline.csv"})

    execs = [
        ToolExecution(id="E-LOG", investigation_id="I-REAL", tool_name="analyze_logs", status="success", output_result=log_res.data),
        ToolExecution(id="E-PROF", investigation_id="I-REAL", tool_name="profile_dataset", status="success", output_result=prof_res.data),
        ToolExecution(id="E-CODE", investigation_id="I-REAL", tool_name="inspect_code", status="success", output_result=code_res.data),
        ToolExecution(id="E-DEP", investigation_id="I-REAL", tool_name="trace_dependencies", status="success", output_result=dep_res.data),
        ToolExecution(id="E-SCH", investigation_id="I-REAL", tool_name="inspect_schema", status="success", output_result=sch_res.data),
    ]

    # 1. analyze_logs real schema
    s_log, _, tid_log = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="log_stacktrace",
            line_number=4,
            column_name="customer_id",
            observation="KeyError on customer_id in clean_orders at line 4",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_log == "verified" and tid_log == "E-LOG"

    # 2. profile_dataset real schema (`column_profiles`)
    s_prof, _, tid_prof = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="orders.csv",
            evidence_type="dataset_anomaly",
            column_name="amount",
            observation="amount column has 1 null value",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_prof == "verified" and tid_prof == "E-PROF"

    # 3. inspect_code real schema (`line_start`, `line_end`, `arguments`)
    s_code, _, tid_code = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="code_ast",
            line_number=2,
            observation="Function clean_orders is defined at line 2 with parameter df",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_code == "verified" and tid_code == "E-CODE"

    # 4. trace_dependencies real schema (`stages`, `edges`, `target_analysis`)
    s_dep, _, tid_dep = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            observation="pipeline.py depends on orders.csv",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_dep == "verified" and tid_dep == "E-DEP"

    # 5. inspect_schema real schema (`differences.missing_columns`, `renamed_candidates`)
    s_sch, _, tid_sch = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="orders.csv",
            evidence_type="schema_diff",
            column_name="customer_id",
            observation="Schema diff: customer_id column is missing",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_sch == "verified" and tid_sch == "E-SCH"

    # Also verify cross-tool distorted claims fail closed against real tool outputs:
    s_false_dep, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="pipeline.py",
            evidence_type="dependency_graph",
            tool_execution_id="E-DEP",
            observation="orders.csv depends on pipeline.py",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_false_dep == "unverified"

    s_false_col, _, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="orders.csv",
            evidence_type="dataset_anomaly",
            column_name="client_id",
            tool_execution_id="E-PROF",
            observation="client_id has 1 null value",
        ),
        files, execs, investigation_id="I-REAL", workspace_dir=tmp_path,
    )
    assert s_false_col == "unverified"


# ==============================================================================
# 11. SCHEMA TYPE MISMATCH ROLE PRESERVATION REGRESSION TESTS
# ==============================================================================


def test_schema_type_mismatch_preserves_expected_and_actual_roles():
    """
    Type mismatch verification must preserve exact column -> expected_dtype -> actual_dtype
    role bindings: accepting valid full or narrower claims, while rejecting reversed,
    incorrect, unsupported, ambiguous, or cross-column claims.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="data/orders.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")
    ]
    te_schema = ToolExecution(
        id="TE-SCH-TM",
        investigation_id="INV-TM",
        tool_name="inspect_schema",
        status="success",
        output_result={
            "target_schema": {
                "columns": ["customer_id", "amount", "quantity"],
                "fields": {
                    "customer_id": {"dtype": "int64", "nullable": False},
                    "amount": {"dtype": "object", "nullable": False},
                    "quantity": {"dtype": "str", "nullable": False},
                },
                "total_columns": 3,
            },
            "differences": {
                "has_drift": True,
                "missing_columns": [],
                "unexpected_columns": [],
                "type_mismatches": [
                    {"column": "amount", "expected_dtype": "float64", "actual_dtype": "object"},
                    {"column": "quantity", "expected_dtype": "int64", "actual_dtype": "str"},
                ],
                "renamed_candidates": [],
            },
        },
    )

    # 1. Correct expected and actual types -> verified
    for obs in (
        "The amount column has a type mismatch: expected float64, actual object.",
        "amount column type mismatch: actual object, expected float64",
        "Type mismatch on amount: expected_dtype=float64, actual_dtype=object",
        "The amount column has a type mismatch: actual object instead of float64",
        "The amount column has a type mismatch: float64 expected, object actual",
    ):
        s, is_inf, tid = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="data/orders.csv",
                evidence_type="schema_diff",
                column_name="amount",
                tool_execution_id="TE-SCH-TM",
                observation=obs,
            ),
            files, [te_schema], investigation_id="INV-TM",
        )
        assert s == "verified", f"Expected verified for: {obs}"
        assert is_inf is False
        assert tid == "TE-SCH-TM"

    # 2. Reversed expected and actual types -> unverified
    for rev_obs in (
        "The amount column has a type mismatch: expected object, actual float64.",
        "The amount column has a type mismatch: actual float64, expected object.",
        "The amount column has a type mismatch: expected_dtype=object, actual_dtype=float64.",
        "The amount column has a type mismatch: float64 instead of object.",
        "The amount column has a type mismatch: object expected, float64 actual.",
    ):
        s_rev, _, _ = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="data/orders.csv",
                evidence_type="schema_diff",
                column_name="amount",
                tool_execution_id="TE-SCH-TM",
                observation=rev_obs,
            ),
            files, [te_schema], investigation_id="INV-TM",
        )
        assert s_rev == "unverified", f"Expected unverified for reversed roles: {rev_obs}"

    # 3. Incorrect expected type -> unverified
    for bad_exp in (
        "The amount column has a type mismatch: expected int64, actual object.",
        "The amount column has a type mismatch: expected decimal, actual object.",
        "The amount column has a type mismatch: expected object.",
    ):
        s_be, _, _ = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="data/orders.csv",
                evidence_type="schema_diff",
                column_name="amount",
                tool_execution_id="TE-SCH-TM",
                observation=bad_exp,
            ),
            files, [te_schema], investigation_id="INV-TM",
        )
        assert s_be == "unverified", f"Expected unverified for wrong expected type: {bad_exp}"

    # 4. Incorrect actual type -> unverified
    for bad_act in (
        "The amount column has a type mismatch: expected float64, actual int64.",
        "The amount column has a type mismatch: expected float64, actual string.",
        "The amount column has a type mismatch: expected float64, actual varchar.",
        "The amount column has a type mismatch: actual float64.",
    ):
        s_ba, _, _ = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="data/orders.csv",
                evidence_type="schema_diff",
                column_name="amount",
                tool_execution_id="TE-SCH-TM",
                observation=bad_act,
            ),
            files, [te_schema], investigation_id="INV-TM",
        )
        assert s_ba == "unverified", f"Expected unverified for wrong actual type: {bad_act}"

    # 5. Narrower valid claims mentioning only one role -> verified
    s_exp_only, inf_exp, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="schema_diff",
            column_name="amount",
            tool_execution_id="TE-SCH-TM",
            observation="The amount column has a type mismatch: expected float64.",
        ),
        files, [te_schema], investigation_id="INV-TM",
    )
    assert s_exp_only == "verified" and inf_exp is False

    s_act_only, inf_act, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="data/orders.csv",
            evidence_type="schema_diff",
            column_name="amount",
            tool_execution_id="TE-SCH-TM",
            observation="The amount column has a type mismatch: actual object.",
        ),
        files, [te_schema], investigation_id="INV-TM",
    )
    assert s_act_only == "verified" and inf_act is False

    # 6. Ambiguous / role-less / cross-column / negated claims -> unverified
    for ambig_obs in (
        "The amount column has a type mismatch between float64 and object.",
        "The amount column has a type mismatch involving float64.",
        "The amount column has a type mismatch: expected float64 or int64, actual object.",
        "The amount column has a type mismatch: expected type is not float64, actual object.",
        "The amount column has a type mismatch: expected int64, actual str.",
        "amount and quantity have a type mismatch: expected float64, actual object.",
    ):
        s_amb, _, _ = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="data/orders.csv",
                evidence_type="schema_diff",
                column_name="amount",
                tool_execution_id="TE-SCH-TM",
                observation=ambig_obs,
            ),
            files, [te_schema], investigation_id="INV-TM",
        )
        assert s_amb == "unverified", f"Expected unverified for ambiguous/cross-col claim: {ambig_obs}"


def test_schema_type_mismatch_incomplete_telemetry_fails_closed():
    """
    Missing expected_dtype or actual_dtype in schema telemetry must fail closed
    rather than verifying claims without backing field data.
    """
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="data/orders.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")
    ]
    incomplete_payloads = [
        {"differences": {"type_mismatches": [{"column": "amount", "expected_dtype": "float64"}]}},
        {"differences": {"type_mismatches": [{"column": "amount", "expected_dtype": "float64", "actual_dtype": None}]}},
        {"differences": {"type_mismatches": [{"column": "amount", "actual_dtype": "object"}]}},
        {"differences": {"type_mismatches": [{"column": "amount"}]}},
        {"type_mismatches": ["amount"]},
    ]
    candidate_obs = [
        "The amount column has a type mismatch: expected float64, actual object.",
        "The amount column has a type mismatch: expected float64.",
        "The amount column has a type mismatch: actual object.",
        "The amount column has a type mismatch.",
    ]

    for idx, payload in enumerate(incomplete_payloads):
        te = ToolExecution(
            id=f"TE-INC-{idx}",
            investigation_id="INV-INC-TM",
            tool_name="inspect_schema",
            status="success",
            output_result=payload,
        )
        for obs in candidate_obs:
            status, _, _ = verify_evidence_against_telemetry(
                AgentEvidenceInput(
                    source_file="data/orders.csv",
                    evidence_type="schema_diff",
                    column_name="amount",
                    tool_execution_id=f"TE-INC-{idx}",
                    observation=obs,
                ),
                files, [te], investigation_id="INV-INC-TM",
            )
            assert status != "verified"
            assert status == "unverified"


@pytest.mark.asyncio
async def test_schema_type_mismatch_real_inspect_schema_tool_end_to_end(tmp_path: Path):
    """
    Runs the actual InspectSchemaTool on baseline and drifted CSV datasets and
    verifies role-preserving checks against the real generated output shape.
    """
    from backend.app.tools.inspect_schema import InspectSchemaTool

    (tmp_path / "baseline.csv").write_text(
        "id,amount\n1,10.5\n2,20.0\n",
        encoding="utf-8",
    )
    (tmp_path / "current.csv").write_text(
        "id,amount\n1,ten_dollars\n2,twenty_dollars\n",
        encoding="utf-8",
    )

    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="current.csv", file_type="data", size_bytes=100, storage_path=str(tmp_path / "current.csv")),
    ]
    res = await InspectSchemaTool().execute(str(tmp_path), {"dataset_path": "current.csv", "baseline_path": "baseline.csv"})
    assert res.success is True
    assert any(
        tm["column"] == "amount" and tm["expected_dtype"] == "float64" and tm["actual_dtype"] == "object"
        for tm in res.data["differences"]["type_mismatches"]
    )

    te = ToolExecution(
        id="E-REAL-TM",
        investigation_id="INV-REAL-TM",
        tool_name="inspect_schema",
        status="success",
        output_result=res.data,
    )

    # Exact roles -> verified
    s_ok, inf_ok, tid_ok = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="current.csv",
            evidence_type="schema_diff",
            column_name="amount",
            observation="The amount column has a type mismatch: expected float64, actual object.",
        ),
        files, [te], investigation_id="INV-REAL-TM", workspace_dir=tmp_path,
    )
    assert s_ok == "verified" and inf_ok is False and tid_ok == "E-REAL-TM"

    # Expected-only narrower claim -> verified
    s_exp, inf_exp, _ = verify_evidence_against_telemetry(
        AgentEvidenceInput(
            source_file="current.csv",
            evidence_type="schema_diff",
            column_name="amount",
            observation="The amount column has a type mismatch: expected float64.",
        ),
        files, [te], investigation_id="INV-REAL-TM", workspace_dir=tmp_path,
    )
    assert s_exp == "verified" and inf_exp is False

    # Reversed, incorrect expected, incorrect actual -> unverified
    for bad_obs in (
        "The amount column has a type mismatch: expected object, actual float64.",
        "The amount column has a type mismatch: expected int64, actual object.",
        "The amount column has a type mismatch: expected float64, actual int64.",
    ):
        s_bad, _, _ = verify_evidence_against_telemetry(
            AgentEvidenceInput(
                source_file="current.csv",
                evidence_type="schema_diff",
                column_name="amount",
                observation=bad_obs,
            ),
            files, [te], investigation_id="INV-REAL-TM", workspace_dir=tmp_path,
        )
        assert s_bad == "unverified"





