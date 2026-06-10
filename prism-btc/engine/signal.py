# engine/signal.py — Rule-based signal generation (§7)
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from engine.regime import RegimeSnapshot, TFState

Side = Literal["long", "short", "none"]

LONG_TFS = ("12h", "1d", "1w")   # 장기 TF — 방향 결정권
SHORT_TFS = ("30m", "1h")         # 단기 TF — 타이밍 결정권

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


def generate_signal(snapshot: RegimeSnapshot) -> Signal:
    """
    RegimeSnapshot → Signal.

    롱: alignment_score >= +40 AND 장기TF 가중 방향 양수 AND 단기(30m/1h) 지지/돌파
    숏: 대칭
    |score| < 40 → none
    """
    score = snapshot.alignment_score
    tf_states = snapshot.tf_states
    abs_score = abs(score)

    if abs_score < 40:
        return Signal(side="none", strength=abs_score, reason=f"score={score:.1f} < 40, 횡보관망")

    if score >= 40:
        # 롱 후보
        if not _long_tf_direction_positive(tf_states):
            return Signal(side="none", strength=abs_score, reason="장기TF 방향 미정렬(롱)")
        if not _short_tfs_aligned(tf_states, LONG_ENTRY_POSITIONS):
            return Signal(side="none", strength=abs_score, reason="단기TF 타이밍 미충족(롱)")
        return Signal(side="long", strength=abs_score, reason=f"롱신호 score={score:.1f}")

    # score <= -40
    if not _long_tf_direction_negative(tf_states):
        return Signal(side="none", strength=abs_score, reason="장기TF 방향 미정렬(숏)")
    if not _short_tfs_aligned(tf_states, SHORT_ENTRY_POSITIONS):
        return Signal(side="none", strength=abs_score, reason="단기TF 타이밍 미충족(숏)")
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
    short_reversed = False
    four_h_warning = False

    short_tfs_reversed_count = 0
    for tf in SHORT_TFS:
        state = tf_states.get(tf)
        if state is None:
            continue
        if position_side == "long" and state.candle_position in EXIT_SIGNAL_LONG:
            short_tfs_reversed_count += 1
        elif position_side == "short" and state.candle_position in EXIT_SIGNAL_SHORT:
            short_tfs_reversed_count += 1

    short_reversed = short_tfs_reversed_count >= len(SHORT_TFS)

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
