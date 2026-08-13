"""PRISM 어댑터 검증.

PRISM 의 슬롯은 비중이 아니라 고정 금액이다.
따라서 계좌가 커질수록 같은 슬롯이 더 작은 비중이 된다 — 그 사실이 그대로 기록되어야 한다.
"""

from __future__ import annotations

from decimal import Decimal as D

import pytest

from prism_core.stance_adapter import (
    AccountSnapshot, StanceReporter, buy_target_weight, sell_target_weight,
    snapshot_from_kis,
)


def snap(cash, holdings: dict | None = None) -> AccountSnapshot:
    holdings = holdings or {}
    return AccountSnapshot(cash=D(str(cash)),
                           holdings_value=sum(holdings.values(), D(0)),
                           position_values=holdings)


def test_total_assets_includes_holdings():
    """예수금만 세면 안 된다. 가장 흔한 실수다."""
    s = snap(3_000_000, {"005930": D(7_000_000)})
    assert s.total_assets == D(10_000_000)


def test_fixed_slot_becomes_smaller_weight_as_account_grows():
    unit = D(1_000_000)

    small = buy_target_weight(snap(10_000_000), "005930", unit)
    big = buy_target_weight(snap(20_000_000), "005930", unit)

    assert small == D("0.1")     # 총자산 1,000만이면 슬롯 하나가 10%
    assert big == D("0.05")      # 2,000만으로 불면 같은 슬롯이 5%
    assert big < small


def test_reentry_adds_to_existing_value():
    """이미 들고 있는 종목에 한 슬롯 더 넣으면 기존 평가액 위에 쌓인다."""
    s = snap(4_000_000, {"005930": D(6_000_000)})
    w = buy_target_weight(s, "005930", D(1_000_000))
    assert w == D("0.7")         # (600만 + 100만) / 1,000만


def test_sell_defaults_to_full_exit():
    """PRISM 은 올인/올아웃이라 수량을 주지 않으면 전량 청산으로 본다."""
    assert sell_target_weight(snap(1_000_000), "005930") == D(0)


def test_partial_sell_leaves_remaining_weight():
    s = snap(5_000_000, {"005930": D(5_000_000)})
    # 100주 중 40주 매도 → 남는 평가액 300만 / 총자산 1,000만
    assert sell_target_weight(s, "005930", sold_qty=40, held_qty=100) == D("0.3")


def test_selling_everything_is_zero():
    s = snap(5_000_000, {"005930": D(5_000_000)})
    assert sell_target_weight(s, "005930", sold_qty=100, held_qty=100) == D(0)
    assert sell_target_weight(s, "005930", sold_qty=150, held_qty=100) == D(0)


def test_weight_is_clamped_to_one():
    s = snap(1_000_000)
    assert buy_target_weight(s, "005930", D(999_000_000)) == D(1)


def test_zero_assets_raises():
    with pytest.raises(ValueError):
        buy_target_weight(snap(0), "005930", D(1_000_000))


def test_reporter_never_breaks_trading():
    """선언 전송이 실패해도 PRISM 의 매매를 막아서는 안 된다."""

    class Exploding:
        def set(self, *a, **k):
            raise RuntimeError("네트워크 장애")

        def hold(self, *a, **k):
            raise RuntimeError("네트워크 장애")

    r = StanceReporter(client=Exploding(), unit_amount=D(1_000_000))
    assert r.report_buy(snap(10_000_000), "005930") is None
    assert r.report_sell("005930") is None
    assert r.report_hold() is None


def test_reporter_disabled_without_client():
    r = StanceReporter(client=None, unit_amount=D(1_000_000))
    assert not r.enabled
    assert r.report_buy(snap(10_000_000), "005930") is None


def test_reporter_sends_expected_weight():
    sent = []

    class Recorder:
        def set(self, symbol, weight, reason=None):
            sent.append((symbol, weight, reason))
            return {"admit": "accepted"}

    r = StanceReporter(client=Recorder(), unit_amount=D(1_000_000))
    r.report_buy(snap(9_000_000, {"000660": D(1_000_000)}), "005930", reason="눌림목")

    assert sent == [("005930", D("0.1"), "눌림목")]


def test_reporter_buy_uses_actual_order_amount_when_provided():
    """반 슬롯 매수는 기본 슬롯이 아니라 실제 주문금액으로 선언한다."""
    sent = []

    class Recorder:
        def set(self, symbol, weight, reason=None):
            sent.append((symbol, weight, reason))
            return {"admit": "accepted"}

    r = StanceReporter(client=Recorder(), unit_amount=D(1_000_000))
    r.report_buy(snap(10_000_000), "005930", add_amount=500_000)

    assert sent == [("005930", D("0.05"), None)]


def test_snapshot_from_kis_shape():
    balance = {
        "output1": [{"pdno": "005930", "evlu_amt": "7000000"},
                    {"pdno": "000660", "evlu_amt": "1000000"}],
        "output2": [{"dnca_tot_amt": "2000000"}],
    }
    s = snapshot_from_kis(balance)
    assert s.cash == D(2_000_000)
    assert s.holdings_value == D(8_000_000)
    assert s.total_assets == D(10_000_000)
    assert buy_target_weight(s, "005930", D(1_000_000)) == D("0.8")


# ── KIS 시세 제공자 ───────────────────────────────────────────────────────

from prism_core.stance_quotes import KisQuoteProvider, StaticQuoteProvider  # noqa: E402


class FakeKis:
    def __init__(self, payload=None, boom=False):
        self.payload, self.boom = payload, boom

    def get_current_price(self, code):
        if self.boom:
            raise RuntimeError("KIS 장애")
        return self.payload


def test_quote_provider_maps_price():
    q = KisQuoteProvider(FakeKis({"current_price": 71200, "iscd_stat_cls_code": "00"}))("KRX", "005930")
    assert q.price == D(71200)
    assert q.tradable
    assert q.source == "kis"


def test_halted_stock_is_not_tradable():
    """거래정지(58)는 현실에서 체결할 수 없다."""
    q = KisQuoteProvider(FakeKis({"current_price": 1000, "iscd_stat_cls_code": "58"}))("KRX", "005930")
    assert not q.tradable


@pytest.mark.parametrize("payload", [None, {}, {"current_price": 0}, {"current_price": -1}])
def test_unusable_response_becomes_none(payload):
    """None 을 돌려주면 서버가 보류(PENDING)로 처리한다. 참여자를 벌하지 않는다."""
    assert KisQuoteProvider(FakeKis(payload))("KRX", "005930") is None


def test_provider_never_raises():
    """시세 조회 예외가 선언 접수를 깨뜨리면 안 된다."""
    assert KisQuoteProvider(FakeKis(boom=True))("KRX", "005930") is None


def test_provider_is_krx_only():
    assert KisQuoteProvider(FakeKis({"current_price": 100}))("NASDAQ", "AAPL") is None


def test_static_provider_for_demo():
    p = StaticQuoteProvider({"005930": 70000})
    assert p("KRX", "005930").price == D(70000)
    assert p("KRX", "000660") is None


def test_provider_detects_upper_limit():
    """상한가 도달 — 매수는 막고 매도는 허용해야 한다."""
    q = KisQuoteProvider(FakeKis({
        "current_price": 300000, "iscd_stat_cls_code": "00",
        "upper_limit": "300000", "lower_limit": "162000",
    }))("KRX", "005930")
    assert q.at_upper_limit and not q.can_buy
    assert q.can_sell


def test_provider_detects_lower_limit():
    q = KisQuoteProvider(FakeKis({
        "current_price": 162000, "iscd_stat_cls_code": "00",
        "upper_limit": "300000", "lower_limit": "162000",
    }))("KRX", "005930")
    assert q.at_lower_limit and not q.can_sell
    assert q.can_buy


def test_normal_price_has_no_limit_flags():
    """실측값 — 삼성전자 231,500 / 상한가 300,000 / 하한가 162,000"""
    q = KisQuoteProvider(FakeKis({
        "current_price": 231500, "iscd_stat_cls_code": "55",
        "upper_limit": "300000", "lower_limit": "162000",
    }))("KRX", "005930")
    assert not q.at_upper_limit and not q.at_lower_limit
    assert q.can_buy and q.can_sell


def test_missing_limit_fields_are_ignored():
    """필드가 없으면 판정하지 않는다 — 없다고 막아버리면 안 된다."""
    q = KisQuoteProvider(FakeKis({"current_price": 1000, "iscd_stat_cls_code": "00"}))("KRX", "A")
    assert not q.at_upper_limit and not q.at_lower_limit


# ── 계좌 스냅샷 ───────────────────────────────────────────────────────────

class FakeTrader:
    def __init__(self, portfolio=None, summary=None, boom=False):
        self._p, self._s, self.boom = portfolio or [], summary or {}, boom

    def get_portfolio(self):
        if self.boom:
            raise RuntimeError("잔고 조회 실패")
        return self._p

    def get_account_summary(self):
        return self._s


def test_snapshot_from_trader():
    from prism_core.stance_adapter import snapshot_from_trader

    s = snapshot_from_trader(FakeTrader(
        portfolio=[{"stock_code": "005930", "eval_amount": 7_000_000},
                   {"stock_code": "000660", "eval_amount": 1_000_000}],
        summary={"deposit": 2_000_000},
    ))
    assert s.cash == D(2_000_000)
    assert s.total_assets == D(10_000_000)
    assert buy_target_weight(s, "005930", D(1_000_000)) == D("0.8")


# ── 환경변수 게이팅 ───────────────────────────────────────────────────────

def test_reporter_is_off_without_env(monkeypatch):
    """서버가 뜨기 전에 켜지면 주문마다 죽은 엔드포인트를 때린다. 기본은 꺼짐이다."""
    for k in ("STANCE_ENDPOINT", "STANCE_API_KEY", "STANCE_STRATEGY"):
        monkeypatch.delenv(k, raising=False)
    assert not StanceReporter.from_env().enabled


def test_reporter_needs_only_endpoint_and_api_key(monkeypatch):
    monkeypatch.setenv("STANCE_ENDPOINT", "http://127.0.0.1:8800")
    monkeypatch.setenv("STANCE_API_KEY", "stk_x")
    monkeypatch.delenv("STANCE_STRATEGY", raising=False)
    assert StanceReporter.from_env().enabled


def test_reporter_uses_market_specific_key_and_amount(monkeypatch):
    monkeypatch.setenv("STANCE_ENDPOINT", "http://127.0.0.1:8800")
    monkeypatch.setenv("STANCE_API_KEY", "stk_legacy")
    monkeypatch.setenv("STANCE_KR_API_KEY", "stk_kr")
    monkeypatch.setenv("STANCE_US_API_KEY", "stk_us")
    monkeypatch.setenv("STANCE_KR_UNIT_AMOUNT", "1000000")
    monkeypatch.setenv("STANCE_US_UNIT_AMOUNT", "750")

    kr = StanceReporter.from_env("KR")
    us = StanceReporter.from_env("US")

    assert kr.client.token == "stk_kr"
    assert us.client.token == "stk_us"
    assert kr.unit_amount == D("1000000")
    assert us.unit_amount == D("750")


def test_market_specific_config_does_not_fall_back_to_other_market(monkeypatch):
    monkeypatch.setenv("STANCE_ENDPOINT", "http://127.0.0.1:8800")
    monkeypatch.setenv("STANCE_KR_API_KEY", "stk_kr")
    monkeypatch.delenv("STANCE_API_KEY", raising=False)
    monkeypatch.delenv("STANCE_US_API_KEY", raising=False)

    assert StanceReporter.from_env("KR").enabled
    assert not StanceReporter.from_env("US").enabled
