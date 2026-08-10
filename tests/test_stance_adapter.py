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


def test_sell_is_always_zero():
    assert sell_target_weight() == D(0)


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
