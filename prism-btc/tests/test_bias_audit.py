from __future__ import annotations

import pandas as pd
from analysis.bias_audit import audit_snapshots, precompute_indicators

_FREQUENCIES = {
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "12h": "12h",
    "1d": "1D",
    "1w": "7D",
}


def _tf_data(rows: int = 120) -> dict[str, pd.DataFrame]:
    result = {}
    for offset, (timeframe, frequency) in enumerate(_FREQUENCIES.items()):
        index = pd.date_range("2020-01-01", periods=rows, freq=frequency, tz="UTC")
        base = pd.Series(range(rows), index=index, dtype=float) + 100 + offset
        result[timeframe] = pd.DataFrame(
            {
                "open": base,
                "high": base + 2,
                "low": base - 2,
                "close": base + 1,
                "volume": 1000.0,
                "turnover": 100_000.0,
            },
            index=index,
        )
    return result


def test_audit_passes_for_causal_sma_atr_pipeline() -> None:
    data = _tf_data()
    evaluation = data["1w"].index[-1] + pd.Timedelta(days=7)

    result = audit_snapshots(
        data,
        [evaluation],
        startup_sizes=(40, 80),
        required_startup_size=80,
    )

    assert result["passed"] is True
    assert result["lookahead_bias_count"] == 0
    assert result["recursive_drift_count"] == 0
    assert result["sample_count"] == 1


def test_audit_detects_future_contaminated_precomputed_indicator() -> None:
    data = _tf_data()
    evaluation = data["1w"].index[-1] + pd.Timedelta(days=7)
    contaminated = precompute_indicators(data)
    contaminated["4h"].loc[:, "ma10"] += 50.0

    result = audit_snapshots(
        data,
        [evaluation],
        startup_sizes=(40, 80),
        required_startup_size=80,
        precomputed_tf_data=contaminated,
    )

    assert result["passed"] is False
    assert result["lookahead_bias_count"] > 0
    assert any(item["timeframe"] == "4h" for item in result["lookahead_differences"])


def test_audit_reports_missing_snapshot_without_false_pass() -> None:
    data = _tf_data(rows=20)
    evaluation = data["1w"].index[-1] + pd.Timedelta(days=7)

    result = audit_snapshots(
        data,
        [evaluation],
        startup_sizes=(40,),
        required_startup_size=40,
    )

    assert result["passed"] is False
    assert result["missing_snapshot_count"] == 1
