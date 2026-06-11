# engine/sizing.py — Leverage & position sizing (§6, §9)
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# ---------------------------------------------------------------------------
# Constants (all tuneable here without touching logic)
# ---------------------------------------------------------------------------

RISK_PER_TRADE: float = 0.02          # 2% of equity per trade
MMR: float = 0.005                    # Bybit isolated MMR approximation (0.5%)

# Leverage bands (라운드4: 12~18x 폐기, 라운드2 수준 8~12x 복원 — 라운드3 문서
# "다음 후보 제안 E" 채택). 12~18x는 liq 거리를 좁혀 강제감축을 유발했고
# (2024-25 강제감축 PnL -$58), 8~12x 복원 A/B에서 전 구간 liq_approach 0 +
# 2024-25 수익 -1.1% → +8.3% 반전 확인 (analysis/round4_attribution.py 참조).
# With fixed 2% risk sizing leverage doesn't change qty, but it governs the
# liquidation distance and the residual-leg exposure after BE/trailing.
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

# Liquidation buffer: SL must be >= 65% inside the gap between entry and liq price.
# 라운드3 B: raised 0.50 → 0.65 to directly block liq_approach. On entry, SL must
# sit at least 65% of the entry→liq gap away from liq; otherwise auto-deleverage
# until satisfied, and if the floor still fails, cancel the entry.
LIQ_BUFFER_MIN_FRAC: float = 0.65    # 65% of entry→liq distance must remain between SL and liq
# Deleverage floor restored to 8x to match the 8~12x band (라운드4).
LEV_FLOOR_BUFFER: float = 8.0        # do not deleverage below 8x to satisfy buffer; reject instead

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
    """
    Check: SL must be >= 30% inside the entry→liq gap (away from liq).
    Gap = |entry - liq|. SL-to-liq distance must be >= 30% of gap.

    For long: liq < sl < entry. sl_to_liq = sl - liq. gap = entry - liq.
    Condition: (sl - liq) / (entry - liq) >= LIQ_BUFFER_MIN_FRAC

    For short: entry < sl < liq. sl_to_liq = liq - sl. gap = liq - entry.
    Condition: (liq - sl) / (liq - entry) >= LIQ_BUFFER_MIN_FRAC
    """
    if side == "long":
        gap = entry - liq
        if gap <= 0:
            return False
        sl_to_liq = sl - liq
        return (sl_to_liq / gap) >= LIQ_BUFFER_MIN_FRAC
    else:
        gap = liq - entry
        if gap <= 0:
            return False
        sl_to_liq = liq - sl
        return (sl_to_liq / gap) >= LIQ_BUFFER_MIN_FRAC


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

    # Compute liq price and check buffer — auto-deleverage until SL is >= 65%
    # inside the entry→liq gap. Floor at LEV_FLOOR_BUFFER (12x); if 12x still
    # fails, cancel the entry (라운드3 B). Never deleverage below 12x.
    liq = approx_liq_price(entry, lev, side)
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
            rejected=True, reject_reason="청산가 버퍼(65%) 불충족 @12x, 진입 취소",
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
