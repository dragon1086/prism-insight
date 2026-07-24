import hashlib
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import prism_core.storage.legacy_manifest as legacy_manifest
from prism_core.storage.legacy_manifest import (
    LegacyMigrationError,
    inspect_legacy,
    migrate_legacy,
    open_legacy_database,
)


def _file_state(path: Path) -> tuple[str, int]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime_ns


def _optional_file_state(path: Path) -> tuple[str, int] | None:
    return _file_state(path) if path.exists() else None


def _create_supported_source(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE trading_intuitions (
                id INTEGER PRIMARY KEY,
                category TEXT NOT NULL,
                subcategory TEXT,
                condition TEXT NOT NULL,
                insight TEXT NOT NULL,
                confidence REAL,
                supporting_trades INTEGER,
                success_rate REAL,
                source_journal_ids TEXT,
                created_at TEXT NOT NULL,
                last_validated_at TEXT,
                is_active INTEGER DEFAULT 1,
                scope TEXT DEFAULT 'universal'
            );
            CREATE TABLE trading_principles (
                id INTEGER PRIMARY KEY,
                scope TEXT NOT NULL DEFAULT 'universal',
                scope_context TEXT,
                condition TEXT NOT NULL,
                action TEXT NOT NULL,
                reason TEXT,
                priority TEXT DEFAULT 'medium',
                confidence REAL DEFAULT 0.5,
                supporting_trades INTEGER DEFAULT 1,
                source_journal_ids TEXT,
                created_at TEXT NOT NULL,
                last_validated_at TEXT,
                is_active INTEGER DEFAULT 1
            );
            """
        )
        connection.execute(
            "INSERT INTO trading_intuitions "
            "(id, category, condition, insight, confidence, created_at) "
            "VALUES (1, 'risk', 'volatility rises', 'reduce uncertainty', 0.6, "
            "'2026-01-02 03:04:05')"
        )
        connection.execute(
            "INSERT INTO trading_principles "
            "(id, scope, condition, action, created_at) "
            "VALUES (7, 'universal', 'evidence conflicts', 'do not enter', "
            "'2026-02-03 04:05:06')"
        )


def test_fixture_source_is_opened_read_only_and_rejects_writes(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE trading_intuitions (id INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO trading_intuitions VALUES (1)")

    before = _file_state(source)
    with open_legacy_database(source) as connection:
        assert connection.execute("PRAGMA query_only").fetchone() == (1,)
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("INSERT INTO trading_intuitions VALUES (2)")
    assert _file_state(source) == before


def test_inspection_reproduces_counts_and_checksums_without_row_content(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)

    first = inspect_legacy(source)
    second = inspect_legacy(source)

    assert first == second
    by_table = {table.source_table: table for table in first.tables}
    assert by_table["trading_intuitions"].source_count == 1
    assert by_table["trading_intuitions"].transformed_count == 1
    assert by_table["trading_intuitions"].rejected_count == 0
    assert by_table["trading_principles"].source_count == 1
    assert by_table["trading_principles"].transformed_count == 1
    assert by_table["trading_principles"].rejected_count == 0
    assert by_table["trading_intuitions"].source_checksum.startswith("sha256:")
    assert by_table["trading_intuitions"].transformed_checksum.startswith("sha256:")
    report_text = first.to_json()
    assert "volatility rises" not in report_text
    assert "do not enter" not in report_text
    assert first.migration_ready is True


def test_migration_routes_only_inert_legacy_lessons_to_research(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    destination = tmp_path / "migrated"
    _create_supported_source(source)
    source_before = _file_state(source)

    result = migrate_legacy(source, destination)

    assert result.inspection.migration_ready is True
    assert {path.name for path in destination.iterdir()} == {
        "ops.sqlite",
        "paper.sqlite",
        "research.sqlite",
    }
    with sqlite3.connect(destination / "research.sqlite") as connection:
        lessons = connection.execute(
            "SELECT strategy_id, status, payload_json FROM lessons ORDER BY lesson_id"
        ).fetchall()
    assert len(lessons) == 2
    assert {(strategy_id, status) for strategy_id, status, _ in lessons} == {
        ("LEGACY_UNVALIDATED", "LEGACY_UNVALIDATED")
    }
    for _, _, payload_json in lessons:
        payload = json.loads(payload_json)
        assert payload["activation_allowed"] is False
        assert payload["score_adjustment"] == 0
    with sqlite3.connect(destination / "paper.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM positions").fetchone() == (0,)
    with sqlite3.connect(destination / "ops.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM job_runs").fetchone() == (0,)
    assert _file_state(source) == source_before


def test_rerun_is_deterministic_and_destination_collision_cannot_duplicate_rows(
    tmp_path: Path,
):
    source = tmp_path / "legacy.sqlite"
    first_destination = tmp_path / "first"
    second_destination = tmp_path / "second"
    _create_supported_source(source)

    first = migrate_legacy(source, first_destination)
    second = migrate_legacy(source, second_destination)

    assert first.destination_checksums == second.destination_checksums
    assert not (tmp_path / ".first.lock").exists()
    assert not (tmp_path / ".second.lock").exists()
    with pytest.raises(LegacyMigrationError, match="destination collision"):
        migrate_legacy(source, first_destination)
    with sqlite3.connect(first_destination / "research.sqlite") as connection:
        assert connection.execute("SELECT COUNT(*) FROM lessons").fetchone() == (2,)

    dangling_destination = tmp_path / "dangling"
    dangling_destination.symlink_to(tmp_path / "missing-target", target_is_directory=True)
    with pytest.raises(LegacyMigrationError, match="destination collision"):
        migrate_legacy(source, dangling_destination)

    reserved_destination = tmp_path / "reserved"
    reservation = tmp_path / ".reserved.lock"
    reservation.write_text("other migration", encoding="utf-8")
    with pytest.raises(LegacyMigrationError, match="destination collision"):
        migrate_legacy(source, reserved_destination)
    assert reservation.read_text(encoding="utf-8") == "other migration"


def test_unsupported_schema_object_fails_closed(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "CREATE VIEW private_legacy_view AS SELECT insight FROM trading_intuitions"
        )

    report = inspect_legacy(source)

    assert report.migration_ready is False
    assert "UNSUPPORTED_SCHEMA_OBJECT" in report.issues
    with pytest.raises(LegacyMigrationError, match="not migration-ready"):
        migrate_legacy(source, tmp_path / "migrated")


def test_missing_required_columns_and_unknown_nonempty_tables_fail_closed(
    tmp_path: Path,
):
    missing = tmp_path / "missing.sqlite"
    with sqlite3.connect(missing) as connection:
        connection.execute(
            "CREATE TABLE trading_intuitions "
            "(id INTEGER PRIMARY KEY, category TEXT, condition TEXT, created_at TEXT)"
        )
    missing_report = inspect_legacy(missing)
    assert missing_report.migration_ready is False
    assert "trading_intuitions:MISSING_REQUIRED_COLUMNS" in missing_report.issues

    unknown = tmp_path / "unknown.sqlite"
    _create_supported_source(unknown)
    with sqlite3.connect(unknown) as connection:
        connection.execute("CREATE TABLE surprise_private_data (secret TEXT)")
        connection.execute("INSERT INTO surprise_private_data VALUES ('never print this')")
    unknown_report = inspect_legacy(unknown)
    assert unknown_report.migration_ready is False
    assert "surprise_private_data:UNSUPPORTED_NONEMPTY_TABLE" in unknown_report.issues
    assert "never print this" not in unknown_report.to_json()

    unexpected = tmp_path / "unexpected.sqlite"
    _create_supported_source(unexpected)
    with sqlite3.connect(unexpected) as connection:
        connection.execute("ALTER TABLE trading_principles ADD COLUMN account_key TEXT")
    unexpected_report = inspect_legacy(unexpected)
    assert unexpected_report.migration_ready is False
    assert "trading_principles:UNEXPECTED_COLUMNS" in unexpected_report.issues


def test_transform_errors_are_counted_without_leaking_rows_and_rollback_is_scoped(
    tmp_path: Path,
):
    source = tmp_path / "legacy.sqlite"
    destination = tmp_path / "migrated"
    _create_supported_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "INSERT INTO trading_intuitions "
            "(id, category, condition, insight, created_at) "
            "VALUES (2, 'secret-category', '', 'private-invalid-insight', '2026-01-01')"
        )
    before = _file_state(source)

    report = inspect_legacy(source)
    table = {item.source_table: item for item in report.tables}["trading_intuitions"]
    assert (table.source_count, table.transformed_count, table.rejected_count) == (2, 1, 1)
    assert "private-invalid-insight" not in report.to_json()
    with pytest.raises(LegacyMigrationError, match="not migration-ready"):
        migrate_legacy(source, destination)

    assert not destination.exists()
    assert _file_state(source) == before
    assert not list(tmp_path.glob(".prism-legacy-stage-*"))


def test_inspection_cli_dry_run_is_deterministic_and_metadata_only(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)

    command = [sys.executable, "tools/inspect_legacy_db.py", str(source)]
    first = subprocess.run(command, check=True, capture_output=True, text=True)
    second = subprocess.run(command, check=True, capture_output=True, text=True)

    assert first.stdout == second.stdout
    parsed = json.loads(first.stdout)
    assert parsed["migration_ready"] is True
    assert "volatility rises" not in first.stdout
    assert str(source) not in first.stdout
    assert first.stderr == ""


def test_migration_cli_dry_run_never_creates_destinations(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    destination = tmp_path / "migrated"
    _create_supported_source(source)

    completed = subprocess.run(
        [
            sys.executable,
            "tools/migrate_legacy_readonly.py",
            str(source),
            str(destination),
            "--dry-run",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(completed.stdout)["migration_ready"] is True
    assert not destination.exists()
    assert "reduce uncertainty" not in completed.stdout
    assert completed.stderr == ""


def test_wal_source_snapshot_includes_committed_rows_without_mutating_source(
    tmp_path: Path,
):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)
    writer = sqlite3.connect(source)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO trading_intuitions "
            "(id, category, condition, insight, created_at) "
            "VALUES (2, 'wal', 'committed in wal', 'snapshot must include', '2026-03-01')"
        )
        writer.commit()
        wal = Path(f"{source}-wal")
        before = (_file_state(source), _optional_file_state(wal))

        report = inspect_legacy(source)

        table = {item.source_table: item for item in report.tables}["trading_intuitions"]
        assert table.source_count == 2
        assert (_file_state(source), _optional_file_state(wal)) == before
    finally:
        writer.close()


def test_unsupported_version_and_source_mutation_indicators_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    versioned = tmp_path / "versioned.sqlite"
    _create_supported_source(versioned)
    with sqlite3.connect(versioned) as connection:
        connection.execute("PRAGMA user_version=99")
    with pytest.raises(LegacyMigrationError, match="unsupported legacy schema version"):
        inspect_legacy(versioned)

    source = tmp_path / "mutating.sqlite"
    _create_supported_source(source)
    original_inspect = legacy_manifest._inspect_snapshot

    def mutate_then_inspect(*args, **kwargs):
        with source.open("ab") as handle:
            handle.write(b"mutation-indicator")
        return original_inspect(*args, **kwargs)

    monkeypatch.setattr(legacy_manifest, "_inspect_snapshot", mutate_then_inspect)
    with pytest.raises(LegacyMigrationError, match="source changed"):
        inspect_legacy(source)


def test_destination_verification_mismatch_rolls_back_only_new_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    source = tmp_path / "legacy.sqlite"
    destination = tmp_path / "migrated"
    unrelated = tmp_path / "keep.txt"
    _create_supported_source(source)
    unrelated.write_text("preserve", encoding="utf-8")
    source_before = _file_state(source)

    def reject_destination(*args, **kwargs):
        del args, kwargs
        raise LegacyMigrationError("destination lesson checksum mismatch")

    monkeypatch.setattr(legacy_manifest, "_verify_lessons", reject_destination)
    with pytest.raises(LegacyMigrationError, match="checksum mismatch"):
        migrate_legacy(source, destination)

    assert not destination.exists()
    assert unrelated.read_text(encoding="utf-8") == "preserve"
    assert _file_state(source) == source_before
    assert not list(tmp_path.glob(".prism-legacy-stage-*"))


def test_checksum_is_type_sensitive_and_real_verifier_detects_mismatch(tmp_path: Path):
    assert legacy_manifest._digest_rows([(1,)]) != legacy_manifest._digest_rows([("1",)])

    source = tmp_path / "legacy.sqlite"
    destination = tmp_path / "migrated"
    _create_supported_source(source)
    before = inspect_legacy(source)
    with sqlite3.connect(source) as connection:
        connection.execute(
            "UPDATE trading_intuitions SET insight='changed evidence' WHERE id=1"
        )
    after = inspect_legacy(source)
    before_table = {item.source_table: item for item in before.tables}["trading_intuitions"]
    after_table = {item.source_table: item for item in after.tables}["trading_intuitions"]
    assert before_table.source_checksum != after_table.source_checksum
    assert before_table.transformed_checksum != after_table.transformed_checksum

    migrate_legacy(source, destination)
    with pytest.raises(LegacyMigrationError, match="count mismatch"):
        legacy_manifest._verify_lessons(
            destination / "research.sqlite",
            {"trading_intuitions": [], "trading_principles": []},
        )


def test_at_rest_wal_source_is_read_without_a_live_writer(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)
    script = """
import os
import sqlite3
import sys
connection = sqlite3.connect(sys.argv[1])
connection.execute('PRAGMA journal_mode=WAL')
connection.execute('PRAGMA wal_autocheckpoint=0')
connection.execute(
    \"INSERT INTO trading_intuitions \"
    \"(id, category, condition, insight, created_at) \"
    \"VALUES (2, 'wal', 'at rest', 'must be copied', '2026-03-02')\"
)
connection.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, "-c", script, str(source)], check=True)
    wal = Path(f"{source}-wal")
    assert wal.exists() and wal.stat().st_size > 0
    before = (_file_state(source), _optional_file_state(wal))

    report = inspect_legacy(source)

    table = {item.source_table: item for item in report.tables}["trading_intuitions"]
    assert table.source_count == 2
    assert (_file_state(source), _optional_file_state(wal)) == before


def test_nonfinite_real_is_reported_as_rejected_without_traceback_or_value(tmp_path: Path):
    source = tmp_path / "legacy.sqlite"
    _create_supported_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute("UPDATE trading_intuitions SET confidence=?", (float("inf"),))

    completed = subprocess.run(
        [sys.executable, "tools/inspect_legacy_db.py", str(source)],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 2
    output = json.loads(completed.stdout)
    table = next(
        item for item in output["tables"] if item["source_table"] == "trading_intuitions"
    )
    assert table["rejected_count"] == 1
    assert table["issues"] == ["INVALID_ROW"]
    assert "Infinity" not in completed.stdout
    assert completed.stderr == ""
