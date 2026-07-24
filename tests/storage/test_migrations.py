import sqlite3
from pathlib import Path

import pytest

from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, MigrationError, migrate_database


RESEARCH_TABLES = {
    "schema_migrations",
    "securities",
    "symbol_mappings",
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
PAPER_TABLES = {
    "schema_migrations",
    "strategy_books",
    "cash_ledger",
    "paper_orders",
    "fills",
    "positions",
    "nav_snapshots",
}
OPS_TABLES = {
    "schema_migrations",
    "job_runs",
    "leases",
    "heartbeats",
    "alerts",
    "backup_records",
    "recovery_events",
}
APPEND_ONLY_TABLES = {
    DatabaseKind.RESEARCH: {
        "symbol_mappings",
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
    },
    DatabaseKind.PAPER: {
        "cash_ledger",
        "paper_orders",
        "fills",
        "positions",
        "nav_snapshots",
    },
    DatabaseKind.OPS: {
        "heartbeats",
        "alerts",
        "backup_records",
        "recovery_events",
    },
}


def _user_tables(connection) -> set[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_schema "
        "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row[0] for row in rows}


def test_empty_research_database_migrates_to_current_version(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        applied = migrate_database(connection, DatabaseKind.RESEARCH)

        assert applied == (1,)
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1, "initial")]
        assert _user_tables(connection) == RESEARCH_TABLES


def test_migration_rerun_is_idempotent(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        assert migrate_database(connection, DatabaseKind.RESEARCH) == (1,)
        first_history = connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations"
        ).fetchall()

        assert migrate_database(connection, DatabaseKind.RESEARCH) == ()
        assert connection.execute(
            "SELECT version, name, checksum, applied_at FROM schema_migrations"
        ).fetchall() == first_history


@pytest.mark.parametrize(
    ("kind", "expected_tables"),
    [
        (DatabaseKind.PAPER, PAPER_TABLES),
        (DatabaseKind.OPS, OPS_TABLES),
    ],
)
def test_each_database_migrates_only_its_own_table_boundary(
    tmp_path: Path,
    kind: DatabaseKind,
    expected_tables: set[str],
):
    with open_database(tmp_path / f"{kind.value}.sqlite") as connection:
        assert migrate_database(connection, kind) == (1,)
        assert _user_tables(connection) == expected_tables


@pytest.mark.parametrize(
    ("kind", "foreign_table"),
    [
        (DatabaseKind.RESEARCH, "paper_orders"),
        (DatabaseKind.PAPER, "job_runs"),
        (DatabaseKind.OPS, "proposals"),
    ],
)
def test_database_rejects_table_owned_by_another_boundary(
    tmp_path: Path,
    kind: DatabaseKind,
    foreign_table: str,
):
    with open_database(tmp_path / f"{kind.value}.sqlite") as connection:
        connection.execute(f"CREATE TABLE {foreign_table} (id TEXT PRIMARY KEY)")

        with pytest.raises(MigrationError, match="database boundary"):
            migrate_database(connection, kind)


def test_out_of_order_migration_files_are_rejected(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    (migration_dir / "001_initial.sql").write_text(
        "CREATE TABLE first_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )
    (migration_dir / "003_skipped.sql").write_text(
        "CREATE TABLE skipped_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        with pytest.raises(MigrationError, match="contiguous"):
            migrate_database(
                connection,
                DatabaseKind.RESEARCH,
                migrations_root=tmp_path / "migrations",
            )


def test_invalid_migration_is_rejected_without_partial_schema(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    (migration_dir / "001_broken.sql").write_text(
        "CREATE TABLE partial_table (id TEXT PRIMARY KEY);\n"
        "THIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        with pytest.raises(MigrationError, match="001_broken failed"):
            migrate_database(
                connection,
                DatabaseKind.RESEARCH,
                migrations_root=tmp_path / "migrations",
            )

        assert "partial_table" not in _user_tables(connection)
        assert connection.execute("SELECT * FROM schema_migrations").fetchall() == []


@pytest.mark.parametrize(
    ("kind", "expected_tables"),
    [
        (DatabaseKind.RESEARCH, RESEARCH_TABLES),
        (DatabaseKind.PAPER, PAPER_TABLES),
        (DatabaseKind.OPS, OPS_TABLES),
    ],
)
def test_foreign_keys_never_cross_database_boundary(
    tmp_path: Path,
    kind: DatabaseKind,
    expected_tables: set[str],
):
    with open_database(tmp_path / f"{kind.value}.sqlite") as connection:
        migrate_database(connection, kind)

        for table in expected_tables - {"schema_migrations"}:
            targets = {
                row[2] for row in connection.execute(f"PRAGMA foreign_key_list({table})")
            }
            assert targets <= expected_tables


def test_audit_records_reject_update_and_delete(tmp_path: Path):
    with open_database(tmp_path / "ops.sqlite") as connection:
        migrate_database(connection, DatabaseKind.OPS)
        connection.execute(
            "INSERT INTO alerts "
            "(alert_id, severity, alert_kind, payload_json, created_at) "
            "VALUES ('alert-1', 'ERROR', 'TEST', '{}', '2026-07-24T00:00:00+00:00')"
        )

        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE alerts SET severity = 'INFO' WHERE alert_id = 'alert-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute("DELETE FROM alerts WHERE alert_id = 'alert-1'")


@pytest.mark.parametrize("kind", list(DatabaseKind))
def test_evidence_and_audit_tables_have_append_only_guards(
    tmp_path: Path, kind: DatabaseKind
):
    with open_database(tmp_path / f"{kind.value}.sqlite") as connection:
        migrate_database(connection, kind)
        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
            )
        }

        for table in APPEND_ONLY_TABLES[kind]:
            assert f"{table}_append_only_update" in triggers
            assert f"{table}_append_only_delete" in triggers


def test_database_rejects_unregistered_table_in_managed_schema(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        connection.execute("CREATE TABLE ad_hoc_table (id TEXT PRIMARY KEY)")

        with pytest.raises(MigrationError, match="database boundary"):
            migrate_database(connection, DatabaseKind.RESEARCH)


def test_later_migration_applies_without_rewriting_prior_history(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    (migration_dir / "001_initial.sql").write_text(
        "CREATE TABLE first_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        assert migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        ) == (1,)
        first_history = connection.execute(
            "SELECT name, checksum, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone()
        (migration_dir / "002_second.sql").write_text(
            "CREATE TABLE second_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
        )

        assert migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        ) == (2,)
        assert connection.execute(
            "SELECT name, checksum, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone() == first_history
        assert "second_table" in _user_tables(connection)


def test_applied_migration_checksum_drift_is_rejected(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    migration_file = migration_dir / "001_initial.sql"
    migration_file.write_text(
        "CREATE TABLE first_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        )
        migration_file.write_text(
            "CREATE TABLE changed_table (id TEXT PRIMARY KEY);\n", encoding="utf-8"
        )

        with pytest.raises(MigrationError, match="does not match"):
            migrate_database(
                connection,
                DatabaseKind.RESEARCH,
                migrations_root=tmp_path / "migrations",
            )
