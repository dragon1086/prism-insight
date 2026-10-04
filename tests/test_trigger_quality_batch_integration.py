"""Real KR/US trigger batch -> JSON with the trigger-quality priority.

Only market-data, clock and network boundaries are mocked; trigger detection,
hybrid scoring, final selection and JSON export are the production modules.
History comes from a temporary SQLite file shaped like the production tables.
Network is disabled and nothing is sent or ordered.
"""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

KR_RUN = r'''
import json, socket, sys
from unittest.mock import patch
import numpy as np, pandas as pd
import trigger_batch as batch
from cores import market_data

mode, output = sys.argv[1:3]
tickers = [f"{i:06d}" for i in range(100001, 100013)]
n = len(tickers)
rng = np.random.default_rng(7)
close_prev = np.full(n, 10000.)
chg = np.linspace(0.02, 0.13, n)
rng.shuffle(chg)
close = close_prev * (1 + chg)
openp = close_prev * (1 + rng.uniform(0.0, 0.06, n))
high = np.maximum(close, openp) * (1 + rng.uniform(0.0, 0.03, n))
low = np.minimum(close, openp) * (1 - rng.uniform(0.0, 0.02, n))
vol = rng.integers(2_000_000, 9_000_000, n)
snapshot = pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close,
                         "Volume": vol, "Amount": close * vol}, index=tickers)
previous = pd.DataFrame({"Open": close_prev, "High": close_prev * 1.01, "Low": close_prev * 0.99,
                         "Close": close_prev}, index=tickers)
previous["Volume"] = (vol / rng.uniform(1.5, 6.0, n)).astype(int)
previous["Amount"] = previous["Close"] * previous["Volume"]
cap = pd.DataFrame({"시가총액": rng.uniform(6e11, 5e12, n)}, index=tickers)
bundle = batch.MarketSnapshotBundle(snapshot, previous, cap, "20260915", "KIS_FIXTURE")

def history(start, end, ticker, **kwargs):
    i = tickers.index(ticker)
    c = np.linspace(7000. + 150 * i, 10000. + 40 * i, 260)
    return pd.DataFrame({"Open": c - 10, "High": c + 20, "Low": c - 20, "Close": c,
                         "Volume": 2000000, "Amount": c * 2000000},
                        index=pd.bdate_range(end="2026-09-15", periods=260))

def no_network(*a, **k):
    raise AssertionError("Unexpected network/order/LLM in isolated KR batch")

macro = {"market_regime": "sideways", "leading_sectors": [], "sector_map": {}}
with patch.object(socket.socket, "connect", no_network), \
        patch.object(socket, "create_connection", no_network), \
        patch.object(batch, "_resolve_trade_date", return_value="20260916"), \
        patch.object(batch, "load_market_snapshot_bundle", return_value=bundle), \
        patch.object(batch, "_get_ticker_name_map", return_value={s: s for s in tickers}), \
        patch.object(market_data, "get_market_ohlcv_by_date", side_effect=history):
    result = batch.run_batch(mode, "INFO", output, macro_context=macro)
    assert result, "fixture must reach final selection"
'''

US_RUN = r'''
import json, socket, sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import numpy as np, pandas as pd

sys.path.insert(0, str(Path.cwd() / "prism-us"))
import us_trigger_batch as batch
from cores import us_surge_detector as provider
from prism_core import market_intelligence

class FixedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        value = datetime(2026, 9, 14, 15, 0, tzinfo=timezone.utc)
        return value.astimezone(tz) if tz else value.replace(tzinfo=None)

mode, output = sys.argv[1:3]
tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "LLL"]
n = len(tickers)
rng = np.random.default_rng(11)
close_prev = np.full(n, 100.)
chg = np.linspace(0.02, 0.13, n)
rng.shuffle(chg)
close = close_prev * (1 + chg)
openp = close_prev * (1 + rng.uniform(0.0, 0.06, n))
high = np.maximum(close, openp) * (1 + rng.uniform(0.0, 0.03, n))
low = np.minimum(close, openp) * (1 - rng.uniform(0.0, 0.02, n))
vol = rng.integers(4_000_000, 9_000_000, n)
snapshot = pd.DataFrame({"Open": openp, "High": high, "Low": low, "Close": close,
                         "Volume": vol, "Amount": close * vol}, index=tickers)
previous = pd.DataFrame({"Open": close_prev, "High": close_prev * 1.01, "Low": close_prev * 0.99,
                         "Close": close_prev}, index=tickers)
previous["Volume"] = (vol / rng.uniform(1.5, 6.0, n)).astype(int)
previous["Amount"] = previous["Close"] * previous["Volume"]

def download(ticker, **kwargs):
    i = tickers.index(ticker)
    c = np.linspace(70. + 1.5 * i, 100. + 0.4 * i, 260)
    return pd.DataFrame({"Open": c - 1, "High": c + 2, "Low": c - 2, "Close": c,
                         "Volume": np.full(260, 2_000_000)},
                        index=pd.bdate_range(end="2026-09-14", periods=260))

def no_network(*a, **k):
    raise AssertionError("Unexpected real network access in US batch")

macro = {"market_regime": "sideways", "leading_sectors": []}
with patch.object(socket.socket, "connect", no_network), \
        patch.object(socket, "create_connection", no_network), \
        patch.object(market_intelligence, "datetime", FixedClock), \
        patch.object(batch, "get_major_tickers", return_value=tickers), \
        patch.object(batch, "get_snapshot", return_value=snapshot), \
        patch.object(batch, "get_previous_snapshot", return_value=(previous, "20260911")), \
        patch.object(batch, "get_market_cap_df", return_value=pd.DataFrame(
            {"MarketCap": np.full(n, 5e10)}, index=tickers)), \
        patch.object(provider.yf, "download", side_effect=download), \
        patch.object(provider.yf, "Ticker", side_effect=lambda t: SimpleNamespace(
            info={"shortName": t, "sector": "Technology", "priceToBook": 2.0},
            fast_info={"marketCap": 5e10})):
    result = batch.run_batch(mode, "INFO", output, macro_context=macro, override_date="20260914")
    assert result, "fixture must reach final selection"
'''

KR_TRIGGERS = {
    "strong": ["일중 상승률 상위주", "갭 상승 모멘텀 상위주"],
    "weak": ["마감 강도 상위주", "거래량 급증 상위주", "시총 대비 집중 자금 유입 상위주", "거래량 증가 상위 횡보주"],
}
US_TRIGGERS = {
    "strong": ["Intraday Rise Top", "Gap Up Momentum Top"],
    "weak": ["Closing Strength Top", "Volume Surge Top", "Macro Sector Leader", "Volume Surge Sideways"],
}


def _history_db(path: Path, market: str) -> Path:
    """Strong triggers find runners and make money; weak ones do neither."""
    names = KR_TRIGGERS if market == "KR" else US_TRIGGERS
    connection = sqlite3.connect(path)
    if market == "KR":
        connection.executescript("""
            CREATE TABLE analysis_performance_tracker (id INTEGER PRIMARY KEY, ticker TEXT,
              trigger_type TEXT, analyzed_date TEXT, tracked_7d_return REAL,
              tracked_14d_return REAL, tracked_30d_return REAL);
            CREATE TABLE trading_history (id INTEGER PRIMARY KEY, ticker TEXT, trigger_type TEXT,
              sell_date TEXT, profit_rate REAL, scenario TEXT);
        """)
        candidate_sql = ("INSERT INTO analysis_performance_tracker (ticker, trigger_type, analyzed_date, "
                         "tracked_7d_return, tracked_14d_return, tracked_30d_return) VALUES ('X', ?, ?, ?, ?, ?)")
        trade_sql = ("INSERT INTO trading_history (ticker, trigger_type, sell_date, profit_rate, scenario) "
                     "VALUES ('X', ?, ?, ?, NULL)")
        day = "2026-07-01 10:00:00"
    else:
        connection.executescript("""
            CREATE TABLE us_analysis_performance_tracker (id INTEGER PRIMARY KEY, ticker TEXT,
              trigger_type TEXT, analysis_date TEXT, return_7d REAL, return_14d REAL, return_30d REAL);
            CREATE TABLE us_trading_history (id INTEGER PRIMARY KEY, ticker TEXT, trigger_type TEXT,
              sell_date TEXT, profit_rate REAL, scenario TEXT);
        """)
        candidate_sql = ("INSERT INTO us_analysis_performance_tracker (ticker, trigger_type, analysis_date, "
                         "return_7d, return_14d, return_30d) VALUES ('X', ?, ?, ?, ?, ?)")
        trade_sql = ("INSERT INTO us_trading_history (ticker, trigger_type, sell_date, profit_rate, scenario) "
                     "VALUES ('X', ?, ?, ?, NULL)")
        day = "2026-07-01 03:00:00"
    for name in names["strong"]:
        connection.executemany(candidate_sql, [(name, day, 0.05, 0.12, 0.35 if i % 2 else 0.02)
                                               for i in range(60)])
        connection.executemany(trade_sql, [(name, day, 6.0 if i % 2 else -2.0) for i in range(30)])
    for name in names["weak"]:
        connection.executemany(candidate_sql, [(name, day, 0.0, -0.02, -0.05) for _ in range(60)])
        connection.executemany(trade_sql, [(name, day, -4.0) for _ in range(30)])
    connection.commit()
    connection.close()
    return path


def _run(tmp_path, market, mode, case):
    output = tmp_path / f"{market}-{mode}-{case}.json"
    db = tmp_path / f"{market}-history.sqlite"
    if case != "no_db" and not db.exists():
        _history_db(db, market)
    env = dict(os.environ, PYTHONHASHSEED="0", PRISM_DISABLE_SIGNAL_PUBLISH="1",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"),
               REGIME_WEAK_NO_TOPDOWN="true", REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED="false",
               US_SCREENING_UNIVERSE="major_indices", US_SCREENING_QUALITY_CAPTURE_ENABLED="false",
               TRIGGER_QUALITY_DB=str(db if case != "no_db" else tmp_path / "missing.sqlite"),
               TRIGGER_QUALITY_PRIORITY="false" if case == "disabled" else "true")
    script = KR_RUN if market == "KR" else US_RUN
    result = subprocess.run([sys.executable, "-c", script, mode, str(output)], cwd=ROOT, env=env,
                            capture_output=True, text=True, timeout=90, check=False)
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-4000:]
    payload = json.loads(output.read_text())
    payload["metadata"].pop("run_time")
    return payload, result.stdout + result.stderr


def _selected(payload):
    return sorted((stock.get("code") or stock.get("ticker"), trigger)
                  for trigger, rows in payload.items() if trigger != "metadata" for stock in rows)


def _without_quality(payload):
    trimmed = json.loads(json.dumps(payload))
    trimmed["metadata"].pop("trigger_quality")
    for trigger, rows in trimmed.items():
        if trigger != "metadata":
            for stock in rows:
                assert stock.pop("trigger_quality_weight") == 1.0
    return trimmed


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("mode", ["morning", "afternoon"])
def test_real_batch_priority_reorders_only_when_enabled(tmp_path, market, mode):
    disabled, _ = _run(tmp_path, market, mode, "disabled")
    no_db, _ = _run(tmp_path, market, mode, "no_db")
    enabled, log = _run(tmp_path, market, mode, "enabled")

    # Kill switch and fail-open both reproduce the legacy selection exactly.
    assert disabled["metadata"]["trigger_quality"]["status"] == "disabled"
    assert no_db["metadata"]["trigger_quality"]["status"] == "unavailable"
    assert _without_quality(disabled) == _without_quality(no_db)

    quality = enabled["metadata"]["trigger_quality"]
    assert quality["status"] == "ok"
    names = KR_TRIGGERS if market == "KR" else US_TRIGGERS
    assert all(quality["weights"][name] > 1.0 for name in names["strong"])
    assert all(quality["weights"][name] <= 0.95 for name in names["weak"])
    assert "[TRIGGER_QUALITY] trigger=" in log and " weight=" in log and " n=" in log

    for trigger, rows in enabled.items():
        if trigger != "metadata":
            assert all(stock["trigger_quality_weight"] == quality["weights"].get(trigger, 1.0)
                       for stock in rows)
    legacy, weighted = _selected(disabled), _selected(enabled)
    assert len(weighted) == len(legacy)  # same slot budget, never fewer picks
    legacy_weak = sum(trigger in names["weak"] for _, trigger in legacy)
    weighted_weak = sum(trigger in names["weak"] for _, trigger in weighted)
    assert weighted_weak < legacy_weak
    assert (legacy, weighted) == EXPECTED[(market, mode)]

    # Evidence for the two-week review: the legacy pick that lost its slot, with a reference price.
    selection = quality["selection"]
    assert sorted((c["ticker"], c["trigger"]) for c in selection["displaced"]) == sorted(set(legacy) - set(weighted))
    assert all(c["reference_price"] for c in selection["displaced"] + selection["fill_picks"])
    assert "selection" not in disabled["metadata"]["trigger_quality"]
    assert "[TRIGGER_QUALITY] displaced ticker=" in log
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    recorded = [e for e in events if e["event_type"] == "trigger_quality.selection" and e["market"] == market]
    assert recorded and recorded[-1]["attributes"]["trigger_mode"] == mode


# Legacy: the weak trigger takes the second slot through the per-trigger
# guarantee. Weighted: it loses only that guarantee and the strong trigger's
# runner-up wins the weighted fill.
EXPECTED = {
    ("KR", "morning"): ([("100001", "거래량 급증 상위주"), ("100003", "갭 상승 모멘텀 상위주")],
                        [("100002", "갭 상승 모멘텀 상위주"), ("100003", "갭 상승 모멘텀 상위주")]),
    ("KR", "afternoon"): ([("100002", "마감 강도 상위주"), ("100003", "일중 상승률 상위주")],
                          [("100003", "일중 상승률 상위주"), ("100012", "일중 상승률 상위주")]),
    ("US", "morning"): ([("DDD", "Volume Surge Top"), ("III", "Gap Up Momentum Top")],
                        [("CCC", "Gap Up Momentum Top"), ("III", "Gap Up Momentum Top")]),
    ("US", "afternoon"): ([("BBB", "Intraday Rise Top"), ("CCC", "Closing Strength Top")],
                          [("BBB", "Intraday Rise Top"), ("III", "Intraday Rise Top")]),
}
