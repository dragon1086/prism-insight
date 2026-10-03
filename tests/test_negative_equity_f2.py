"""F2 for US negative-equity issuers: deterministic checks + per-report block (2026-10-03)."""
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from prism_core import negative_equity_f2 as f2  # noqa: E402

COLS = ["2026", "2025", "2024", "2023"]


def _frame(rows):
    return pd.DataFrame({k: v for k, v in rows.items()}, index=COLS).T


def _yf(balance, income, cashflow):
    ticker = SimpleNamespace(quarterly_balance_sheet=_frame(balance), income_stmt=_frame(income),
                             cashflow=_frame(cashflow))
    return SimpleNamespace(Ticker=lambda _symbol: ticker)


# DELL-like: buyback-driven deficit, ND/EBITDA ~1.9x, coverage ~5.7x, FCF positive.
DELL = _yf(
    {"Stockholders Equity": [-1.43e9] * 4, "Retained Earnings": [10.06e9] * 4, "Total Debt": [31.2e9] * 4,
     "Cash Cash Equivalents And Short Term Investments": [8.3e9] * 4},
    {"Net Income": [5.94e9, 4.59e9, 3.37e9, 2.4e9], "EBITDA": [11.9e9] * 4, "EBIT": [8.6e9] * 4,
     "Interest Expense": [1.5e9] * 4},
    {"Free Cash Flow": [8.55e9] * 4, "Repurchase Of Capital Stock": [-6.4e9] * 4,
     "Cash Dividends Paid": [-1.46e9] * 4})
# AMC-like: loss-driven deficit.
AMC = _yf(
    {"Stockholders Equity": [-1.45e9] * 4, "Retained Earnings": [-9.11e9] * 4, "Total Debt": [8.1e9] * 4,
     "Cash Cash Equivalents And Short Term Investments": [1.2e9] * 4},
    {"Net Income": [-0.63e9, -0.35e9, -0.40e9, -0.97e9], "EBITDA": [0.21e9] * 4, "EBIT": [-0.1e9] * 4,
     "Interest Expense": [0.53e9] * 4},
    {"Free Cash Flow": [-0.37e9] * 4})


def test_buyback_driven_deficit_passes_all_four_checks():
    facts = f2.compute("DELL", yf_module=DELL)
    assert facts["checks"] == {"a": True, "b": True, "c": True, "d": True} and facts["passed"]
    block = f2.prompt_block(facts, "ko")
    assert "F2 판정: **통과**" in block and "음수 자본 자체를 미달 사유로 쓰지 않습니다" in block
    assert "F2 verdict: **PASS**" in f2.prompt_block(facts, "en")


def test_loss_driven_deficit_fails():
    facts = f2.compute("AMC", yf_module=AMC)
    assert facts["checks"]["a"] is False and not facts["passed"]
    assert "F2 판정: **미달**" in f2.prompt_block(facts, "ko")


def test_unconfirmed_value_fails_closed():
    no_interest = _yf(
        {"Stockholders Equity": [-1.0e9] * 4, "Retained Earnings": [5e9] * 4, "Total Debt": [10e9] * 4},
        {"Net Income": [1e9, 1e9, 1e9, 1e9], "EBITDA": [5e9] * 4, "EBIT": [4e9] * 4},
        {"Free Cash Flow": [2e9] * 4})
    facts = f2.compute("X", yf_module=no_interest)
    assert facts["checks"]["c"] is None and not facts["passed"]
    assert "확인 불가(미달)" in f2.prompt_block(facts, "ko")


def test_positive_equity_and_disabled_and_errors_leave_the_prompt_unchanged(monkeypatch):
    healthy = _yf({"Stockholders Equity": [3e9] * 4}, {"Net Income": [1e9] * 4}, {"Free Cash Flow": [1e9] * 4})
    assert f2.compute("OK", yf_module=healthy) is None
    assert f2.block_for("OK", yf_module=healthy) == ""
    broken = SimpleNamespace(Ticker=lambda _s: (_ for _ in ()).throw(RuntimeError("network")))
    assert f2.block_for("ERR", yf_module=broken) == ""
    monkeypatch.setenv(f2.ENV, "off")
    assert f2.block_for("DELL", yf_module=DELL) == ""
