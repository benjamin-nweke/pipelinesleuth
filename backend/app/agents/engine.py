"""
Autonomous AI Investigation Engine
Orchestrates bounded investigation loop, state transitions, tool execution,
evidence collection, hypothesis testing, loop prevention, and event broadcasting.
"""
import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Callable
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.models.investigation import (
    Investigation, InvestigationEvent, EvidenceRecord, Hypothesis, ToolExecution
)
from backend.app.models.patch import PatchProposal
from backend.app.models.report import InvestigationReport
from backend.app.models.project import Project, ProjectFile
from backend.app.tools.registry import tool_registry
from backend.app.agents.provider import AIProvider, ModelProviderError
from backend.app.agents.prompts import INVESTIGATION_SYSTEM_PROMPT


class InvestigationEngine:
    def __init__(
        self,
        db: AsyncSession,
        investigation_id: str,
        ai_provider: Optional[AIProvider] = None,
        event_callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ):
        self.db = db
        self.investigation_id = investigation_id
        self.provider = ai_provider or AIProvider()
        self.event_callback = event_callback
        self.max_tool_calls = settings.MAX_TOOL_CALLS
        self.max_repeated_tool = settings.MAX_REPEATED_TOOL_CALLS
        self.tool_call_counts: Dict[str, int] = {}
        self.last_tool_signature: Optional[str] = None

    async def emit_event(self, event_type: str, title: str, payload: Dict[str, Any]) -> InvestigationEvent:
        """Persists and broadcasts an investigation event."""
        event = InvestigationEvent(
            investigation_id=self.investigation_id,
            event_type=event_type,
            title=title,
            payload=payload
        )
        self.db.add(event)
        await self.db.commit()
        await self.db.refresh(event)

        if self.event_callback:
            try:
                self.event_callback({
                    "id": event.id,
                    "investigation_id": self.investigation_id,
                    "event_type": event_type,
                    "title": title,
                    "payload": payload,
                    "created_at": event.created_at.isoformat()
                })
            except Exception:
                pass

        return event

    async def run(self) -> None:
        """
        Executes the bounded autonomous investigative reasoning loop.
        """
        # Load investigation and project details
        inv_result = await self.db.execute(
            select(Investigation).where(Investigation.id == self.investigation_id)
        )
        investigation = inv_result.scalar_one_or_none()
        if not investigation:
            return

        proj_result = await self.db.execute(
            select(Project).where(Project.id == investigation.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if not project:
            await self.emit_event("error", "Project Not Found", {"error": "Project record missing."})
            return

        files_result = await self.db.execute(
            select(ProjectFile).where(ProjectFile.project_id == project.id)
        )
        project_files = files_result.scalars().all()
        project_storage_dir = str(settings.UPLOADS_DIR / project.id)

        investigation.status = "investigating"
        investigation.stage = "context_collection"
        await self.db.commit()

        await self.emit_event(
            "stage_change",
            "Investigation Started",
            {"stage": "context_collection", "objective": investigation.objective}
        )

        # Prepare tool definitions summary for the prompt
        tool_defs = tool_registry.list_tool_definitions()
        tool_defs_str = "\n".join([
            f"- `{t.name}`: {t.description} (params: {[p.name for p in t.parameters]})"
            for t in tool_defs
        ])

        project_files_summary = ", ".join([f.relative_path for f in project_files]) or "No files uploaded"

        consecutive_duplicate_count = 0
        loop_counter = 0

        while loop_counter < self.max_tool_calls:
            loop_counter += 1
            investigation.remaining_budget = self.max_tool_calls - loop_counter
            await self.db.commit()

            # Retrieve active hypotheses and evidence records from DB
            hyp_result = await self.db.execute(
                select(Hypothesis).where(Hypothesis.investigation_id == self.investigation_id)
            )
            active_hypotheses = hyp_result.scalars().all()
            hyp_summary = [
                f"[{h.id}] {h.title} (Status: {h.status}, Confidence: {h.confidence})"
                for h in active_hypotheses
            ]

            evd_result = await self.db.execute(
                select(EvidenceRecord).where(EvidenceRecord.investigation_id == self.investigation_id)
            )
            collected_evidence = evd_result.scalars().all()
            evd_summary = [
                f"[{e.id}] ({e.evidence_type}) {e.observation} in {e.source_file}"
                for e in collected_evidence
            ]

            # Retrieve recent tool execution results to feed empirical feedback to the model
            tool_exec_result = await self.db.execute(
                select(ToolExecution)
                .where(ToolExecution.investigation_id == self.investigation_id)
                .order_by(ToolExecution.created_at.desc())
                .limit(6)
            )
            recent_tool_execs = list(tool_exec_result.scalars().all())
            recent_tool_execs.reverse()
            recent_tools_summary = []
            for te in recent_tool_execs:
                output_str = json.dumps(te.output_result) if te.output_result else ""
                if len(output_str) > 1200:
                    output_str = output_str[:1200] + "... [truncated]"
                recent_tools_summary.append({
                    "tool": te.tool_name,
                    "arguments": te.input_arguments,
                    "status": te.status,
                    "error": te.error_message,
                    "result_summary": output_str
                })

            # Construct agent prompt
            prompt_content = f"""
Current Investigation Context:
- Project ID: {project.id}
- Project Name: {project.name}
- Pipeline Objective: {investigation.objective}
- Project Files: {project_files_summary}
- Remaining Budget: {investigation.remaining_budget} steps
- Current Stage: {investigation.stage}
- Hypotheses Formulated: {json.dumps(hyp_summary)}
- Evidence Records Logged: {json.dumps(evd_summary)}
- Recent Diagnostic Tool Outputs: {json.dumps(recent_tools_summary, indent=2)}

Inspect the available data, empirical tool findings, and decide whether to call a diagnostic tool,
formulate/test hypotheses, propose a patch, or conclude the investigation. Output pure JSON.
"""

            system_instruction = INVESTIGATION_SYSTEM_PROMPT.format(
                tool_definitions=tool_defs_str,
                investigation_id=self.investigation_id,
                objective=investigation.objective,
                project_files=project_files_summary,
                current_stage=investigation.stage,
                remaining_budget=investigation.remaining_budget,
                active_hypotheses=json.dumps(hyp_summary),
                collected_evidence=json.dumps(evd_summary),
                recent_tool_history=json.dumps(recent_tools_summary, indent=2)
            )

            # Call AI model provider
            await self.emit_event(
                "thought",
                "Analyzing Evidence and Planning Action",
                {"step": loop_counter, "stage": investigation.stage}
            )

            try:
                decision = await self.provider.generate_decision(
                    prompt=prompt_content,
                    system_instruction=system_instruction
                )
            except ModelProviderError as mpe:
                investigation.status = "failed"
                await self.db.commit()
                await self.emit_event(
                    "error",
                    "AI Model Unavailable",
                    {"error": str(mpe), "provider": self.provider.provider_type}
                )
                return

            # Update stage and confidence from decision
            investigation.stage = decision.current_stage
            investigation.confidence_score = decision.confidence_score
            investigation.confidence_justification = decision.confidence_justification
            await self.db.commit()

            # Record thought event
            await self.emit_event(
                "thought",
                f"Agent Reasoning (Step {loop_counter})",
                {
                    "thinking": decision.thinking,
                    "confidence_score": decision.confidence_score,
                    "stage": decision.current_stage
                }
            )

            # Process new or updated hypotheses
            for h_input in decision.hypotheses:
                existing_hyp = None
                if h_input.id:
                    for h in active_hypotheses:
                        if h.id == h_input.id:
                            existing_hyp = h
                            break

                if existing_hyp:
                    existing_hyp.title = h_input.title
                    existing_hyp.description = h_input.description
                    existing_hyp.status = h_input.status
                    existing_hyp.confidence = h_input.confidence
                    existing_hyp.reasoning = h_input.reasoning
                    await self.emit_event(
                        "hypothesis_update",
                        f"Updated Hypothesis: {existing_hyp.title}",
                        {"hypothesis_id": existing_hyp.id, "status": existing_hyp.status, "confidence": existing_hyp.confidence}
                    )
                else:
                    new_hyp = Hypothesis(
                        investigation_id=self.investigation_id,
                        title=h_input.title,
                        description=h_input.description,
                        status=h_input.status,
                        confidence=h_input.confidence,
                        reasoning=h_input.reasoning
                    )
                    self.db.add(new_hyp)
                    await self.db.commit()
                    await self.db.refresh(new_hyp)
                    await self.emit_event(
                        "hypothesis_update",
                        f"Formulated Hypothesis: {new_hyp.title}",
                        {"hypothesis_id": new_hyp.id, "status": new_hyp.status, "confidence": new_hyp.confidence}
                    )

            # Process new evidence records
            for ev_input in decision.new_evidence:
                ev_rec = EvidenceRecord(
                    investigation_id=self.investigation_id,
                    source_file=ev_input.source_file,
                    evidence_type=ev_input.evidence_type,
                    line_number=ev_input.line_number,
                    column_name=ev_input.column_name,
                    execution_stage=ev_input.execution_stage,
                    observation=ev_input.observation,
                    associated_hypothesis_id=ev_input.associated_hypothesis_id,
                    verification_status="verified"
                )
                self.db.add(ev_rec)
                await self.db.commit()
                await self.db.refresh(ev_rec)
                await self.emit_event(
                    "evidence_found",
                    f"Evidence Logged: {ev_rec.evidence_type}",
                    {
                        "evidence_id": ev_rec.id,
                        "source": ev_rec.source_file,
                        "observation": ev_rec.observation,
                        "line": ev_rec.line_number,
                        "column": ev_rec.column_name
                    }
                )

            # Handle Action Types
            action = decision.action_type

            if action == "call_tool" and decision.tool_call:
                tool_req = decision.tool_call
                tool_name = tool_req.tool_name
                args = tool_req.arguments

                # Loop prevention & duplicate detection
                current_sig = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
                if current_sig == self.last_tool_signature:
                    consecutive_duplicate_count += 1
                    if consecutive_duplicate_count >= self.max_repeated_tool:
                        await self.emit_event(
                            "thought",
                            "Loop Prevention Guard Triggered",
                            {
                                "message": f"Repeated tool call '{tool_name}' detected. Forcing hypothesis synthesis or report.",
                                "repeated_tool": tool_name
                            }
                        )
                        # Switch to synthesizing report
                        action = "conclude_inconclusive"
                else:
                    consecutive_duplicate_count = 0
                    self.last_tool_signature = current_sig

                self.tool_call_counts[tool_name] = self.tool_call_counts.get(tool_name, 0) + 1

                if action == "call_tool":
                    await self.emit_event(
                        "tool_call",
                        f"Invoking Diagnostic: {tool_name}",
                        {"tool_name": tool_name, "arguments": args}
                    )

                    tool_res = await tool_registry.execute_tool(
                        tool_name=tool_name,
                        project_path=project_storage_dir,
                        arguments=args,
                        investigation_id=self.investigation_id
                    )

                    # Persist ToolExecution record
                    t_exec = ToolExecution(
                        investigation_id=self.investigation_id,
                        tool_name=tool_name,
                        input_arguments=args,
                        output_result=tool_res.data,
                        error_message=tool_res.error,
                        execution_time_ms=tool_res.execution_time_ms,
                        status="success" if tool_res.success else "error"
                    )
                    self.db.add(t_exec)
                    await self.db.commit()

                    await self.emit_event(
                        "tool_result",
                        f"Diagnostic Result: {tool_name}",
                        {
                            "tool_name": tool_name,
                            "success": tool_res.success,
                            "summary": tool_res.summary,
                            "duration_ms": tool_res.execution_time_ms
                        }
                    )
                    continue

            if action == "propose_patch" and decision.patch_proposal:
                p_in = decision.patch_proposal
                # Call propose_patch tool for unified diff and syntax verification
                patch_tool_res = await tool_registry.execute_tool(
                    tool_name="propose_patch",
                    project_path=project_storage_dir,
                    arguments={
                        "target_file": p_in.target_file,
                        "original_snippet": p_in.original_code_context,
                        "replacement_code": p_in.proposed_code,
                        "explanation": p_in.explanation,
                        "supporting_evidence_ids": p_in.supporting_evidence_ids,
                        "expected_impact": p_in.expected_impact,
                        "potential_risks": p_in.potential_risks
                    },
                    investigation_id=self.investigation_id
                )

                if patch_tool_res.success:
                    p_data = patch_tool_res.data
                    proposal = PatchProposal(
                        investigation_id=self.investigation_id,
                        target_file=p_data["target_file"],
                        original_code_context=p_data["original_code_context"],
                        proposed_code=p_data["proposed_code"],
                        unified_diff=p_data["unified_diff"],
                        explanation=p_data["explanation"],
                        supporting_evidence_ids=p_data["supporting_evidence_ids"],
                        expected_impact=p_data["expected_impact"],
                        potential_risks=p_data["potential_risks"],
                        status="proposed"
                    )
                    self.db.add(proposal)
                    investigation.status = "awaiting_patch_approval"
                    investigation.stage = "awaiting_approval"
                    await self.db.commit()
                    await self.db.refresh(proposal)

                    await self.emit_event(
                        "patch_generated",
                        f"Proposed Code Correction: {proposal.target_file}",
                        {
                            "patch_id": proposal.id,
                            "target_file": proposal.target_file,
                            "explanation": proposal.explanation,
                            "unified_diff": proposal.unified_diff
                        }
                    )
                    # Await human review and approval
                    return
                else:
                    await self.emit_event(
                        "error",
                        "Patch Generation Failed Syntax Check",
                        {"error": patch_tool_res.error, "summary": patch_tool_res.summary}
                    )
                    continue

            if action in ("conclude_success", "conclude_inconclusive") or loop_counter >= self.max_tool_calls:
                final_rep = decision.final_report
                is_success = (action == "conclude_success" and investigation.confidence_score >= 0.7)

                investigation.status = "completed" if is_success else "inconclusive"
                investigation.stage = "report_generation"
                investigation.root_cause_summary = final_rep.root_cause if final_rep else "Insufficient evidence to isolate a singular root cause."
                await self.db.commit()

                # Generate comprehensive final report
                await self._build_final_report(final_rep, is_success)
                return

        # Fallback if loop finishes without explicit conclusion
        investigation.status = "inconclusive"
        investigation.stage = "report_generation"
        await self.db.commit()
        await self._build_final_report(None, False)

    async def _build_final_report(self, rep_input: Optional[Any], is_success: bool) -> None:
        """Constructs and stores the final markdown investigation report."""
        inv_result = await self.db.execute(
            select(Investigation).where(Investigation.id == self.investigation_id)
        )
        inv = inv_result.scalar_one()

        ev_result = await self.db.execute(
            select(EvidenceRecord).where(EvidenceRecord.investigation_id == self.investigation_id)
        )
        evidence = ev_result.scalars().all()

        hyp_result = await self.db.execute(
            select(Hypothesis).where(Hypothesis.investigation_id == self.investigation_id)
        )
        hypotheses = hyp_result.scalars().all()

        confirmed_hyp = [h for h in hypotheses if h.status == "confirmed"]
        rejected_hyp = [h for h in hypotheses if h.status == "rejected"]

        title = getattr(rep_input, "title", None) or f"Investigation Report: {inv.objective[:50]}"
        summary = getattr(rep_input, "summary", None) or (
            f"Investigation concluded with verdict: {'CONFIRMED ROOT CAUSE' if is_success else 'INCONCLUSIVE'}. "
            f"Analyzed {len(evidence)} evidence records across {len(hypotheses)} hypotheses."
        )
        root_cause = getattr(rep_input, "root_cause", None) or (
            confirmed_hyp[0].description if confirmed_hyp else "No singular root cause could be verified with necessary confidence."
        )
        components = getattr(rep_input, "affected_components", []) or list(set(e.source_file for e in evidence))
        risks = getattr(rep_input, "remaining_risks", None) or "None recorded."
        next_steps = getattr(rep_input, "next_steps", []) or ["Review evidence records", "Deploy approved fixes"]

        md_lines = [
            f"# {title}\n",
            f"**Status:** {'COMPLETED' if is_success else 'INCONCLUSIVE'} | **Confidence:** {int(inv.confidence_score * 100)}%\n",
            f"### 1. Investigation Objective\n{inv.objective}\n",
            f"### 2. Summary of Failure\n{summary}\n",
            f"### 3. Affected Pipeline Components\n" + "\n".join([f"- `{c}`" for c in components]) + "\n",
            f"### 4. Root Cause Assessment\n{root_cause}\n",
            f"**Confidence Justification:** {inv.confidence_justification or 'Based on collected runtime evidence'}\n",
            f"### 5. Supporting Traceable Evidence\n"
        ]
        for e in evidence:
            md_lines.append(f"- **[{e.id}] ({e.evidence_type})** `{e.source_file}`: {e.observation}")
        
        md_lines.append(f"\n### 6. Hypotheses Evaluated\n")
        for h in hypotheses:
            md_lines.append(f"- **{h.title}** (`{h.status}`, confidence: {h.confidence}): {h.reasoning or h.description}")

        md_lines.append(f"\n### 7. Remaining Risks\n{risks}\n")
        md_lines.append(f"### 8. Recommended Next Steps\n" + "\n".join([f"1. {s}" for s in next_steps]))

        markdown_report = "\n".join(md_lines)

        report = InvestigationReport(
            investigation_id=self.investigation_id,
            title=title,
            summary=summary,
            root_cause=root_cause,
            affected_components=components,
            supporting_evidence=[{"id": e.id, "type": e.evidence_type, "obs": e.observation} for e in evidence],
            hypotheses_evaluated=[{"id": h.id, "title": h.title, "status": h.status} for h in hypotheses],
            hypotheses_rejected=[{"id": h.id, "title": h.title} for h in rejected_hyp],
            remaining_risks=risks,
            next_steps=next_steps,
            markdown_content=markdown_report
        )
        self.db.add(report)
        await self.db.commit()

        await self.emit_event(
            "report_ready",
            f"Final Report Generated: {title}",
            {"report_id": report.id, "root_cause": root_cause, "is_success": is_success}
        )

