"""
Diagnostic Tool Registry
Coordinates all registered diagnostic tools with schema validation, permission checks,
timeouts, and audit logging.
"""
import time
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from backend.app.tools.base import BaseDiagnosticTool, ToolResult, DiagnosticToolDefinition, ToolParameter
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

    def list_agent_tool_definitions(self) -> List[DiagnosticToolDefinition]:
        """Returns only the tool definitions that the AI agent is authorized to call."""
        return [
            tool.get_definition()
            for tool in self._tools.values()
            if tool.permission_level != "human_approval_required"
        ]

    def list_tool_names(self) -> List[str]:
        return list(self._tools.keys())

    def _validate_arguments(
        self,
        tool: BaseDiagnosticTool,
        arguments: Dict[str, Any]
    ) -> Tuple[bool, Optional[str]]:
        """
        Centrally validates arguments against the tool's parameter schema.
        Rejects missing required arguments, unexpected arguments, and type mismatches.
        """
        definition = tool.get_definition()
        declared_params = {p.name: p for p in definition.parameters}

        # 1. Reject unexpected / undeclared arguments
        for arg_name in arguments.keys():
            if arg_name not in declared_params:
                return False, f"Unexpected argument '{arg_name}' not in tool schema. Allowed: {sorted(list(declared_params.keys()))}"

        # 2. Check for missing required arguments
        for param_name, param in declared_params.items():
            if param.required and param.default is None:
                if param_name not in arguments or arguments[param_name] is None:
                    return False, f"Missing required argument '{param_name}' of type {param.type}."

        # 3. Type check provided arguments
        for arg_name, val in arguments.items():
            if val is None:
                continue
            param = declared_params.get(arg_name)
            if not param:
                continue

            ptype = param.type.lower()
            if ptype == "string" and not isinstance(val, str):
                return False, f"Argument '{arg_name}' must be of type string, got {type(val).__name__}."
            elif ptype == "integer" and (not isinstance(val, int) or isinstance(val, bool)):
                return False, f"Argument '{arg_name}' must be of type integer, got {type(val).__name__}."
            elif ptype == "number" and (not isinstance(val, (int, float)) or isinstance(val, bool)):
                return False, f"Argument '{arg_name}' must be of type number, got {type(val).__name__}."
            elif ptype == "boolean" and not isinstance(val, bool):
                return False, f"Argument '{arg_name}' must be of type boolean, got {type(val).__name__}."
            elif ptype in ("array", "list") and not isinstance(val, list):
                return False, f"Argument '{arg_name}' must be of type array, got {type(val).__name__}."
            elif ptype in ("dict", "object") and not isinstance(val, dict):
                return False, f"Argument '{arg_name}' must be of type object/dict, got {type(val).__name__}."

        return True, None

    async def execute_tool(
        self,
        tool_name: str,
        project_path: str,
        arguments: Dict[str, Any],
        investigation_id: Optional[str] = None,
        caller_role: str = "agent",  # "agent", "human_approved_workflow", "system", "unauthorized"
        remaining_budget: Optional[int] = None,
        execution_context: Optional[Dict[str, Any]] = None
    ) -> ToolResult:
        """
        Executes a registered diagnostic tool with central schema validation, permission checks,
        execution budget guards, per-tool timeouts, and tamper-resistant audit logging.
        """
        start_time = time.time()
        tool = self.get_tool(tool_name)

        # 1. Unknown tool check
        if not tool:
            err_msg = f"Unregistered diagnostic tool '{tool_name}'. Available: {self.list_tool_names()}"
            result = ToolResult(
                success=False,
                summary=err_msg,
                error=err_msg,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
            return result

        # 2. Execution budget check
        if remaining_budget is not None and remaining_budget <= 0:
            err_msg = f"Execution budget exhausted for investigation '{investigation_id}'. No further tool calls permitted."
            result = ToolResult(
                success=False,
                summary="Tool execution budget exhausted.",
                error=err_msg,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
            return result

        # 3. Central permission enforcement
        if caller_role == "unauthorized":
            err_msg = f"Permission denied: Caller '{caller_role}' is not authorized to execute tools."
            result = ToolResult(
                success=False,
                summary="Permission denied: Unauthorized caller.",
                error=err_msg,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
            return result

        if tool.permission_level == "human_approval_required" and caller_role != "human_approved_workflow":
            err_msg = (
                f"Permission denied: Tool '{tool_name}' requires explicit human approval and cannot be invoked "
                f"by caller role '{caller_role}'. Direct AI invocation of patch validation is strictly blocked."
            )
            result = ToolResult(
                success=False,
                summary="Security violation: Direct invocation of patch validation by AI is blocked.",
                error=err_msg,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
            return result

        # 4. Central schema & argument validation
        valid_args, arg_err = self._validate_arguments(tool, arguments)
        if not valid_args:
            result = ToolResult(
                success=False,
                summary=f"Invalid arguments for tool '{tool_name}': {arg_err}",
                error=arg_err,
                execution_time_ms=0
            )
            self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
            return result

        # 5. Central per-tool timeout enforcement
        timeout = tool.timeout_seconds
        try:
            result = await asyncio.wait_for(tool.execute(project_path, arguments), timeout=timeout)
        except asyncio.TimeoutError:
            result = ToolResult(
                success=False,
                summary=f"Diagnostic tool '{tool_name}' timed out after {timeout} seconds.",
                error=f"Timeout: Execution exceeded {timeout} seconds.",
                execution_time_ms=int(timeout * 1000)
            )
        except Exception as e:
            result = ToolResult(
                success=False,
                summary=f"Internal tool error in '{tool_name}': {str(e)}",
                error=str(e),
                execution_time_ms=int((time.time() - start_time) * 1000)
            )

        self._log_audit(investigation_id, tool_name, arguments, result, caller_role)
        return result

    def _log_audit(
        self,
        investigation_id: Optional[str],
        tool_name: str,
        arguments: Dict[str, Any],
        result: ToolResult,
        caller_role: str = "agent"
    ) -> None:
        entry = {
            "timestamp": time.time(),
            "investigation_id": investigation_id,
            "tool_name": tool_name,
            "caller_role": caller_role,
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

