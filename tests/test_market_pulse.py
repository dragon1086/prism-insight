"""Unit tests for cores.market_pulse — O'Neil market-direction state machine.

Pure logic, zero network. Table-driven synthetic bar sequences.
Run with:  .venv/bin/python -m pytest tests/test_market_pulse.py -q
"""

from __future__ import annotations

from typing import List, Optional

import pytest

from cores.market_pulse import (
    CORRECTION,
    UNDER_PRESSURE,
    UPTREND,
    DailyBar,
    MarketPulse,
    _count_distribution_days,
)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _bars(rows: List[tuple]) -> List[DailyBar]:
    """rows = [(close, volume), ...] -> synthetic bars with sequential dates."""
    out = []
    for i, row in enumerate(rows):
        close, vol = row
        out.append(DailyBar(date=f"2026-01-{i + 1:02d}", close=float(close), volume=vol))
    return out


def _dd_from_closes(closes: List[float], vols: List[Optional[float]], **kw) -> int:
    return _count_distribution_days(closes, vols, **kw)


def _make_dd_run(n: int, start: float = 100.0):
    """Produce n distribution days: each -1% close with rising volume."""
    closes = [start]
    vols = [1000.0]
    for i in range(n):
        closes.append(closes[-1] * 0.99)      # -1% <= -0.2%
        vols.append(vols[-1] + 100.0)          # volume up
    return closes, vols


# --------------------------------------------------------------------------- #
# Distribution-day counting + expiry                                          #
# --------------------------------------------------------------------------- #
class TestDistributionCount:
    def test_basic_dd_count(self):
        closes, vols = _make_dd_run(4)
        assert _dd_from_closes(closes, vols) == 4

    def test_no_dd_when_volume_not_up(self):
        # price drops but volume falls -> no distribution day
        closes = [100, 99, 98, 97]
        vols = [1000, 900, 800, 700]
        assert _dd_from_closes(closes, vols) == 0

    def test_no_dd_when_drop_too_small(self):
        # -0.1% drop is above the -0.2% threshold -> not a DD
        closes = [100.0, 99.9, 99.8]
        vols = [1000, 1100, 1200]
        assert _dd_from_closes(closes, vols) == 0

    def test_missing_volume_cannot_confirm(self):
        closes = [100, 99, 98]
        vols = [None, None, None]
        assert _dd_from_closes(closes, vols) == 0

    def test_25_session_window_expiry(self):
        # 3 DDs, then 25 flat/up sessions push them out of the window.
        closes, vols = _make_dd_run(3)               # closes[0..3]
        base = closes[-1]
        for k in range(26):                          # many later sessions
            base *= 1.0005                           # tiny +0.05% (not a DD, <5% recovery slowly)
            closes.append(base)
            vols.append(500.0)                        # volume down -> never a DD
        # The 3 DDs are now >25 comparison-sessions back -> outside window.
        assert _dd_from_closes(closes, vols) == 0

    def test_5pct_recovery_expiry(self):
        # 1 DD then price recovers >5% above the DD close -> DD expires.
        closes = [100.0, 99.0]        # -1% DD at index1 (close 99)
        vols = [1000.0, 1100.0]
        closes.append(99.0 * 1.06)    # +6% above the DD close -> recovery expiry
        vols.append(1200.0)
        assert _dd_from_closes(closes, vols) == 0

    def test_start_idx_reset(self):
        closes, vols = _make_dd_run(4)
        # Ignore everything before the last index -> no countable DD.
        assert _dd_from_closes(closes, vols, start_idx=len(closes)) == 0


# --------------------------------------------------------------------------- #
# UPTREND -> UNDER_PRESSURE -> CORRECTION transitions                         #
# --------------------------------------------------------------------------- #
class TestStateTransitions:
    def test_starts_uptrend(self):
        mp = MarketPulse()
        assert mp.feed(DailyBar("2026-01-01", 100.0, 1000.0)) == UPTREND

    def test_three_dd_stays_uptrend(self):
        closes, vols = _make_dd_run(3)
        mp = MarketPulse()
        states = [mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
                  for i, (c, v) in enumerate(zip(closes, vols))]
        assert states[-1] == UPTREND
        assert mp.distribution_days == 3

    def test_four_dd_under_pressure(self):
        closes, vols = _make_dd_run(4)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            state = mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        assert mp.distribution_days == 4
        assert state == UNDER_PRESSURE

    def test_five_dd_under_pressure(self):
        closes, vols = _make_dd_run(5)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            state = mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        assert mp.distribution_days == 5
        assert state == UNDER_PRESSURE

    def test_six_dd_correction(self):
        closes, vols = _make_dd_run(6)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            state = mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        assert mp.distribution_days == 6
        assert state == CORRECTION

    def test_correction_is_sticky(self):
        # Enter correction, then flat days (DD would decay) -> still CORRECTION
        # because only an FTD exits.
        closes, vols = _make_dd_run(6)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        # one small down day (no rally, no FTD)
        last = closes[-1]
        st = mp.feed(DailyBar("2026-02-01", last * 0.999, 100.0))
        assert st == CORRECTION


# --------------------------------------------------------------------------- #
# Rally attempts + Follow-Through Day                                         #
# --------------------------------------------------------------------------- #
class TestRallyAndFTD:
    def _into_correction(self) -> MarketPulse:
        closes, vols = _make_dd_run(6)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        assert mp.state == CORRECTION
        self._low = closes[-1]
        return mp

    def test_ftd_day4_gain_volume_returns_uptrend(self):
        mp = self._into_correction()
        low = self._low
        # Day 1: first up close from prior; days 2-3 hold; day 4 = FTD.
        c1 = low * 1.005
        st1 = mp.feed(DailyBar("2026-02-01", c1, 1000.0))       # rally day 1
        c2 = c1 * 1.003
        st2 = mp.feed(DailyBar("2026-02-02", c2, 1100.0))       # day 2
        c3 = c3v = c2 * 1.002
        st3 = mp.feed(DailyBar("2026-02-03", c3, 1200.0))       # day 3
        c4 = c3 * 1.02                                          # +2% >= 1.7%
        st4 = mp.feed(DailyBar("2026-02-04", c4, 2000.0))       # day 4 vol up -> FTD
        assert st1 == CORRECTION
        assert st2 == CORRECTION
        assert st3 == CORRECTION
        assert st4 == UPTREND
        assert mp.distribution_days == 0  # DD window reset on FTD

    def test_no_ftd_before_day4(self):
        mp = self._into_correction()
        low = self._low
        c1 = low * 1.02   # big +2% but it's only rally day 1 -> not FTD
        st = mp.feed(DailyBar("2026-02-01", c1, 5000.0))
        assert st == CORRECTION

    def test_ftd_fires_at_new_1_25_threshold(self):
        # §7 Rev.1: threshold lowered 1.7% -> 1.25%. A +1.4% day-4 gain (between
        # the old and new thresholds) is now a valid FTD. All closes stay well
        # below the pre-correction peak (100), so price-recovery does not fire.
        mp = self._into_correction()
        low = self._low
        c1 = low * 1.005
        mp.feed(DailyBar("2026-02-01", c1, 1000.0))
        c2 = c1 * 1.003
        mp.feed(DailyBar("2026-02-02", c2, 1100.0))
        c3 = c2 * 1.002
        mp.feed(DailyBar("2026-02-03", c3, 1200.0))
        c4 = c3 * 1.014          # +1.4% >= 1.25% (would fail the old 1.7%)
        st = mp.feed(DailyBar("2026-02-04", c4, 2000.0))
        assert st == UPTREND
        assert mp.distribution_days == 0

    def test_no_ftd_below_1_25_threshold(self):
        # A +1.0% day-4 gain is below the 1.25% threshold -> not an FTD.
        mp = self._into_correction()
        low = self._low
        c1 = low * 1.005
        mp.feed(DailyBar("2026-02-01", c1, 1000.0))
        c2 = c1 * 1.003
        mp.feed(DailyBar("2026-02-02", c2, 1100.0))
        c3 = c2 * 1.002
        mp.feed(DailyBar("2026-02-03", c3, 1200.0))
        c4 = c3 * 1.01           # +1.0% < 1.25% -> no FTD
        st = mp.feed(DailyBar("2026-02-04", c4, 2000.0))
        assert st == CORRECTION

    def test_ftd_requires_volume_up_when_volume_present(self):
        mp = self._into_correction()
        low = self._low
        c1 = low * 1.005
        mp.feed(DailyBar("2026-02-01", c1, 1000.0))
        c2 = c1 * 1.003
        mp.feed(DailyBar("2026-02-02", c2, 1100.0))
        c3 = c2 * 1.002
        mp.feed(DailyBar("2026-02-03", c3, 1200.0))
        c4 = c3 * 1.02        # +2% on day 4 but volume DOWN -> not an FTD
        st = mp.feed(DailyBar("2026-02-04", c4, 500.0))
        assert st == CORRECTION

    def test_rally_reset_on_undercut(self):
        mp = self._into_correction()
        low = self._low
        # Start a rally (day 1 up), then undercut the rally-start low -> reset.
        c1 = low * 1.01
        mp.feed(DailyBar("2026-02-01", c1, 1000.0))            # rally day 1
        c2 = c1 * 1.005
        mp.feed(DailyBar("2026-02-02", c2, 1100.0))            # rally day 2
        undercut = low * 0.97                                  # below rally-start low
        mp.feed(DailyBar("2026-02-03", undercut, 1200.0))      # reset rally
        # Now a fresh day-1 up close; a single +2% here must NOT be an FTD
        # (rally day count was reset to 0).
        c_new = undercut * 1.02
        st = mp.feed(DailyBar("2026-02-04", c_new, 2000.0))
        assert st == CORRECTION

    def test_ftd_gain_only_when_volume_missing(self):
        mp = self._into_correction()
        low = self._low
        c1 = low * 1.005
        mp.feed(DailyBar("2026-02-01", c1, None))
        c2 = c1 * 1.003
        mp.feed(DailyBar("2026-02-02", c2, None))
        c3 = c2 * 1.002
        mp.feed(DailyBar("2026-02-03", c3, None))
        c4 = c3 * 1.02        # +2% on day 4, volume missing -> gain-only FTD
        st = mp.feed(DailyBar("2026-02-04", c4, None))
        assert st == UPTREND


# --------------------------------------------------------------------------- #
# Price-recovery exit (§7 Rev.1)                                              #
# --------------------------------------------------------------------------- #
class TestPriceRecoveryExit:
    def _into_correction(self) -> MarketPulse:
        # 6-DD run from start=100 -> CORRECTION; pre-correction peak = 100.0
        # (the first close, highest observed before/at entry); low ~= 94.15.
        closes, vols = _make_dd_run(6)
        mp = MarketPulse()
        for i, (c, v) in enumerate(zip(closes, vols)):
            mp.feed(DailyBar(f"2026-01-{i+1:02d}", c, v))
        assert mp.state == CORRECTION
        return mp

    def test_close_above_peak_exits_and_resets_dd(self):
        mp = self._into_correction()
        st = mp.feed(DailyBar("2026-02-01", 101.0, 5000.0))  # 101 > peak 100
        assert st == UPTREND
        assert mp.distribution_days == 0  # DD window reset (same as FTD exit)

    def test_no_recovery_while_below_peak(self):
        mp = self._into_correction()
        # up close but still below the pre-correction peak (100) -> stays CORRECTION
        st = mp.feed(DailyBar("2026-02-01", 99.0, 5000.0))
        assert st == CORRECTION

    def test_peak_tracked_per_episode(self):
        # Episode 1 peak = 100; exit via a new high, climb to a higher high,
        # then a second episode must use ITS OWN (higher) peak.
        mp = self._into_correction()
        assert mp.feed(DailyBar("2026-02-01", 101.0, 5000.0)) == UPTREND  # exit ep1
        # Climb to a new cumulative high of 110 (up closes, never DDs).
        mp.feed(DailyBar("2026-02-02", 106.0, 900.0))
        mp.feed(DailyBar("2026-02-03", 110.0, 900.0))
        # 6 fresh distribution days from 110 -> episode 2 CORRECTION, peak = 110.
        c, vol, day = 110.0, 1000.0, 4
        for _ in range(6):
            c *= 0.99
            vol += 100.0
            mp.feed(DailyBar(f"2026-02-{day:02d}", c, vol))
            day += 1
        assert mp.state == CORRECTION
        # 105 exceeds episode-1's peak (100) but NOT episode-2's peak (110):
        # must stay CORRECTION -> proves the peak is per-episode.
        assert mp.feed(DailyBar(f"2026-02-{day:02d}", 105.0, 5000.0)) == CORRECTION
        day += 1
        # A close above episode-2's own peak (110) exits.
        assert mp.feed(DailyBar(f"2026-02-{day:02d}", 111.0, 5000.0)) == UPTREND


# --------------------------------------------------------------------------- #
# Price-drawdown CORRECTION entry (§7 Rev.2)                                   #
# --------------------------------------------------------------------------- #
class TestDrawdownEntry:
    def test_waterfall_crash_falling_volume_enters_correction(self):
        # A waterfall crash with FALLING volume produces ZERO distribution days
        # (a DD needs volume > prev). Rev.2: a >=10% drawdown from the rolling
        # peak must still enter CORRECTION. This is the exact case DD-count
        # missed (KOSPI 2026-03 −19.1% stayed UPTREND under the old rule).
        mp = MarketPulse()
        mp.feed(DailyBar("2026-03-01", 100.0, 5000.0))          # rolling peak
        prices = [97.0, 94.0, 91.0, 89.0]                       # 89 = -11% vs peak
        vol = 5000.0
        st = UPTREND
        for i, p in enumerate(prices):
            vol -= 500.0                                        # volume FALLING => no DD
            st = mp.feed(DailyBar(f"2026-03-{i + 2:02d}", p, vol))
        assert mp.distribution_days == 0                        # zero DDs (falling vol)
        assert st == CORRECTION                                 # entered via -10% drawdown

    def test_no_reenter_after_ftd_exit_still_below_old_peak(self):
        # After an FTD exit the reference peak is RESET to the exit close, so a
        # day still >10% below the OLD peak — but not below the reset peak — must
        # NOT immediately re-enter (anti-flap).
        mp = MarketPulse()
        mp.feed(DailyBar("2026-04-01", 100.0, 5000.0))          # peak
        assert mp.feed(DailyBar("2026-04-02", 84.0, 4000.0)) == CORRECTION  # -16%
        mp.feed(DailyBar("2026-04-03", 84.5, 4100.0))           # rally day 1
        mp.feed(DailyBar("2026-04-06", 85.0, 4200.0))           # day 2
        mp.feed(DailyBar("2026-04-07", 85.3, 4300.0))           # day 3
        # day 4: +1.5% (>=1.25) on rising volume -> FTD -> UPTREND, peak reset ~86.58
        assert mp.feed(DailyBar("2026-04-08", 86.58, 6000.0)) == UPTREND
        # 84.0 is still >10% below the OLD peak (100) yet only ~3% below the reset
        # peak (~86.58) -> stays UPTREND (does NOT re-enter).
        assert mp.feed(DailyBar("2026-04-09", 84.0, 3000.0)) == UPTREND

    def test_reenter_on_new_decline_from_post_exit_peak(self):
        # A genuinely NEW >=10% decline measured from the post-exit (reset) peak
        # DOES re-trigger CORRECTION.
        mp = MarketPulse()
        mp.feed(DailyBar("2026-05-01", 100.0, 5000.0))          # peak
        assert mp.feed(DailyBar("2026-05-04", 84.0, 4000.0)) == CORRECTION
        mp.feed(DailyBar("2026-05-05", 84.5, 4100.0))
        mp.feed(DailyBar("2026-05-06", 85.0, 4200.0))
        mp.feed(DailyBar("2026-05-07", 85.3, 4300.0))
        # FTD exit -> reference peak reset to ~86.58 (86.58 * 0.90 = 77.92).
        assert mp.feed(DailyBar("2026-05-08", 86.58, 6000.0)) == UPTREND
        # 77.0 < 77.92 -> a new >=10% decline from the post-exit peak re-enters.
        assert mp.feed(DailyBar("2026-05-11", 77.0, 3000.0)) == CORRECTION


# --------------------------------------------------------------------------- #
# replay() convenience                                                        #
# --------------------------------------------------------------------------- #
class TestReplay:
    def test_replay_shape_and_values(self):
        closes, vols = _make_dd_run(4)
        bars = _bars(list(zip(closes, vols)))
        mp = MarketPulse()
        out = mp.replay(bars)
        assert len(out) == len(bars)
        assert all(len(t) == 3 for t in out)
        # last row: (date, state, dd_count)
        assert out[-1][1] == UNDER_PRESSURE
        assert out[-1][2] == 4


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
