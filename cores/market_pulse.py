"""Market Pulse — O'Neil "market direction (M)" state machine (pure, no I/O).

Deterministic finite state machine that classifies overall market health from a
sequence of index daily bars into one of three states::

    UPTREND         — normal; distribution days (DD) <= 3 in the rolling window
    UNDER_PRESSURE  — 4 or 5 accumulated distribution days
    CORRECTION      — 6+ distribution days, a price drawdown of >=10% below the
                      rolling reference peak close (edge-trigger, Rev.2), OR
                      repeated failed rally attempts; exited by a valid
                      Follow-Through Day (FTD) OR by a price-recovery close above
                      the pre-correction peak (O'Neil: a new high is by
                      definition an uptrend; FTD is an early-bottom catch, not the
                      sole exit). See §7 Rev.1/Rev.2.

The single source of truth for the whole regime/buy/rest policy. It is pure:
input is a sequence of ``DailyBar`` values, output is a state string. No network,
no filesystem, no clock — fully unit-testable.

Constant provenance (IBD / William O'Neil public methodology — 60y market
history; NOT tuned on our 156 trades). See tasks/market_pulse/00_VALIDATION_PLAN.md §1:

    | Constant                | Value            | Source                                   |
    |-------------------------|------------------|------------------------------------------|
    | Distribution Day        | close <= -0.2% & | IBD standard                             |
    |                         | volume > prev    |                                          |
    | DD expiry               | 25 sessions, or  | IBD standard                             |
    |                         | +5% recovery     |                                          |
    | Correction entry        | DD >= 6          | IBD "market in correction"               |
    | Correction 진입(가격)   | 롤링 피크 종가   | 시장사 보편 정의(10% correction);        |
    |                         | 대비 -10%        | edge-trigger, 탈출 시 피크 리셋; Rev.2   |
    |                         | (edge-trigger)   |                                          |
    | Under-pressure          | DD in {4, 5}     | IBD                                      |
    | Rally attempt Day 1     | first up-close   | O'Neil, "How to Make Money in Stocks"    |
    |                         | after new low    |                                          |
    | Follow-Through Day      | rally day >= 4 & | O'Neil HTMMIS canonical (1.25%+); §7     |
    |                         | +1.25% & vol up  | Rev.1 (1.7→1.25, was over-conservative)  |
    | Price-recovery exit     | close > pre-     | O'Neil — a new high IS an uptrend;       |
    |                         | correction peak  | §7 Rev.1 (added exit besides FTD)        |
    | Rally reset             | close < rally    | O'Neil standard                          |
    |                         | start low        |                                          |

Distribution-day semantics are MIRRORED from the existing deterministic repo
implementation, ``_count_distribution_days`` in
``prism-us/cores/data_prefetch.py`` (lines ~1050-1107; constants
DISTRIBUTION_WINDOW=25, DISTRIBUTION_DROP_PCT=0.2, DISTRIBUTION_RECOVERY_PCT=5.0
at lines 994-996), which feeds ``index_summary.distribution_days`` in
production. We reproduce its exact definition and dual expiry (25-session window
+ 5%-recovery) so both code paths agree. The only intentional difference: this
module never returns ``None`` on missing volume — instead a bar whose volume (or
its predecessor's) is missing simply *cannot confirm* a DD and is not counted,
which is the same downstream effect (DD unconfirmed) expressed as a state rather
than a null. Per IBD, a price drop without a volume increase is NOT a
distribution day, so missing volume => "cannot confirm" => not a DD.

Missing-volume handling summary:
  * Distribution day: requires volume(t) > volume(t-1). If either is None the
    day cannot be a DD (never counted). Matches IBD.
  * Follow-Through Day: normally requires volume(t) > volume(t-1). If volume data
    is missing for the comparison, FTD falls back to the price condition only
    (rally day >= 4 AND gain >= +1.25%) — a documented, deliberate degradation so
    indices without reliable volume still transition out of CORRECTION.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------- #
# Constants (IBD/O'Neil — see module docstring table; mirror data_prefetch.py)  #
# --------------------------------------------------------------------------- #
DISTRIBUTION_WINDOW: int = 25          # rolling sessions (IBD standard)
DISTRIBUTION_DROP_PCT: float = 0.2     # close change <= -0.2% (IBD)
DISTRIBUTION_RECOVERY_PCT: float = 5.0  # DD expires early on +5% recovery (IBD)

UNDER_PRESSURE_MIN_DD: int = 4         # 4-5 DD => under pressure (IBD)
CORRECTION_MIN_DD: int = 6             # >=6 DD => market in correction (IBD)

# Rev.2: price-drawdown correction entry. A close >=10% below the rolling
# reference peak (a standard "10% = correction" market-history definition, not
# tuned) enters CORRECTION even with zero DDs — waterfall/gap crashes skip the
# distribution-day (topping) stage. Edge-triggered; the reference peak is reset
# on CORRECTION exit so a NEW >=10% decline from post-exit levels is required.
DRAWDOWN_CORRECTION_PCT: float = 10.0  # >=10% below rolling peak => correction

FTD_MIN_RALLY_DAY: int = 4             # follow-through on day 4+ (O'Neil HTMMIS)
FTD_MIN_GAIN_PCT: float = 1.25         # +1.25% (O'Neil HTMMIS canonical; §7 Rev.1, was 1.7)

# PulseState values (enum-ish string constants).
UPTREND: str = "UPTREND"
UNDER_PRESSURE: str = "UNDER_PRESSURE"
CORRECTION: str = "CORRECTION"

PulseState = str  # semantic alias: one of UPTREND / UNDER_PRESSURE / CORRECTION


@dataclass(frozen=True)
class DailyBar:
    """A single index daily bar. ``volume`` may be None when unavailable."""

    date: str            # YYYY-MM-DD
    close: float
    volume: Optional[float] = None


def _count_distribution_days(
    closes: List[float],
    vols: List[Optional[float]],
    window: int = DISTRIBUTION_WINDOW,
    start_idx: int = 0,
    drop_threshold_pct: float = DISTRIBUTION_DROP_PCT,
    recovery_pct: float = DISTRIBUTION_RECOVERY_PCT,
) -> int:
    """Count live distribution days over the trailing ``window`` sessions.

    Mirrors ``_count_distribution_days`` in prism-us/cores/data_prefetch.py
    (deterministic O'Neil count). A DD occurs at index ``i`` when::

        (close[i] - close[i-1]) / close[i-1] * 100 <= -drop_threshold_pct
        AND vol[i] > vol[i-1]   (volume confirmation; missing => not a DD)

    Expiry: (1) only the last ``window`` comparison-days are considered; (2) a DD
    is removed if any *later* close is >= its close * (1 + recovery_pct/100)
    (a +5% recovery above the DD's own close).

    ``start_idx`` lets the caller reset the window after a Follow-Through Day so
    pre-FTD distribution days no longer count (only DDs at index >= start_idx).
    """
    n = len(closes)
    if n < 2:
        return 0
    start = max(1, n - window, start_idx)
    running_max_after = float("-inf")
    flags: List[Tuple[int, bool, float]] = []
    # Reverse pass so ``running_max_after`` is the max close strictly AFTER i.
    for i in range(n - 1, start - 1, -1):
        prev_c = closes[i - 1]
        cur_c = closes[i]
        if prev_c <= 0:
            flags.append((i, False, running_max_after))
            running_max_after = max(running_max_after, cur_c)
            continue
        pct = (cur_c - prev_c) / prev_c * 100.0
        vi = vols[i]
        vp = vols[i - 1]
        vol_up = vi is not None and vp is not None and vi > vp
        is_dist = (pct <= -drop_threshold_pct) and vol_up
        flags.append((i, is_dist, running_max_after))
        running_max_after = max(running_max_after, cur_c)
    kept = 0
    for i, is_dist, max_after in flags:
        if not is_dist:
            continue
        if max_after >= closes[i] * (1 + recovery_pct / 100.0):
            continue  # expired by +5% recovery
        kept += 1
    return kept


class MarketPulse:
    """Incremental O'Neil market-direction state machine.

    Feed index daily bars in chronological order via :meth:`feed`; it returns the
    current :data:`PulseState` after each bar. State depends only on the bars fed
    so far (as-of semantics), so replaying a fixed sequence is deterministic.
    """

    def __init__(self) -> None:
        self._closes: List[float] = []
        self._vols: List[Optional[float]] = []
        self._dates: List[str] = []
        self._state: PulseState = UPTREND
        self._last_dd: int = 0
        # DD window reset point (absolute bar index); bumped after an FTD.
        self._dd_window_start: int = 0
        # Correction / rally-attempt tracking (O'Neil).
        self._correction_low: Optional[float] = None
        self._rally_active: bool = False
        self._rally_day: int = 0
        self._rally_start_low: Optional[float] = None
        # Highest close observed before/at the moment CORRECTION was entered.
        # A later close above this pre-correction peak = new high = uptrend exit
        # (§7 Rev.1). Tracked per-episode: re-captured on each _enter_correction.
        self._pre_correction_peak: Optional[float] = None
        # Rev.2: rolling reference peak close — max close since machine start OR
        # since the last CORRECTION exit. Drives the >=10% price-drawdown entry
        # (edge-trigger) and is reset to the exit close when leaving CORRECTION so
        # the market must make a NEW >=10% decline from post-exit levels before it
        # can re-trigger (standard trailing correction/bear labeling; anti-flap).
        self._reference_peak: Optional[float] = None

    @property
    def state(self) -> PulseState:
        return self._state

    @property
    def distribution_days(self) -> int:
        return self._last_dd

    def feed(self, bar: DailyBar) -> PulseState:
        """Ingest one bar (chronological) and return the resulting state."""
        self._dates.append(bar.date)
        self._closes.append(float(bar.close))
        self._vols.append(None if bar.volume is None else float(bar.volume))
        n = len(self._closes)
        cur = self._closes[-1]

        # Rev.2: maintain the rolling reference peak (max close since start or the
        # last CORRECTION exit). Updated every bar, before entry checks so a new
        # high simply lifts the peak (drawdown 0) and never self-triggers.
        if self._reference_peak is None or cur > self._reference_peak:
            self._reference_peak = cur

        dd = _count_distribution_days(
            self._closes, self._vols, DISTRIBUTION_WINDOW, self._dd_window_start
        )
        self._last_dd = dd

        if self._state == CORRECTION:
            self._update_correction(n)
        else:
            # UPTREND / UNDER_PRESSURE are re-derived from DD count each day;
            # only CORRECTION is "sticky" (exited via FTD / price-recovery).
            drawdown_trigger = (
                self._reference_peak is not None
                and cur < self._reference_peak * (1.0 - DRAWDOWN_CORRECTION_PCT / 100.0)
            )
            if drawdown_trigger or dd >= CORRECTION_MIN_DD:
                # Rev.2: a >=10% drawdown from the rolling peak enters CORRECTION
                # even with zero DDs (waterfall crashes skip the topping stage).
                self._enter_correction()
            elif dd >= UNDER_PRESSURE_MIN_DD:
                self._state = UNDER_PRESSURE
            else:
                self._state = UPTREND
        return self._state

    def replay(self, bars) -> List[Tuple[str, PulseState, int]]:
        """Feed a whole sequence; return [(date, state, dd_count), ...]."""
        out: List[Tuple[str, PulseState, int]] = []
        for bar in bars:
            state = self.feed(bar)
            out.append((bar.date, state, self._last_dd))
        return out

    # ------------------------------------------------------------------ #
    # Correction / rally-attempt internals                                #
    # ------------------------------------------------------------------ #
    def _enter_correction(self) -> None:
        self._state = CORRECTION
        self._correction_low = self._closes[-1]
        # Pre-correction peak = the rolling reference peak at trigger time (max
        # close since start or the last CORRECTION exit). Identical to the highest
        # close for a DD-triggered entry, and exactly the drawdown reference for a
        # price-drawdown entry (Rev.2). Captured fresh per episode, so a later
        # episode uses its own peak and the new-high recovery exit compares against
        # the current episode's peak.
        self._pre_correction_peak = self._reference_peak
        self._rally_active = False
        self._rally_day = 0
        self._rally_start_low = None

    def _update_correction(self, n: int) -> None:
        cur = self._closes[-1]
        prev = self._closes[-2] if n >= 2 else cur
        cur_vol = self._vols[-1]
        prev_vol = self._vols[-2] if n >= 2 else None

        if self._correction_low is None:
            self._correction_low = cur

        # Price-recovery exit (§7 Rev.1): a close above the pre-correction peak
        # is by definition a new high => uptrend. Checked every day, independent
        # of any rally attempt. FTD is an early-bottom catch, not the sole exit.
        if self._pre_correction_peak is not None and cur > self._pre_correction_peak:
            self._exit_to_uptrend(n)
            return

        if not self._rally_active:
            # Day 1 of a rally attempt = first close up from the prior close.
            if n >= 2 and cur > prev:
                self._rally_active = True
                self._rally_day = 1
                self._rally_start_low = self._correction_low
            else:
                if cur < self._correction_low:
                    self._correction_low = cur
            return

        # Active rally attempt.
        if self._rally_start_low is not None and cur < self._rally_start_low:
            # Undercut the rally-start low => rally fails, reset; new low stands.
            self._rally_active = False
            self._rally_day = 0
            self._correction_low = min(self._correction_low, cur)
            self._rally_start_low = None
            return

        self._rally_day += 1
        gain = (cur - prev) / prev * 100.0 if prev > 0 else 0.0
        has_vol = cur_vol is not None and prev_vol is not None
        vol_up = has_vol and cur_vol > prev_vol
        # Volume confirmation; gain-only fallback when volume data is missing.
        vol_ok = vol_up if has_vol else True
        if self._rally_day >= FTD_MIN_RALLY_DAY and gain >= FTD_MIN_GAIN_PCT and vol_ok:
            self._follow_through(n)

    def _follow_through(self, n: int) -> None:
        """Valid FTD: return to UPTREND and reset the DD window."""
        self._exit_to_uptrend(n)

    def _exit_to_uptrend(self, n: int) -> None:
        """Shared CORRECTION exit (FTD or price-recovery): UPTREND + DD reset."""
        self._state = UPTREND
        self._dd_window_start = n  # exclude all DDs up to and including today
        self._last_dd = 0
        self._rally_active = False
        self._rally_day = 0
        self._rally_start_low = None
        self._correction_low = None
        self._pre_correction_peak = None
        # Rev.2 anti-flap: reset the rolling reference peak to today's close so a
        # NEW >=10% decline from post-exit levels is required to re-trigger a
        # price-drawdown CORRECTION. Thereafter the peak grows with new highs.
        self._reference_peak = self._closes[-1]
