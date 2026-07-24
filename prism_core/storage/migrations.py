"""Ordered, checksummed, atomic SQLite migrations for PRISM databases."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from prism_core.storage.database import transaction


class DatabaseKind(str, Enum):
    RESEARCH = "research"
    PAPER = "paper"
    OPS = "ops"


class MigrationError(RuntimeError):
    """Raised when migration files or applied history violate invariants."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    checksum: str
    sql: str


_FILENAME = re.compile(r"^(?P<version>[0-9]{3})_(?P<name>[a-z][a-z0-9_]*)\.sql$")
_DEFAULT_ROOT = Path(__file__).with_name("migrations")
_BOUNDARY_TABLES = {
    DatabaseKind.RESEARCH: frozenset(
        {
            "securities",
            "symbol_mappings",
            "security_alias_events",
            "security_listing_events",
            "corporate_action_events",
            "market_snapshots",
            "observations",
            "features",
            "proposals",
            "proposal_dispositions",
            "outcomes",
            "retrospectives",
            "lessons",
            "lesson_evidence",
            "reports",
        }
    ),
    DatabaseKind.PAPER: frozenset(
        {
            "strategy_books",
            "cash_ledger",
            "paper_orders",
            "fills",
            "positions",
            "nav_snapshots",
        }
    ),
    DatabaseKind.OPS: frozenset(
        {
            "job_runs",
            "leases",
            "heartbeats",
            "alerts",
            "backup_records",
            "recovery_events",
        }
    ),
}


def _checksum(sql: str) -> str:
    normalized = sql.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _load_migrations(kind: DatabaseKind, root: Path) -> tuple[Migration, ...]:
    directory = root / kind.value
    if not directory.is_dir():
        raise MigrationError(f"migration directory does not exist: {directory}")

    migrations: list[Migration] = []
    for path in sorted(directory.iterdir()):
        if path.name.startswith("."):
            continue
        match = _FILENAME.fullmatch(path.name)
        if match is None or not path.is_file():
            raise MigrationError(f"invalid migration filename: {path.name}")
        sql = path.read_text(encoding="utf-8")
        migrations.append(
            Migration(
                version=int(match.group("version")),
                name=match.group("name"),
                checksum=_checksum(sql),
                sql=sql,
            )
        )

    expected = list(range(1, len(migrations) + 1))
    actual = [migration.version for migration in migrations]
    if actual != expected:
        raise MigrationError(
            f"migration versions must be contiguous from 001; found {actual}"
        )
    return tuple(migrations)


def _statements(sql: str) -> tuple[str, ...]:
    statements: list[str] = []
    pending = ""
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            if pending.strip():
                statements.append(pending)
            pending = ""
    if pending.strip():
        raise MigrationError("migration contains an incomplete SQL statement")
    return tuple(statements)


def _ensure_history(connection: sqlite3.Connection) -> None:
    with transaction(connection):
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY CHECK (version > 0),
                name TEXT NOT NULL,
                checksum TEXT NOT NULL,
                applied_at TEXT NOT NULL
            )
            """
        )


def _reject_cross_boundary_tables(
    connection: sqlite3.Connection, kind: DatabaseKind, *, strict: bool
) -> None:
    existing = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    foreign = set().union(
        *(tables for owner, tables in _BOUNDARY_TABLES.items() if owner is not kind)
    )
    violations = existing & foreign
    if strict:
        violations |= existing - _BOUNDARY_TABLES[kind] - {"schema_migrations"}
    violations = sorted(violations)
    if violations:
        raise MigrationError(
            f"{kind.value} database boundary contains foreign tables: {violations}"
        )


def _validate_complete_boundary(
    connection: sqlite3.Connection, kind: DatabaseKind
) -> None:
    actual = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_schema "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    expected = _BOUNDARY_TABLES[kind] | {"schema_migrations"}
    if actual != expected:
        raise MigrationError(
            f"{kind.value} database boundary mismatch; "
            f"missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )


def migrate_database(
    connection: sqlite3.Connection,
    kind: DatabaseKind | str,
    *,
    migrations_root: str | Path | None = None,
) -> tuple[int, ...]:
    """Validate migration history and atomically apply all pending migrations."""
    try:
        normalized_kind = DatabaseKind(kind)
    except ValueError as exc:
        raise MigrationError(f"unknown database kind: {kind!r}") from exc

    using_default_root = migrations_root is None
    root = Path(migrations_root) if migrations_root is not None else _DEFAULT_ROOT
    migrations = _load_migrations(normalized_kind, root)
    _reject_cross_boundary_tables(
        connection, normalized_kind, strict=using_default_root
    )
    _ensure_history(connection)

    applied_rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations ORDER BY version"
    ).fetchall()
    if [row[0] for row in applied_rows] != list(range(1, len(applied_rows) + 1)):
        raise MigrationError("applied migration history is out of order")
    if len(applied_rows) > len(migrations):
        raise MigrationError("database version is newer than available migrations")
    for row, migration in zip(applied_rows, migrations):
        if tuple(row) != (migration.version, migration.name, migration.checksum):
            raise MigrationError(
                f"applied migration {row[0]} does not match its migration file"
            )

    applied_now: list[int] = []
    for migration in migrations[len(applied_rows) :]:
        try:
            with transaction(connection):
                for statement in _statements(migration.sql):
                    connection.execute(statement)
                connection.execute(
                    "INSERT INTO schema_migrations "
                    "(version, name, checksum, applied_at) VALUES (?, ?, ?, ?)",
                    (
                        migration.version,
                        migration.name,
                        migration.checksum,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                violations = connection.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise MigrationError(
                        f"migration {migration.version:03d} violates foreign keys"
                    )
        except sqlite3.Error as exc:
            raise MigrationError(
                f"migration {migration.version:03d}_{migration.name} failed"
            ) from exc
        applied_now.append(migration.version)
    if using_default_root:
        _validate_complete_boundary(connection, normalized_kind)
    return tuple(applied_now)
