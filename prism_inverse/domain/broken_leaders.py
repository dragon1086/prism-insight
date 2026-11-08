"""① Broken-leader list: which former leaders have broken down, recovered, or broken again.

Definition skeleton from K-SHORT-23 (prior run-up, close below the 50/200-day lines on
rising volume, a recovery attempt). Thresholds live in ``InversePolicy`` and are chosen in S2.
Missing volume cannot confirm a break (same convention as ``cores/market_pulse.py``).
"""

from __future__ import annotations

from typing import Optional, Sequence

from prism_inverse.domain.indicators import volume_ratio
from prism_inverse.domain.models import Bar, LeaderState
from prism_inverse.domain.policy import InversePolicy


def leader_states(code: str, bars: Sequence[Bar], policy: InversePolicy) -> list[LeaderState]:
    """One state per bar (oldest first), using only bars up to that day."""
    # shortcut: O(n * lookback) per stock; fine for the registry's ~20 stocks, vectorise
    # in S2 if the 500-stock research universe makes it slow.
    out: list[LeaderState] = []
    status: Optional[str] = None
    since = None
    sum50 = sum200 = 0.0
    for i, bar in enumerate(bars):
        sum50 += bar.close - (bars[i - 50].close if i >= 50 else 0.0)
        sum200 += bar.close - (bars[i - 200].close if i >= 200 else 0.0)
        ma50 = sum50 / 50 if i >= 49 else None
        ma200 = sum200 / 200 if i >= 199 else None
        if ma50 is None or (policy.leader_require_ma200 and ma200 is None):
            out.append(LeaderState(code, None, None))
            continue

        start = max(0, i - policy.leader_lookback + 1)
        window = bars[start:i + 1]
        peak_at = max(range(len(window)), key=lambda k: window[k].close)
        peak = window[peak_at].close
        low_before = min(b.close for b in window[:peak_at + 1])
        prior_gain = peak / low_before - 1 if low_before > 0 else None
        drop = 1 - bar.close / peak if peak > 0 else None

        below = bar.close < ma50 and (not policy.leader_require_ma200 or bar.close < ma200)
        vr = volume_ratio(bars[:i + 1], 50) if policy.leader_volume_ratio is not None else None
        volume_ok = policy.leader_volume_ratio is None or (vr is not None and vr >= policy.leader_volume_ratio)
        broken_now = (
            prior_gain is not None and prior_gain >= policy.leader_min_prior_gain
            and drop is not None and drop >= policy.leader_min_drop
            and below and volume_ok
        )
        recovered_now = bar.close > ma50 and bar.close >= peak * (1 - policy.leader_recover_gap)

        if status in (None, "recovered") and broken_now:
            status = "rebroken" if status == "recovered" else "broken"
            since = bar.day
        elif status in ("broken", "rebroken") and recovered_now:
            status, since = "recovered", bar.day
        elif status is not None and (bar.day - since).days > policy.leader_lookback * 7 // 5:
            # About one lookback of trading days without a new event: no longer a recent leader.
            status, since = None, None

        out.append(LeaderState(code, status, since, prior_gain, drop, vr))
    return out


def leader_state(code: str, bars: Sequence[Bar], policy: InversePolicy) -> LeaderState:
    states = leader_states(code, bars, policy)
    return states[-1] if states else LeaderState(code, None, None)
