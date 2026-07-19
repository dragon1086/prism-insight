import json
import sqlite3
import subprocess
import sys
import time
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from prism_core.positions import (
    InvalidPositionTransition,
    LegacyPositionWriteResult,
    PositionStore,
    account_fingerprint,
    bounded_link_write_fail_open,
    legacy_position_id,
    mirror_write_fail_open,
)
from tools.compare_position_ledger import main as compare_main


def _legacy_schema(conn: sqlite3.Connection) -> None:
    for table in ("stock_holdings", "us_stock_holdings"):
        conn.execute(
            f"""
            CREATE TABLE {table} (
                id INTEGER PRIMARY KEY,
                account_key TEXT,
                account_name TEXT,
                ticker TEXT,
                buy_price REAL,
                buy_date TEXT
            )
            """
        )


def _order_intents_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE order_intents (
            id TEXT PRIMARY KEY,
            market TEXT NOT NULL,
            account_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            source_position_id TEXT
        )
        """
    )


def _insert_intent(
    conn: sqlite3.Connection,
    intent_id: str,
    *,
    market: str = "KR",
    account_id: str = "acct",
    symbol: str = "005930",
    side: str = "BUY",
    source_position_id: str | None = "legacy:KR:1",
) -> None:
    conn.execute(
        "INSERT INTO order_intents VALUES (?, ?, ?, ?, ?, ?)",
        (intent_id, market, account_id, symbol, side, source_position_id),
    )


def _insert_legacy(
    conn: sqlite3.Connection,
    table: str,
    row_id: int,
    account_id: str | None,
    symbol: str,
    price: float = 100.0,
) -> None:
    conn.execute(
        f"INSERT INTO {table} VALUES (?, ?, ?, ?, ?, ?)",
        (row_id, account_id, "primary", symbol, price, "2026-07-18T09:00:00"),
    )


def test_schema_is_additive_and_caller_controls_transaction() -> None:
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (1, '005930')")
    conn.commit()
    before = conn.execute("PRAGMA table_info(stock_holdings)").fetchall()

    conn.execute("BEGIN")
    PositionStore(conn).ensure_schema()
    conn.rollback()

    assert conn.execute("PRAGMA table_info(stock_holdings)").fetchall() == before
    assert conn.execute("SELECT * FROM stock_holdings").fetchall() == [(1, "005930")]
    assert (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='positions'"
        ).fetchone()
        is None
    )

    PositionStore(conn).ensure_schema()
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"positions", "position_mirror_errors"} <= tables


def test_legacy_position_write_result_is_immutable_and_explicit() -> None:
    result = LegacyPositionWriteResult(success=True, legacy_holding_id=17)

    assert result.success is True
    assert result.legacy_holding_id == 17
    with pytest.raises(FrozenInstanceError):
        setattr(result, "success", False)


def test_transition_graph_and_database_check_are_enforced() -> None:
    conn = sqlite3.connect(":memory:")
    store = PositionStore(conn)
    store.ensure_schema()
    assert store.open_legacy_position(
        market="KR",
        legacy_holding_id=7,
        account_id="vps:kr-primary:01",
        account_name="primary",
        symbol="005930",
        entry_price=71000,
        opened_at="2026-07-18T09:00:00",
    )

    assert store.transition(
        market="KR",
        legacy_holding_id=7,
        account_id="vps:kr-primary:01",
        to_status="CLOSED",
        exit_price=73000,
        realized_pnl_pct=2.81,
        exit_kind="target",
    )
    row = conn.execute(
        "SELECT status, exit_price, exit_kind, closed_at FROM positions"
    ).fetchone()
    assert row[:3] == ("CLOSED", 73000.0, "target")
    assert row[3]

    with pytest.raises(InvalidPositionTransition):
        store.transition(
            market="KR",
            legacy_holding_id=7,
            account_id="vps:kr-primary:01",
            to_status="OPEN",
        )
    with pytest.raises(LookupError):
        store.transition(
            market="KR",
            legacy_holding_id=7,
            account_id="wrong-account",
            to_status="OPEN",
        )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """
            INSERT INTO positions (
                id, market, legacy_holding_id, account_id, symbol, status,
                execution_mode, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "bad",
                "KR",
                "99",
                account_fingerprint("account"),
                "005930",
                "BROKEN",
                "legacy",
                "2026-07-18",
                "2026-07-18",
            ),
        )


def test_kr_and_us_backfill_is_idempotent_and_skips_invalid_rows() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "kr-secret-account", "005930")
    _insert_legacy(conn, "stock_holdings", 2, None, "000660")
    _insert_legacy(conn, "us_stock_holdings", 11, "us-secret-account", "AAPL", 190)
    store = PositionStore(conn)
    store.ensure_schema()

    kr = store.backfill_legacy_positions("KR")
    us = store.backfill_legacy_positions("US")
    again = store.backfill_legacy_positions("KR")

    assert kr == {"market": "KR", "inserted": 1, "existing": 0, "skipped": 1}
    assert us == {"market": "US", "inserted": 1, "existing": 0, "skipped": 0}
    assert again == {"market": "KR", "inserted": 0, "existing": 1, "skipped": 1}
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
    rows = conn.execute(
        "SELECT id, market, account_id, symbol, status, execution_mode "
        "FROM positions ORDER BY market"
    ).fetchall()
    assert rows == [
        (
            legacy_position_id("KR", 1),
            "KR",
            "kr-secret-account",
            "005930",
            "OPEN",
            "legacy",
        ),
        (
            legacy_position_id("US", 11),
            "US",
            "us-secret-account",
            "AAPL",
            "OPEN",
            "legacy",
        ),
    ]
    assert store.compare_legacy_positions("US")["matches"]

    mismatch = store.compare_legacy_positions("KR")
    payload = json.dumps(mismatch, sort_keys=True)
    assert not mismatch["matches"]
    assert mismatch["invalid_legacy_rows"] == [{"legacy_holding_id": "2"}]
    assert "kr-secret-account" not in payload
    assert "us-secret-account" not in payload


def test_cursor_backfill_does_not_commit_callers_transaction() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    store = PositionStore(conn.cursor())
    store.ensure_schema()
    conn.commit()

    conn.execute("BEGIN")
    _insert_legacy(conn, "stock_holdings", 1, "acct", "005930")
    assert store.backfill_legacy_positions("KR")["inserted"] == 1
    conn.rollback()

    assert conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0


def test_pyramiding_rows_remain_independent() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "acct", "005930", 70000)
    _insert_legacy(conn, "stock_holdings", 2, "acct", "005930", 72000)
    store = PositionStore(conn)
    store.ensure_schema()
    assert store.backfill_legacy_positions("KR")["inserted"] == 2

    assert store.close_legacy_position(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        exit_price=74000,
        realized_pnl_pct=5.71,
        exit_kind="target",
    )
    rows = conn.execute(
        "SELECT legacy_holding_id, status FROM positions ORDER BY legacy_holding_id"
    ).fetchall()
    assert rows == [("1", "CLOSED"), ("2", "OPEN")]


def test_us_full_exit_can_close_multiple_rows_with_one_intent() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    for row_id, price in ((1, 180.0), (2, 175.0), (3, 170.0)):
        _insert_legacy(conn, "us_stock_holdings", row_id, "acct", "AAPL", price)
    store = PositionStore(conn)
    store.ensure_schema()
    assert store.backfill_legacy_positions("US")["inserted"] == 3

    for row_id in (1, 2, 3):
        assert store.close_legacy_position(
            market="US",
            legacy_holding_id=row_id,
            account_id="acct",
            exit_intent_id="intent-full-exit",
            exit_price=190.0,
            exit_kind="stop",
        )

    rows = conn.execute(
        "SELECT status, exit_intent_id FROM positions ORDER BY legacy_holding_id"
    ).fetchall()
    assert rows == [("CLOSED", "intent-full-exit")] * 3


def test_entry_link_requires_persisted_matching_buy_intent() -> None:
    conn = sqlite3.connect(":memory:")
    _order_intents_schema(conn)
    store = PositionStore(conn)
    store.ensure_schema()
    assert store.open_legacy_position(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        account_name="primary",
        symbol="005930",
    )

    with pytest.raises(LookupError, match="intent not found"):
        store.link_entry_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="missing",
        )

    mismatches = (
        ("wrong-market", "US", "acct", "005930", "BUY"),
        ("wrong-account", "KR", "other", "005930", "BUY"),
        ("wrong-symbol", "KR", "acct", "000660", "BUY"),
        ("wrong-side", "KR", "acct", "005930", "SELL"),
    )
    for intent_id, market, account_id, symbol, side in mismatches:
        _insert_intent(
            conn,
            intent_id,
            market=market,
            account_id=account_id,
            symbol=symbol,
            side=side,
        )
        with pytest.raises(ValueError, match="does not match position"):
            store.link_entry_intent(
                market="KR",
                legacy_holding_id=1,
                account_id="acct",
                intent_id=intent_id,
            )

    _insert_intent(conn, "missing-source", source_position_id=None)
    _insert_intent(conn, "wrong-source", source_position_id="legacy:KR:999")
    for intent_id in ("missing-source", "wrong-source"):
        with pytest.raises(ValueError, match="source_position_id"):
            store.link_entry_intent(
                market="KR",
                legacy_holding_id=1,
                account_id="acct",
                intent_id=intent_id,
            )

    _insert_intent(conn, "entry-intent")
    assert store.link_entry_intent(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="entry-intent",
    )
    assert conn.execute("SELECT entry_intent_id FROM positions").fetchone() == (
        "entry-intent",
    )


def test_intent_link_is_idempotent_rejects_overwrite_and_does_not_commit() -> None:
    conn = sqlite3.connect(":memory:")
    _order_intents_schema(conn)
    store = PositionStore(conn)
    store.ensure_schema()
    store.open_legacy_position(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        account_name="primary",
        symbol="005930",
    )
    _insert_intent(conn, "entry-one")
    _insert_intent(conn, "entry-two")
    conn.commit()

    conn.execute("BEGIN")
    assert store.link_entry_intent(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="entry-one",
    )
    assert store.link_entry_intent(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="entry-one",
    )
    store.close_legacy_position(
        market="KR", legacy_holding_id=1, account_id="acct"
    )
    assert store.link_entry_intent(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="entry-one",
    )
    conn.execute(
        "UPDATE positions SET status='OPEN', closed_at=NULL WHERE id='legacy:KR:1'"
    )
    conn.execute(
        "UPDATE order_intents SET source_position_id='legacy:KR:999' "
        "WHERE id='entry-one'"
    )
    with pytest.raises(ValueError, match="source_position_id"):
        store.link_entry_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="entry-one",
        )
    conn.execute(
        "UPDATE order_intents SET source_position_id='legacy:KR:1' "
        "WHERE id='entry-one'"
    )
    with pytest.raises(ValueError, match="already linked"):
        store.link_entry_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="entry-two",
        )
    conn.rollback()

    assert conn.execute("SELECT entry_intent_id FROM positions").fetchone() == (None,)


def test_entry_and_exit_intent_links_enforce_position_state() -> None:
    conn = sqlite3.connect(":memory:")
    _order_intents_schema(conn)
    store = PositionStore(conn)
    store.ensure_schema()
    store.open_legacy_position(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        account_name="primary",
        symbol="005930",
    )
    _insert_intent(conn, "entry-intent")
    _insert_intent(conn, "exit-intent", side="SELL")

    with pytest.raises(InvalidPositionTransition, match="CLOSED"):
        store.link_exit_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="exit-intent",
        )

    store.close_legacy_position(
        market="KR", legacy_holding_id=1, account_id="acct"
    )
    with pytest.raises(InvalidPositionTransition, match="OPEN"):
        store.link_entry_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="entry-intent",
        )
    assert store.link_exit_intent(
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="exit-intent",
    )


def test_us_sibling_positions_can_share_one_persisted_exit_intent() -> None:
    conn = sqlite3.connect(":memory:")
    _order_intents_schema(conn)
    store = PositionStore(conn)
    store.ensure_schema()
    _insert_intent(
        conn,
        "full-exit",
        market="US",
        account_id="acct",
        symbol="AAPL",
        side="SELL",
        source_position_id=(
            "legacy:US:1,legacy:US:2,legacy:US:3"
        ),
    )
    expected_position_ids = {
        legacy_position_id("US", legacy_holding_id)
        for legacy_holding_id in (1, 2, 3)
    }
    for legacy_holding_id in (1, 2, 3):
        store.open_legacy_position(
            market="US",
            legacy_holding_id=legacy_holding_id,
            account_id="acct",
            account_name="primary",
            symbol="AAPL",
        )
    conn.execute("UPDATE positions SET symbol='MSFT' WHERE id='legacy:US:3'")
    with pytest.raises(ValueError, match="does not match position"):
        store.link_exit_intent(
            market="US",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="full-exit",
            expected_position_ids=expected_position_ids,
        )
    conn.execute("UPDATE positions SET symbol='AAPL' WHERE id='legacy:US:3'")

    conn.execute("UPDATE positions SET account_id='other' WHERE id='legacy:US:3'")
    with pytest.raises(LookupError, match="source positions not found"):
        store.link_exit_intent(
            market="US",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="full-exit",
            expected_position_ids=expected_position_ids,
        )
    conn.execute("UPDATE positions SET account_id='acct' WHERE id='legacy:US:3'")

    with pytest.raises(InvalidPositionTransition, match="CLOSED"):
        store.link_exit_intent(
            market="US",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="full-exit",
            expected_position_ids=expected_position_ids,
        )

    for legacy_holding_id in (1, 2, 3):
        store.close_legacy_position(
            market="US",
            legacy_holding_id=legacy_holding_id,
            account_id="acct",
        )

    conn.execute(
        "UPDATE order_intents SET source_position_id="
        "'legacy:US:1,legacy:US:2,legacy:US:999' WHERE id='full-exit'"
    )
    with pytest.raises(LookupError, match="source positions not found"):
        store.link_exit_intent(
            market="US",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="full-exit",
            expected_position_ids={
                "legacy:US:1",
                "legacy:US:2",
                "legacy:US:999",
            },
        )
    conn.execute(
        "UPDATE order_intents SET source_position_id="
        "'legacy:US:1,legacy:US:2,legacy:US:3' WHERE id='full-exit'"
    )

    assert store.link_exit_intent(
        market="US",
        legacy_holding_id=1,
        account_id="acct",
        intent_id="full-exit",
        expected_position_ids=expected_position_ids,
    )

    assert conn.execute(
        "SELECT COUNT(*) FROM positions WHERE exit_intent_id='full-exit'"
    ).fetchone() == (3,)


def test_bounded_link_lock_wait_is_short_and_comparator_detects_missing_link(
    tmp_path,
) -> None:
    db_path = tmp_path / "locked.sqlite"
    conn = sqlite3.connect(db_path)
    _legacy_schema(conn)
    _order_intents_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "acct", "005930")
    store = PositionStore(conn)
    store.ensure_schema()
    store.backfill_legacy_positions("KR")
    _insert_intent(conn, "entry-intent")
    conn.commit()

    blocker = sqlite3.connect(db_path)
    blocker.execute("BEGIN")
    blocker.execute("SELECT * FROM positions").fetchall()
    logger = MagicMock()
    original_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    started = time.monotonic()
    linked = bounded_link_write_fail_open(
        conn,
        logger=logger,
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        operation="link_entry_intent",
        write=lambda position_store: position_store.link_entry_intent(
            market="KR",
            legacy_holding_id=1,
            account_id="acct",
            intent_id="entry-intent",
        ),
    )
    elapsed = time.monotonic() - started

    assert linked is False
    assert elapsed < 1.0
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == original_timeout
    assert logger.critical.call_count >= 1
    assert conn.execute(
        "SELECT COUNT(*) FROM position_mirror_errors"
    ).fetchone() == (0,)

    blocker.rollback()
    comparison = PositionStore(conn).compare_legacy_positions("KR")
    assert comparison["matches"] is False
    assert comparison["intent_link_mismatches"] == [
        {
            "intent_id": "entry-intent",
            "position_id": "legacy:KR:1",
            "account_ref": account_fingerprint("acct"),
            "symbol": "005930",
            "side": "BUY",
            "reasons": ["missing_or_wrong_link"],
        }
    ]


def test_bounded_link_recognizes_python310_lock_error_without_error_code() -> None:
    conn = sqlite3.connect(":memory:")
    logger = MagicMock()

    def raise_legacy_lock_error(_store) -> None:
        error = sqlite3.OperationalError("database is locked")
        assert getattr(error, "sqlite_errorcode", None) is None
        raise error

    assert bounded_link_write_fail_open(
        conn,
        logger=logger,
        market="KR",
        legacy_holding_id=1,
        account_id="acct",
        operation="link_entry_intent",
        write=raise_legacy_lock_error,
    ) is False
    assert logger.critical.call_count >= 2


def test_compare_is_read_only_and_reports_structured_mismatches_and_audit_errors() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "secret-account", "005930")
    _insert_legacy(conn, "stock_holdings", 2, "secret-account", "000660")
    store = PositionStore(conn)
    store.ensure_schema()
    store.open_legacy_position(
        market="KR",
        legacy_holding_id=1,
        account_id="secret-account",
        account_name="primary",
        symbol="005930",
        entry_price=100,
        opened_at="2026-07-18",
    )
    store.open_legacy_position(
        market="KR",
        legacy_holding_id=3,
        account_id="secret-account",
        account_name="primary",
        symbol="035420",
        entry_price=100,
        opened_at="2026-07-18",
    )
    store.open_legacy_position(
        market="KR",
        legacy_holding_id=2,
        account_id="secret-account",
        account_name="primary",
        symbol="000660",
        entry_price=100,
        opened_at="2026-07-18",
    )
    store.close_legacy_position(
        market="KR", legacy_holding_id=2, account_id="secret-account"
    )
    error_id = store.record_mirror_error(
        market="KR",
        legacy_holding_id=1,
        account_id="secret-account",
        operation="close",
        error=RuntimeError("failed account=secret-account"),
    )
    before = conn.total_changes

    result = store.compare_legacy_positions("KR")

    assert conn.total_changes == before
    assert not result["matches"]
    assert result["missing_positions"] == []
    assert result["extra_open_positions"][0]["legacy_holding_id"] == "3"
    assert result["non_open_positions"][0] == {
        "legacy_holding_id": "2",
        "account_ref": account_fingerprint("secret-account"),
        "symbol": "000660",
        "status": "CLOSED",
    }
    assert result["unresolved_mirror_errors"][0]["id"] == error_id
    assert "secret-account" not in json.dumps(result, sort_keys=True)
    stored_error = conn.execute(
        "SELECT account_ref, error_message FROM position_mirror_errors WHERE id=?",
        (error_id,),
    ).fetchone()
    assert stored_error == (
        account_fingerprint("secret-account"),
        "failed account=[REDACTED]",
    )

    assert store.resolve_mirror_error(error_id)
    assert not store.resolve_mirror_error(error_id)


def test_compare_detects_entry_fingerprint_drift_without_exposing_account() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "secret-account", "005930", 70000)
    store = PositionStore(conn)
    store.ensure_schema()
    assert store.backfill_legacy_positions("KR")["inserted"] == 1
    conn.execute(
        "UPDATE positions SET entry_price=71000 WHERE market='KR' "
        "AND legacy_holding_id='1'"
    )

    result = store.compare_legacy_positions("KR")

    assert not result["matches"]
    assert len(result["entry_mismatches"]) == 1
    payload = json.dumps(result, sort_keys=True)
    assert "secret-account" not in payload


def test_mirror_error_redacts_credentials() -> None:
    conn = sqlite3.connect(":memory:")
    store = PositionStore(conn)
    store.ensure_schema()
    store.record_mirror_error(
        market="US",
        legacy_holding_id=1,
        account_id="secret-account",
        operation="open",
        error=RuntimeError(
            "Bearer bearer-secret api_key=key-secret token:token-secret "
            "password=pass-secret account=secret-account"
        ),
    )

    message = conn.execute(
        "SELECT error_message FROM position_mirror_errors"
    ).fetchone()[0]
    assert "bearer-secret" not in message
    assert "key-secret" not in message
    assert "token-secret" not in message
    assert "pass-secret" not in message
    assert "secret-account" not in message


def test_mirror_savepoint_rolls_back_partial_write_and_keeps_audit() -> None:
    conn = sqlite3.connect(":memory:")
    _legacy_schema(conn)
    store = PositionStore(conn)
    store.ensure_schema()
    _insert_legacy(conn, "stock_holdings", 1, "secret-account", "005930")

    class Logger:
        def critical(self, *_args, **_kwargs):
            pass

    def broken_write(position_store: PositionStore) -> None:
        position_store.open_legacy_position(
            market="KR",
            legacy_holding_id=1,
            account_id="secret-account",
            account_name="primary",
            symbol="005930",
            entry_price=70000,
            opened_at="2026-07-18",
        )
        raise RuntimeError("token=top-secret account=secret-account")

    assert not mirror_write_fail_open(
        conn.cursor(),
        logger=Logger(),
        market="KR",
        legacy_holding_id=1,
        account_id="secret-account",
        operation="open",
        write=broken_write,
    )
    conn.commit()

    assert conn.execute("SELECT COUNT(*) FROM stock_holdings").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 0
    error = conn.execute(
        "SELECT account_ref, error_message, resolved FROM position_mirror_errors"
    ).fetchone()
    assert error[0] == account_fingerprint("secret-account")
    assert "secret-account" not in error[1]
    assert "top-secret" not in error[1]
    assert error[2] == 0


def test_compare_cli_is_read_only_and_never_prints_raw_account(
    tmp_path, capsys
) -> None:
    db_path = tmp_path / "ledger.sqlite"
    conn = sqlite3.connect(db_path)
    _legacy_schema(conn)
    _insert_legacy(conn, "stock_holdings", 1, "secret-account", "005930")
    _insert_legacy(conn, "us_stock_holdings", 2, "us-secret-account", "AAPL")
    store = PositionStore(conn)
    store.ensure_schema()
    store.backfill_legacy_positions("KR")
    store.backfill_legacy_positions("US")
    conn.commit()
    before = conn.total_changes
    conn.close()

    assert compare_main(["--db-path", str(db_path)]) == 0
    output = capsys.readouterr().out
    assert '"status": "ok"' in output
    assert "secret-account" not in output
    assert "us-secret-account" not in output

    verify = sqlite3.connect(db_path)
    assert verify.total_changes == 0
    verify.close()
    assert before > 0

    command = [
        sys.executable,
        str(Path(__file__).parents[1] / "tools" / "compare_position_ledger.py"),
        "--db-path",
        str(db_path),
    ]
    direct = subprocess.run(
        command,
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )
    assert direct.returncode == 0, direct.stderr
    assert '"status": "ok"' in direct.stdout
    assert "secret-account" not in direct.stdout
    assert "us-secret-account" not in direct.stdout

    conn = sqlite3.connect(db_path)
    conn.execute(
        "DELETE FROM positions WHERE market='KR' AND legacy_holding_id='1'"
    )
    conn.commit()
    conn.close()

    assert compare_main(["--db-path", str(db_path), "--market", "kr"]) == 1
    assert '"status": "mismatch"' in capsys.readouterr().out


def test_compare_cli_returns_setup_error_without_leaking_details(tmp_path, capsys) -> None:
    missing = tmp_path / "missing.sqlite"

    assert compare_main(["--db-path", str(missing)]) == 2
    output = capsys.readouterr().out
    assert '"status": "error"' in output
    assert str(missing) not in output
