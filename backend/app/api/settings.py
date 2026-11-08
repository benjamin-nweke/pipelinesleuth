"""
Settings API router
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.database.session import get_db
from backend.app.models.settings import ApplicationSettings
from backend.app.schemas.settings import SettingsRead, SettingsUpdate, ConnectivityTestResult
from backend.app.agents.provider import AIProvider
from backend.app.sandbox.manager import sandbox_manager

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=SettingsRead)
async def get_settings(db: AsyncSession = Depends(get_db)):
    docker_ok, _ = await sandbox_manager.check_docker_available()

    res = await db.execute(select(ApplicationSettings).limit(1))
    db_settings = res.scalar_one_or_none()

    if db_settings:
        return SettingsRead(
            ai_provider=db_settings.ai_provider,
            ollama_base_url=db_settings.ollama_base_url,
            ollama_model=db_settings.ollama_model,
            openai_base_url=db_settings.openai_base_url,
            openai_model=db_settings.openai_model,
            has_openai_key=bool(db_settings.openai_api_key_masked),
            max_tool_calls=db_settings.max_tool_calls,
            sandbox_timeout_seconds=db_settings.sandbox_timeout_seconds,
            docker_available=docker_ok,
            extra_config=db_settings.extra_config or {},
            updated_at=db_settings.updated_at
        )

    # Defaults from config
    return SettingsRead(
        ai_provider=settings.AI_PROVIDER,
        ollama_base_url=settings.OLLAMA_BASE_URL,
        ollama_model=settings.OLLAMA_MODEL,
        openai_base_url=settings.OPENAI_BASE_URL,
        openai_model=settings.OPENAI_MODEL,
        has_openai_key=bool(settings.OPENAI_API_KEY),
        max_tool_calls=settings.MAX_TOOL_CALLS,
        sandbox_timeout_seconds=settings.SANDBOX_TIMEOUT_SECONDS,
        docker_available=docker_ok,
        extra_config={},
        updated_at=datetime.now(timezone.utc)
    )


@router.patch("", response_model=SettingsRead)
async def update_settings(update_data: SettingsUpdate, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ApplicationSettings).limit(1))
    db_settings = res.scalar_one_or_none()

    if not db_settings:
        db_settings = ApplicationSettings(
            ai_provider=settings.AI_PROVIDER,
            ollama_base_url=settings.OLLAMA_BASE_URL,
            ollama_model=settings.OLLAMA_MODEL,
            openai_base_url=settings.OPENAI_BASE_URL,
            openai_model=settings.OPENAI_MODEL,
            max_tool_calls=settings.MAX_TOOL_CALLS,
            sandbox_timeout_seconds=settings.SANDBOX_TIMEOUT_SECONDS
        )
        db.add(db_settings)

    if update_data.ai_provider is not None:
        db_settings.ai_provider = update_data.ai_provider
        settings.AI_PROVIDER = update_data.ai_provider
    if update_data.ollama_base_url is not None:
        db_settings.ollama_base_url = update_data.ollama_base_url
        settings.OLLAMA_BASE_URL = update_data.ollama_base_url
    if update_data.ollama_model is not None:
        db_settings.ollama_model = update_data.ollama_model
        settings.OLLAMA_MODEL = update_data.ollama_model
    if update_data.openai_base_url is not None:
        db_settings.openai_base_url = update_data.openai_base_url
        settings.OPENAI_BASE_URL = update_data.openai_base_url
    if update_data.openai_model is not None:
        db_settings.openai_model = update_data.openai_model
        settings.OPENAI_MODEL = update_data.openai_model
    if update_data.openai_api_key is not None:
        db_settings.openai_api_key_masked = "•" * 12 + update_data.openai_api_key[-4:] if len(update_data.openai_api_key) > 4 else "••••"
        settings.OPENAI_API_KEY = update_data.openai_api_key
    if update_data.max_tool_calls is not None:
        db_settings.max_tool_calls = update_data.max_tool_calls
        settings.MAX_TOOL_CALLS = update_data.max_tool_calls
    if update_data.sandbox_timeout_seconds is not None:
        db_settings.sandbox_timeout_seconds = update_data.sandbox_timeout_seconds
        settings.SANDBOX_TIMEOUT_SECONDS = update_data.sandbox_timeout_seconds

    await db.commit()
    await db.refresh(db_settings)

    docker_ok, _ = await sandbox_manager.check_docker_available()
    return SettingsRead(
        ai_provider=db_settings.ai_provider,
        ollama_base_url=db_settings.ollama_base_url,
        ollama_model=db_settings.ollama_model,
        openai_base_url=db_settings.openai_base_url,
        openai_model=db_settings.openai_model,
        has_openai_key=bool(db_settings.openai_api_key_masked),
        max_tool_calls=db_settings.max_tool_calls,
        sandbox_timeout_seconds=db_settings.sandbox_timeout_seconds,
        docker_available=docker_ok,
        extra_config=db_settings.extra_config or {},
        updated_at=db_settings.updated_at
    )


@router.post("/test-connectivity", response_model=ConnectivityTestResult)
async def test_connectivity(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ApplicationSettings).limit(1))
    db_settings = res.scalar_one_or_none()

    provider = AIProvider(
        provider_type=db_settings.ai_provider if db_settings else settings.AI_PROVIDER,
        ollama_base_url=db_settings.ollama_base_url if db_settings else settings.OLLAMA_BASE_URL,
        ollama_model=db_settings.ollama_model if db_settings else settings.OLLAMA_MODEL,
        openai_base_url=db_settings.openai_base_url if db_settings else settings.OPENAI_BASE_URL,
        openai_api_key=settings.OPENAI_API_KEY,
        openai_model=db_settings.openai_model if db_settings else settings.OPENAI_MODEL,
    )

    success, message, latency = await provider.test_connectivity()
    return ConnectivityTestResult(
        success=success,
        provider=provider.provider_type,
        model=provider.ollama_model if provider.provider_type == "ollama" else provider.openai_model,
        latency_ms=latency,
        message=message
    )
