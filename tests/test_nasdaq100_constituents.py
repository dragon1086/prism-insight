import ast
import logging
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
import requests


def load_function():
    path = Path(__file__).parents[1] / "prism-us/cores/us_surge_detector.py"
    tree = ast.parse(path.read_text())
    function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "get_nasdaq100_tickers")
    namespace = {"pd": pd, "List": list, "logger": logging.getLogger(__name__)}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)  # noqa: S102 - trusted local function
    return namespace[function.name]


def install_pages(monkeypatch, pages):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["timeout"] == 10
        page = pages[len(calls) - 1]
        return SimpleNamespace(text=page, raise_for_status=lambda: None)

    monkeypatch.setattr(requests, "get", get)
    return calls


def table(**columns):
    return pd.DataFrame(columns).to_html(index=False)


def test_new_page_validates_normalizes_and_deduplicates(monkeypatch):
    page = table(Ticker=["AAPL", "BRK.B", "AAPL"], Company=["Apple", "Berkshire", "Apple"], ICBIndustry=["Tech"] * 3)
    calls = install_pages(monkeypatch, [page])
    assert load_function()() == ["AAPL", "BRK-B"]
    assert len(calls) == 1
    assert calls[0].endswith("List_of_NASDAQ-100_companies")


def test_legacy_page_fallback_once(monkeypatch):
    calls = install_pages(monkeypatch, [table(Year=[2026]), table(Ticker=["MSFT"], Company=["Microsoft"])])
    assert load_function()() == ["MSFT"]
    assert len(calls) == 2
    assert calls[1].endswith("Nasdaq-100")


@pytest.mark.parametrize("bad", [
    table(Company=["Apple"]),
    table(Ticker=[123], Company=["Apple"]),
    table(Ticker=["AAPL"], Company=[None]),
    table(Ticker=["bad ticker"], Company=["Apple"]),
    table(Ticker=["AAPL"], Company=["Apple"], Removed=["2026"]),
    table(Ticker=["AAPL"], Company=["Apple"]) * 2,
    "<table><tr><th colspan='2'>Added</th></tr><tr><th>Ticker</th><th>Company</th></tr><tr><td>AAPL</td><td>Apple</td></tr></table>",
])
def test_invalid_or_ambiguous_tables_fail_closed(monkeypatch, bad):
    calls = install_pages(monkeypatch, [bad, bad])
    assert load_function()() == []
    assert len(calls) == 2


def test_current_members_not_history(monkeypatch):
    historical = table(Ticker=["OLD"], Company=["Former"], Date=["2001"])
    current = table(Ticker=["NVDA"], Company=["Nvidia"], ICBSubsector=["Semiconductors"])
    install_pages(monkeypatch, [historical + current])
    assert load_function()() == ["NVDA"]
