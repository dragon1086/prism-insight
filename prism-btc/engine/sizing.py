# engine/sizing.py — Leverage & position sizing (§6, §9)
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Constants (all tuneable here without touching logic)
# ---------------------------------------------------------------------------

# G-2: 메인 리스크 3%→5% 상향. 2022~2025 연속 복리 재검증 결과
# MDD 16.2%, PF 1.924, 청산 접근 0건. 레버리지 정책은 10배 고정이다.
# Validated G-2 sizing: 5% of equity per full position lifecycle.  The
# tranche fractions still split this across 40/30/30 pyramid legs.
RISK_PER_TRADE: float = 0.05
MMR: float = 0.005                    # Bybit isolated MMR approximation (0.5%)

# 레버리지 정책: 점수/ATR에 따라 8~12x를 흔들던 정책 대신 운영에서는 10x를
# 고정한다. 포지션 수량은 손절거리와 RISK_PER_TRADE로 계산되므로, 고정 배율은
# 방향/확신 점수에 따라 베팅을 몰래 키우지 않고 청산거리만 예측 가능하게 만든다.
# 연구용 동적 정책을 되살릴 수 있도록 기존 밴드 상수는 아래에 남겨두되 기본
# LEVERAGE_MODE는 fixed다.
LEVERAGE_MODE: str = "fixed"
FIXED_LEVERAGE: float = 10.0

# Legacy dynamic leverage bands (used only when LEVERAGE_MODE="dynamic").
LEV_BAND_HIGH_MIN: float = 80.0       # |score| >= 80 → upper sub-range
LEV_HIGH_LOW: float = 11.0
LEV_HIGH_HIGH: float = 12.0

LEV_BAND_MID_MIN: float = 60.0       # 60 <= |score| < 80
LEV_MID_LOW: float = 10.0
LEV_MID_HIGH: float = 11.0

LEV_BAND_LOW_MIN: float = 40.0       # 40 <= |score| < 60
LEV_LOW_LOW: float = 8.0
LEV_LOW_HIGH: float = 10.0

# ATR volatility cap: if ATR(14,1h)/close > this threshold → cap leverage.
ATR_HIGH_THRESHOLD: float = 0.025     # 2.5% ATR/close ratio
LEV_ATR_CAP: float = 10.0

# 멀티에셋 R1: 변동성 연동 청산거리 규칙 — liq 거리(≈1/lev)가 항상
# 12×ATR(14,1h) 이상이 되도록 레버리지 천장을 연속적으로 제한.
# lev ≤ 1 / (12 × atr_ratio). 자산별 튜닝이 아닌 단일 전역 규칙:
# BTC 평상시(1h ATR ~0.5%)엔 천장 16x로 밴드(8~12x)에 안 걸리고,
# 고변동(ETH·급락장 1h ATR 1%+)에선 8.3x 이하로 자동 축소된다.
# 근거: ETH 교차검증에서 liq_approach 5회(2023) — BTC 변동성 기준 밴드가
# 더 거친 자산에서 청산거리를 좁히는 문제의 구조적 해법. 스윕 없음.
LIQ_ATR_MULT: float = 12.0

# Liquidation safety: liquidation distance must be at least this multiple of the
# structural stop distance. The old 65%-of-gap rule was equivalent to requiring
# roughly 2.86x liquidation-distance/stop-distance, which rejected the recent
# 70-score long despite a valid signal. 1.20x still leaves a positive cushion,
# while allowing wide, volatility-aware stops to trade at fixed 10x.
LIQ_TO_SL_MIN_RATIO: float = 1.20
# Backward-compatible name for research/tests that imported the old constant.
# It is no longer used by the production buffer calculation.
LIQ_BUFFER_MIN_FRAC: float = 0.65
LEV_FLOOR_BUFFER: float = 8.0        # legacy dynamic-mode floor

# Pyramid tranches
TRANCHE_FRACS: tuple[float, ...] = (0.40, 0.30, 0.30)  # 40% / 30% / 30%
MAX_TRANCHES: int = 3


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class SizingResult:
    leverage: float
    qty: float                  # contracts (nominal qty = qty * price)
    sl_price: float
    tp1_price: float            # 1R
    tp2_price: float            # 2R
    tp3_price: float            # 3R
    liq_price: float            # approximate liquidation price
    tranche_index: int          # 0 = first, 1 = second, 2 = third
    rejected: bool = False
    reject_reason: str = ""


# ---------------------------------------------------------------------------
# Leverage calculation
# ---------------------------------------------------------------------------

def _lerp(x: float, x0: float, x1: float, y0: float, y1: float) -> float:
    """Linear interpolation: map x in [x0,x1] to y in [y0,y1]."""
    t = (x - x0) / (x1 - x0) if x1 != x0 else 0.0
    t = max(0.0, min(1.0, t))
    return y0 + t * (y1 - y0)


def compute_leverage(
    abs_score: float,
    atr_ratio: float,  # ATR(14,1h) / close
) -> float:
    """
    Compute leverage from |alignment_score| and ATR/close ratio.
    Returns float leverage (not rounded — rounding happens at exchange layer).
    """
    # Leverage policy is independent from the entry-quality gate, but weak
    # signals must still be rejected defensively even in fixed mode.
    if abs_score < LEV_BAND_LOW_MIN:
        return 0.0

    if LEVERAGE_MODE == "fixed":
        return FIXED_LEVERAGE

    if abs_score >= LEV_BAND_HIGH_MIN:
        lev = _lerp(abs_score, LEV_BAND_HIGH_MIN, 100.0, LEV_HIGH_LOW, LEV_HIGH_HIGH)
    elif abs_score >= LEV_BAND_MID_MIN:
        lev = _lerp(abs_score, LEV_BAND_MID_MIN, LEV_BAND_HIGH_MIN, LEV_MID_LOW, LEV_MID_HIGH)
    elif abs_score >= LEV_BAND_LOW_MIN:
        lev = _lerp(abs_score, LEV_BAND_LOW_MIN, LEV_BAND_MID_MIN, LEV_LOW_LOW, LEV_LOW_HIGH)
    else:
        # |score| < 40 → no entry (caller should guard, but defensively return 0)
        return 0.0

    # ATR volatility cap
    if atr_ratio > ATR_HIGH_THRESHOLD:
        lev = min(lev, LEV_ATR_CAP)

    # 멀티에셋 R1: 변동성 연동 청산거리 천장 (liq 거리 ≥ 12×ATR_1h)
    if atr_ratio > 0:
        lev = min(lev, 1.0 / (LIQ_ATR_MULT * atr_ratio))

    return lev


# ---------------------------------------------------------------------------
# Stop-loss distance
# ---------------------------------------------------------------------------

def compute_sl_price(
    entry: float,
    side: Literal["long", "short"],
    swing_ref: float,        # recent swing low (long) or swing high (short)
    atr_1h: float,
    ma35: float,
) -> float:
    """
    SL = structural reference: max(swing_ref, MA35 - 0.5×ATR) for long.
    Gives price-based SL that avoids placing it too close.
    """
    buffer = 0.5 * atr_1h
    if side == "long":
        # SL below swing low AND below MA35 by buffer
        structural = min(swing_ref, ma35 - buffer)
        # Must be below entry
        return min(structural, entry * 0.999)
    else:
        structural = max(swing_ref, ma35 + buffer)
        return max(structural, entry * 1.001)


# ---------------------------------------------------------------------------
# Liquidation price (isolated mode approximation)
# ---------------------------------------------------------------------------

def approx_liq_price(
    entry: float,
    leverage: float,
    side: Literal["long", "short"],
    mmr: float = MMR,
) -> float:
    """
    Isolated margin liquidation price approximation.
    Bybit formula: liq ≈ entry × (1 ∓ 1/lev × (1 - MMR))
    Long: liq = entry * (1 - 1/lev * (1 - mmr))  [price goes down]
    Short: liq = entry * (1 + 1/lev * (1 - mmr))  [price goes up]
    """
    factor = (1.0 / leverage) * (1.0 - mmr)
    if side == "long":
        return entry * (1.0 - factor)
    else:
        return entry * (1.0 + factor)


# ---------------------------------------------------------------------------
# Buffer check
# ---------------------------------------------------------------------------

def _sl_passes_buffer(
    entry: float,
    sl: float,
    liq: float,
    side: Literal["long", "short"],
) -> bool:
    """Require liquidation to remain safely beyond the structural stop.

    This is intentionally expressed as a distance ratio rather than the old
    opaque fraction of the entry→liquidation gap:

        abs(entry - liq) / abs(entry - sl) >= LIQ_TO_SL_MIN_RATIO

    The stop must also lie between entry and liquidation.  A stop above the
    liquidation price (long) or below it (short) is always rejected.
    """
    stop_distance = abs(entry - sl)
    liq_distance = abs(entry - liq)
    if stop_distance <= 0 or liq_distance <= 0:
        return False
    if side == "long" and not (liq < sl < entry):
        return False
    if side == "short" and not (entry < sl < liq):
        return False
    return (liq_distance / stop_distance) >= LIQ_TO_SL_MIN_RATIO


# ---------------------------------------------------------------------------
# Main sizing function
# ---------------------------------------------------------------------------

def compute_sizing(
    side: Literal["long", "short"],
    entry: float,
    abs_score: float,
    equity: float,
    atr_1h: float,
    swing_ref: float,
    ma35_1h: float,
    tranche_index: int = 0,
) -> SizingResult:
    """
    Compute leverage, SL, TP, quantity for a new tranche.

    Steps:
    1. Compute initial leverage from score + ATR
    2. Compute SL price (structural)
    3. SL distance % from entry
    4. Compute qty = (equity × RISK_PER_TRADE × tranche_frac) / SL_dist_pct / entry
       (qty in contracts, nominal = qty × entry)
    5. Check liquidation buffer; if fail → reduce leverage; if still fail → reject
    6. Compute TPs (1R/2R/3R)
    """
    atr_ratio = atr_1h / entry if entry > 0 else 0.0
    lev = compute_leverage(abs_score, atr_ratio)
    if lev == 0.0:
        return SizingResult(
            leverage=0, qty=0, sl_price=0, tp1_price=0, tp2_price=0,
            tp3_price=0, liq_price=0, tranche_index=tranche_index,
            rejected=True, reject_reason="score < 40, no entry",
        )

    tranche_frac = TRANCHE_FRACS[min(tranche_index, MAX_TRANCHES - 1)]

    sl_price = compute_sl_price(entry, side, swing_ref, atr_1h, ma35_1h)

    sl_dist_pct = abs(entry - sl_price) / entry
    if sl_dist_pct <= 0:
        return SizingResult(
            leverage=lev, qty=0, sl_price=sl_price, tp1_price=0, tp2_price=0,
            tp3_price=0, liq_price=0, tranche_index=tranche_index,
            rejected=True, reject_reason="SL distance zero",
        )

    # Nominal position size: qty = risk_capital / (sl_dist_pct * entry)
    risk_capital = equity * RISK_PER_TRADE * tranche_frac
    qty = risk_capital / (sl_dist_pct * entry)

    # Compute liq price and check the fixed-leverage safety ratio. Dynamic mode
    # retains the old auto-deleveraging fallback for research only; production
    # fixed mode never silently changes the requested 10x.
    liq = approx_liq_price(entry, lev, side)
    if LEVERAGE_MODE != "fixed":
        max_attempts = 40
        for _ in range(max_attempts):
            liq = approx_liq_price(entry, lev, side)
            if _sl_passes_buffer(entry, sl_price, liq, side):
                break
            if lev <= LEV_FLOOR_BUFFER:
                break
            lev = max(lev - 1.0, LEV_FLOOR_BUFFER)

    liq = approx_liq_price(entry, lev, side)
    if not _sl_passes_buffer(entry, sl_price, liq, side):
        return SizingResult(
            leverage=lev, qty=0, sl_price=sl_price, tp1_price=0, tp2_price=0,
            tp3_price=0, liq_price=liq, tranche_index=tranche_index,
            rejected=True,
            reject_reason=(
                f"청산거리/손절거리 {LIQ_TO_SL_MIN_RATIO:.2f}배 미달 "
                f"(lev={lev:.1f}x), 진입 취소"
            ),
        )

    # TP levels: 1R / 2R / 3R
    sl_dist_abs = abs(entry - sl_price)
    if side == "long":
        tp1 = entry + 1.0 * sl_dist_abs
        tp2 = entry + 2.0 * sl_dist_abs
        tp3 = entry + 3.0 * sl_dist_abs
    else:
        tp1 = entry - 1.0 * sl_dist_abs
        tp2 = entry - 2.0 * sl_dist_abs
        tp3 = entry - 3.0 * sl_dist_abs

    return SizingResult(
        leverage=lev,
        qty=qty,
        sl_price=round(sl_price, 2),
        tp1_price=round(tp1, 2),
        tp2_price=round(tp2, 2),
        tp3_price=round(tp3, 2),
        liq_price=round(liq, 2),
        tranche_index=tranche_index,
        rejected=False,
    )


def compute_event_sizing(
    side: Literal["long", "short"],
    entry: float,
    atr_value: float,
    equity: float,
) -> SizingResult:
    """Size one volatility event with a small independent risk budget."""
    from engine.config import EVENT_RISK_PER_TRADE, EVENT_STOP_ATR_MULT

    if equity <= 0 or entry <= 0 or atr_value <= 0:
        return SizingResult(
            leverage=0, qty=0, sl_price=0, tp1_price=0, tp2_price=0,
            tp3_price=0, liq_price=0, tranche_index=0,
            rejected=True, reject_reason="invalid event sizing inputs",
        )
    lev = FIXED_LEVERAGE if LEVERAGE_MODE == "fixed" else compute_leverage(80.0, atr_value / entry)
    stop_distance = atr_value * EVENT_STOP_ATR_MULT
    sl_price = entry - stop_distance if side == "long" else entry + stop_distance
    liq = approx_liq_price(entry, lev, side)
    if not _sl_passes_buffer(entry, sl_price, liq, side):
        return SizingResult(
            leverage=lev, qty=0, sl_price=round(sl_price, 2),
            tp1_price=0, tp2_price=0, tp3_price=0, liq_price=round(liq, 2),
            tranche_index=0, rejected=True,
            reject_reason="event liquidation distance/stop distance too small",
        )
    risk_capital = equity * EVENT_RISK_PER_TRADE
    qty = risk_capital / stop_distance
    qty = min(qty, equity * lev / entry)
    if qty <= 0:
        return SizingResult(
            leverage=lev, qty=0, sl_price=round(sl_price, 2),
            tp1_price=0, tp2_price=0, tp3_price=0, liq_price=round(liq, 2),
            tranche_index=0, rejected=True, reject_reason="event zero quantity",
        )
    tp1 = entry + stop_distance if side == "long" else entry - stop_distance
    tp2 = entry + 2 * stop_distance if side == "long" else entry - 2 * stop_distance
    tp3 = entry + 3 * stop_distance if side == "long" else entry - 3 * stop_distance
    return SizingResult(
        leverage=lev, qty=qty, sl_price=round(sl_price, 2),
        tp1_price=round(tp1, 2), tp2_price=round(tp2, 2),
        tp3_price=round(tp3, 2), liq_price=round(liq, 2), tranche_index=0,
    )


# ---------------------------------------------------------------------------
# Pyramid guard
# ---------------------------------------------------------------------------

def can_add_tranche(
    current_tranche: int,
    avg_entry: float,
    current_price: float,
    side: Literal["long", "short"],
) -> bool:
    """
    피라미딩: 직전 트랜치가 수익 중일 때만 허용.
    Long: current_price > avg_entry
    Short: current_price < avg_entry
    """
    if current_tranche >= MAX_TRANCHES:
        return False
    if side == "long":
        return current_price > avg_entry
    else:
        return current_price < avg_entry
