import math

import pandas as pd
import pytest

from tools import ops_watchdog, watch_extreme_fear as W


def test_wilder_rsi_direction_and_balance():
    assert W.wilder_rsi(pd.Series([100.0 - i for i in range(60)])) == 0
    assert W.wilder_rsi(pd.Series([100.0 + i for i in range(60)])) == 100
    assert W.wilder_rsi(pd.Series([100.0, 101.0] * 100)) == pytest.approx(50, abs=3)


@pytest.mark.parametrize("values,met", [
    ((28.4, 5.8, 30.6), True),     # 2026-03-30, listed signal
    ((26.6, 18.6, 35.9), False),   # 2026-11-20: CNN value above 10
    ((30, 10, 30), True),
    ((30.1, 5, 40), False),
    ((25, 5, 29.9), False),
])
def test_all_three_conditions(values, met):
    assert all(W.checks(*values)) is met


def test_missing_input_is_an_error_not_a_miss():
    with pytest.raises(ValueError):
        W.checks(math.nan, 5, 40)


def test_watchdog_reads_the_summary_line():
    job = next(j for j in ops_watchdog.JOBS if j.key == "extreme_fear_us")
    assert job.heartbeat in open(W.__file__, encoding="utf-8").read()
