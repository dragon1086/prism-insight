# core/risk.py — E4 operating-risk overlay (라이브 준비, 백테스트 미연결)
#
# Pure helper for the live daemon. NOT wired into backtest/engine.py — the
# backtest champion's behavior must stay byte-identical, so this overlay is
# prepared for live use only.
#
# Rationale: in live operation we throttle per-trade risk when the account is in
# drawdown, restoring full risk once recovered. This is a live capital-
# preservation guardrail, distinct from per-trade sizing (engine.sizing).
from __future__ import annotations


def compute_operating_risk(
    equity: float,
    peak: float,
    base_risk: float = 0.05,
    dd_threshold: float = 0.05,
    reduced_risk: float = 0.025,
) -> float:
    """Return the operating per-trade risk fraction given current drawdown.

    If the account is drawn down by at least `dd_threshold` from its `peak`
    equity, return the `reduced_risk` fraction; otherwise return `base_risk`.

    drawdown = (peak - equity) / peak  (0 when at/above peak; peak<=0 → no DD).

    Args:
        equity:        current account equity.
        peak:          high-water-mark equity.
        base_risk:     normal per-trade risk fraction (default 0.05 = 5%).
        dd_threshold:  drawdown fraction that triggers de-risking (default 0.05).
        reduced_risk:  throttled per-trade risk while in drawdown (default 0.025).

    Returns:
        base_risk when drawdown < dd_threshold, else reduced_risk.
    """
    if peak <= 0:
        return base_risk
    drawdown = (peak - equity) / peak
    if drawdown >= dd_threshold:
        return reduced_risk
    return base_risk
