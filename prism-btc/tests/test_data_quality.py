import pytest

from analysis.data_quality import require_regular_bars


def test_contiguous_execution_data_is_accepted():
    require_regular_bars([0, 1800000, 3600000], 1800000)


@pytest.mark.parametrize("times", [[0, 3600000], [0, 0], [1800000, 0], []])
def test_bad_data_cannot_produce_backtest_metrics(times):
    with pytest.raises(ValueError):
        require_regular_bars(times, 1800000)
