import json

from live import tracking
from live.exit_capture import capture


def test_snapshot_spool_and_mode_isolation(tmp_path, monkeypatch):
    spool = tmp_path / "events.jsonl"
    monkeypatch.setenv("PRISM_OBSERVABILITY_SPOOL", str(spool))
    conn = tracking.get_connection(":memory:")
    tracking.ensure_schema(conn)
    tracking.record_equity(conn, 10000, "demo")
    tracking.save_position(conn, tracking.PositionRow(
        side="long", entry_price=100, qty=2, leverage=5, sl_price=95,
        tp1_price=105, tp2_price=110, tp3_price=115, liq_price=80,
        entry_time="2026-09-05T00:00:00Z", tranche_index=0,
        entry_bar_idx=0, initial_risk=10, mode="demo"))
    capture(conn, mode="swing", timestamp="t", mark_price=102, stage="before")
    assert not spool.exists()
    capture(conn, mode="demo", timestamp="t", mark_price=102, stage="before")
    event = json.loads(spool.read_text())
    assert event["event_type"] == "btc.exit.snapshot"
    attrs = event["attributes"]
    assert attrs["gross_pnl_estimate"] == 4
    assert attrs["effective_exposure"] == .0204
    assert attrs["net_pnl"] is None
    assert attrs["exchange_stop_confirmed"] is None
    conn.close()


def test_capture_failure_does_not_raise():
    capture(None, mode="demo", timestamp="t", mark_price=None, stage="before")
