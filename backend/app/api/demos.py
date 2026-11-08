"""
Demo Scenarios Loader API
Enables one-click loading of reproducible failure scenarios into PipelineSleuth.
"""
import shutil
from pathlib import Path
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from backend.app.core.config import settings
from backend.app.database.session import get_db
from backend.app.models.project import Project, ProjectFile
from backend.app.schemas.project import ProjectRead

router = APIRouter(prefix="/demos", tags=["demos"])

DEMO_SCENARIOS = {
    "schema_drift": {
        "key": "schema_drift",
        "name": "Scenario A: Schema Drift (Customer ID vs Client ID)",
        "description": "Customer transaction pipeline where an upstream rename from 'customer_id' to 'client_id' causes a fatal KeyError.",
        "directory": "schema_drift",
        "pipeline_type": "python_batch",
        "entry_point": "pipeline.py",
        "default_objective": "Investigate why the customer transaction pipeline crashed with a KeyError during the monthly spend aggregation stage."
    },
    "data_quality": {
        "key": "data_quality",
        "name": "Scenario B: Data Quality Failure (Unexpected Null Amounts)",
        "description": "Financial ledger aggregation pipeline halting on unexpected null values in critical financial amount fields.",
        "directory": "data_quality",
        "pipeline_type": "financial_ledger",
        "entry_point": "pipeline.py",
        "default_objective": "Determine why the ledger reconciliation pipeline failed with a data quality exception and propose an appropriate handling policy."
    },
    "type_mismatch": {
        "key": "type_mismatch",
        "name": "Scenario C: Type Mismatch (Dirty Strings in Numeric Float)",
        "description": "Revenue aggregation pipeline where non-numeric values ('pending', 'N/A') in transaction amounts cause a float conversion failure.",
        "directory": "type_mismatch",
        "pipeline_type": "revenue_aggregation",
        "entry_point": "pipeline.py",
        "default_objective": "Investigate the float conversion failure on transaction amounts and implement robust numeric parsing."
    }
}


@router.get("")
async def list_demos():
    return list(DEMO_SCENARIOS.values())


@router.post("/{scenario_key}/load", response_model=ProjectRead)
async def load_demo_scenario(scenario_key: str, db: AsyncSession = Depends(get_db)):
    scenario = DEMO_SCENARIOS.get(scenario_key)
    if not scenario:
        raise HTTPException(status_code=404, detail=f"Scenario '{scenario_key}' not found.")

    source_dir = settings.DEMO_DIR / scenario["directory"]
    if not source_dir.exists():
        raise HTTPException(status_code=500, detail=f"Demo directory '{source_dir}' missing.")

    # 1. Create project record
    project = Project(
        name=scenario["name"],
        description=scenario["description"],
        pipeline_type=scenario["pipeline_type"],
        entry_point=scenario["entry_point"]
    )
    db.add(project)
    await db.commit()
    await db.refresh(project)

    # 2. Copy scenario files to storage/uploads/{project.id}
    target_dir = settings.UPLOADS_DIR / project.id
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_files: List[ProjectFile] = []
    for item in source_dir.rglob("*"):
        if item.is_file() and not item.name.startswith("."):
            rel_path = item.relative_to(source_dir)
            dest_file = target_dir / rel_path
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dest_file)

            ext = dest_file.suffix.lower()
            category = "code"
            if ext in (".csv", ".parquet"):
                category = "data"
            elif ext in (".log", ".txt") and ("log" in dest_file.name.lower() or "err" in dest_file.name.lower()):
                category = "log"
            elif ext in (".yaml", ".yml", ".ini"):
                category = "config"
            elif dest_file.name.startswith("test_") or dest_file.name.endswith("_test.py"):
                category = "test"

            pf = ProjectFile(
                project_id=project.id,
                relative_path=str(rel_path),
                file_type=category,
                size_bytes=dest_file.stat().st_size,
                storage_path=str(dest_file),
                metadata_json={"demo_scenario": scenario_key}
            )
            db.add(pf)
            saved_files.append(pf)

    await db.commit()
    
    # Reload with files eagerly loaded to prevent async lazy loading in serialization
    loaded_res = await db.execute(
        select(Project).options(selectinload(Project.files)).where(Project.id == project.id)
    )
    return loaded_res.scalar_one()
