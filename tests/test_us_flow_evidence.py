"""Offline math, dated-input and real report/BUY wiring contracts."""
import ast
import importlib.util
import logging
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal
import pytest

from prism_core import flow_evidence as flow

ROOT = Path(__file__).resolve().parents[1]
ASOF = "2026-08-31T21:00:00Z"


def bars(end="2026-08-31"):
    dates = mcal.get_calendar("NYSE").schedule("2026-06-01", end).index
    closes = np.arange(len(dates), dtype=float) + 100
    return pd.DataFrame({"open": closes - 1, "high": closes + 1,
                         "low": closes - 2, "close": closes,
                         "volume": np.arange(len(dates)) + 10.}, index=dates)


def compute(frame, asof=ASOF):
    return flow.compute_us_flow_evidence(frame, asof_utc=asof)


def test_math_scaling_hash_and_no_mutation():
    frame = bars()
    original = frame.copy(deep=True)
    frame.loc[frame.index[-5:], "close"] -= [2, 0, 2, 0, 2]
    result = compute(frame)
    for n in (5, 20):
        sample = frame.tail(n + 1)
        expected = sum(np.sign(np.diff(sample.close)) * sample.volume.iloc[1:]) / sum(sample.volume.iloc[1:])
        assert result["metrics"][f"signed_volume_ratio_{n}"]["value"] == pytest.approx(expected, abs=1e-8)
    s = frame.tail(20)
    expected = sum((2*s.close-s.high-s.low)/(s.high-s.low)*s.volume)/sum(s.volume)
    assert result["metrics"]["cmf_20"]["value"] == pytest.approx(expected, abs=1e-8)
    scaled = frame.copy()
    scaled.volume *= 1000
    assert compute(scaled)["metrics"] == result["metrics"]
    assert compute(frame)["input_hash"] == result["input_hash"]
    assert compute(scaled)["input_hash"] != result["input_hash"]
    assert all(-1 <= m["value"] <= 1 for m in result["metrics"].values())
    compute(original)
    pd.testing.assert_frame_equal(original, bars())


def test_warmup_missing_sessions_are_not_replaced_by_older_rows():
    f = bars()
    r = compute(f.tail(20))["metrics"]
    assert r["cmf_20"]["status"] == "computed"
    assert r["signed_volume_ratio_20"]["reason"] == "missing_window_sessions"
    assert r["signed_volume_ratio_5"]["status"] == "computed"
    assert compute(f.tail(5))["metrics"]["signed_volume_ratio_5"]["value"] is None
    r = compute(f.drop(f.index[-8]))["metrics"]
    assert r["signed_volume_ratio_5"]["status"] == "computed"
    assert r["cmf_20"]["value"] is None
    assert all(v["value"] is None for v in compute(f.iloc[:-1])["metrics"].values())


@pytest.mark.parametrize("column,value", [("volume", -1), ("volume", np.inf),
    ("close", np.nan), ("high", 1), ("low", 10000), ("open", 0), ("volume", "bad"),
    ("volume", True), ("close", False)])
def test_invalid_ohlcv_is_unknown(column, value):
    f = bars().astype(object)
    f.loc[f.index[-1], column] = value
    assert all(v["value"] is None for v in compute(f)["metrics"].values())


def test_duplicates_zero_volume_flat_and_gap_counterexample():
    f = bars()
    assert compute(pd.concat([f, f.tail(1)]))["reason"] == "duplicate_sessions"
    f.volume = 0
    assert all(v["value"] is None for v in compute(f)["metrics"].values())
    f.volume = 1
    f[["open", "high", "low", "close"]] = 100.
    r = compute(f)["metrics"]
    assert all(v["value"] == 0 for v in r.values())
    assert r["cmf_20"]["high_equals_low_zero_contribution_rows"] == 20
    # Each session gaps down, then closes at its own high: negative OBV, positive CMF.
    closes = 200 - np.arange(len(f))
    f["close"] = f["high"] = closes
    f["open"] = f["low"] = closes - .5
    r = compute(f)["metrics"]
    assert r["signed_volume_ratio_20"]["value"] == -1
    assert r["cmf_20"]["value"] == 1
    assert compute(bars())["metrics"]["signed_volume_ratio_20"]["value"] == 1


@pytest.mark.parametrize("asof,cutoff", [
    ("2026-07-03T22:00:00Z", "2026-07-02"),  # observed Independence Day
    ("2026-08-31T19:59:00Z", "2026-08-28"),
    ("2026-08-31T20:00:00Z", "2026-08-31"),
    ("2026-11-27T17:59:00Z", "2026-11-25"),  # Thanksgiving early close
    ("2026-11-27T18:00:00Z", "2026-11-27"),
    ("2026-12-01T20:59:00Z", "2026-11-30"),  # standard time
])
def test_completed_session_cutoff(asof, cutoff):
    r = compute(bars("2026-12-02"), asof)
    assert r["completed_through"] == cutoff
    assert r["metrics"]["signed_volume_ratio_20"]["status"] == "computed"


def test_holiday_intraday_future_excluded_and_timezone_labels():
    f = bars()
    for date in ("2026-08-29", "2026-08-31 12:00", "2026-09-01"):
        extra = f.tail(1).copy()
        extra.index = pd.DatetimeIndex([date])
        r = compute(pd.concat([f, extra]))
        assert r["excluded_rows"] == 1
        assert r["metrics"] == compute(f)["metrics"]
    f.index = f.index.tz_localize("America/New_York")
    assert compute(f)["metrics"] == compute(bars())["metrics"]
    assert compute(f, "2026-08-31")["reason"].startswith("invalid_dated_input")


def test_holdings_stale_unknown_future_are_not_daily_trades():
    holders = {"institutional_holders": pd.DataFrame({"Date Reported": [
        "2025-12-31", None, "2027-03-31"], "Shares": [1, 2, 3]})}
    snapshot = flow.describe_us_holdings(holders, asof_utc=ASOF)
    assert snapshot["report_dates"] == ["2025-12-31"]
    assert snapshot["fetched_at_utc"].startswith("2026-08-31")
    assert snapshot["unknown_report_date_rows"] == snapshot["future_report_date_rows"] == 1
    assert snapshot["daily_institutional_flow"] == "MISSING_not_supplied"
    assert holders["institutional_holders"].Shares.tolist() == [1, 2, 3]


def load_file(relative):
    spec = importlib.util.spec_from_file_location("us_flow_test_" + Path(relative).stem, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def extract_method(relative, method):
    tree = ast.parse((ROOT / relative).read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == method)
    module = ast.Module(body=[node], type_ignores=[])
    namespace = {"logger": logging.getLogger(__name__),
                 "_import_from_main_cores": lambda *a: types.SimpleNamespace(get_market_pulse_detail=lambda *a: None)}
    exec(compile(ast.fix_missing_locations(module), relative, "exec"), namespace)  # noqa: S102 - own repository method only
    return namespace[method]


def test_same_source_report_and_real_buy_facts_no_new_fetch(monkeypatch):
    frame = bars()
    calls = []
    class Client:
        def get_ohlcv(self, ticker, **kwargs):
            calls.append(("ohlcv", ticker))
            return frame.copy()
        def get_index_data(self, ticker, **kwargs):
            calls.append(("index", ticker))
            return frame.copy()
    original = flow.compute_us_flow_evidence
    monkeypatch.setattr(flow, "compute_us_flow_evidence", lambda df, **kw: original(df, asof_utc=ASOF))
    prefetch = load_file("prism-us/cores/data_prefetch.py")
    monkeypatch.setattr(prefetch, "_get_us_data_client", Client)
    report = prefetch.prefetch_us_stock_ohlcv("FIXTURE")
    expected = flow.render_flow_evidence(original(frame, asof_utc=ASOF))
    assert expected in report
    assert calls == [("ohlcv", "FIXTURE")]
    monkeypatch.setitem(sys.modules, "cores.us_data_client", types.SimpleNamespace(get_us_data_client=Client))
    monkeypatch.setitem(sys.modules, "observability.trend_research", types.SimpleNamespace(cache_snapshot=lambda *a, **kw: None))
    method = extract_method("prism-us/us_stock_tracking_agent.py", "_get_trend_facts")
    facts = method(types.SimpleNamespace(), "FIXTURE")
    assert expected in facts
    assert "T1_hit" in facts and "T2_hit" in facts
    assert calls == [("ohlcv", "FIXTURE"), ("ohlcv", "FIXTURE"), ("index", "^GSPC")]
    wiring = (ROOT / "prism-us/cores/agents/__init__.py").read_text()
    assert 'prefetched_data=pf.get("stock_ohlcv")' in wiring
    buy = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text()
    assert 'trend_facts = self._get_trend_facts(ticker)' in buy
    assert '{trend_facts}' in buy


@pytest.mark.parametrize("language", ["ko", "en"])
def test_bilingual_agent_contracts(language, monkeypatch):
    fake = types.ModuleType("mcp_agent.agents.agent")
    fake.Agent = lambda **kwargs: types.SimpleNamespace(**kwargs)
    monkeypatch.setitem(sys.modules, "mcp_agent.agents.agent", fake)
    agents = load_file("prism-us/cores/agents/stock_price_agents.py")
    trading = load_file("prism-us/cores/agents/trading_agents.py")
    contract = flow.us_flow_interpretation_contract(language)
    agent = agents.create_us_institutional_holdings_analysis_agent(
        ticker="FIXTURE", company_name="Fixture", reference_date="20260831",
        max_years_ago="20240831", max_years=2, language=language)
    assert contract in agent.instruction
    assert contract in trading.create_us_trading_scenario_agent(language).instruction
    assert contract not in trading.create_us_sell_decision_agent(language).instruction


def test_legacy_rule_constants_unchanged():
    module = load_file("prism-us/cores/data_prefetch.py")
    assert (module.DISTRIBUTION_WINDOW, module.DISTRIBUTION_DROP_PCT,
            module.DISTRIBUTION_RECOVERY_PCT) == (25, .2, 5.)
    assert (module.HIVOL_DD_VOL_PCT, module.HIVOL_DD_DRAWDOWN_PCT,
            module.HIVOL_DD_NET_DECLINE_PCT, module.HIVOL_DD_CONFIDENCE) == (2.5, 8., -3., .55)
    source = (ROOT / "prism-us/us_stock_tracking_agent.py").read_text()
    assert "close <= ma20 * 0.95" in source


def test_prefetch_holder_provenance_and_context_failure_preserves_ohlcv(monkeypatch):
    prefetch = load_file("prism-us/cores/data_prefetch.py")
    holder_frame = pd.DataFrame({"Date Reported": ["2025-12-31", "2099-01-01"],
                                 "Holder": ["Old Fund", "Future Fund"], "Shares": [10, 20]})
    calls = []
    class Client:
        def get_institutional_holders(self, ticker):
            calls.append("holders")
            return {"institutional_holders": holder_frame}
        def get_ohlcv(self, ticker, **kwargs):
            calls.append("ohlcv")
            return bars()
    monkeypatch.setattr(prefetch, "_get_us_data_client", Client)
    text = prefetch.prefetch_us_holder_info("FIXTURE")
    assert '"future_report_date_rows":1' in text
    assert "2025-12-31" in text and "fetched_at_utc" in text
    assert "Old Fund" in text and "source_hashes" in text
    assert "Future Fund" not in text and "2099-01-01" not in text
    def broken(*args, **kwargs):
        raise ValueError("private/provider/location")
    monkeypatch.setattr(prefetch, "compute_us_flow_evidence", broken)
    text = prefetch.prefetch_us_stock_ohlcv("FIXTURE")
    assert "OHLCV: FIXTURE" in text and "calculation_unavailable" in text
    assert "private/provider/location" not in text
    assert calls == ["holders", "ohlcv"]


def test_known_split_volume_units_are_unknown_not_cross_split_proxy():
    f = bars()
    f["stock_splits"] = 0.
    f.loc[f.index[-10], "stock_splits"] = 4.
    r = compute(f)["metrics"]
    assert r["signed_volume_ratio_5"]["status"] == "computed"
    assert r["signed_volume_ratio_20"]["reason"] == "known_split_unadjusted_volume"
    assert r["cmf_20"]["value"] is None
def test_holder_dates_use_us_civil_day_and_count_undated_major_rows():
    holders = {
        "major_holders": pd.DataFrame({"Value": [0.6]}),
        "institutional_holders": pd.DataFrame({"Date Reported": ["2026-07-01"], "Shares": [100]}),
    }
    asof = "2026-07-01T00:30:00Z"  # Still June 30 in New York.
    result = flow.describe_us_holdings(holders, asof_utc=asof)
    assert result["future_report_date_rows"] == 1
    assert result["unknown_report_date_rows"] == 1
    assert result["report_dates"] == []
    assert flow.holdings_asof_frame(holders["institutional_holders"], asof_utc=asof).empty


def test_prefetch_holder_percents_are_rounded_not_raw_fractions(monkeypatch):
    prefetch = load_file("prism-us/cores/data_prefetch.py")
    major = pd.DataFrame({"Value": [0.0123456, 0.92794997, 0.95123456, 1234.0]},
                         index=pd.Index(["insidersPercentHeld", "institutionsPercentHeld",
                                         "institutionsFloatPercentHeld", "institutionsCount"],
                                        name="Breakdown"))
    institutional = pd.DataFrame({"Date Reported": ["2025-12-31"], "Holder": ["Old Fund"],
                                  "pctHeld": [0.08231234], "Shares": [10], "pctChange": [-0.01234]})

    class Client:
        def get_institutional_holders(self, ticker):
            return {"major_holders": major, "institutional_holders": institutional}

    monkeypatch.setattr(prefetch, "_get_us_data_client", Client)
    text = prefetch.prefetch_us_holder_info("DDOG")
    for shown in ("| institutionsPercentHeld | 92.79% |", "| insidersPercentHeld | 1.23% |",
                  "95.12%", "8.23%", "-1.23%", "| institutionsCount | 1234.0 |"):
        assert shown in text, shown
    for raw in ("0.92794997", "92.794997", "0.08231234", "0.0123456"):
        assert raw not in text, raw
    assert "source_hashes" in text  # provenance still hashes the original provider frames
