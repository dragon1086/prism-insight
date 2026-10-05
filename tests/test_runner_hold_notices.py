"""Runner hold channel notices (2026-10-05): detection, stop moved to the entry, primary-account dedupe.

Network-free: reuses the synthetic calendar bars of tests/test_runner_hold.py.
"""
import asyncio
import json

import pytest

from test_runner_hold import Agent, L, R, _runner_case, runner_on  # noqa: F401 - autouse fixture


class NoticeAgent(Agent):
    """Tracker stand-in with a Telegram queue and a primary + secondary account."""

    def __init__(self, market, scenario, stop):
        super().__init__(market, scenario, stop)
        self.message_queue, self._msg_types = [], []
        self.account_configs = [{"account_key": "primary"}, {"account_key": "secondary"}]


RUNNER_PATH = [101, 103, 106, 110, 115, 121, 124]


def _notice_case(market, path, *, price, account="primary", stop=93.0):
    _, stock, bars, now = _runner_case(market, path, price=price, stop=stop)
    agent = NoticeAgent(market, json.loads(stock["scenario"]), stop)
    stock.update(company_name="Leader", account_key=account)
    return agent, stock, bars, now


@pytest.mark.parametrize("market", ["KR", "US"])
def test_runner_detection_sends_one_notice_with_the_stop_move_on_the_primary_account(market, monkeypatch):
    agent, stock, bars, now = _notice_case(market, RUNNER_PATH, price=125.0, stop=110.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    assert asyncio.run(L.review_holding(agent, market, stock, now=now)) is None
    assert len(agent.message_queue) == 1 and agent._msg_types == ["analysis"]
    text = agent.message_queue[0]
    # Korean for both markets; only the currency differs.
    stop = "손절가: 110원 → 매수가 100원" if market == "KR" else "손절가: $110.00 → 매수가 $100.00"
    assert text.startswith("🏃 주도주 전환: Leader(T)\n")
    assert "매수 후 6거래일째 종가가 매수가 대비 +21.0%로 주도주로 판정했습니다." in text
    assert stop in text and "또는 매수가 아래로 마감하기 전까지 보유합니다 (~" in text
    assert "목표가 도달·과열·단기 추세 이탈로는 팔지 않습니다." in text
    for jargon in ("R5", "RUNNER", "runner-hold", "L97", "SHADOW"):
        assert jargon not in text
    # Second review: nothing changes, so nothing is announced again.
    asyncio.run(L.review_holding(agent, market, stock, now=now))
    assert len(agent.message_queue) == 1


@pytest.mark.parametrize("market", ["KR", "US"])
def test_secondary_account_runner_is_not_announced_twice(market, monkeypatch):
    agent, stock, bars, now = _notice_case(market, RUNNER_PATH, price=125.0, account="secondary")
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    asyncio.run(L.review_holding(agent, market, stock, now=now))
    assert agent.message_queue == [] and agent.row()[0]["runner"]["status"] == R.RUNNER


def test_runner_found_below_the_entry_defers_the_stop_and_announces_the_later_move(monkeypatch):
    agent, stock, bars, now = _notice_case("KR", RUNNER_PATH, price=99.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    asyncio.run(L.review_holding(agent, "KR", stock, now=now))
    assert "손절가: 현재가가 매수가 위로 올라오면 매수가로 옮깁니다" in agent.message_queue[0]
    assert agent.row()[1] == 93.0
    stock["current_price"] = 126.0
    asyncio.run(L.review_holding(agent, "KR", stock, now=now))
    assert len(agent.message_queue) == 2
    assert agent.message_queue[1].startswith("🏃 주도주 손절가 조정: Leader(T)\n손절가: 93원 → 매수가 100원\n")


def test_forced_exit_review_sends_only_the_sell_message(monkeypatch):
    path = RUNNER_PATH + [120 - i for i in range(10)] + [100.5]
    agent, stock, bars, now = _notice_case("KR", path, price=101.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)
    assert asyncio.run(L.review_holding(agent, "KR", stock, now=now)).startswith("RUNNER_MA50")
    assert agent.message_queue == []


def test_agent_without_a_queue_and_notice_failures_never_block_the_review(monkeypatch):
    agent, stock, bars, now = _notice_case("KR", RUNNER_PATH, price=125.0)
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars)

    def broken(*args, **kwargs):
        raise ValueError("format")

    monkeypatch.setattr(R, "notice", broken)
    assert asyncio.run(L.review_holding(agent, "KR", stock, now=now)) is None
    assert agent.row()[1] == 100.0 and agent.message_queue == []
    plain, stock2, bars2, now2 = _runner_case("US", RUNNER_PATH, price=125.0)  # no message_queue attribute
    monkeypatch.setattr(L, "fetch_daily_bars", lambda m, t, now=None: bars2)
    assert asyncio.run(L.review_holding(plain, "US", stock2, now=now2)) is None


def test_extended_phase_notice_and_public_sell_reason():
    block = {"status": R.RUNNER, "session": 7, "gain_pct": 22.0, "hold_until": "2026-10-01", "entry_ref": 100.0}
    text = R.notice(block, market="KR", company_name="A", ticker="1", today="2026-10-05", detected=False,
                    stop_change=(93.0, 100.0), ma50=95.0)
    assert "보유 기한이 지나 20일선, 50일선(95원) 또는 매수가 아래로 마감하면 매도합니다." in text
    us = R.notice(block, market="US", company_name="A", ticker="A", today="2026-09-01", detected=True, ma50=None)
    assert "50일선 또는 매수가 아래로 마감하기 전까지 보유합니다 (~10/01)." in us
    assert R.public_reason("RUNNER_MA50: 주도주 보유 규칙 매도 — 종가") == "주도주 보유 규칙 매도 — 종가"
    assert R.public_reason("TIER1_STOPLOSS: x") == "TIER1_STOPLOSS: x" and R.public_reason(None) == ""
