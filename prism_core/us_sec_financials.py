"""SEC EDGAR XBRL financial statements in the yfinance statement shape (replaces yfinance financials).

yfinance scrapes Yahoo without a licence; SEC EDGAR is public. One companyfacts
call per issuer (data.sec.gov/api/xbrl/companyfacts) is turned into frames that
look like yfinance ``income_stmt``/``balance_sheet``/``cashflow`` and their
quarterly versions: rows carry the yfinance labels the consumers already read,
columns are period-end Timestamps, newest first. Consumers stay unchanged.

Rules: values are what the issuer filed (latest filing wins for a restated
period). Quarterly flows the issuer filed only year-to-date (cash flows, the
fourth quarter) are differences of filed year-to-date values with the same
start date. A row is built only from us-gaap concepts; anything not filed is
NaN, never zero. Foreign (IFRS) and non-USD filers come back empty (unknown).
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
MAX_FACTS_BYTES = 60_000_000  # JPM companyfacts is ~8 MB; the shared SEC fetcher caps at 5 MB
CACHE_SECONDS = 6 * 3600
ANNUAL_COLUMNS = 4
QUARTER_COLUMNS = 5
_CACHE_DIR = Path(__file__).resolve().parents[1] / "runtime" / "sec_companyfacts"
_memory: dict = {}
_lock = threading.Lock()

# yfinance label -> us-gaap concepts, first concept with a value for the period wins.
DURATION = {
    "Total Revenue": ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                      "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                      "RevenuesNetOfInterestExpense"),
    "Gross Profit": ("GrossProfit",),
    "Operating Income": ("OperatingIncomeLoss",),
    "Pretax Income": ("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
                      "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments"),
    "Net Income": ("NetIncomeLoss",),
    "Net Income Common Stockholders": ("NetIncomeLossAvailableToCommonStockholdersBasic", "NetIncomeLoss"),
    "Interest Expense": ("InterestExpense", "InterestExpenseNonoperating", "InterestExpenseDebt"),
    "Reconciled Depreciation": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
                                "DepreciationAmortizationAndAccretionNet"),
    "Operating Cash Flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "Capital Expenditure": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "Repurchase Of Capital Stock": ("PaymentsForRepurchaseOfCommonStock",),
    "Cash Dividends Paid": ("PaymentsOfDividends", "PaymentsOfDividendsCommonStock"),
    "_tax_withholding": ("PaymentsRelatedToTaxWithholdingForShareBasedCompensation",),
}
PER_SHARE = {"Diluted EPS": ("EarningsPerShareDiluted",), "Basic EPS": ("EarningsPerShareBasic",)}
INSTANT = {
    "Stockholders Equity": ("StockholdersEquity",),
    "Retained Earnings": ("RetainedEarningsAccumulatedDeficit",),
    "Cash And Cash Equivalents": ("CashAndCashEquivalentsAtCarryingValue",),
    "Cash Cash Equivalents And Short Term Investments": ("CashCashEquivalentsAndShortTermInvestments",),
    "Total Assets": ("Assets",),
    "Total Liabilities Net Minority Interest": ("Liabilities",),
    "Long Term Debt": ("LongTermDebtNoncurrent", "LongTermDebtAndCapitalLeaseObligations"),
    "Current Debt": ("DebtCurrent",),
    "_current_long_term_debt": ("LongTermDebtCurrent",),
    "_commercial_paper": ("CommercialPaper",),
    "_short_term_borrowings": ("ShortTermBorrowings",),
}
# Flows filed as cash outflows: yfinance shows them negative.
_NEGATED = {"Capital Expenditure", "Repurchase Of Capital Stock", "Cash Dividends Paid", "_tax_withholding"}
INCOME_ROWS = ("Total Revenue", "Gross Profit", "Operating Income", "Pretax Income", "Interest Expense", "EBIT",
               "EBITDA", "Reconciled Depreciation", "Net Income", "Net Income Common Stockholders",
               "Diluted EPS", "Basic EPS")
BALANCE_ROWS = ("Total Assets", "Total Liabilities Net Minority Interest", "Stockholders Equity",
                "Common Stock Equity", "Retained Earnings", "Cash And Cash Equivalents",
                "Cash Cash Equivalents And Short Term Investments", "Long Term Debt", "Current Debt", "Total Debt")
CASHFLOW_ROWS = ("Operating Cash Flow", "Capital Expenditure", "Free Cash Flow", "Repurchase Of Capital Stock",
                 "Cash Dividends Paid")


def _fetch_json(url: str) -> dict:
    from prism_core.us_official_company_sources import _fetch
    body, _ = _fetch(url, timeout=30, max_bytes=MAX_FACTS_BYTES)
    return json.loads(body)


def _cik(ticker: str) -> int | None:
    from prism_core.corporate_filings import _fetch_sec, _us_cik
    return _us_cik(ticker, _fetch_sec)


def company_facts(ticker: str, *, fetch=_fetch_json, cik_lookup=_cik) -> dict:
    """companyfacts JSON for ``ticker`` (memory + runtime disk cache), {} when SEC has no issuer."""
    cik = cik_lookup(ticker)
    if cik is None:
        return {}
    now = time.time()
    with _lock:
        cached = _memory.get(cik)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1]
    path = _CACHE_DIR / f"CIK{cik:010d}.json"
    try:
        if now - path.stat().st_mtime < CACHE_SECONDS:
            facts = json.loads(path.read_text())
            with _lock:
                _memory[cik] = (now, facts)
            return facts
    except (OSError, ValueError):
        pass
    facts = fetch(FACTS_URL.format(cik=cik))
    try:  # best effort: a failed cache write only costs a refetch
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{os.getpid()}.tmp")
        temporary.write_text(json.dumps(facts))
        os.replace(temporary, path)
    except OSError as error:
        logger.debug("SEC companyfacts cache not saved: %s", type(error).__name__)
    with _lock:
        _memory[cik] = (now, facts)
    return facts


def _days(start, end) -> int:
    return (date.fromisoformat(end) - date.fromisoformat(start)).days


def _latest_filed(rows):
    """One value per (start, end): the most recently filed one."""
    best = {}
    for row in rows:
        key = (row.get("start"), row["end"])
        if key not in best or str(row.get("filed", "")) >= str(best[key].get("filed", "")):
            best[key] = row
    return best


def _concept_rows(gaap, concepts, unit):
    for concept in concepts:
        rows = ((gaap.get(concept) or {}).get("units") or {}).get(unit)
        if rows:
            yield rows


def _flows(gaap, concepts, unit):
    """({annual_end: value}, {quarter_end: value}) for a duration concept, first concept with data per period."""
    annual, quarterly = {}, {}
    for rows in _concept_rows(gaap, concepts, unit):
        facts = _latest_filed([r for r in rows if r.get("start") and r.get("end")])
        a, q, ytd = {}, {}, {}
        for (start, end), row in facts.items():
            days, value = _days(start, end), float(row["val"])
            if 350 <= days <= 380:
                a[end] = value
                ytd.setdefault(start, {})[end] = value
            elif 80 <= days <= 100:
                q[end] = value
            elif 170 <= days <= 285:
                ytd.setdefault(start, {})[end] = value
        for start, by_end in ytd.items():  # quarters filed only as year-to-date differences
            first_quarter = [e for (s, e), v in facts.items() if s == start and 80 <= _days(s, e) <= 100]
            ends = sorted(by_end)
            previous = {e: by_end[e] for e in ends}
            prior_end = first_quarter[0] if first_quarter else None
            prior_value = q.get(prior_end) if prior_end else None
            for end in ends:
                if prior_end is not None and prior_value is not None and end not in q and 80 <= _days(prior_end, end) <= 100:
                    q[end] = previous[end] - prior_value
                prior_end, prior_value = end, previous[end]
        for end, value in a.items():
            annual.setdefault(end, value)
        for end, value in q.items():
            quarterly.setdefault(end, value)
    return annual, quarterly


def _instants(gaap, concepts):
    out = {}
    for rows in _concept_rows(gaap, concepts, "USD"):
        for (_, end), row in _latest_filed([r for r in rows if r.get("end") and not r.get("start")]).items():
            out.setdefault(end, (float(row["val"]), row.get("form", ""), row.get("fp", "")))
    return out


def _month_end(end: str) -> pd.Timestamp:
    """Nearest calendar month end, as yfinance labels periods (52/53-week years: 2025-09-27 -> 09-30,
    2026-05-01 -> 04-30)."""
    day = pd.Timestamp(end)
    previous, current = day - pd.offsets.MonthEnd(1), day + pd.offsets.MonthEnd(0)
    return previous if (day - previous) < (current - day) else current


def _frame(series: dict, rows, count) -> pd.DataFrame:
    ends = sorted({end for values in series.values() for end in values}, reverse=True)[:count]
    if not ends:
        return pd.DataFrame()
    data = {label: [series.get(label, {}).get(end, float("nan")) for end in ends] for label in rows}
    frame = pd.DataFrame.from_dict(data, orient="index", columns=[_month_end(end) for end in ends])
    return frame.dropna(how="all")  # like yfinance, a line never filed is absent so label fallbacks still work


def statements(ticker: str, *, facts: dict | None = None) -> dict[str, pd.DataFrame]:
    """yfinance-shaped statement frames; every frame empty when SEC has no us-gaap USD facts."""
    facts = company_facts(ticker) if facts is None else facts
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    names = ("income_stmt", "quarterly_income_stmt", "balance_sheet", "quarterly_balance_sheet",
             "cashflow", "quarterly_cashflow")
    if not gaap:
        return {name: pd.DataFrame() for name in names}
    annual, quarterly = {}, {}
    for label, concepts in DURATION.items():
        a, q = _flows(gaap, concepts, "USD")
        sign = -1.0 if label in _NEGATED else 1.0
        annual[label] = {k: sign * v for k, v in a.items()}
        quarterly[label] = {k: sign * v for k, v in q.items()}
    for label, concepts in PER_SHARE.items():
        # shortcut: a fourth-quarter EPS is not FY minus nine months (share counts move); only filed
        # three-month EPS is used, so a quarter filed only in the 10-K stays NaN.
        a, q = _flows(gaap, concepts, "USD/shares")
        annual[label] = a
        quarterly[label] = {end: v for end, v in q.items() if _filed_three_month(gaap, concepts, end)}
    for flows in (annual, quarterly):
        for end in set(flows["Pretax Income"]) | set(flows["Operating Income"]):
            pretax, interest = flows["Pretax Income"].get(end), flows["Interest Expense"].get(end)
            # yfinance EBIT: pretax income + interest expense; operating income when no interest expense is filed
            ebit = (pretax + interest if pretax is not None and interest is not None
                    else flows["Operating Income"].get(end) if interest is None else None)
            if ebit is not None:
                flows.setdefault("EBIT", {})[end] = ebit
                dep = flows["Reconciled Depreciation"].get(end)
                if dep is not None:
                    flows.setdefault("EBITDA", {})[end] = ebit + dep
        # yfinance "Repurchase Of Capital Stock" includes shares withheld for employee taxes (DELL FY2026: 6.014 + 0.390 B)
        for end in set(flows["Repurchase Of Capital Stock"]) | set(flows["_tax_withholding"]):
            parts = [flows[k][end] for k in ("Repurchase Of Capital Stock", "_tax_withholding") if end in flows[k]]
            flows["Repurchase Of Capital Stock"][end] = sum(parts)
        for end, ocf in flows["Operating Cash Flow"].items():
            capex = flows["Capital Expenditure"].get(end)
            if capex is not None:
                flows.setdefault("Free Cash Flow", {})[end] = ocf + capex
    balance_q, balance_a = {}, {}
    annual_ends = set(annual["Total Revenue"]) | set(annual["Net Income"])
    for label, concepts in INSTANT.items():
        values = _instants(gaap, concepts)
        balance_q[label] = {end: v for end, (v, _, _) in values.items()}
        balance_a[label] = {end: v for end, (v, form, fp) in values.items() if end in annual_ends}
    for balance in (balance_q, balance_a):
        balance["Common Stock Equity"] = dict(balance["Stockholders Equity"])
        parts = ("_current_long_term_debt", "_commercial_paper", "_short_term_borrowings")
        for end in set().union(*(balance[k] for k in parts)) - set(balance["Current Debt"]):
            balance["Current Debt"][end] = sum(balance[k][end] for k in parts if end in balance[k])
        debt = {}
        for end in set(balance["Long Term Debt"]) | set(balance["Current Debt"]):
            long_term, current = balance["Long Term Debt"].get(end), balance["Current Debt"].get(end)
            if long_term is not None or current is not None:  # one filed part with the other absent = no such debt
                debt[end] = (long_term or 0.0) + (current or 0.0)
        balance["Total Debt"] = debt
    return {
        "income_stmt": _frame(annual, INCOME_ROWS, ANNUAL_COLUMNS),
        "quarterly_income_stmt": _frame(quarterly, INCOME_ROWS, QUARTER_COLUMNS),
        "balance_sheet": _frame(balance_a, BALANCE_ROWS, ANNUAL_COLUMNS),
        "quarterly_balance_sheet": _frame(balance_q, BALANCE_ROWS, QUARTER_COLUMNS),
        "cashflow": _frame(annual, CASHFLOW_ROWS, ANNUAL_COLUMNS),
        "quarterly_cashflow": _frame(quarterly, CASHFLOW_ROWS, QUARTER_COLUMNS),
    }


def _filed_three_month(gaap, concepts, end):
    for rows in _concept_rows(gaap, concepts, "USD/shares"):
        if any(r.get("start") and r["end"] == end and 80 <= _days(r["start"], r["end"]) <= 100 for r in rows):
            return True
    return False


class SecStatements:
    """Drop-in for the statement attributes of ``yfinance.Ticker`` (lazy, one SEC fetch)."""

    def __init__(self, ticker: str):
        self.ticker = ticker
        self._frames = None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._frames is None:
            self._frames = statements(self.ticker)
        if name not in self._frames:
            raise AttributeError(name)
        return self._frames[name]
