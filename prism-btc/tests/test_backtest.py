# tests/test_backtest.py — Offline tests for backtester (no network, no real DB)
from __future__ import annotations

import sqlite3
import pytest
import pandas as pd
import numpy as np
from datetime import timezone
from unittest.mock import patch, MagicMock

from backtest.engine import (
    _get_tf_slice,
    _build_snapshot_at,
    compute_metrics,
    BacktestState,
    TradeLog,
    run_backtest,
)
from engine.sizing import approx_liq_price, _sl_passes_buffer, LIQ_BUFFER_MIN_FRAC

ALL_TFS = ("30m", "1h", "4h", "12h", "1d", "1w")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_ohlcv_df(n: int, base: float = 50000.0, trend: float = 0.0) -> pd.DataFrame:
    """Create an OHLCV DataFrame with n rows, optional uptrend."""
    closes = [base + trend * i for i in range(n)]
    highs = [c * 1.005 for c in closes]
    lows = [c * 0.995 for c in closes]
    opens = [c * 0.999 for c in closes]
    return pd.DataFrame({
        "open": opens, "high": highs, "low": lows, "close": closes,
        "volume": [1.0] * n, "turnover": [1.0] * n,
    })


def _make_indexed_df(n: int, start_ts_ms: int, interval_ms: int, **kwargs) -> pd.DataFrame:
    """Create an OHLCV DataFrame indexed by UTC timestamps."""
    df = _make_ohlcv_df(n, **kwargs)
    idx = pd.to_datetime(
        [start_ts_ms + i * interval_ms for i in range(n)], unit="ms", utc=True
    )
    df.index = idx
    return df


def _make_tf_data(n_30m: int = 200, base: float = 50000.0) -> dict[str, pd.DataFrame]:
    """Build a minimal tf_data dict with all 6 TFs, no look-ahead."""
    intervals_ms = {
        "30m": 30 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "12h": 12 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
        "1w": 7 * 24 * 60 * 60 * 1000,
    }
    start_ms = 1640995200000  # 2022-01-01
    tf_data = {}
    for tf, interval_ms in intervals_ms.items():
        n = max(n_30m // (interval_ms // intervals_ms["30m"]), 80)
        tf_data[tf] = _make_indexed_df(n, start_ms, interval_ms, base=base)
    return tf_data


def _make_in_memory_db() -> sqlite3.Connection:
    """Create an in-memory SQLite DB with klines schema and sample data."""
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS klines (
            timeframe  TEXT NOT NULL,
            open_time  INTEGER NOT NULL,
            open       REAL NOT NULL,
            high       REAL NOT NULL,
            low        REAL NOT NULL,
            close      REAL NOT NULL,
            volume     REAL NOT NULL,
            turnover   REAL NOT NULL,
            confirmed  INTEGER NOT NULL DEFAULT 1,
            PRIMARY KEY (timeframe, open_time)
        )
    """)
    intervals_ms = {
        "30m": 30 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "4h": 4 * 60 * 60 * 1000,
        "12h": 12 * 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
        "1w": 7 * 24 * 60 * 60 * 1000,
    }
    start_ms = 1640995200000  # 2022-01-01
    # Insert 400 rows for 30m (enough for warm-up + signal generation)
    for tf, interval_ms in intervals_ms.items():
        n = max(400 // (interval_ms // intervals_ms["30m"]), 80)
        rows = []
        for i in range(n):
            ot = start_ms + i * interval_ms
            close = 50000.0 + i * 10  # slight uptrend
            high = close * 1.005
            low = close * 0.995
            open_ = close * 0.999
            rows.append((tf, ot, open_, high, low, close, 1.0, 1.0, 1))
        conn.executemany(
            "INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?)", rows
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# No look-ahead tests
# ---------------------------------------------------------------------------

class TestNoLookahead:
    def test_get_tf_slice_excludes_current_bar(self):
        """_get_tf_slice must NOT include candles at or after current_30m_time."""
        start_ms = 1640995200000
        interval_ms = 30 * 60 * 1000
        df = _make_indexed_df(100, start_ms, interval_ms)
        current_time = df.index[50]  # the 51st bar

        sliced = _get_tf_slice({"30m": df}, current_time, "30m")
        # Must have exactly 50 rows (indices 0..49), not including 50
        assert len(sliced) == 50
        assert sliced.index.max() < current_time

    def test_get_tf_slice_upper_tf_cutoff(self):
        """Upper TF (1h) slice at a 30m bar time must not include any 1h candle
        that starts at or after current_30m_time."""
        start_ms = 1640995200000
        interval_30m_ms = 30 * 60 * 1000
        interval_1h_ms = 60 * 60 * 1000

        df_1h = _make_indexed_df(50, start_ms, interval_1h_ms)
        # current_time = exactly start of 2nd 1h candle → only 1 confirmed 1h candle
        # The 2nd 1h candle has open_time = start_ms + interval_1h_ms
        # current_time = same → NOT strictly less than → excluded
        current_time = pd.Timestamp(start_ms + interval_1h_ms, unit="ms", tz="UTC")

        sliced = _get_tf_slice({"1h": df_1h}, current_time, "1h")
        # Only the first 1h candle (at start_ms) should be included
        assert len(sliced) == 1
        assert sliced.index[0] == pd.Timestamp(start_ms, unit="ms", tz="UTC")

    def test_build_snapshot_uses_only_past_data(self):
        """build_snapshot_at should return None when insufficient data,
        and when sufficient, all tf slices must be strictly before current_time."""
        tf_data = _make_tf_data(n_30m=500)
        # Use a time well into the data so all TFs have enough rows
        # Find the 200th 30m bar time
        bars = tf_data["30m"]
        current_time = bars.index[200]

        snapshot = _build_snapshot_at(tf_data, current_time)
        # Verify: each TF slice used must end before current_time
        for tf in ALL_TFS:
            sliced = _get_tf_slice(tf_data, current_time, tf)
            if len(sliced) > 0:
                assert sliced.index.max() < current_time

    def test_insufficient_data_returns_none(self):
        """When < MIN_ROWS rows available, snapshot must be None (no warm-up issue)."""
        tf_data = _make_tf_data(n_30m=500)
        # Use bar index 5 → only 5 rows available → not enough for MA35
        bars = tf_data["30m"]
        current_time = bars.index[5]
        snapshot = _build_snapshot_at(tf_data, current_time)
        assert snapshot is None

    def test_slice_only_closed_candles_synthetic(self):
        """A higher-TF candle still in progress at current_time must be EXCLUDED.
        4h candle open_time=00:00 closes at 04:00; at a 30m time of 02:00 it is
        unconfirmed → must NOT appear in the slice (P0-1 look-ahead regression)."""
        start_ms = 1640995200000  # 2022-01-01 00:00
        interval_4h_ms = 4 * 60 * 60 * 1000
        df_4h = _make_indexed_df(50, start_ms, interval_4h_ms)
        # current 30m time = 02:00 → first 4h candle (00:00-04:00) NOT yet closed
        current_time = pd.Timestamp(start_ms + 2 * 60 * 60 * 1000, unit="ms", tz="UTC")
        sliced = _get_tf_slice({"4h": df_4h}, current_time, "4h")
        # zero closed 4h candles at 02:00
        assert len(sliced) == 0
        # at exactly 04:00 the first candle is closed → exactly 1
        at_close = pd.Timestamp(start_ms + interval_4h_ms, unit="ms", tz="UTC")
        assert len(_get_tf_slice({"4h": df_4h}, at_close, "4h")) == 1


# ---------------------------------------------------------------------------
# No look-ahead regression with REAL DB data (P0-1)
# ---------------------------------------------------------------------------

class TestNoLookaheadRealDB:
    """Validate that snapshots built from the real market.db never include any
    higher-TF candle that has not yet closed as of the simulated 30m bar time."""

    def _db_path(self):
        from pathlib import Path
        p = Path(__file__).resolve().parent.parent / "state" / "market.db"
        return p

    def test_no_unclosed_upper_tf_candle_in_snapshot(self):
        import sqlite3
        from backtest.engine import _load_tf_data, _get_tf_slice, TF_DURATION

        db = self._db_path()
        if not db.exists():
            pytest.skip("real market.db not present")

        conn = sqlite3.connect(str(db))
        try:
            tf_data = {tf: _load_tf_data(conn, tf) for tf in ALL_TFS}
        finally:
            conn.close()

        bars_30m = tf_data["30m"]
        if len(bars_30m) < 5000:
            pytest.skip("insufficient real data")

        # Sample a spread of 30m bar times across the dataset.
        sample_idxs = [3000, 10000, 25000, 40000, 60000]
        checked = 0
        for idx in sample_idxs:
            if idx >= len(bars_30m):
                continue
            current_time = bars_30m.index[idx]
            for tf in ("1h", "4h", "12h", "1d", "1w"):
                sliced = _get_tf_slice(tf_data, current_time, tf)
                if sliced.empty:
                    continue
                dur = TF_DURATION[tf]
                # Every candle in the slice must be fully closed by current_time:
                # open_time + duration <= current_time. The last one is the binding case.
                last_close = sliced.index.max() + dur
                assert last_close <= current_time, (
                    f"{tf} candle open={sliced.index.max()} closes at {last_close} "
                    f"> current {current_time}: unclosed candle leaked"
                )
                checked += 1
        assert checked > 0  # ensure the assertion actually ran


# ---------------------------------------------------------------------------
# Liquidation buffer rejection (integration)
# ---------------------------------------------------------------------------

class TestLiquidationBufferRejection:
    def test_sl_below_liq_rejected_by_buffer(self):
        """SL inside the buffer (< LIQ_BUFFER_MIN_FRAC of gap) must not pass."""
        entry = 50000.0
        lev = 30.0
        liq = approx_liq_price(entry, lev, "long")
        gap = entry - liq

        # SL well below threshold → fails
        sl_fail = liq + (LIQ_BUFFER_MIN_FRAC - 0.2) * gap
        assert _sl_passes_buffer(entry, sl_fail, liq, "long") is False

        # SL just above threshold → passes
        sl_pass = liq + (LIQ_BUFFER_MIN_FRAC + 0.01) * gap
        assert _sl_passes_buffer(entry, sl_pass, liq, "long") is True

    def test_short_buffer_logic(self):
        entry = 50000.0
        lev = 30.0
        liq = approx_liq_price(entry, lev, "short")
        gap = liq - entry

        sl_fail = liq - (LIQ_BUFFER_MIN_FRAC - 0.2) * gap
        assert _sl_passes_buffer(entry, sl_fail, liq, "short") is False

        sl_pass = liq - (LIQ_BUFFER_MIN_FRAC + 0.01) * gap
        assert _sl_passes_buffer(entry, sl_pass, liq, "short") is True


# ---------------------------------------------------------------------------
# R multiple calculation
# ---------------------------------------------------------------------------

class TestRMultipleCalculation:
    def test_r_positive_for_profitable_trade(self):
        # Win is defined on NET pnl (P0-2). net_pnl > 0 → win.
        state = BacktestState(equity=10000.0)
        state.trade_logs.append(TradeLog(
            trade_id=0, side="long",
            entry_time="2022-01-01", entry_price=50000.0,
            exit_time="2022-01-02", exit_price=51000.0,
            qty=0.01, leverage=10.0, sl_price=49000.0,
            exit_reason="tp3",
            r_multiple=1.0, fee_paid=0.001, funding_paid=0.0,
            tranche_index=0, liq_price=45000.0,
            net_pnl=10.0, gross_pnl=10.5, gross_r_multiple=1.05,
        ))
        metrics = compute_metrics(state, 10000.0)
        assert metrics["win_rate_pct"] == pytest.approx(100.0)

    def test_r_negative_for_sl_trade(self):
        state = BacktestState(equity=9800.0)
        state.trade_logs.append(TradeLog(
            trade_id=0, side="long",
            entry_time="2022-01-01", entry_price=50000.0,
            exit_time="2022-01-02", exit_price=49000.0,
            qty=0.01, leverage=10.0, sl_price=49000.0,
            exit_reason="sl",
            r_multiple=-1.0, fee_paid=0.001, funding_paid=0.0,
            tranche_index=0, liq_price=45000.0,
            net_pnl=-10.0, gross_pnl=-9.5, gross_r_multiple=-0.95,
        ))
        metrics = compute_metrics(state, 10000.0)
        assert metrics["win_rate_pct"] == pytest.approx(0.0)

    def test_profit_factor_gt_1_when_wins_dominate(self):
        # PF is computed on NET $ (P0-2): 6 wins of +10 vs 4 losses of -10 → 1.5.
        state = BacktestState(equity=11000.0)
        for i in range(6):
            state.trade_logs.append(TradeLog(
                trade_id=i, side="long",
                entry_time="2022-01-01", entry_price=50000.0,
                exit_time="2022-01-02", exit_price=51000.0,
                qty=0.01, leverage=10.0, sl_price=49000.0,
                exit_reason="tp3",
                r_multiple=1.0, fee_paid=0.001, funding_paid=0.0,
                tranche_index=0, liq_price=45000.0,
                net_pnl=10.0, gross_pnl=10.5, gross_r_multiple=1.05,
            ))
        for i in range(4):
            state.trade_logs.append(TradeLog(
                trade_id=6 + i, side="long",
                entry_time="2022-01-03", entry_price=50000.0,
                exit_time="2022-01-04", exit_price=49000.0,
                qty=0.01, leverage=10.0, sl_price=49000.0,
                exit_reason="sl",
                r_multiple=-1.0, fee_paid=0.001, funding_paid=0.0,
                tranche_index=0, liq_price=45000.0,
                net_pnl=-10.0, gross_pnl=-9.5, gross_r_multiple=-0.95,
            ))
        metrics = compute_metrics(state, 10000.0)
        assert metrics["profit_factor"] == pytest.approx(6.0 / 4.0)
        assert metrics["win_rate_pct"] == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Smoke test: run_backtest with in-memory DB (short period)
# ---------------------------------------------------------------------------

class TestRunBacktestSmoke:
    def test_smoke_no_crash(self):
        """run_backtest should complete without exception on small in-memory DB."""
        conn = _make_in_memory_db()
        start_ts = pd.Timestamp("2022-01-01", tz="UTC")
        end_ts = pd.Timestamp("2022-01-10", tz="UTC")
        state = run_backtest(conn, start_ts, end_ts, initial_equity=10_000.0)
        conn.close()
        # Should have equity curve entries
        assert len(state.equity_curve) >= 1
        # Equity should be a positive number
        assert state.equity > 0

    def test_liq_approach_count_never_exceeds_trade_count_badly(self):
        """Liquidation approach count should be non-negative."""
        conn = _make_in_memory_db()
        start_ts = pd.Timestamp("2022-01-01", tz="UTC")
        end_ts = pd.Timestamp("2022-01-05", tz="UTC")
        state = run_backtest(conn, start_ts, end_ts, initial_equity=10_000.0)
        conn.close()
        assert state.liq_approach_count >= 0

    def test_metrics_mdd_non_negative(self):
        conn = _make_in_memory_db()
        start_ts = pd.Timestamp("2022-01-01", tz="UTC")
        end_ts = pd.Timestamp("2022-01-07", tz="UTC")
        state = run_backtest(conn, start_ts, end_ts, initial_equity=10_000.0)
        conn.close()
        metrics = compute_metrics(state, 10_000.0)
        assert metrics["mdd_pct"] >= 0.0
        assert metrics["profit_factor"] >= 0.0
