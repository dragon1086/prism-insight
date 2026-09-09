from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import sys
from zoneinfo import ZoneInfo

import pytest

from tools import build_historical_closed_record_evidence as historical


@pytest.fixture
def source(tmp_path):
    directory = tmp_path.resolve() / "source"
    directory.mkdir()
    path = directory / "history.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("""CREATE TABLE trading_history (
            id INTEGER PRIMARY KEY, account_key TEXT, ticker TEXT, company_name TEXT,
            buy_price REAL, buy_date TEXT, sell_price REAL, sell_date TEXT, profit_rate REAL,
            holding_days INTEGER, scenario TEXT, trigger_type TEXT, trigger_mode TEXT,
            sector TEXT, exit_kind TEXT)""")
        for identifier, account in ((1, "ACCOUNT_PRIVATE_CANARY_A"), (2, "ACCOUNT_PRIVATE_CANARY_B")):
            db.execute("INSERT INTO trading_history VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                identifier, account, "005930", "COMPANY_PRIVATE_CANARY", 100, "2026-09-01 09:00:00",
                90, "2026-09-09 10:00:00", -10, 8, "PROMPT_SECRET_CANARY",
                "갭 상승 모멘텀 상위주", "morning", "SECTOR_PRIVATE_CANARY", "stop"))
    return path


def build(path, **kwargs):
    return historical.build(path, ["KR"], "prism-legacy-v1", b"k" * 32, ZoneInfo("Asia/Seoul"), **kwargs)


def test_repeat_logical_export_and_account_separation_without_secrets(source, monkeypatch):
    from tools import backfill_observability
    monkeypatch.setattr(backfill_observability, "emit_event", lambda *a, **k: pytest.fail("emitter forbidden"))
    before = source.read_bytes()
    first = build(source, now=datetime(2026, 9, 10, tzinfo=timezone.utc))
    second = build(source, now=datetime(2026, 9, 11, tzinfo=timezone.utc))
    assert first["logical_source_sha256"] == second["logical_source_sha256"]
    assert first["artifact_sha256"] != second["artifact_sha256"]
    assert source.read_bytes() == before
    assert len(first["legacy_books"]) == 2
    assert len({r["legacy_book_ref"] for r in first["records"]}) == 2
    assert "CANARY" not in json.dumps(first)
    for row in first["records"]:
        assert row["original_decision_ref"] is None
        assert row["original_position_ref"] is None
        assert row["observed_at"] is None
        assert row["regime"] is None
        assert row["original_stop"] is None
        assert row["recorded_return_pct"] == -10
        assert row["ingestion_mode"] == "backfill"
        assert row["recorded_entry_at"] == "2026-09-01T00:00:00Z"
    assert first["prospective"] is False
    assert first["candidate_universe_exported"] is False
    assert first["broker_fill_filter_applied"] is False
    assert first["canonical_strategy_book_verified"] is False


@pytest.mark.parametrize("sql", [
    "UPDATE trading_history SET buy_price=1", "DELETE FROM trading_history", "CREATE TABLE bad(x)",
    "PRAGMA query_only=OFF", "ATTACH DATABASE ':memory:' AS other", "SELECT load_extension('bad')",
    "SELECT scenario FROM trading_history", "SELECT company_name FROM trading_history",
])
def test_sql_writes_and_escape_capabilities_denied(source, sql):
    connection = historical.read_only_connection(source)
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.DatabaseError):
            connection.execute(sql)
    finally:
        connection.close()


def test_source_projection_never_reads_scenario_names_or_candidates():
    query = historical.query_for("KR", True)
    assert "scenario" not in query
    assert "company_name" not in query
    assert "account_name" not in query
    assert "sector" not in query
    assert "analysis_performance_tracker" not in query


def test_cutoff_and_missing_close_are_excluded_not_backfilled(source):
    with sqlite3.connect(source) as db:
        db.execute("UPDATE trading_history SET sell_date='2026-09-10 10:00:00' WHERE id=1")
        db.execute("UPDATE trading_history SET sell_date='not-a-date' WHERE id=2")
    artifact = build(source)
    assert not artifact["records"]
    assert artifact["excluded_counts"] == {"AFTER_FROZEN_CUTOFF": 1, "CLOSE_TIME_CUTOFF_UNPROVABLE": 1}


def test_missing_scope_is_ungroupable_not_one_invented_book(source):
    with sqlite3.connect(source) as db:
        db.execute("UPDATE trading_history SET account_key=NULL")
    artifact = build(source)
    assert artifact["legacy_books"] == []
    assert artifact["ungroupable_record_count"] == 2
    assert all(r["book_scope_status"] == "UNKNOWN_UNGROUPABLE" for r in artifact["records"])


@pytest.mark.parametrize("placeholder", ["", "   ", "\t\n", "UNKNOWN", "UnKnOwN", " missing ", "[REDACTED]", " [rEdAcTeD] "])
def test_placeholder_scope_is_never_hashed_into_shared_book(source, placeholder):
    with sqlite3.connect(source) as db:
        db.execute("UPDATE trading_history SET account_key=?", (placeholder,))
    artifact = build(source)
    assert artifact["legacy_books"] == []
    assert artifact["ungroupable_record_count"] == 2
    assert all(r["legacy_book_ref"] is None for r in artifact["records"])


def test_legitimate_opaque_scope_keys_are_not_trimmed_or_case_folded(source):
    with sqlite3.connect(source) as db:
        db.execute("UPDATE trading_history SET account_key=' RealScope ' WHERE id=1")
        db.execute("UPDATE trading_history SET account_key='RealScope' WHERE id=2")
    artifact = build(source)
    actual = {r["legacy_book_ref"] for r in artifact["records"]}
    expected = {historical.reference(b"k" * 32, "legacy-book", "prism-legacy-v1", "KR", key)
                for key in (" RealScope ", "RealScope")}
    assert actual == expected
    assert len(actual) == 2


def test_timezone_is_explicit_not_inferred_from_market(source):
    korean = build(source)
    utc = historical.build(source, ["KR"], "prism-legacy-v1", b"k" * 32, ZoneInfo("UTC"))
    assert korean["records"][0]["recorded_entry_at"] != utc["records"][0]["recorded_entry_at"]
    assert korean["logical_source_sha256"] != utc["logical_source_sha256"]


def test_output_cannot_be_source_spool_symlink_or_existing_artifact(source, tmp_path):
    export = tmp_path.resolve() / "export"
    export.mkdir()
    with pytest.raises(ValueError):
        historical.validate_output(source, source.parent, source.parent / "new.json")
    with pytest.raises(ValueError):
        historical.validate_output(source, export, export / "prism_events.jsonl")
    existing = export / "existing.json"
    existing.write_text("UNCHANGED")
    with pytest.raises(ValueError):
        historical.validate_output(source, export, existing)
    linked = export / "alias.json"
    linked.symlink_to(source)
    with pytest.raises(ValueError):
        historical.validate_output(source, export, linked)
    assert historical.validate_output(source, export, export / "new.json") == export / "new.json"
    assert existing.read_text() == "UNCHANGED"


def test_output_parent_traversal_cannot_escape_into_source_directory(source, tmp_path):
    export = tmp_path.resolve() / "export"
    export.mkdir()
    escaped = export / ".." / "source" / "new.json"
    with pytest.raises(ValueError, match="output_parent_traversal_forbidden"):
        historical.validate_output(source, export, escaped)
    with pytest.raises(ValueError, match="output_parent_traversal_forbidden"):
        historical.validate_output(source, export / ".." / "export", export / "new.json")
    assert not (source.parent / "new.json").exists()


def test_row_mutation_changes_projection_hash_but_not_original_ref(source):
    before = build(source)
    with sqlite3.connect(source) as db:
        db.execute("UPDATE trading_history SET sell_price=91 WHERE id=1")
    after = build(source)
    assert [r["source_record_ref"] for r in before["records"]] == [r["source_record_ref"] for r in after["records"]]
    assert before["logical_source_sha256"] != after["logical_source_sha256"]


def test_hash_covers_sanitized_projection(source):
    artifact = build(source)
    expected = artifact.pop("artifact_sha256")
    assert historical.digest(artifact) == expected
    assert len(hashlib.sha256(b"k" * 32).hexdigest()) == 64


def test_cli_is_output_only_private_and_refuses_repeat(source, tmp_path, monkeypatch, capsys):
    root = tmp_path.resolve() / "export"
    root.mkdir(mode=0o700)
    key = tmp_path / "history.key"
    key.write_bytes(b"k" * 32)
    key.chmod(0o600)
    output = root / "closed.json"
    args = ["export", "--db", str(source), "--market", "KR", "--source-namespace", "prism-test",
            "--naive-source-timezone", "Asia/Seoul", "--pseudonym-key-file", str(key),
            "--output-root", str(root), "--output", str(output)]
    monkeypatch.setattr(sys, "argv", args)
    assert historical.main() == 0
    message = capsys.readouterr().out
    assert json.loads(message)["record_count"] == 2
    assert "CANARY" not in message
    assert output.stat().st_mode & 0o777 == 0o600
    before = output.read_bytes()
    assert historical.main() == 2
    assert output.read_bytes() == before


def test_committed_wal_is_seen_without_changing_database_or_wal(source):
    writer = sqlite3.connect(source)
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("UPDATE trading_history SET sell_price=92")
        writer.commit()
        wal = source.with_name(source.name + "-wal")
        db_before, wal_before = source.read_bytes(), wal.read_bytes()
        artifact = build(source)
        assert all(row["recorded_exit_price"] == 92 for row in artifact["records"])
        assert source.read_bytes() == db_before
        assert wal.read_bytes() == wal_before
    finally:
        writer.close()
