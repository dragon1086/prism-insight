"""시장 프로파일과, 자산군을 넓히려 할 때 드러나는 문제들.

핵심 주장은 이것이다 — **코어는 자산군을 몰라야 한다.**
시장마다 다른 사실(캘린더·시세 권위·세금·마감 시각)을 코어에 넣으면
자산군이 늘 때마다 프로토콜이 흔들린다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from stance.server import (
    DailyMark, Engine, EventType, Kind, MarketEvent, Quote, Stance, score,
    summary_lines,
)
from stance.server.markets import (
    CRYPTO, KRX, NASDAQ, PROFILES, Support, describe, profile_for, stable_markets,
)
from stance.server.models import Costs

UTC = timezone.utc
T0 = datetime(2026, 8, 10, 0, 30, tzinfo=UTC)


def st(seq, symbol, w, at=None):
    return Stance(seq=seq, received_at=at or (T0 + timedelta(minutes=seq)),
                  kind=Kind.SET, symbol=symbol, target_weight=D(str(w)))


# ── v1 지원 범위 ──────────────────────────────────────────────────────────

def test_only_equities_are_stable_in_v1():
    """v1 이 공식 지원하는 것은 현물 주식뿐이다."""
    assert stable_markets() == ["KRX", "NASDAQ", "NYSE"]
    assert CRYPTO.support is Support.EXPERIMENTAL


def test_crypto_declares_its_unsolved_problems():
    """실험적 지원은 무엇이 안 풀렸는지 밝혀야 한다. 숨기면 표준이 아니다."""
    assert CRYPTO.notes
    joined = " ".join(CRYPTO.notes)
    assert "코어 규칙 ①" in joined          # 시세 권위가 없다
    assert "선물" in joined                  # 롱온리 현물만 다룬다
    assert "미확정" in CRYPTO.price_authority


def test_equity_markets_have_a_single_price_authority():
    """주식은 거래소가 하나라 '서버가 가격을 정한다' 가 자명하게 성립한다."""
    for p in (KRX, NASDAQ):
        assert "미확정" not in p.price_authority
        assert p.support is Support.STABLE


def test_unknown_market_is_rejected_with_guidance():
    with pytest.raises(ValueError, match="지원 시장"):
        profile_for("FOREX")


@pytest.mark.parametrize("alias,expected", [
    ("US", "NASDAQ"), ("KOSPI", "KRX"), ("KOSDAQ", "KRX"),
    ("UPBIT", "CRYPTO"), ("krx", "KRX"),
])
def test_market_aliases(alias, expected):
    assert profile_for(alias).code == expected


# ── 시장마다 달라야 하는 것 ────────────────────────────────────────────────

def test_minimum_track_record_is_three_months_not_sixty_days():
    """'60거래일' 을 그대로 쓰면 시장마다 실제 기간이 달라진다.

    주식 60거래일은 3개월이지만, 휴장일이 없는 크립토의 60일은 2개월이다.
    """
    assert KRX.min_track_periods == 63        # 252 * 0.25
    assert CRYPTO.min_track_periods == 91     # 365 * 0.25
    assert CRYPTO.min_track_periods > KRX.min_track_periods


def test_downside_floor_scales_with_market_volatility():
    """주식용 하한을 크립토에 쓰면 사실상 무력하다. 일간 변동성이 3~5배다."""
    assert CRYPTO.downside_floor_daily > KRX.downside_floor_daily


def test_crypto_has_no_statutory_tax_but_equities_do():
    assert CRYPTO.costs.tax == D(0)
    assert KRX.costs.tax > 0
    # 거래소 수수료는 거래소·등급마다 달라 법정 비용이 아니다
    assert CRYPTO.costs.commission == D(0)


def test_experimental_market_is_flagged_in_scorecard():
    e = Engine(profile=CRYPTO)
    for i in range(3):
        e.apply_mark(DailyMark(date(2026, 1, 1) + timedelta(days=i), {}))

    m = score(e.result, profile=CRYPTO)
    assert m.experimental
    assert m.market == "CRYPTO"
    assert any("실험적" in line for line in summary_lines(m))


def test_stable_market_is_not_flagged():
    e = Engine(profile=KRX)
    for i in range(3):
        e.apply_mark(DailyMark(date(2026, 1, 1) + timedelta(days=i), {}))
    m = score(e.result, profile=KRX)
    assert not m.experimental


def test_describe_surfaces_authority_and_gaps():
    lines = describe(CRYPTO)
    assert any("시세 권위" in x for x in lines)
    assert any("미해결" in x for x in lines)


# ── 종목 표기 ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "BTC", "BTC/KRW", "BTC-KRW", "KRW-BTC", "BTCKRW", "BTC_USDT", "XBT", "btc",
])
def test_crypto_symbol_spellings_collapse(raw):
    """업비트·바이낸스·코인베이스가 같은 자산을 다르게 적는다."""
    assert CRYPTO.normalize(raw) == "BTC"


def test_equity_symbol_normalization_is_market_specific():
    assert KRX.normalize("A005930") == "005930"
    assert NASDAQ.normalize("aapl") == "AAPL"
    # 주식 규칙을 크립토에 적용하면 안 된다
    assert KRX.normalize("BTC") == "BTC"


# ── 주식에서 미고려됐던 이벤트 ─────────────────────────────────────────────

def test_rename_keeps_the_position_alive():
    """종목코드가 바뀌었는데 놔두면 포지션이 그 자리에서 끊긴다.

    이후 매도가 전부 '보유하지 않은 종목' 으로 거부되어 전략이 망가진다.
    """
    e = Engine()
    e.apply_stance(st(1, "005930", "0.4"), Quote("005930", D(1000)))
    before = e.book.assets()

    e.apply_event(MarketEvent(EventType.RENAME, "005930", T0, to_symbol="999999"))

    assert "005930" not in e.book.positions
    assert "999999" in e.book.positions
    assert abs(e.book.assets() - before) < D("1e-12")

    # 새 코드로 정상 청산된다
    f = e.apply_stance(st(2, "999999", "0"), Quote("999999", D(1000)))
    assert f.admit.value == "accepted"


def test_merge_converts_at_ratio_and_preserves_cost():
    """합병 — A 1주가 B 0.5주로 전환. 원가는 보존되어야 손익이 왜곡되지 않는다."""
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.5"), Quote("AAA", D(1000)))
    qty_before = e.book.positions["AAA"].qty
    cost_before = qty_before * e.book.positions["AAA"].avg_cost

    e.apply_event(MarketEvent(EventType.MERGE, "AAA", T0,
                              to_symbol="BBB", ratio=D("0.5")))

    pos = e.book.positions["BBB"]
    assert pos.qty == qty_before * D("0.5")
    assert abs(pos.qty * pos.avg_cost - cost_before) < D("1e-12")


def test_merge_into_existing_position_averages_cost():
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.3"), Quote("AAA", D(1000)))
    e.apply_stance(st(2, "BBB", "0.3"), Quote("BBB", D(1000)))
    total_cost = sum(p.qty * p.avg_cost for p in e.book.positions.values())

    e.apply_event(MarketEvent(EventType.MERGE, "AAA", T0, to_symbol="BBB", ratio=D(1)))

    assert list(e.book.positions) == ["BBB"]
    pos = e.book.positions["BBB"]
    assert abs(pos.qty * pos.avg_cost - total_cost) < D("1e-12")


def test_rename_without_target_is_ignored():
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.3"), Quote("AAA", D(1000)))
    e.apply_event(MarketEvent(EventType.RENAME, "AAA", T0))   # to_symbol 없음
    assert "AAA" in e.book.positions


def test_profile_supplies_costs_to_engine():
    e = Engine(profile=NASDAQ)
    e.apply_stance(st(1, "AAPL", "0.5"), Quote("AAPL", D(200)))
    e.apply_stance(st(2, "AAPL", "0"), Quote("AAPL", D(200)))
    assert abs(e.book.assets() - D("0.99995")) < D("1e-9")


def test_all_profiles_are_self_consistent():
    for code, p in PROFILES.items():
        assert p.code == code
        assert p.periods_per_year > 0
        assert p.downside_floor_daily > 0
        assert p.min_track_periods > 0
        assert p.price_authority
        assert p.mark_at
        if p.is_experimental:
            assert p.notes, f"{code}: 실험적이면 미해결 항목을 밝혀야 한다"


def test_profile_tax_matches_the_current_statutory_rate():
    """프로파일의 세율이 곧 채점에 쓰이는 값이다.

    models.Costs 기본값만 고치고 프로파일의 하드코딩 값을 놓쳐
    실제로는 옛 세율로 채점되던 적이 있다. 배포 후에야 드러났다.
    """
    assert KRX.costs.tax == D("0.0020")      # 2026-01-01 시행
    assert KRX.costs.sell_fee == D("0.0020")  # 증권사 수수료는 0
    assert Costs().tax == KRX.costs.tax       # 기본값과 프로파일이 어긋나면 안 된다


def test_every_stable_profile_declares_its_tax_explicitly():
    for code, p in PROFILES.items():
        if p.support is Support.STABLE:
            assert p.costs.tax >= 0, code
            assert p.costs.commission == D(0), f"{code}: 증권사 수수료는 반영하지 않는다"
