"""
Base classes and interfaces for Diagnostic Tools
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field


class ToolParameter(BaseModel):
    name: str
    type: str
    description: str
    required: bool = True
    default: Optional[Any] = None


class DiagnosticToolDefinition(BaseModel):
    name: str
    description: str
    parameters: list[ToolParameter]
    permission_level: str = "read_only"  # read_only, sandboxed_exec, patch_prep
    timeout_seconds: int = 30


class ToolResult(BaseModel):
    success: bool
    data: Dict[str, Any] = Field(default_factory=dict)
    summary: str
    error: Optional[str] = None
    execution_time_ms: int = 0


class BaseDiagnosticTool(ABC):
    name: str
    description: str
    permission_level: str = "read_only"
    timeout_seconds: int = 30

    @abstractmethod
    def get_definition(self) -> DiagnosticToolDefinition:
        """Return the schema definition of the tool."""
        pass

    @abstractmethod
    async def execute(self, project_path: str, arguments: Dict[str, Any]) -> ToolResult:
        """Execute the tool safely and return structured result."""
        pass
