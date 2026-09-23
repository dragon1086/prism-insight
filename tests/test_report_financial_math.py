"""Report arithmetic is deterministic, period-bound and separate from trading."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from prism_core.report_financial_math import (
    extract_report_financial_math,
    render_annual_leverage_calculations,
    render_target_upside_calculations,
)


def frames():
    period = pd.Timestamp('2025-12-31')
    balance = pd.DataFrame({period: {'Total Debt': 6_585_000_000, 'Stockholders Equity': 7_170_000_000,
                                    'Cash And Cash Equivalents': 420_000_000}})
    income = pd.DataFrame({period: {'EBITDA': 2_000_000_000}})
    return income, balance


def test_exact_target_upside_uses_report_reference_not_current_quote():
    text = render_target_upside_calculations({'target_mean': 247.4, 'target_median': 250,
                                            'target_high': 260, 'target_low': 220, 'price': 236.1},
                                           234.76, 'regularMarketPrice', '2026-09-22T20:00:00Z', 'dated_recent')
    assert '5.3842%' in text and '5.6000%' not in text
    assert '234.76' in text and '247.40' in text and 'not a final session Close' in text


def test_same_period_debt_ratios_and_definition():
    text = render_annual_leverage_calculations(*frames())
    assert '47.8735%' in text and '53.4000%' not in text
    assert 'Debt / Equity' in text and '0.9184x' in text
    assert '3.0825x' in text and '2025-12-31' in text
    assert '6,585,000,000' in text and '7,170,000,000' in text


@pytest.mark.parametrize('value', [None, float('nan'), float('inf'), True, 0, -1])
def test_invalid_quote_or_target_has_no_fabricated_upside(value):
    assert 'N/A' in render_target_upside_calculations({'target_mean': 247.4}, value)
    text = render_target_upside_calculations({'target_mean': value}, 234.76)
    assert '| Mean target upside | N/A |' in text


def test_different_periods_missing_denominator_and_quarter_not_annualized():
    income, balance = frames()
    income.columns = [pd.Timestamp('2024-12-31')]
    text = render_annual_leverage_calculations(income, balance)
    assert '| Net debt / annual EBITDA | N/A |' in text
    assert '47.8735%' in text
    income, balance = frames()
    income.loc['EBITDA'] = 0
    balance.loc['Stockholders Equity'] = -1
    text = render_annual_leverage_calculations(income, balance)
    assert '| Debt / Equity | N/A |' in text
    assert '| Net debt / annual EBITDA | N/A |' in text
    assert 'quarterly EBITDA is not annualized' in text


def test_ambiguous_and_future_periods_not_combined():
    income, balance = frames()
    duplicate = pd.concat([balance, balance], axis=1)
    assert 'N/A' in render_annual_leverage_calculations(income, duplicate)
    assert '47.8735%' not in render_annual_leverage_calculations(income, duplicate)
    assert '47.8735%' not in render_annual_leverage_calculations(income, balance, as_of='2025-01-01')


@pytest.mark.parametrize('value', [float('nan'), float('inf'), True, None])
def test_invalid_statement_values_do_not_become_ratios(value):
    income, balance = frames()
    balance = balance.astype(object)
    balance.loc['Stockholders Equity'] = value
    assert '| Debt / (Debt + Equity) | N/A |' in render_annual_leverage_calculations(income, balance)


def test_exact_operands_retained_and_zero_debt_not_missing():
    income, balance = frames()
    balance.loc['Total Debt'] = 0
    text = render_annual_leverage_calculations(income, balance)
    assert '| Debt / (Debt + Equity) | 0.0000% |' in text
    assert '-0.2100x' in text  # Valid net cash, not an absent denominator.
    quote = render_target_upside_calculations({'target_mean': 247.405}, 234.765)
    assert '247.405 USD / 234.765 USD' in quote


def test_annual_ratios_do_not_fallback_to_quarterly_or_older_income():
    _, balance = frames()
    text = render_annual_leverage_calculations(None, balance)
    assert '| Net debt / annual EBITDA | N/A |' in text
    assert '47.8735%' in text


def test_financial_appendix_is_a_renderable_markdown_table():
    import markdown

    income, balance = frames()
    blocks = extract_report_financial_math(
        render_target_upside_calculations({'target_mean': 247.4}, 234.76),
        render_annual_leverage_calculations(income, balance))
    html = markdown.markdown(blocks, extensions=['tables'])
    assert html.count('<table>') == 2
    assert '<td>5.3842%</td>' in html and '<td>47.8735%</td>' in html


def load_prefetch():
    path = Path(__file__).resolve().parents[1] / 'prism-us/cores/data_prefetch.py'
    spec = importlib.util.spec_from_file_location('financial_math_prefetch', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_real_prefetch_uses_existing_snapshots_without_more_calls(monkeypatch):
    import yfinance

    module = load_prefetch()
    income, balance = frames()
    calls = []
    values = {'income_stmt': income, 'balance_sheet': balance}

    class Provider:
        def __getattr__(self, name):
            calls.append(name)
            return values.get(name, pd.DataFrame())

    monkeypatch.setattr(yfinance, 'Ticker', lambda ticker: Provider())
    text = module.prefetch_financial_statements('TEST')
    assert '47.8735%' in text
    assert calls == ['income_stmt', 'balance_sheet', 'cashflow', 'quarterly_income_stmt', 'quarterly_balance_sheet', 'quarterly_cashflow']
    assert text.index('47.8735%') < text.index('Annual Income Statement')
    timestamp = (datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()
    info = {'name': 'Test', 'price': 236.1, 'price_field_source': 'currentPrice',
            'regular_market_price': 234.76, 'regular_market_time': timestamp, 'target_mean': 247.4}
    monkeypatch.setattr(module, '_get_us_data_client', lambda: SimpleNamespace(get_company_info=lambda ticker: info))
    quote_text = module.prefetch_stock_info('TEST')
    assert '5.3842%' in quote_text
    blocks = extract_report_financial_math(quote_text, text)
    assert '5.3842%' in blocks and '47.8735%' in blocks and 'Annual Income Statement' not in blocks
    info.update(price_field_source='regularMarketPrice', regular_market_time=1)
    assert '5.3842%' not in module.prefetch_stock_info('TEST')
