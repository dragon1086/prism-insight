"""KR quantity windows and prompt wiring, with fake providers only."""

import ast
import copy
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cores import data_prefetch
from cores.agents import get_agent_directory
from cores.agents.trading_agents import create_trading_scenario_agent
from prism_core.kr_flow_evidence import (
    compute_kr_flow_evidence,
    render_kr_flow_evidence,
)

ASOF = "2026-09-12T00:00:00Z"


def fixture_rows(n=31):
    # Synthetic observed reference sessions; not an official Korean calendar.
    dates = [str(d.date()) for d in pd.bdate_range(end="2026-09-11", periods=n)]
    flow = {day: {"외국인합계": i + 1, "기관합계": -i} for i, day in enumerate(dates)}
    price = {day: {"Close": 100, "Volume": 1000} for day in dates}
    return flow, price, copy.deepcopy(price)


def test_exact_windows_quantities_streaks_and_repeat():
    inputs = fixture_rows()
    a = compute_kr_flow_evidence(*inputs, asof_utc=ASOF)
    assert a == compute_kr_flow_evidence(*inputs, asof_utc=ASOF)
    for n in (5, 20, 30):
        w = a["windows"][str(n)]
        assert w["status"] == "OK"
        assert w["net_shares"]["combined"] == n
        assert w["net_shares"]["foreign"] == sum(range(32 - n, 32))
        assert w["positive_sessions"] == {"foreign": n, "institution": 0, "combined": n}
        assert w["trailing_positive_sessions_within_window"]["combined"] == n
        assert w["combined_pct_of_traded_shares"] == pytest.approx(0.1)
    assert a["unit"] == "shares_not_KRW"


def test_missing_middle_date_is_not_replaced_with_older_row():
    flow, price, reference = fixture_rows()
    del flow[sorted(flow)[-2]]
    a = compute_kr_flow_evidence(flow, price, reference, asof_utc=ASOF)
    assert all(w["status"] == "MISSING" for w in a["windows"].values())
    assert a["windows"]["30"]["observed_sessions"] == 29


def test_short_history_has_partial_windows_not_false_thirty_days():
    a = compute_kr_flow_evidence(*fixture_rows(25), asof_utc=ASOF)
    assert a["windows"]["20"]["status"] == "OK"
    assert a["windows"]["30"]["status"] == "MISSING"


def test_merged_intraday_nan_does_not_erase_valid_historical_quantities():
    flow, price, reference = fixture_rows()
    for row in flow.values():
        row["개인·기타합계"] = float("nan")
    flow["2026-09-14"] = {"외국인합계": 100, "기관합계": float("nan")}
    reference["2026-09-14"] = price["2026-09-14"] = {"Volume": 200}
    flow["__meta__"] = {"data_status": "intraday_estimate", "as_of": "2026-09-14T09:30:00+09:00"}
    result = compute_kr_flow_evidence(flow, price, reference, asof_utc="2026-09-14T01:00:00Z")
    assert all(w["status"] == "OK" for w in result["windows"].values())
    assert result["windows"]["30"]["net_shares"]["combined"] == 30


@pytest.mark.parametrize("asof", ["2026-09-14T05:00:00Z", "2026-09-14T08:00:00Z"])
@pytest.mark.parametrize("label", ["2026-09-14T14:30:00+09:00", "2026-09-14 14:30 KST"])
def test_intraday_estimate_stays_excluded_even_when_read_after_close(asof, label):
    flow, price, reference = fixture_rows()
    flow["2026-09-14"] = {"외국인합계": 999999, "기관합계": 999999}
    price["2026-09-14"] = reference["2026-09-14"] = {"Volume": 999999}
    flow["__meta__"] = {"data_status": "intraday_estimate", "as_of": label}
    a = compute_kr_flow_evidence(flow, price, reference, asof_utc=asof)
    assert a["windows"]["20"]["end"] == "2026-09-11"
    assert a["windows"]["20"]["net_shares"]["combined"] == 20


@pytest.mark.parametrize("bad", [None, True, 1.5, float("nan"), float("inf"), "bad"])
def test_invalid_quantity_is_unknown_not_zero(bad):
    flow, price, reference = fixture_rows()
    flow[max(flow)]["외국인합계"] = bad
    a = compute_kr_flow_evidence(flow, price, reference, asof_utc=ASOF)
    assert all(w["status"] == "MISSING" for w in a["windows"].values())


def test_missing_price_volume_does_not_invent_ratio_or_erase_quantities():
    flow, price, reference = fixture_rows()
    price[max(price)].pop("Volume")
    a = compute_kr_flow_evidence(flow, price, reference, asof_utc=ASOF)
    w = a["windows"]["20"]
    assert w["status"] == "OK" and w["net_shares"]["combined"] == 20
    assert w["combined_pct_of_traded_shares"] is None


@pytest.mark.parametrize("prices", [None, {}, {"error": "unavailable"}])
def test_entire_price_outage_preserves_independently_known_investor_totals(prices):
    flow, _prices, reference = fixture_rows()
    result = compute_kr_flow_evidence(flow, prices, reference, asof_utc=ASOF)
    for n, window in result["windows"].items():
        assert window["status"] == "OK"
        assert window["net_shares"]["combined"] == int(n)
        assert window["volume_ratio_status"] == "MISSING"


def test_naive_asof_duplicate_dates_and_wrong_unit_fail_closed():
    inputs = fixture_rows()
    assert compute_kr_flow_evidence(*inputs, asof_utc="2026-09-11")["reason"]
    flow, _price, _reference = inputs
    flow["20260911"] = flow["2026-09-11"]
    assert compute_kr_flow_evidence(*inputs, asof_utc=ASOF)["reason"]
    flow.pop("20260911")
    flow["__meta__"] = {"unit": "KRW"}
    assert compute_kr_flow_evidence(*inputs, asof_utc=ASOF)["reason"]


def test_prefetch_reuses_four_calls_and_delivers_same_block_to_report_agent(monkeypatch):
    flow, price, reference = fixture_rows()
    calls = []

    def serve(kind, payload):
        def invoke(*args):
            calls.append(kind)
            return payload
        return invoke

    server = SimpleNamespace(get_stock_ohlcv=serve("price", price),
                             get_stock_trading_volume=serve("flow", flow),
                             get_index_ohlcv=serve("index", reference))
    monkeypatch.setattr(data_prefetch, "_get_mcp_server_module", lambda: server)
    result = data_prefetch.prefetch_kr_analysis_data("000660", "20260911", "20260701", asof_utc=ASOF)
    assert calls == ["price", "flow", "index", "index"]
    block = result["flow_evidence"]
    assert block in result["trading_volume"]
    agent = get_agent_directory("SK", "000660", "20260911", ["investor_trading_analysis"],
                                prefetched_data=result)["investor_trading_analysis"]
    assert block in agent.instruction
    assert not agent.server_names


def test_deterministic_appendix_survives_narrative_omission():
    source = Path(__file__).parents[1] / "cores/analysis.py"
    tree = ast.parse(source.read_text())
    node = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "prefetched.get('flow_evidence')")
    block = render_kr_flow_evidence(compute_kr_flow_evidence(*fixture_rows(), asof_utc=ASOF))
    context = {"prefetched": {"flow_evidence": block}, "final_report": "narrative"}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source), "exec"), context)  # noqa: S102
    assert block in context["final_report"]


@pytest.mark.parametrize("language", ["ko", "en"])
def test_buy_contract_keeps_unknown_and_existing_rule_boundaries(language):
    text = create_trading_scenario_agent(language).instruction
    assert "KR_FLOW_EVIDENCE_V1" in text and "MISSING" in text
    assert "-7%" in text
