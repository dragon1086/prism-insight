from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from live import ack_probe, tracking


NOW = datetime(2026, 8, 31, 3, 0, tzinfo=timezone.utc)


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    tracking.ensure_schema(connection)
    return connection


class _ProbeSession:
    def __init__(
        self,
        *,
        position: bool = False,
        open_order: bool = False,
        fail_positions: bool = False,
        position_after_submit: bool = False,
    ) -> None:
        self.position = position
        self.open_order = open_order
        self.fail_positions = fail_positions
        self.position_after_submit = position_after_submit
        self.calls: list[tuple[str, dict]] = []

    @staticmethod
    def _ok(rows: list[dict]) -> dict:
        return {"retCode": 0, "retMsg": "OK", "result": {"list": rows}}

    def get_positions(self, **kwargs):
        self.calls.append(("get_positions", kwargs))
        if self.fail_positions:
            return {"retCode": 10001, "retMsg": "temporary", "result": {}}
        rows = []
        if self.position:
            rows = [{"symbol": "BTCUSDT", "side": "Buy", "size": "0.001"}]
        return self._ok(rows)

    def get_open_orders(self, **kwargs):
        self.calls.append(("get_open_orders", kwargs))
        rows = []
        if self.open_order:
            rows = [{"symbol": "BTCUSDT", "orderId": "probe-order"}]
        return self._ok(rows)

    def get_tickers(self, **kwargs):
        self.calls.append(("get_tickers", kwargs))
        return self._ok(
            [{"symbol": "BTCUSDT", "lastPrice": "100000", "bid1Price": "99999"}]
        )

    def place_order(self, **kwargs):
        self.calls.append(("place_order", kwargs))
        self.open_order = True
        if self.position_after_submit:
            self.position = True
            self.open_order = False
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "raw-probe-order-id"},
        }

    def cancel_order(self, **kwargs):
        self.calls.append(("cancel_order", kwargs))
        self.open_order = False
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": kwargs["orderId"]},
        }


def _start(connection: sqlite3.Connection, **kwargs) -> dict:
    return ack_probe.start_probe(connection, now=NOW, **kwargs)


def test_probe_places_only_far_postonly_limit_then_cancels() -> None:
    connection = _conn()
    session = _ProbeSession()
    _start(connection)

    result = ack_probe.run_probe_once(connection, session=session, now=NOW)

    assert result["status"] == "clean"
    assert result["completed_cycles"] == 1
    place = next(kwargs for name, kwargs in session.calls if name == "place_order")
    assert place == {
        "category": "linear",
        "symbol": "BTCUSDT",
        "side": "Buy",
        "orderType": "Limit",
        "qty": "0.001",
        "price": "94999.0",
        "timeInForce": "PostOnly",
        "positionIdx": 0,
        "orderLinkId": place["orderLinkId"],
    }
    assert place["orderLinkId"].startswith("prism-ack-")
    assert [name for name, _ in session.calls].count("cancel_order") == 1
    assert not session.open_order
    assert not session.position

    rows = connection.execute(
        "SELECT operation, phase, success, order_ref FROM btc_execution_samples "
        "ORDER BY id"
    ).fetchall()
    assert [tuple(row[:3]) for row in rows] == [
        ("probe_submit", "SUBMIT_TO_ACK", 1),
        ("probe_cancel", "SUBMIT_TO_ACK", 1),
    ]
    assert all(len(row[3]) == 24 for row in rows)
    assert "raw-probe-order-id" not in str([dict(row) for row in rows])
    connection.close()


def test_probe_alternates_buy_below_and_sell_above_market() -> None:
    connection = _conn()
    session = _ProbeSession()
    _start(connection)

    first = ack_probe.run_probe_once(connection, session=session, now=NOW)
    second = ack_probe.run_probe_once(
        connection, session=session, now=NOW + timedelta(hours=2)
    )

    assert first["side"] == "Buy"
    assert first["price"] == 94_999.0
    assert second["side"] == "Sell"
    assert second["price"] == 105_000.0
    connection.close()


def test_probe_skips_when_position_or_open_order_exists() -> None:
    for session, expected in (
        (_ProbeSession(position=True), "skipped_position"),
        (_ProbeSession(open_order=True), "skipped_open_orders"),
    ):
        connection = _conn()
        _start(connection)

        result = ack_probe.run_probe_once(connection, session=session, now=NOW)

        assert result["status"] == expected
        assert all(name != "place_order" for name, _ in session.calls)
        assert result["completed_cycles"] == 0
        connection.close()


def test_probe_fails_closed_when_precheck_is_unknown(monkeypatch) -> None:
    connection = _conn()
    session = _ProbeSession(fail_positions=True)
    _start(connection)
    monkeypatch.setattr("live.demo.time.sleep", lambda _seconds: None)

    result = ack_probe.run_probe_once(connection, session=session, now=NOW)

    assert result["status"] == "precheck_failed"
    assert all(name != "place_order" for name, _ in session.calls)
    connection.close()


def test_probe_halts_if_position_appears_after_submit() -> None:
    connection = _conn()
    session = _ProbeSession(position_after_submit=True)
    _start(connection)

    result = ack_probe.run_probe_once(connection, session=session, now=NOW)

    assert result["status"] == "halted_position_detected"
    state = ack_probe.get_probe_status(connection, now=NOW)
    assert state["probe_status"] == "halted"
    assert state["completed_cycles"] == 0
    connection.close()


def test_probe_stops_at_target_or_deadline_without_order() -> None:
    target_conn = _conn()
    target_session = _ProbeSession()
    _start(target_conn, target_cycles=1)
    ack_probe.run_probe_once(target_conn, session=target_session, now=NOW)
    target_result = ack_probe.run_probe_once(
        target_conn, session=target_session, now=NOW + timedelta(hours=2)
    )
    assert target_result["status"] == "complete"
    assert [name for name, _ in target_session.calls].count("place_order") == 1

    deadline_conn = _conn()
    deadline_session = _ProbeSession()
    _start(deadline_conn, duration_hours=1)
    deadline_result = ack_probe.run_probe_once(
        deadline_conn, session=deadline_session, now=NOW + timedelta(hours=1)
    )
    assert deadline_result["status"] == "expired"
    assert all(name != "place_order" for name, _ in deadline_session.calls)
    target_conn.close()
    deadline_conn.close()
