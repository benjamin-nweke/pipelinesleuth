"""
Privileged Patch Validation Service
Separated privileged execution boundary for human-approved patch validation.
AI agents and ordinary diagnostic tools have no access to this execution path.
Includes durable, cross-process database-backed validation claims and concurrency protection.
"""
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from backend.app.models.investigation import Investigation
from backend.app.models.project import Project
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.tools.validate_patch import ValidatePatchTool
from backend.app.core.config import settings

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
        worker_id: str = "worker-01"
    ) -> PatchProposal:
        """
        Durable, database-backed atomic claim step.
        Ensures exactly ONE worker across all processes can claim a patch for validation.
        Transitions patch from 'approved' -> 'validating'.
        """
        now = datetime.now(timezone.utc)

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

        # 4. Atomic database state transition: 'approved' -> 'validating'
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
                validation_claimed_by=worker_id
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
            details=f"Validation claim acquired by worker '{worker_id}'."
        ))
        return patch

    async def recover_abandoned_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        timeout_seconds: int = 300,
        force: bool = False
    ) -> PatchProposal:
        """
        Safely recovers an abandoned validation claim if the worker process died or timed out.
        Atomically resets patch status back to 'approved' so it can be re-claimed.
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

        if not force and patch.validation_claimed_at:
            claimed_at = patch.validation_claimed_at
            if claimed_at.tzinfo is None:
                claimed_at = claimed_at.replace(tzinfo=timezone.utc)
            elapsed = (now - claimed_at).total_seconds()
            if elapsed < timeout_seconds:
                raise ValueError(f"Validation claim is still active ({elapsed:.1f}s elapsed < {timeout_seconds}s timeout).")

        reset_stmt = (
            update(PatchProposal)
            .where(
                PatchProposal.id == patch_id,
                PatchProposal.status == "validating"
            )
            .values(
                status="approved",
                validation_claimed_at=None,
                validation_claimed_by=None
            )
        )
        await db.execute(reset_stmt)
        await db.commit()
        await db.refresh(patch)
        self._record_audit(PrivilegedValidationAuditEntry(
            investigation_id=investigation_id,
            patch_id=patch_id,
            authorized_by=patch.approved_by or "system",
            status="recovered",
            details=f"Abandoned validation claim on patch '{patch_id}' recovered and reset to 'approved'."
        ))
        return patch

    async def execute_approved_patch_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        test_command: Optional[List[str]] = None,
        worker_id: str = "worker-default"
    ) -> ValidationRun:
        """
        Executes an isolated validation run for an approved patch.
        1. Atomically claims patch in database ('approved' -> 'validating').
        2. Executes candidate patch in isolated copy.
        3. Records accurate final status: 'validated' on test success, 'validation_failed' on test failure.
        4. Persists unique ValidationRun record.
        """
        start_time = time.time()

        # 1. Acquire atomic database-backed claim
        patch = await self.claim_patch_for_validation(
            db=db,
            investigation_id=investigation_id,
            patch_id=patch_id,
            worker_id=worker_id
        )

        # 2. Retrieve project workspace directory
        inv_res = await db.execute(
            select(Investigation).where(Investigation.id == investigation_id)
        )
        inv = inv_res.scalar_one_or_none()
        if not inv:
            raise ValueError(f"Investigation '{investigation_id}' not found.")

        proj_res = await db.execute(
            select(Project).where(Project.id == inv.project_id)
        )
        project = proj_res.scalar_one_or_none()
        if not project:
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

        # 3. Execute isolated candidate patch validation using ValidatePatchTool
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

            res_data = tool_result.data or {}
            stdout = res_data.get("stdout", "")
            stderr = res_data.get("stderr", "") or (tool_result.error or "")
            exit_code = res_data.get("exit_code", 0 if tool_result.success else 1)
            val_status = "passed" if (tool_result.success and exit_code == 0) else "failed"
            execution_time_ms = int((time.time() - start_time) * 1000)

            # 4. Persist ValidationRun record bound to the patch and approver
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

            # 5. Accurate status update reflecting validation outcome
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
            # Update patch to validation_failed so it does not remain stuck in 'validating'
            try:
                patch.status = "validation_failed"
                if inv:
                    inv.status = "patch_validation_failed"
                    inv.stage = "report_generation"
                await db.commit()
            except Exception as db_err:
                logger.error(f"Failed to record validation failure state: {db_err}")
            raise


privileged_validation_service = PrivilegedValidationService()
