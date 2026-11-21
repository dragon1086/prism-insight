"""F1/F2 for US financials without an operating income line: deterministic facts + per-report block (2026-11-10)."""
from types import SimpleNamespace

import pytest

pd = pytest.importorskip("pandas")

from prism_core import us_financial_f_gate as gate  # noqa: E402

Q = [pd.Timestamp("2026-07-31"), pd.Timestamp("2026-04-30"), pd.Timestamp("2026-01-31")]


def _frame(rows):
    return pd.DataFrame({c: [v[i] for v in rows.values()] for i, c in enumerate(Q)}, index=list(rows))


def _yf(income, balance, sector="Financial Services", industry="Banks - Diversified"):
    ticker = SimpleNamespace(quarterly_income_stmt=_frame(income), quarterly_balance_sheet=_frame(balance),
                             get_info=lambda: {"sector": sector, "industry": industry})
    return SimpleNamespace(Ticker=lambda _symbol: ticker)


NAN = float("nan")
# BMO-like: no operating income row, pretax profit, equity/assets 5.6%.
BMO = _yf({"Total Revenue": [9908e6, 9537e6, 9799e6], "Pretax Income": [2496e6, 3498e6, 3325e6],
           "Net Income": [1748e6, 2626e6, 2490e6]},
          {"Total Assets": [1538685e6] * 3, "Stockholders Equity": [86523e6] * 3, "Total Debt": [312816e6] * 3})


def test_bank_passes_f1_on_pretax_and_f2_on_equity_to_assets():
    facts = gate.compute("BMO", yf_module=BMO)
    assert facts["subtype"] == "bank" and facts["f1"] is True and facts["f2"] is True
    assert facts["pretax"] == [("2026-07-31", 2496e6), ("2026-04-30", 3498e6)]
    assert abs(facts["equity_to_assets"] - 0.0562) < 1e-3
    block = gate.prompt_block(facts, "ko")
    assert "세전이익 흑자: **통과**" in block and "자기자본/총자산 5% 이상" in block and "5.6%" in block
    assert "영업이익이 없다는 이유로 F1을 미달로 쓰지 말고" in block
    en = gate.prompt_block(facts, "en")
    assert "latest two reported quarters: **PASS**" in en and "well capitalized" in en
    assert gate.stamp(facts) == {"kind": "financial", "subtype": "bank", "basis": "yfinance_no_operating_income",
                                 "f1_rule": "pretax_income", "f2_rule": "equity_to_assets", "f1": True, "f2": True}


def test_sparse_latest_quarter_uses_reported_quarters_and_thin_equity_fails_f2():
    # SAN-like: newest column NaN (not reported by the source) -> the two reported quarters are judged.
    san = _yf({"Total Revenue": [NAN, 15e9, 15e9], "Pretax Income": [NAN, 4932e6, 4800e6], "Net Income": [NAN, 3518e6, 3400e6]},
              {"Total Assets": [1954465e6] * 3, "Stockholders Equity": [70000e6] * 3, "Total Debt": [348067e6] * 3})
    facts = gate.compute("SAN", yf_module=san)
    assert [p for p, _ in facts["pretax"]] == ["2026-04-30", "2026-01-31"] and facts["f1"] is True
    assert facts["f2"] is False and "자기자본/총자산 5% 이상" in gate.prompt_block(facts, "ko")
    assert "**미달**" in gate.prompt_block(facts, "ko")


def test_asset_manager_uses_debt_to_equity_and_loss_fails_f1():
    bx = _yf({"Total Revenue": [3e9, 4e9, 3e9], "Pretax Income": [-100e6, 2808e6, 2000e6], "Net Income": [-50e6, 1229e6, 900e6]},
             {"Total Assets": [49636e6] * 3, "Stockholders Equity": [8838e6] * 3, "Total Debt": [14194e6] * 3},
             industry="Asset Management")
    facts = gate.compute("BX", yf_module=bx)
    assert facts["subtype"] == "financial_other" and facts["f2_rule"] == "debt_to_equity"
    assert facts["f1"] is False and facts["f2"] is True and abs(facts["debt_to_equity"] - 1.606) < 1e-3
    assert "이자부 총차입금/자기자본 200% 미만: **통과**" in gate.prompt_block(facts, "ko")


def test_missing_balance_data_is_not_confirmed_fail():
    thin = _yf({"Total Revenue": [1e9] * 3, "Pretax Income": [1e8] * 3, "Net Income": [8e7] * 3}, {"Total Assets": [1e10] * 3})
    facts = gate.compute("X", yf_module=thin)
    assert facts["f2"] is None and "확인 불가(미달)" in gate.prompt_block(facts, "ko")


def test_non_financials_and_companies_with_operating_income_are_untouched():
    apple = _yf({"Total Revenue": [1e11] * 3, "Operating Income": [3e10] * 3, "Pretax Income": [3e10] * 3},
                {"Total Assets": [4e11] * 3, "Stockholders Equity": [1e11] * 3}, sector="Technology", industry="Consumer Electronics")
    assert gate.compute("AAPL", yf_module=apple) is None
    odd = _yf({"Total Revenue": [1e9] * 3, "Pretax Income": [1e8] * 3}, {"Total Assets": [1e10] * 3},
              sector="Industrials", industry="Conglomerates")
    assert gate.compute("ODD", yf_module=odd) is None   # no operating income row but not a financial: shared rules
    assert gate.prompt_block(None, "ko") == ""


def test_switch_off_and_data_failure_fail_open(monkeypatch):
    monkeypatch.setenv(gate.ENV, "off")
    assert gate.facts_for("BMO", yf_module=BMO) is None
    monkeypatch.setenv(gate.ENV, "on")
    broken = SimpleNamespace(Ticker=lambda _s: (_ for _ in ()).throw(RuntimeError("yfinance down")))
    assert gate.facts_for("BMO", yf_module=broken) is None
    assert gate.facts_for("BMO", yf_module=BMO)["f1"] is True
