"""
Privileged Patch Validation Service
Separated privileged execution boundary for human-approved patch validation.
AI agents and ordinary diagnostic tools have no access to this execution path.
"""
import time
import logging
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy import select, func
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

    async def execute_approved_patch_validation(
        self,
        db: AsyncSession,
        investigation_id: str,
        patch_id: str,
        test_command: Optional[List[str]] = None
    ) -> ValidationRun:
        """
        Executes an isolated validation run for an approved patch.
        Independently confirms all prerequisites against persisted DB state.
        Never trusts caller-supplied authorization strings or agent parameters.
        """
        start_time = time.time()

        # 1. Independently confirm patch exists and belongs to investigation
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

        # 2. Independently verify status is strictly 'approved'
        if patch.status != "approved":
            self._record_audit(PrivilegedValidationAuditEntry(
                investigation_id=investigation_id,
                patch_id=patch_id,
                authorized_by=patch.approved_by or "none",
                status="denied",
                details=f"Patch '{patch_id}' is in status '{patch.status}', not 'approved'. Execution blocked."
            ))
            raise ValueError(
                f"Security violation: Patch '{patch_id}' cannot be validated because its status is '{patch.status}' (expected 'approved')."
            )

        # 3. Independently verify approval metadata (principal & timestamp)
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

        # 4. Independently verify no validation run has already been scheduled or completed
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

        # 5. Retrieve project workspace directory
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
            details=f"Executing isolated patch validation for target file: {patch.target_file}"
        ))

        # 6. Execute isolated candidate patch validation using ValidatePatchTool
        val_tool = ValidatePatchTool()
        val_args = {
            "patch_id": patch.id,
            "target_file": patch.target_file,
            "proposed_code": patch.proposed_code,
            "test_command": test_command or ["pytest"]
        }

        tool_result = await val_tool.execute(
            project_path=project_dir,
            arguments=val_args
        )

        res_data = tool_result.data or {}
        stdout = res_data.get("stdout", "")
        stderr = res_data.get("stderr", "") or (tool_result.error or "")
        exit_code = res_data.get("exit_code", 0 if tool_result.success else 1)
        val_status = "passed" if tool_result.success else "failed"

        execution_time_ms = int((time.time() - start_time) * 1000)

        # 7. Persist ValidationRun record bound to the patch and approver
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

        # Update patch status to validated
        patch.status = "validated"
        if inv:
            inv.status = "patch_validated" if val_status == "passed" else "patch_validation_failed"
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


privileged_validation_service = PrivilegedValidationService()
