"""
Regression & Integration Tests for End-to-End Integration Blockers:
1. Investigation creation & background AsyncSession lifecycle
2. Candidate patch application & original project immutability during validation
3. Truthful, idempotent InvestigationReport lifecycle and schema uniqueness migration
"""
import asyncio
import secrets
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.core.config import settings
from backend.app.database.session import (
    Base,
    check_unique_single_column_index,
    get_db,
    sync_upgrade_schema,
)
from backend.app.main import app
from backend.app.models.investigation import EvidenceRecord, Hypothesis, Investigation
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.project import Project, ProjectFile
from backend.app.models.report import InvestigationReport
from backend.app.agents.engine import InvestigationEngine
from backend.app.agents.provider import AIProvider
from backend.app.schemas.agent import (
    AgentDecision,
    AgentEvidenceInput,
    AgentHypothesisInput,
    AgentPatchInput,
    InvestigationSummary,
    ToolCallRequest,
)
from backend.app.services.investigation_service import investigation_service
from backend.app.services.report_service import generate_or_update_investigation_report
from backend.app.services.validation_service import privileged_validation_service
from backend.app.tools.validate_patch import ValidatePatchTool


async def _create_isolated_db(db_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(sync_upgrade_schema)
    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    return engine, session_factory


async def _seed_project_with_bug(
    session: AsyncSession,
    uploads_dir: Path,
    project_id: str = "proj-buggy-01",
    buggy: bool = True,
) -> tuple[Project, Path]:
    proj_dir = uploads_dir / project_id
    proj_dir.mkdir(parents=True, exist_ok=True)
    pipeline_py = proj_dir / "pipeline.py"
    if buggy:
        pipeline_py.write_text(
            "def compute_ratio(total: float, count: int) -> float:\n"
            "    return total / count\n",
            encoding="utf-8",
        )
    else:
        pipeline_py.write_text(
            "def compute_ratio(total: float, count: int) -> float:\n"
            "    return total / count if count else 0.0\n",
            encoding="utf-8",
        )
    test_py = proj_dir / "test_pipeline.py"
    test_py.write_text(
        "from pipeline import compute_ratio\n\n"
        "def test_zero_count_handled():\n"
        "    assert compute_ratio(10.0, 0) == 0.0\n",
        encoding="utf-8",
    )

    project = Project(
        id=project_id,
        name="Zero Division Pipeline",
        description="ETL ratio calculator",
        pipeline_type="python_batch",
        entry_point="pipeline.py",
    )
    session.add(project)
    session.add(
        ProjectFile(
            project_id=project.id,
            relative_path="pipeline.py",
            file_type="code",
            size_bytes=pipeline_py.stat().st_size,
            storage_path=str(pipeline_py),
        )
    )
    session.add(
        ProjectFile(
            project_id=project.id,
            relative_path="test_pipeline.py",
            file_type="test",
            size_bytes=test_py.stat().st_size,
            storage_path=str(test_py),
        )
    )
    await session.commit()
    return project, proj_dir


class _MockPatchProposingAI(AIProvider):
    """Minimal deterministic AIProvider stub for driving an investigation to propose_patch."""

    def __init__(self, target_file: str, orig: str, proposed: str):
        super().__init__()
        self.step = 0
        self.target_file = target_file
        self.orig = orig
        self.proposed = proposed

    async def generate_decision(
        self, prompt: str, system_instruction: str = "", temperature: float = 0.2
    ) -> AgentDecision:
        self.step += 1
        if self.step == 1:
            return AgentDecision(
                investigation_summary=InvestigationSummary(
                    investigation_focus="Inspect pipeline.py AST to locate compute_ratio definition",
                    tool_selection_reason="Need structural view of pipeline.py",
                    finding_summary="pipeline.py uploaded in project directory",
                    hypothesis_impact="Initial hypothesis formulated",
                    next_steps="Inspect AST of pipeline.py",
                    conclusion_rationale="Gathering initial code structure evidence",
                ),
                current_stage="context_collection",
                action_type="call_tool",
                tool_call=ToolCallRequest(
                    tool_name="inspect_code_ast",
                    arguments={"file_path": self.target_file},
                ),
                hypotheses=[
                    AgentHypothesisInput(
                        title="Division by zero when count == 0",
                        description="compute_ratio divides directly by count without zero guard",
                        status="testing",
                        confidence=0.6,
                        reasoning="Inspecting function AST",
                    )
                ],
                confidence_score=0.5,
                confidence_justification="Starting AST inspection",
            )
        return AgentDecision(
            investigation_summary=InvestigationSummary(
                investigation_focus="Propose zero-division guard for compute_ratio in pipeline.py",
                tool_selection_reason=None,
                finding_summary="AST confirms function compute_ratio in pipeline.py",
                hypothesis_impact="Confirms hypothesis H1",
                next_steps="Propose candidate patch for human approval",
                conclusion_rationale="Root cause isolated in compute_ratio",
            ),
            current_stage="patch_generation",
            action_type="propose_patch",
            new_evidence=[
                AgentEvidenceInput(
                    source_file=self.target_file,
                    evidence_type="ast_inspection",
                    line_number=1,
                    observation="Function compute_ratio is defined in pipeline.py",
                    is_inference=False,
                )
            ],
            hypotheses=[
                AgentHypothesisInput(
                    title="Division by zero when count == 0",
                    description="compute_ratio fails on zero count and needs safe guard",
                    status="confirmed",
                    confidence=0.95,
                    reasoning="Confirmed compute_ratio divides without checking count",
                )
            ],
            patch_proposal=AgentPatchInput(
                target_file=self.target_file,
                original_code_context=self.orig,
                proposed_code=self.proposed,
                unified_diff="",
                explanation="Return 0.0 when count is falsy",
                supporting_evidence_ids=[],
                expected_impact="Prevents ZeroDivisionError",
                potential_risks="Silently returns 0.0 instead of raising",
            ),
            confidence_score=0.95,
            confidence_justification="AST inspection confirms compute_ratio structure",
        )


# ---------------------------------------------------------------------------
# PART 1: Investigation creation & background session lifecycle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_investigations_persists_and_runs_independent_session(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part1.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async with session_factory() as seed_db:
        await _seed_project_with_bug(seed_db, uploads_dir, project_id="proj-part1")

    request_sessions = []
    bg_sessions = []
    observed_in_run = {}

    async def override_get_db():
        async with session_factory() as s:
            request_sessions.append(s)
            try:
                yield s
            finally:
                await s.close()

    @asynccontextmanager
    async def tracked_bg_session_local():
        s = session_factory()
        bg_sessions.append(s)
        try:
            yield s
        finally:
            await s.close()

    async def fake_engine_run(self):
        # Verify the investigation record is already committed & visible to independent session
        res = await self.db.execute(
            select(Investigation).where(Investigation.id == self.investigation_id)
        )
        inv = res.scalar_one_or_none()
        observed_in_run["inv_found_before_run"] = inv is not None
        observed_in_run["engine_session"] = self.db
        observed_in_run["engine_shares_request_session"] = self.db in request_sessions
        if inv:
            inv.status = "inconclusive"
            inv.stage = "report_generation"
            await self.db.commit()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(
        "backend.app.services.investigation_service.AsyncSessionLocal",
        tracked_bg_session_local,
    )
    monkeypatch.setattr(InvestigationEngine, "run", fake_engine_run)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/api/investigations",
                json={"project_id": "proj-part1", "objective": "Investigate divide by zero failure"},
            )

        assert resp.status_code == 200
        body = resp.json()
        inv_id = body["id"]
        assert body["project_id"] == "proj-part1"
        assert body["status"] == "created"

        bg_task = investigation_service._active_investigations.get(inv_id)
        if bg_task is not None:
            await asyncio.wait_for(bg_task, timeout=5.0)

        assert observed_in_run["inv_found_before_run"] is True
        assert observed_in_run["engine_shares_request_session"] is False
        assert len(bg_sessions) == 1
        assert bg_sessions[0].in_transaction() is False

        async with session_factory() as check_db:
            persisted = (
                await check_db.execute(select(Investigation).where(Investigation.id == inv_id))
            ).scalar_one()
            assert persisted.status == "inconclusive"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_post_investigations_missing_project_returns_404_and_no_task_scheduled(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part1_404.db")

    async def override_get_db():
        async with session_factory() as s:
            yield s

    scheduled = []

    def fail_if_scheduled(*args, **kwargs):
        scheduled.append((args, kwargs))
        raise AssertionError("Background task should never be scheduled for missing project")

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(investigation_service, "dispatch_investigation_background", fail_if_scheduled)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                "/api/investigations",
                json={"project_id": "does-not-exist", "objective": "Investigate missing project"},
            )
        assert resp.status_code == 404
        assert scheduled == []
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_start_investigation_commit_failure_does_not_schedule_task(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part1_commit_err.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async with session_factory() as seed_db:
        await _seed_project_with_bug(seed_db, uploads_dir, project_id="proj-commit-fail")

    scheduled = []

    def spy_dispatch(*args, **kwargs):
        scheduled.append((args, kwargs))

    monkeypatch.setattr(investigation_service, "dispatch_investigation_background", spy_dispatch)

    try:
        async with session_factory() as session:
            async def failing_commit():
                raise RuntimeError("Simulated commit failure")

            monkeypatch.setattr(session, "commit", failing_commit)
            with pytest.raises(RuntimeError, match="Simulated commit failure"):
                await investigation_service.start_investigation(
                    db=session,
                    project_id="proj-commit-fail",
                    objective="Should abort cleanly before dispatch",
                )
        assert scheduled == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_background_investigation_session_cleanup_on_error_and_cancellation(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part1_cleanup.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async with session_factory() as seed_db:
        await _seed_project_with_bug(seed_db, uploads_dir, project_id="proj-cleanup")
        inv_err = Investigation(project_id="proj-cleanup", objective="Error path test")
        inv_cancel = Investigation(project_id="proj-cleanup", objective="Cancel path test")
        seed_db.add_all([inv_err, inv_cancel])
        await seed_db.commit()

    closed_sessions = []

    @asynccontextmanager
    async def tracked_session_local():
        s = session_factory()
        try:
            yield s
        finally:
            await s.close()
            closed_sessions.append(s)

    monkeypatch.setattr(
        "backend.app.services.investigation_service.AsyncSessionLocal",
        tracked_session_local,
    )

    # 1. Error path
    async def run_raises(self):
        raise RuntimeError("Simulated engine boom")

    monkeypatch.setattr(InvestigationEngine, "run", run_raises)
    events = []
    q = investigation_service.subscribe(inv_err.id)
    try:
        await investigation_service._run_investigation_background(inv_err.id)
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        investigation_service.unsubscribe(inv_err.id, q)

    assert len(closed_sessions) == 2  # engine session + recovery session
    assert any(ev["event_type"] == "investigation_failed" for ev in events)

    async with session_factory() as verify_db:
        refreshed = (
            await verify_db.execute(select(Investigation).where(Investigation.id == inv_err.id))
        ).scalar_one()
        assert refreshed.status == "failed"
        assert refreshed.stage == "report_generation"
        assert "Simulated engine boom" in (refreshed.root_cause_summary or "")

    # 2. Cancellation path
    closed_sessions.clear()

    async def run_cancels(self):
        raise asyncio.CancelledError()

    monkeypatch.setattr(InvestigationEngine, "run", run_cancels)
    with pytest.raises(asyncio.CancelledError):
        await investigation_service._run_investigation_background(inv_cancel.id)

    assert len(closed_sessions) == 1
    assert closed_sessions[0].in_transaction() is False

    await engine.dispose()


# ---------------------------------------------------------------------------
# PART 2: Actual patch application & original project immutability
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_validate_patch_tool_patches_candidate_runs_tests_leaves_original_untouched(
    tmp_path: Path, monkeypatch
):
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)

    orig_proj = uploads_dir / "proj-real-patch"
    orig_proj.mkdir()
    orig_code = "def compute_ratio(total: float, count: int) -> float:\n    return total / count"
    fixed_code = "def compute_ratio(total: float, count: int) -> float:\n    return total / count if count else 0.0"

    (orig_proj / "pipeline.py").write_text(orig_code + "\n", encoding="utf-8")
    (orig_proj / "test_pipeline.py").write_text(
        "from pipeline import compute_ratio\n\n"
        "def test_ratio():\n"
        "    assert compute_ratio(10.0, 0) == 0.0\n",
        encoding="utf-8",
    )

    observed = {}

    async def isolated_subprocess_runner(workspace_path: str, command, timeout_seconds=None):
        ws = Path(workspace_path)
        observed["workspace_path"] = ws
        observed["candidate_file_content"] = (ws / "pipeline.py").read_text(encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(ws),
            capture_output=True,
            text=True,
        )
        return {
            "success": proc.returncode == 0,
            "docker_available": True,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }

    monkeypatch.setattr(
        "backend.app.tools.validate_patch.sandbox_manager.run_in_sandbox",
        isolated_subprocess_runner,
    )

    tool = ValidatePatchTool()
    res = await tool.execute(
        project_path=str(orig_proj),
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": orig_code,
            "replacement_code": fixed_code,
            "test_command": ["pytest"],
        },
    )

    assert res.success is True
    assert res.data["patch_applied"] is True
    assert res.data["validation_status"] == "passed"
    assert res.data["test_summary"]["passed"] == 1
    assert observed["workspace_path"] != orig_proj
    assert fixed_code in observed["candidate_file_content"]
    # Candidate workspace cleaned up and original uploaded project remained completely untouched
    assert not observed["workspace_path"].exists()
    assert orig_code in (orig_proj / "pipeline.py").read_text(encoding="utf-8")
    assert "if count else 0.0" not in (orig_proj / "pipeline.py").read_text(encoding="utf-8")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "orig_snippet,repl_code,expected_err",
    [
        ("", "def x(): return 1", "original_snippet"),
        ("   \n", "def x(): return 1", "original_snippet"),
        ("def compute_ratio", "", "replacement_code"),
        ("def compute_ratio", "   \t", "replacement_code"),
        ("nonexistent_symbol_xyz", "def y(): pass", "Snippet mismatch"),
        ("return total / count", "return total / count", "No-op"),
    ],
)
async def test_validate_patch_tool_rejects_invalid_or_noop_inputs(
    tmp_path: Path, monkeypatch, orig_snippet: str, repl_code: str, expected_err: str
):
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)

    proj = uploads_dir / "proj-reject-invalid"
    proj.mkdir()
    (proj / "pipeline.py").write_text(
        "def compute_ratio(total, count):\n    return total / count\n",
        encoding="utf-8",
    )

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("Sandbox execution must not run when patch application is rejected")

    monkeypatch.setattr("backend.app.tools.validate_patch.sandbox_manager.run_in_sandbox", fail_if_called)

    tool = ValidatePatchTool()
    res = await tool.execute(
        project_path=str(proj),
        arguments={
            "target_file": "pipeline.py",
            "original_snippet": orig_snippet,
            "replacement_code": repl_code,
            "test_command": ["pytest"],
        },
    )

    assert res.success is False
    assert res.data["patch_applied"] is False
    assert expected_err in (res.error or "")


@pytest.mark.asyncio
async def test_validation_service_applies_patch_to_candidate_and_records_pass(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part2_service.db")
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)
    monkeypatch.setattr("backend.app.services.validation_service.AsyncSessionLocal", session_factory)

    orig_snippet = "    return total / count"
    repl_snippet = "    return total / count if count else 0.0"

    async with session_factory() as db:
        proj, proj_dir = await _seed_project_with_bug(db, uploads_dir, project_id="proj-svc-pass")
        inv = Investigation(project_id=proj.id, objective="Fix zero division", status="validating")
        db.add(inv)
        await db.commit()

        patch_rec = PatchProposal(
            investigation_id=inv.id,
            target_file="pipeline.py",
            original_code_context=orig_snippet,
            proposed_code=repl_snippet,
            unified_diff="--- a/pipeline.py\n+++ b/pipeline.py\n",
            explanation="Handle count == 0 safely",
            status="approved",
            approved_by="operator-1",
            approved_at=datetime.now(timezone.utc),
        )
        db.add(patch_rec)
        await db.commit()

        async def run_pytest_on_candidate(workspace_path: str, command, timeout_seconds=None):
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
            )
            return {
                "success": proc.returncode == 0,
                "docker_available": True,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }

        monkeypatch.setattr(
            "backend.app.tools.validate_patch.sandbox_manager.run_in_sandbox",
            run_pytest_on_candidate,
        )

        # Verify original unpatched file STILL fails pytest independently
        orig_proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=str(proj_dir),
            capture_output=True,
            text=True,
        )
        assert orig_proc.returncode != 0

        val_run = await privileged_validation_service.execute_approved_patch_validation(
            db=db,
            investigation_id=inv.id,
            patch_id=patch_rec.id,
        )

        assert val_run.status == "passed"
        assert val_run.exit_code == 0
        assert val_run.test_summary["passed"] == 1
        assert val_run.test_summary["patch_applied"] is True
        await db.refresh(patch_rec)
        await db.refresh(inv)
        assert patch_rec.status == "validated"
        assert inv.status == "patch_validated"
        # Original file remains untouched
        assert "if count else 0.0" not in (proj_dir / "pipeline.py").read_text(encoding="utf-8")

    await engine.dispose()


# ---------------------------------------------------------------------------
# PART 3: Report generation lifecycle, accuracy, rejection, skip, idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_human_approval_gate_and_report_after_passed_validation(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_gate.db")
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)
    monkeypatch.setattr("backend.app.services.validation_service.AsyncSessionLocal", session_factory)

    async def override_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    try:
        async with session_factory() as db:
            proj, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-gate")
            inv = Investigation(project_id=proj.id, objective="Investigate divide by zero")
            db.add(inv)
            await db.commit()

            ai = _MockPatchProposingAI(
                target_file="pipeline.py",
                orig="    return total / count",
                proposed="    return total / count if count else 0.0",
            )
            inv_engine = InvestigationEngine(db=db, investigation_id=inv.id, ai_provider=ai)
            await inv_engine.run()

            await db.refresh(inv)
            assert inv.status == "awaiting_patch_approval"

            # Human approval gate: NO report may exist before patch validation completes
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                pre_rep = await client.get(f"/api/investigations/{inv.id}/report")
                assert pre_rep.status_code == 404

            patch_rec = (
                await db.execute(select(PatchProposal).where(PatchProposal.investigation_id == inv.id))
            ).scalar_one()

            # Approve patch via service
            token = secrets.token_urlsafe(32)
            monkeypatch.setattr(settings, "OPERATOR_API_KEY", token)
            monkeypatch.setattr(settings, "OPERATOR_TOKENS_JSON", None)

            # Prevent background task from racing with direct execution call below
            async def noop_dispatch(inv_id, p_id):
                return True

            monkeypatch.setattr(investigation_service, "dispatch_validation_background", noop_dispatch)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                approve_resp = await client.post(
                    f"/api/investigations/{inv.id}/patches/{patch_rec.id}/action",
                    json={"action": "approve"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert approve_resp.status_code == 200

            async def passing_sandbox(workspace_path: str, command, timeout_seconds=None):
                proc = subprocess.run(
                    [sys.executable, "-m", "pytest", "-q"],
                    cwd=workspace_path,
                    capture_output=True,
                    text=True,
                )
                return {
                    "success": proc.returncode == 0,
                    "docker_available": True,
                    "exit_code": proc.returncode,
                    "stdout": proc.stdout,
                    "stderr": proc.stderr,
                }

            monkeypatch.setattr(
                "backend.app.tools.validate_patch.sandbox_manager.run_in_sandbox",
                passing_sandbox,
            )

            async with session_factory() as val_db:
                await privileged_validation_service.execute_approved_patch_validation(
                    db=val_db,
                    investigation_id=inv.id,
                    patch_id=patch_rec.id,
                )

            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
                post_rep = await client.get(f"/api/investigations/{inv.id}/report")
                assert post_rep.status_code == 200
                rep_json = post_rep.json()
                assert rep_json["validation_summary"]["validated"] is True
                assert rep_json["validation_summary"]["status"] == "passed"
                assert rep_json["proposed_correction"]["patch_id"] == patch_rec.id
                assert "VALIDATED" in rep_json["markdown_content"]
                assert len(rep_json["supporting_evidence"]) >= 1
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_report_reflects_failed_validation_never_successful(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_fail_skip.db")
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)
    monkeypatch.setattr("backend.app.services.validation_service.AsyncSessionLocal", session_factory)

    async with session_factory() as db:
        # 1. Failed validation case (patch still breaks assertion)
        proj_fail, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-fail-val")
        inv_fail = Investigation(project_id=proj_fail.id, objective="Fix failure (bad patch)")
        db.add(inv_fail)
        await db.commit()

        bad_patch = PatchProposal(
            investigation_id=inv_fail.id,
            target_file="pipeline.py",
            original_code_context="    return total / count",
            proposed_code="    return total / count if count else -1.0",  # still fails == 0.0 test
            unified_diff="diff",
            explanation="Wrong fallback value",
            status="approved",
            approved_by="op-fail",
            approved_at=datetime.now(timezone.utc),
        )
        db.add(bad_patch)
        await db.commit()

        async def run_real_pytest(workspace_path: str, command, timeout_seconds=None):
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"],
                cwd=workspace_path,
                capture_output=True,
                text=True,
            )
            return {
                "success": proc.returncode == 0,
                "docker_available": True,
                "exit_code": proc.returncode,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            }

        monkeypatch.setattr(
            "backend.app.tools.validate_patch.sandbox_manager.run_in_sandbox",
            run_real_pytest,
        )

        val_fail = await privileged_validation_service.execute_approved_patch_validation(
            db=db,
            investigation_id=inv_fail.id,
            patch_id=bad_patch.id,
        )
        assert val_fail.status == "failed"

        rep_fail = (
            await db.execute(
                select(InvestigationReport).where(InvestigationReport.investigation_id == inv_fail.id)
            )
        ).scalar_one()
        assert rep_fail.validation_summary["validated"] is False
        assert rep_fail.validation_summary["status"] == "failed"
        assert "VALIDATION_FAILED" in rep_fail.markdown_content

    await engine.dispose()


@pytest.mark.asyncio
async def test_report_reflects_skipped_validation_when_docker_unavailable(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_skip.db")
    uploads_dir = tmp_path / "uploads"
    workspaces_dir = tmp_path / "workspaces"
    uploads_dir.mkdir()
    workspaces_dir.mkdir()
    monkeypatch.setattr(settings, "UPLOADS_DIR", uploads_dir)
    monkeypatch.setattr(settings, "WORKSPACES_DIR", workspaces_dir)
    monkeypatch.setattr("backend.app.sandbox.manager.sandbox_manager.workspaces_root", workspaces_dir)
    monkeypatch.setattr("backend.app.services.validation_service.AsyncSessionLocal", session_factory)

    async with session_factory() as db:
        proj_skip, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-skip-val")
        inv_skip = Investigation(project_id=proj_skip.id, objective="Fix failure without Docker")
        db.add(inv_skip)
        await db.commit()

        skip_patch = PatchProposal(
            investigation_id=inv_skip.id,
            target_file="pipeline.py",
            original_code_context="    return total / count",
            proposed_code="    return total / count if count else 0.0",
            unified_diff="diff",
            explanation="Guarded denominator",
            status="approved",
            approved_by="op-skip",
            approved_at=datetime.now(timezone.utc),
        )
        db.add(skip_patch)
        await db.commit()

        with patch(
            "backend.app.sandbox.manager.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("docker"),
        ):
            val_skip = await privileged_validation_service.execute_approved_patch_validation(
                db=db,
                investigation_id=inv_skip.id,
                patch_id=skip_patch.id,
            )

        assert val_skip.status == "failed"
        assert val_skip.test_summary["docker_available"] is False
        assert val_skip.test_summary["validation_status"] == "skipped"

        rep_skip = (
            await db.execute(
                select(InvestigationReport).where(InvestigationReport.investigation_id == inv_skip.id)
            )
        ).scalar_one()
        assert rep_skip.validation_summary["validated"] is False
        assert rep_skip.validation_summary["status"] == "skipped"
        assert rep_skip.validation_summary["docker_available"] is False
        assert "VALIDATION_SKIPPED" in rep_skip.markdown_content
        assert "Docker" in rep_skip.summary

    await engine.dispose()


@pytest.mark.asyncio
async def test_report_generated_when_patch_rejected_via_api(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_reject.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    token = secrets.token_urlsafe(32)
    monkeypatch.setattr(settings, "OPERATOR_API_KEY", token)
    monkeypatch.setattr(settings, "OPERATOR_TOKENS_JSON", None)

    async def override_get_db():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_db] = override_get_db

    try:
        async with session_factory() as db:
            proj, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-reject-rep")
            inv = Investigation(
                project_id=proj.id,
                objective="Test rejection report creation",
                status="awaiting_patch_approval",
                stage="awaiting_approval",
                confidence_score=0.85,
            )
            db.add(inv)
            await db.commit()

            patch_rec = PatchProposal(
                investigation_id=inv.id,
                target_file="pipeline.py",
                original_code_context="    return total / count",
                proposed_code="    return 0.0",
                unified_diff="diff",
                explanation="Too broad change",
                status="proposed",
            )
            db.add(patch_rec)
            await db.commit()

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            rej_resp = await client.post(
                f"/api/investigations/{inv.id}/patches/{patch_rec.id}/action",
                json={
                    "action": "reject",
                    "reason": "Changes non-zero count semantics",
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert rej_resp.status_code == 200
            assert rej_resp.json()["status"] == "rejected"

            rep_resp = await client.get(f"/api/investigations/{inv.id}/report")
            assert rep_resp.status_code == 200
            rep_json = rep_resp.json()
            assert rep_json["validation_summary"]["validated"] is False
            assert rep_json["validation_summary"]["status"] == "not_run"
            assert rep_json["proposed_correction"]["status"] == "rejected"
            assert "PATCH_REJECTED" in rep_json["markdown_content"]
            assert "Changes non-zero count semantics" in rep_json["markdown_content"]
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


@pytest.mark.asyncio
async def test_report_generation_idempotent_and_unique_migration(tmp_path: Path):
    # 1. Idempotent generation via service keeps 1 row
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_idempotent.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    async with session_factory() as db:
        proj, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-idem")
        inv = Investigation(project_id=proj.id, objective="Idempotency check")
        db.add(inv)
        await db.commit()

        db.add(
            EvidenceRecord(
                investigation_id=inv.id,
                source_file="pipeline.py",
                evidence_type="ast_inspection",
                observation="compute_ratio defined cleanly",
                verification_status="verified",
            )
        )
        db.add(
            Hypothesis(
                investigation_id=inv.id,
                title="Missing zero check",
                description="Need zero guard",
                status="confirmed",
                confidence=0.9,
            )
        )
        await db.commit()

        r1 = await generate_or_update_investigation_report(db, inv.id, is_success_hint=False)
        r2 = await generate_or_update_investigation_report(db, inv.id, is_success_hint=False)
        assert r1 is not None and r2 is not None
        assert r1.id == r2.id

        cnt = (
            await db.execute(
                select(func.count(InvestigationReport.id)).where(
                    InvestigationReport.investigation_id == inv.id
                )
            )
        ).scalar_one()
        assert cnt == 1

    await engine.dispose()

    # 2. Legacy schema upgrade deduplicates pre-existing duplicate reports and creates unique index
    legacy_db_path = tmp_path / "legacy_reports.db"
    legacy_engine = create_async_engine(f"sqlite+aiosqlite:///{legacy_db_path}", echo=False)
    async with legacy_engine.begin() as conn:
        await conn.execute(text("CREATE TABLE projects (id VARCHAR(36) PRIMARY KEY)"))
        await conn.execute(
            text("CREATE TABLE investigations (id VARCHAR(36) PRIMARY KEY, project_id VARCHAR(36))")
        )
        await conn.execute(
            text(
                "CREATE TABLE investigation_reports ("
                "id VARCHAR(36) PRIMARY KEY, "
                "investigation_id VARCHAR(36) NOT NULL, "
                "title VARCHAR(255) NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO investigation_reports (id, investigation_id, title, created_at) "
                "VALUES ('REP-OLD', 'INV-1', 'Old Title', '2026-01-01 00:00:00')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO investigation_reports (id, investigation_id, title, created_at) "
                "VALUES ('REP-NEW', 'INV-1', 'Latest Title', '2026-01-02 00:00:00')"
            )
        )

    async with legacy_engine.begin() as conn:
        await conn.run_sync(sync_upgrade_schema)
        rows = (await conn.execute(text("SELECT id, title FROM investigation_reports"))).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "REP-NEW"
        has_uniq = await conn.run_sync(
            lambda sync_conn: check_unique_single_column_index(
                sync_conn, "investigation_reports", "investigation_id"
            )
        )
        assert has_uniq is True

    await legacy_engine.dispose()


@pytest.mark.asyncio
async def test_background_validation_system_error_generates_failed_report(tmp_path: Path, monkeypatch):
    engine, session_factory = await _create_isolated_db(tmp_path / "part3_bg_recovery.db")
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    monkeypatch.setattr(
        "backend.app.services.investigation_service.AsyncSessionLocal",
        session_factory,
    )
    monkeypatch.setattr(
        "backend.app.services.validation_service.AsyncSessionLocal",
        session_factory,
    )

    async with session_factory() as db:
        proj, _ = await _seed_project_with_bug(db, uploads_dir, project_id="proj-bg-err")
        inv = Investigation(project_id=proj.id, objective="Recover from validation error")
        db.add(inv)
        await db.commit()

        patch_rec = PatchProposal(
            investigation_id=inv.id,
            target_file="pipeline.py",
            original_code_context="    return total / count",
            proposed_code="    return total / count if count else 0.0",
            unified_diff="diff",
            explanation="Guarded denominator",
            status="approved",
            approved_by="op-bg",
            approved_at=datetime.now(timezone.utc),
        )
        db.add(patch_rec)
        await db.commit()

    async def exploding_validation(*args, **kwargs):
        raise OSError("Simulated workspace failure")

    monkeypatch.setattr(
        privileged_validation_service,
        "execute_approved_patch_validation",
        exploding_validation,
    )

    events = []
    q = investigation_service.subscribe(inv.id)
    try:
        await investigation_service._validate_patch_background(inv.id, patch_rec.id)
        while not q.empty():
            events.append(q.get_nowait())
    finally:
        investigation_service.unsubscribe(inv.id, q)

    assert any(ev["event_type"] == "validation_result" for ev in events)
    report_events = [ev for ev in events if ev["event_type"] == "report_ready"]
    assert len(report_events) == 1
    assert report_events[0]["payload"]["is_success"] is False

    async with session_factory() as check_db:
        p = (await check_db.execute(select(PatchProposal).where(PatchProposal.id == patch_rec.id))).scalar_one()
        i = (await check_db.execute(select(Investigation).where(Investigation.id == inv.id))).scalar_one()
        vr = (await check_db.execute(select(ValidationRun).where(ValidationRun.patch_id == patch_rec.id))).scalar_one()
        rep = (
            await check_db.execute(select(InvestigationReport).where(InvestigationReport.investigation_id == inv.id))
        ).scalar_one()

        assert p.status == "validation_failed"
        assert i.status == "patch_validation_failed"
        assert i.stage == "report_generation"
        assert vr.status == "failed"
        assert rep.validation_summary["validated"] is False
        assert rep.validation_summary["status"] == "failed"

    await engine.dispose()
