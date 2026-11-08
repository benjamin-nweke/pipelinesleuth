"""
PipelineSleuth Core Configuration
"""
from pathlib import Path
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
STORAGE_DIR = BASE_DIR / "storage"
UPLOADS_DIR = STORAGE_DIR / "uploads"
WORKSPACES_DIR = STORAGE_DIR / "workspaces"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="allow")

    PROJECT_NAME: str = "PipelineSleuth"
    VERSION: str = "1.0.0"
    API_V1_PREFIX: str = "/api"

    # Persistence
    DATABASE_URL: str = f"sqlite+aiosqlite:///{STORAGE_DIR}/pipelinesleuth.db"
    
    # Storage Paths
    BASE_DIR: Path = BASE_DIR
    STORAGE_DIR: Path = STORAGE_DIR
    UPLOADS_DIR: Path = UPLOADS_DIR
    WORKSPACES_DIR: Path = WORKSPACES_DIR
    DEMO_DIR: Path = BASE_DIR / "demo"

    # AI Provider Settings
    AI_PROVIDER: str = "ollama"  # "ollama" or "openai"
    OLLAMA_BASE_URL: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3.1:8b"
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4o"

    # Agent Loop Boundaries
    MAX_TOOL_CALLS: int = 12
    MAX_REPEATED_TOOL_CALLS: int = 2
    PER_TOOL_TIMEOUT_SECONDS: int = 30
    INVESTIGATION_TIMEOUT_SECONDS: int = 300

    # Sandbox Execution Settings
    DOCKER_IMAGE: str = "python:3.11-slim"
    SANDBOX_TIMEOUT_SECONDS: int = 60
    SANDBOX_MEMORY_LIMIT: str = "1g"
    SANDBOX_CPU_QUOTA: int = 100000  # 1 CPU
    ENABLE_DOCKER_SANDBOX: bool = True

    # Security & Uploads
    MAX_UPLOAD_SIZE_BYTES: int = 50 * 1024 * 1024  # 50MB
    ALLOWED_EXTENSIONS: list[str] = [
        ".py", ".csv", ".parquet", ".json", ".log", ".txt", ".sql", ".yaml", ".yml", ".md"
    ]




settings = Settings()

# Ensure directories exist
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
WORKSPACES_DIR.mkdir(parents=True, exist_ok=True)
