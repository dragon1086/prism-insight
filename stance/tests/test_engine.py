"""Stance 엔진 검증.

스펙 문서에 적힌 숫자가 코드에서도 그대로 나오는지 확인한다.
문서와 구현이 어긋나면 표준으로서 의미가 없다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from stance.server import (
    Admit, Costs, DailyMark, Engine, EventType, Kind,
    Ledger, MarketEvent, Quote, Stance, replay, score, summary_lines,
)
from stance.client import to_target_weight

UTC = timezone.utc
T0 = datetime(2026, 8, 10, 0, 30, tzinfo=UTC)
D = Decimal


def approx(x, y, tol="1e-9"):
    return abs(Decimal(str(x)) - Decimal(str(y))) <= Decimal(tol)


def st(seq, symbol, w, at=None):
    return Stance(seq=seq, received_at=at or (T0 + timedelta(minutes=seq)),
                  kind=Kind.SET, symbol=symbol, target_weight=D(str(w)))


# ── 코어 회계 ─────────────────────────────────────────────────────────────

def test_worked_example_from_spec():
    """스펙 §6.2 — 목표비중 0.1 진입, 주가 2배, 절반 익절."""
    e = Engine()

    f1 = e.apply_stance(st(1, "005930", "0.1"), Quote("005930", D(10000)))
    assert f1.admit is Admit.ACCEPTED
    assert approx(e.book.cash, "0.9")
    assert approx(e.book.assets(), "1.0")

    # 주가 2배 — 아무것도 선언하지 않아도 비중이 올라간다
    e.apply_mark(DailyMark(date(2026, 8, 11), {"005930": D(20000)}))
    assert approx(e.book.assets(), "1.1")
    w = e.book.weight_of("005930")
    assert approx(w, "0.1818181818181818181818181818", "1e-18")

    # 절반 익절 = 지금 비중의 절반을 목표로 선언한다
    f2 = e.apply_stance(st(2, "005930", w / 2), Quote("005930", D(20000)))
    assert f2.admit is Admit.ACCEPTED
    assert approx(f2.realized_pnl, "0.0498")      # 문서값
    assert approx(e.book.cash, "0.9998")          # 문서값
    assert approx(e.book.assets(), "1.0998")      # 문서값
    # 잔여 평가액 0.1 을 자산 1.0998 로 나눈 값. 매도 비용만큼 분모가 줄어 있으므로
    # 0.1/1.1 (=9.0909%) 이 아니라 9.0926% 다. 반올림하면 문서의 9.09% 와 같다.
    assert approx(e.book.weight_of("005930"), D("0.1") / D("1.0998"), "1e-18")
    assert round(float(e.book.weight_of("005930")) * 100, 2) == 9.09


def test_target_weight_is_market_value_not_cost():
    """스펙 §3-1 — 2배 오른 뒤 0.2 를 보내면 0.02 만 산다."""
    e = Engine()
    e.apply_stance(st(1, "000660", "0.1"), Quote("000660", D(10000)))
    e.apply_mark(DailyMark(date(2026, 8, 11), {"000660": D(20000)}))

    f = e.apply_stance(st(2, "000660", "0.2"), Quote("000660", D(20000)))
    assert approx(f.traded_value, "0.02")         # 문서값 — 의도한 0.11 의 5분의 1

    # 한 칸(자산의 10%)을 제대로 더 넣으려면 28.18% 를 보내야 한다
    e2 = Engine()
    e2.apply_stance(st(1, "000660", "0.1"), Quote("000660", D(10000)))
    e2.apply_mark(DailyMark(date(2026, 8, 11), {"000660": D(20000)}))
    target = to_target_weight(position_value=D("0.2"), total_assets=D("1.1"),
                              add_amount=D("0.11"))
    assert approx(target, "0.2818181818181818", "1e-15")
    f2 = e2.apply_stance(st(2, "000660", target), Quote("000660", D(20000)))
    assert approx(f2.traded_value, "0.11", "1e-15")


def test_exit_is_target_zero():
    e = Engine()
    e.apply_stance(st(1, "005930", "0.5"), Quote("005930", D(1000)))
    f = e.apply_stance(st(2, "005930", "0"), Quote("005930", D(1000)))
    assert f.admit is Admit.ACCEPTED
    assert "005930" not in e.book.positions
    assert approx(e.book.assets(), "0.999")       # 매도 0.5 에 0.20% 비용
    assert e.result.closed_trades == 1


def test_clamp_instead_of_reject():
    """현금이 모자라면 거부하지 않고 줄여서 받는다.

    거부하면 그 종목이 서버 장부에서 통째로 사라지고,
    이후 매도가 '보유하지 않은 종목'으로 연쇄 거부되어 전략이 영구히 망가진다.
    """
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.6"), Quote("AAA", D(100)))
    f = e.apply_stance(st(2, "BBB", "0.6"), Quote("BBB", D(100)))

    assert f.admit is Admit.CLAMPED
    assert approx(f.traded_value, "0.4")          # 남은 현금만큼만
    assert "BBB" in e.book.positions              # 사라지지 않는다
    assert approx(e.book.cash, "0")


def test_no_leverage():
    e = Engine()
    for i, sym in enumerate(["A", "B", "C"], start=1):
        e.apply_stance(st(i, sym, "0.5"), Quote(sym, D(100)))
    assert e.book.cash >= 0
    assert approx(e.book.assets(), "1.0")


def test_untradable_is_rejected():
    """상한가 잠김·거래정지는 현실에서 체결할 수 없다."""
    e = Engine()
    f = e.apply_stance(st(1, "005930", "0.1"),
                       Quote("005930", D(10000), tradable=False))
    assert f.admit is Admit.REJECTED
    assert not e.book.positions


def test_sell_without_position_rejected():
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.1"), Quote("AAA", D(100)))
    f = e.apply_stance(st(2, "BBB", "0"), Quote("BBB", D(100)))
    assert f.admit is Admit.REJECTED


# ── 기업행위 ──────────────────────────────────────────────────────────────

def test_split_does_not_change_assets():
    """액면분할은 수량과 단가가 정확히 상쇄되어 자산에 영향이 없다.

    수정주가 방식이었다면 과거 자산 추이가 전부 다시 계산되어
    이미 발표된 순위가 소급해 바뀐다. 그래서 이벤트로 처리한다.
    """
    e = Engine()
    e.apply_stance(st(1, "005930", "0.5"), Quote("005930", D(50000)))
    before = e.book.assets()

    e.apply_event(MarketEvent(EventType.SPLIT, "005930", T0, ratio=D(50)))

    assert approx(e.book.assets(), before)
    pos = e.book.positions["005930"]
    assert approx(pos.avg_cost, "1000")
    assert approx(e.book.last_price["005930"], "1000")


def test_dividend_adds_cash_after_tax():
    e = Engine()
    e.apply_stance(st(1, "005930", "0.1"), Quote("005930", D(10000)))
    cash_before = e.book.cash
    qty = e.book.positions["005930"].qty          # 0.1/10000 = 1e-5

    e.apply_event(MarketEvent(EventType.DIVIDEND, "005930", T0, per_share=D(500)))

    gross = qty * D(500)                          # 0.005
    assert approx(gross, "0.005")
    assert approx(e.book.cash - cash_before, gross * (D(1) - D("0.154")))
    assert approx(e.book.cash - cash_before, "0.00423")   # 문서값


def test_delist_forces_liquidation():
    e = Engine()
    e.apply_stance(st(1, "BAD", "0.2"), Quote("BAD", D(1000)))
    e.apply_event(MarketEvent(EventType.DELIST, "BAD", T0, final_price=D(100)))
    assert "BAD" not in e.book.positions
    assert e.result.closed_trades == 1
    assert approx(e.book.assets(), "0.81996")     # 0.8 + 0.02*(1-0.002)


def test_halt_freezes_price_and_blocks_trading():
    e = Engine()
    e.apply_stance(st(1, "HLT", "0.3"), Quote("HLT", D(1000)))
    e.apply_event(MarketEvent(EventType.HALT, "HLT", T0))

    # 정지 중에는 시세가 들어와도 평가를 갱신하지 않는다
    e.apply_mark(DailyMark(date(2026, 8, 11), {"HLT": D(9999)}))
    assert approx(e.book.last_price["HLT"], "1000")

    f = e.apply_stance(st(2, "HLT", "0"), Quote("HLT", D(1000)))
    assert f.admit is Admit.REJECTED


# ── hold ──────────────────────────────────────────────────────────────────

def test_hold_records_day_without_changing_book():
    e = Engine()
    e.apply_stance(st(1, "AAA", "0.3"), Quote("AAA", D(100)))
    before = e.book.assets()
    f = e.apply_stance(Stance(seq=2, received_at=T0, kind=Kind.HOLD))
    assert f.admit is Admit.ACCEPTED
    assert approx(e.book.assets(), before)
    assert T0.date() in e.result.declared_days


# ── 원장 ──────────────────────────────────────────────────────────────────

def test_ledger_is_append_only():
    led = Ledger()
    led.register("s1", "테스트", "@me")
    sid = led.append_stance("s1", 1, Kind.SET, "005930", D("0.1"))

    with pytest.raises(sqlite3.Error):
        led.conn.execute("UPDATE stances SET target_weight='0.9' WHERE id=?", (sid,))
    with pytest.raises(sqlite3.Error):
        led.conn.execute("DELETE FROM stances WHERE id=?", (sid,))
    led.close()


def test_hash_chain_detects_tampering():
    led = Ledger()
    led.register("s1", "테스트", "@me")
    for i in range(1, 4):
        led.append_stance("s1", i, Kind.SET, "005930", D("0.1"))
    assert led.verify_chain("stances")

    # 트리거를 우회해 강제로 고쳐도 체인 검증에서 드러난다
    led.conn.execute("DROP TRIGGER stances_no_update")
    led.conn.execute("UPDATE stances SET prev_hash='조작' WHERE seq=2")
    assert not led.verify_chain("stances")
    led.close()


def test_ledger_roundtrip_replays_identically():
    """원장만 있으면 누구든 같은 결과를 재현할 수 있다."""
    led = Ledger()
    led.register("s1", "테스트", "@me")

    for seq, (sym, w, px) in enumerate(
        [("005930", "0.2", 10000), ("000660", "0.3", 5000), ("005930", "0", 12000)], start=1
    ):
        sid = led.append_stance("s1", seq, Kind.SET, sym, D(w),
                                received_at=(T0 + timedelta(minutes=seq)).isoformat())
        led.append_quote(sid, Quote(sym, D(px)))

    r1 = replay(led.timeline("s1"))
    r2 = replay(led.timeline("s1"))
    assert r1.book.cash == r2.book.cash
    assert led.next_seq("s1") == 4
    led.close()


# ── 채점 ──────────────────────────────────────────────────────────────────

def _series(values):
    return [(date(2026, 1, 1) + timedelta(days=i), D(str(v))) for i, v in enumerate(values)]


def test_cash_parking_does_not_win():
    """현금만 들고 미세하게 움직이는 전략이 위험지표 1등을 하면 안 된다.

    하락편차 하한이 0 으로 나누는 것을 막는다.
    하한을 크게 잡으면 정상적인 방어 전략까지 벌하므로 최소한으로만 둔다.
    """
    from stance.server.engine import ReplayResult
    from stance.server.scoring import _sortino

    # 게이밍: 거의 안 움직이고 거의 안 번다
    gaming = [1.0 + 0.00004 * i + (0.00002 if i % 2 else -0.00002) for i in range(250)]
    # 방어형: 현금이 많지만 실제로 수익을 낸다
    defensive = [1.0 + 0.0003 * i + (0.002 if i % 3 else -0.0015) for i in range(250)]

    from stance.server.scoring import _returns
    g = _sortino(_returns(_series(gaming)))
    d = _sortino(_returns(_series(defensive)))

    assert g < d, f"게이밍({g:.2f})이 방어형({d:.2f})을 이기면 안 된다"
    assert g < 3.0, f"하한이 작동하지 않는다: {g:.2f}"


def test_downside_floor_prevents_infinity():
    from stance.server.scoring import _sortino
    flat_up = [0.0001] * 200          # 손실일이 하나도 없다
    s = _sortino(flat_up)
    assert s == s and s != float("inf"), "무한대가 되면 안 된다"
    assert s < 10


def test_gate_requires_three_things_only():
    from stance.server.engine import ReplayResult

    r = ReplayResult(book=Engine().book)
    r.daily_assets = _series([1.0 + 0.001 * i for i in range(70)])
    r.daily_exposure = [(d, D("0.05")) for d, _ in r.daily_assets]   # 투자비중 5%
    r.declared_days = {d for d, _ in r.daily_assets}
    r.closed_trades = r.closed_trades_material = 25

    m = score(r)
    # 투자비중 5% 여도 통과해야 한다 — 현금을 드는 것도 판단이다
    assert m.qualified, m.gate_failures
    assert m.avg_exposure == pytest.approx(0.05)
    assert any("투자비중" in line for line in summary_lines(m))


def test_gate_blocks_insufficient_record():
    from stance.server.engine import ReplayResult

    r = ReplayResult(book=Engine().book)
    r.daily_assets = _series([1.0, 1.01, 1.02])
    r.daily_exposure = [(d, D("0.5")) for d, _ in r.daily_assets]
    r.declared_days = {r.daily_assets[0][0]}

    m = score(r)
    assert not m.qualified
    assert len(m.gate_failures) == 3          # 기간·거래수·제출률 전부 미달


def test_small_trades_do_not_count_toward_gate():
    """투입비중 1% 미만 거래를 반복해 거래 건수만 채우는 것을 막는다."""
    e = Engine()
    seq = 0
    for i in range(30):
        seq += 1
        e.apply_stance(st(seq, "TINY", "0.001"), Quote("TINY", D(100)))
        seq += 1
        e.apply_stance(st(seq, "TINY", "0"), Quote("TINY", D(100)))

    assert e.result.closed_trades == 30
    assert e.result.closed_trades_material == 0


def test_us_costs_differ():
    e = Engine(costs=Costs.for_market("NASDAQ"))
    e.apply_stance(st(1, "AAPL", "0.5"), Quote("AAPL", D(200)))
    e.apply_stance(st(2, "AAPL", "0"), Quote("AAPL", D(200)))
    assert approx(e.book.assets(), "0.99995")     # 0.5 매도에 0.01%
