from __future__ import annotations

import json
import sqlite3

from core.actions import OpenIntent
from core.entries import EntryEvaluation
from engine.regime import RegimeSnapshot, TFState
from engine.signal import Signal
from engine.sizing import SizingResult
from live import decision_capture, tracking


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    tracking.ensure_schema(connection)
    return connection


def _snapshot() -> RegimeSnapshot:
    return RegimeSnapshot(
        tf_states={
            tf: TFState(
                trend="up",
                candle_position="above_all",
                ma10=100.0,
                ma35=90.0,
                close=110.0,
                atr14=5.0,
            )
            for tf in ("30m", "1h", "4h", "12h", "1d", "1w")
        },
        alignment_score=82.5,
        evaluated_at="2026-08-31T00:00:00Z",
    )


def test_signal_capture_is_stable_versioned_and_secret_minimized() -> None:
    connection = _conn()
    decision_id = decision_capture.capture_signal_decision(
        connection,
        ts="2026-08-31T00:00:00Z",
        mode="demo",
        strategy_id="main_trend_v1",
        snapshot=_snapshot(),
        signal=Signal(side="long", strength=82.5, reason="롱신호 score=82.5"),
        bar_close=110.0,
        positions=[],
        equity=10_000.0,
        peak_equity=10_500.0,
        pending=False,
        code_version="abc123",
    )
    repeated = decision_capture.capture_signal_decision(
        connection,
        ts="2026-08-31T00:00:00Z",
        mode="demo",
        strategy_id="main_trend_v1",
        snapshot=_snapshot(),
        signal=Signal(side="long", strength=82.5, reason="롱신호 score=82.5"),
        bar_close=110.0,
        positions=[],
        equity=10_000.0,
        peak_equity=10_500.0,
        pending=False,
        code_version="abc123",
    )

    assert repeated == decision_id
    row = dict(
        connection.execute(
            "SELECT * FROM btc_decision_log WHERE decision_id=?", (decision_id,)
        ).fetchone()
    )
    market = json.loads(row["market_snapshot"])
    position = json.loads(row["position_context"])
    assert row["schema_version"] == 1
    assert row["strategy_id"] == "main_trend_v1"
    assert row["signal_reason_code"] == "SIGNAL_ACCEPTED"
    assert row["entry_status"] == "PENDING_EVALUATION"
    assert len(row["config_hash"]) == 24
    assert len(row["input_hash"]) == 24
    assert market["tf_states"]["4h"]["trend_strength"] == 2.0
    assert position["n_open"] == 0
    assert position["effective_n_open"] == 0
    serialized = json.dumps(row, ensure_ascii=False)
    assert "api_key" not in serialized
    connection.close()


def test_final_entry_capture_records_rejection_and_accepted_intent() -> None:
    connection = _conn()
    decision_id = decision_capture.capture_signal_decision(
        connection,
        ts="2026-08-31T00:00:00Z",
        mode="shadow",
        strategy_id="main_trend_v1",
        snapshot=_snapshot(),
        signal=Signal(side="long", strength=82.5, reason="롱신호 score=82.5"),
        bar_close=110.0,
        positions=[],
        equity=10_000.0,
        peak_equity=10_000.0,
        pending=False,
        code_version="abc123",
    )

    decision_capture.finalize_entry_decision(
        connection,
        decision_id,
        EntryEvaluation(None, "cooldown 4/16"),
        current_tranche=0,
    )
    rejected = connection.execute(
        "SELECT entry_status, entry_rejection_code FROM btc_decision_log"
    ).fetchone()
    assert tuple(rejected) == ("REJECTED", "COOLDOWN")

    sizing = SizingResult(
        leverage=10.0,
        qty=0.1,
        sl_price=100.0,
        tp1_price=120.0,
        tp2_price=130.0,
        tp3_price=140.0,
        liq_price=95.0,
        tranche_index=0,
    )
    accepted = EntryEvaluation(
        OpenIntent(
            side="long",
            limit_price=110.0,
            sizing=sizing,
            initial_risk=200.0,
            tranche_index=0,
        ),
        "accepted",
    )
    decision_capture.finalize_entry_decision(
        connection,
        decision_id,
        accepted,
        current_tranche=0,
    )
    row = connection.execute(
        "SELECT entry_status, entry_rejection_code, entry_context "
        "FROM btc_decision_log"
    ).fetchone()
    context = json.loads(row["entry_context"])
    assert row["entry_status"] == "ACCEPTED"
    assert row["entry_rejection_code"] is None
    assert context["qty"] == 0.1
    assert context["leverage"] == 10.0
    assert context["initial_risk"] == 200.0
    connection.close()


def test_signal_none_is_final_without_entry_evaluation() -> None:
    connection = _conn()
    decision_id = decision_capture.capture_signal_decision(
        connection,
        ts="2026-08-31T04:00:00Z",
        mode="demo",
        strategy_id="main_trend_v1",
        snapshot=_snapshot(),
        signal=Signal(side="none", strength=10.0, reason="score=10 < 70"),
        bar_close=110.0,
        positions=[],
        equity=10_000.0,
        peak_equity=10_000.0,
        pending=False,
        code_version="abc123",
    )
    row = connection.execute(
        "SELECT entry_status, signal_reason_code FROM btc_decision_log "
        "WHERE decision_id=?",
        (decision_id,),
    ).fetchone()
    assert tuple(row) == ("SIGNAL_REJECTED", "SCORE_BELOW_MIN")
    connection.close()
