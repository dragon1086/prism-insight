from __future__ import annotations

import json

import pandas as pd
from analysis.strategy_evidence_packet import build_evidence_packet


def _decision() -> dict:
    return {
        "decision_id": "decision-1",
        "ts": "2026-08-31T00:00:00Z",
        "mode": "demo",
        "schema_version": 1,
        "strategy_id": "main_trend_v1",
        "code_version": "abc123",
        "config_hash": "c" * 24,
        "input_hash": "i" * 24,
        "signal_side": "long",
        "signal_strength": 82.5,
        "signal_reason_code": "SIGNAL_ACCEPTED",
        "entry_status": "ACCEPTED",
        "entry_rejection_code": None,
        "market_snapshot": json.dumps(
            {
                "bar_close": 100.0,
                "alignment_score": 82.5,
                "tf_states": {
                    "4h": {
                        "trend": "up",
                        "candle_position": "above_all",
                        "trend_strength": 2.5,
                    }
                },
                "api_key": "must-not-copy",
            }
        ),
        "position_context": json.dumps(
            {"n_open": 0, "effective_n_open": 0, "equity": 10_000.0}
        ),
        "entry_context": json.dumps(
            {"qty": 0.1, "leverage": 10.0, "initial_risk": 200.0}
        ),
    }


def _bars() -> pd.DataFrame:
    index = pd.date_range("2026-08-31T00:30:00Z", periods=336, freq="30min")
    close = [101.0 + index for index in range(336)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [value + 2 for value in close],
            "low": [value - 3 for value in close],
            "close": close,
        },
        index=index,
    )


def test_packet_is_deterministic_and_contains_forward_labels() -> None:
    decision = _decision()
    forward = build_evidence_packet([decision], _bars())
    reverse = build_evidence_packet(list(reversed([decision])), _bars())

    assert forward["packet_id"] == reverse["packet_id"]
    assert forward["packet_schema_version"] == 1
    assert forward["decision_count"] == 1
    row = forward["decisions"][0]
    assert row["outcomes"]["30m"]["return_pct"] == 1.0
    assert row["outcomes"]["3h"]["return_pct"] == 6.0
    assert row["outcomes"]["24h"]["mfe_pct"] == 50.0
    assert row["outcomes"]["7d"]["return_pct"] == 336.0
    assert row["market_snapshot"]["tf_states"]["4h"]["trend_strength"] == 2.5


def test_packet_whitelist_excludes_raw_snapshot_secrets() -> None:
    serialized = json.dumps(build_evidence_packet([_decision()], _bars()))

    assert "must-not-copy" not in serialized
    assert "api_key" not in serialized
    assert "automatic_live_forbidden" in serialized


def test_unmatured_horizons_remain_missing() -> None:
    short = _bars().iloc[:5]
    packet = build_evidence_packet([_decision()], short)

    assert packet["decisions"][0]["outcomes"]["30m"]["status"] == "OK"
    assert packet["decisions"][0]["outcomes"]["3h"]["status"] == "MISSING"
    assert packet["coverage"]["matured_horizons"]["30m"] == 1
    assert packet["coverage"]["matured_horizons"]["3h"] == 0
