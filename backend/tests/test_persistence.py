"""
Database and Persistence tests
"""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import select

from backend.app.database.session import Base
from backend.app.models.project import Project, ProjectFile
from backend.app.models.investigation import Investigation, EvidenceRecord, Hypothesis, InvestigationEvent
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.report import InvestigationReport


@pytest_asyncio.fixture
async def test_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async_session = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session() as session:
        yield session
    
    await engine.dispose()


@pytest.mark.asyncio
async def test_project_crud(test_db: AsyncSession):
    project = Project(name="Test Pipeline", description="Testing DB", pipeline_type="python_batch")
    test_db.add(project)
    await test_db.commit()
    await test_db.refresh(project)

    assert project.id is not None
    assert project.name == "Test Pipeline"

    # Add file
    file = ProjectFile(
        project_id=project.id,
        relative_path="pipeline.py",
        file_type="code",
        size_bytes=1024,
        storage_path="/tmp/pipeline.py"
    )
    test_db.add(file)
    await test_db.commit()

    res = await test_db.execute(select(Project).where(Project.id == project.id))
    fetched = res.scalar_one()
    assert fetched.name == "Test Pipeline"


@pytest.mark.asyncio
async def test_investigation_lifecycle_persistence(test_db: AsyncSession):
    proj = Project(name="Drift Test")
    test_db.add(proj)
    await test_db.commit()

    inv = Investigation(
        project_id=proj.id,
        objective="Find root cause of KeyError"
    )
    test_db.add(inv)
    await test_db.commit()
    await test_db.refresh(inv)

    # Add hypothesis
    hyp = Hypothesis(
        investigation_id=inv.id,
        title="Column renamed upstream",
        description="client_id replaced customer_id",
        status="formulated",
        confidence=0.6
    )
    test_db.add(hyp)
    await test_db.commit()
    await test_db.refresh(hyp)

    # Add evidence
    evd = EvidenceRecord(
        investigation_id=inv.id,
        source_file="data/transactions.csv",
        evidence_type="schema_diff",
        observation="Missing column customer_id, found client_id",
        associated_hypothesis_id=hyp.id
    )
    test_db.add(evd)
    await test_db.commit()

    # Query back
    evd_res = await test_db.execute(select(EvidenceRecord).where(EvidenceRecord.investigation_id == inv.id))
    records = evd_res.scalars().all()
    assert len(records) == 1
    assert records[0].associated_hypothesis_id == hyp.id
