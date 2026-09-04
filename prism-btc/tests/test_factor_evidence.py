from __future__ import annotations

import json

import numpy as np
import pandas as pd

from analysis.factor_evidence import build_evidence_packet


def _decisions(count: int) -> list[dict]:
    times = pd.date_range("2025-01-01", periods=count, freq="4h", tz="UTC")
    return [
        {
            "decision_id": f"d-{i}",
            "ts": str(ts + pd.Timedelta(hours=4)),
            "signal_side": "long" if i % 3 == 0 else "none",
            "market_snapshot": json.dumps({
                "ohlcv_factors": {
                    "timeframes": {
                        "4h": {
                            "status": "ok",
                            "as_of_open_time": str(ts),
                            "values": {"trend_r2_21": i / count},
                        }
                    }
                }
            }),
        }
        for i, ts in enumerate(times)
    ]


def test_factor_evidence_uses_purged_chronological_windows() -> None:
    times = pd.date_range("2025-01-01", periods=150, freq="4h", tz="UTC")
    closes = pd.Series(
        np.exp(np.arange(150, dtype=float) ** 2 * 0.00005) * 100.0,
        index=times,
    )

    packet = build_evidence_packet(
        _decisions(130),
        closes,
        horizon_bars=3,
        execution_lag=1,
        train_size=50,
        validation_size=20,
        test_size=20,
        step_size=20,
    )

    assert packet["schema_version"] == 1
    assert packet["status"] == "ready"
    assert packet["embargo_size"] == 4
    assert packet["labeled_decisions"] == 130
    assert packet["splits"]
    first = packet["splits"][0]
    assert first["ranges"]["train"] == [0, 50]
    assert first["ranges"]["validation"] == [54, 74]
    assert first["ranges"]["test"] == [78, 98]
    assert first["factors"]["trend_r2_21"]["validation"]["correlation"] > 0
    assert first["factors"]["trend_r2_21"]["test"]["correlation"] > 0


def test_factor_evidence_refuses_to_claim_on_insufficient_sample() -> None:
    times = pd.date_range("2025-01-01", periods=20, freq="4h", tz="UTC")
    closes = pd.Series(np.arange(20, dtype=float) + 100.0, index=times)

    packet = build_evidence_packet(
        _decisions(10),
        closes,
        horizon_bars=3,
        execution_lag=1,
        train_size=10,
        validation_size=5,
        test_size=5,
    )

    assert packet["status"] == "insufficient"
    assert packet["splits"] == []
    assert packet["promotion_allowed"] is False
