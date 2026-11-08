"""
Investigation Lifecycle and Event Streaming Service
"""
import asyncio
from typing import Dict, Any, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.investigation import Investigation, InvestigationEvent
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.project import Project
from backend.app.agents.engine import InvestigationEngine
from backend.app.agents.provider import AIProvider
from backend.app.sandbox.manager import sandbox_manager
from backend.app.tools.registry import tool_registry
from backend.app.core.config import settings


class InvestigationService:
    def __init__(self):
        # Event queues mapped by investigation_id: list of asyncio.Queue
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}

    def subscribe(self, investigation_id: str) -> asyncio.Queue:
        queue = asyncio.Queue()
        if investigation_id not in self._subscribers:
            self._subscribers[investigation_id] = []
        self._subscribers[investigation_id].append(queue)
        return queue

    def unsubscribe(self, investigation_id: str, queue: asyncio.Queue) -> None:
        if investigation_id in self._subscribers:
            if queue in self._subscribers[investigation_id]:
                self._subscribers[investigation_id].remove(queue)
            if not self._subscribers[investigation_id]:
                del self._subscribers[investigation_id]

    def broadcast(self, investigation_id: str, event_data: Dict[str, Any]) -> None:
        if investigation_id in self._subscribers:
            for q in self._subscribers[investigation_id]:
                try:
                    q.put_nowait(event_data)
                except Exception:
                    pass

    async def start_investigation(
        self,
        db: AsyncSession,
        project_id: str,
        objective: str
    ) -> Investigation:
        inv = Investigation(
            project_id=project_id,
            objective=objective,
            status="investigating",
            stage="context_collection"
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)

        # Launch background engine task
        asyncio.create_task(self._run_investigation_background(inv.id))
        return inv

    async def _run_investigation_background(self, investigation_id: str) -> None:
        async with AsyncSessionLocal() as session:
            def event_callback(data: Dict[str, Any]):
                self.broadcast(investigation_id, data)

            engine = InvestigationEngine(
                db=session,
                investigation_id=investigation_id,
                event_callback=event_callback
            )
            try:
                await engine.run()
            except Exception as e:
                inv_res = await session.execute(
                    select(Investigation).where(Investigation.id == investigation_id)
                )
                inv = inv_res.scalar_one_or_none()
                if inv:
                    inv.status = "failed"
                    await session.commit()
                await engine.emit_event(
                    "error",
                    "Investigation Encountered Unhandled Error",
                    {"error": str(e)}
                )

    async def handle_patch_action(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        action: str,  # "approve" or "reject"
        reason: Optional[str] = None
    ) -> PatchProposal:
        res = await db.execute(
            select(PatchProposal).where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id
            )
        )
        patch = res.scalar_one_or_none()
        if not patch:
            raise ValueError(f"Patch '{patch_id}' not found.")

        inv_res = await db.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = inv_res.scalar_one_or_none()

        if action == "reject":
            patch.status = "rejected"
            if inv:
                inv.status = "inconclusive"
                inv.stage = "report_generation"
            await db.commit()
            self.broadcast(investigation_id, {
                "event_type": "patch_rejected",
                "title": f"Patch Rejected by User: {patch.target_file}",
                "payload": {"patch_id": patch.id, "reason": reason}
            })
            return patch

        # Approved action: trigger isolated candidate validation
        patch.status = "approved"
        if inv:
            inv.status = "validating"
            inv.stage = "validation"
        await db.commit()

        self.broadcast(investigation_id, {
            "event_type": "patch_approved",
            "title": f"Patch Approved for Isolated Validation: {patch.target_file}",
            "payload": {"patch_id": patch.id}
        })

        # Launch validation in background
        asyncio.create_task(self._validate_patch_background(investigation_id, patch.id))
        return patch

    async def _validate_patch_background(self, investigation_id: str, patch_id: str) -> None:
        async with AsyncSessionLocal() as session:
            patch_res = await session.execute(
                select(PatchProposal).where(PatchProposal.id == patch_id)
            )
            patch = patch_res.scalar_one_or_none()
            inv_res = await session.execute(
                select(Investigation).where(Investigation.id == investigation_id)
            )
            inv = inv_res.scalar_one_or_none()
            proj_res = await session.execute(
                select(Project).where(Project.id == inv.project_id)
            )
            project = proj_res.scalar_one_or_none()

            project_dir = str(settings.UPLOADS_DIR / project.id)

            # Determine test command: prefer pytest, or test the entrypoint
            test_cmd = ["pytest"]
            val_res = await tool_registry.execute_tool(
                tool_name="validate_patch",
                project_path=project_dir,
                arguments={
                    "target_file": patch.target_file,
                    "original_snippet": patch.original_code_context,
                    "replacement_code": patch.proposed_code,
                    "test_command": test_cmd
                },
                investigation_id=investigation_id
            )

            v_data = val_res.data
            status = v_data.get("validation_status", "failed")

            val_run = ValidationRun(
                investigation_id=investigation_id,
                patch_id=patch.id,
                status=status,
                exit_code=v_data.get("exit_code", -1),
                stdout=v_data.get("stdout", ""),
                stderr=v_data.get("stderr", ""),
                test_summary=v_data.get("test_summary", {}),
                regression_results=v_data.get("regression_results", {}),
                execution_time_ms=val_res.execution_time_ms
            )
            session.add(val_run)

            if status == "passed":
                patch.status = "validated"
                inv.status = "completed"
                inv.stage = "regression_testing"
            else:
                patch.status = "validation_failed"
                inv.status = "inconclusive"
                inv.stage = "report_generation"

            await session.commit()

            self.broadcast(investigation_id, {
                "event_type": "validation_result",
                "title": f"Validation Completed: {status.upper()}",
                "payload": {
                    "validation_id": val_run.id,
                    "status": status,
                    "exit_code": val_run.exit_code,
                    "test_summary": val_run.test_summary,
                    "summary": val_res.summary
                }
            })


investigation_service = InvestigationService()

