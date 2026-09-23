"""Small report-only calculations from existing, explicitly aligned snapshots."""
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from numbers import Real

import pandas as pd

START = '<!-- REPORT_FINANCIAL_MATH_START -->'
END = '<!-- REPORT_FINANCIAL_MATH_END -->'


def _number(value):
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(value):
        return None
    return Decimal(str(value))


def _operand(value, minimum_decimals=0):
    text = format(value, ',f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    if minimum_decimals:
        whole, _, fraction = text.partition('.')
        text = whole + '.' + fraction.ljust(minimum_decimals, '0')
    return text


def _block(title, rows, note):
    return '\n'.join([START, '### ' + title, note, '| Calculation | Value | Input basis / formula |',
                      '|---|---|---|', *['| ' + ' | '.join(row) + ' |' for row in rows], END]) + '\n\n'


def render_target_upside_calculations(info, reference_price, reference_basis='unknown',
                                      reference_time=None, freshness='unknown'):
    reference = _number(reference_price)
    reference = reference if reference is not None and reference > 0 else None
    rows = []
    for label, key in (('Mean', 'target_mean'), ('Median', 'target_median'),
                       ('High', 'target_high'), ('Low', 'target_low')):
        target = _number(info.get(key))
        if reference is None or target is None or target <= 0:
            value, basis = 'N/A', 'missing/invalid positive target or report reference price'
        else:
            value = f'{(target / reference - 1) * 100:.4f}%'
            basis = f'({_operand(target, 2)} USD / {_operand(reference, 2)} USD - 1) × 100'
        rows.append((f'{label} target upside', value, basis))
    note = (f'Report reference: {reference_basis}; market time: {reference_time or "unknown"}; '
            f'freshness: {freshness}. This is arithmetic, not a final session Close or a price forecast. '
            'Analyst target publication time is unverified; no return guarantee. '
            'Use these values rather than recalculating against another quote.')
    return _block('Code-calculated analyst target upside', rows, note)


def _period_columns(frame, as_of):
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return {}
    periods = {}
    for column in frame.columns:
        if not isinstance(column, (str, date, datetime, pd.Timestamp)):
            continue
        if isinstance(column, str) and not re.match(r'^\d{4}-\d{2}-\d{2}(?:$|[ T])', column):
            continue
        try:
            period = pd.Timestamp(column).date()
        except (ValueError, TypeError):
            continue
        if pd.isna(period) or period > as_of:
            continue
        periods.setdefault(period, []).append(column)
    return {period: columns[0] for period, columns in periods.items() if len(columns) == 1}


def _cell(frame, columns, period, label):
    if period not in columns or label not in frame.index or not frame.index.is_unique:
        return None
    return _number(frame.loc[label, columns[period]])


def render_annual_leverage_calculations(income, balance, as_of=None):
    """Annual same-period ratios only; no TTM/quarter multiplication or row aliases."""
    cutoff = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or datetime.now(timezone.utc).date()
    balance_columns = _period_columns(balance, cutoff)
    income_columns = _period_columns(income, cutoff)
    period = max(balance_columns) if balance_columns else None
    debt = _cell(balance, balance_columns, period, 'Total Debt')
    equity = _cell(balance, balance_columns, period, 'Stockholders Equity')
    cash = _cell(balance, balance_columns, period, 'Cash And Cash Equivalents')
    ebitda = _cell(income, income_columns, period, 'EBITDA')
    net_debt = _cell(balance, balance_columns, period, 'Net Debt')
    net_basis = f'provider Net Debt {_operand(net_debt)}' if net_debt is not None else 'Net Debt unavailable'
    if net_debt is None and debt is not None and debt >= 0 and cash is not None and cash >= 0:
        net_debt = debt - cash
        net_basis = f'Total Debt {_operand(debt)} - Cash And Cash Equivalents {_operand(cash)}'
    rows = []
    valid_capital = debt is not None and debt >= 0 and equity is not None and equity > 0
    rows.append(('Debt / Equity', f'{debt / equity:.4f}x' if valid_capital else 'N/A',
                 f'Total Debt {_operand(debt)} / Stockholders Equity {_operand(equity)}' if valid_capital else 'missing/invalid same-period debt or positive equity'))
    rows.append(('Debt / (Debt + Equity)', f'{debt / (debt + equity) * 100:.4f}%' if valid_capital else 'N/A',
                 f'{_operand(debt)} / ({_operand(debt)} + {_operand(equity)}) × 100' if valid_capital else 'missing/invalid same-period capital inputs'))
    valid_leverage = net_debt is not None and ebitda is not None and ebitda > 0
    rows.append(('Net debt / annual EBITDA', f'{net_debt / ebitda:.4f}x' if valid_leverage else 'N/A',
                 f'({net_basis}) / annual EBITDA {_operand(ebitda)}' if valid_leverage else 'missing/invalid exact same-period net debt or positive annual EBITDA'))
    note = (f'Annual statement period ended: {period.isoformat() if period else "unavailable"}. '
            'Source: existing yfinance annual statements; amounts in their same provider currency, ratios dimensionless. '
            'Debt means interest-bearing Total Debt, not Total Liabilities; equity means Stockholders Equity. '
            'Net debt may be negative (net cash). No quarter/annual mixing: quarterly EBITDA is not annualized. '
            'This historical annual-period calculation is not certified current or a point-in-time filing vintage.')
    return _block('Code-calculated annual leverage', rows, note)


def extract_report_financial_math(*texts):
    """Extract only deterministic blocks for downstream synthesis, without tables of raw filings."""
    blocks = []
    for text in texts:
        if not isinstance(text, str):
            continue
        for match in re.finditer(re.escape(START) + r'(.*?)' + re.escape(END), text, re.DOTALL):
            block = match.group(1).strip()
            if block not in blocks:
                blocks.append(block)
    return '\n\n'.join(blocks)
