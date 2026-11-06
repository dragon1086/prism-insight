"""SEC companyfacts -> yfinance-shaped statements, offline."""
import math

import pandas as pd

from prism_core import us_sec_financials as S


def fact(start, end, val, form="10-Q", filed="2026-08-01"):
    row = {"end": end, "val": val, "form": form, "fp": "Q", "filed": filed}
    if start:
        row["start"] = start
    return row


FACTS = {"facts": {"us-gaap": {
    "Revenues": {"units": {"USD": [
        fact("2025-02-01", "2026-01-30", 400, "10-K", "2026-03-10"),
        fact("2025-02-01", "2025-05-02", 90), fact("2025-02-01", "2025-08-01", 190),
        fact("2025-05-03", "2025-08-01", 100), fact("2025-02-01", "2025-10-31", 300),
        fact("2025-08-02", "2025-10-31", 110),
        fact("2025-08-02", "2025-10-31", 111, filed="2026-12-01"),  # restated later: latest filing wins
    ]}},
    "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [  # cash flow filed year-to-date only
        fact("2025-02-01", "2025-05-02", 10), fact("2025-02-01", "2025-08-01", 25),
        fact("2025-02-01", "2025-10-31", 45), fact("2025-02-01", "2026-01-30", 70, "10-K")]}},
    "PaymentsForRepurchaseOfCommonStock": {"units": {"USD": [fact("2025-02-01", "2026-01-30", 6, "10-K")]}},
    "PaymentsRelatedToTaxWithholdingForShareBasedCompensation": {"units": {"USD": [
        fact("2025-02-01", "2026-01-30", 1, "10-K")]}},
    "EarningsPerShareDiluted": {"units": {"USD/shares": [
        fact("2025-08-02", "2025-10-31", 1.1), fact("2025-02-01", "2026-01-30", 4.0, "10-K")]}},
    "StockholdersEquity": {"units": {"USD": [fact(None, "2026-01-30", -5, "10-K"), fact(None, "2025-10-31", -4)]}},
}}}


def test_statements_follow_yfinance_shape_and_rules():
    out = S.statements("X", facts=FACTS)
    q = out["quarterly_income_stmt"]
    assert list(q.columns) == [pd.Timestamp("2026-01-31"), pd.Timestamp("2025-10-31"),
                               pd.Timestamp("2025-07-31"), pd.Timestamp("2025-04-30")]  # nearest month ends
    assert q.at["Total Revenue", pd.Timestamp("2026-01-31")] == 100  # Q4 = FY - nine months
    assert q.at["Total Revenue", pd.Timestamp("2025-10-31")] == 111  # restatement
    assert math.isnan(q.at["Diluted EPS", pd.Timestamp("2026-01-31")])  # Q4 EPS not filed: unknown, not derived
    assert "Operating Income" not in q.index  # never filed: absent, not zero
    qc = out["quarterly_cashflow"]
    assert [qc.at["Operating Cash Flow", pd.Timestamp(d)] for d in ("2025-04-30", "2025-07-31", "2025-10-31",
                                                                  "2026-01-31")] == [10, 15, 20, 25]
    assert out["cashflow"].at["Repurchase Of Capital Stock", pd.Timestamp("2026-01-31")] == -7
    assert out["quarterly_balance_sheet"].at["Stockholders Equity", pd.Timestamp("2025-10-31")] == -4


def test_no_us_gaap_facts_is_all_empty():
    assert all(frame.empty for frame in S.statements("SPOT", facts={"facts": {"ifrs-full": {}}}).values())
