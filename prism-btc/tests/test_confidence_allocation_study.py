"""Synthetic arithmetic and attribution tests; no historical outcome selection."""
from dataclasses import asdict, replace
import json
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from analysis import confidence_allocation_study as study
from backtest import engine
from core.actions import OpenIntent
from engine.sizing import SizingResult


def test_json_output_handles_numpy_count_and_boolean_without_changing_numbers(tmp_path):
    payload = {'requests': np.int64(3), 'accepted': np.bool_(True), 'ratio': np.float64(.25)}
    assert json.loads(study.canonical(payload)) == {'requests': 3, 'accepted': True, 'ratio': .25}
    path = tmp_path / 'summary.json'
    study.write_json(path, payload)
    assert json.loads(path.read_text()) == json.loads(study.canonical(payload))


@pytest.mark.parametrize('bad', [np.float32('nan'), np.float32('inf')])
def test_json_output_still_rejects_nonfinite_numpy(bad):
    with pytest.raises(ValueError):
        study.canonical({'value': bad})


def test_json_output_does_not_stringify_unknown_objects():
    with pytest.raises(TypeError):
        study.canonical({'value': object()})


def position(**overrides):
    values = dict(side="long", qty=2., entry_price=100., sl_price=95.,
                  tranche_index=0, leverage=10., entry_time="2024-01-01 00:30:00+00:00",
                  initial_qty=2., initial_risk=10.)
    return engine.ResearchPositionView(**(values | overrides))


def state(*positions, **overrides):
    values = dict(equity=10000., positions=tuple(positions), pending_order=None,
                  total_fees=0., total_funding=0., realized_net_pnl=0., trade_count=0)
    return engine.ResearchStateView(**(values | overrides))


def test_marked_nav_and_observer_gross_heat():
    view = state(position(), position(side="short", qty=1., entry_price=120., sl_price=125.))
    assert study.marked_equity(view, 110.) == 10030.
    observer = study.CloseObserver()
    observer(view, pd.Timestamp("2024-01-01 00:30", tz="UTC"), 110.)
    row = observer.rows[0]
    assert row["nav"] == 10030.
    assert row["gross_fraction"] == pytest.approx(330 / 10030)
    assert row["entry_heat_fraction"] == pytest.approx(15 / 10030)
    assert row["open_tranches"] == 2


@pytest.mark.parametrize("cash", [0., -1., float("nan"), float("inf")])
def test_observer_refuses_invalid_nav(cash):
    with pytest.raises(ValueError, match="close_nav"):
        study.CloseObserver()(state(equity=cash), pd.Timestamp("2024-01-01", tz="UTC"), 100.)


def test_observer_refuses_missing_bar():
    observer = study.CloseObserver()
    observer(state(), pd.Timestamp("2024-01-01", tz="UTC"), 100.)
    with pytest.raises(ValueError, match="noncontiguous"):
        observer(state(), pd.Timestamp("2024-01-01 01:00", tz="UTC"), 100.)


def test_summary_includes_final_eop_fee_and_closed_cash_identity():
    observer = study.CloseObserver()
    for stamp in pd.date_range("2024-01-01 00:30", "2024-01-02", freq="30min", tz="UTC"):
        observer(state(), stamp, 100.)
    final = SimpleNamespace(equity=9990., trade_logs=[SimpleNamespace(net_pnl=-10.)],
                            total_fees=10., total_funding=0.)
    summary, daily = study.summarize(final, observer.rows, "2024-01-01", "2024-01-02")
    assert daily[-1]["nav"] == 9990.
    assert daily[-1]["return"] == pytest.approx(-0.001)
    assert summary["bar_close_mtm_mdd"] == pytest.approx(0.001)
    assert summary["closed_trade_cash_error"] == 0
    assert summary["profitability_status"] == "INSUFFICIENT"
    assert summary["auto_activate"] is False
    final.equity = 9991.
    with pytest.raises(ValueError, match="cash_identity"):
        study.summarize(final, observer.rows, "2024-01-01", "2024-01-02")


def test_block_bounds_reproducible_and_constant_effect():
    times = [study.utc_ms("2024-01-01") + i * study.DAY_MS for i in range(60)]
    differences = {"a": [0.001] * 60, "b": [0.002] * 60}
    first = study.block_bounds(differences, times, draws=25)
    assert first == study.block_bounds(differences, times, draws=25)
    assert first["q95"] == pytest.approx(0, abs=1e-15)
    assert first["adjusted_lower_bounds"]["a"] == pytest.approx(.001)
    assert first["family_size"] == 2


@pytest.mark.parametrize("times", [[], [0, 0], [0, 2 * study.DAY_MS],
                                   [1, study.DAY_MS + 1], [study.DAY_MS, 0]])
def test_block_bounds_reject_invalid_timestamps(times):
    with pytest.raises(ValueError, match="invalid_paired"):
        study.block_bounds({"a": [0.] * len(times)}, times, draws=3)


def test_block_bounds_reject_nonfinite_or_unpaired_values():
    with pytest.raises(ValueError, match="invalid_paired"):
        study.block_bounds({"a": [np.nan]}, [study.DAY_MS], draws=3)
    with pytest.raises(ValueError, match="invalid_paired"):
        study.block_bounds({"a": [0., 1.]}, [study.DAY_MS], draws=3)


def test_audit_uses_marked_nav_and_preserves_geometry():
    offered = OpenIntent("long", 110., SizingResult(10., 200., 105., 115., 120., 125., 99., 1), 1000., 1)
    snapshot = SimpleNamespace(tf_states={tf: SimpleNamespace(trend="up") for tf in ("1h", "4h", "1d")})
    view = state(position())
    audit = study.AllocationAudit("graded", "2024-01-01")
    result = audit(offered, snapshot, view, pd.Timestamp("2024-01-01 01:00", tz="UTC"))
    assert result is not None
    assert audit.decisions[0]["reference_nav"] == 10020.
    assert audit.decisions[0]["target_notional"] == pytest.approx(30060.)
    assert audit.decisions[0]["grade"] == "high"
    assert audit.decisions[0]["bar_idx"] == 2
    assert audit.decisions[0]["available_at"] == "2024-01-01T01:30:00+00:00"
    assert 0 < result.sizing.qty <= offered.sizing.qty
    assert result.initial_risk == result.sizing.qty * 5
    assert asdict(replace(result.sizing, qty=offered.sizing.qty)) == asdict(offered.sizing)
    assert (result.side, result.limit_price, result.tranche_index) == ("long", 110., 1)
    assert study.AllocationAudit("legacy", "2024-01-01")(
        offered, snapshot, view, pd.Timestamp("2024-01-01", tz="UTC")) is offered


def test_grade_attribution_never_guesses_missing_pending_link():
    observer = study.CloseObserver()
    pending = engine.ResearchPendingView("long", 2., 100., 95., 10., 0, 0)
    observer(state(pending_order=pending), pd.Timestamp("2024-01-01 00:30", tz="UTC"), 100.)
    known = position()
    unknown = position(entry_time="2024-01-01 01:00:00+00:00", tranche_index=1)
    observer(state(known), pd.Timestamp("2024-01-01 01:00", tz="UTC"), 101.)
    observer(state(known, unknown), pd.Timestamp("2024-01-01 01:30", tz="UTC"), 102.)
    decisions = [dict(bar_idx=0, grade="high", qty=2., actual_margin_fraction=.02, reasons=[])]
    trades = [SimpleNamespace(entry_time=p.entry_time, side=p.side,
                              tranche_index=p.tranche_index, net_pnl=pnl)
              for p, pnl in ((known, 10.), (unknown, -3.))]
    result = study.grade_summary(decisions, trades, observer)
    assert result["linked_closed_tranches"] == 1
    assert result["unlinked_closed_tranches"] == 1
    assert result["linked_net_pnl_by_grade"] == {"high": 10.}
    assert result["unlinked_net_pnl"] == -3.


def test_research_context_restores_on_exception_and_caches(monkeypatch):
    old = {name: getattr(engine, name) for name in ("MAKER_FEE", "TAKER_FEE", "SLIPPAGE_SL")}
    seen = []
    def builder(frames, stamp):
        seen.append(stamp)
        return object()
    monkeypatch.setattr(engine, "_build_snapshot_at", builder)
    stamp = pd.Timestamp("2024-01-01", tz="UTC")
    with pytest.raises(RuntimeError):
        with study.research_context(2, {}):
            assert all(getattr(engine, name) == value * 2 for name, value in old.items())
            assert engine._build_snapshot_at({}, stamp) is engine._build_snapshot_at({}, stamp)
            raise RuntimeError("synthetic")
    assert len(seen) == 1
    assert engine._build_snapshot_at is builder
    assert all(getattr(engine, name) == value for name, value in old.items())
    assert engine._END_NS_CACHE == {}


@pytest.mark.parametrize("timestamp", [True, "86400000", 86400000.5, float("nan"),
                                       float("inf"), -1, 253402300800000])
def test_block_bounds_refuses_coerced_or_out_of_range_timestamp(timestamp):
    with pytest.raises(ValueError, match="invalid_bootstrap"):
        study.block_bounds({"a": [0.]}, [timestamp], draws=2)


@pytest.mark.parametrize("draws", [True, 0, -1, 1.5, "2"])
def test_block_bounds_refuses_invalid_draws(draws):
    with pytest.raises(ValueError, match="invalid_bootstrap"):
        study.block_bounds({"a": [0.]}, [study.DAY_MS], draws=draws)


@pytest.mark.parametrize("allocated_grades", [("high",), ("medium", "high")])
def test_constant_equivalent_cannot_support_grade_effect(allocated_grades):
    results = {}
    for cost in (1, 2):
        for arm in ("fixed_low", "uniform", "fixed_high", "graded", "reversed"):
            returns = .002 if arm in ("graded", "fixed_high") else .001
            daily = [dict(timestamp_ms=study.utc_ms("2024-01-01") + i * study.DAY_MS,
                          nav=10000 * (1 + returns) ** (i + 1), return_=returns)
                     for i in range(3)]
            for row in daily:
                row["return"] = row.pop("return_")
            summary = dict(net_return=returns, bar_close_mtm_mdd=.01,
                           yearly_returns={year: returns for year in ("2023", "2024", "2025")},
                           allocation={"decisions": {grade: {"accepted_requests": 5 if grade in allocated_grades else 0}
                                                     for grade in ("low", "medium", "high", "unknown")}})
            results[f"evaluation_c{cost}_{arm}"] = dict(period="evaluation", daily=daily, summary=summary)
    comparison = study.comparison(results)
    assert comparison["checks"]["cost1_higher_return"]
    assert comparison["checks"]["positive_adjusted_lower_bound"]
    assert comparison["checks"]["at_least_two_allocated_grades"] == (len(allocated_grades) >= 2)
    assert not comparison["checks"]["not_constant_allocation_equivalent"]
    assert comparison["constant_equivalent_arms"] == ["fixed_high"]
    assert comparison["exploratory_verdict"] == "UNIDENTIFIABLE_GRADE_EFFECT"
    assert comparison["auto_activate"] is False
