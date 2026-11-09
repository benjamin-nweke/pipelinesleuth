"""
Database session and connection management
"""
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from backend.app.core.config import settings

"""
Database session and connection management
"""
import logging
from typing import AsyncGenerator
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from backend.app.core.config import settings

logger = logging.getLogger(__name__)


def check_supported_database_dialect(url: str) -> None:
    """
    Enforces that PipelineSleuth is currently configured with SQLite.
    Fails early and clearly if PostgreSQL, MySQL, or other engines are configured.
    """
    clean_url = url.strip().lower() if url else ""
    if not (clean_url.startswith("sqlite:") or clean_url.startswith("sqlite+aiosqlite:")):
        dialect_name = clean_url.split("://")[0] if "://" in clean_url else "unknown"
        raise RuntimeError(
            f"Unsupported database engine configured in DATABASE_URL: '{dialect_name}'. "
            "PipelineSleuth currently only supports SQLite ('sqlite+aiosqlite:///...'). "
            "PostgreSQL and other external database engines are not supported in this release."
        )


check_supported_database_dialect(settings.DATABASE_URL)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False, "timeout": 30.0}
)


@event.listens_for(engine.sync_engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if engine.dialect.name == "sqlite":
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


def sync_upgrade_schema(connection) -> None:
    """
    Performs deterministic schema upgrades and enforces uniqueness invariants.
    Inspects tables, resolves duplicate records deterministically, and creates
    required indexes without suppressing errors.
    """
    # 1. Ensure patch_proposals has concurrency and lease tracking columns
    res = connection.execute(text("PRAGMA table_info(patch_proposals)"))
    columns = [row[1] for row in res.fetchall()]
    if columns:
        if "validation_claimed_at" not in columns:
            connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_claimed_at DATETIME"))
        if "validation_claimed_by" not in columns:
            connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_claimed_by VARCHAR(255)"))
        if "validation_claim_token" not in columns:
            connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_claim_token VARCHAR(64)"))
        if "validation_lease_expires_at" not in columns:
            connection.execute(text("ALTER TABLE patch_proposals ADD COLUMN validation_lease_expires_at DATETIME"))

    # 2. Enforce unique index on validation_runs(patch_id)
    res = connection.execute(text("SELECT name FROM sqlite_master WHERE type='table' AND name='validation_runs'"))
    if not res.fetchone():
        return

    res = connection.execute(text("PRAGMA index_list(validation_runs)"))
    indexes = [row[1] for row in res.fetchall()]
    has_unique = any("unique" in idx.lower() or "patch_id" in idx.lower() for idx in indexes)

    if not has_unique:
        # Inspect existing records for duplicate patch IDs
        dup_query = text("""
            SELECT patch_id, COUNT(*) as cnt 
            FROM validation_runs 
            GROUP BY patch_id 
            HAVING cnt > 1
        """)
        duplicates = connection.execute(dup_query).fetchall()

        if duplicates:
            for dup_row in duplicates:
                p_id = dup_row[0]
                runs = connection.execute(
                    text("SELECT id, status, created_at FROM validation_runs WHERE patch_id = :p_id ORDER BY created_at DESC, id DESC"),
                    {"p_id": p_id}
                ).fetchall()

                # Check if conflicting definitive terminal states exist
                statuses = {r[1] for r in runs}
                if "passed" in statuses and "failed" in statuses:
                    raise RuntimeError(
                        f"Database uniqueness migration failed: Detected duplicate validation runs for patch '{p_id}' "
                        f"with irreconcilable conflicting statuses {statuses}. "
                        "Manual operator remediation is required before the unique constraint can be enforced."
                    )

                # Deterministic remediation: retain the latest record and remove older duplicates
                keep_id = runs[0][0]
                delete_ids = [r[0] for r in runs[1:]]
                for del_id in delete_ids:
                    connection.execute(
                        text("DELETE FROM validation_runs WHERE id = :del_id"),
                        {"del_id": del_id}
                    )
                logger.info(f"Remediated duplicate validation runs for patch '{p_id}': retained {keep_id}, removed {len(delete_ids)} duplicates.")

        # Create unique index without swallowing exceptions
        try:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_validation_runs_patch_id ON validation_runs (patch_id)"))
        except Exception as e:
            raise RuntimeError(f"Database migration error: Failed to create unique index 'uq_validation_runs_patch_id': {e}") from e


async def init_db() -> None:
    # Validate dialect
    check_supported_database_dialect(settings.DATABASE_URL)

    # Import all models so metadata is populated
    import backend.app.models.project # noqa
    import backend.app.models.investigation # noqa
    import backend.app.models.patch # noqa
    import backend.app.models.report # noqa
    import backend.app.models.settings # noqa

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(sync_upgrade_schema)

