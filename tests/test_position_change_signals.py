"""Position-change messages and signals (2026-10-05).

Telegram add message, ADD / re-entry signal payloads (publish kill switch, primary-account-only),
re-entry labels, and the subscriber's ADD mirroring. Network-free: brokers, publishers and
alerts are fakes; the publish guard stays on unless a test replaces it explicitly.
"""
import asyncio
import importlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from prism_core import micro_split_live as live
from prism_core import reentry_v3_live as reentry

SUB_MOD = "examples.messaging.gcp_pubsub_subscriber_example"


# ---------------------------------------------------------------- Telegram add message

def test_add_message_is_plain_and_complete_kr_and_us():
    meta = {"scenario_id": "breakout_1", "scenario_type": "breakout", "rationale": "prior high reclaimed",
            "rail": "ACCELERATION", "acceleration": {"gain_pct": 8.5, "volume_pace": 2.4}}
    ko = live.add_message(market="KR", company_name="삼성전자", ticker="005930", before=0.5, after=0.8, price=10200,
                          average=10050.5, order_status="주문 접수(체결은 별도 확인)", scenario=meta, stop_loss=9300)
    assert ko == ("📈 추가 매수(비중 확대): 삼성전자(005930)\n비중: 50% → 80% (1슬롯 기준)\n추가 매수가: 10,200원\n"
                  "평균 매수가: 10,050원\n손절가: 9,300원 (손절 시 전량 매도)\n근거: 돌파 조건 확인 — prior high reclaimed\n"
                  "가속 구간: 오늘 두 번째 추가 매수 (최초 매수가 대비 +8.5%, 거래량 평소의 2.4배)\n"
                  "주문: 주문 접수(체결은 별도 확인)\n")
    en = live.add_message(market="US", company_name="Nvidia", ticker="NVDA", before=0.35, after=0.6, price=181.2,
                          average=178.4, order_status="주문 접수(체결은 별도 확인)", scenario=meta,
                          stop_loss=170)
    # US is Korean too (the US channel is Korean); only the currency differs.
    assert en == ("📈 추가 매수(비중 확대): Nvidia(NVDA)\n비중: 35% → 60% (1슬롯 기준)\n추가 매수가: $181.20\n"
                  "평균 매수가: $178.40\n손절가: $170.00 (손절 시 전량 매도)\n근거: 돌파 조건 확인 — prior high reclaimed\n"
                  "가속 구간: 오늘 두 번째 추가 매수 (최초 매수가 대비 +8.5%, 거래량 평소의 2.4배)\n"
                  "주문: 주문 접수(체결은 별도 확인)\n")
    for text in (ko, en):
        assert "breakout_1" not in text and "초분할" not in text and "Micro-split" not in text


def test_add_order_status_hides_reason_codes():
    assert live.add_order_status({"success": True}, "KR") == "주문 접수(체결은 별도 확인)"
    below = {"success": False, "reason_code": "micro_split_add_below_one_share"}
    assert live.add_order_status(below, "KR") == "1주 미만이라 주문하지 않았습니다(전략 비중에는 반영)"
    assert live.add_order_status(below, "US") == "1주 미만이라 주문하지 않았습니다(전략 비중에는 반영)"
    assert live.add_order_status({"success": False, "reason_code": "kis_rejected"}, "KR") == \
        "주문이 접수되지 않았습니다(전략 비중에는 반영)"


class _Broker:
    calls = []

    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute_buy(self, **kwargs):
        _Broker.calls.append(kwargs)
        return {"success": True, "status": "submitted"}


def _add_agent(tmp_path, accounts):
    scenario = {"micro_split": live.entry_record(
        plan={"initial_nominal": "0.5", "policy_version": "v3", "plan_hash": "h", "entry_reference": 10000},
        unit_amount=1_000_000, market="KR", entered_at="t0")}
    scenario["micro_split"]["add_plan"] = {"plan_hash": "ph", "status": "ACTIVE", "valid_for": "2026-10-05"}
    conn = sqlite3.connect(tmp_path / "h.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE stock_holdings (id INTEGER PRIMARY KEY, ticker TEXT, company_name TEXT, "
                 "account_key TEXT, scenario TEXT, buy_price REAL, stop_loss REAL)")
    conn.execute("INSERT INTO stock_holdings VALUES (7, '005930', '삼성전자', 'acc2', ?, 10000, 9300)",
                 (json.dumps(scenario),))
    conn.commit()
    return SimpleNamespace(conn=conn, cursor=conn.cursor(), db_path=str(tmp_path / "h.sqlite"),
                           account_configs=accounts, _set_active_account=lambda account: None,
                           message_queue=[], _msg_types=[])


def _run_add(agent, monkeypatch):
    from observability import events
    from prism_core import execution_service

    published = []

    async def capture(**kwargs):
        published.append(kwargs)

    monkeypatch.setenv("MICRO_SPLIT_LIVE_ENABLED", "true")
    monkeypatch.setattr(events, "emit_event", lambda *a, **k: None)
    monkeypatch.setattr(execution_service.ExecutionService, "domestic", _Broker, raising=False)
    monkeypatch.setattr(live, "publish_add_signal", capture)
    meta = {"plan_hash": "ph", "scenario_id": "breakout_1", "scenario_type": "breakout", "lens": ["oneil"],
            "session": "2026-10-05", "trigger_price": 10200, "rationale": "r"}
    result = asyncio.run(live.execute_add(
        agent, market="KR", campaign={"position_id": "legacy:KR:7", "account_key": "acc2", "symbol": "005930",
                                      "campaign_id": "c1"},
        decision={"target_allocation": "0.8", "price": 10200, "bar_end": "add-plan:2026-10-05:breakout_1",
                  "add_plan": meta}, now="t1"))
    return result, published


def test_primary_add_is_announced_once_and_published_with_its_fields(tmp_path, monkeypatch):
    agent = _add_agent(tmp_path, [{"account_key": "acc2", "name": "primary"}])
    result, published = _run_add(agent, monkeypatch)
    assert result["announced"] is True and len(agent.message_queue) == 1
    (signal,) = published
    fields = signal["fields"]
    assert (signal["ticker"], signal["company_name"], signal["price"]) == ("005930", "삼성전자", 10200.0)
    assert fields["allocation_before"] == 0.5 and fields["allocation_after"] == 0.8
    assert fields["delta_fraction"] == pytest.approx(0.3) and fields["limit_price"] == 10200.0
    assert fields["market"] == "KR" and fields["position_id"] == "legacy:KR:7" and fields["stop_loss"] == 9300.0
    assert fields["signal_id"] and fields["trade_success"] is True and fields["acceleration"] is False


def test_secondary_account_add_orders_but_sends_no_message_or_signal(tmp_path, monkeypatch):
    agent = _add_agent(tmp_path, [{"account_key": "acc1", "name": "primary"},
                                  {"account_key": "acc2", "name": "secondary"}])
    _Broker.calls.clear()
    result, published = _run_add(agent, monkeypatch)
    assert result["status"] == "EXECUTED" and result["announced"] is False
    assert len(_Broker.calls) == 1 and agent.message_queue == [] and published == []


# ---------------------------------------------------------------- publisher payloads and kill switch

class _Future:
    def result(self):
        return "msg-1"


class _FakeClient:
    def __init__(self):
        self.sent = []

    def publish(self, topic, data):
        self.sent.append(json.loads(data.decode("utf-8")))
        return _Future()

    def xadd(self, stream, message_id, data):
        self.sent.append(json.loads(data["data"]))
        return "1-0"


def _gcp(monkeypatch, *, guard_off):
    gcp = importlib.import_module("messaging.gcp_pubsub_signal_publisher")
    publisher = gcp.SignalPublisher(project_id="p", topic_id="t")
    client = _FakeClient()
    publisher._publisher, publisher._topic_path = client, "projects/p/topics/t"
    monkeypatch.setattr(gcp, "_global_publisher", publisher)
    if guard_off:
        monkeypatch.setattr(gcp, "signal_publishing_disabled", lambda: False)
    return gcp, client


def test_add_signal_payload_and_backward_compatible_type(monkeypatch):
    gcp, client = _gcp(monkeypatch, guard_off=True)
    fields = {"market": "US", "signal_id": "s1", "allocation_before": 0.35, "allocation_after": 0.6,
              "delta_fraction": 0.25, "limit_price": 181.2, "position_id": "legacy:US:3"}
    assert asyncio.run(gcp.publish_add_signal(ticker="NVDA", company_name="Nvidia", price=181.2,
                                              fields=fields)) == "msg-1"
    (sent,) = client.sent
    assert sent["type"] == "ADD" and sent["ticker"] == "NVDA" and sent["price"] == 181.2
    assert {k: sent[k] for k in fields} == fields and "position_fraction" not in sent


def test_redis_add_signal_uses_the_same_payload(monkeypatch):
    redis = importlib.import_module("messaging.redis_signal_publisher")
    publisher = redis.SignalPublisher.__new__(redis.SignalPublisher)
    client = _FakeClient()
    publisher._redis = client
    monkeypatch.setattr(redis, "_global_publisher", publisher)
    monkeypatch.setattr(redis, "signal_publishing_disabled", lambda: False)
    asyncio.run(redis.publish_add_signal(ticker="005930", company_name="삼성전자", price=10200,
                                         fields={"market": "KR", "signal_id": "s2", "delta_fraction": 0.3}))
    assert client.sent[0]["type"] == "ADD" and client.sent[0]["signal_id"] == "s2"


def test_publish_kill_switch_blocks_add_signals(monkeypatch):
    monkeypatch.setenv("PRISM_DISABLE_SIGNAL_PUBLISH", "1")
    gcp, client = _gcp(monkeypatch, guard_off=False)
    assert asyncio.run(gcp.publish_add_signal(ticker="NVDA", company_name="Nvidia", price=1.0,
                                              fields={"signal_id": "s"})) is None
    assert client.sent == []


def test_reentry_buy_is_labelled_and_regular_buys_are_unchanged(monkeypatch):
    gcp, client = _gcp(monkeypatch, guard_off=True)
    base = {"target_price": 12, "stop_loss": 9, "buy_score": 7}
    meta = {"version": "reentry_v3", "signal": "RETEST", "attempt": 2, "max_attempts": 3, "level": 10.5,
            "watch_id": "w"}
    asyncio.run(gcp.publish_buy_signal(ticker="A", company_name="A", price=11, scenario=dict(base, reentry=meta)))
    asyncio.run(gcp.publish_buy_signal(ticker="B", company_name="B", price=11, scenario=dict(base)))
    labelled, regular = client.sent
    assert labelled["entry_kind"] == "REENTRY"
    assert labelled["reentry"] == {"signal": "RETEST", "attempt": 2, "max_attempts": 3, "level": 10.5}
    assert "entry_kind" not in regular and "reentry" not in regular


# ---------------------------------------------------------------- re-entry labels

def _reentry_scenario(market="KR"):
    return {"reentry": {"version": "reentry_v3", "signal": "REBREAK", "attempt_label": "1/3", "attempt": 1,
                        "max_attempts": 3, "level": 11950.0, "level_basis": "primary_support",
                        "source": "STOP_EXIT", "band_level": 11950.0}}


def test_us_reentry_buy_line_is_english_and_kr_stays_korean():
    us = reentry.entry_message_line(_reentry_scenario(), "US")
    assert us == ("🔁 Re-entry Buy (re-break of the reference price, attempt 1/3)\n"
                  "We stopped out of this stock earlier. It moved back above the level it broke out of at the "
                  "first buy ($11,950.00).\n")
    assert reentry.entry_message_line(_reentry_scenario(), "KR").startswith(
        "🔁 재진입 매수 (기준 가격 재돌파 매수, 1/3번째 시도)\n")


def test_reentry_holding_tag_for_portfolio_and_sell_messages():
    scenario = _reentry_scenario()
    assert reentry.holding_tag(scenario, "KR", indent="  ") == "  🔁 재진입 종목 (기준 가격 재돌파 매수, 1/3번째 시도)\n"
    assert reentry.holding_tag(json.dumps(scenario), "US") == \
        "🔁 Re-entry position (re-break of the reference price, attempt 1/3)\n"
    assert reentry.holding_tag(scenario, "US", language="ko").startswith("🔁 재진입 종목")
    assert reentry.holding_tag({}, "KR") == "" and reentry.holding_tag("not json", "KR") == ""
    assert reentry.signal_fields({}) == {}


# ---------------------------------------------------------------- subscriber ADD mirroring

class _Trader:
    def __init__(self, *, held=30, status="HELD", buy_amount=1_000_000, success=True):
        self.held, self.status, self.buy_amount, self.success = held, status, buy_amount, success
        self.buys = []

    def get_holding_quantity_checked(self, ticker):
        return self.status, (None if self.status == "UNKNOWN" else self.held)

    async def async_buy_stock(self, **kwargs):
        self.buys.append(kwargs)
        return {"success": self.success, "message": "ok" if self.success else "rejected"}


class _USTrader(_Trader):
    get_holding_quantity_checked = None

    def get_holding_quantity(self, ticker):
        return self.held


def _context(trader):
    class _Ctx:
        async def __aenter__(self):
            return trader

        async def __aexit__(self, *exc):
            return False

    return lambda market: _Ctx()


def _signal(**overrides):
    signal = {"type": "ADD", "market": "KR", "ticker": "005930", "company_name": "삼성전자", "price": 10000,
              "limit_price": 10000, "signal_id": "sig-1", "allocation_before": 0.3, "allocation_after": 0.8,
              "delta_fraction": 0.5}
    signal.update(overrides)
    return signal


@pytest.fixture
def sub(monkeypatch, tmp_path):
    module = importlib.import_module(SUB_MOD)
    monkeypatch.setenv("SUBSCRIBER_ADD_LEDGER", str(tmp_path / "adds.jsonl"))
    monkeypatch.delenv("SUBSCRIBER_FOLLOW_ADDS", raising=False)
    return module


def _follow(sub, signal, trader=None, *, dry_run=False, market_open=True, alerts=None):
    import logging
    alerts = alerts if alerts is not None else []

    def no_broker(market):
        raise AssertionError("broker must not be touched")

    return asyncio.run(sub.follow_add_signal(
        signal, logging.getLogger("test-sub"), dry_run=dry_run,
        open_trading=_context(trader) if trader else no_broker, alert=alerts.append,
        market_open=lambda market: market_open))


def test_held_ticker_buys_the_delta_of_one_slot_with_a_strict_limit(sub):
    trader = _Trader(held=30)  # 300,000 of a 1,000,000 slot = PRISM's 30%
    result = _follow(sub, _signal(), trader)
    assert result["success"] is True
    assert trader.buys == [{"stock_code": "005930", "buy_amount": 500_000, "limit_price": 10000,
                            "strict_budget": True}]


def test_add_is_capped_at_prism_allocation_and_one_slot(sub):
    alerts = []
    risen = _Trader(held=30)
    _follow(sub, _signal(signal_id="a", limit_price=11000, price=11000), risen)
    assert risen.buys[0]["buy_amount"] == 470_000  # 800,000 target - 330,000 held value
    full = _Trader(held=100)  # legacy full-slot entry already holds more than 80%
    result = _follow(sub, _signal(signal_id="b"), full, alerts=alerts)
    assert result["skipped"] and full.buys == [] and "holding already at PRISM's allocation" in alerts[0]


def test_not_held_or_unknown_holding_never_opens_a_position(sub):
    alerts = []
    flat = _Trader(held=0, status="FLAT")
    assert _follow(sub, _signal(signal_id="c"), flat, alerts=alerts)["skipped"] and flat.buys == []
    unknown = _Trader(status="UNKNOWN")
    assert _follow(sub, _signal(signal_id="d"), unknown, alerts=alerts)["skipped"] and unknown.buys == []
    assert "not held here" in alerts[0] and "holding query failed" in alerts[1]


def test_same_signal_id_never_buys_twice_across_restarts(sub, tmp_path):
    first = _Trader()
    _follow(sub, _signal(), first)
    importlib.reload(sub)  # a restarted process only has the ledger file
    second = _Trader()
    result = _follow(sub, _signal(), second)
    assert result == {"success": True, "skipped": True, "message": "duplicate signal"} and second.buys == []
    phases = [json.loads(line)["phase"] for line in (tmp_path / "adds.jsonl").read_text().splitlines()]
    assert phases == ["claimed", "result"]


def test_failed_add_is_alerted_and_not_retried(sub):
    alerts = []
    _follow(sub, _signal(signal_id="e"), _Trader(success=False), alerts=alerts)
    assert alerts and alerts[0].startswith("❌ [ADD_FAILED]")
    again = _Trader()
    assert _follow(sub, _signal(signal_id="e"), again)["message"] == "duplicate signal" and again.buys == []


def test_kill_switch_dry_run_market_hours_and_bad_payloads_place_no_order(sub, monkeypatch, tmp_path):
    alerts = []
    assert _follow(sub, _signal(signal_id="f"), dry_run=True)["message"] == "dry-run"
    assert _follow(sub, _signal(signal_id="g"), market_open=False, alerts=alerts)["skipped"]
    assert "outside regular market hours" in alerts[-1]
    for bad in (_signal(signal_id=""), _signal(signal_id="h", allocation_after=1.2, delta_fraction=0.9),
                _signal(signal_id="i", delta_fraction=0.1)):
        assert _follow(sub, bad, alerts=alerts)["skipped"]
    monkeypatch.setenv("SUBSCRIBER_FOLLOW_ADDS", "false")
    before = len(alerts)
    assert _follow(sub, _signal(signal_id="j"))["message"] == "SUBSCRIBER_FOLLOW_ADDS is off"
    assert len(alerts) == before and not (tmp_path / "adds.jsonl").exists()


def test_us_add_uses_cents_and_the_plain_holding_query(sub):
    trader = _USTrader(held=2, buy_amount=1000.0)  # 2 x $150 = 30% of a $1,000 slot
    _follow(sub, _signal(market="US", ticker="NVDA", signal_id="k", limit_price=150.0, price=150.0,
                         delta_fraction=0.25, allocation_after=0.55), trader)
    assert trader.buys == [{"ticker": "NVDA", "buy_amount": 250.0, "limit_price": 150.0, "strict_budget": True}]


def test_plan_add_amount_edges(sub):
    assert sub.plan_add_amount(unit_amount=0, delta_fraction=0.5, allocation_after=0.8, held_quantity=1,
                               price=10)[0] == 0
    assert sub.plan_add_amount(unit_amount=1000, delta_fraction=0.001, allocation_after=0.4, held_quantity=1,
                               price=10)[1] == "add amount below one share"
    assert sub.plan_add_amount(unit_amount=1000.0, delta_fraction=0.333, allocation_after=1.0, held_quantity=1,
                               price=10, market="US") == (333.0, None)
    assert sub._position_fraction({}) == 1.0  # old BUY signals without the field stay a full slot
