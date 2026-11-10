"""
Privileged Patch Validation Service
Separated privileged execution boundary for human-approved patch validation.
AI agents and ordinary diagnostic tools have no access to this execution path.
Includes durable, cross-process database-backed validation claims and concurrency protection.
"""
import time
import uuid
import asyncio
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.database.session import AsyncSessionLocal
from backend.app.models.investigation import Investigation
from backend.app.models.project import Project
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.tools.validate_patch import ValidatePatchTool
from backend.app.core.config import settings
from backend.app.core.auth import AuthenticatedPrincipal

logger = logging.getLogger(__name__)


class PrivilegedValidationAuditEntry:
    def __init__(
        self,
        investigation_id: str,
        patch_id: str,
        authorized_by: str,
        status: str,
        details: str
    ):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.investigation_id = investigation_id
        self.patch_id = patch_id
        self.authorized_by = authorized_by
        self.status = status
        self.details = details

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "investigation_id": self.investigation_id,
            "patch_id": self.patch_id,
            "authorized_by": self.authorized_by,
            "status": self.status,
            "details": self.details,
        }


class PrivilegedValidationService:
    """
    Dedicated server-side service boundary for validating human-approved patches.
    This service cannot be invoked by the diagnostic tool registry or model tool calls.
    It verifies persisted database state independently before performing validation.
    Enforces durable cross-process claims and uniqueness constraints.
    """
    def __init__(self):
        self._audit_log: List[Dict[str, Any]] = []

    def get_audit_log(self, patch_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if patch_id:
            return [e for e in self._audit_log if e.get("patch_id") == patch_id]
        return list(self._audit_log)

    def _record_audit(self, entry: PrivilegedValidationAuditEntry) -> None:
        self._audit_log.append(entry.to_dict())
        logger.info(
            f"[VALIDATION-AUDIT] patch={entry.patch_id} status={entry.status} "
            f"user={entry.authorized_by}: {entry.details}"
        )

    async def claim_patch_for_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        worker_id: str = "worker-01",
        lease_seconds: int = 300
    ) -> PatchProposal:
        """
        Durable, atomic database-backed claim acquisition with lease-token semantics.
        Ensures exactly ONE worker across all processes can claim a patch for validation.
        Transitions patch from 'approved' -> 'validating' with a unique claim token and lease expiry.
        """
        now = datetime.now(timezone.utc)
        claim_token = f"claim_{uuid.uuid4().hex}"
        lease_expires_at = now + timedelta(seconds=lease_seconds)

        # 1. Pre-check: verify patch existence and investigation scoping
        patch_res = await db.execute(
            select(PatchProposal).where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id
            )
        )
        patch = patch_res.scalar_one_or_none()
        if not patch:
            self._record_audit(PrivilegedValidationAuditEntry(
                investigation_id=investigation_id,
                patch_id=patch_id,
                authorized_by="unknown",
                status="denied",
                details=f"Patch '{patch_id}' does not exist or does not belong to investigation '{investigation_id}'."
            ))
            raise ValueError(f"Security violation: Patch '{patch_id}' not found for investigation '{investigation_id}'.")

        # 2. Check approval metadata
        if not patch.approved_by or not patch.approved_at:
            self._record_audit(PrivilegedValidationAuditEntry(
                investigation_id=investigation_id,
                patch_id=patch_id,
                authorized_by="none",
                status="denied",
                details="Patch lacks valid server-verified approver identity or timestamp."
            ))
            raise ValueError(
                f"Security violation: Patch '{patch_id}' lacks verified human approval metadata."
            )

        # 3. Check durable uniqueness constraint: no existing validation run
        val_count_res = await db.execute(
            select(func.count(ValidationRun.id)).where(ValidationRun.patch_id == patch_id)
        )
        existing_val_count = val_count_res.scalar_one() or 0
        if existing_val_count > 0:
            self._record_audit(PrivilegedValidationAuditEntry(
                investigation_id=investigation_id,
                patch_id=patch_id,
                authorized_by=patch.approved_by,
                status="denied",
                details=f"Validation already exists ({existing_val_count} runs found). Duplicate validation blocked."
            ))
            raise ValueError(
                f"Security violation: Patch '{patch_id}' has already been validated. Duplicate validation runs are forbidden."
            )

        # 4. Atomic database state transition: 'approved' -> 'validating' with lease token
        claim_stmt = (
            update(PatchProposal)
            .where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id,
                PatchProposal.status == "approved"
            )
            .values(
                status="validating",
                validation_claimed_at=now,
                validation_lease_expires_at=lease_expires_at,
                validation_claimed_by=worker_id,
                validation_claim_token=claim_token
            )
        )
        claim_res = await db.execute(claim_stmt)
        await db.commit()

        if claim_res.rowcount == 0:
            # Another concurrent process claimed, rejected, or completed it
            await db.refresh(patch)
            if patch.status == "validating":
                raise ValueError(
                    f"Concurrency violation: Patch '{patch_id}' is already claimed by active worker '{patch.validation_claimed_by}'."
                )
            elif patch.status in ("validated", "validation_failed"):
                raise ValueError(
                    f"Concurrency violation: Patch '{patch_id}' has already been validated (status: '{patch.status}'). Duplicate validation blocked."
                )
            else:
                raise ValueError(
                    f"Security violation: Patch '{patch_id}' cannot be validated because its status is '{patch.status}' (expected 'approved')."
                )

        await db.refresh(patch)
        self._record_audit(PrivilegedValidationAuditEntry(
            investigation_id=investigation_id,
            patch_id=patch_id,
            authorized_by=patch.approved_by,
            status="claimed",
            details=f"Validation claim acquired by worker '{worker_id}' with token '{claim_token[:12]}...' (lease expires: {lease_expires_at.isoformat()})."
        ))
        return patch

    async def renew_validation_lease(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        claim_token: str,
        extend_seconds: int = 300
    ) -> PatchProposal:
        """
        Renews an active validation lease using compare-and-swap semantics.
        A worker holding the valid claim_token can extend its lease to avoid expiration during long tests.
        Workers lacking the matching claim_token are strictly rejected.
        """
        now = datetime.now(timezone.utc)
        new_expires = now + timedelta(seconds=extend_seconds)

        patch_res = await db.execute(
            select(PatchProposal).where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id
            )
        )
        patch = patch_res.scalar_one_or_none()
        if not patch:
            raise ValueError(f"Patch '{patch_id}' not found.")

        renew_stmt = (
            update(PatchProposal)
            .where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id,
                PatchProposal.status == "validating",
                PatchProposal.validation_claim_token == claim_token
            )
            .values(
                validation_lease_expires_at=new_expires
            )
        )
        res = await db.execute(renew_stmt)
        await db.commit()

        if res.rowcount == 0:
            raise ValueError(
                f"Lease renewal rejected: Invalid claim token or patch '{patch_id}' is not in validating status under this lease."
            )

        await db.refresh(patch)
        return patch

    async def recover_abandoned_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        timeout_seconds: int = 300
    ) -> PatchProposal:
        """
        Safely recovers an abandoned validation claim using compare-and-swap (CAS) semantics.
        1. Verifies that the existing claim lease has genuinely expired.
        2. Matches the exact claim token and validation_lease_expires_at in the atomic update predicate.
        3. Enforces validation_lease_expires_at <= :now directly in the SQL update condition.
        4. Refuses to reset if another worker has already reclaimed, renewed, or modified the claim.
        """
        now = datetime.now(timezone.utc)
        patch_res = await db.execute(
            select(PatchProposal).where(
                PatchProposal.id == patch_id,
                PatchProposal.investigation_id == investigation_id
            )
        )
        patch = patch_res.scalar_one_or_none()
        if not patch:
            raise ValueError(f"Patch '{patch_id}' not found.")
        if patch.status != "validating":
            raise ValueError(f"Patch '{patch_id}' is not in 'validating' state (current: '{patch.status}').")

        # Check lease expiry in-memory first for informative diagnostics
        if patch.validation_lease_expires_at:
            exp = patch.validation_lease_expires_at
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=timezone.utc)
            if now < exp:
                remaining = (exp - now).total_seconds()
                raise ValueError(f"Validation claim is still active ({remaining:.1f}s remaining on lease). Cannot recover active claim.")
        elif patch.validation_claimed_at:
            claimed_at = patch.validation_claimed_at
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            elapsed = (now - claimed_at).total_seconds()
            if elapsed < timeout_seconds:
                raise ValueError(f"Validation claim is still active ({elapsed:.1f}s elapsed < {timeout_seconds}s timeout).")

        expected_token = patch.validation_claim_token
        expected_expires_at = patch.validation_lease_expires_at

        # Atomic Compare-And-Swap (CAS) update with database-level expiry enforcement
        where_conditions = [
            PatchProposal.id == patch_id,
            PatchProposal.investigation_id == investigation_id,
            PatchProposal.status == "validating"
        ]
        if expected_expires_at is not None:
            where_conditions.append(PatchProposal.validation_lease_expires_at == expected_expires_at)
            where_conditions.append(PatchProposal.validation_lease_expires_at <= now)
        elif patch.validation_claimed_at is not None and timeout_seconds > 0:
            where_conditions.append(PatchProposal.validation_claimed_at == patch.validation_claimed_at)
            where_conditions.append(PatchProposal.validation_claimed_at <= now - timedelta(seconds=timeout_seconds))

        if expected_token:
            where_conditions.append(PatchProposal.validation_claim_token == expected_token)

        reset_stmt = (
            update(PatchProposal)
            .where(*where_conditions)
            .values(
                status="approved",
                validation_claimed_at=None,
                validation_lease_expires_at=None,
                validation_claimed_by=None,
                validation_claim_token=None
            )
        )
        res = await db.execute(reset_stmt)
        await db.commit()

        if res.rowcount == 0:
            raise ValueError(
                f"Concurrency violation: Stale recovery attempt rejected. "
                f"Patch '{patch_id}' claim was already reclaimed, renewed, or updated by another worker, or is still active."
            )

        await db.refresh(patch)
        self._record_audit(PrivilegedValidationAuditEntry(
            investigation_id=investigation_id,
            patch_id=patch_id,
            authorized_by=patch.approved_by or "system",
            status="recovered",
            details=f"Abandoned validation claim on patch '{patch_id}' recovered via CAS and reset to 'approved'."
        ))
        return patch

    async def execute_approved_patch_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        test_command: Optional[List[str]] = None,
        worker_id: str = "worker-default",
        lease_seconds: int = 300,
        renewal_interval: Optional[float] = None
    ) -> ValidationRun:
        """
        Executes an isolated validation run for an approved patch.
        1. Atomically claims patch in database ('approved' -> 'validating').
        2. Spawns an async heartbeat renewal task that periodically extends the lease.
        3. Executes candidate patch in isolated copy.
        4. Detects loss of claim ownership and rejects commit if ownership was lost.
        5. Commits final status atomically using CAS validation token match.
        6. Reliably cancels and cleans up heartbeat task in finally block.
        """
        start_time = time.time()

        # 1. Acquire atomic database-backed claim
        patch = await self.claim_patch_for_validation(
            db=db,
            investigation_id=investigation_id,
            patch_id=patch_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds
        )
        claim_token = patch.validation_claim_token

        # 2. Spawn async heartbeat renewal loop
        stop_heartbeat = asyncio.Event()
        ownership_lost = False
        heartbeat_error: Optional[str] = None
        interval = renewal_interval or min(10.0, max(1.0, lease_seconds / 3.0))

        async def _heartbeat_loop():
            nonlocal ownership_lost, heartbeat_error
            try:
                while not stop_heartbeat.is_set():
                    try:
                        await asyncio.wait_for(stop_heartbeat.wait(), timeout=interval)
                        break
                    except asyncio.TimeoutError:
                        pass
                    if stop_heartbeat.is_set():
                        break

                    try:
                        async with AsyncSessionLocal() as hb_session:
                            await self.renew_validation_lease(
                                db=hb_session,
                                investigation_id=investigation_id,
                                patch_id=patch_id,
                                claim_token=claim_token,
                                extend_seconds=lease_seconds
                            )
                            logger.debug(f"Heartbeat renewed lease for patch {patch_id}")
                    except Exception as renew_err:
                        logger.warning(f"Heartbeat renewal failed for patch {patch_id}: {renew_err}")
                        ownership_lost = True
                        heartbeat_error = str(renew_err)
                        break
            except asyncio.CancelledError:
                pass

        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        # 3. Retrieve project workspace directory
        inv_res = await db.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = inv_res.scalar_one_or_none()
        if not inv:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            raise ValueError(f"Investigation '{investigation_id}' not found.")

        proj_res = await db.execute(
            select(Project).where(Project.id == inv.project_id)
        )
        project = proj_res.scalar_one_or_none()
        if not project:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            raise ValueError(f"Project for investigation '{investigation_id}' not found.")

        project_dir = str(settings.UPLOADS_DIR / project.id)

        # Record authorized execution audit
        self._record_audit(PrivilegedValidationAuditEntry(
            investigation_id=investigation_id,
            patch_id=patch_id,
            authorized_by=patch.approved_by,
            status="authorized",
            details=f"Executing isolated candidate patch validation for: {patch.target_file}"
        ))

        # 4. Execute isolated candidate patch validation using ValidatePatchTool
        val_tool = ValidatePatchTool()
        val_args = {
            "patch_id": patch.id,
            "target_file": patch.target_file,
            "proposed_code": patch.proposed_code,
            "test_command": test_command or ["pytest"]
        }

        try:
            tool_result = await val_tool.execute(
                project_path=project_dir,
                arguments=val_args
            )

            # Check if claim ownership was lost during tool execution
            if ownership_lost:
                raise RuntimeError(
                    f"Security violation: Validation claim ownership for patch '{patch_id}' was lost during execution: {heartbeat_error}"
                )

            res_data = tool_result.data or {}
            stdout = res_data.get("stdout", "")
            stderr = res_data.get("stderr", "") or (tool_result.error or "")
            exit_code = res_data.get("exit_code", 0 if tool_result.success else 1)
            val_status = "passed" if (tool_result.success and exit_code == 0) else "failed"
            execution_time_ms = int((time.time() - start_time) * 1000)

            # 5. Persist ValidationRun record bound to the patch and approver
            validation_run = ValidationRun(
                investigation_id=investigation_id,
                patch_id=patch.id,
                status=val_status,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                test_summary=res_data.get("test_summary", {}),
                regression_results=res_data.get("regression_results", {}),
                execution_time_ms=execution_time_ms,
                authorized_by=patch.approved_by,
                authorized_at=patch.approved_at
            )
            db.add(validation_run)

            # 6. Atomic CAS status update reflecting validation outcome with claim_token guard
            final_patch_status = "validated" if val_status == "passed" else "validation_failed"
            commit_stmt = (
                update(PatchProposal)
                .where(
                    PatchProposal.id == patch.id,
                    PatchProposal.investigation_id == investigation_id,
                    PatchProposal.status == "validating",
                    PatchProposal.validation_claim_token == claim_token
                )
                .values(
                    status=final_patch_status,
                    validation_lease_expires_at=None,
                    validation_claim_token=None,
                    validation_claimed_by=None
                )
            )
            commit_res = await db.execute(commit_stmt)
            if commit_res.rowcount == 0:
                raise RuntimeError(
                    f"Security violation: Failed to commit validation outcome for patch '{patch.id}'. "
                    f"Claim ownership was lost, expired, or superseded during execution."
                )

            if val_status == "passed":
                patch.status = "validated"
                if inv:
                    inv.status = "patch_validated"
                    inv.stage = "regression_testing"
            else:
                patch.status = "validation_failed"
                if inv:
                    inv.status = "patch_validation_failed"
                    inv.stage = "report_generation"

            await db.commit()
            await db.refresh(validation_run)

            self._record_audit(PrivilegedValidationAuditEntry(
                investigation_id=investigation_id,
                patch_id=patch.id,
                authorized_by=patch.approved_by,
                status=val_status,
                details=f"Validation completed with status '{val_status}' (exit_code={exit_code}) in {execution_time_ms}ms"
            ))

            return validation_run

        except Exception as e:
            logger.error(f"Execution error during validation of patch {patch_id}: {e}", exc_info=True)
            if not ownership_lost:
                try:
                    fail_stmt = (
                        update(PatchProposal)
                        .where(
                            PatchProposal.id == patch.id,
                            PatchProposal.investigation_id == investigation_id,
                            PatchProposal.status == "validating",
                            PatchProposal.validation_claim_token == claim_token
                        )
                        .values(
                            status="validation_failed",
                            validation_lease_expires_at=None,
                            validation_claim_token=None,
                            validation_claimed_by=None
                        )
                    )
                    await db.execute(fail_stmt)
                    if inv:
                        inv.status = "patch_validation_failed"
                        inv.stage = "report_generation"
                    await db.commit()
                except Exception as db_err:
                    logger.error(f"Failed to record validation failure state: {db_err}")
            raise

        finally:
            stop_heartbeat.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass


privileged_validation_service = PrivilegedValidationService()
