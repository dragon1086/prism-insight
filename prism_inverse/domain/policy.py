"""Every number the lane uses, in one frozen object.

The defaults are S1 placeholders so the skeleton runs end to end. They are NOT tuned
and NOT the book's percentages: stage S2 (pre-registered H-1108-inverse-lane) picks
them on the development period only. Nothing may trade on ``POLICY_VERSION`` "s1-*".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Optional

POLICY_VERSION = "s1-placeholder"


@dataclass(frozen=True)
class InversePolicy:
    # ① broken leaders (K-SHORT-23: prior run-up, break below MAs on volume, recovery)
    leader_lookback: int = 250
    leader_min_prior_gain: float = 0.5        # peak / low-before-peak - 1
    leader_min_drop: float = 0.2              # close <= peak * (1 - drop)
    leader_require_ma200: bool = True         # below 200-day as well as 50-day
    leader_volume_ratio: Optional[float] = 1.0  # break-day volume / 50d mean; None = ignore volume
    leader_recover_gap: float = 0.1           # recovered: above 50-day and within gap of peak

    # ② market gate (Market Pulse states that switch the lane on)
    gate_states: tuple = ("CORRECTION",)
    gate_min_leader_weight: Optional[float] = None  # share of the index in broken leaders

    # ③ ETF eligibility (per market; values in local currency)
    min_value_20d_kr: float = 5_000_000_000
    min_value_20d_us: float = 5_000_000
    max_tick_cost: float = 0.005
    # None = the check is not used for that market (KIS publishes NAV/괴리율 for KR ETFs only).
    max_premium_pct_kr: Optional[float] = 1.0
    max_premium_pct_us: Optional[float] = None
    max_tracking_error: Optional[float] = None
    index_choice: str = "main"                # main | overlap (S2 compares; overlap needs index weights)
    main_underlying_kr: str = "069500"        # KODEX 200 stands in for KOSPI200
    main_underlying_us: str = "SPY"
    index_multiplier: int = -1                # one per market, chosen in S2 (never switched daily)
    stock_multiplier_pref: tuple = (-1, -2)   # single stock: 1x before 2x
    max_stock_positions: int = 3

    # ④ entry: "signal" = gate on today; "rally" = underlying rallies up to a moving average
    entry_mode: str = "rally"
    rally_ma: int = 50
    rally_band: float = 0.01                  # close within band below the MA counts as a touch
    stop_atr: float = 1.0                     # stop = max(MA, close + stop_atr * ATR)
    atr_days: int = 20

    # ⑤ exit
    target_atr: Optional[float] = 3.0         # first take-profit distance below entry
    max_hold_days: int = 60
    time_stop_days: Optional[int] = 10        # no progress after N days -> exit (Livermore, p.297)
    time_stop_min_move: float = 0.02

    # sizing
    risk_per_trade: float = 0.005             # share of equity lost if the stop is hit
    gap_risk_per_trade: float = 0.01          # single-stock: loss if the worst recent up-gap repeats
    gap_lookback: int = 250

    def config_hash(self) -> str:
        blob = json.dumps(asdict(self), sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:16]

    def min_value_20d(self, market: str) -> float:
        return self.min_value_20d_kr if market == "KR" else self.min_value_20d_us

    def max_premium_pct(self, market: str) -> Optional[float]:
        return self.max_premium_pct_kr if market == "KR" else self.max_premium_pct_us

    def main_underlying(self, market: str) -> str:
        return self.main_underlying_kr if market == "KR" else self.main_underlying_us
