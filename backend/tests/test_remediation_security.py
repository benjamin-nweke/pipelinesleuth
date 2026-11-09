"""
Comprehensive Security and Engineering Remediation Tests
Validates all six remediation areas:
1. Server-side human approval enforcement & duplicate protection
2. Strict target file path containment, symlink rejection & traversal guards
3. Ground-truth evidence verification before labelling verified
4. Central tool argument validation, permission checks, timeouts, and audit logging
5. Hardened Docker sandbox isolation flags and process termination
6. Replacement of internal chain-of-thought with structured user-facing investigation summaries
"""
import pytest
import pytest_asyncio
import os
import asyncio
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from backend.app.database.session import Base, AsyncSessionLocal, init_db
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, EvidenceRecord, Hypothesis, ToolExecution
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.services.investigation_service import investigation_service
from backend.app.tools.registry import tool_registry, DiagnosticToolRegistry
from backend.app.tools.base import BaseDiagnosticTool, DiagnosticToolDefinition, ToolParameter, ToolResult
from backend.app.tools.validate_patch import validate_and_resolve_target_file, ValidatePatchTool
from backend.app.agents.engine import verify_evidence_against_telemetry
from backend.app.schemas.agent import (
    AgentEvidenceInput, AgentDecision, InvestigationSummary,
    AgentHypothesisInput, ToolCallRequest
)
from backend.app.core.auth import AuthenticatedPrincipal
from backend.app.sandbox.manager import sandbox_manager

test_operator = AuthenticatedPrincipal(
    principal_id="op_test_01",
    username="alice@data-eng.com",
    role="operator",
    scopes=["patch:approve", "patch:reject"]
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
# 1. Human Approval Server-Side Enforcement Tests
# ============================================================================

@pytest.mark.asyncio
async def test_human_approval_unapproved_patch_cannot_execute(db_session: AsyncSession):
    """An unapproved patch cannot be validated directly."""
    project = Project(name="Approval Test Proj")
    db_session.add(project)
    await db_session.commit()

    inv = Investigation(project_id=project.id, objective="Approval security check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old_code()",
        proposed_code="new_code()",
        unified_diff="--- a\n+++ b",
        explanation="Fix",
        status="proposed"  # Unapproved
    )
    db_session.add(patch)
    await db_session.commit()

    # Attempting to run background validation on an unapproved patch should cleanly abort
    await investigation_service._validate_patch_background(inv.id, patch.id)

    # Check that no validation run was persisted
    val_res = await db_session.execute(
        select(ValidationRun).where(ValidationRun.patch_id == patch.id)
    )
    runs = val_res.scalars().all()
    assert len(runs) == 0


@pytest.mark.asyncio
async def test_human_approval_rejected_patch_cannot_execute(db_session: AsyncSession):
    """A rejected patch cannot be approved or validated."""
    project = Project(name="Reject Test Proj")
    db_session.add(project)
    await db_session.commit()

    inv = Investigation(project_id=project.id, objective="Rejection check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old()",
        proposed_code="new()",
        unified_diff="--- a\n+++ b",
        explanation="Test fix",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    # Reject the patch
    rejected = await investigation_service.handle_patch_action(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        action="reject",
        principal=test_operator,
        reason="Does not address edge case"
    )
    assert rejected.status == "rejected"
    assert rejected.rejected_at is not None
    assert "edge case" in rejected.rejection_reason

    # Subsequent approval attempts must be blocked
    with pytest.raises(ValueError, match="already been rejected"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            action="approve",
            principal=test_operator
        )


@pytest.mark.asyncio
async def test_human_approval_approved_patch_records_metadata_and_authorizes(db_session: AsyncSession):
    """Approving a patch records operator authorization metadata and triggers validation."""
    project = Project(name="Approve Test Proj")
    db_session.add(project)
    await db_session.commit()

    inv = Investigation(project_id=project.id, objective="Approve check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old()",
        proposed_code="new()",
        unified_diff="--- a\n+++ b",
        explanation="Good fix",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    approved = await investigation_service.handle_patch_action(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        action="approve",
        principal=test_operator
    )
    assert approved.status == "approved"
    assert approved.approved_by == "alice@data-eng.com"
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_direct_ai_invocation_of_validation_blocked():
    """Direct attempts by the AI agent to invoke validate_patch are blocked by central registry."""
    res = await tool_registry.execute_tool(
        tool_name="validate_patch",
        project_path=".",
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": "foo",
            "replacement_code": "bar"
        },
        caller_role="agent"  # AI Agent caller
    )
    assert res.success is False
    assert "Permission denied" in res.error
    assert "requires explicit human approval" in res.error


@pytest.mark.asyncio
async def test_repeated_approval_requests_blocked(db_session: AsyncSession):
    """Repeated approval requests on an already approved patch are rejected as duplicate executions."""
    project = Project(name="Duplicate Approval Proj")
    db_session.add(project)
    await db_session.commit()

    inv = Investigation(project_id=project.id, objective="Dup check")
    db_session.add(inv)
    await db_session.commit()

    patch = PatchProposal(
        investigation_id=inv.id,
        target_file="pipeline.py",
        original_code_context="old()",
        proposed_code="new()",
        unified_diff="--- a\n+++ b",
        explanation="Fix",
        status="proposed"
    )
    db_session.add(patch)
    await db_session.commit()

    # First approval
    await investigation_service.handle_patch_action(
        db=db_session,
        investigation_id=inv.id,
        patch_id=patch.id,
        action="approve",
        principal=test_operator
    )

    # Second approval attempt must be blocked
    with pytest.raises(ValueError, match="already been approved"):
        await investigation_service.handle_patch_action(
            db=db_session,
            investigation_id=inv.id,
            patch_id=patch.id,
            action="approve",
            principal=test_operator
        )


# ============================================================================
# 2. Secure Patch File Paths Tests
# ============================================================================

def test_validate_target_file_traversal_blocked(tmp_path: Path):
    """Rejects ../../ traversal attempts."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "pipeline.py").write_text("x = 1")

    valid, target, err = validate_and_resolve_target_file(ws, "../../outside.py")
    assert valid is False
    assert target is None
    assert "Path traversal" in err or "escapes" in err


def test_validate_target_file_absolute_path_blocked(tmp_path: Path):
    """Rejects absolute paths."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    valid, target, err = validate_and_resolve_target_file(ws, "/etc/passwd")
    assert valid is False
    assert target is None
    assert "Absolute target paths are forbidden" in err


def test_validate_target_file_symlink_traversal_blocked(tmp_path: Path):
    """Rejects symlinks pointing outside the workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("classified")

    symlink_file = ws / "evil_link.py"
    try:
        symlink_file.symlink_to(outside_file)
    except OSError:
        pytest.skip("Symlinks not supported on this environment")

    valid, target, err = validate_and_resolve_target_file(ws, "evil_link.py")
    assert valid is False
    assert target is None
    assert "outside candidate workspace" in err or "Symlink traversal" in err


def test_validate_target_file_nested_valid_path(tmp_path: Path):
    """Accepts valid nested project paths."""
    ws = tmp_path / "workspace"
    nested_dir = ws / "src" / "pipelines"
    nested_dir.mkdir(parents=True)
    target = nested_dir / "transform.py"
    target.write_text("def run(): pass")

    valid, candidate_path, err = validate_and_resolve_target_file(ws, "src/pipelines/transform.py")
    assert valid is True
    assert err is None
    assert candidate_path == target.resolve()


def test_validate_target_file_missing_file_rejected(tmp_path: Path):
    """Rejects non-existent target files."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    valid, target, err = validate_and_resolve_target_file(ws, "does_not_exist.py")
    assert valid is False
    assert "not found" in err


def test_validate_target_file_directory_rejected(tmp_path: Path):
    """Rejects directories targeted as patch files."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    sub = ws / "subdir"
    sub.mkdir()

    valid, target, err = validate_and_resolve_target_file(ws, "subdir")
    assert valid is False
    assert "not a regular file" in err


# ============================================================================
# 3. Verify Evidence Before Labelling Verified Tests
# ============================================================================

def test_evidence_matching_tool_output_verified():
    """Evidence matching empirical tool telemetry is correctly verified."""
    project_files = [ProjectFile(relative_path="pipeline.py")]
    tool_execs = [
        ToolExecution(
            id="EXEC-001",
            tool_name="analyze_logs",
            output_result={
                "fatal_error": {
                    "exception_type": "KeyError",
                    "exception_message": "customer_id",
                    "last_frame": {"file": "pipeline.py", "line": 21}
                }
            }
        )
    ]
    ev_input = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        line_number=21,
        column_name="customer_id",
        observation="KeyError on customer_id at line 21",
        tool_execution_id="EXEC-001"
    )

    v_status, is_inf, matched_id = verify_evidence_against_telemetry(ev_input, project_files, tool_execs)
    assert v_status == "verified"
    assert is_inf is False
    assert matched_id == "EXEC-001"


def test_evidence_invented_observation_unverified():
    """Hallucinated observation with no tool corroboration remains unverified."""
    project_files = [ProjectFile(relative_path="pipeline.py")]
    tool_execs = [
        ToolExecution(
            id="EXEC-002",
            tool_name="analyze_logs",
            output_result={"fatal_error": {"exception_type": "KeyError", "last_frame": {"line": 21}}}
        )
    ]
    ev_input = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        line_number=21,
        observation="Quantum memory leak in distributed redis buffer cluster",
        tool_execution_id="EXEC-002"
    )

    v_status, is_inf, matched_id = verify_evidence_against_telemetry(ev_input, project_files, tool_execs)
    assert v_status == "unverified"
    assert matched_id is None


def test_evidence_incorrect_file_reference_unverified():
    """Evidence referencing a non-existent file in project artifacts remains unverified."""
    project_files = [ProjectFile(relative_path="pipeline.py")]
    tool_execs = [
        ToolExecution(id="EXEC-003", tool_name="inspect_project", output_result={"total_files": 1})
    ]
    ev_input = AgentEvidenceInput(
        source_file="completely_fabricated_file.py",
        evidence_type="code_pattern",
        observation="Found suspicious function call"
    )

    v_status, is_inf, matched_id = verify_evidence_against_telemetry(ev_input, project_files, tool_execs)
    assert v_status == "unverified"
    assert matched_id is None


def test_evidence_unsupported_line_number_unverified():
    """Evidence claiming an unsupported line number not in tool output remains unverified."""
    project_files = [ProjectFile(relative_path="pipeline.py")]
    tool_execs = [
        ToolExecution(
            id="EXEC-004",
            tool_name="analyze_logs",
            output_result={
                "fatal_error": {
                    "exception_type": "KeyError",
                    "last_frame": {"file": "pipeline.py", "line": 21}
                }
            }
        )
    ]
    ev_input = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="log_stacktrace",
        line_number=999,  # Unsupported line number
        observation="KeyError on line 999",
        tool_execution_id="EXEC-004"
    )

    v_status, is_inf, matched_id = verify_evidence_against_telemetry(ev_input, project_files, tool_execs)
    assert v_status == "unverified"
    assert matched_id is None


def test_evidence_inference_distinguishable():
    """Inferences are marked as inference and distinguished from verified facts."""
    project_files = [ProjectFile(relative_path="pipeline.py")]
    tool_execs = []
    ev_input = AgentEvidenceInput(
        source_file="pipeline.py",
        evidence_type="inference",
        observation="Hypothesize upstream schema rename occurred based on industry trends",
        is_inference=True
    )

    v_status, is_inf, matched_id = verify_evidence_against_telemetry(ev_input, project_files, tool_execs)
    assert v_status == "inference"
    assert is_inf is True
    assert matched_id is None


# ============================================================================
# 4. Central Tool Permissions, Timeouts, and Audit Logging Tests
# ============================================================================

@pytest.mark.asyncio
async def test_tool_argument_validation_unexpected_arg():
    """Registry rejects unexpected arguments not declared in tool schema."""
    res = await tool_registry.execute_tool(
        tool_name="inspect_project",
        project_path=".",
        arguments={"non_existent_arg": 123},
        caller_role="agent"
    )
    assert res.success is False
    assert "Unexpected argument" in res.error


@pytest.mark.asyncio
async def test_tool_argument_validation_missing_required():
    """Registry rejects missing required arguments."""
    res = await tool_registry.execute_tool(
        tool_name="inspect_code",
        project_path=".",
        arguments={},  # 'file_path' is required
        caller_role="agent"
    )
    assert res.success is False
    assert "Missing required argument 'file_path'" in res.error


@pytest.mark.asyncio
async def test_tool_argument_validation_type_mismatch():
    """Registry rejects parameter type mismatches."""
    res = await tool_registry.execute_tool(
        tool_name="inspect_code",
        project_path=".",
        arguments={"file_path": 12345},  # Expected string, passed integer
        caller_role="agent"
    )
    assert res.success is False
    assert "must be of type string" in res.error


@pytest.mark.asyncio
async def test_tool_unknown_rejected():
    """Registry cleanly rejects unregistered tools."""
    res = await tool_registry.execute_tool(
        tool_name="non_existent_tool_xyz",
        project_path=".",
        arguments={},
        caller_role="agent"
    )
    assert res.success is False
    assert "Unregistered diagnostic tool" in res.error


@pytest.mark.asyncio
async def test_tool_unauthorized_execution_rejected():
    """Caller role 'unauthorized' is blocked centrally."""
    res = await tool_registry.execute_tool(
        tool_name="inspect_project",
        project_path=".",
        arguments={},
        caller_role="unauthorized"
    )
    assert res.success is False
    assert "Permission denied: Caller 'unauthorized'" in res.error


@pytest.mark.asyncio
async def test_tool_permission_escalation_blocked():
    """Agent attempting to call validate_patch directly is blocked as permission violation."""
    res = await tool_registry.execute_tool(
        tool_name="validate_patch",
        project_path=".",
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": "foo",
            "replacement_code": "bar"
        },
        caller_role="agent"
    )
    assert res.success is False
    assert "requires explicit human approval" in res.error


@pytest.mark.asyncio
async def test_tool_timeout_enforced():
    """Centrally applies timeout and returns structured failure."""
    class SlowTool(BaseDiagnosticTool):
        name = "slow_mock_tool"
        description = "Mock tool for timeout test"
        permission_level = "read_only"
        timeout_seconds = 1  # 1 second timeout

        def get_definition(self):
            return DiagnosticToolDefinition(
                name=self.name,
                description=self.description,
                parameters=[],
                permission_level=self.permission_level,
                timeout_seconds=self.timeout_seconds
            )

        async def execute(self, project_path, arguments):
            await asyncio.sleep(3)  # Sleep longer than timeout
            return ToolResult(success=True, summary="Done")

    reg = DiagnosticToolRegistry()
    reg.register_tool(SlowTool())

    res = await reg.execute_tool(
        tool_name="slow_mock_tool",
        project_path=".",
        arguments={},
        caller_role="agent"
    )
    assert res.success is False
    assert "timed out after 1 seconds" in res.summary


@pytest.mark.asyncio
async def test_tool_execution_budget_enforced():
    """Tool execution is blocked when remaining budget is exhausted."""
    res = await tool_registry.execute_tool(
        tool_name="inspect_project",
        project_path=".",
        arguments={},
        caller_role="agent",
        remaining_budget=0  # Budget exhausted
    )
    assert res.success is False
    assert "Execution budget exhausted" in res.error


@pytest.mark.asyncio
async def test_tool_audit_log_persists_all_attempts():
    """Audit log records both successful and blocked attempts with caller role and telemetry."""
    reg = DiagnosticToolRegistry()
    await reg.execute_tool("inspect_project", ".", {}, caller_role="agent")
    await reg.execute_tool("validate_patch", ".", {"target_file": "p.py", "original_snippet": "a", "replacement_code": "b"}, caller_role="agent")

    audit = reg.get_audit_log()
    assert len(audit) >= 2
    # Verify first call logged
    assert audit[-2]["tool_name"] == "inspect_project"
    assert audit[-2]["caller_role"] == "agent"
    # Verify second blocked call logged
    assert audit[-1]["tool_name"] == "validate_patch"
    assert audit[-1]["success"] is False
    assert "requires explicit human approval" in audit[-1]["error"]


# ============================================================================
# 5. Harden Docker Sandbox Isolation Tests
# ============================================================================

def test_sandbox_docker_command_hardening_flags():
    """Verifies that the sandbox manager constructs Docker run commands with hardened security flags."""
    # Inspect SandboxManager run_in_sandbox implementation for security flags
    import inspect
    src = inspect.getsource(sandbox_manager.run_in_sandbox)
    assert "--cap-drop=ALL" in src
    assert "--security-opt=no-new-privileges:true" in src
    assert "--pids-limit" in src
    assert "--read-only" in src
    assert "--tmpfs" in src
    assert "--network" in src
    assert "none" in src
    assert "ps_sandbox_" in src


# ============================================================================
# 6. Replaced Internal Chain-of-Thought Tests
# ============================================================================

def test_agent_decision_investigation_summary_enforced():
    """AgentDecision accepts and enforces structured, user-facing InvestigationSummary."""
    decision = AgentDecision(
        investigation_summary=InvestigationSummary(
            investigation_focus="Investigating KeyError in customer transaction aggregation",
            tool_selection_reason="Inspect schema of data/transactions.csv to identify missing column",
            finding_summary="Log indicates KeyError at pipeline.py line 21",
            hypothesis_impact="Supports hypothesis HYP-01",
            next_steps="Call inspect_schema on transactions dataset",
            conclusion_rationale=None
        ),
        current_stage="failure_localization",
        action_type="call_tool",
        tool_call=ToolCallRequest(tool_name="inspect_schema", arguments={"data_path": "data/transactions.csv"}),
        confidence_score=0.75,
        confidence_justification="Stack trace pinpointed failure"
    )

    assert decision.investigation_summary.investigation_focus == "Investigating KeyError in customer transaction aggregation"
    assert decision.investigation_summary.tool_selection_reason == "Inspect schema of data/transactions.csv to identify missing column"
    assert "thinking" not in AgentDecision.model_fields


def test_agent_decision_legacy_thinking_rejected():
    """Legacy payloads with thinking only are rejected; investigation_summary is strictly required."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError) as exc_info:
        AgentDecision(
            thinking="Legacy thinking text from older mock test",
            current_stage="context_collection",
            action_type="conclude_inconclusive",
            confidence_score=0.2
        )
    assert "investigation_summary" in str(exc_info.value)
