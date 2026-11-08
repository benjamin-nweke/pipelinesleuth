"""
ApplicationSettings model for persistent app-wide settings
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, Integer, DateTime, JSON
from backend.app.database.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationSettings(Base):
    __tablename__ = "application_settings"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    ai_provider = Column(String(50), default="ollama", nullable=False)
    ollama_base_url = Column(String(255), default="http://127.0.0.1:11434")
    ollama_model = Column(String(100), default="llama3.1:8b")
    openai_base_url = Column(String(255), default="https://api.openai.com/v1")
    openai_model = Column(String(100), default="gpt-4o")
    openai_api_key_masked = Column(String(100), nullable=True)
    max_tool_calls = Column(Integer, default=12)
    sandbox_timeout_seconds = Column(Integer, default=60)
    extra_config = Column(JSON, default=dict)
    updated_at = Column(DateTime, default=utcnow, onupdate=utcnow, nullable=False)
