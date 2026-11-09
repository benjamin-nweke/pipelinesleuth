"""
PipelineSleuth: Final Security Remediation & Engineering Verification Test Suite
Covers Parts 1 to 5:
- Part 1: Authenticated Human Approval
- Part 2: Server-Controlled Tool Authorization
- Part 3: Factual Evidence Verification
- Part 4: Removal of Legacy Thinking Field
- Part 5: Concurrency-Safe Approval and Validation
"""
import pytest
import pytest_asyncio
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from httpx import AsyncClient, ASGITransport

from backend.app.main import app
from backend.app.database.session import AsyncSessionLocal, init_db
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, EvidenceRecord, ToolExecution
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.core.auth import AuthenticatedPrincipal, register_operator_credential
from backend.app.core.config import settings
from backend.app.tools.registry import tool_registry, DiagnosticToolRegistry
from backend.app.services.investigation_service import investigation_service
from backend.app.services.validation_service import privileged_validation_service
from backend.app.agents.engine import verify_evidence_against_telemetry
from backend.app.schemas.agent import (
    AgentDecision,
    InvestigationSummary,
    AgentEvidenceInput,
    ToolCallRequest
)


@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    await init_db()


@pytest_asyncio.fixture
async def db_session():
    await init_db()
    async with AsyncSessionLocal() as session:
        yield session


# ============================================================================
# PART 1: Authenticated Human Approval Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part1_unauthenticated_approval_rejected(db_session: AsyncSession):
    """Unauthenticated approval attempts are rejected with 401."""
    proj = Project(name="Auth Test Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Auth check")
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
        # 1. No Authorization header
        resp = await client.post(
            f"/api/investigations/{inv.id}/patches/{patch.id}/action",
            json={"action": "approve"}
        )
        assert resp.status_code == 401
        assert "Authentication required" in resp.json()["detail"]

    # Verify patch state was not changed
    await db_session.refresh(patch)
    assert patch.status == "proposed"
    assert patch.approved_by is None


@pytest.mark.asyncio
async def test_part1_unauthenticated_rejection_rejected(db_session: AsyncSession):
    """Unauthenticated rejection attempts are rejected with 401."""
    proj = Project(name="Auth Reject Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Auth reject check")
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
            json={"action": "reject", "reason": "No auth test"}
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_part1_authenticated_authorized_approval_succeeds(db_session: AsyncSession):
    """Authenticated operator successfully approves patch and approval metadata is derived from server principal."""
    proj = Project(name="Authorized Proj")
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
            headers={"Authorization": f"Bearer {settings.OPERATOR_API_KEY}"},
            json={"action": "approve"}
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approved"
        assert data["approved_by"] == "lead_operator"
        assert data["approved_at"] is not None

    await db_session.refresh(patch)
    assert patch.status in ("approved", "validated")
    assert patch.approved_by == "lead_operator"


@pytest.mark.asyncio
async def test_part1_authenticated_unauthorized_role_rejected(db_session: AsyncSession):
    """Authenticated user with 'viewer' role is rejected with 403 Forbidden."""
    proj = Project(name="Viewer Test Proj")
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
        resp = await client.post(
            f"/api/investigations/{inv.id}/patches/{patch.id}/action",
            headers={"Authorization": "Bearer ps_viewer_token_guest"},
            json={"action": "approve"}
        )
        assert resp.status_code == 403
        assert "Forbidden" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_part1_forged_authorized_by_field_ignored(db_session: AsyncSession):
    """A forged 'authorized_by' in request body cannot override server-derived principal identity."""
    proj = Project(name="Forged Auth Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Forged auth")
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
            headers={"Authorization": f"Bearer {settings.OPERATOR_API_KEY}"},
            json={
                "action": "approve",
                "authorized_by": "ATTACKER_IMPERSONATING_CHIEF_SECURITY_OFFICER"
            }
        )
        assert resp.status_code == 200
        data = resp.json()
        # Identity MUST be the server authenticated principal, NOT the attacker payload
        assert data["approved_by"] == "lead_operator"
        assert "ATTACKER" not in data["approved_by"]


# ============================================================================
# PART 2: Server-Controlled Tool Authorization Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part2_agent_invocation_of_validate_patch_denied():
    """Agent cannot invoke validate_patch through the diagnostic tool registry."""
    res = await tool_registry.execute_tool(
        tool_name="validate_patch",
        project_path=".",
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": "a",
            "replacement_code": "b"
        },
        caller_role="agent"
    )
    assert res.success is False
    assert "Permission denied" in res.error
    assert "validate_patch" in res.error


@pytest.mark.asyncio
async def test_part2_caller_role_string_cannot_bypass_privilege_boundary():
    """Supplying caller_role='human_approved_workflow' to tool registry still fails to invoke validate_patch."""
    res = await tool_registry.execute_tool(
        tool_name="validate_patch",
        project_path=".",
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": "a",
            "replacement_code": "b"
        },
        caller_role="human_approved_workflow"
    )
    assert res.success is False
    assert "Security violation" in res.summary
    assert "cannot be invoked through the diagnostic tool registry" in res.error


@pytest.mark.asyncio
async def test_part2_unapproved_patch_cannot_be_validated(db_session: AsyncSession):
    """PrivilegedValidationService rejects validation for patches that are not approved."""
    proj = Project(name="Priv Val Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Check unapproved")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="diff",
        explanation="exp",
        status="proposed"  # NOT approved
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="status is 'proposed'"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


@pytest.mark.asyncio
async def test_part2_cross_investigation_validation_rejected(db_session: AsyncSession):
    """Validation fails if patch belongs to a different investigation."""
    proj = Project(name="Cross Inv Proj")
    db_session.add(proj)
    await db_session.commit()

    inv1 = Investigation(project_id=proj.id, objective="Inv 1")
    inv2 = Investigation(project_id=proj.id, objective="Inv 2")
    db_session.add_all([inv1, inv2])
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv1.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="diff",
        explanation="exp",
        status="approved",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    # Attempting to validate patch under inv2 must be rejected
    with pytest.raises(ValueError, match="not found for investigation"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv2.id,
            patch_id=patch.id
        )


@pytest.mark.asyncio
async def test_part2_patch_without_approval_metadata_rejected(db_session: AsyncSession):
    """Patch marked 'approved' but lacking approver identity is rejected."""
    proj = Project(name="Metadata Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Missing metadata")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="diff",
        explanation="exp",
        status="approved",
        approved_by=None,  # Missing!
        approved_at=None
    )
    db_session.add(patch)
    await db_session.commit()

    with pytest.raises(ValueError, match="lacks verified human approval metadata"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )


# ============================================================================
# PART 3: Evidence Verification Must Be Factual Tests
# ============================================================================

def test_part3_basename_collision_between_directories_rejected():
    """Basename matching across different directories is rejected; exact relative path is required."""
    files = [
        ProjectFile(id="f1", project_id="p1", relative_path="src/etl/pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1"),
        ProjectFile(id="f2", project_id="p1", relative_path="src/models/pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f2")
    ]
    # Input specifies ambiguous basename without dir or wrong dir
    ev = AgentEvidenceInput(
        source_file="pipeline.py",  # Ambiguous basename! Not in project_paths directly
        evidence_type="log_stacktrace",
        line_number=10,
        observation="Crash at line 10"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev, files, [])
    assert status == "unverified"

    # Input specifies wrong nested path
    ev_wrong_dir = AgentEvidenceInput(
        source_file="src/other/pipeline.py",
        evidence_type="log_stacktrace",
        line_number=10,
        observation="Crash at line 10"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev_wrong_dir, files, [])
    assert status == "unverified"


def test_part3_generic_keyword_overlap_rejected():
    """Fabricated observation containing words present in tool output cannot be verified without structured facts."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-01",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "tracebacks": [
                {
                    "frames": [{"file": "pipeline.py", "line": 42}],
                    "exception_type": "ZeroDivisionError",
                    "message": "division by zero in calculate_metrics"
                }
            ]
        }
    )

    # Fabricated observation sharing common words like "metrics", "pipeline", "calculate" but wrong line
    fabricated_ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        line_number=999,  # Line 999 does NOT exist in traceback!
        observation="metrics calculate pipeline had an issue with data processing"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(fabricated_ev, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_unsupported_column_name_rejected():
    """Evidence claiming a nonexistent column is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-02",
        investigation_id="INV-01",
        tool_name="inspect_schema",
        status="success",
        output_result={
            "drift_columns": ["client_id"],
            "missing_columns": ["customer_id"]
        }
    )

    ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="schema_diff",
        column_name="invented_crypto_token",
        observation="Crypto token column missing from schema"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev, files, [te], investigation_id="INV-01")
    assert status == "unverified"


def test_part3_foreign_investigation_execution_id_rejected():
    """Tool execution ID belonging to a different investigation is rejected."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te_foreign = ToolExecution(
        id="TE-FOREIGN",
        investigation_id="INV-OTHER",
        tool_name="analyze_logs",
        status="success",
        output_result={"tracebacks": [{"frames": [{"file": "pipeline.py", "line": 10}]}]}
    )

    ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-FOREIGN",
        line_number=10,
        observation="Failure"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev, files, [te_foreign], investigation_id="INV-CURRENT")
    assert status == "unverified"


def test_part3_valid_structured_facts_verified():
    """Genuinely corroborated factual evidence matching structured telemetry is verified."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="src/pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    te = ToolExecution(
        id="TE-VALID",
        investigation_id="INV-01",
        tool_name="analyze_logs",
        status="success",
        output_result={
            "fatal_error": {
                "last_frame": {"file": "src/pipeline.py", "line": 55},
                "exception_type": "KeyError",
                "message": "'customer_id'"
            }
        }
    )

    ev = AgentEvidenceInput(
        source_file="src/pipeline.py",
        evidence_type="log_stacktrace",
        tool_execution_id="TE-VALID",
        line_number=55,
        observation="KeyError: 'customer_id' encountered at line 55"
    )
    status, is_inf, tid = verify_evidence_against_telemetry(ev, files, [te], investigation_id="INV-01")
    assert status == "verified"
    assert is_inf is False
    assert tid == "TE-VALID"


def test_part3_inference_explicitly_distinguished():
    """Speculative reasoning or hypotheses are preserved as inference, not verified facts."""
    files = [ProjectFile(id="f1", project_id="p1", relative_path="pipeline.py", file_type="code", size_bytes=100, storage_path="/tmp/f1")]
    ev = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="hypothesis_inference",
        is_inference=True,
        observation="The upstream API probably started sending null values due to an unannounced deploy"
    )
    status, is_inf, _ = verify_evidence_against_telemetry(ev, files, [])
    assert status == "inference"
    assert is_inf is True


# ============================================================================
# PART 4: Remove Legacy Thinking Field Tests
# ============================================================================

def test_part4_agent_decision_validates_without_thinking():
    """AgentDecision validates cleanly with investigation_summary and has no thinking attribute."""
    decision = AgentDecision(
        investigation_summary=InvestigationSummary(
            investigation_focus="Investigate KeyError in pipeline.py",
            tool_selection_reason="Need log details",
            finding_summary="KeyError found",
            hypothesis_impact="Confirms drift",
            next_steps="Propose patch",
            conclusion_rationale=None
        ),
        current_stage="failure_localization",
        action_type="call_tool",
        tool_call=ToolCallRequest(tool_name="analyze_logs", arguments={}),
        confidence_score=0.8
    )
    assert decision.investigation_summary.investigation_focus == "Investigate KeyError in pipeline.py"
    assert "thinking" not in AgentDecision.model_fields


def test_part4_legacy_thinking_only_payload_rejected():
    """A payload supplying only 'thinking' without 'investigation_summary' raises ValidationError."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        AgentDecision(
            thinking="Legacy unformatted chain of thought",
            current_stage="context_collection",
            action_type="conclude_inconclusive",
            confidence_score=0.1
        )


# ============================================================================
# PART 5: Concurrency-Safe Approval and Validation Tests
# ============================================================================

@pytest.mark.asyncio
async def test_part5_simultaneous_approval_race_exactly_one_succeeds(db_session: AsyncSession):
    """
    Two simultaneous approval requests execute concurrently via asyncio.gather.
    Exactly ONE request succeeds in transitioning state; the concurrent request fails with conflict.
    """
    proj = Project(name="Race Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Race check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old()",
        proposed_code="new()",
        unified_diff="--- a\n+++ b",
        explanation="Race patch",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    principal_alice = AuthenticatedPrincipal(
        principal_id="op_alice",
        username="alice",
        role="operator",
        scopes=["patch:approve"]
    )
    principal_bob = AuthenticatedPrincipal(
        principal_id="op_bob",
        username="bob",
        role="operator",
        scopes=["patch:approve"]
    )

    async def approve_as(principal):
        async with AsyncSessionLocal() as session:
            try:
                res = await investigation_service.handle_patch_action(
                    db=session,
                    investigation_id=inv.id,
                    patch_id=patch.id,
                    action="approve",
                    principal=principal
                )
                return ("success", res.approved_by)
            except ValueError as e:
                return ("error", str(e))

    # Fire both concurrently
    results = await asyncio.gather(
        approve_as(principal_alice),
        approve_as(principal_bob),
        return_exceptions=True
    )

    successes = [r for r in results if isinstance(r, tuple) and r[0] == "success"]
    errors = [r for r in results if isinstance(r, tuple) and r[0] == "error"]

    # Exactly ONE must succeed, and exactly ONE must fail due to conflict
    assert len(successes) == 1, f"Expected 1 success, got {len(successes)}: {results}"
    assert len(errors) == 1, f"Expected 1 conflict error, got {len(errors)}: {results}"
    assert "already been approved" in errors[0][1]


@pytest.mark.asyncio
async def test_part5_rejection_racing_with_approval_atomic(db_session: AsyncSession):
    """
    Concurrent approval and rejection requests race.
    Only one state transition wins; the other is rejected as conflict.
    """
    proj = Project(name="Approve/Reject Race")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Race approve reject")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old()",
        proposed_code="new()",
        unified_diff="--- a\n+++ b",
        explanation="Race patch",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    principal = AuthenticatedPrincipal(
        principal_id="op_charlie",
        username="charlie",
        role="operator",
        scopes=["patch:approve", "patch:reject"]
    )

    async def do_action(action):
        async with AsyncSessionLocal() as session:
            try:
                res = await investigation_service.handle_patch_action(
                    db=session,
                    investigation_id=inv.id,
                    patch_id=patch.id,
                    action=action,
                    principal=principal
                )
                return ("success", res.status)
            except ValueError as e:
                return ("error", str(e))

    results = await asyncio.gather(
        do_action("approve"),
        do_action("reject")
    )

    successes = [r for r in results if r[0] == "success"]
    errors = [r for r in results if r[0] == "error"]

    assert len(successes) == 1
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_part5_duplicate_validation_prevented(db_session: AsyncSession):
    """PrivilegedValidationService rejects duplicate validation runs for the same approved patch."""
    proj = Project(name="Dup Val Proj")
    db_session.add(proj)
    await db_session.commit()

    inv = Investigation(project_id=proj.id, objective="Dup val check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="x",
        proposed_code="y",
        unified_diff="diff",
        explanation="exp",
        status="approved",
        approved_by="lead_operator",
        approved_at=datetime.now(timezone.utc)
    )
    db_session.add(patch)
    await db_session.commit()

    # Pre-insert an existing validation run
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

    # Duplicate execution attempt must fail
    with pytest.raises(ValueError, match="already been validated"):
        await privileged_validation_service.execute_approved_patch_validation(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id
        )
