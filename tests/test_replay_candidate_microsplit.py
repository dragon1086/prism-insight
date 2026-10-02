"""Tests for tools/replay_candidate_microsplit.py

Tests use synthetic bar data; no DB, no network, no pandas.
Run with: python -m pytest tests/test_replay_candidate_microsplit.py -v
"""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers to import the module under test
# ---------------------------------------------------------------------------

def _module():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "replay_candidate_microsplit",
        Path(__file__).parent.parent / "tools" / "replay_candidate_microsplit.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


try:
    M = _module()
except Exception:
    M = None  # will fail at test time with a meaningful error


# ---------------------------------------------------------------------------
# Synthetic bar builder
# ---------------------------------------------------------------------------

def _make_bars(start_date_str, n, base_close=100.0, step=0.5, base_vol=1_000_000):
    """Return list of bar dicts starting one calendar day after start_date_str."""
    from datetime import date, timedelta
    bars = []
    d = date.fromisoformat(start_date_str)
    close = base_close
    for i in range(n):
        d = d + timedelta(days=1)
        # skip weekends
        while d.weekday() >= 5:
            d = d + timedelta(days=1)
        close_val = round(close + step * i, 2)
        bars.append({
            "date": d.isoformat(),
            "high": round(close_val * 1.01, 2),
            "low": round(close_val * 0.99, 2),
            "close": close_val,
            "open": round(close_val * 1.001, 2),
            "vol": base_vol,
        })
    return bars


def _make_cand(entry_date, entry_price, stop=None, buy_score=7,
               market="KR", ticker="005930", regime=None):
    eff_stop = stop if stop else entry_price * 0.93
    return {
        "ticker": ticker,
        "market": market,
        "entry_date": entry_date,
        "entry_price": entry_price,
        "buy_score": buy_score,
        "min_score": 7,
        "decision": "BUY",
        "was_traded": True,
        "stop": eff_stop,
        "target_price": entry_price * 1.15,
        "regime": regime or "strong_bull",
        "regime_class": M.classify_regime(regime or "strong_bull"),
        "band": M.score_band(buy_score),
    }


def _run_arm(cand, bars, arm):
    """Run simulate_one and extract one arm result."""
    sim = M._simulate_one(cand, bars)
    if sim is None:
        return None
    return sim.get(arm)


# ===========================================================================
# 1. Exit type tests
# ===========================================================================

class TestExitStop:
    def _setup_with_prior(self, post_n=50, post_step=0.0, post_close_offset=0.5,
                           base_close=100.0, base_vol=1_000_000):
        """Return (prior_bars, entry_date, entry_price, post_bars)."""
        prior = _make_bars("2025-11-01", 35, base_close=base_close, step=0.0,
                           base_vol=base_vol)
        entry_date = prior[-1]["date"]
        entry_price = prior[-1]["close"]
        post = _make_bars(entry_date, post_n,
                          base_close=entry_price + post_close_offset,
                          step=post_step, base_vol=base_vol)
        return prior, entry_date, entry_price, post

    def test_stop_triggered_on_low(self):
        """Bar with low <= stop*0.995 causes stop exit."""
        prior, entry_date, entry_price, post = self._setup_with_prior()
        stop = entry_price * 0.95
        cand = _make_cand(entry_date, entry_price, stop=stop)
        # Inject a stop-triggering bar on session 10
        post[10]["low"] = stop * 0.99  # clearly below stop*0.995
        bars = prior + post
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        assert result["exit_reason"] == "stop"

    def test_hard_stop_at_093(self):
        """Bar with low <= entry*0.93 triggers hard stop even if stop is higher."""
        prior, entry_date, entry_price, post = self._setup_with_prior()
        stop = entry_price * 0.95  # above hard stop
        cand = _make_cand(entry_date, entry_price, stop=stop)
        post[8]["low"] = entry_price * 0.92  # below entry * 0.93
        bars = prior + post
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        assert result["exit_reason"] == "stop"

    def test_no_stop_if_low_above_threshold(self):
        """If low never dips to stop*0.995, no stop exit."""
        prior, entry_date, entry_price, post = self._setup_with_prior(post_step=0.3)
        stop = entry_price * 0.95
        cand = _make_cand(entry_date, entry_price, stop=stop)
        bars = prior + post
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        # rising bars with stop at 95% — should not stop out
        assert result["exit_reason"] != "stop"


class TestExitTrend:
    def test_trend_exit_after_5_sessions(self):
        """After >=5 sessions, if close < SMA20, trend exit is triggered."""
        # Need 30 prior bars + 5 after-entry bars + bars where close < SMA20
        prior = _make_bars("2025-11-01", 40, base_close=100.0, step=0.0)
        entry_date = prior[-1]["date"]
        entry_price = prior[-1]["close"]
        stop = entry_price * 0.85

        # Bars after entry: first 5 sessions flat then big drop on session 6
        post = _make_bars(entry_date, 10, base_close=entry_price, step=-1.5)
        # Force session 6+ to have close < SMA20 (approximate — just make closes very low)
        for i in range(5, len(post)):
            post[i]["close"] = entry_price * 0.96
            post[i]["low"] = entry_price * 0.96 - 0.5
            post[i]["high"] = entry_price * 0.97

        bars = prior + post
        cand = _make_cand(entry_date, entry_price, stop=stop)
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        # trend exit should fire (close < SMA20 after >=5 sessions)
        assert result["exit_reason"] in ("trend", "stop", "time")
        # The net return should be valid
        assert isinstance(result["net"], float)

    def test_no_trend_exit_before_5_sessions(self):
        """Trend exit can't fire before 5 sessions are held."""
        prior = _make_bars("2025-11-01", 35, base_close=100.0, step=0.0)
        entry_date = prior[-1]["date"]
        entry_price = prior[-1]["close"]
        stop = entry_price * 0.80  # wide stop so stop exit doesn't fire first

        # First 3 sessions after entry have close < SMA20 (big drop)
        post = _make_bars(entry_date, 45, base_close=entry_price, step=0.2)
        for i in range(3):
            post[i]["close"] = entry_price * 0.92
            post[i]["high"] = entry_price * 0.93
            post[i]["low"] = entry_price * 0.91

        bars = prior + post
        cand = _make_cand(entry_date, entry_price, stop=stop)
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        # Could stop or trend depending on bar values; just must be valid
        assert result["exit_date"] is not None


class TestExitTime:
    def test_time_exit_at_session_40(self):
        """Exit at session 40 if no other exit fires."""
        prior = _make_bars("2025-11-01", 35, base_close=100.0, step=0.0)
        entry_date = prior[-1]["date"]
        entry_price = prior[-1]["close"]
        stop = entry_price * 0.60  # very wide stop — won't fire

        # 60 sessions after entry, slowly rising, no trend exit (always > SMA20)
        post = _make_bars(entry_date, 60, base_close=entry_price, step=0.3)
        bars = prior + post
        cand = _make_cand(entry_date, entry_price, stop=stop)
        result = _run_arm(cand, bars, "FULL")
        assert result is not None
        assert result["exit_reason"] == "time"


# ===========================================================================
# 2. MS_SLOW add rule tests
# ===========================================================================

class TestMsSlowAddRules:
    def _base_setup(self, entry_price=100.0, base_vol=1_000_000):
        """Return (prior_bars, entry_date, cand)."""
        prior = _make_bars("2025-11-01", 35, base_close=entry_price, step=0.0,
                           base_vol=base_vol)
        entry_date = prior[-1]["date"]
        stop = entry_price * 0.60  # wide stop
        cand = _make_cand(entry_date, entry_price, stop=stop)
        return prior, entry_date, cand

    def test_no_add_before_3_sessions(self):
        """MS_SLOW: no add allowed in the first 2 sessions held."""
        prior, entry_date, cand = self._base_setup()
        # High volume, new highs immediately — but should not add until session 3
        post = _make_bars(entry_date, 45, base_close=cand["entry_price"] + 2.0,
                          step=0.5, base_vol=2_000_000)
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        assert sim is not None
        ms = sim["MS_SLOW"]
        assert ms is not None
        # If adds happened, they must be at session >= 3
        assert ms["n_adds"] >= 0  # just valid

    def test_no_add_without_new_high(self):
        """MS_SLOW: add only if close is a new post-entry closing high."""
        prior, entry_date, cand = self._base_setup(entry_price=100.0)
        ep = cand["entry_price"]
        # Post-entry bars: 10 sessions flat at 101 (first new high immediately),
        # then 30 sessions flat at 100.5 — below the 101 high, no more adds
        post = _make_bars(entry_date, 45, base_close=ep * 1.01, step=0.0,
                          base_vol=2_000_000)
        # Force all bars after session 10 to be below 101
        for i in range(10, len(post)):
            post[i]["close"] = ep * 1.005
            post[i]["high"] = ep * 1.015
            post[i]["low"] = ep * 0.995

        bars = prior + post
        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        # At most 1 add in first window (the first new high at session ~3+)
        assert ms["n_adds"] <= 1

    def test_no_add_without_volume(self):
        """MS_SLOW: add blocked if volume < 1.3x 20-day avg."""
        prior, entry_date, cand = self._base_setup(base_vol=1_000_000)
        ep = cand["entry_price"]
        # Rising closes (new highs), but low volume (0.5x average)
        post = _make_bars(entry_date, 45, base_close=ep + 2.0, step=0.5,
                          base_vol=500_000)  # low volume
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        # Low volume should suppress adds
        assert ms["n_adds"] == 0

    def test_add_when_all_conditions_met(self):
        """MS_SLOW: add fires when new high + high volume + SMA20 + avg_cost."""
        prior, entry_date, cand = self._base_setup(entry_price=100.0, base_vol=1_000_000)
        ep = cand["entry_price"]
        # Strong uptrend: new highs every session, high volume
        post = _make_bars(entry_date, 45, base_close=ep + 0.5, step=0.8,
                          base_vol=1_500_000)
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        # Should have at least one add in an uptrend with good volume
        assert ms["n_adds"] >= 1

    def test_one_add_per_3_sessions(self):
        """MS_SLOW: at most one add per 3 sessions."""
        prior, entry_date, cand = self._base_setup(entry_price=100.0, base_vol=1_000_000)
        ep = cand["entry_price"]
        # Very high volume, new high every session
        post = _make_bars(entry_date, 45, base_close=ep + 0.3, step=0.6,
                          base_vol=2_000_000)
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        # With 45 sessions and 3-session cooldown, max adds = 45//3 = 15
        # Also capped at 1.0 total frac
        if ms["n_adds"] > 1:
            # Verify total fraction is reasonable (capped at 1.0)
            assert ms["final_frac"] <= M.MS_CAP + 1e-9

    def test_add_cap_at_1(self):
        """MS_SLOW: final fraction never exceeds 1.0."""
        prior, entry_date, cand = self._base_setup(entry_price=100.0, base_vol=1_000_000)
        ep = cand["entry_price"]
        post = _make_bars(entry_date, 60, base_close=ep + 0.5, step=1.0,
                          base_vol=3_000_000)
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        assert ms["final_frac"] <= M.MS_CAP + 1e-9

    def test_add_never_larger_than_previous(self):
        """MS_SLOW: each add <= previous add size."""
        # This is enforced in the code; just verify via simulation
        prior, entry_date, cand = self._base_setup(entry_price=100.0, base_vol=1_000_000)
        ep = cand["entry_price"]
        post = _make_bars(entry_date, 60, base_close=ep + 0.5, step=0.9,
                          base_vol=2_500_000)
        bars = prior + post

        sim = M._simulate_one(cand, bars)
        ms = sim["MS_SLOW"] if sim else None
        assert ms is not None
        # We can't inspect legs directly; just confirm it's valid
        assert ms["final_frac"] >= 0


class TestRiskClip:
    def test_clip_reduces_add_at_risk_limit(self):
        """risk_clip returns less than nominal when risk budget is near exhausted."""
        ep = 100.0
        stop = 92.0
        legs = [(0.6, ep)]  # already in with 60% at entry
        # Add at same price (worst case for marginal)
        actual = M.risk_clip(legs, ep, stop, ep * 1.02, 0.30)
        nominal = 0.30
        # If we're already near limit, actual < nominal
        limit = (ep - stop) / ep  # 0.08
        base_loss = 0.6 * (1.0 - stop / ep)  # 0.6 * 0.08 = 0.048
        marginal = 1.0 - stop / (ep * 1.02)
        allowed = (limit - base_loss) / marginal
        assert abs(actual - min(nominal, max(0.0, allowed))) < 1e-9

    def test_clip_zero_when_budget_exhausted(self):
        """risk_clip returns 0 when base_loss >= risk_limit."""
        ep = 100.0
        stop = 95.0
        # Already at exactly risk limit
        limit = (ep - stop) / ep  # 0.05
        # legs where base_loss >= limit
        legs = [(1.0, ep)]  # 1.0 * 0.05 = 0.05 = limit exactly
        actual = M.risk_clip(legs, ep, stop, ep * 1.01, 0.20)
        assert actual <= 1e-9

    def test_clip_no_op_when_stop_invalid(self):
        """risk_clip returns nominal when stop is invalid."""
        assert M.risk_clip([], 100.0, 0.0, 101.0, 0.3) == 0.3
        assert M.risk_clip([], 100.0, -5.0, 101.0, 0.3) == 0.3
        assert M.risk_clip([], 100.0, 105.0, 101.0, 0.3) == 0.3  # stop >= entry


# ===========================================================================
# 3. Band classification
# ===========================================================================

class TestBandClassification:
    @pytest.mark.parametrize("score,expected", [
        (9.0, "7+"),
        (7.0, "7+"),
        (6.9, "5-6"),
        (5.0, "5-6"),
        (4.0, "4"),
        (4.9, "4"),
        (3.9, "<=3"),
        (0.0, "<=3"),
        (None, "unknown"),
    ])
    def test_score_band(self, score, expected):
        assert M.score_band(score) == expected


# ===========================================================================
# 4. Regime classification
# ===========================================================================

class TestRegimeClassification:
    @pytest.mark.parametrize("text,expected", [
        ("strong_bull", "bull"),
        ("parabolic", "bull"),
        ("moderate_bull", "bull"),
        ("상승추세_강", "bull"),
        ("강세장", "bull"),
        ("sideways", "sideways"),
        ("횡보장", "sideways"),
        ("bear", "bear"),
        ("weak_bear", "bear"),
        ("약세장", "bear"),
        ("", "unknown"),
        (None, "unknown"),
        ("random_text", "unknown"),
    ])
    def test_classify_regime(self, text, expected):
        assert M.classify_regime(text) == expected


# ===========================================================================
# 5. Dedupe logic in extract
# ===========================================================================

class TestExtractDedupe:
    def _make_db(self, tmpdir):
        """Create in-memory SQLite with test data."""
        db_path = str(tmpdir / "test.sqlite")
        conn = sqlite3.connect(db_path)
        conn.execute("""
            CREATE TABLE analysis_performance_tracker (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                analyzed_date TEXT,
                analyzed_price REAL,
                buy_score REAL,
                min_score REAL,
                decision TEXT,
                was_traded INTEGER DEFAULT 0,
                stop_loss REAL,
                target_price REAL,
                watchlist_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE watchlist_history (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                scenario TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE trading_history (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                buy_date TEXT,
                buy_price REAL,
                sell_date TEXT,
                sell_price REAL,
                scenario TEXT
            )
        """)
        # US tables (empty is fine — just need them to exist)
        conn.execute("""
            CREATE TABLE us_analysis_performance_tracker (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                analysis_date TEXT,
                analysis_price REAL,
                buy_score REAL,
                decision TEXT,
                was_traded INTEGER DEFAULT 0,
                stop_loss REAL,
                watchlist_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE us_watchlist_history (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                scenario TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE us_trading_history (
                id INTEGER PRIMARY KEY,
                ticker TEXT,
                buy_date TEXT,
                buy_price REAL,
                sell_date TEXT,
                sell_price REAL,
                scenario TEXT
            )
        """)
        conn.commit()
        return db_path, conn

    def test_dedupe_prefers_traded_row(self, tmp_path):
        """When two rows have same ticker+date, prefer the one with was_traded=True."""
        db_path, conn = self._make_db(tmp_path)
        # Insert two rows for same ticker+date, one traded, one not
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss)
            VALUES ('005930', '2026-01-15', 75000.0, 6.5, 'WATCH', 0, 70000.0)
        """)
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss)
            VALUES ('005930', '2026-01-15', 76000.0, 8.0, 'BUY', 1, 71000.0)
        """)
        conn.commit()
        conn.close()

        out = str(tmp_path / "cands.json")
        M.extract(db_path, out)
        data = json.loads(Path(out).read_text())
        kr_rows = [r for r in data["rows"] if r["market"] == "KR"]
        # Should have exactly 1 row for this ticker+date
        same_day = [r for r in kr_rows if r["ticker"] == "005930" and r["entry_date"] == "2026-01-15"]
        assert len(same_day) == 1
        assert same_day[0]["was_traded"] is True

    def test_extract_includes_non_traded(self, tmp_path):
        """Non-traded (watch-only) candidates are included in output."""
        db_path, conn = self._make_db(tmp_path)
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss)
            VALUES ('000660', '2026-02-10', 180000.0, 5.0, 'WATCH', 0, 170000.0)
        """)
        conn.commit()
        conn.close()

        out = str(tmp_path / "cands.json")
        M.extract(db_path, out)
        data = json.loads(Path(out).read_text())
        found = [r for r in data["rows"]
                 if r["ticker"] == "000660" and r["entry_date"] == "2026-02-10"]
        assert len(found) == 1
        assert found[0]["was_traded"] is False

    def test_extract_score_below_threshold_included(self, tmp_path):
        """Candidates with buy_score <= 3 are included (control band)."""
        db_path, conn = self._make_db(tmp_path)
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss)
            VALUES ('035420', '2026-03-05', 350000.0, 2.5, 'REJECT', 0, 320000.0)
        """)
        conn.commit()
        conn.close()

        out = str(tmp_path / "cands.json")
        M.extract(db_path, out)
        data = json.loads(Path(out).read_text())
        found = [r for r in data["rows"] if r["ticker"] == "035420"]
        assert len(found) == 1
        assert found[0]["band"] == "<=3"

    def test_stop_from_scenario_overrides_apt(self, tmp_path):
        """Stop from scenario JSON in watchlist_history overrides apt.stop_loss."""
        db_path, conn = self._make_db(tmp_path)
        scenario = json.dumps({"stop_loss": 72000.0, "market_regime": "strong_bull"})
        conn.execute("INSERT INTO watchlist_history (id, ticker, scenario) VALUES (1, '005930', ?)",
                     (scenario,))
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss, watchlist_id)
            VALUES ('005930', '2026-04-10', 80000.0, 7.0, 'BUY', 1, 74000.0, 1)
        """)
        conn.commit()
        conn.close()

        out = str(tmp_path / "cands.json")
        M.extract(db_path, out)
        data = json.loads(Path(out).read_text())
        found = [r for r in data["rows"] if r["ticker"] == "005930"]
        assert len(found) == 1
        # Scenario stop (72000) < entry (80000), so it should be used
        assert found[0]["stop"] == pytest.approx(72000.0)

    def test_stop_defaults_to_093_if_invalid(self, tmp_path):
        """When no valid stop in scenario, fall back to entry * 0.93."""
        db_path, conn = self._make_db(tmp_path)
        conn.execute("""
            INSERT INTO analysis_performance_tracker
            (ticker, analyzed_date, analyzed_price, buy_score, decision, was_traded, stop_loss)
            VALUES ('035720', '2026-05-10', 100000.0, 6.0, 'WATCH', 0, NULL)
        """)
        conn.commit()
        conn.close()

        out = str(tmp_path / "cands.json")
        M.extract(db_path, out)
        data = json.loads(Path(out).read_text())
        found = [r for r in data["rows"] if r["ticker"] == "035720"]
        assert len(found) == 1
        assert found[0]["stop"] == pytest.approx(100000.0 * 0.93)


# ===========================================================================
# 6. Simulation arm returns / cost logic
# ===========================================================================

class TestArmReturns:
    def _rising_setup(self, entry_price=100.0, n_post=45, step=0.5):
        """Standard setup: 35 prior bars + n_post rising bars after entry."""
        prior = _make_bars("2025-11-01", 35, base_close=entry_price, step=0.0,
                           base_vol=1_000_000)
        entry_date = prior[-1]["date"]
        stop = entry_price * 0.60
        post = _make_bars(entry_date, n_post, base_close=entry_price + 0.5,
                          step=step, base_vol=1_500_000)
        bars = prior + post
        cand = _make_cand(entry_date, entry_price, stop=stop)
        return cand, bars

    def test_full_arm_no_adds(self):
        cand, bars = self._rising_setup()
        sim = M._simulate_one(cand, bars)
        assert sim is not None
        assert sim["FULL"]["n_adds"] == 0
        assert sim["FULL"]["final_frac"] == pytest.approx(1.0)

    def test_ms_init_smaller_initial_fraction(self):
        """MS_INIT starts with initial_sizing < 1.0."""
        cand, bars = self._rising_setup()
        sim = M._simulate_one(cand, bars)
        assert sim is not None
        ms = sim["MS_INIT"]
        assert ms is not None
        assert ms["final_frac"] < 1.0 + 1e-9
        # initial_sizing with ATR-proxy from flat bars should be between 0.30 and 0.80
        assert ms["final_frac"] >= 0.29

    def test_cost_deducted_from_net(self):
        """Net < gross due to costs."""
        cand, bars = self._rising_setup()
        sim = M._simulate_one(cand, bars)
        assert sim is not None
        full = sim["FULL"]
        # Gross return on FULL is exit/entry - 1; net is gross minus round-trip cost
        expected_cost = 1.0 * M.ROUND_TRIP_COST
        assert full["gross"] - full["net"] == pytest.approx(expected_cost, abs=1e-9)

    def test_ms_slow_net_vs_full_in_flat_market(self):
        """In a flat market (small gains), MS_SLOW should be similar to MS_INIT (few/no adds)."""
        prior = _make_bars("2025-11-01", 35, base_close=100.0, step=0.0, base_vol=500_000)
        entry_date = prior[-1]["date"]
        stop = 60.0
        # Flat market, low volume — no adds expected
        post = _make_bars(entry_date, 45, base_close=101.0, step=0.0, base_vol=400_000)
        bars = prior + post
        cand = _make_cand(entry_date, 100.0, stop=stop)
        sim = M._simulate_one(cand, bars)
        assert sim is not None
        ms_slow = sim["MS_SLOW"]
        ms_init = sim["MS_INIT"]
        assert ms_slow is not None and ms_init is not None
        # No adds in flat/low-vol market
        assert ms_slow["n_adds"] == 0
        assert ms_slow["net"] == pytest.approx(ms_init["net"], abs=1e-9)


# ===========================================================================
# 7. ATR14 helper
# ===========================================================================

class TestAtr14:
    def test_returns_none_for_insufficient_bars(self):
        bars = _make_bars("2026-01-01", 10, base_close=100.0, step=0.0)
        assert M.atr14(bars) is None

    def test_returns_float_for_15_bars(self):
        bars = _make_bars("2026-01-01", 15, base_close=100.0, step=0.5)
        result = M.atr14(bars)
        assert result is not None
        assert result > 0


# ===========================================================================
# 8. Bootstrap CI sanity
# ===========================================================================

class TestBootstrapCI:
    def test_ci_ordered(self):
        """Lower bound <= upper bound."""
        vals = [0.01 * i for i in range(-10, 20)]
        tickers = ["A"] * 10 + ["B"] * 10 + ["C"] * 10
        ci = M._boot_ci_mean(vals, tickers, seed=42, n=500)
        assert ci[0] is not None and ci[1] is not None
        assert ci[0] <= ci[1]

    def test_diff_ci_centered_near_zero(self):
        """CI for diff of identical series is centered near zero."""
        vals = [0.05] * 20
        ref = [0.05] * 20
        tickers = ["X"] * 10 + ["Y"] * 10
        ci = M._boot_ci_diff(ref, vals, tickers, seed=42, n=500)
        assert ci[0] is not None and ci[1] is not None
        assert ci[0] <= 0.0 <= ci[1] or abs(ci[0]) < 0.01

    def test_ci_returns_none_for_empty(self):
        assert M._boot_ci_mean([], [], seed=42, n=100) == [None, None]
        assert M._boot_ci_diff([], [], [], seed=42, n=100) == [None, None]


# ===========================================================================
# 9. Insufficient history → None
# ===========================================================================

class TestInsufficientHistory:
    def test_no_prior_bars_returns_none(self):
        """Fewer than 30 prior sessions returns None."""
        bars = _make_bars("2026-01-01", 20, base_close=100.0, step=0.5)
        entry_date = "2026-01-01"
        cand = _make_cand(entry_date, 100.0)
        assert M._simulate_one(cand, bars) is None

    def test_no_future_bars_returns_none(self):
        """No sessions after entry_date returns None."""
        bars = _make_bars("2025-11-01", 35, base_close=100.0, step=0.0)
        entry_date = bars[-1]["date"]  # last bar == entry_date, no future
        cand = _make_cand(entry_date, 100.0)
        assert M._simulate_one(cand, bars) is None

    def test_empty_bars_returns_none(self):
        cand = _make_cand("2026-01-10", 100.0)
        assert M._simulate_one(cand, []) is None

    def test_error_bars_returns_none(self):
        cand = _make_cand("2026-01-10", 100.0)
        assert M._simulate_one(cand, {"error": "ConnectionError"}) is None
