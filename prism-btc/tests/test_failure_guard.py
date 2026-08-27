from __future__ import annotations

import sqlite3

import pandas as pd

from core.failure_guard import (
    FailureGuardFeatures,
    classify_failure_cluster,
    should_observe_pyramid_block,
)
from engine.indicators import add_indicators
from live import failure_observer, tracking
from live.failure_observer import FailureObserver, load_oi_change_6h


def test_frozen_model_classifies_c1_and_normal_c3() -> None:
    c1 = FailureGuardFeatures(
        ts_4h=2.1103,
        ts_1d=0.9968,
        extension_4h=2.9909,
        ret_24h=0.0231,
        atr_ratio_4h=0.0172,
        volume_ratio_4h=0.9948,
        range_ratio_4h=0.8338,
        funding_z=1.6717,
        oi_change_6h=0.0045,
        lane_swing=0.0,
    )
    c3 = FailureGuardFeatures(
        ts_4h=0.2997,
        ts_1d=1.1076,
        extension_4h=2.3694,
        ret_24h=0.0185,
        atr_ratio_4h=0.0150,
        volume_ratio_4h=1.2731,
        range_ratio_4h=0.9285,
        funding_z=0.0,
        oi_change_6h=0.0055,
        lane_swing=1.0,
    )
    assert classify_failure_cluster(c1) == 1
    assert classify_failure_cluster(c3) == 3


def test_missing_feature_fails_open_and_initial_entry_is_never_observed() -> None:
    incomplete = FailureGuardFeatures(
        ts_4h=2.1,
        ts_1d=1.0,
        extension_4h=3.0,
        ret_24h=0.02,
        atr_ratio_4h=0.017,
        volume_ratio_4h=1.0,
        range_ratio_4h=0.8,
        funding_z=1.6,
        oi_change_6h=None,
        lane_swing=0.0,
    )
    assert classify_failure_cluster(incomplete) is None
    assert should_observe_pyramid_block(cluster=1, tranche_index=0) is False
    assert should_observe_pyramid_block(cluster=1, tranche_index=1) is True
    assert should_observe_pyramid_block(cluster=None, tranche_index=1) is False


def test_oi_change_loader_is_point_in_time_and_stale_safe(tmp_path) -> None:
    path = tmp_path / "oi.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE open_interest (timestamp INTEGER PRIMARY KEY, "
        "open_interest REAL NOT NULL)"
    )
    start = pd.Timestamp("2025-01-01", tz="UTC")
    for hour in range(7):
        ts = int((start + pd.Timedelta(hours=hour)).timestamp() * 1000)
        conn.execute("INSERT INTO open_interest VALUES (?, ?)", (ts, 100.0 + hour))
    # Future row must never enter the 6h comparison.
    future = int((start + pd.Timedelta(hours=10)).timestamp() * 1000)
    conn.execute("INSERT INTO open_interest VALUES (?, ?)", (future, 1000.0))
    conn.commit()
    conn.close()

    as_of = start + pd.Timedelta(hours=6, minutes=30)
    change = load_oi_change_6h(path, as_of, max_stale_hours=2.0)
    assert change == (106.0 / 100.0) - 1.0
    assert load_oi_change_6h(
        path, start + pd.Timedelta(hours=9), max_stale_hours=2.0
    ) is None
    assert load_oi_change_6h(tmp_path / "missing.db", as_of) is None


def test_observer_records_c1_intent_without_returning_a_gate(tmp_path, monkeypatch) -> None:
    bar_time = pd.Timestamp("2025-01-01", tz="UTC")

    def frame(freq: str, periods: int) -> pd.DataFrame:
        index = pd.date_range(end=bar_time - pd.Timedelta(freq), periods=periods,
                              freq=freq, tz="UTC")
        close = pd.Series([100.0 + i * 0.1 for i in range(periods)], index=index)
        raw = pd.DataFrame({
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "volume": [100.0 + (i % 5) for i in range(periods)],
            "turnover": [1.0] * periods,
        }, index=index)
        return add_indicators(raw)

    tf_data = {"4h": frame("4h", 220), "1d": frame("1d", 100)}
    oi_path = tmp_path / "oi.db"
    oi_conn = sqlite3.connect(oi_path)
    oi_conn.execute(
        "CREATE TABLE open_interest (timestamp INTEGER PRIMARY KEY, "
        "open_interest REAL NOT NULL)"
    )
    for hours_back in range(6, -1, -1):
        ts = bar_time - pd.Timedelta(hours=hours_back)
        oi_conn.execute(
            "INSERT INTO open_interest VALUES (?, ?)",
            (int(ts.timestamp() * 1000), 100.0 + (6 - hours_back)),
        )
    oi_conn.commit()
    oi_conn.close()

    root = sqlite3.connect(":memory:")
    root.row_factory = sqlite3.Row
    tracking.ensure_schema(root)
    funding_times = [
        int((bar_time - pd.Timedelta(hours=8 * (180 - i))).timestamp() * 1000)
        for i in range(181)
    ]
    funding_rates = [0.00005 + (i % 7) * 0.000001 for i in range(181)]
    monkeypatch.setattr(failure_observer, "classify_failure_cluster", lambda _: 1)
    observer = FailureObserver(
        root, tf_data, funding_times, funding_rates,
        oi_path=oi_path, mode="shadow",
    )
    observation_id = observer.observe(
        bar_time=bar_time, side="long", tranche_index=1
    )
    assert observation_id is not None
    row = root.execute(
        "SELECT * FROM btc_failure_shadow WHERE id=?", (observation_id,)
    ).fetchone()
    assert row["cluster"] == 1 and row["status"] == "intent"
    event = root.execute(
        "SELECT kind FROM btc_events WHERE kind='c1_pyramid_would_block'"
    ).fetchone()
    assert event is not None
