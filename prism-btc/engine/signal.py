# engine/signal.py — Rule-based signal generation (§7)
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.regime import RegimeSnapshot, TFState

Side = Literal["long", "short", "none"]

LONG_TFS = ("12h", "1d", "1w")   # 장기 TF — 방향 결정권
SHORT_TFS = ("30m", "1h")         # 단기 TF — 타이밍 결정권 (청산 로직 전용)
# 라운드2 #3: 신규 진입의 "단기 캔들 위치" 판정을 30m/1h → 4h 확정봉으로 상향.
ENTRY_TRIGGER_TF = "4h"

# 단기 캔들 위치 중 롱 진입 허용 패턴
LONG_ENTRY_POSITIONS = frozenset({
    "support_ma10",
    "support_ma35",
    "break_ma10_up",
    "break_ma35_up",
    "above_all",
})

# 단기 캔들 위치 중 숏 진입 허용 패턴
SHORT_ENTRY_POSITIONS = frozenset({
    "resist_ma10",
    "resist_ma35",
    "break_ma10_down",
    "break_ma35_down",
    "below_all",
})

# 청산 신호 판단 - 역방향 캔들 위치
EXIT_SIGNAL_LONG = frozenset({
    "break_ma10_down",
    "break_ma35_down",
    "below_all",
})
EXIT_SIGNAL_SHORT = frozenset({
    "break_ma10_up",
    "break_ma35_up",
    "above_all",
})

# 4h 경고 패턴 (역방향 전환 or break)
WARNING_LONG = frozenset({
    "break_ma10_down",
    "break_ma35_down",
    "below_all",
    "resist_ma10",
    "resist_ma35",
})
WARNING_SHORT = frozenset({
    "break_ma10_up",
    "break_ma35_up",
    "above_all",
    "support_ma10",
    "support_ma35",
})


@dataclass
class Signal:
    side: Side
    strength: float          # |alignment_score|, 0~100
    reason: str
    exit_action: Literal["hold", "reduce", "exit"] = "hold"


def _long_tf_direction_positive(tf_states: dict[str, TFState]) -> bool:
    """장기TF(12h/1d/1w) 가중 방향이 순 양수인지 확인."""
    from engine.config import TF_WEIGHTS
    raw = 0.0
    for tf in LONG_TFS:
        state = tf_states.get(tf)
        if state is None:
            continue
        w = TF_WEIGHTS.get(tf, 0)
        if state.trend == "up":
            raw += w
        elif state.trend == "down":
            raw -= w
    return raw > 0


def _long_tf_direction_negative(tf_states: dict[str, TFState]) -> bool:
    """장기TF(12h/1d/1w) 가중 방향이 순 음수인지 확인."""
    from engine.config import TF_WEIGHTS
    raw = 0.0
    for tf in LONG_TFS:
        state = tf_states.get(tf)
        if state is None:
            continue
        w = TF_WEIGHTS.get(tf, 0)
        if state.trend == "up":
            raw += w
        elif state.trend == "down":
            raw -= w
    return raw < 0


def _short_tfs_aligned(
    tf_states: dict[str, TFState],
    allowed_positions: frozenset[str],
) -> bool:
    """단기 TF(30m/1h) 모두 허용 캔들 위치인지 확인."""
    for tf in SHORT_TFS:
        state = tf_states.get(tf)
        if state is None:
            return False
        if state.candle_position not in allowed_positions:
            return False
    return True


def _entry_tf_aligned(
    tf_states: dict[str, TFState],
    entry_tf: str,
    allowed_positions: frozenset[str],
) -> bool:
    """진입 트리거 TF(라운드2 #3: 4h)의 캔들 위치가 허용 패턴인지 확인."""
    state = tf_states.get(entry_tf)
    if state is None:
        return False
    return state.candle_position in allowed_positions


def trend_strength(state: TFState) -> float:
    """추세강도 = |MA10 - MA35| / ATR14 (라운드2 #1 횡보 필터).

    ATR14 가 0/음수면 측정 불가로 0.0 (게이트 통과 불가).
    """
    if state.atr14 <= 0:
        return 0.0
    return abs(state.ma10 - state.ma35) / state.atr14


def chop_filter_passed(tf_states: dict[str, TFState]) -> bool:
    """추세강도 게이트: TS_GATE_TFS(4h·1d) 두 TF 모두 trend_strength >= TS_MIN.

    하나라도 미달이거나 상태가 없으면 횡보로 보고 신규 진입 금지.
    보유 포지션 관리에는 영향을 주지 않는다(호출처에서 진입에만 사용).
    """
    from engine.config import TS_MIN, TS_GATE_TFS

    for tf in TS_GATE_TFS:
        state = tf_states.get(tf)
        if state is None:
            return False
        if trend_strength(state) < TS_MIN:
            return False
    return True


def generate_signal(snapshot: RegimeSnapshot) -> Signal:
    """
    RegimeSnapshot → Signal.

    롱: alignment_score >= +ENTRY_SCORE_MIN AND 장기TF 가중 방향 양수 AND 단기(30m/1h) 지지/돌파
    숏: 대칭
    |score| < ENTRY_SCORE_MIN → none
    """
    from engine.config import ENTRY_SCORE_MIN

    score = snapshot.alignment_score
    tf_states = snapshot.tf_states
    abs_score = abs(score)

    if abs_score < ENTRY_SCORE_MIN:
        return Signal(side="none", strength=abs_score,
                      reason=f"score={score:.1f} < {ENTRY_SCORE_MIN:.0f}, 횡보관망")

    # 라운드2 #1: 횡보 필터 — 4h·1d 추세강도 게이트. 둘 다 통과해야 신규 진입.
    if not chop_filter_passed(tf_states):
        return Signal(side="none", strength=abs_score, reason="추세강도 미달(횡보 게이트)")

    if score >= ENTRY_SCORE_MIN:
        # 롱 후보
        if not _long_tf_direction_positive(tf_states):
            return Signal(side="none", strength=abs_score, reason="장기TF 방향 미정렬(롱)")
        # 라운드2 #3: 진입 캔들 위치 판정을 4h 확정봉 기준으로.
        if not _entry_tf_aligned(tf_states, ENTRY_TRIGGER_TF, LONG_ENTRY_POSITIONS):
            return Signal(side="none", strength=abs_score, reason="4h 타이밍 미충족(롱)")
        return Signal(side="long", strength=abs_score, reason=f"롱신호 score={score:.1f}")

    # score <= -ENTRY_SCORE_MIN
    if not _long_tf_direction_negative(tf_states):
        return Signal(side="none", strength=abs_score, reason="장기TF 방향 미정렬(숏)")
    if not _entry_tf_aligned(tf_states, ENTRY_TRIGGER_TF, SHORT_ENTRY_POSITIONS):
        return Signal(side="none", strength=abs_score, reason="4h 타이밍 미충족(숏)")
    return Signal(side="short", strength=abs_score, reason=f"숏신호 score={score:.1f}")


def check_exit_signal(
    snapshot: RegimeSnapshot,
    position_side: Side,
) -> Signal:
    """
    보유 포지션 중 청산 신호 확인.

    단기(30m+1h) 추세 역전 AND 4h 경고 → reduce
    장기 정렬 반전 → exit
    """
    if position_side == "none":
        return Signal(side="none", strength=0, reason="no position", exit_action="hold")

    tf_states = snapshot.tf_states
    score = snapshot.alignment_score

    # 장기 정렬 반전 검사
    if position_side == "long" and score <= -40 and _long_tf_direction_negative(tf_states):
        return Signal(side=position_side, strength=abs(score), reason="장기정렬반전→exit", exit_action="exit")
    if position_side == "short" and score >= 40 and _long_tf_direction_positive(tf_states):
        return Signal(side=position_side, strength=abs(score), reason="장기정렬반전→exit", exit_action="exit")

    # 단기 역전 + 4h 경고
    # P1-1: 추세를 길게 타기 위해 청산 민감도 완화. 30m 단독 역전으로는 reduce 금지 —
    # 1h 확정 역전이 필수 조건. (1h가 반전돼야만 단기역전으로 인정)
    four_h_warning = False

    one_h_state = tf_states.get("1h")
    one_h_reversed = False
    if one_h_state is not None:
        if position_side == "long" and one_h_state.candle_position in EXIT_SIGNAL_LONG:
            one_h_reversed = True
        elif position_side == "short" and one_h_state.candle_position in EXIT_SIGNAL_SHORT:
            one_h_reversed = True

    # 1h 확정 역전이 없으면 단기역전으로 보지 않는다 (30m 단독 무시)
    short_reversed = one_h_reversed

    four_h_state = tf_states.get("4h")
    if four_h_state is not None:
        if position_side == "long" and four_h_state.candle_position in WARNING_LONG:
            four_h_warning = True
        elif position_side == "short" and four_h_state.candle_position in WARNING_SHORT:
            four_h_warning = True
        # 4h 추세 자체가 역전된 경우도 경고
        if position_side == "long" and four_h_state.trend == "down":
            four_h_warning = True
        elif position_side == "short" and four_h_state.trend == "up":
            four_h_warning = True

    if short_reversed and four_h_warning:
        return Signal(
            side=position_side,
            strength=abs(score),
            reason="단기역전+4h경고→reduce",
            exit_action="reduce",
        )

    return Signal(side=position_side, strength=abs(score), reason="hold", exit_action="hold")
