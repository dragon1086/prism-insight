"""Causal, deterministic analysis lenses, never order gates or risk authority."""
from __future__ import annotations

import math

from engine.scenario_snapshot import TIMEFRAME_MS


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _primary(snapshot, tf):
    now = snapshot.get("as_of_ms")
    frame = snapshot.get("timeframes", {}).get(tf, {})
    result = dict(timeframe=tf, source=None, provisional=False, direction=None,
                  reason="primary_unavailable")
    if not _finite(now) or frame.get("status") != "ok":
        return result
    forming = frame.get("forming") or {}
    observed, opened = forming.get("observed_at_ms"), forming.get("open_time_ms")
    elapsed = forming.get("elapsed_ms")
    if (not all(_finite(v) for v in (observed, opened, elapsed))
            or not opened <= observed <= now < opened + TIMEFRAME_MS[tf]
            or now - observed > 120_000 or elapsed != observed - opened):
        return {**result, "reason": "invalid_primary_observation_time"}
    use_confirmed = elapsed == 0 or forming.get("observation_kind") == "synthetic_boundary"
    source = "confirmed" if use_confirmed else "forming"
    facts = frame.get(source) or {}
    result.update(source=source, provisional=not use_confirmed,
                  observed_at_ms=observed, source_open_time_ms=facts.get("open_time_ms"),
                  boundary_fallback=use_confirmed)
    if use_confirmed and (facts.get("is_confirmed") is not True
                          or facts.get("open_time_ms") != opened - TIMEFRAME_MS[tf]):
        return {**result, "reason": "invalid_confirmed_boundary_history"}
    fast, slow = facts.get("ma10"), facts.get("ma35")
    fs, ss = facts.get("ma10_slope_fraction_per_bar"), facts.get("ma35_slope_fraction_per_bar")
    position, order = facts.get("price_position"), facts.get("ma_order")
    if (not all(_finite(v) for v in (fast, slow, fs, ss)) or min(fast, slow) <= 0
            or position not in ("above", "below", "between")
            or order != ("bullish" if fast > slow else "bearish" if fast < slow else "equal")):
        return {**result, "reason": "invalid_primary_ma_facts"}
    direction = ("LONG" if position == "above" and order == "bullish" and fs >= 0 and ss >= 0
                 else "SHORT" if position == "below" and order == "bearish" and fs <= 0 and ss <= 0
                 else None)
    result.update(direction=direction, price_position=position, ma_order=order,
                  ma10_slope_fraction_per_bar=fs, ma35_slope_fraction_per_bar=ss,
                  reason="directional_support" if direction else "mixed_or_early_primary")
    return result


def decision_framing(snapshot: dict, context: dict) -> dict:
    """Recompute from current facts; ignore any caller-supplied framing label."""
    primary = [_primary(snapshot, tf) for tf in ("30m", "1h")]
    reasons = []
    if snapshot.get("valid") is not True:
        reasons.append("snapshot_invalid")
    if any(f["reason"] not in ("directional_support", "mixed_or_early_primary") for f in primary):
        reasons.append("primary_evidence_unavailable")
    if context.get("new_risk_blocked"):
        reasons.append("new_risk_blocked")
    if context.get("accounting_status") == "pending":
        reasons.append("accounting_pending")
    held = context.get("side") if context.get("positions") or context.get("scenario_id") else None
    adverse_position = "below" if held == "LONG" else "above" if held == "SHORT" else None
    if adverse_position and any(f.get("price_position") == adverse_position for f in primary):
        reasons.append("primary_evidence_adverse_to_active_scenario")
    directions = [f["direction"] for f in primary]
    bias = directions[0] if directions[0] and directions[0] == directions[1] else None
    state = "DEFENSIVE" if reasons else "OPPORTUNITY" if bias else "TRANSITION"
    return dict(version=1, state=state, bias=bias, active_side=held,
                reasons=reasons or ["aligned_primary_support" if bias else "mixed_or_early_primary"],
                primary=primary, as_of_ms=snapshot.get("as_of_ms"),
                role="soft_analysis_lens_not_order_gate", higher_frames="context_not_veto",
                five_minute="pace_not_gate", risk_budget_changed=False)


_FRAMING_PROMPTS = {
    "OPPORTUNITY": """Frame: OPPORTUNITY (기회 검토). Both primary frames support the indicated
direction, LONG and SHORT symmetrically. Actively compare WAIT with a small,
risk-capped exploratory entry with explicit invalidation: non-participation can
miss a valid net-of-cost opportunity. Prefer timely participation when evidence,
execution costs and stop structure justify it, rather than accumulating redundant
confirmation. Scale in only as new evidence supports the thesis. WAIT remains
valid, but name its concrete cost/structure blocker and a testable next trigger;
'the candle is unfinished' alone is not a blocker. For an existing aligned
position, compare holding a protected runner and selective additions, not just
early profit taking. Do not force a trade or imply an observed edge guarantees profit.
Prefix the Korean rationale with [기회 검토].""",
    "TRANSITION": """Frame: TRANSITION (전환 탐색). Primary evidence is mixed or an early
transition. Compare a smaller exploratory entry with WAIT using explicit
invalidation and net costs. Do not wait mechanically for every MA and timeframe
to align; reassess earlier triggers and a compression breakout as it develops.
If waiting is better, state the concrete next trigger without moving goalposts.
Do not interpret this frame as a ban on entry or as proof of a breakout.
Prefix the Korean rationale with [전환 탐색].""",
    "DEFENSIVE": """Frame: DEFENSIVE (위험 관리). Prioritize existing exposure, protection
and reliable accounting over missed participation. Examine whether the existing
thesis has broken; compare preserving/tightening protection, reducing or exiting.
Never add risk to a broken thesis or while new risk is blocked/accounting pending.
If flat with unreliable primary facts, WAIT rather than invent missing evidence.
A bearish opportunity for a flat account is NOT good news for a held LONG, and
a bullish opportunity is NOT good news for a held SHORT. Do not force liquidation
solely from the framing label; use actual position and current evidence.
Prefix the Korean rationale with [위험 관리].""",
}


def framing_prompt(framing: dict) -> str:
    """Only trusted fixed strings enter the system prompt, never reason strings."""
    return _FRAMING_PROMPTS.get(framing.get("state"), _FRAMING_PROMPTS["DEFENSIVE"]) + """
This framing is a soft analysis lens, not permission or a calibrated probability.
Potential missed opportunity is NOT realized loss and MUST NOT be booked as a
loss or enlarge the original 2% scenario budget. Fixed 10x leverage, existing
daily/three-loss circuit breakers, pending-order risk, fee/slippage allowances,
no widening live hard stops and no recycling profits remain unchanged.
Return only the existing response schema, with no framing or other extra fields.
"""
