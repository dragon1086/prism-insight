"""ExecutionService 의 Stance 훅 — **매매를 막지 않는 것이 유일한 요구사항이다.**

실제 돈이 오가는 경로에 붙는 코드다. 선언 전송이 어떤 식으로 실패하든
주문 결과는 그대로 반환되어야 하고, 예외가 새어 나가서는 안 된다.
"""

from __future__ import annotations

import asyncio

import pytest

from prism_core.execution_service import ExecutionService


class FakeTrader:
    """주문은 항상 성공하는 가짜 브로커."""

    def __init__(self):
        self.buys: list[dict] = []
        self.sells: list[dict] = []

    async def async_buy_stock(self, **kwargs):
        self.buys.append(kwargs)
        return {"status": "success", "order_no": "B1"}

    async def async_sell_stock(self, **kwargs):
        self.sells.append(kwargs)
        return {"status": "success", "order_no": "S1"}

    def get_portfolio(self):
        return [{"stock_code": "005930", "eval_amount": 3_000_000}]

    def get_account_summary(self):
        return {"deposit": 7_000_000}

    def get_holding_quantity(self, code):
        return 10


class RecordingReporter:
    enabled = True

    def __init__(self, boom: bool = False):
        self.boom = boom
        self.calls: list[tuple] = []

    def report_buy(self, snapshot, symbol, reason=None):
        if self.boom:
            raise RuntimeError("Stance 서버 다운")
        self.calls.append(("BUY", symbol, snapshot.total_assets))

    def report_sell(self, symbol, reason=None, snapshot=None, sold_qty=None, held_qty=None):
        if self.boom:
            raise RuntimeError("Stance 서버 다운")
        self.calls.append(("SELL", symbol, sold_qty, held_qty))


def run(coro):
    return asyncio.run(coro)


# ── 기본은 꺼짐 ───────────────────────────────────────────────────────────

def test_disabled_by_default():
    """리포터를 주지 않으면 아무 일도 일어나지 않는다."""
    trader = FakeTrader()
    svc = ExecutionService(trader)

    result = run(svc.execute_buy(stock_code="005930", limit_price=70000))

    assert result["status"] == "success"
    assert trader.buys == [{"stock_code": "005930", "limit_price": 70000}]


def test_env_gating_keeps_it_off(monkeypatch):
    for k in ("STANCE_ENDPOINT", "STANCE_API_KEY", "STANCE_STRATEGY"):
        monkeypatch.delenv(k, raising=False)
    from prism_core.execution_service import _stance_reporter_from_env

    reporter = _stance_reporter_from_env()
    assert reporter is None or not reporter.enabled


# ── 켜졌을 때 ─────────────────────────────────────────────────────────────

def test_buy_declares_with_account_snapshot():
    trader = FakeTrader()
    reporter = RecordingReporter()
    svc = ExecutionService(trader, stance_reporter=reporter)

    run(svc.execute_buy(stock_code="005930", limit_price=70000))

    assert len(reporter.calls) == 1
    side, symbol, total = reporter.calls[0]
    assert (side, symbol) == ("BUY", "005930")
    assert total == 10_000_000       # 예수금 700만 + 보유 300만


def test_sell_passes_quantities_for_partial_exit():
    trader = FakeTrader()
    reporter = RecordingReporter()
    svc = ExecutionService(trader, stance_reporter=reporter)

    run(svc.execute_sell(stock_code="005930", quantity=4, limit_price=70000))

    side, symbol, sold, held = reporter.calls[0]
    assert (side, symbol, sold, held) == ("SELL", "005930", 4, 10)


# ── 절대 매매를 막지 않는다 ───────────────────────────────────────────────

def test_reporter_failure_does_not_break_the_order():
    """Stance 서버가 죽어도 주문 결과는 그대로 나와야 한다."""
    trader = FakeTrader()
    svc = ExecutionService(trader, stance_reporter=RecordingReporter(boom=True))

    result = run(svc.execute_buy(stock_code="005930", limit_price=70000))

    assert result["status"] == "success"
    assert trader.buys                       # 주문은 실제로 나갔다


def test_broken_snapshot_does_not_break_the_order():
    """잔고 조회가 깨져도 마찬가지다."""
    class BrokenTrader(FakeTrader):
        def get_portfolio(self):
            raise RuntimeError("잔고 조회 실패")

    trader = BrokenTrader()
    svc = ExecutionService(trader, stance_reporter=RecordingReporter())

    result = run(svc.execute_buy(stock_code="005930", limit_price=70000))
    assert result["status"] == "success"


def test_declaration_happens_after_the_order():
    """주문을 지연시키지 않는다 — 주문이 끝난 뒤에 선언한다."""
    order: list[str] = []

    class OrderedTrader(FakeTrader):
        async def async_buy_stock(self, **kwargs):
            order.append("order")
            return {"status": "success"}

        def get_portfolio(self):
            order.append("declare")
            return []

        def get_account_summary(self):
            return {"deposit": 1_000_000}

    svc = ExecutionService(OrderedTrader(), stance_reporter=RecordingReporter())
    run(svc.execute_buy(stock_code="005930"))

    assert order == ["order", "declare"]


def test_missing_symbol_is_skipped_quietly():
    trader = FakeTrader()
    reporter = RecordingReporter()
    svc = ExecutionService(trader, stance_reporter=reporter)

    run(svc.execute_buy(limit_price=70000))    # stock_code 없음

    assert reporter.calls == []


@pytest.mark.parametrize("enabled", [False, True])
def test_disabled_reporter_is_never_called(enabled):
    reporter = RecordingReporter()
    reporter.enabled = enabled
    svc = ExecutionService(FakeTrader(), stance_reporter=reporter)

    run(svc.execute_buy(stock_code="005930"))

    assert bool(reporter.calls) is enabled
