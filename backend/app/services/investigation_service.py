"""
Investigation Lifecycle and Event Streaming Service
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, update, func
from sqlalchemy.orm import selectinload

from backend.app.database.session import AsyncSessionLocal
from backend.app.models.investigation import Investigation, InvestigationEvent
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.project import Project
from backend.app.models.report import InvestigationReport
from backend.app.agents.engine import InvestigationEngine
from backend.app.agents.provider import AIProvider
from backend.app.sandbox.manager import sandbox_manager
from backend.app.core.config import settings
from backend.app.core.auth import AuthenticatedPrincipal
from backend.app.services.report_service import generate_or_update_investigation_report
from backend.app.services.validation_service import privileged_validation_service

logger = logging.getLogger(__name__)


class InvestigationService:
    def __init__(self):
        # Event queues mapped by investigation_id: list of asyncio.Queue
        self._subscribers: Dict[str, List[asyncio.Queue]] = {}
        # Active background investigation tasks mapped by investigation_id
        self._active_investigations: Dict[str, asyncio.Task] = {}
        # Concurrency safety tracking for active validation tasks
        self._active_validations: Set[str] = set()
        self._validation_lock = asyncio.Lock()

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

    async def create_investigation(
        self,
        db: AsyncSession,
        project_id: str,
        objective: str,
    ) -> Investigation:
        """
        Validates the target project exists, persists a new Investigation record,
        commits and refreshes it before any background work is scheduled.
        """
        proj_res = await db.execute(select(Project).where(Project.id == project_id))
        project = proj_res.scalar_one_or_none()
        if not project:
            raise ValueError(f"Project '{project_id}' not found.")

        inv = Investigation(
            project_id=project_id,
            objective=objective,
            status="created",
            stage="context_collection",
            confidence_score=0.0,
            remaining_budget=settings.MAX_TOOL_CALLS,
        )
        db.add(inv)
        await db.commit()
        await db.refresh(inv)
        return inv

    def dispatch_investigation_background(
        self,
        investigation_id: str,
        provider: Optional[AIProvider] = None,
    ) -> asyncio.Task:
        """
        Schedules background investigation execution using its own dedicated AsyncSessionLocal session.
        """
        task = asyncio.create_task(
            self._run_investigation_background(investigation_id, provider=provider)
        )
        self._active_investigations[investigation_id] = task
        task.add_done_callback(lambda _t: self._active_investigations.pop(investigation_id, None))
        return task

    async def start_investigation(
        self,
        db: AsyncSession,
        project_id: str,
        objective: str,
        provider: Optional[AIProvider] = None,
    ) -> Investigation:
        inv = await self.create_investigation(
            db=db,
            project_id=project_id,
            objective=objective,
        )
        # Run autonomous loop asynchronously using a separate background session
        self.dispatch_investigation_background(inv.id, provider=provider)
        return inv

    async def _run_investigation_background(
        self,
        investigation_id: str,
        provider: Optional[AIProvider] = None,
    ) -> None:
        try:
            async with AsyncSessionLocal() as session:
                engine = InvestigationEngine(
                    db=session,
                    investigation_id=investigation_id,
                    ai_provider=provider,
                    event_callback=lambda ev: self.broadcast(investigation_id, ev),
                )
                await engine.run()
        except asyncio.CancelledError:
            logger.info(f"Background investigation {investigation_id} was cancelled.")
            raise
        except Exception as e:
            logger.error(f"Investigation {investigation_id} failed with unhandled exception: {e}", exc_info=True)
            try:
                async with AsyncSessionLocal() as recovery_session:
                    res = await recovery_session.execute(
                        select(Investigation).where(Investigation.id == investigation_id)
                    )
                    inv = res.scalar_one_or_none()
                    if inv:
                        inv.status = "failed"
                        inv.stage = "report_generation"
                        inv.root_cause_summary = f"Investigation terminated unexpectedly: {str(e)}"
                        await recovery_session.commit()
                    self.broadcast(
                        investigation_id,
                        {
                            "event_type": "investigation_failed",
                            "title": "Investigation Encountered Unhandled Error",
                            "payload": {"error": str(e)},
                        },
                    )
            except Exception as persist_err:
                logger.error(f"Failed to record investigation failure state for {investigation_id}: {persist_err}")

    async def handle_patch_action(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        action: str,  # "approve" or "reject"
        principal: AuthenticatedPrincipal,
        reason: Optional[str] = None
    ) -> PatchProposal:
        """
        Concurrency-safe, authenticated patch approval or rejection.
        Requires an authenticated principal with explicit permission scopes:
        - "patch:approve" for approval
        - "patch:reject" for rejection
        """
        # 1. Enforce mandatory principal
        if not principal or not isinstance(principal, AuthenticatedPrincipal):
            raise ValueError("Authentication error: An authenticated principal is strictly required to authorize patch actions.")

        # 2. Enforce scope-based authorization (not just role name)
        if action == "approve":
            if "patch:approve" not in principal.scopes:
                raise PermissionError(f"Forbidden: Principal '{principal.username}' lacks the required 'patch:approve' scope.")
        elif action == "reject":
            if "patch:reject" not in principal.scopes:
                raise PermissionError(f"Forbidden: Principal '{principal.username}' lacks the required 'patch:reject' scope.")
        else:
            raise ValueError(f"Invalid patch action '{action}'. Allowed: 'approve', 'reject'.")

        approver_identity = principal.username
        now = datetime.now(timezone.utc)
        target_status = "approved" if action == "approve" else "rejected"

        # Concurrency-safe atomic UPDATE:
        # Only transitions if the patch is currently in 'proposed' state.
        update_values: Dict[str, Any] = {
            "status": target_status,
        }
        if action == "approve":
            update_values["approved_by"] = approver_identity
            update_values["approved_at"] = now
        else:
            update_values["rejected_at"] = now
            update_values["rejection_reason"] = reason or f"Rejected by {approver_identity}"

        stmt = (
            update(PatchProposal)
            .where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id,
                PatchProposal.status == "proposed"
            )
            .values(**update_values)
        )
        res = await db.execute(stmt)
        await db.commit()

        # If 0 rows were updated, determine conflict cause and reject immediately
        if res.rowcount == 0:
            existing_res = await db.execute(
                select(PatchProposal).where(PatchProposal.id == patch_id)
            )
            existing = existing_res.scalar_one_or_none()
            if not existing:
                raise ValueError(f"Patch '{patch_id}' not found.")
            if existing.investigation_id != investigation_id:
                raise ValueError(f"Patch '{patch_id}' does not belong to investigation '{investigation_id}'.")
            if existing.status in ("approved", "validated", "validation_failed"):
                if action == "approve":
                    raise ValueError(f"Patch '{patch_id}' has already been approved. Duplicate validation is blocked.")
                else:
                    raise ValueError(f"Patch '{patch_id}' has already been approved and cannot be rejected.")
            if existing.status == "rejected":
                raise ValueError(f"Patch '{patch_id}' has already been rejected and cannot be modified or validated.")
            raise ValueError(f"Patch '{patch_id}' is in state '{existing.status}' and cannot be transitioned.")

        # Re-fetch updated patch with validation runs loaded
        patch_res = await db.execute(
            select(PatchProposal)
            .options(selectinload(PatchProposal.validation_runs))
            .where(PatchProposal.id == patch_id)
        )
        patch = patch_res.scalar_one()

        # Update investigation status
        inv_res = await db.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = inv_res.scalar_one_or_none()
        if inv:
            if action == "reject":
                inv.status = "inconclusive"
                inv.stage = "report_generation"
            else:
                inv.status = "validating"
                inv.stage = "validation"
            await db.commit()

        if action == "reject":
            report = await generate_or_update_investigation_report(
                db=db,
                investigation_id=investigation_id,
            )
            self.broadcast(investigation_id, {
                "event_type": "patch_rejected",
                "title": f"Patch Rejected by Operator: {patch.target_file}",
                "payload": {
                    "patch_id": patch.id,
                    "reason": patch.rejection_reason,
                    "rejected_at": now.isoformat(),
                    "authorized_by": approver_identity
                }
            })
            if report is not None:
                self.broadcast(investigation_id, {
                    "event_type": "report_ready",
                    "title": f"Final Report Generated: {report.title}",
                    "payload": {
                        "report_id": report.id,
                        "root_cause": report.root_cause,
                        "is_success": False,
                    },
                })
            return patch

        # Approved action
        self.broadcast(investigation_id, {
            "event_type": "patch_approved",
            "title": f"Patch Approved for Isolated Validation: {patch.target_file}",
            "payload": {
                "patch_id": patch.id,
                "approved_by": patch.approved_by,
                "approved_at": patch.approved_at.isoformat() if patch.approved_at else None
            }
        })

        # Launch background validation with persisted dispatch
        await self.dispatch_validation_background(investigation_id, patch.id)
        return patch

    async def dispatch_validation_background(self, investigation_id: str, patch_id: str) -> bool:
        """
        Dispatches background validation if not already actively validating in this process.
        Returns True if a new background validation task was scheduled.
        """
        async with self._validation_lock:
            if patch_id in self._active_validations:
                logger.warning(f"Validation for patch '{patch_id}' is already active. Ignoring duplicate dispatch.")
                return False
            self._active_validations.add(patch_id)

        asyncio.create_task(self._validate_patch_background(investigation_id, patch_id))
        return True

    async def resume_pending_validations_on_startup(self) -> List[str]:
        """
        On application startup, detects approved patches whose validation was interrupted
        by a process restart, crash, or deployment event, and safely resumes dispatch.
        - Identifies 'approved' patches without existing validation runs.
        - Identifies abandoned 'validating' patches with expired leases, recovers them to 'approved', and resumes.
        - Ignores completed ('validated', 'validation_failed') or rejected patches.
        - Ignores actively validating patches holding unexpired leases.
        """
        dispatched_ids: List[str] = []
        try:
            async with AsyncSessionLocal() as session:
                # 1. Recover abandoned validating patches whose lease has expired
                now = datetime.now(timezone.utc)
                abandoned_query = select(PatchProposal).where(PatchProposal.status == "validating")
                res = await session.execute(abandoned_query)
                validating_patches = res.scalars().all()
                for p in validating_patches:
                    is_expired = False
                    if p.validation_lease_expires_at:
                        exp = p.validation_lease_expires_at.replace(tzinfo=timezone.utc) if p.validation_lease_expires_at.tzinfo is None else p.validation_lease_expires_at
                        is_expired = now >= exp
                    elif p.validation_claimed_at:
                        c_at = p.validation_claimed_at.replace(tzinfo=timezone.utc) if p.validation_claimed_at.tzinfo is None else p.validation_claimed_at
                        is_expired = (now - c_at).total_seconds() >= 300

                    if is_expired:
                        try:
                            await privileged_validation_service.recover_abandoned_validation(
                                db=session,
                                investigation_id=p.investigation_id,
                                patch_id=p.id
                            )
                            logger.info(f"Startup recovery: Reset abandoned validating patch '{p.id}' back to approved.")
                        except Exception as e:
                            logger.warning(f"Startup recovery could not reset validating patch '{p.id}': {e}")

                # 2. Find eligible approved patches with no existing ValidationRun
                approved_query = select(PatchProposal).where(PatchProposal.status == "approved")
                res = await session.execute(approved_query)
                approved_patches = res.scalars().all()
                for p in approved_patches:
                    val_count_res = await session.execute(
                        select(func.count(ValidationRun.id)).where(ValidationRun.patch_id == p.id)
                    )
                    if (val_count_res.scalar_one() or 0) == 0:
                        dispatched = await self.dispatch_validation_background(p.investigation_id, p.id)
                        if dispatched:
                            dispatched_ids.append(p.id)
                            logger.info(f"Startup recovery: Resumed background validation for approved patch '{p.id}'.")
        except Exception as e:
            logger.error(f"Error during startup pending validation recovery: {e}", exc_info=True)
        return dispatched_ids

    async def _validate_patch_background(self, investigation_id: str, patch_id: str) -> None:
        """
        Executes validation via PrivilegedValidationService in a background task.
        Safely captures failures to prevent patches from being stranded in 'validating' state.
        """
        try:
            async with AsyncSessionLocal() as session:
                val_run = await privileged_validation_service.execute_approved_patch_validation(
                    db=session,
                    investigation_id=investigation_id,
                    patch_id=patch_id
                )
                rep_res = await session.execute(
                    select(InvestigationReport).where(
                        InvestigationReport.investigation_id == investigation_id
                    )
                )
                persisted_report = rep_res.scalar_one_or_none()
                self.broadcast(investigation_id, {
                    "event_type": "validation_result",
                    "title": f"Validation Completed: {val_run.status.upper()}",
                    "payload": {
                        "validation_id": val_run.id,
                        "status": val_run.status,
                        "exit_code": val_run.exit_code,
                        "test_summary": val_run.test_summary,
                        "authorized_by": val_run.authorized_by
                    }
                })
                if persisted_report is not None:
                    val_summary = persisted_report.validation_summary or {}
                    self.broadcast(investigation_id, {
                        "event_type": "report_ready",
                        "title": f"Final Report Generated: {persisted_report.title}",
                        "payload": {
                            "report_id": persisted_report.id,
                            "root_cause": persisted_report.root_cause,
                            "is_success": bool(val_summary.get("validated", False)),
                        },
                    })
        except Exception as e:
            logger.error(f"Background validation failed for patch {patch_id}: {e}", exc_info=True)
            # Recovery: ensure patch and investigation state do not stay stranded
            try:
                async with AsyncSessionLocal() as recovery_session:
                    p_res = await recovery_session.execute(
                        select(PatchProposal).where(PatchProposal.id == patch_id)
                    )
                    p = p_res.scalar_one_or_none()
                    if p and p.status in ("approved", "validation_failed"):
                        existing_run_res = await recovery_session.execute(
                            select(ValidationRun).where(ValidationRun.patch_id == patch_id)
                        )
                        existing_run = existing_run_res.scalar_one_or_none()
                        if existing_run is None:
                            failed_run = ValidationRun(
                                investigation_id=investigation_id,
                                patch_id=patch_id,
                                status="failed",
                                exit_code=1,
                                stdout="",
                                stderr=f"Validation execution failed: {str(e)}",
                                authorized_by=p.approved_by,
                                authorized_at=p.approved_at
                            )
                            recovery_session.add(failed_run)
                        else:
                            failed_run = existing_run

                        p.status = "validation_failed"

                        i_res = await recovery_session.execute(
                            select(Investigation).where(Investigation.id == investigation_id)
                        )
                        i = i_res.scalar_one_or_none()
                        if i:
                            i.status = "patch_validation_failed"
                            i.stage = "report_generation"

                        await recovery_session.commit()
                        await recovery_session.refresh(failed_run)

                        recovery_report = await generate_or_update_investigation_report(
                            db=recovery_session,
                            investigation_id=investigation_id,
                        )

                        self.broadcast(investigation_id, {
                            "event_type": "validation_result",
                            "title": "Validation Failed: System Error",
                            "payload": {
                                "validation_id": failed_run.id,
                                "status": failed_run.status,
                                "exit_code": failed_run.exit_code,
                                "error": str(e),
                                "authorized_by": p.approved_by
                            }
                        })
                        if recovery_report is not None:
                            self.broadcast(investigation_id, {
                                "event_type": "report_ready",
                                "title": f"Final Report Generated: {recovery_report.title}",
                                "payload": {
                                    "report_id": recovery_report.id,
                                    "root_cause": recovery_report.root_cause,
                                    "is_success": False,
                                },
                            })
            except Exception as persist_err:
                logger.error(f"Failed to record validation failure state: {persist_err}")
        finally:
            async with self._validation_lock:
                self._active_validations.discard(patch_id)


investigation_service = InvestigationService()
