from __future__ import annotations

import json
import sqlite3

from analysis.execution_latency_packet import build_latency_packet
from live import tracking
from live.demo import DemoAdapter


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    tracking.ensure_schema(connection)
    return connection


def test_execution_sample_hashes_order_id_and_keeps_safe_details() -> None:
    connection = _conn()
    order_ref = tracking.execution_order_ref("raw-order-id-123")
    tracking.record_execution_sample(
        connection,
        mode="demo",
        operation="entry_submit",
        phase="SUBMIT_TO_ACK",
        order_ref=order_ref,
        request_at="2026-08-31T00:00:00Z",
        completed_at="2026-08-31T00:00:00.125Z",
        latency_ms=125.0,
        success=True,
        retry_count=0,
        ret_code=0,
        details={
            "side": "Buy",
            "order_type": "Limit",
            "qty": 0.1,
            "price": 100_000.0,
            "api_key": "must-not-store",
        },
    )

    row = dict(connection.execute("SELECT * FROM btc_execution_samples").fetchone())
    details = json.loads(row["details"])
    assert len(row["order_ref"]) == 24
    assert "raw-order-id-123" not in json.dumps(row)
    assert details == {
        "order_type": "Limit",
        "price": 100_000.0,
        "qty": 0.1,
        "side": "Buy",
    }
    connection.close()


def test_latency_packet_reports_percentiles_and_retry_rate() -> None:
    samples = []
    for index, latency in enumerate((100.0, 200.0, 300.0, 400.0), 1):
        samples.append(
            {
                "id": index,
                "mode": "demo",
                "operation": "entry_submit",
                "phase": "SUBMIT_TO_ACK",
                "order_ref": f"ref-{index}",
                "request_at": "2026-08-31T00:00:00Z",
                "completed_at": "2026-08-31T00:00:01Z",
                "latency_ms": latency,
                "success": 1 if index < 4 else 0,
                "retry_count": 1 if index == 2 else 0,
                "ret_code": 0 if index < 4 else 10001,
                "details": json.dumps({"side": "Buy", "secret": "drop-me"}),
            }
        )

    packet = build_latency_packet(samples)
    metrics = packet["cohorts"][0]
    assert packet["sample_count"] == 4
    assert metrics["p50_ms"] == 250.0
    assert metrics["p90_ms"] == 370.0
    assert metrics["p95_ms"] == 385.0
    assert metrics["success_rate"] == 0.75
    assert metrics["retry_rate"] == 0.25
    assert "drop-me" not in json.dumps(packet)
    assert packet["readiness"]["automatic_live_forbidden"] is True


def test_fill_confirmation_is_separate_from_exchange_fill_time() -> None:
    packet = build_latency_packet(
        [
            {
                "id": 1,
                "mode": "demo",
                "operation": "entry_fill",
                "phase": "ACK_TO_RECONCILE",
                "order_ref": "hashed-ref",
                "request_at": "2026-08-31T00:00:00Z",
                "completed_at": "2026-08-31T00:10:00Z",
                "latency_ms": 600_000.0,
                "success": 1,
                "retry_count": 0,
                "ret_code": 0,
                "details": json.dumps(
                    {"confirmation_source": "position_and_open_order_reconcile"}
                ),
            }
        ]
    )

    assert packet["cohorts"][0]["phase"] == "ACK_TO_RECONCILE"
    assert packet["interpretation"]["fill_latency"] == (
        "reconcile detection upper bound, not exchange execution timestamp"
    )


class _OrderSession:
    def __init__(self, *, fail_once: bool = False) -> None:
        self.fail_once = fail_once
        self.calls = 0

    def place_order(self, **_kwargs):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            return {"retCode": 10001, "retMsg": "temporary", "result": {}}
        return {
            "retCode": 0,
            "retMsg": "OK",
            "result": {"orderId": "raw-exchange-order-id"},
        }


def _adapter(connection: sqlite3.Connection, session: _OrderSession) -> DemoAdapter:
    adapter = DemoAdapter(connection, {}, [], [], mode="demo")
    adapter.sess = session
    return adapter


def test_demo_order_submit_records_ack_without_raw_order_id(monkeypatch) -> None:
    connection = _conn()
    adapter = _adapter(connection, _OrderSession())

    order_id = adapter._place_limit_postonly("long", 0.1, 100_000.0)

    assert order_id == "raw-exchange-order-id"
    row = dict(connection.execute("SELECT * FROM btc_execution_samples").fetchone())
    assert row["operation"] == "entry_submit"
    assert row["phase"] == "SUBMIT_TO_ACK"
    assert row["success"] == 1
    assert row["retry_count"] == 0
    assert row["order_ref"] == tracking.execution_order_ref(order_id)
    assert order_id not in json.dumps(row)
    connection.close()


def test_demo_order_submit_records_retry_count(monkeypatch) -> None:
    connection = _conn()
    session = _OrderSession(fail_once=True)
    adapter = _adapter(connection, session)
    monkeypatch.setattr("live.demo.time.sleep", lambda _seconds: None)

    adapter._place_limit_postonly("long", 0.1, 100_000.0)

    row = connection.execute(
        "SELECT success, retry_count, ret_code FROM btc_execution_samples"
    ).fetchone()
    assert tuple(row) == (1, 1, 0)
    connection.close()


def test_demo_fill_confirmation_records_ack_to_reconcile() -> None:
    connection = _conn()
    adapter = _adapter(connection, _OrderSession())
    pending = {
        "latency_order_ref": "hashed-ref",
        "latency_ack_wall_ns": 1_000_000_000,
        "side": "long",
        "sizing_qty": 0.1,
    }

    adapter._capture_fill_confirmation(
        pending,
        completed_wall_ns=1_600_000_000,
    )

    row = dict(connection.execute("SELECT * FROM btc_execution_samples").fetchone())
    assert row["operation"] == "entry_fill"
    assert row["phase"] == "ACK_TO_RECONCILE"
    assert row["latency_ms"] == 600.0
    assert json.loads(row["details"])["confirmation_source"] == (
        "position_and_open_order_reconcile"
    )
    connection.close()
