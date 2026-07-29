import sqlite3
from pathlib import Path

import pytest

from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, MigrationError, migrate_database


RESEARCH_TABLES = {
    "schema_migrations",
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
    "feedback_runs",
    "decision_snapshots",
    "trade_plan_proposals",
    "proposal_disposition_events",
    "proposal_outcomes",
    "retrospective_events",
    "lesson_candidates",
    "lesson_evidence_events",
    "leadership_history_events",
    "process_quality_outcomes",
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
        "feedback_runs",
        "decision_snapshots",
        "trade_plan_proposals",
        "proposal_disposition_events",
        "proposal_outcomes",
        "retrospective_events",
        "lesson_candidates",
        "lesson_evidence_events",
        "leadership_history_events",
        "process_quality_outcomes",
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

        assert applied == (1, 2, 3, 4)
        assert connection.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall() == [
            (1, "initial"),
            (2, "security_master_actions"),
            (3, "feedback_storage"),
            (4, "kr_leadership_feedback_cycle"),
        ]
        assert _user_tables(connection) == RESEARCH_TABLES


def test_migration_rerun_is_idempotent(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        assert migrate_database(connection, DatabaseKind.RESEARCH) == (1, 2, 3, 4)
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
        expected_versions = (1, 2) if kind is DatabaseKind.PAPER else (1,)
        assert migrate_database(connection, kind) == expected_versions
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


def test_default_research_v1_upgrades_to_v2_without_rewriting_history(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    source = (
        Path(__file__).parents[2]
        / "prism_core"
        / "storage"
        / "migrations"
        / "research"
        / "001_initial.sql"
    )
    (migration_dir / "001_initial.sql").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        assert migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        ) == (1,)
        v1_history = connection.execute(
            "SELECT name, checksum, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone()

        assert migrate_database(connection, DatabaseKind.RESEARCH) == (2, 3, 4)
        assert connection.execute(
            "SELECT name, checksum, applied_at FROM schema_migrations WHERE version = 1"
        ).fetchone() == v1_history


def test_default_paper_v1_upgrade_preserves_positions_and_rearms_guards(
    tmp_path: Path,
):
    migration_dir = tmp_path / "migrations" / "paper"
    migration_dir.mkdir(parents=True)
    source_dir = (
        Path(__file__).parents[2]
        / "prism_core"
        / "storage"
        / "migrations"
        / "paper"
    )
    (migration_dir / "001_initial.sql").write_text(
        (source_dir / "001_initial.sql").read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    with open_database(tmp_path / "paper.sqlite") as connection:
        assert migrate_database(
            connection,
            DatabaseKind.PAPER,
            migrations_root=tmp_path / "migrations",
        ) == (1,)
        connection.execute(
            "INSERT INTO strategy_books "
            "(book_id, strategy_id, market, currency, created_at) VALUES "
            "('book-1', 'SWING_V1', 'US', 'USD', '2026-07-26T00:00:00+00:00')"
        )
        rows = [
            (
                "position-1",
                "book-1",
                "security-1",
                "1",
                "100",
                "2026-07-26T01:00:00+00:00",
                "2026-07-26T01:00:00+00:00",
            ),
            (
                "position-2",
                "book-1",
                "security-1",
                "2",
                "101",
                "2026-07-26T02:00:00+00:00",
                "2026-07-26T02:00:00+00:00",
            ),
        ]
        connection.executemany(
            "INSERT INTO positions "
            "(position_snapshot_id, book_id, security_id, quantity, average_cost, "
            "as_of_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        (migration_dir / "002_simulated_broker_positions.sql").write_text(
            (source_dir / "002_simulated_broker_positions.sql").read_text(
                encoding="utf-8"
            ),
            encoding="utf-8",
        )

        assert migrate_database(
            connection,
            DatabaseKind.PAPER,
            migrations_root=tmp_path / "migrations",
        ) == (2,)
        assert connection.execute(
            "SELECT position_snapshot_id, book_id, security_id, quantity, "
            "average_cost, as_of_at, created_at FROM positions ORDER BY rowid"
        ).fetchall() == rows
        connection.execute(
            "INSERT INTO positions "
            "(position_snapshot_id, book_id, security_id, quantity, average_cost, "
            "as_of_at, created_at) VALUES "
            "('position-3', 'book-1', 'security-1', '3', '102', "
            "'2026-07-26T02:00:00+00:00', '2026-07-26T02:00:00+00:00')"
        )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "UPDATE positions SET quantity = '999' "
                "WHERE position_snapshot_id = 'position-1'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            connection.execute(
                "DELETE FROM positions WHERE position_snapshot_id = 'position-1'"
            )


def test_v2_freezes_old_symbol_mappings_and_guards_new_evidence(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)

        with pytest.raises(sqlite3.IntegrityError, match="frozen after migration 002"):
            connection.execute(
                "INSERT INTO symbol_mappings "
                "(mapping_id, security_id, provider, provider_symbol, valid_from, "
                "revision, created_at) VALUES "
                "('m', 's', 'FMP', 'XYZ', '2026-01-01T00:00:00+00:00', 0, "
                "'2026-01-01T00:00:00+00:00')"
            )

        triggers = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_schema WHERE type = 'trigger'"
            )
        }
        for table in {
            "security_alias_events",
            "security_listing_events",
            "corporate_action_events",
        }:
            assert f"{table}_append_only_update" in triggers
            assert f"{table}_append_only_delete" in triggers


def test_v2_refuses_to_orphan_preexisting_v1_symbol_mapping_rows(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    source = (
        Path(__file__).parents[2]
        / "prism_core"
        / "storage"
        / "migrations"
        / "research"
        / "001_initial.sql"
    )
    (migration_dir / "001_initial.sql").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        )
        connection.execute(
            "INSERT INTO securities VALUES "
            "('security-1', 'US', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO symbol_mappings "
            "(mapping_id, security_id, provider, provider_symbol, valid_from, "
            "revision, created_at) VALUES "
            "('mapping-1', 'security-1', 'FMP', 'XYZ', "
            "'2026-01-01T00:00:00+00:00', 0, '2026-01-01T00:00:00+00:00')"
        )

        with pytest.raises(MigrationError, match="002_security_master_actions failed"):
            migrate_database(connection, DatabaseKind.RESEARCH)

        assert connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall() == [(1,)]
        assert "security_alias_events" not in _user_tables(connection)


def test_v3_preserves_v1_feedback_rows_and_freezes_legacy_writers(tmp_path: Path):
    migration_dir = tmp_path / "migrations" / "research"
    migration_dir.mkdir(parents=True)
    source = (
        Path(__file__).parents[2]
        / "prism_core"
        / "storage"
        / "migrations"
        / "research"
        / "001_initial.sql"
    )
    (migration_dir / "001_initial.sql").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(
            connection,
            DatabaseKind.RESEARCH,
            migrations_root=tmp_path / "migrations",
        )
        connection.execute(
            "INSERT INTO securities VALUES ('s', 'US', '2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO market_snapshots VALUES "
            "('snap', 'US', '2026-01-01', 'hash', 'FRESH', "
            "'2026-01-01T00:00:00+00:00')"
        )
        connection.execute(
            "INSERT INTO proposals VALUES "
            "('legacy-p', 'snap', 's', 'SWING_V1', 'v1', 'raw', '{}', "
            "'2026-01-01T00:00:00+00:00')"
        )

        assert migrate_database(connection, DatabaseKind.RESEARCH) == (2, 3, 4)
        assert connection.execute(
            "SELECT raw_output FROM proposals WHERE proposal_id = 'legacy-p'"
        ).fetchone() == ("raw",)
        for table in (
            "proposals",
            "proposal_dispositions",
            "outcomes",
            "retrospectives",
            "lesson_evidence",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="frozen after migration 003"):
                connection.execute(f"INSERT INTO {table} DEFAULT VALUES")
        connection.execute(
            "INSERT INTO lessons "
            "(lesson_id, strategy_id, status, payload_json, created_at) "
            "VALUES ('legacy-new', 'SWING_V1', 'LEGACY_UNVALIDATED', '{}', 't1')"
        )
        assert connection.execute(
            "SELECT status FROM lessons WHERE lesson_id = 'legacy-new'"
        ).fetchone() == ("LEGACY_UNVALIDATED",)


def test_lesson_evidence_proposal_fk_carries_strategy_identity(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        rows = connection.execute(
            "PRAGMA foreign_key_list(lesson_evidence_events)"
        ).fetchall()
        proposal_fk = {
            (row[3], row[4])
            for row in rows
            if row[2] == "trade_plan_proposals"
        }
        assert proposal_fk == {
            ("proposal_record_id", "proposal_record_id"),
            ("strategy_id", "strategy_id"),
            ("strategy_version", "strategy_version"),
        }


def test_lesson_evidence_schema_enforces_complete_timing_order(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        table_sql = connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'lesson_evidence_events'"
        ).fetchone()[0]

        assert "observed_at <= available_at" in table_sql
        assert "available_at <= ingested_at" in table_sql
        assert "available_at <= as_of_at" in table_sql


def test_feedback_schema_scopes_natural_revisions_and_horizons_by_strategy(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        proposal_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(trade_plan_proposals)")
            if row[2]
        }
        lesson_indexes = {
            row[1]
            for row in connection.execute("PRAGMA index_list(lesson_candidates)")
            if row[2]
        }
        outcome_sql = connection.execute(
            "SELECT sql FROM sqlite_schema "
            "WHERE type = 'table' AND name = 'proposal_outcomes'"
        ).fetchone()[0]

        assert "trade_plan_proposals_strategy_revision" in proposal_indexes
        assert "lesson_candidates_strategy_revision" in lesson_indexes
        assert "strategy_id = 'SWING_V1' AND horizon_sessions IN (5, 10, 20)" in outcome_sql
        assert "strategy_id = 'TREND_V1' AND horizon_sessions IN (20, 60, 120)" in outcome_sql


def test_v3_feedback_schema_has_no_broker_or_order_columns(tmp_path: Path):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        for table in {
            "feedback_runs",
            "decision_snapshots",
            "trade_plan_proposals",
            "proposal_disposition_events",
            "proposal_outcomes",
            "retrospective_events",
            "lesson_candidates",
            "lesson_evidence_events",
        }:
            columns = {row[1].lower() for row in connection.execute(f"PRAGMA table_info({table})")}
            assert not any(
                token in column
                for column in columns
                for token in ("broker", "order", "intent", "quantity", "qty", "fill")
            )
