"""US quarterly results table collected by code (2026-10-07).

The US company-status writer had the raw yfinance statements but wrote only annual operating income and
quarterly net income/EPS (its collection list named "Operating Expense", not operating income). The BUY F1
gate (latest two quarters' operating income) then failed as "unverifiable" for NVIDIA, Meta, AMD, AES and SMCI,
most of all in the MCP-less re-entry recheck. The table is now computed by code and appended to section 2-1.
"""
import importlib.util
from pathlib import Path

import pandas as pd

from prism_core.us_quarterly_facts import quarterly_rows, render_us_quarterly_facts

ROOT = Path(__file__).resolve().parents[1]
URLS = {k: "https://example.test/" + k for k in ("key_statistics", "financials", "analysis")}


def statement(rows, quarters):
    """yfinance quarterly_income_stmt shape: rows = line items, columns = quarter ends (newest first)."""
    columns = [pd.Timestamp(q) for q in quarters]
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(columns)}, index=list(rows))


AES = statement({  # 2026-10 values (USD); EPS missing for 2025-12 as yfinance returns it
    "Total Revenue": [3422e6, 3180e6, 3101e6, 3351e6, 2855e6, float("nan")],
    "Operating Income": [630e6, 585e6, 513e6, 689e6, 404e6, float("nan")],
    "Net Income Common Stockholders": [426e6, 487e6, 325e6, 634e6, -105e6, float("nan")],
    "Diluted EPS": [0.6, 0.68, float("nan"), 0.89, -0.15, 0.07],
}, ["2026-06-30", "2026-03-31", "2025-12-31", "2025-09-30", "2025-06-30", "2025-03-31"])


def test_rows_are_oldest_first_and_keep_eps_only_quarters():
    rows = quarterly_rows(AES)
    assert [r["period"] for r in rows] == ["2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    assert rows[-1]["operating_income"] == 630e6 and rows[2]["diluted_eps"] is None


def test_korean_table_states_latest_two_quarters_operating_profit_and_yoy():
    text = render_us_quarterly_facts(quarterly_rows(AES), "ko")
    assert "| 2026-03-31 | 3,180.0 | 585.0 | 487.0 | 0.68 |" in text
    assert "2026-03-31 585.0 (흑자) / 2026-06-30 630.0 (흑자) → 두 분기 모두 영업흑자" in text
    assert "최근 분기 2026-06-30 vs 전년 동기 2025-06-30: 매출 +19.9%, 영업이익 +55.9%, 희석 EPS -0.15 → 0.60" in text


def test_losses_and_a_bank_without_operating_income():
    loss = statement({"Total Revenue": [10e6, 9e6], "Operating Income": [-1e6, -2e6], "Net Income": [-1e6, -3e6]},
                     ["2026-06-30", "2026-03-31"])
    assert "→ 두 분기 모두 영업적자" in render_us_quarterly_facts(quarterly_rows(loss), "ko")
    bank = statement({"Total Revenue": [15519e6, 15444e6], "Net Income": [3518e6, 5455e6]}, ["2026-06-30", "2026-03-31"])
    text = render_us_quarterly_facts(quarterly_rows(bank), "ko")
    assert "영업이익 항목이 없습니다" in text and "| 2026-06-30 | 15,519.0 | N/A | 3,518.0 | N/A |" in text


def test_english_and_empty():
    assert "operating profit in both quarters" in render_us_quarterly_facts(quarterly_rows(AES), "en")
    assert render_us_quarterly_facts([], "ko") == "" and quarterly_rows(None) == []


def test_company_status_writer_gets_the_table():
    spec = importlib.util.spec_from_file_location("us_status_agents", ROOT / "prism-us/cores/agents/company_info_agents.py")
    agents = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(agents)
    table = render_us_quarterly_facts(quarterly_rows(AES), "ko")
    agent = agents.create_us_company_status_agent("AES", "AES", "20261007", URLS, "ko", {"stock_info": "S"},
                                                  quarterly_facts=table)
    assert table in agent.instruction and "자동 첨부" in agent.instruction
    plain = agents.create_us_company_status_agent("AES", "AES", "20261007", URLS, "ko", {"stock_info": "S"})
    assert "분기 실적 표" not in plain.instruction


def test_prefetch_stashes_rows(monkeypatch):
    spec = importlib.util.spec_from_file_location("us_prefetch_q", ROOT / "prism-us/cores/data_prefetch.py")
    prefetch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prefetch)

    class Ticker:
        def __init__(self, _):
            empty = pd.DataFrame()
            self.income_stmt = self.balance_sheet = self.cashflow = empty
            self.quarterly_balance_sheet = self.quarterly_cashflow = empty
            self.quarterly_income_stmt = AES

    import yfinance
    monkeypatch.setattr(yfinance, "Ticker", Ticker)
    out = {}
    prefetch.prefetch_financial_statements("AES", quarterly_out=out)
    assert [r["period"] for r in out["quarterly_results"]][-1] == "2026-06-30"
