"""다양한 환경의 클라이언트가 붙었을 때 불합리한 일이 생기지 않는지 검증한다.

각 테스트는 실제로 있을 법한 시스템 유형 하나를 대입한 것이다.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D

import pytest

from stance.server import (
    Admit, Cadence, Costs, DailyMark, Engine, Kind, Ledger,
    Quote, Stance, normalize_symbol, score, summary_lines,
)

UTC = timezone.utc
T0 = datetime(2026, 8, 10, 0, 30, tzinfo=UTC)


def st(seq, symbol=None, w=None, kind=Kind.SET, at=None):
    return Stance(seq=seq, received_at=at or (T0 + timedelta(minutes=seq)),
                  kind=kind, symbol=symbol,
                  target_weight=D(str(w)) if w is not None else None)


def series(n, start=date(2026, 1, 1)):
    return [start + timedelta(days=i) for i in range(n)]


# ── 서버 장애가 참여자를 벌하면 안 된다 ────────────────────────────────────

def test_missing_quote_is_pending_not_rejected():
    """시세 소스가 죽은 것은 서버 책임이다. 선언은 살아 있어야 한다."""
    e = Engine()
    f = e.apply_stance(st(1, "005930", "0.1"), quote=None)

    assert f.admit is Admit.PENDING
    assert f.admit is not Admit.REJECTED
    assert len(e.result.pending) == 1
    # 장부는 건드리지 않는다 — 나중에 시세를 확보해 확정한다
    assert not e.book.positions


def test_zero_price_is_also_pending():
    e = Engine()
    f = e.apply_stance(st(1, "005930", "0.1"), Quote("005930", D(0)))
    assert f.admit is Admit.PENDING


def test_pending_count_surfaces_in_scorecard():
    e = Engine()
    e.apply_stance(st(1, "005930", "0.1"), quote=None)
    e.apply_mark(DailyMark(date(2026, 1, 1), {}))
    e.apply_mark(DailyMark(date(2026, 1, 2), {}))

    m = score(e.result)
    assert m.pending == 1
    assert any("시세 미확보" in line for line in summary_lines(m))


# ── 매일 돌지 않는 시스템 ──────────────────────────────────────────────────

@pytest.mark.parametrize("cadence,every", [
    (Cadence.DAILY, 1),
    (Cadence.WEEKLY, 5),     # 거래일 기준 한 주
    (Cadence.MONTHLY, 21),   # 거래일 기준 한 달
])
def test_non_daily_systems_are_not_punished(cadence, every):
    """주간·월간 배치로 도는 시스템은 hold 조차 매일 보낼 수 없다.

    거래일 기준으로 제출률을 재면 주간 20%, 월간 5% 가 나와 전멸한다.
    각자 밝힌 주기 대비로 재야 한다.
    """
    e = Engine()
    days = series(120)
    for i, d in enumerate(days):
        if i % every == 0:
            e.apply_stance(st(i + 1, kind=Kind.HOLD,
                              at=datetime(d.year, d.month, d.day, 1, tzinfo=UTC)))
        e.apply_mark(DailyMark(d, {}))

    m = score(e.result, cadence=cadence)
    assert m.coverage >= 0.95, f"{cadence.value}: {m.coverage:.0%}"


def test_event_driven_system_is_not_measured_by_coverage():
    """신호가 나올 때만 도는 시스템에는 기대 주기가 없다."""
    e = Engine()
    for i, d in enumerate(series(120)):
        e.apply_mark(DailyMark(d, {}))
    m = score(e.result, cadence=Cadence.EVENT)
    assert m.coverage == 1.0


def test_coverage_is_not_a_gate():
    """제출률이 0 이어도 자격을 막지 않는다.

    제출률이 막으려던 조작('지는 날은 생략')은 목표비중 방식에서
    구조적으로 불가능하다 — 생략하면 포지션이 유지되어 손실이 그대로 반영된다.
    """
    e = Engine()
    for i, d in enumerate(series(70)):
        e.apply_mark(DailyMark(d, {}))
    e.result.closed_trades_material = 25

    m = score(e.result)
    assert m.coverage == 0.0
    assert m.qualified, m.gate_failures
    assert not any("제출률" in f for f in m.gate_failures)


def test_skipping_declarations_does_not_avoid_losses():
    """위 주장을 실제로 확인한다 — 선언을 끊어도 손실은 온전히 반영된다."""
    e = Engine()
    e.apply_stance(st(1, "AAA", "1.0"), Quote("AAA", D(1000)))
    for i, d in enumerate(series(30)):
        e.apply_mark(DailyMark(d, {"AAA": D(1000 - i * 20)}))   # 계속 하락

    m = score(e.result)
    assert m.cumulative_return < -0.4
    assert len(e.result.declared_days) == 1     # 첫날 말고는 아무것도 안 보냈다


# ── 시스템 점검·휴가 ───────────────────────────────────────────────────────

def test_pause_exempts_coverage_but_not_returns():
    """중단을 밝히면 제출률에서 빠진다. 그러나 자산 추이는 그대로 계산된다.

    이 구분이 중요하다. 수익까지 면제하면 하락장에 pause 를 걸어
    손실을 피하는 공짜 풋옵션이 된다.
    """
    e = Engine()
    e.apply_stance(st(1, "AAA", "1.0"), Quote("AAA", D(1000)))
    e.apply_stance(st(2, kind=Kind.PAUSE))

    for i, d in enumerate(series(20)):
        e.apply_mark(DailyMark(d, {"AAA": D(1000 - i * 30)}))   # 중단 중 폭락

    m = score(e.result)
    assert m.paused_days == 20
    assert m.cumulative_return < -0.4, "중단 중에도 손실은 그대로 반영되어야 한다"


def test_declaration_auto_resumes():
    e = Engine()
    e.apply_stance(st(1, kind=Kind.PAUSE))
    assert e.paused
    e.apply_stance(st(2, "AAA", "0.1"), Quote("AAA", D(100)))
    assert not e.paused


def test_explicit_resume():
    e = Engine()
    e.apply_stance(st(1, kind=Kind.PAUSE))
    e.apply_stance(st(2, kind=Kind.RESUME))
    assert not e.paused


# ── 종목 표기 차이 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["005930", "A005930", "005930.KS", " 005930 ", "5930"])
def test_symbol_spellings_collapse_to_one_position(raw):
    """같은 종목이 다른 표기로 들어와 포지션이 둘로 갈리면 안 된다."""
    assert normalize_symbol(raw, "KRX") == "005930"


def test_mixed_spellings_do_not_split_position():
    e = Engine()
    e.apply_stance(st(1, "005930", "0.2"), Quote("005930", D(1000)))
    e.apply_stance(st(2, "A005930", "0.4"), Quote("A005930", D(1000)))
    assert list(e.book.positions) == ["005930"]


def test_empty_symbol_rejected():
    with pytest.raises(ValueError):
        normalize_symbol("", "KRX")


# ── 비용 정책 ─────────────────────────────────────────────────────────────

def test_only_statutory_tax_is_charged():
    """법정 거래세만 반영한다. 증권사 수수료는 회사마다 달라 넣지 않는다."""
    c = Costs()
    assert c.commission == D(0)
    assert c.sell_fee == c.tax


def test_crypto_has_no_statutory_tax():
    c = Costs.for_market("CRYPTO")
    assert c.sell_fee == D(0)


def test_turnover_is_penalised_so_churn_is_visible():
    """회전율이 높으면 세금이 실제로 쌓여야 한다.

    비용을 빼면 고회전 전략에게 공짜 우위를 주게 된다.
    '얼마나 자주 거래할 것인가' 는 집행이 아니라 판단이므로 반영해야 한다.
    """
    def churn(rounds):
        e = Engine()
        seq = 0
        for _ in range(rounds):
            seq += 1
            e.apply_stance(st(seq, "AAA", "1.0"), Quote("AAA", D(1000)))
            seq += 1
            e.apply_stance(st(seq, "AAA", "0"), Quote("AAA", D(1000)))
        return e.book.assets()

    few, many = churn(3), churn(30)
    assert many < few
    assert float(few / many - 1) > 0.04     # 27회 추가 왕복이 4%p 이상 차이를 만든다


# ── 원장 ──────────────────────────────────────────────────────────────────

def test_ledger_accepts_pause_and_cadence():
    led = Ledger()
    led.register("s1", "주간 리밸런서", "@me", cadence=Cadence.WEEKLY)
    assert led.cadence_of("s1") is Cadence.WEEKLY

    led.append_stance("s1", 1, Kind.PAUSE)
    led.append_stance("s1", 2, Kind.RESUME)
    assert led.verify_chain("stances")

    kinds = [r["kind"] for r in led.conn.execute("SELECT kind FROM stances ORDER BY id")]
    assert kinds == ["pause", "resume"]
    led.close()


def test_cadence_defaults_to_daily():
    led = Ledger()
    led.register("s1", "기본", "@me")
    assert led.cadence_of("s1") is Cadence.DAILY
    led.close()
