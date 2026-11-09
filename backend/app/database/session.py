"""
Database session and connection management
"""
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from backend.app.core.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30.0}
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

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

    def _sync_upgrade_schema(connection):
        from sqlalchemy import text
        # Ensure patch_proposals has concurrency tracking columns
        res = connection.execute(text("PRAGMA table_info(patch_proposals)"))
        columns = [row[1] for row in res.fetchall()]
        if columns:
            if "validation_claimed_at" not in columns:
                connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_claimed_at DATETIME"))
            if "validation_claimed_by" not in columns:
                connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_claimed_by VARCHAR(255)"))

        # Ensure validation_runs has unique index on patch_id
        res = connection.execute(text("PRAGMA index_list(validation_runs)"))
        indexes = [row[1] for row in res.fetchall()]
        if "ix_validation_runs_patch_id_unique" not in indexes and "uq_validation_runs_patch_id" not in indexes:
            try:
                connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_validation_runs_patch_id ON validation_runs (patch_id)"))
            except Exception:
                pass

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_sync_upgrade_schema)

