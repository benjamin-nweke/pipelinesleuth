"""
Investigation Report Generation Service
Provides a single idempotent, evidence-grounded path for synthesizing and persisting
final InvestigationReport records across engine conclusions, patch rejections, and
validation outcomes.
"""
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy import select, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.investigation import Investigation, EvidenceRecord, Hypothesis
from backend.app.models.patch import PatchProposal, ValidationRun
from backend.app.models.report import InvestigationReport

logger = logging.getLogger(__name__)


def classify_validation_outcome(
    patch: Optional[PatchProposal],
    val_run: Optional[ValidationRun],
) -> str:
    """
    Determines the truthful terminal/current outcome for patch validation:
    'passed', 'skipped', 'failed', 'rejected', or 'not_run'.
    Failed or skipped validations are never classified as 'passed'.
    """
    if patch and patch.status == "rejected":
        return "rejected"

    if val_run is not None:
        summary_meta = val_run.test_summary if isinstance(val_run.test_summary, dict) else {}
        stderr_text = val_run.stderr or ""
        docker_unavailable = (
            val_run.status == "skipped"
            or summary_meta.get("validation_status") == "skipped"
            or summary_meta.get("docker_available") is False
            or "Docker is unavailable" in stderr_text
            or "EXECUTION BLOCKED BY SECURITY POLICY" in stderr_text
        )
        if docker_unavailable:
            return "skipped"
        if val_run.status == "passed" and val_run.exit_code == 0 and (not patch or patch.status == "validated"):
            return "passed"
        return "failed"

    if patch and patch.status == "validation_failed":
        return "failed"

    return "not_run"


def _build_proposed_correction_dict(patch: Optional[PatchProposal]) -> Dict[str, Any]:
    if not patch:
        return {}
    return {
        "patch_id": patch.id,
        "target_file": patch.target_file,
        "status": patch.status,
        "explanation": patch.explanation,
        "unified_diff": patch.unified_diff,
        "supporting_evidence_ids": patch.supporting_evidence_ids or [],
        "expected_impact": patch.expected_impact,
        "potential_risks": patch.potential_risks,
        "approved_by": patch.approved_by,
        "approved_at": patch.approved_at.isoformat() if patch.approved_at else None,
        "rejected_at": patch.rejected_at.isoformat() if patch.rejected_at else None,
        "rejection_reason": patch.rejection_reason,
    }


def _build_validation_summary_dict(
    outcome: str,
    patch: Optional[PatchProposal],
    val_run: Optional[ValidationRun],
) -> Dict[str, Any]:
    if val_run is not None:
        raw_test_summary = val_run.test_summary if isinstance(val_run.test_summary, dict) else {}
        cleaned_counts = {
            "passed": int(raw_test_summary.get("passed", 0) or 0),
            "failed": int(raw_test_summary.get("failed", 0) or 0),
            "errors": int(raw_test_summary.get("errors", 0) or 0),
            "total": int(raw_test_summary.get("total", 0) or 0),
            "details": raw_test_summary.get("details", []),
        }
        return {
            "validation_id": val_run.id,
            "patch_id": val_run.patch_id,
            "status": outcome,
            "recorded_run_status": val_run.status,
            "validated": outcome == "passed",
            "exit_code": val_run.exit_code,
            "execution_time_ms": val_run.execution_time_ms,
            "authorized_by": val_run.authorized_by,
            "authorized_at": val_run.authorized_at.isoformat() if val_run.authorized_at else None,
            "docker_available": raw_test_summary.get("docker_available", outcome != "skipped"),
            "test_summary": cleaned_counts,
            "error_excerpt": (val_run.stderr or "")[:400] if outcome != "passed" else "",
        }

    if outcome == "rejected" and patch is not None:
        return {
            "patch_id": patch.id,
            "status": "not_run",
            "validated": False,
            "reason": f"Patch rejected prior to validation: {patch.rejection_reason or 'Rejected by operator'}",
        }

    if outcome == "failed" and patch is not None:
        return {
            "patch_id": patch.id,
            "status": "failed",
            "validated": False,
            "reason": "Validation failed prior to recording test summary.",
        }

    return {
        "status": "not_run",
        "validated": False,
        "reason": "No patch validation run executed.",
    }


async def generate_or_update_investigation_report(
    db: AsyncSession,
    investigation_id: str,
    rep_input: Optional[Any] = None,
    is_success_hint: Optional[bool] = None,
) -> Optional[InvestigationReport]:
    """
    Idempotently creates or updates the InvestigationReport for `investigation_id`
    from persisted evidence, hypotheses, patch proposals, and validation runs.
    """
    inv_result = await db.execute(
        select(Investigation).where(Investigation.id == investigation_id)
    )
    inv = inv_result.scalar_one_or_none()
    if not inv:
        return None

    ev_result = await db.execute(
        select(EvidenceRecord)
        .where(EvidenceRecord.investigation_id == investigation_id)
        .order_by(EvidenceRecord.timestamp.asc())
    )
    evidence: List[EvidenceRecord] = list(ev_result.scalars().all())

    hyp_result = await db.execute(
        select(Hypothesis)
        .where(Hypothesis.investigation_id == investigation_id)
        .order_by(Hypothesis.created_at.asc())
    )
    hypotheses: List[Hypothesis] = list(hyp_result.scalars().all())

    patch_result = await db.execute(
        select(PatchProposal)
        .where(PatchProposal.investigation_id == investigation_id)
        .order_by(PatchProposal.created_at.desc())
    )
    patches: List[PatchProposal] = list(patch_result.scalars().all())

    val_result = await db.execute(
        select(ValidationRun)
        .where(ValidationRun.investigation_id == investigation_id)
        .order_by(ValidationRun.created_at.desc())
    )
    val_runs: List[ValidationRun] = list(val_result.scalars().all())

    primary_patch = patches[0] if patches else None
    matching_val: Optional[ValidationRun] = None
    if primary_patch:
        for vr in val_runs:
            if vr.patch_id == primary_patch.id:
                matching_val = vr
                break
    if matching_val is None and val_runs:
        matching_val = val_runs[0]

    outcome = classify_validation_outcome(primary_patch, matching_val)

    # Never report validated success unless patch validation passed (or diagnostic-only conclusion with no patches)
    if patches:
        is_validated_success = (outcome == "passed")
    else:
        is_validated_success = bool(is_success_hint)

    confirmed_hyp = [h for h in hypotheses if h.status == "confirmed"]
    rejected_hyp = [h for h in hypotheses if h.status == "rejected"]

    proposed_correction = _build_proposed_correction_dict(primary_patch)
    validation_summary = _build_validation_summary_dict(outcome, primary_patch, matching_val)

    # Status label for presentation
    if outcome == "passed":
        status_label = "VALIDATED"
    elif outcome == "skipped":
        status_label = "VALIDATION_SKIPPED"
    elif outcome == "failed":
        status_label = "VALIDATION_FAILED"
    elif outcome == "rejected":
        status_label = "PATCH_REJECTED"
    elif is_validated_success:
        status_label = "COMPLETED"
    else:
        status_label = "INCONCLUSIVE"

    title = getattr(rep_input, "title", None) or f"Investigation Report: {inv.objective[:60]}"
    root_cause = (
        getattr(rep_input, "root_cause", None)
        or (confirmed_hyp[0].description if confirmed_hyp else None)
        or inv.root_cause_summary
        or (primary_patch.explanation if primary_patch else None)
        or "No singular root cause could be verified with necessary confidence."
    )

    if outcome == "passed" and primary_patch and matching_val:
        ts = validation_summary["test_summary"]
        summary = (
            f"Root cause identified and candidate fix for '{primary_patch.target_file}' "
            f"(patch {primary_patch.id}) was approved by '{primary_patch.approved_by}' and PASSED "
            f"isolated sandbox validation (run {matching_val.id}, exit code 0: "
            f"{ts['passed']} passed, {ts['failed']} failed, {ts['errors']} errors)."
        )
    elif outcome == "skipped" and primary_patch:
        summary = (
            f"Proposed patch {primary_patch.id} for '{primary_patch.target_file}' was approved by "
            f"'{primary_patch.approved_by}', but sandbox validation was SKIPPED because Docker "
            f"container isolation was unavailable. The fix has NOT been runtime-verified."
        )
    elif outcome == "failed" and primary_patch:
        exit_info = f" (exit code {matching_val.exit_code})" if matching_val else ""
        summary = (
            f"Proposed patch {primary_patch.id} for '{primary_patch.target_file}' FAILED validation"
            f"{exit_info}. The candidate correction cannot be represented as validated."
        )
    elif outcome == "rejected" and primary_patch:
        summary = (
            f"Proposed patch {primary_patch.id} for '{primary_patch.target_file}' was REJECTED by "
            f"operator ({primary_patch.rejection_reason or 'no reason supplied'}). Validation was not "
            f"executed and the fix remains unapplied."
        )
    else:
        summary = getattr(rep_input, "summary", None) or (
            f"Investigation concluded with status {status_label}. "
            f"Analyzed {len(evidence)} evidence records across {len(hypotheses)} hypotheses."
        )

    components = getattr(rep_input, "affected_components", None) or sorted(
        {e.source_file for e in evidence if e.source_file}
        | ({primary_patch.target_file} if primary_patch and primary_patch.target_file else set())
    )

    risk_notes: List[str] = []
    rep_risks = getattr(rep_input, "remaining_risks", None)
    if rep_risks:
        risk_notes.append(rep_risks)
    if primary_patch and primary_patch.potential_risks:
        risk_notes.append(f"Patch risks ({primary_patch.target_file}): {primary_patch.potential_risks}")
    if outcome == "skipped":
        risk_notes.append("Docker isolation was unavailable; regression tests were skipped and patch remains unverified.")
    elif outcome == "failed":
        risk_notes.append("Candidate validation failed; inspect validation stderr/stdout before revising or retrying.")
    elif outcome == "rejected":
        risk_notes.append("Human operator rejected the proposed patch; pipeline remains broken until a new fix is approved and validated.")
    elif patches and outcome == "not_run":
        risk_notes.append("Proposed patch has not completed human approval and isolated validation.")
    remaining_risks = " ".join(risk_notes) if risk_notes else "None recorded."

    if outcome == "passed":
        default_next_steps = [
            f"Review validated diff for {primary_patch.target_file} and apply to production branch",
            "Monitor pipeline execution metrics after deployment",
        ]
    elif outcome == "skipped":
        default_next_steps = [
            "Start local Docker daemon and build the PipelineSleuth sandbox image",
            "Re-submit or re-run validation in an isolated Docker container before applying any fix",
        ]
    elif outcome == "failed":
        default_next_steps = [
            "Inspect failed validation stdout/stderr and test summary",
            "Investigate failure details and formulate a corrected patch",
        ]
    elif outcome == "rejected":
        default_next_steps = [
            "Review operator rejection feedback",
            "Formulate a revised patch addressing the rejection reason",
        ]
    else:
        default_next_steps = ["Review evidence records", "Follow up on outstanding hypotheses"]

    next_steps = getattr(rep_input, "next_steps", None) or default_next_steps

    md_lines = [
        f"# {title}\n",
        f"**Status:** {status_label} | **Confidence:** {int((inv.confidence_score or 0.0) * 100)}%\n",
        f"### 1. Investigation Objective\n{inv.objective}\n",
        f"### 2. Summary of Failure & Outcome\n{summary}\n",
        "### 3. Affected Pipeline Components\n"
        + ("\n".join(f"- `{c}`" for c in components) if components else "- None recorded")
        + "\n",
        f"### 4. Root Cause Assessment\n{root_cause}\n",
        f"**Confidence Justification:** {inv.confidence_justification or 'Based on collected runtime evidence'}\n",
        "### 5. Supporting Traceable Evidence\n",
    ]
    if evidence:
        for e in evidence:
            md_lines.append(
                f"- **[{e.id}] ({e.evidence_type}, {e.verification_status})** `{e.source_file}`: {e.observation}"
            )
    else:
        md_lines.append("- None recorded.")

    md_lines.append("\n### 6. Hypotheses Evaluated\n")
    if hypotheses:
        for h in hypotheses:
            md_lines.append(
                f"- **{h.title}** (`{h.status}`, confidence: {h.confidence}): {h.reasoning or h.description}"
            )
    else:
        md_lines.append("- None recorded.")

    if primary_patch:
        md_lines.append("\n### 7. Proposed Correction & Approval Decision\n")
        md_lines.append(f"- **Patch ID:** `{primary_patch.id}` | **Target:** `{primary_patch.target_file}` | **Status:** `{primary_patch.status}`")
        if primary_patch.approved_by:
            md_lines.append(f"- **Approved By:** `{primary_patch.approved_by}` at `{primary_patch.approved_at.isoformat() if primary_patch.approved_at else 'N/A'}`")
        if primary_patch.rejected_at:
            md_lines.append(f"- **Rejected At:** `{primary_patch.rejected_at.isoformat()}` | **Reason:** {primary_patch.rejection_reason}")
        md_lines.append(f"- **Explanation:** {primary_patch.explanation}")
        if primary_patch.unified_diff:
            md_lines.append(f"\n```diff\n{primary_patch.unified_diff}\n```")

    md_lines.append("\n### 8. Sandbox Validation Outcome\n")
    if matching_val:
        ts = validation_summary["test_summary"]
        md_lines.append(
            f"- **Validation ID:** `{matching_val.id}` | **Outcome:** `{outcome.upper()}` (run status: `{matching_val.status}`, exit code: `{matching_val.exit_code}`)"
        )
        md_lines.append(
            f"- **Test Summary:** passed={ts['passed']}, failed={ts['failed']}, errors={ts['errors']}, total={ts['total']}"
        )
        if outcome != "passed" and matching_val.stderr:
            md_lines.append(f"- **Stderr Excerpt:** `{matching_val.stderr.strip()[:300]}`")
    else:
        md_lines.append(f"- **Outcome:** `{outcome.upper()}` — {validation_summary.get('reason', 'Not executed')}")

    md_lines.append(f"\n### 9. Remaining Risks\n{remaining_risks}\n")
    md_lines.append("### 10. Recommended Next Steps\n" + "\n".join(f"1. {s}" for s in next_steps))

    markdown_report = "\n".join(md_lines)

    report_fields = {
        "title": title,
        "summary": summary,
        "root_cause": root_cause,
        "affected_components": components,
        "supporting_evidence": [
            {
                "id": e.id,
                "type": e.evidence_type,
                "verification_status": e.verification_status,
                "obs": e.observation,
            }
            for e in evidence
        ],
        "hypotheses_evaluated": [{"id": h.id, "title": h.title, "status": h.status} for h in hypotheses],
        "hypotheses_rejected": [{"id": h.id, "title": h.title} for h in rejected_hyp],
        "proposed_correction": proposed_correction,
        "validation_summary": validation_summary,
        "remaining_risks": remaining_risks,
        "next_steps": next_steps,
        "markdown_content": markdown_report,
    }

    existing_res = await db.execute(
        select(InvestigationReport)
        .where(InvestigationReport.investigation_id == investigation_id)
        .order_by(InvestigationReport.created_at.desc(), InvestigationReport.id.desc())
    )
    existing_reports = list(existing_res.scalars().all())

    if existing_reports:
        report = existing_reports[0]
        for key, value in report_fields.items():
            setattr(report, key, value)
        if len(existing_reports) > 1:
            stale_ids = [r.id for r in existing_reports[1:]]
            await db.execute(delete(InvestigationReport).where(InvestigationReport.id.in_(stale_ids)))
        await db.commit()
        await db.refresh(report)
        return report

    report = InvestigationReport(
        investigation_id=investigation_id,
        **report_fields,
    )
    db.add(report)
    try:
        await db.commit()
        await db.refresh(report)
        return report
    except IntegrityError:
        await db.rollback()
        retry_res = await db.execute(
            select(InvestigationReport)
            .where(InvestigationReport.investigation_id == investigation_id)
            .order_by(InvestigationReport.created_at.desc(), InvestigationReport.id.desc())
        )
        existing = retry_res.scalars().first()
        if existing is None:
            raise
        for key, value in report_fields.items():
            setattr(existing, key, value)
        await db.commit()
        await db.refresh(existing)
        return existing
