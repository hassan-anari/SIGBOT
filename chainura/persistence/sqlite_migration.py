"""SQLite migration integrity guard used by the Alembic online runner."""
from __future__ import annotations

from collections.abc import Callable


class SQLiteMigrationIntegrityError(RuntimeError):
    """The migrated schema contains at least one broken foreign key."""


def validate_sqlite_foreign_keys(connection) -> None:
    rows = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if not rows:
        return
    # PRAGMA also returns child rowids. Never include those values: table and
    # referenced-table names are sufficient, privacy-neutral diagnostics.
    relationships = sorted({f"{row[0]}->{row[2]}" for row in rows})
    raise SQLiteMigrationIntegrityError(
        "SQLite migration foreign-key validation failed "
        f"count={len(rows)} relationships={','.join(relationships)}"
    )


def run_with_sqlite_foreign_keys_suspended(connection, operation: Callable[[], None]) -> None:
    """Run migration DDL safely, validate it, and always restore enforcement."""
    connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
    connection.commit()
    try:
        operation()
        validate_sqlite_foreign_keys(connection)
    except BaseException:
        if connection.in_transaction():
            connection.rollback()
        raise
    finally:
        if connection.in_transaction():
            connection.commit()
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        enabled = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        connection.commit()
        if enabled != 1:
            raise RuntimeError("SQLite foreign-key enforcement was not restored")
