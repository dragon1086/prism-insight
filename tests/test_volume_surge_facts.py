"""Momentum signal 1 must see today's volume, as a lower bound.

2026-09-28 US afternoon batch (14:30 ET): MDB had traded 15.06M shares against
a 20-session average of ~2.03M (7.4x), AMC 60.24M against ~27.0M (2.23x). Both
BUY messages said the 20-day ratio was "at most 0.67x / 0.86x", because the
agent was told never to compare an unfinished bar with full sessions and so
only quoted 09-23..09-25. KR morning/afternoon batches did the same.

Volume only accumulates, so an unfinished bar already at 200% proves the
condition; below 200% it is undetermined, never "weak". Decisions for MDB/AMC
were unaffected (trend gate / fundamentals), but the condition was dead.
"""

import importlib.util
import os
from datetime import date, datetime, time, timedelta
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism_core.volume_surge_facts import (  # noqa: E402
    compute_volume_surge_facts, render_volume_surge_facts,
)

US_CLOSE = time(16, 0)
KR_CLOSE = time(15, 30)


def _sessions(n, end):
    days, d = [], end
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return list(reversed(days))


def _mdb_like(today_volume):
    """23 completed sessions ending 09-25 (flat 2.03M, then MDB's last three) + today."""
    days = _sessions(24, date(2026, 9, 28))
    vols = [2_030_000] * 20 + [1_192_100, 1_204_600, 1_381_100, today_volume]
    return days, vols


def test_mdb_partial_surge_counts_as_met_lower_bound():
    days, vols = _mdb_like(15_062_229)
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 28, 14, 31), session_close=US_CLOSE)

    part = facts["partial_session"]
    assert part["date"] == "2026-09-28" and part["status"] == "met_lower_bound"
    assert round(part["ratio"], 2) == 7.87
    assert facts["signal1"] == "met"
    # The completed sessions are still reported (newest first), not replaced.
    assert [s["date"] for s in facts["confirmed_sessions"]] == ["2026-09-25", "2026-09-24", "2026-09-23"]
    assert all(s["status"] == "not_met" for s in facts["confirmed_sessions"])

    text = render_volume_surge_facts(facts)
    assert "7.87배" in text and "충족 확정" in text and "신호 1 판정: 충족" in text
    assert "2026-09-25" in text  # completed sessions remain visible


def test_partial_below_threshold_is_undetermined_not_weak():
    """Counterexample: 10:15 with 1.2x must neither count nor read as weak volume."""
    days, vols = _mdb_like(2_436_000)
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 28, 10, 15), session_close=US_CLOSE)

    assert facts["partial_session"]["status"] == "pending"
    assert facts["signal1"] == "undetermined"
    text = render_volume_surge_facts(facts)
    assert "충족으로 세지 않되" in text and "부진 근거로도 쓰지" in text
    assert "충족 확정" not in text


def test_after_close_today_is_a_completed_session():
    days, vols = _mdb_like(1_500_000)
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 28, 16, 5), session_close=US_CLOSE)
    assert facts["partial_session"] is None
    assert facts["confirmed_sessions"][0]["date"] == "2026-09-28"
    assert facts["signal1"] == "not_met"


def test_completed_surge_within_three_sessions_still_counts():
    days = _sessions(24, date(2026, 9, 29))
    vols = [1_000_000] * 20 + [2_500_000, 900_000, 800_000, 300_000]
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 29, 9, 45), session_close=KR_CLOSE)
    assert facts["partial_session"]["status"] == "pending"
    assert facts["confirmed_sessions"][-1]["status"] == "met"
    assert facts["signal1"] == "met"


def test_each_ratio_uses_only_the_20_sessions_before_its_bar():
    """The surge day itself must not dilute the next day's average window."""
    days = _sessions(23, date(2026, 9, 29))
    vols = [1_000_000] * 20 + [3_000_000, 1_000_000, 500_000]
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 29, 20, 0), session_close=KR_CLOSE)
    newest, middle, oldest = facts["confirmed_sessions"]
    assert oldest["ratio"] == pytest.approx(3.0)
    assert middle["average"] == pytest.approx(1_100_000)
    assert newest["average"] == pytest.approx((18 * 1_000_000 + 3_000_000 + 1_000_000) / 20)


@pytest.mark.parametrize("today", [float("nan"), None, -5])
def test_missing_today_volume_is_unknown(today):
    days, vols = _mdb_like(today)
    facts = compute_volume_surge_facts(days, vols, now_local=datetime(2026, 9, 28, 14, 31), session_close=US_CLOSE)
    assert facts["partial_session"]["status"] == "missing"
    assert facts["signal1"] == "undetermined"
    assert "계산 불가" in render_volume_surge_facts(facts)


def test_short_history_is_undetermined():
    days = _sessions(12, date(2026, 9, 28))
    facts = compute_volume_surge_facts(days, [1_000_000] * 12, now_local=datetime(2026, 9, 28, 14, 31), session_close=US_CLOSE)
    assert facts["signal1"] == "undetermined"
    assert all(s["ratio"] is None for s in facts["confirmed_sessions"])


# ------------------------------------------------------------------ prompts

def _factories(market):
    path = ROOT / ("prism-us" if market == "US" else "") / "cores/agents/trading_agents.py"
    spec = importlib.util.spec_from_file_location("volume_surge_" + market, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    prefix = "create_us_" if market == "US" else "create_"
    return getattr(module, prefix + "trading_scenario_agent"), getattr(module, prefix + "sell_decision_agent")


@pytest.mark.parametrize("market", ["KR", "US"])
@pytest.mark.parametrize("language", ["ko", "en"])
def test_buy_prompt_accepts_the_lower_bound_and_sell_is_unchanged(market, language):
    buy_factory, sell_factory = _factories(market)
    buy = buy_factory(language=language).instruction
    sell = sell_factory(language=language).instruction
    marker = ("예외(하한 판정)", "거래량은 마감까지 줄지 않으므로", "당일 대량 거래를 생략하지") if language == "ko" else (
        "Exception (lower bound)", "it cannot fall before the close", "omitting today's heavy volume")
    assert all(m in buy for m in marker)
    assert buy.count(marker[0]) == 1
    assert marker[0] not in sell
    # The existing unfinished-bar and no-new-threshold rules stay in place.
    assert ("이력 부족·미완성봉은 미확정" if language == "ko" else "insufficient history or unfinished bars unknown") in buy
    assert ("20일 평균 대비 200%" if language == "ko" else "200% of 20-day average") in buy


# ------------------------------------------------------ real producers (KR/US)

RUN = r'''
from contextlib import ExitStack
from datetime import datetime as _dt
from pathlib import Path
import socket
import sys
from types import SimpleNamespace
from unittest.mock import patch

root, market, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
sys.path[:0] = ([str(root / "prism-us"), str(root)] if market == "US" else [str(root)])

def deny(*args, **kwargs):
    raise AssertionError("Network forbidden")

class FrozenNow(_dt):
    """Batch clock: KR 09:45 KST / US 14:31 ET, i.e. today's bar is open."""
    @classmethod
    def now(cls, tz=None):
        base = _dt(2026, 9, 28, 9, 45) if market == "KR" else _dt(2026, 9, 28, 14, 31)
        return base.replace(tzinfo=tz) if tz else base

with ExitStack() as stack:
    stack.enter_context(patch.object(socket.socket, "connect", deny))
    stack.enter_context(patch.object(socket, "create_connection", deny))
    stack.enter_context(patch("dotenv.load_dotenv", return_value=False))
    import pandas as pd
    import cores  # pin the market's own cores namespace before the agent import
    closes = [100. + i * .1 for i in range(260)]
    volumes = [2_030_000] * 257 + [1_192_100, 1_204_600, 1_381_100]
    index = pd.bdate_range(end="2026-09-25", periods=260)
    # Today's unfinished bar, as the providers return it mid-session.
    closes.append(95.0)
    volumes.append(15_062_229)
    index = index.append(pd.DatetimeIndex(["2026-09-28"]))
    frame = pd.DataFrame({"Open": closes, "High": [c * 1.02 for c in closes],
                          "Low": [c * .98 for c in closes], "Close": closes, "Volume": volumes}, index=index)
    if market == "KR":
        import stock_tracking_agent as tracking
        from cores import stock_chart, regime_policy
        stack.enter_context(patch.object(stock_chart, "get_market_ohlcv_by_date", lambda *a, **k: frame.copy()))
        stack.enter_context(patch.object(stock_chart, "get_index_ohlcv_by_date", lambda *a, **k: frame.copy()))
        stack.enter_context(patch.object(stock_chart, "_detect_index_ticker", return_value="1001"))
        stack.enter_context(patch.object(regime_policy, "get_market_pulse_detail", return_value=None))
        agent, ticker = object.__new__(tracking.StockTrackingAgent), "005930"
    else:
        import us_stock_tracking_agent as tracking
        from cores import us_data_client
        us_frame = frame.rename(columns=str.lower)
        us_frame.index = us_frame.index.tz_localize("America/New_York")  # yfinance shape
        stack.enter_context(patch.object(us_data_client, "get_us_data_client", return_value=SimpleNamespace(
            get_ohlcv=lambda *a, **k: us_frame.copy(), get_index_data=lambda *a, **k: us_frame.copy())))
        original_import = tracking._import_from_main_cores
        regime_policy = original_import("prism_root_regime_policy", "cores/regime_policy.py")
        stack.enter_context(patch.object(regime_policy, "get_market_pulse_detail", return_value=None))
        stack.enter_context(patch.object(tracking, "_import_from_main_cores",
            lambda name, path: regime_policy if path == "cores/regime_policy.py" else original_import(name, path)))
        agent, ticker = object.__new__(tracking.USStockTrackingAgent), "MDB"
    stack.enter_context(patch.object(tracking, "datetime", FrozenNow))
    agent.trigger_mode = mode
    facts = agent._get_trend_facts(ticker)
    prev_line = "오전 누적 거래량 ≥ 전일 거래량: 예 (당일 2026-09-28"
    assert (prev_line in facts) == (mode == "morning"), facts
    assert "2026-09-28 장중 누적(미완성봉" in facts, facts
    assert "7.87배" in facts and "충족 확정" in facts, facts
    assert "2026-09-25 0.71배" in facts, facts
    assert "신호 1 판정: 충족" in facts, facts
    assert "T1_hit" in facts and "T2_hit" in facts, facts
    # 52-week high from completed sessions only: today's 95.0 bar is excluded.
    assert "52주 확정 최고가(당일 봉 제외, 확정 250거래일)" in facts, facts
    print(market + " producer volume facts ok")
'''


@pytest.mark.parametrize("mode", ["morning", "afternoon"])
@pytest.mark.parametrize("market", ["KR", "US"])
def test_actual_trend_facts_producer_carries_the_partial_surge(market, mode, tmp_path):
    env = dict(os.environ, PRISM_DISABLE_SIGNAL_PUBLISH="1", TREND_RESEARCH_CAPTURE_ENABLED="false",
               PRISM_OBSERVABILITY_SPOOL=str(tmp_path / "events.jsonl"), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-c", RUN, str(ROOT), market, mode], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-5000:]
    assert f"{market} producer volume facts ok" in result.stdout


# ------------------------------------------------- morning prev-day reference fact

def test_prev_day_reference_fact_is_reference_only():
    from prism_core.volume_surge_facts import render_prev_day_volume_fact

    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(25)]
    now = datetime(2026, 9, 25, 9, 45)
    reached = compute_volume_surge_facts(days, [100] * 23 + [400, 450], now_local=now, session_close=time(15, 30))
    line = render_prev_day_volume_fact(reached)
    assert "오전 누적 거래량 ≥ 전일 거래량: 예" in line and "450주" in line and "전일 2026-09-24 400주" in line
    assert "신호 1 충족으로 세지 않는" in line
    assert reached["signal1"] == "met"  # unchanged by the reference line (400 >= 2x avg)
    below = compute_volume_surge_facts(days, [100] * 24 + [90], now_local=now, session_close=time(15, 30))
    assert ": 아니오 (" in render_prev_day_volume_fact(below)
    missing = compute_volume_surge_facts(days, [100] * 23 + [None, 90], now_local=now, session_close=time(15, 30))
    assert "결측" in render_prev_day_volume_fact(missing)
    closed = compute_volume_surge_facts(days, [100] * 25, now_local=datetime(2026, 9, 25, 16, 0),
                                        session_close=time(15, 30))
    assert render_prev_day_volume_fact(closed) == ""  # no open bar -> no line
