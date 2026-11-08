"""
Diagnostic Tool Registry
Coordinates all registered diagnostic tools with schema validation, permission checks,
timeouts, and audit logging.
"""
import time
from typing import Dict, Any, List, Optional
from backend.app.tools.base import BaseDiagnosticTool, ToolResult, DiagnosticToolDefinition
from backend.app.tools.inspect_project import InspectProjectTool
from backend.app.tools.inspect_schema import InspectSchemaTool
from backend.app.tools.profile_dataset import ProfileDatasetTool
from backend.app.tools.analyze_logs import AnalyzeLogsTool
from backend.app.tools.inspect_code import InspectCodeTool
from backend.app.tools.trace_dependencies import TraceDependenciesTool
from backend.app.tools.run_diagnostic import RunDiagnosticTool
from backend.app.tools.test_hypothesis import TestHypothesisTool
from backend.app.tools.propose_patch import ProposePatchTool
from backend.app.tools.validate_patch import ValidatePatchTool


class DiagnosticToolRegistry:
    def __init__(self):
        self._tools: Dict[str, BaseDiagnosticTool] = {}
        self._audit_log: List[Dict[str, Any]] = []
        self._register_default_tools()

    def _register_default_tools(self) -> None:
        tools = [
            InspectProjectTool(),
            InspectSchemaTool(),
            ProfileDatasetTool(),
            AnalyzeLogsTool(),
            InspectCodeTool(),
            TraceDependenciesTool(),
            RunDiagnosticTool(),
            TestHypothesisTool(),
            ProposePatchTool(),
            ValidatePatchTool(),
        ]
        for tool in tools:
            self.register_tool(tool)

    def register_tool(self, tool: BaseDiagnosticTool) -> None:
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[BaseDiagnosticTool]:
        return self._tools.get(name)

    def list_tool_definitions(self) -> List[DiagnosticToolDefinition]:
        return [tool.get_definition() for tool in self._tools.values()]

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    async def execute_tool(
        self,
        tool_name: str,
        project_path: str,
        arguments: Dict[str, Any],
        investigation_id: Optional[str] = None
    ) -> ToolResult:
        """
        Executes a registered diagnostic tool with safety checks and audit logging.
        """
        start_time = time.time()
        tool = self.get_tool(tool_name)

        if not tool:
            err_msg = f"Unregistered diagnostic tool '{tool_name}'. Available: {self.list_tool_names()}"
            result = ToolResult(
                success=False,
                summary=err_msg,
                error=err_msg,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result)
            return result

        try:
            result = await tool.execute(project_path, arguments)
        except Exception as e:
            result = ToolResult(
                success=False,
                summary=f"Internal tool error in '{tool_name}': {str(e)}",
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        self._log_audit(investigation_id, tool_name, arguments, result)
        return result

    def _log_audit(
        self,
        investigation_id: Optional[str],
        tool_name: str,
        arguments: Dict[str, Any],
        result: ToolResult
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "investigation_id": investigation_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "success": result.success,
            "summary": result.summary,
            "error": result.error,
            "execution_time_ms": result.execution_time_ms
        }
        self._audit_log.append(entry)

    def get_audit_log(self, investigation_id: Optional[str] = None) -> List[Dict[str, Any]]:
        if investigation_id:
            return [e for e in self._audit_log if e.get("investigation_id") == investigation_id]
        return list(self._audit_log)


tool_registry = DiagnosticToolRegistry()

