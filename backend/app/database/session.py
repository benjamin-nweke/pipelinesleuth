"""
Database session and connection management
"""
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from backend.app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)

Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    # Import all models so metadata is populated
    import backend.app.models.project # noqa
    import backend.app.models.investigation # noqa
    import backend.app.models.patch # noqa
    import backend.app.models.report # noqa
    import backend.app.models.settings # noqa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
