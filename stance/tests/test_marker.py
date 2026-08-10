"""하루 마감 — 채점의 시간축을 만드는 작업.

이것이 돌지 않으면 자산 추이가 비어 운영일수·투자비중·하락위험 지표가 전부 0 이 된다.
즉 리더보드가 영원히 죽어 있다. 실 서버 검증에서 실제로 발견된 구멍이라 테스트로 고정한다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from decimal import Decimal as D

import pytest

from stance.server import Kind, Ledger, Quote, close_day, held_symbols
from stance.server.leaderboard import build
from stance.server.service import StanceService


def prices(**kw):
    table = {k: D(str(v)) for k, v in kw.items()}
    return lambda market, symbol: (
        Quote(symbol, table[symbol]) if symbol in table else None
    )


@pytest.fixture
def svc():
    s = StanceService(ledger=Ledger(), quote_provider=prices(AAA=1000, BBB=2000))
    s.register("s1", "테스트 전략", "@me", market="KRX")
    return s


# ── 없으면 리더보드가 죽는다 ──────────────────────────────────────────────

def test_without_marking_every_metric_is_zero(svc):
    """마감이 없으면 지표가 전부 0 이다 — 실 서버에서 실제로 발생했던 상태."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")

    m = svc.metrics("s1")
    assert m.trading_days == 0
    assert m.avg_exposure == 0.0        # 실제로는 50% 를 들고 있는데도
    assert svc.portfolio("s1")["invested_ratio"] == pytest.approx(0.5)


def test_marking_gives_the_scoring_a_time_axis(svc):
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")

    # 마감은 선언 이후 날짜여야 한다. 과거로 찍으면 그 시점 장부는 비어 있다.
    for i in range(3):
        close_day(svc.ledger, "KRX", prices(AAA=1000 + i * 10),
                  on=date.today() + timedelta(days=i))

    svc._engines.clear()                # 원장에서 다시 만든다
    m = svc.metrics("s1")
    assert m.trading_days == 3
    assert m.avg_exposure == pytest.approx(0.5, abs=0.02)


# ── 마감 동작 ─────────────────────────────────────────────────────────────

def test_held_symbols_covers_positions_without_recent_stances(svc):
    """보유는 선언과 무관하게 이어진다. 선언이 없던 종목도 마감 대상이다."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.3")
    svc.submit("s1", 2, symbol="BBB", target_weight="0.3")
    svc.submit("s1", 3, symbol="AAA", target_weight="0")   # 청산

    assert held_symbols(svc.ledger, "KRX") == {"BBB"}


def test_same_day_is_not_closed_twice(svc):
    """원장은 고칠 수 없으므로 잘못 마감하면 되돌릴 방법이 없다."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    day = date(2026, 1, 5)

    first = close_day(svc.ledger, "KRX", prices(AAA=1000), on=day)
    second = close_day(svc.ledger, "KRX", prices(AAA=9999), on=day)

    assert first["skipped"] is False
    assert second["skipped"] is True
    assert len(svc.ledger.daily_marks("KRX")) == 1


def test_missing_close_does_not_skip_the_day(svc):
    """종가를 못 구한 종목이 있어도 마감은 진행한다.

    건너뛰면 시간축에 구멍이 나고, 구멍은 나중에 메울 수 없다(원장이 불변이므로).
    """
    svc.submit("s1", 1, symbol="AAA", target_weight="0.3")
    svc.submit("s1", 2, symbol="BBB", target_weight="0.3")

    result = close_day(svc.ledger, "KRX", prices(AAA=1000), on=date(2026, 1, 5))

    assert result["skipped"] is False
    assert result["marked"] == 1
    assert result["missing"] == ["BBB"]


def test_marks_are_sealed_in_the_ledger(svc):
    """종가가 원장에 있어야 제3자가 순위를 독립 재현할 수 있다."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    close_day(svc.ledger, "KRX", prices(AAA=1234), on=date(2026, 1, 5))

    marks = svc.ledger.daily_marks("KRX")
    assert marks[0].prices["AAA"] == D(1234)
    assert svc.ledger.verify_chain("daily_marks")

    with pytest.raises(sqlite3.Error):
        svc.ledger.conn.execute("UPDATE daily_marks SET prices='{}'")
    with pytest.raises(sqlite3.Error):
        svc.ledger.conn.execute("DELETE FROM daily_marks")


# ── 타임라인 병합 ─────────────────────────────────────────────────────────

def test_mark_comes_after_that_days_stances(svc):
    """같은 날에는 선언이 먼저, 마감이 나중이다.

    그날의 선언이 모두 반영된 뒤 자산을 찍어야 한다.
    """
    svc.submit("s1", 1, symbol="AAA", target_weight="0.4")
    today = svc.ledger.timeline("s1")[0][0].received_at.date()
    close_day(svc.ledger, "KRX", prices(AAA=1000), on=today)

    items = svc.ledger.full_timeline("s1")
    assert isinstance(items[0], tuple)          # 선언
    assert not isinstance(items[-1], tuple)     # 마감


def test_replay_from_ledger_reproduces_the_same_scores(svc):
    """원장만으로 같은 결과가 나와야 한다 — 재현 가능성의 핵심."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    for i in range(5):
        close_day(svc.ledger, "KRX", prices(AAA=1000 + i * 20),
                  on=date.today() + timedelta(days=i))

    a = build(svc.ledger, [("s1", "테스트 전략", "@me", "KRX")])
    b = build(svc.ledger, [("s1", "테스트 전략", "@me", "KRX")])
    assert a["boards"]["KRX"]["entries"] == b["boards"]["KRX"]["entries"]
    assert a["boards"]["KRX"]["entries"][0]["metrics"]["trading_days"] == 5


def test_leaderboard_shows_real_exposure_after_marking(svc):
    svc.submit("s1", 1, symbol="AAA", target_weight="0.6")
    for i in range(4):
        close_day(svc.ledger, "KRX", prices(AAA=1000),
                  on=date.today() + timedelta(days=i))

    entry = build(svc.ledger, [("s1", "테스트 전략", "@me", "KRX")])["boards"]["KRX"]["entries"][0]
    assert entry["metrics"]["avg_exposure"] == pytest.approx(0.6, abs=0.01)
    assert entry["metrics"]["trading_days"] == 4


# ── 시장 캘린더 ───────────────────────────────────────────────────────────

def test_holiday_is_not_closed(svc):
    """휴장일에 마감하면 그날이 거래일로 박혀 운영일수와 연율화가 부풀려진다."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    holiday = date(2026, 1, 1)

    result = close_day(svc.ledger, "KRX", prices(AAA=1000), on=holiday,
                       is_trading_day=lambda d: False)

    assert result["skipped"] is True
    assert result["reason"] == "not_a_trading_day"
    assert svc.ledger.daily_marks("KRX") == []


def test_trading_day_is_closed(svc):
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    result = close_day(svc.ledger, "KRX", prices(AAA=1000),
                       on=date.today(), is_trading_day=lambda d: True)
    assert result["skipped"] is False


def test_without_calendar_every_day_is_a_trading_day(svc):
    """캘린더는 주입 대상이다. 주지 않으면 매일이 거래일로 취급된다."""
    svc.submit("s1", 1, symbol="AAA", target_weight="0.5")
    result = close_day(svc.ledger, "KRX", prices(AAA=1000), on=date(2026, 1, 1))
    assert result["skipped"] is False
