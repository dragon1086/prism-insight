"""③ ETF eligibility and choice. Rules read the underlying; the ETF is only checked for cost."""

from __future__ import annotations

from datetime import date
from typing import Iterable, Mapping, Optional

from prism_inverse.domain.models import EtfSnapshot, EtfSpec
from prism_inverse.domain.policy import InversePolicy


def reject_reason(spec: EtfSpec, snap: EtfSnapshot, day: date, policy: InversePolicy) -> Optional[str]:
    """``None`` when eligible, else a stable reason code. Unknown inputs reject as MISSING_*."""
    if day < spec.listed:
        return "NOT_LISTED"
    if abs(spec.multiplier) not in (1, 2):
        return "MULTIPLIER_EXCLUDED"
    if snap.value_20d is None:
        return "MISSING_VALUE_20D"
    if snap.value_20d < policy.min_value_20d(spec.market):
        return "LOW_VALUE_20D"
    if snap.tick_cost is None:
        return "MISSING_TICK_COST"
    if snap.tick_cost > policy.max_tick_cost:
        return "TICK_COST_HIGH"
    max_premium = policy.max_premium_pct(spec.market)
    if max_premium is not None:
        if snap.premium_pct is None:
            return "MISSING_PREMIUM"
        if snap.premium_pct > max_premium:
            return "PREMIUM_HIGH"
    if policy.max_tracking_error is not None:
        if snap.tracking_error is None:
            return "MISSING_TRACKING_ERROR"
        if snap.tracking_error > policy.max_tracking_error:
            return "TRACKING_ERROR_HIGH"
    return None


def best_per_underlying(eligible: Iterable[tuple[EtfSpec, EtfSnapshot]]) -> list[tuple[EtfSpec, EtfSnapshot]]:
    """Same underlying and multiplier: keep the cheapest tick, then the most traded."""
    best: dict[tuple[str, int], tuple[EtfSpec, EtfSnapshot]] = {}
    for spec, snap in eligible:
        key = (spec.underlying.code, spec.multiplier)
        cur = best.get(key)
        if cur is None or (snap.tick_cost, -snap.value_20d) < (cur[1].tick_cost, -cur[1].value_20d):
            best[key] = (spec, snap)
    return sorted(best.values(), key=lambda pair: pair[0].code)


def choose_index(
    gated: list[EtfSpec], overlap: Mapping[str, Optional[float]], main_code: str, policy: InversePolicy,
) -> tuple[Optional[EtfSpec], str]:
    """Pick one index/sector ETF among those whose own underlying passed the gate."""
    if not gated:
        return None, "NO_GATED_INDEX"
    if policy.index_choice == "main":
        hit = [s for s in gated if s.underlying.code == main_code]
        return (hit[0], "MAIN_INDEX") if hit else (None, "MAIN_NOT_GATED")
    scored = [(overlap.get(s.underlying.code), s) for s in gated]
    known = [(w, s) for w, s in scored if w is not None]
    if not known:
        return None, "MISSING_OVERLAP"
    known.sort(key=lambda pair: (-pair[0], pair[1].underlying.code != main_code, pair[1].code))
    return known[0][1], "MAX_OVERLAP"


def prefer_multiplier(specs: Iterable[EtfSpec], order: tuple[int, ...]) -> dict[str, EtfSpec]:
    """Per underlying stock, the ETF whose multiplier comes first in ``order`` (1x before 2x)."""
    out: dict[str, EtfSpec] = {}
    for spec in specs:
        if spec.multiplier not in order:
            continue
        cur = out.get(spec.underlying.code)
        if cur is None or order.index(spec.multiplier) < order.index(cur.multiplier):
            out[spec.underlying.code] = spec
    return out
