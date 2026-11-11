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


def check_unique_single_column_index(connection, table_name: str, column_name: str) -> bool:
    """
    Directly inspects SQLite index metadata for a unique single-column index
    on `table_name(column_name)` via PRAGMA index_list and PRAGMA index_info.
    """
    res = connection.execute(text(f"PRAGMA index_list('{table_name}')"))
    for row in res.fetchall():
        # SQLite PRAGMA index_list returns: (seq, name, unique, origin, partial)
        idx_name = row[1]
        is_unique = bool(row[2])
        if is_unique:
            safe_idx = str(idx_name).replace("'", "''")
            info_res = connection.execute(text(f"PRAGMA index_info('{safe_idx}')"))
            cols = [col_row[2] for col_row in info_res.fetchall()]
            if cols == [column_name]:
                return True
    return False


def check_validation_runs_unique_index(connection) -> bool:
    """
    Directly inspects SQLite index metadata for a unique index on validation_runs(patch_id).
    """
    return check_unique_single_column_index(connection, "validation_runs", "patch_id")


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
    val_tbl = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='validation_runs'")
    ).fetchone()
    if val_tbl and not check_validation_runs_unique_index(connection):
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

        try:
            connection.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_validation_runs_patch_id ON validation_runs (patch_id)"))
        except Exception as e:
            raise RuntimeError(f"Database migration error: Failed to create unique index 'uq_validation_runs_patch_id': {e}") from e

        if not check_validation_runs_unique_index(connection):
            raise RuntimeError(
                "Database migration verification error: Unique index on validation_runs(patch_id) "
                "could not be verified in SQLite index metadata after creation."
            )

    # 3. Enforce unique index on investigation_reports(investigation_id)
    rep_tbl = connection.execute(
        text("SELECT name FROM sqlite_master WHERE type='table' AND name='investigation_reports'")
    ).fetchone()
    if rep_tbl and not check_unique_single_column_index(connection, "investigation_reports", "investigation_id"):
        rep_dups = connection.execute(text("""
            SELECT investigation_id, COUNT(*) as cnt
            FROM investigation_reports
            GROUP BY investigation_id
            HAVING cnt > 1
        """)).fetchall()

        for dup_row in rep_dups:
            inv_id = dup_row[0]
            rows = connection.execute(
                text(
                    "SELECT id FROM investigation_reports "
                    "WHERE investigation_id = :inv_id ORDER BY created_at DESC, id DESC"
                ),
                {"inv_id": inv_id}
            ).fetchall()
            keep_id = rows[0][0]
            for row in rows[1:]:
                connection.execute(
                    text("DELETE FROM investigation_reports WHERE id = :del_id"),
                    {"del_id": row[0]}
                )
            logger.info(
                f"Remediated duplicate investigation reports for investigation '{inv_id}': "
                f"retained {keep_id}, removed {len(rows) - 1} duplicates."
            )

        try:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS "
                    "uq_investigation_reports_investigation_id ON investigation_reports (investigation_id)"
                )
            )
        except Exception as e:
            raise RuntimeError(
                f"Database migration error: Failed to create unique index 'uq_investigation_reports_investigation_id': {e}"
            ) from e

        if not check_unique_single_column_index(connection, "investigation_reports", "investigation_id"):
            raise RuntimeError(
                "Database migration verification error: Unique index on investigation_reports(investigation_id) "
                "could not be verified in SQLite index metadata after creation."
            )


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

