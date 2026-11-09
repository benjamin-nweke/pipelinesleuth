"""
PipelineSleuth: Final Security Remediation & Engineering Verification Test Suite
Covers the 4 Core Audit Corrections:
- Part 1: Removal of Hardcoded Credentials & Dynamic Provisioning Enforcement
- Part 2: Mandatory Authenticated Principals & Scope Authorization in Service Layer
- Part 3: Tightened Factual Evidence Verification (No suffix matching, no generic words, exact properties)
- Part 4: Durable Cross-Process Validation Claims, Recovery, and Outcome Consistency
"""
import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone, timedelta
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.database.session import AsyncSessionLocal, init_db
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, EvidenceRecord, ToolExecution
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.core.auth import (
    AuthenticatedPrincipal,
    register_operator_credential,
    clear_operator_credentials,
    PROHIBITED_TOKENS
)
from backend.app.core.config import settings
from backend.app.tools.registry import tool_registry, DiagnosticToolRegistry
from backend.app.services.investigation_service import investigation_service
from backend.app.services.validation_service import PrivilegedValidationService, privileged_validation_service
from backend.app.agents.engine import verify_evidence_against_telemetry
from backend.app.schemas.agent import (
    AgentDecision,
    InvestigationSummary,
    AgentEvidenceInput,
    ToolCallRequest
)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db_and_auth():
    await init_db()
    clear_operator_credentials()
    yield
    clear_operator_credentials()


@pytest_asyncio.fixture
async def db_session():
    await init_db()
    async with AsyncSessionLocal() as session:
        yield session


@pytest.fixture
def provisioned_operator():
    token = "test_dynamic_operator_token_456"
    principal = AuthenticatedPrincipal(
        principal_id="op_test_dynamic_01",
        username="lead_operator",
        role="operator",
        scopes=["patch:approve", "patch:reject", "investigation:run"]
    )
    register_operator_credential(token, principal)
    return token, principal


@pytest.fixture
def provisioned_viewer():
    token = "test_dynamic_viewer_token_789"
    principal = AuthenticatedPrincipal(
        principal_id="usr_view_01",
        username="read_only_viewer",
        role="viewer",
        scopes=["investigation:read"]
    )
    register_operator_credential(token, principal)
    return token, principal


# ============================================================================
# PART 1: Remove All Hardcoded Authentication Credentials Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part1_old_hardcoded_tokens_rejected():
    """All legacy, hardcoded, and predictable credentials are on the security blocklist and rejected with 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        for old_token in ["ps_admin_token_master", "ps_viewer_token_guest", "ps_operator_sec_key_2026", "admin", "password"]:
            resp = await client.post(
                "/api/investigations/INV-01/patches/PATCH-01/action",
                headers={"Authorization": f"Bearer {old_token}"},
                json={"action": "approve"}
            )
            assert resp.status_code == 401
            assert "Authentication failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_part1_unset_operator_credential_cannot_authenticate():
    """When no operator credentials have been configured or provisioned, authentication is refused."""
    clear_operator_credentials()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/investigations/INV-01/patches/PATCH-01/action",
            headers={"Authorization": "Bearer any_random_token"},
            json={"action": "approve"}
        )
        assert resp.status_code == 401
        assert "Authentication unconfigured" in resp.json()["detail"] or "Authentication failed" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_part1_invalid_credential_receives_401(provisioned_operator):
    """An invalid token sent when valid credentials exist returns 401 Unauthorized."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/api/investigations/INV-01/patches/PATCH-01/action",
            headers={"Authorization": "Bearer completely_bogus_token"},
            json={"action": "approve"}
        )
        assert resp.status_code == 401
        assert "Invalid credentials" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_part1_properly_provisioned_operator_can_authenticate(db_session: AsyncSession, provisioned_operator):
    """Properly provisioned operator credential authenticates successfully and sets patch to approved."""
    token, principal = provisioned_operator
    proj = Project(name="Auth Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Approval auth")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x = 1",
        proposed_code="x = 2",
        unified_diff="--- a\n+++ b",
        explanation="Test patch",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            f"/api/investigations/{inv.id}/patches/{patch.id}/action",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "approve"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("approved", "validating", "validated")
        assert data["approved_by"] == principal.username
        assert data["approved_at"] is not None


@pytest.mark.asyncio
async def test_part1_viewer_cannot_approve_or_reject(db_session: AsyncSession, provisioned_viewer):
    """Authenticated user with 'viewer' role is rejected with 403 Forbidden on approval and rejection."""
    token, _ = provisioned_viewer
    proj = Project(name="Viewer Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Viewer test")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x = 1",
        proposed_code="x = 2",
        unified_diff="--- a\n+++ b",
        explanation="Test patch",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Approval attempt
        resp_approve = await client.post(
            f"/api/investigations/{inv.id}/patches/{patch.id}/action",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "approve"}
        )
        assert resp_approve.status_code == 403
        assert "Forbidden" in resp_approve.json()["detail"]

        # Rejection attempt
        resp_reject = await client.post(
            f"/api/investigations/{inv.id}/patches/{patch.id}/action",
            headers={"Authorization": f"Bearer {token}"},
            json={"action": "reject", "reason": "Viewer try"}
        )
        assert resp_reject.status_code == 403


# ============================================================================
# PART 2: Mandatory Authenticated Principals in Service Layer Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part2_service_rejects_missing_principal(db_session: AsyncSession):
    """Calling handle_patch_action without an AuthenticatedPrincipal raises ValueError."""
    with pytest.raises(ValueError, match="authenticated principal is strictly required"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id="INV-01",
            patch_id="PATCH-01",
            action="approve",
            principal=None  # type: ignore
        )


@pytest.mark.asyncio
async def test_part2_service_rejects_fabricated_identity_type(db_session: AsyncSession):
    """Calling handle_patch_action with a plain string or dictionary identity raises ValueError."""
    with pytest.raises(ValueError, match="authenticated principal is strictly required"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id="INV-01",
            patch_id="PATCH-01",
            action="approve",
            principal="admin_user_string"  # type: ignore
        )


@pytest.mark.asyncio
async def test_part2_principal_without_approve_scope_cannot_approve(db_session: AsyncSession):
    """A principal without 'patch:approve' scope cannot approve a patch (raises PermissionError)."""
    proj = Project(name="Scope Test Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Scope test")
    test_patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="diff",
        explanation="test",
        status="proposed"
    )
    test_patch.investigation = inv
    db_session.add_all([inv, test_patch])
    await db_session.commit()

    # Principal has reject permission but NOT approve
    reject_only_principal = AuthenticatedPrincipal(
        principal_id="op_rej_only",
        username="auditor",
        role="operator",
        scopes=["patch:reject"]
    )

    with pytest.raises(PermissionError, match="lacks the required 'patch:approve' scope"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id=inv.id,
            patch_id=test_patch.id,
            action="approve",
            principal=reject_only_principal
        )


@pytest.mark.asyncio
async def test_part2_principal_without_reject_scope_cannot_reject(db_session: AsyncSession):
    """A principal without 'patch:reject' scope cannot reject a patch (raises PermissionError)."""
    proj = Project(name="Scope Test Proj 2")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Scope test 2")
    test_patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="diff",
        explanation="test",
        status="proposed"
    )
    test_patch.investigation = inv
    db_session.add_all([inv, test_patch])
    await db_session.commit()

    # Principal has approve permission but NOT reject
    approve_only_principal = AuthenticatedPrincipal(
        principal_id="op_app_only",
        username="engineer",
        role="operator",
        scopes=["patch:approve"]
    )

    with pytest.raises(PermissionError, match="lacks the required 'patch:reject' scope"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id=inv.id,
            patch_id=test_patch.id,
            action="reject",
            principal=approve_only_principal
        )


@pytest.mark.asyncio
async def test_part2_valid_approval_and_rejection_authorized(db_session: AsyncSession):
    """Principals with valid scopes successfully execute approval and rejection."""
    proj = Project(name="Valid Action Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Valid actions")
    p1 = PatchProposal(
        investigation_id=inv.id,
        target_file="p1.py",
        original_code_context="a",
        proposed_code="b",
        unified_diff="d",
        explanation="e",
        status="proposed"
    )
    p2 = PatchProposal(
        investigation_id=inv.id,
        target_file="p2.py",
        original_code_context="c",
        proposed_code="d",
        unified_diff="d",
        explanation="e",
        status="proposed"
    )
    p1.investigation = inv
    p2.investigation = inv
    db_session.add_all([inv, p1, p2])
    await db_session.commit()

    full_op = AuthenticatedPrincipal(
        principal_id="op_full",
        username="lead_eng",
        role="operator",
        scopes=["patch:approve", "patch:reject"]
    )

    # Approve p1
    res1 = await investigation_service.handle_patch_action(
        db=db_session,
        investigation_id=inv.id,
        patch_id=p1.id,
        action="approve",
        principal=full_op
    )
    assert res1.status in ("approved", "validating", "validated")
    assert res1.approved_by == "lead_eng"

    # Reject p2
    res2 = await investigation_service.handle_patch_action(
        db=db_session,
        investigation_id=inv.id,
        patch_id=p2.id,
        action="reject",
        principal=full_op,
        reason="Rejected on review"
    )
    assert res2.status == "rejected"
    assert "Rejected on review" in res2.rejection_reason


# ============================================================================
# PART 3: Tightened Factual Evidence Verification Tests
# ============================================================================

def test_part3_suffix_based_traceback_path_rejected():
    """A traceback path that only matches by suffix (e.g. /other/nested/pipeline.py vs src/pipeline.py) is rejected."""
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="src/pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")
    ]
    # Traceback points to a different directory file that happens to end with 'pipeline.py'
    te = ToolExecution(
        id="TE-TRACE-01",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "tracebacks": [
                {
                    "frames": [{"file": "other_module/pipeline.py", "line": 42}],
                    "exception_type": "KeyError",
                    "message": "'item_id'"
                }
            ]
        }
    )

    ev = AgentEvidenceInput(
        source_file="src/pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-TRACE-01",
        line_number=42,
        observation="KeyError: 'item_id' in src/pipeline.py"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_generic_error_observation_rejected():
    """A generic 'error' observation without actual exception type or message corroboration is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-GEN-01",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 15},
                "exception_type": "ZeroDivisionError",
                "message": "division by zero in compute_ratio"
            }
        }
    )

    # Observation matches line 15, but has only generic words without mentioning ZeroDivisionError or compute_ratio
    ev_generic = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-GEN-01",
        line_number=15,
        observation="The pipeline execution failed due to an error during batch processing"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_generic, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_existing_column_claimed_as_missing_rejected():
    """A column that exists in actual_schema cannot be verified if claimed as missing."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="data/raw.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-SCH-01",
        investigation_id="INV-01",
        tool_name="inspect_schema",
        status="success",
        output_result={
            "actual_schema": {"user_id": "int64", "timestamp": "datetime"},
            "missing_columns": ["revenue"],  # 'user_id' is NOT missing!
            "drift_columns": ["revenue"]
        }
    )

    ev_false_missing = AgentEvidenceInput(
        source_file="data/raw.csv",
        evidence_type="schema_diff",
        column_name="user_id",
        observation="Missing column: user_id was dropped from dataset"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_false_missing, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_unsupported_type_mismatch_claim_rejected():
    """Claiming a type mismatch for a column that has no type mismatch is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="data/raw.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-SCH-02",
        investigation_id="INV-01",
        tool_name="inspect_schema",
        status="success",
        output_result={
            "actual_schema": {"amount": "float64"},
            "type_mismatches": []  # No type mismatches!
        }
    )

    ev_false_type = AgentEvidenceInput(
        source_file="data/raw.csv",
        evidence_type="schema_diff",
        column_name="amount",
        observation="Incompatible type: amount has a type mismatch with expected int"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_false_type, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_dataset_anomaly_with_invented_statistic_rejected():
    """An observation asserting an invented percentage or count statistic is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="data/raw.csv", file_type="data", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-PROF-01",
        investigation_id="INV-01",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "anomalies": [
                {"column": "price", "type": "null_values", "null_count": 2, "null_percentage": 0.04}
            ],
            "columns": {"price": {"null_count": 2, "null_percentage": 0.04}}
        }
    )

    # Observation invents "75% nulls" or "50 null records"
    ev_invented_stat = AgentEvidenceInput(
        source_file="data/raw.csv",
        evidence_type="dataset_anomaly",
        column_name="price",
        observation="Critical failure: price column contains 75% missing values"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_invented_stat, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_unrelated_function_in_ast_inspection_rejected():
    """An observation claiming a function not present in AST structured output is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-AST-01",
        investigation_id="INV-01",
        tool_name="inspect_code",
        status="success",
        output_result={
            "functions": [
                {"name": "load_records", "lineno": 10, "end_lineno": 25}
            ],
            "classes": []
        }
    )

    ev_unrelated_fn = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="ast_pattern",
        observation="Function compute_encrypted_hashes defined at line 10"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_unrelated_fn, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_unsupported_dependency_claim_rejected():
    """Claiming an unrecorded dependency in trace_dependencies is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-DEP-01",
        investigation_id="INV-01",
        tool_name="trace_dependencies",
        status="success",
        output_result={
            "dependencies": ["pandas", "pytest"],
            "imports": ["pandas", "pytest"]
        }
    )

    ev_unsupported_dep = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="diagnostic_output",
        observation="Pipeline imports tensorflow for deep learning transformations"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_unsupported_dep, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_valid_structured_evidence_verified_across_categories():
    """Valid structured facts matching telemetry across log, schema, profile, AST, and dependencies are verified."""
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1"),
        ProjectFile(id="f2", project_id="p1", relative_path="data/transactions.csv", file_type="data", size_bytes=100, storage_path="/tmp/f2")
    ]

    # 1. Log traceback with exact exception type and message
    te_log = ToolExecution(
        id="TE-LOG-V",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "pipeline.py", "line": 21},
                "exception_type": "KeyError",
                "message": "Required column 'customer_id' missing from dataset"
            }
        }
    )
    ev_log = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-LOG-V",
        line_number=21,
        observation="KeyError: customer_id missing from dataset at line 21"
    )
    s1, _, tid1 = verify_evidence_against_telemetry(ev_log, files, [te_log], investigation_id="INV-01")
    assert s1 == "verified"
    assert tid1 == "TE-LOG-V"

    # 2. Schema drift with exact missing column
    te_schema = ToolExecution(
        id="TE-SCH-V",
        investigation_id="INV-01",
        tool_name="inspect_schema",
        status="success",
        output_result={
            "missing_columns": ["customer_id"],
            "new_columns": ["client_id"],
            "drift_columns": ["customer_id", "client_id"]
        }
    )
    ev_schema = AgentEvidenceInput(
        source_file="data/transactions.csv",
        evidence_type="schema_diff",
        column_name="customer_id",
        tool_execution_id="TE-SCH-V",
        observation="Schema drift: customer_id was dropped and missing from schema"
    )
    s2, _, tid2 = verify_evidence_against_telemetry(ev_schema, files, [te_schema], investigation_id="INV-01")
    assert s2 == "verified"
    assert tid2 == "TE-SCH-V"

    # 3. Profile dataset with exact null count and percentage
    te_prof = ToolExecution(
        id="TE-PROF-V",
        investigation_id="INV-01",
        tool_name="profile_dataset",
        status="success",
        output_result={
            "anomalies": [{"column": "amount", "type": "null_values", "null_count": 2}],
            "columns": {"amount": {"null_count": 2}}
        }
    )
    ev_prof = AgentEvidenceInput(
        source_file="data/transactions.csv",
        evidence_type="dataset_anomaly",
        column_name="amount",
        tool_execution_id="TE-PROF-V",
        observation="Found 2 null values in amount column"
    )
    s3, _, tid3 = verify_evidence_against_telemetry(ev_prof, files, [te_prof], investigation_id="INV-01")
    assert s3 == "verified"
    assert tid3 == "TE-PROF-V"


# ============================================================================
# PART 4: Durable Cross-Process Validation Claims & Concurrency Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part4_two_independent_service_instances_attempting_concurrent_claim(db_session: AsyncSession):
    """
    Two independent PrivilegedValidationService instances attempting to claim the same patch.
    Exactly one succeeds; the second receives a Concurrency violation error.
    """
    proj = Project(name="Concurrent Service Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Cross-instance validation")
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="d",
        explanation="e",
        status="approved",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    patch.investigation = inv
    db_session.add_all([inv, patch])
    await db_session.commit()

    service_instance_a = PrivilegedValidationService()
    service_instance_b = PrivilegedValidationService()

    async def attempt_claim(service, worker_id):
        async with AsyncSessionLocal() as session:
            try:
                p = await service.claim_patch_for_validation(
                    db=session,
                    investigation_id=inv.id,
                    patch_id=patch.id,
                    worker_id=worker_id
                )
                return ("claimed", worker_id, p.status)
            except ValueError as e:
                return ("denied", worker_id, str(e))

    results = await asyncio.gather(
        attempt_claim(service_instance_a, "worker_process_alpha"),
        attempt_claim(service_instance_b, "worker_process_beta")
    )

    claims = [r for r in results if r[0] == "claimed"]
    denials = [r for r in results if r[0] == "denied"]

    assert len(claims) == 1, f"Expected exactly 1 claim, got: {results}"
    assert len(denials) == 1, f"Expected exactly 1 denial, got: {results}"
    assert "already claimed by active worker" in denials[0][2]


@pytest.mark.asyncio
async def test_part4_duplicate_validation_request_after_successful_run(db_session: AsyncSession):
    """Attempting to validate an already validated patch fails with duplicate validation blocked."""
    proj = Project(name="Dup Val Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Dup run check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="d",
        explanation="e",
        status="validated",  # Already successfully validated
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    val_run = ValidationRun(
        investigation_id=inv.id,
        patch_id=patch.id,
        status="passed",
        exit_code=0,
        authorized_by="lead_operator",
        authorized_at=datetime.now(timezone.utc)
    )
    db_session.add(val_run)
    await db_session.commit()

    with pytest.raises(ValueError, match="already been validated"):
        await privileged_validation_service.claim_patch_for_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            worker_id="worker_new"
        )


@pytest.mark.asyncio
async def test_part4_duplicate_request_after_failed_run(db_session: AsyncSession):
    """Attempting to re-claim a patch after a failed validation run is blocked."""
    proj = Project(name="Failed Val Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Failed run check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="d",
        explanation="e",
        status="validation_failed",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    val_run = ValidationRun(
        investigation_id=inv.id,
        patch_id=patch.id,
        status="failed",
        exit_code=1,
        authorized_by="lead_operator",
        authorized_at=datetime.now(timezone.utc)
    )
    db_session.add(val_run)
    await db_session.commit()

    with pytest.raises(ValueError, match="already been validated"):
        await privileged_validation_service.claim_patch_for_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            worker_id="worker_new"
        )


@pytest.mark.asyncio
async def test_part4_recovery_of_abandoned_validation(db_session: AsyncSession):
    """An abandoned validation (worker crashed or timed out) can be safely recovered back to 'approved'."""
    proj = Project(name="Abandoned Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Abandoned check")
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="d",
        explanation="e",
        status="validating",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc) - timedelta(minutes=10),
        validation_claimed_at=datetime.now(timezone.utc) - timedelta(minutes=8),
        validation_claimed_by="crashed_worker_01"
    )
    patch.investigation = inv
    db_session.add_all([inv, patch])
    await db_session.commit()

    # Recovery with timeout threshold of 300s (8 minutes > 5 minutes)
    recovered = await privileged_validation_service.recover_abandoned_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        timeout_seconds=300
    )
    assert recovered.status == "approved"
    assert recovered.validation_claimed_at is None
    assert recovered.validation_claimed_by is None

    # Now the patch can be claimed again by a healthy worker
    reclaimed = await privileged_validation_service.claim_patch_for_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        worker_id="healthy_worker_02"
    )
    assert reclaimed.status == "validating"
    assert reclaimed.validation_claimed_by == "healthy_worker_02"


@pytest.mark.asyncio
async def test_part4_failed_test_command_records_accurate_status(db_session: AsyncSession, tmp_path: Path):
    """When a candidate patch fails the test suite, patch.status is 'validation_failed' (NOT 'validated')."""
    proj = Project(name="Failing Test Proj")
    db_session.add(proj)
    await db_session.commit()

    # Set up project directory with an intentional failing test
    proj_dir = settings.UPLOADS_DIR / proj.id
    proj_dir.mkdir(parents=True, exist_ok=True)
    test_file = proj_dir / "test_pipeline.py"
    test_file.write_text("def test_fail():\n    assert False, 'Expected test failure'\n")
    pipe_file = proj_dir / "pipeline.py"
    pipe_file.write_text("def run(): pass\n")

    inv = Investigation(project_id=proj.id, objective="Test failing run")
    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="def run(): pass",
        proposed_code="def run(): pass # modified",
        unified_diff="diff",
        explanation="test failure",
        status="approved",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    patch.investigation = inv
    db_session.add_all([inv, patch])
    await db_session.commit()

    # Execute validation with pytest running on the failing test
    val_run = await privileged_validation_service.execute_approved_patch_validation(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        test_command=["pytest", str(test_file)],
        worker_id="test_worker"
    )

    # 1. Validation run must report status 'failed' and non-zero exit code
    assert val_run.status == "failed"
    assert val_run.exit_code != 0

    # 2. Patch status must be 'validation_failed' (fixing the status inconsistency)
    await db_session.refresh(patch)
    assert patch.status == "validation_failed"
    assert patch.status != "validated"

    # 3. Investigation status must reflect failure
    await db_session.refresh(inv)
    assert inv.status == "patch_validation_failed"
