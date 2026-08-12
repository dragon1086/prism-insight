"""Stance 프로토콜 — 전체 흐름 데모.

원장에 선언을 쌓고 → 장부를 재구성하고 → 채점해서 성적표를 뽑는다.
외부 의존성이 없다. 그대로 실행하면 된다.

    python3 stance/demo.py
"""

from __future__ import annotations

import random
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal as D
from pathlib import Path

# 직접 실행하면 stance/ 가 sys.path 에 오르므로 저장소 루트를 추가한다
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stance.server import (
    Costs, DailyMark, Engine, EventType, Kind, Ledger,
    MarketEvent, Quote, Stance, score, summary_lines,
)

UTC = timezone.utc
START = datetime(2026, 1, 5, 0, 30, tzinfo=UTC)
SYMBOLS = ["005930", "000660", "035420", "051910"]


def price_paths(days: int, seed: int) -> dict[str, list[D]]:
    """재현 가능한 가상 시세."""
    # 재현 가능한 데모 시세용이다. 키·토큰 등 보안 난수를 만들지 않는다.
    rng = random.Random(seed)  # noqa: S311  # nosec B311
    out = {}
    for s in SYMBOLS:
        px, path = 10_000.0, []
        for _ in range(days):
            px *= 1 + rng.gauss(0.0006, 0.018)
            path.append(D(str(round(px, 2))))
        out[s] = path
    return out


def run_strategy(name: str, decide, days: int = 80, seed: int = 7) -> None:
    """decide(day, engine, prices) -> [(symbol, target_weight, reason)] 또는 None(=hold)"""
    paths = price_paths(days, seed)
    led = Ledger()
    led.register(name, name, "@demo", market="KRX", currency="KRW")

    engine = Engine(costs=Costs.for_market("KRX"))
    seq = 0

    for day in range(days):
        d = date(2026, 1, 5) + timedelta(days=day)
        at = START + timedelta(days=day)
        today = {s: paths[s][day] for s in SYMBOLS}

        calls = decide(day, engine, today)
        if not calls:
            seq += 1
            sid = led.append_stance(name, seq, Kind.HOLD, received_at=at.isoformat())
            engine.apply_stance(Stance(seq=seq, received_at=at, kind=Kind.HOLD))
        else:
            for symbol, weight, reason in calls:
                seq += 1
                q = Quote(symbol, today[symbol], observed_at=at)
                sid = led.append_stance(name, seq, Kind.SET, symbol, D(str(weight)),
                                        reason=reason, received_at=at.isoformat())
                led.append_quote(sid, q)
                engine.apply_stance(
                    Stance(seq=seq, received_at=at, kind=Kind.SET,
                           symbol=symbol, target_weight=D(str(weight)), reason=reason),
                    q,
                )

        engine.apply_mark(DailyMark(d, today))

    m = score(engine.result)
    print(f"\n┌─ {name} " + "─" * max(0, 52 - len(name)))
    for line in summary_lines(m):
        print("│ " + line)
    print("└" + "─" * 58)
    if not led.verify_chain("stances"):
        raise RuntimeError("해시체인 검증 실패")
    led.close()


# ── 세 가지 성격의 전략 ────────────────────────────────────────────────────

def slot_strategy(day, engine, prices):
    """PRISM 식 — 고정 비중 슬랏으로 진입하고 목표 도달 시 청산."""
    calls = []
    for s in SYMBOLS:
        held = engine.book.weight_of(s) > 0
        if not held and day % 7 == SYMBOLS.index(s) and len(engine.book.positions) < 4:
            total = engine.book.assets()
            pv = D(0)
            calls.append((s, (pv + total * D("0.2")) / total, "슬랏 진입"))
        elif held:
            pos = engine.book.positions[s]
            if prices[s] > pos.avg_cost * D("1.12"):
                calls.append((s, 0, "목표 도달 청산"))
            elif prices[s] < pos.avg_cost * D("0.93"):
                calls.append((s, 0, "손절"))
    return calls


def cash_parking(day, engine, prices):
    """게이밍 시도 — 현금만 들고 아주 작은 매매를 매일 반복한다."""
    s = SYMBOLS[day % len(SYMBOLS)]
    if engine.book.weight_of(s) > 0:
        return [(s, 0, "미세 청산")]
    return [(s, "0.004", "미세 진입")]


def buy_and_hold(day, engine, prices):
    if day == 0:
        return [(s, "0.24", "균등 매수") for s in SYMBOLS]
    return None


if __name__ == "__main__":
    print("=" * 60)
    print("  Stance 프로토콜 데모 — 원장 → 장부 재구성 → 채점")
    print("=" * 60)

    run_strategy("슬랏형 (PRISM 식)", slot_strategy)
    run_strategy("현금파킹 (게이밍 시도)", cash_parking)
    run_strategy("매수후보유", buy_and_hold)

    print("\n※ 현금파킹은 위험지표가 좋아 보여도 평균 투자비중이 함께 표시되므로")
    print("   '노출 얼마로 낸 점수인지'가 한눈에 드러난다. 게이트로 막지 않고 드러낸다.")

    # 기업행위가 자산에 영향을 주지 않는지 확인
    e = Engine()
    e.apply_stance(
        Stance(seq=1, received_at=START, kind=Kind.SET, symbol="005930", target_weight=D("0.5")),
        Quote("005930", D(50_000)),
    )
    before = e.book.assets()
    e.apply_event(MarketEvent(EventType.SPLIT, "005930", START, ratio=D(50)))
    print(f"\n액면분할 50:1 전후 자산  {before} → {e.book.assets()}  (변동 없어야 정상)")
