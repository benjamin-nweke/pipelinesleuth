"""
Pydantic schemas for Settings
"""
from datetime import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field


class SettingsRead(BaseModel):
    ai_provider: str
    ollama_base_url: str
    ollama_model: str
    openai_base_url: str
    openai_model: str
    has_openai_key: bool
    max_tool_calls: int
    sandbox_timeout_seconds: int
    docker_available: bool
    extra_config: Dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime


class SettingsUpdate(BaseModel):
    ai_provider: Optional[str] = None
    ollama_base_url: Optional[str] = None
    ollama_model: Optional[str] = None
    openai_base_url: Optional[str] = None
    openai_model: Optional[str] = None
    openai_api_key: Optional[str] = None
    max_tool_calls: Optional[int] = None
    sandbox_timeout_seconds: Optional[int] = None


class ConnectivityTestResult(BaseModel):
    success: bool
    provider: str
    model: str
    latency_ms: int
    message: str

