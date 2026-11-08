"""
End-to-End and Engine Integration Tests
Tests bounded investigative loop, evidence recording, hypothesis revision,
patch review, sandbox containment, and failure modes.
"""
import pytest
from pathlib import Path
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from backend.app.database.session import Base
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, EvidenceRecord, Hypothesis, InvestigationEvent
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.agents.engine import InvestigationEngine
from backend.app.agents.provider import AIProvider, ModelProviderError
from backend.app.schemas.agent import AgentDecision, ToolCallRequest, AgentEvidenceInput, AgentHypothesisInput, AgentPatchInput, AgentReportInput
from backend.app.sandbox.manager import sandbox_manager
from backend.app.services.project_service import ProjectService
from backend.app.services.investigation_service import investigation_service


class MockAIProvider(AIProvider):
    """
    Mock AI Provider for deterministic testing of engine state transitions,
    hypothesis revision, tool-calling loop, and patch preparation.
    """
    def __init__(self, decisions: list[AgentDecision]):
        super().__init__()
        self.decisions = decisions
        self.call_count = 0

    async def generate_decision(self, prompt: str, system_instruction: str, temperature: float = 0.2) -> AgentDecision:
        if self.call_count < len(self.decisions):
            decision = self.decisions[self.call_count]
            self.call_count += 1
            return decision
        # Fallback conclusion
        return AgentDecision(
            thinking="Investigation budget reached.",
            current_stage="report_generation",
            action_type="conclude_inconclusive",
            final_report=AgentReportInput(
                title="Investigation Concluded",
                summary="Concluded with available data.",
                root_cause="Inconclusive",
                affected_components=[]
            ),
            confidence_score=0.4,
            confidence_justification="Limit reached"
        )


@pytest.fixture
async def test_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_investigation_with_model_unavailable(test_session: AsyncSession):
    # Setup project and investigation
    project = await ProjectService.create_project(test_session, name="Unreachable Model Test")
    inv = Investigation(project_id=project.id, objective="Investigate failure")
    test_session.add(inv)
    await test_session.commit()
    await test_session.refresh(inv)

    # Point to invalid Ollama port
    bad_provider = AIProvider(provider_type="ollama", ollama_base_url="http://127.0.0.1:99999")
    engine = InvestigationEngine(db=test_session, investigation_id=inv.id, ai_provider=bad_provider)

    await engine.run()

    # Investigation should cleanly transition to 'failed' with error event
    await test_session.refresh(inv)
    assert inv.status == "failed"

    ev_res = await test_session.execute(
        select(InvestigationEvent).where(InvestigationEvent.investigation_id == inv.id)
    )
    events = ev_res.scalars().all()
    error_events = [e for e in events if e.event_type == "error"]
    assert len(error_events) >= 1
    assert "AI Model Unavailable" in error_events[0].title


@pytest.mark.asyncio
async def test_investigation_loop_with_hypothesis_revision_and_patch(test_session: AsyncSession):
    # Load Scenario A project
    demo_dir = Path(__file__).parent.parent.parent / "demo" / "schema_drift"
    project = await ProjectService.create_project(
        test_session,
        name="Scenario A Drift Project",
        entry_point="pipeline.py"
    )

    # Copy demo files into project upload directory
    dest_dir = Path(__file__).parent.parent.parent / "storage" / "uploads" / project.id
    dest_dir.mkdir(parents=True, exist_ok=True)
    import shutil
    for item in demo_dir.iterdir():
        if item.is_file():
            shutil.copy2(item, dest_dir / item.name)
        elif item.is_dir():
            shutil.copytree(item, dest_dir / item.name, dirs_exist_ok=True)

    inv = Investigation(
        project_id=project.id,
        objective="Investigate KeyError in customer transaction pipeline"
    )
    test_session.add(inv)
    await test_session.commit()
    await test_session.refresh(inv)

    # Multi-step deterministic decision sequence:
    # Step 1: inspect log -> finds KeyError: customer_id. Formulates initial hypothesis HYP-1 (Data corruption)
    decision1 = AgentDecision(
        thinking="First, analyze logs to localize the exact exception and line number.",
        current_stage="failure_localization",
        hypotheses=[
            AgentHypothesisInput(
                title="Input data file is corrupted or empty",
                description="The data loading stage read an empty dataframe causing KeyError.",
                status="formulated",
                confidence=0.4,
                reasoning="Initial speculation before checking schema."
            )
        ],
        action_type="call_tool",
        tool_call=ToolCallRequest(
            tool_name="analyze_logs",
            arguments={"log_path": "logs/pipeline.log"}
        ),
        confidence_score=0.3,
        confidence_justification="Only log analyzed so far"
    )

    # Step 2: Log showed customer_id KeyError. Now inspect schema. Rejects initial hypothesis, formulates HYP-2 (Schema drift)
    decision2 = AgentDecision(
        thinking="The log shows KeyError: 'customer_id' at line 18 in transform_and_aggregate. Inspect schema to check columns.",
        current_stage="evidence_collection",
        hypotheses=[
            AgentHypothesisInput(
                title="Input data file is corrupted or empty",
                description="The data loading stage read an empty dataframe.",
                status="rejected",
                confidence=0.1,
                reasoning="Contradicted: log shows 6 records were ingested with columns ['client_id', 'amount', 'transaction_date', 'status']."
            ),
            AgentHypothesisInput(
                title="Schema drift: customer_id renamed to client_id",
                description="Upstream source renamed customer_id to client_id, breaking transformation.",
                status="testing",
                confidence=0.8,
                reasoning="Available columns contain client_id instead of customer_id."
            )
        ],
        new_evidence=[
            AgentEvidenceInput(
                source_file="logs/pipeline.log",
                evidence_type="log_stacktrace",
                line_number=18,
                execution_stage="transform_and_aggregate",
                observation="KeyError: 'customer_id' missing from transaction dataset with columns ['client_id', 'amount', 'transaction_date', 'status']"
            )
        ],
        action_type="call_tool",
        tool_call=ToolCallRequest(
            tool_name="inspect_schema",
            arguments={"dataset_path": "data/transactions.csv", "baseline_path": "data/baseline_schema.csv"}
        ),
        confidence_score=0.7,
        confidence_justification="Strong log stacktrace evidence"
    )

    # Step 3: Schema drift confirmed. Propose code patch.
    decision3 = AgentDecision(
        thinking="Schema diff proves client_id replaced customer_id. Formulating code correction in pipeline.py.",
        current_stage="patch_generation",
        hypotheses=[
            AgentHypothesisInput(
                title="Schema drift: customer_id renamed to client_id",
                description="Upstream source renamed customer_id to client_id.",
                status="confirmed",
                confidence=0.95,
                reasoning="Confirmed by schema comparison and log stacktrace."
            )
        ],
        new_evidence=[
            AgentEvidenceInput(
                source_file="data/transactions.csv",
                evidence_type="schema_diff",
                column_name="client_id",
                observation="Schema drift confirmed: missing customer_id, added client_id."
            )
        ],
        action_type="propose_patch",
        patch_proposal=AgentPatchInput(
            target_file="pipeline.py",
            original_code_context='if "customer_id" not in df.columns:',
            proposed_code='if "client_id" in df.columns:\n        df = df.rename(columns={"client_id": "customer_id"})\n    if "customer_id" not in df.columns:',
            unified_diff="",
            explanation="Rename client_id to customer_id if present to preserve backwards compatibility.",
            supporting_evidence_ids=[],
            expected_impact="Pipeline succeeds without KeyError",
            potential_risks="None"
        ),
        confidence_score=0.95,
        confidence_justification="Both log and schema diff confirm column rename"
    )

    mock_provider = MockAIProvider([decision1, decision2, decision3])
    engine = InvestigationEngine(db=test_session, investigation_id=inv.id, ai_provider=mock_provider)

    # Execute investigation
    await engine.run()

    # Verify status changed to awaiting_patch_approval
    await test_session.refresh(inv)
    assert inv.status == "awaiting_patch_approval"
    assert inv.stage == "awaiting_approval"

    # Verify evidence records
    ev_res = await test_session.execute(
        select(EvidenceRecord).where(EvidenceRecord.investigation_id == inv.id)
    )
    ev_list = ev_res.scalars().all()
    assert len(ev_list) == 2
    types = [e.evidence_type for e in ev_list]
    assert "log_stacktrace" in types
    assert "schema_diff" in types

    # Verify hypotheses: 1 rejected, 1 confirmed!
    hyp_res = await test_session.execute(
        select(Hypothesis).where(Hypothesis.investigation_id == inv.id)
    )
    hyp_list = hyp_res.scalars().all()
    assert any(h.status == "rejected" for h in hyp_list)
    assert any(h.status == "confirmed" for h in hyp_list)

    # Verify patch proposal exists and original file was NOT modified
    p_res = await test_session.execute(
        select(PatchProposal).where(PatchProposal.investigation_id == inv.id)
    )
    patch = p_res.scalars().first()
    assert patch is not None
    assert patch.status == "proposed"
    assert "rename" in patch.proposed_code
    assert "--- a/pipeline.py" in patch.unified_diff

    # Non-negotiable check: original file content must still have original code!
    original_file = dest_dir / "pipeline.py"
    orig_content = original_file.read_text()
    assert 'if "customer_id" not in df.columns:' in orig_content
    assert 'df.rename(columns={"client_id": "customer_id"})' not in orig_content


@pytest.mark.asyncio
async def test_missing_docker_security_boundary(test_session: AsyncSession):
    # Test sandbox refuses arbitrary host execution if Docker is not available
    res = await sandbox_manager.run_in_sandbox(
        workspace_path="/tmp",
        command=["python3", "-c", "print('hello')"]
    )
    # Since Docker is not running on this machine, it must return docker_available=False
    # and NOT execute unsafely on the host!
    assert res["docker_available"] is False
    assert res["exit_code"] == -1
    assert "EXECUTION BLOCKED BY SECURITY POLICY" in res["stderr"]
