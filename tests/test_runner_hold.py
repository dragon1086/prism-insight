"""Runner hold rule (R5): detection edges, guard, stop raise, persistence, KR/US parity, trend-exit skip.

Network-free: daily bars are synthetic and dated on the real XKRX/XNYS calendars.
"""
import asyncio
import json
import sqlite3
import sys
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas_market_calendars as mc
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prism_core import runner_hold as R  # noqa: E402
from prism_core import runner_hold_live as L  # noqa: E402

SEOUL, NY = ZoneInfo("Asia/Seoul"), ZoneInfo("America/New_York")
_CAL = {"KR": "XKRX", "US": "XNYS"}
_TABLE = {"KR": "stock_holdings", "US": "us_stock_holdings"}


@lru_cache(maxsize=2)
def sessions(market):
    schedule = mc.get_calendar(_CAL[market]).schedule("2026-01-02", "2026-12-30")
    return [ts.date().isoformat() for ts in schedule.index]


def build(market, path, *, entry_idx=60, base=100.0, volume=1000.0):
    """Flat ``base`` history through the entry session, then ``path`` closes (session 1, 2, ...)."""
    days = sessions(market)
    bars = [{"date": days[i], "close": base, "volume": volume} for i in range(entry_idx + 1)]
    bars += [{"date": days[entry_idx + j], "close": float(c), "volume": volume} for j, c in enumerate(path, 1)]
    return bars, days[entry_idx], days


def buy_date(market, session):
    """Server-local (KST) buy_date text whose market session is ``session``."""
    return f"{session} 10:00:00" if market == "KR" else f"{session} 23:40:00"


def at(market, day, hh=10, mm=0):
    tz = SEOUL if market == "KR" else NY
    return datetime.fromisoformat(f"{day}T{hh:02d}:{mm:02d}:00").replace(tzinfo=tz).astimezone(timezone.utc)


@pytest.fixture(autouse=True)
def runner_on(monkeypatch):
    monkeypatch.setenv("RUNNER_HOLD_ENABLED", "true")
    monkeypatch.delenv("RUNNER_HOLD_MARKETS", raising=False)
    monkeypatch.setattr("observability.events.emit_event", lambda *a, **k: None)


# ---------------------------------------------------------------- detection edges

def test_exactly_plus_20pct_close_on_session_15_is_a_runner_but_session_16_is_not():
    bars, start, _ = build("KR", [101] * 14 + [120])
    found = R.detect(bars, entry_ref=100, entry_session=start)
    assert found["status"] == R.RUNNER and found["session"] == 15 and found["gain_pct"] == 20.0
    late, start, _ = build("KR", [101] * 15 + [120])
    assert R.detect(late, entry_ref=100, entry_session=start) is None


def test_intraday_high_alone_never_makes_a_runner():
    bars, start, _ = build("KR", [101, 105, 110, 119.9])
    bars[-1]["high"] = 130.0
    assert R.detect(bars, entry_ref=100, entry_session=start) is None


def test_fast_spike_within_three_sessions_is_excluded_and_session_four_qualifies():
    spike, start, _ = build("KR", [110, 115, 120])
    found = R.detect(spike, entry_ref=100, entry_session=start)
    assert (found["status"], found["reason"], found["session"]) == (R.EXCLUDED, "SPIKE_FAST", 3)
    steady, start, _ = build("KR", [105, 110, 115, 120])
    assert R.detect(steady, entry_ref=100, entry_session=start)["status"] == R.RUNNER


def test_first_crossing_decides_a_later_qualifying_close_does_not_rescue_a_spike():
    bars, start, _ = build("KR", [110, 115, 121, 100, 105, 110, 115, 121])
    assert R.detect(bars, entry_ref=100, entry_session=start)["reason"] == "SPIKE_FAST"


def test_close_more_than_40pct_above_its_50_day_ma_is_excluded():
    extended, start, _ = build("KR", [105, 110, 115, 145])
    found = R.detect(extended, entry_ref=100, entry_session=start)
    assert found["reason"] == "EXTENDED_FROM_MA50" and found["ma50_extension"] > 1.40
    within, start, _ = build("KR", [105, 110, 115, 139])
    assert R.detect(within, entry_ref=100, entry_session=start)["ma50_extension"] <= 1.40


def test_no_50_day_ma_at_the_trigger_is_excluded_and_uncovered_entry_is_unknown():
    young, start, _ = build("KR", [105, 110, 115, 120], entry_idx=20)
    assert R.detect(young, entry_ref=100, entry_session=start)["reason"] == "MA50_UNKNOWN"
    bars, start, _ = build("KR", [105, 110, 115, 120])
    assert R.detect(bars[61:], entry_ref=100, entry_session=start) is None


def test_completed_bars_exclude_today_until_close_plus_30_minutes():
    bars, _, days = build("KR", [101, 102])
    today = bars[-1]["date"]
    assert R.completed_bars(bars, market="KR", now=at("KR", today, 15, 55))[-1]["date"] < today
    assert R.completed_bars(bars, market="KR", now=at("KR", today, 16, 0))[-1]["date"] == today


def test_us_reserved_buy_after_the_close_belongs_to_the_next_session():
    days = sessions("US")
    assert R.entry_session("US", f"{days[100]} 23:40:00").isoformat() == days[100]
    # 08:00 KST is 19:00 New York of the previous day: after that session's close.
    assert R.entry_session("US", f"{days[101]} 08:00:00").isoformat() == days[101]


def test_hold_until_is_40_exchange_sessions_after_the_entry():
    bars, start, days = build("KR", [105, 110, 115, 120])
    block = R.new_record(R.detect(bars, entry_ref=100, entry_session=start), market="KR", entry_ref=100,
                         entry_session=start, detected_at="t")
    assert block["hold_until"] == days[60 + 40]


# ---------------------------------------------------------------- exits, phase and guard

def test_runner_exit_is_close_below_ma50_without_volume_or_close_below_entry():
    bars, _, _ = build("KR", [105, 110, 115, 120, 125, 130])
    assert R.runner_exit(bars, 100)[0] is None
    below_ma, _, _ = build("KR", [105, 110, 115, 120, 125, 101.5], volume=1.0)
    code, facts = R.runner_exit(below_ma, 100)
    assert code == "MA50_CLOSE" and facts["close"] < facts["ma50"]
    breakeven, _, _ = build("KR", [105, 110, 115, 120, 125, 99])
    assert R.runner_exit(breakeven, 100)[0] == "BREAKEVEN_CLOSE"


def test_phases_hold_then_extended_where_a_close_below_ma20_is_added_as_an_exit():
    bars, start, days = build("KR", [101, 103, 106, 110, 115, 121] + [121 + i for i in range(54)])
    block = R.new_record(R.detect(bars, entry_ref=100, entry_session=start), market="KR", entry_ref=100,
                         entry_session=start, detected_at="t")
    assert R.phase(block, block["hold_until"]) == R.HOLD
    after = days[days.index(block["hold_until"]) + 1]
    assert R.phase(block, after) == R.EXTENDED
    assert R.phase(dict(block, status=R.EXCLUDED), block["hold_until"]) is None
    # Close below the 20-day MA but above the 50-day MA and the entry: hold in HOLD, exit in EXTENDED.
    dip = bars + [{"date": days[121], "close": 160.0, "volume": 1000.0}]
    assert R.runner_exit(dip, 100, R.HOLD)[0] is None
    code, facts = R.runner_exit(dip, 100, R.EXTENDED)
    assert code == "MA20_CLOSE" and facts["ma50"] < 160.0 < facts["ma20"]


@pytest.mark.parametrize("reason, code", [
    ("목표가 도달, 횡보 국면이라 전량 매도", "TARGET"),
    ("TIER3_TARGET(weak): regime=sideways target reached", "TARGET"),
    ("단기 과열(RSI 82)로 차익 실현", "OVERHEAT"),
    ("모멘텀 둔화", "MOMENTUM"),
    ("보유 기간 60일 경과 시간 점검", "TIME"),
    ("20일선 종가 이탈 + 거래량 동반", "MA20"),
    ("업종 약세로 비중 축소", "SECTOR"),
    ("TIER2_TRAIL: regime=moderate_bull peak=150 trail(-8%)=138 >= price", "TRAILING"),
])
def test_guard_blocks_every_discretionary_sell_reason(reason, code):
    assert R.sell_verdict(protected=True, exit_code=None, current_price=130, stop_loss=100, buy_price=100,
                          reason=reason) == (False, code)


def test_guard_allows_runner_exits_hard_stop_corporate_events_and_non_runners():
    common = dict(current_price=130, stop_loss=100, buy_price=100, reason="목표가 도달")
    assert R.sell_verdict(protected=True, exit_code="MA50_CLOSE", **common) == (True, "MA50_CLOSE")
    assert R.sell_verdict(protected=True, exit_code="BREAKEVEN_CLOSE", **common) == (True, "BREAKEVEN_CLOSE")
    assert R.sell_verdict(protected=True, exit_code=None, current_price=99.9, stop_loss=100, buy_price=100,
                          reason="x") == (True, "HARD_STOP")
    for reason in ("TIER0_EVENT:KIS_STATUS:51(관리종목)", "[법인이벤트] 공개매수", "[CORP_EVENT] tender offer"):
        assert R.sell_verdict(protected=True, exit_code=None, current_price=130, stop_loss=100, buy_price=100,
                              reason=reason) == (True, "CORPORATE_EVENT")
    assert R.sell_verdict(protected=False, exit_code=None, **common) == (True, "NOT_RUNNER")


def test_runner_stop_is_the_entry_raised_or_reset_from_trailing_but_never_above_the_price():
    block = {"entry_ref": 100.0}
    assert R.stop_target(block, 93, 130) == 100.0
    assert R.stop_target(block, 100, 130) is None
    assert R.stop_target(block, 110, 130) == 100.0  # AI trailing stop replaced (explicit ratchet exception)
    assert R.stop_target(block, 93, 99) is None


def test_runner_entry_reference_is_the_first_micro_split_leg():
    scenario = {"micro_split": {"contract": "micro-split-live-v1", "allocation": "0.8",
                                "legs": [{"price": "10000"}, {"price": "10500"}]}}
    assert R.entry_reference(scenario, 10050) == 10000.0
    assert R.entry_reference({}, 10050) == 10050.0


# ---------------------------------------------------------------- persistence and tracker wiring

class Agent:
    def __init__(self, market, scenario, stop):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute(f"CREATE TABLE {_TABLE[market]} (id INTEGER PRIMARY KEY, scenario TEXT, stop_loss REAL)")
        self.conn.execute(f"INSERT INTO {_TABLE[market]} VALUES (1, ?, ?)", (json.dumps(scenario), stop))
        self.conn.commit()
        self.table = _TABLE[market]

    def row(self):
        scenario, stop = self.conn.execute(f"SELECT scenario, stop_loss FROM {self.table} WHERE id=1").fetchone()
        return json.loads(scenario), stop


def _runner_case(market, path, *, price, stop=93.0, scenario=None):
    bars, start, days = build(market, path)
    scenario = dict(scenario or {"stop_loss": stop})
    agent = Agent(market, scenario, stop)
    stock = {"id": 1, "ticker": "T", "buy_price": 100.0, "buy_date": buy_date(market, start),
             "current_price": price, "stop_loss": stop, "scenario": json.dumps(scenario)}
    now = at(market, days[days.index(bars[-1]["date"]) + 1], 11, 0)
    return agent, stock, bars, now


@pytest.mark.parametrize("market", ["KR", "US"])
def test_detection_persists_block_raises_stop_once_and_guards_identically(market, monkeypatch):
    agent, stock, bars, now = _runner_case(market, [101, 103, 106, 110, 115, 121, 124], price=125.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    assert asyncio.run(L.review_holding(agent, market, stock, now=now)) is None
    scenario, stop = agent.row()
    assert scenario["runner"]["status"] == R.RUNNER and scenario["runner"]["session"] == 6
    assert stop == 100.0 and stock["stop_loss"] == 100.0 and scenario["runner"]["stop_set_to"] == 100.0
    assert json.loads(stock["scenario"])["runner"] == scenario["runner"]
    # Second review: nothing rewritten, stop unchanged.
    set_at = scenario["runner"]["stop_set_at"]
    asyncio.run(L.review_holding(agent, market, stock, now=now))
    assert agent.row()[0]["runner"]["stop_set_at"] == set_at and agent.row()[1] == 100.0
    # Discretionary sell -> hold; hard stop and corporate event pass.
    held = L.guard_result(agent, market, stock, True, "목표가 도달로 매도", logger=None)
    assert held[0] is False and held[1].startswith("[RUNNER_HOLD]")
    assert L.guard_result(agent, market, dict(stock, current_price=99.0), True, "stop", logger=None)[0]
    assert L.guard_result(agent, market, stock, True, "[CORP_EVENT] tender", logger=None)[0]
    decision = L.guard_decision(agent, market, stock, {"should_sell": True, "sell_reason": "과열",
                                                        "portfolio_adjustment": {"needed": True, "new_stop_loss": 118,
                                                                                 "new_target_price": 140}})
    assert decision["should_sell"] is False
    assert decision["portfolio_adjustment"] == {"needed": False, "new_stop_loss": None, "new_target_price": None}
    assert L.prompt_block(stock, market=market, language="en" if market == "US" else "ko").startswith(
        "\n\n### ")


@pytest.mark.parametrize("market", ["KR", "US"])
def test_close_below_ma50_forces_the_runner_exit(market, monkeypatch):
    path = [101, 103, 106, 110, 115, 121, 124] + [120 - i for i in range(10)] + [100.5]
    agent, stock, bars, now = _runner_case(market, path, price=101.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    reason = asyncio.run(L.review_holding(agent, market, stock, now=now))
    assert reason.startswith("RUNNER_MA50") and stock["_runner_ctx"]["exit"] == "MA50_CLOSE"
    from reentry_cooldown import classify_exit_kind
    assert classify_exit_kind(reason) == "trend_exit"


@pytest.mark.parametrize("market", ["KR", "US"])
def test_spike_is_recorded_as_excluded_and_keeps_the_existing_exit_logic(market, monkeypatch):
    agent, stock, bars, now = _runner_case(market, [112, 118, 125, 126], price=126.0)
    events = []
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    monkeypatch.setattr(L, "_emit", lambda event, **kw: events.append(event))
    assert asyncio.run(L.review_holding(agent, market, stock, now=now)) is None
    scenario, stop = agent.row()
    assert scenario["runner"]["status"] == R.EXCLUDED and stop == 93.0 and events == ["runner.spike_excluded"]
    assert L.guard_result(agent, market, stock, True, "목표가 도달", logger=None) == (True, "목표가 도달")
    assert L.prompt_block(stock, market=market, language="ko") == ""


@pytest.mark.parametrize("market", ["KR", "US"])
def test_ai_trailing_stop_above_the_entry_is_reset_to_the_entry_on_detection(market, monkeypatch):
    agent, stock, bars, now = _runner_case(market, [101, 103, 106, 110, 115, 121, 124], price=125.0, stop=115.0)
    events = []
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    monkeypatch.setattr(L, "_emit", lambda event, **kw: events.append((event, kw["attributes"])))
    asyncio.run(L.review_holding(agent, market, stock, now=now))
    scenario, stop = agent.row()
    assert stop == 100.0 and scenario["runner"]["stop_set_from"] == 115.0
    assert ("runner.stop_reset_to_entry", {"stop_loss": 100.0, "previous_stop_loss": 115.0,
                                           "entry_ref": 100.0}) in events


def test_concurrent_micro_split_write_is_kept_by_the_runner_write(monkeypatch):
    micro = {"contract": "micro-split-live-v1", "allocation": "0.35", "legs": [{"price": "100", "allocation": "0.35"}]}
    agent, stock, bars, now = _runner_case("KR", [101, 103, 106, 110, 115, 121, 124], price=125.0,
                                           scenario={"stop_loss": 93.0, "micro_split": micro})
    added = dict(micro, allocation="0.8")
    agent.conn.execute("UPDATE stock_holdings SET scenario=? WHERE id=1",
                       (json.dumps({"stop_loss": 93.0, "micro_split": added}),))
    agent.conn.commit()
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    asyncio.run(L.review_holding(agent, "KR", stock, now=now))
    scenario, _ = agent.row()
    assert scenario["micro_split"]["allocation"] == "0.8" and scenario["runner"]["status"] == R.RUNNER


def test_peak_ratchet_rewrite_keeps_the_runner_block():
    from prism_core.micro_split_live import keep_fresh_record
    block = {"version": R.VERSION, "status": R.RUNNER, "hold_until": "2026-12-01"}
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, scenario TEXT)")
    conn.execute("INSERT INTO stock_holdings VALUES (5, ?)", (json.dumps({"runner": block}),))
    stale = keep_fresh_record(conn.cursor(), "KR", 5, {"highest_price": 130})
    assert stale == {"highest_price": 130, "runner": block}


def test_disabled_or_isolated_runtime_leaves_everything_unchanged(monkeypatch):
    agent, stock, bars, now = _runner_case("KR", [101, 103, 106, 110, 115, 121, 124], price=125.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    monkeypatch.setenv("RUNNER_HOLD_MARKETS", "US")
    assert asyncio.run(L.review_holding(agent, "KR", stock, now=now)) is None and "runner" not in agent.row()[0]
    monkeypatch.delenv("RUNNER_HOLD_MARKETS")
    agent._no_order_effects = object()
    assert asyncio.run(L.review_holding(agent, "KR", stock, now=now)) is None and "runner" not in agent.row()[0]


def test_stored_runner_stays_protected_when_bars_are_unavailable(monkeypatch):
    block = {"version": R.VERSION, "status": R.RUNNER, "entry_ref": 100.0, "session": 6, "gain_pct": 21.0,
             "hold_until": "2026-12-01"}
    stock = {"id": 1, "ticker": "T", "buy_price": 100.0, "current_price": 130.0, "stop_loss": 100.0,
             "scenario": json.dumps({"runner": block})}
    assert L.guard_result(object(), "KR", stock, True, "목표가 도달", logger=None)[0] is False


def test_non_runner_prompt_appendix_is_empty_and_runner_lines_render():
    assert L.prompt_block({"ticker": "T", "scenario": "{}"}, market="KR", language="ko") == ""
    block = {"version": R.VERSION, "status": R.RUNNER, "session": 6, "gain_pct": 21.0, "hold_until": "2026-11-20"}
    line = R.message_line({"runner": block}, today="2026-10-05")
    assert line == "🏃 주도주 보유 규칙 적용: 매수 후 6거래일 +21.0%, 50일선 이탈 전까지 보유 (~11/20)\n"
    assert R.message_line({}, today="2026-10-05") == ""
    assert "주도주 판정" in R.sell_message_line({"runner": block})
    assert R.sell_message_line({"runner": dict(block, status=R.EXCLUDED)}) == ""


# ---------------------------------------------------------------- trend-exit loop

import tools.trend_exit_seller as lb  # noqa: E402
from test_trend_exit_seller import FakeAgent, FakeTrader, _enable, _inflight, _patch  # noqa: E402


@pytest.fixture
def trend_db(tmp_path, monkeypatch):
    db = tmp_path / "t.sqlite"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "buy_price REAL, buy_date TEXT, scenario TEXT, target_price REAL, stop_loss REAL, "
                 "highest_price REAL, account_key TEXT, account_name TEXT)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(lb, "DB_PATH", str(db))
    return str(db)


def _trend_seed(db, scenario, bars, start):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO stock_holdings VALUES (1, '005930', 'S', 100.0, ?, ?, 0, 100.0, 0, 'acc1', 'primary')",
                 (buy_date("KR", start), json.dumps(scenario)))
    conn.commit()
    conn.close()


def _stored_runner(bars, start):
    detection = R.detect(bars, entry_ref=100, entry_session=start)
    return R.new_record(detection, market="KR", entry_ref=100, entry_session=start, detected_at="t")


def test_trend_exit_skips_trailing_on_a_runner_and_acts_at_once_on_its_ma50_exit(trend_db, monkeypatch):
    bars, start, days = build("KR", [101, 103, 106, 110, 115, 121, 124, 130, 140, 150])
    now = at("KR", days[days.index(bars[-1]["date"]) + 1], 11, 0)
    monkeypatch.setattr(L, "_now", lambda: now)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    _enable(monkeypatch, live=False, confirm=2)
    _trend_seed(trend_db, {"highest_price": 150, "runner": _stored_runner(bars, start)}, bars, start)
    calls = []
    _patch(monkeypatch, FakeTrader({"005930": 130.0}, calls=calls), agent_holder=FakeAgent(calls))
    summary = asyncio.run(lb.run_market("KR", "run1"))
    assert summary["runner_held"] == 1 and summary["signaled"] == 0 and calls == []

    broken = bars + [{"date": days[days.index(bars[-1]["date"]) + 1], "close": 103.0, "volume": 1.0}]
    later = at("KR", days[days.index(broken[-1]["date"]) + 1], 11, 0)
    monkeypatch.setattr(L, "_now", lambda: later)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: broken)
    summary = asyncio.run(lb.run_market("KR", "run2"))
    assert summary["signaled"] == 1 and summary["acted"] == 1 and summary["gated"] == 0
    conn = sqlite3.connect(trend_db)
    assert conn.execute("SELECT reason FROM loop_b_inflight_orders").fetchone()[0].startswith("RUNNER_MA50")
    conn.close()


def test_trend_exit_is_unchanged_for_a_non_runner(trend_db, monkeypatch):
    bars, start, days = build("KR", [101, 103, 106, 110, 115, 121, 124, 130, 140, 150])
    now = at("KR", days[days.index(bars[-1]["date"]) + 1], 11, 0)
    monkeypatch.setattr(L, "_now", lambda: now)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    _enable(monkeypatch, live=False, confirm=1)
    _trend_seed(trend_db, {"highest_price": 150, "runner": dict(_stored_runner(bars, start), status=R.EXCLUDED)},
                bars, start)
    calls = []
    _patch(monkeypatch, FakeTrader({"005930": 130.0}, calls=calls), agent_holder=FakeAgent(calls))
    summary = asyncio.run(lb.run_market("KR", "run1"))
    assert summary["runner_held"] == 0 and summary["signaled"] == 1 and _inflight(trend_db, "SHADOW") == 1
