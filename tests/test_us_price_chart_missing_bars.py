"""Real mplfinance rendering must tolerate a partially missing source bar."""
import importlib.util
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pytest

from prism_core.report_technical_facts import build_report_technical_facts

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def chart():
    spec = importlib.util.spec_from_file_location('missing_bars_chart', ROOT / 'prism-us/cores/us_stock_chart.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    yield module
    plt.close('all')


def data(count=60):
    close = np.arange(count, dtype=float) + 100
    return pd.DataFrame({'Open': close - 1, 'High': close + 1, 'Low': close - 2,
                         'Close': close, 'Volume': 1000.}, index=pd.bdate_range('2026-01-01', periods=count))


def test_real_render_partial_terminal_bar_without_filling_or_mutation(chart):
    source = data()
    source.iloc[-1, source.columns.get_loc('Close')] = np.nan
    original = source.copy(deep=True)
    fig = chart.create_us_price_chart('TEST', 'Example', source)
    assert fig is not None
    output = BytesIO()
    fig.savefig(output, format='png')
    assert output.getvalue().startswith(b'\x89PNG') and len(output.getvalue()) > 10000
    assert f'Plotted through {source.index[-2]:%Y-%m-%d}' in fig.texts[-1].get_text()
    assert f'source latest {source.index[-1]:%Y-%m-%d}' in fig.texts[-1].get_text()
    assert 'not filled' in fig.texts[-1].get_text()
    pd.testing.assert_frame_equal(source, original)
    assert 'data:image/jpeg;base64,' in chart.get_us_price_chart_html('TEST', 'Example', source)


def test_interior_gap_does_not_bridge_ma_window(chart):
    source = data()
    source.iloc[-15, source.columns.get_loc('Close')] = np.nan
    fig = chart.create_us_price_chart('TEST', 'Example', source)
    assert fig is not None
    expected = source.Close.rolling(20).mean().loc[source.Close.notna()]
    ma20 = fig.axes[0].lines[1].get_ydata()
    np.testing.assert_allclose(ma20, expected.to_numpy(), equal_nan=True)
    assert np.isnan(ma20[-1])


def test_short_or_all_incomplete_history(chart):
    assert chart.create_us_price_chart('TEST', 'Example', data(5)) is not None
    missing = data(5)
    missing['Close'] = np.nan
    assert chart.create_us_price_chart('TEST', 'Example', missing) is None


def test_raw_split_restarts_chart_mas_like_canonical_facts(chart):
    source = data(250)
    source['Stock Splits'] = 0.0
    source.loc[source.index[-20], 'Stock Splits'] = 2.0
    source.attrs['price_basis'] = 'provider_unadjusted_close'
    original = source.copy(deep=True)
    facts = build_report_technical_facts(source)
    assert facts['indicators']['SMA50'] is None
    fig = chart.create_us_price_chart('TEST', 'Example', source)
    assert fig is not None
    assert len(fig.axes[0].lines) == 2  # MA10 and MA20, never a split-bridging MA50.
    assert fig.axes[0].lines[1].get_ydata()[-1] == facts['indicators']['SMA20']
    assert 'MAs restart at known split' in fig.texts[-1].get_text()
    output = BytesIO()
    fig.savefig(output, format='png')
    assert output.getvalue().startswith(b'\x89PNG')
    pd.testing.assert_frame_equal(source, original)
