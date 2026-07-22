# core/swing.py — 라운드6 Lane B: 추세 초입 스윙 레인의 순수 결정 로직
#
# 검증: analysis/round6_swing_lane.py (2020-03~2026-07 n=186, cum +293%,
# maxDD -35%, 3분리기간 모두 양수) + tasks/btc_round6_swing_lane.md.
# TF 전수검증(§4b/4c): 12h/1w 추가는 초입 상실, 30m/1h 추가는 무개선/유해,
# 메인 6-TF 다이어트(M2)는 한계 코호트 무엣지 — 4h 트리거 + 1d 필터로 고정.
#
# 순수함수 원칙 (core/entries.py 와 동일): pandas/DB/네트워크 금지, 스칼라
# 입출력만. 어댑터(live/swing.py)가 DataFrame 을 소유하고 스칼라를 잘라 넣는다.
# 룰 정의는 백테스트 스크립트의 entryB/exitB 와 문자 그대로 동일해야 한다
# (라이브=백테스트 패리티 — tests/test_swing.py 가 고정).
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from engine.config import (
    SWING_MAX_LEVERAGE,
    SWING_RISK_PER_TRADE,
    SWING_STOP_ATR_MULT,
)

Side = Literal["long", "short"]


def detect_cross(ma10_prev: float, ma35_prev: float,
                 ma10: float, ma35: float) -> int:
    """4h MA10/MA35 크로스 감지: +1 골든, -1 데드, 0 없음.

    백테스트 정의 미러: cross_up = (ma10>ma35) & (ma10_prev<=ma35_prev).
    """
    if ma10 > ma35 and ma10_prev <= ma35_prev:
        return 1
    if ma10 < ma35 and ma10_prev >= ma35_prev:
        return -1
    return 0


def entry_side(cross: int, d1_ma10: float, d1_ma35: float,
               close: float, ma35_4h: float) -> Optional[Side]:
    """진입 방향: 크로스 + 완결 1d 봉 방향 일치 + 4h 종가 MA35 순방향.

    백테스트 entryB 미러 — 숏의 1d 조건은 not(ma10>ma35), 즉 ma10<=ma35.
    """
    if cross > 0 and d1_ma10 > d1_ma35 and close > ma35_4h:
        return "long"
    if cross < 0 and d1_ma10 <= d1_ma35 and close < ma35_4h:
        return "short"
    return None


def stop_price(side: Side, entry: float, atr_4h: float) -> float:
    """하드스탑 = 진입가 ∓ SWING_STOP_ATR_MULT × ATR14(4h)."""
    if side == "long":
        return entry - SWING_STOP_ATR_MULT * atr_4h
    return entry + SWING_STOP_ATR_MULT * atr_4h


def rule_exit_due(side: Side, close_4h: float, ma35_4h: float) -> bool:
    """룰 청산: 4h 확정 종가가 MA35 를 역방향 이탈 (백테스트 exitB 미러)."""
    if side == "long":
        return close_4h < ma35_4h
    return close_4h > ma35_4h


def conflicts_with_main(swing_side: Side, main_sides: list[str]) -> bool:
    """메인 레인 보유 포지션과 방향 충돌이면 True (스윙 진입 금지).

    같은 방향 보유는 허용 — 두 레인의 논리적 노출 합산은 사이징 캡이 제한한다.
    """
    return any(s != swing_side for s in main_sides)


@dataclass(frozen=True)
class SwingSizing:
    qty: float
    leverage: float
    risk_amount: float
    rejected: bool
    reject_reason: str = ""


def compute_swing_sizing(equity: float, entry: float, stop: float) -> SwingSizing:
    """리스크 기반 사이징.

    qty = (equity × SWING_RISK_PER_TRADE) / |entry − stop|.
    명목/equity 가 SWING_MAX_LEVERAGE 를 넘으면 수량을 캡한다 (이때 실제
    리스크는 1% 미만으로 줄어든다 — 스탑 가격은 불변).
    """
    if equity <= 0 or entry <= 0:
        return SwingSizing(0.0, 0.0, 0.0, True, "invalid equity/entry")
    sl_dist = abs(entry - stop)
    if sl_dist <= 0:
        return SwingSizing(0.0, 0.0, 0.0, True, "zero stop distance")
    risk_amount = equity * SWING_RISK_PER_TRADE
    qty = risk_amount / sl_dist
    leverage = qty * entry / equity
    if leverage > SWING_MAX_LEVERAGE:
        qty = equity * SWING_MAX_LEVERAGE / entry
        leverage = SWING_MAX_LEVERAGE
        risk_amount = qty * sl_dist
    if qty <= 0:
        return SwingSizing(0.0, 0.0, 0.0, True, "qty<=0")
    return SwingSizing(qty=qty, leverage=leverage,
                       risk_amount=risk_amount, rejected=False)
