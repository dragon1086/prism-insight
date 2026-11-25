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


def test_today_comes_from_the_index_not_the_after_hours_bar():
    days = pd.to_datetime(["2026-11-23", "2026-11-24", "2026-11-25"])
    ndx = pd.Series([100.0, 100.0, 90.0], index=days)
    qqq = pd.Series([50.0, 50.0, 48.0], index=days)    # 48 = after-hours print
    assert W.qqq_closes(qqq, ndx, days[-1].date()).tolist() == [50.0, 50.0, 45.0]
    with pytest.raises(ValueError):
        W.qqq_closes(qqq, ndx[:2], days[-1].date())    # index not updated yet
    with pytest.raises(ValueError):
        W.qqq_closes(qqq.iloc[[0, 2]], ndx, days[-1].date())    # a settled session is missing
