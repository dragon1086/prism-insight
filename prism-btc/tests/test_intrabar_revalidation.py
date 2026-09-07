"""Small deterministic evidence fixtures, never historical financial studies."""
from io import StringIO
import json
from types import SimpleNamespace

import pytest

from analysis import intrabar_revalidation as study
from backtest.intrabar_broker import PositionView


class Bundle:
    def regime_at(self, ts):
        return {"label": "up_normal" if ts < study.DAY_MS else "down_shock"}


def snapshot(ts, opening, nav):
    return {"ts": ts, "bar_open_ts": opening, "cash": nav, "nav": nav,
            "realized_price": nav - study.INITIAL, "fees": 0., "funding": 0., "unrealized": 0.}


def collector():
    return study.StreamingCollector(Bundle(), [{"ts": study.DAY_MS - study.BAR_MS},
        {"ts": study.DAY_MS}], StringIO())


def broker(final, positions=(), closed=(), pending=()):
    return SimpleNamespace(snapshot=lambda: final, mark=100., positions=lambda: positions,
        closed_trades=closed, pending_entries=lambda: pending, pending_reductions=lambda pid: 0,
        _timers=[], active_parents={})


def value_day_pair(collect):
    first, second = study.DAY_MS - study.BAR_MS, study.DAY_MS
    collect.valuation(snapshot(first, first, 10_000.))
    collect.valuation(snapshot(second, first, 10_100.))  # Old interval closes at midnight.
    collect.valuation(snapshot(second, second, 10_050.))  # New interval open gap, same timestamp.
    final = snapshot(second + study.BAR_MS, second, 10_200.)
    collect.valuation(final)
    return final


def test_exact_preregistered_25_cases_and_profiles():
    cases = study.registry()
    assert len(cases) == len({case["id"] for case in cases}) == 25
    diagnostic = [case for case in cases if case["phase"] == "diagnostic"]
    evaluation = [case for case in cases if case["phase"] == "evaluation"]
    assert len(diagnostic) == 5 and len(evaluation) == 20
    assert {(c["profile"], c["path"]) for c in diagnostic} == {("BASE", "OHLC")}
    assert {(c["profile"], c["path"]) for c in evaluation} == {
        ("BASE", "OHLC"), ("BASE", "OLHC"), ("STRESS", "OHLC"), ("STRESS", "OLHC")}
    assert all(c["start_ms"] == 1_672_531_200_000 and c["end_ms"] == 1_767_225_600_000 for c in evaluation)
    base, stress = study.execution_config("BASE", "OHLC"), study.execution_config("STRESS", "OLHC")
    assert (base.entry_latency_ms, base.cancel_latency_ms, base.participation) == (5000, 2000, .01)
    assert (stress.entry_latency_ms, stress.market_latency_ms, stress.cancel_latency_ms,
            stress.amend_latency_ms, stress.participation) == (30_000, 30_000, 10_000, 10_000, .001)
    assert stress.maker_fee == 2 * base.maker_fee
    assert stress.taker_fee == 2 * base.taker_fee
    assert stress.slippage == 2 * base.slippage and stress.spread == 2 * base.spread


def test_interval_regime_not_endpoint_hindsight_and_conserved_nav():
    collect = collector()
    final = value_day_pair(collect)
    summary, daily, closed = collect.finish(broker(final))
    assert summary["regimes"]["up_normal"]["interval_nav_change"] == 100.
    assert summary["regimes"]["down_shock"]["interval_nav_change"] == 100.
    assert summary["regime_interval_nav_total"] == final["nav"] - study.INITIAL == 200.
    assert len(summary["regimes"]) == 7
    assert summary["regimes"]["up_normal"]["coverage_bars"] == 1
    assert summary["regimes"]["down_shock"]["coverage_bars"] == 1
    assert daily[0] == {"date": "1970-01-01", "timestamp_ms": study.DAY_MS,
                        "nav": 10_100., "return": 10_100 / 10_000 - 1}
    assert daily[1]["return"] == 10_200 / 10_100 - 1
    assert daily[1]["timestamp_ms"] == 2 * study.DAY_MS
    assert summary["yearly_returns"] == {"1970": 10_200 / 10_000 - 1}
    assert closed == []


def test_digest_reproducible_and_pre_post_drawdown_witness():
    first, second = collector(), collector()
    final = value_day_pair(first)
    value_day_pair(second)
    summary = first.finish(broker(final))[0]
    assert first.digest.hexdigest() == second.digest.hexdigest()
    assert first.count == 4
    assert summary["max_drawdown"] == 50 / 10_100
    assert summary["valuation"]["drawdown_witness"]["peak"]["bar_open_ts"] == study.DAY_MS - study.BAR_MS
    assert summary["valuation"]["drawdown_witness"]["trough"]["bar_open_ts"] == study.DAY_MS


def fill(ts, entry=True, lots=10, child=0):
    return {"kind": "fill", "ts": ts, "parent_id": "p", "lane": "main", "entry": entry,
            "bar_open_ts": ts // study.BAR_MS * study.BAR_MS, "fee": 0.,
            "lots": lots, "child": child, "meta": {"regime": "down_shock"}}


def test_parent_entry_cohort_first_fill_not_signal_or_last_fill():
    collect = collector()
    collect.event(fill(study.DAY_MS - 10, lots=5, child=0))
    collect.event(fill(study.DAY_MS + 10, lots=5, child=1))
    collect.event(fill(study.DAY_MS + 20, entry=False))
    final = value_day_pair(collect)
    parent = {"parent_id": "p", "lane": "main", "filled_lots": 10, "exited_lots": 10,
              "net_pnl": 5., "fees": 1., "funding": -.5}
    summary, _, closed = collect.finish(broker(final, closed=[parent]))
    assert closed[0]["entry_regime"] == "up_normal"
    assert summary["execution"]["multi_child_parents"] == 1
    assert summary["regimes"]["up_normal"]["closed_parent_net_pnl"] == 5.
    assert summary["regimes"]["down_shock"]["closed_parents"] == 0
    assert summary["regimes"]["up_normal"]["fees"] == 1.
    assert summary["regimes"]["up_normal"]["funding"] == -.5
    assert summary["regimes"]["up_normal"]["sample_status"] == "INSUFFICIENT_LT30"


def test_open_at_end_preserves_held_lots_without_fake_close():
    collect = collector()
    collect.event(fill(study.DAY_MS - 10))
    final = value_day_pair(collect)
    held = PositionView("p", "main", "long", 10, 100., 90., 110., 10, 0,
        study.DAY_MS - 10, 10, .1, False, True, False, 100., 100.)
    pending = [{"parent_id": "pending", "lots": 3}]
    summary, _, closed = collect.finish(broker(final, positions=[held], pending=pending))
    assert summary["open_at_end"]["held_lots"] == 10
    assert summary["open_at_end"]["pending_lots"] == 3
    assert summary["open_at_end"]["disposition"] == "OPEN_AT_END"
    assert summary["execution"]["closed_parents"] == 0
    assert summary["open_positions"][0]["id"] == "p" and not closed


@pytest.mark.parametrize("field", ["cash", "nav"])
def test_valuation_identity_failure(field):
    collect = collector()
    value = snapshot(study.DAY_MS, study.DAY_MS, 10_000.)
    value[field] += 1
    with pytest.raises(ValueError, match="identity"):
        collect.valuation(value)


def test_missing_interval_tag_and_oversell_rejected():
    collect = collector()
    with pytest.raises(KeyError):
        collect.valuation({"ts": study.DAY_MS})
    with pytest.raises(ValueError, match="oversold"):
        collect.event(fill(study.DAY_MS, entry=False))


def test_run_study_preregisters_and_marks_failed_not_complete(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "_clean_source", lambda: "frozen")
    monkeypatch.setattr(study, "source_manifest", lambda: {"contract": "hash"})
    selected = [study.registry()[0]["id"]]

    def fail(*args):
        assert json.loads((tmp_path / "run/registry.json").read_text())["cases"][0]["id"] == selected[0]
        raise ValueError("synthetic failure")

    monkeypatch.setattr(study, "run_case", fail)
    with pytest.raises(ValueError, match="synthetic"):
        study.run_study(SimpleNamespace(manifest={}), tmp_path / "run", selected)
    progress = json.loads((tmp_path / "run/progress.json").read_text())
    assert progress["status"] == "failed" and progress["completed"] == []
    assert progress["active"] == selected[0]


def test_evaluation_subset_rejected_and_success_progress(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "_clean_source", lambda: "frozen")
    monkeypatch.setattr(study, "source_manifest", lambda: {})
    monkeypatch.setattr(study, "run_case", lambda *args: {})
    data = SimpleNamespace(manifest={})
    with pytest.raises(ValueError, match="diagnostic"):
        study.run_study(data, tmp_path / "bad", [study.registry()[-1]["id"]])
    assert not (tmp_path / "bad").exists()
    result = study.run_study(data, tmp_path / "good", [study.registry()[0]["id"]])
    assert result["status"] == "complete" and len(result["completed"]) == 1


def test_clean_source_gate_rejects_before_output(tmp_path, monkeypatch):
    monkeypatch.setattr(study.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=" M finance.py\n"))
    with pytest.raises(ValueError, match="clean frozen"):
        study.run_study(SimpleNamespace(manifest={}), tmp_path / "absent")
    assert not (tmp_path / "absent").exists()


def test_interval_costs_separate_from_closed_cohort_and_active_timers():
    collect = collector()
    row = fill(study.DAY_MS - 10)
    row["fee"] = 2.
    collect.event(row)
    collect.event({"kind": "funding", "ts": study.DAY_MS, "bar_open_ts": study.DAY_MS, "amount": -1.})
    final = value_day_pair(collect)
    held = PositionView("p", "main", "long", 10, 100., 90., 110., 10, 0,
        study.DAY_MS - 10, 10, .1, False, True, False, 100., 100.)
    actual = broker(final, positions=[held])
    actual.active_parents = {"p": None}
    actual._timers = [(1, 1, "expire", "p", None), (2, 2, "expire", "old_final", None)]
    summary = collect.finish(actual)[0]
    assert summary["regimes"]["up_normal"]["interval_fees"] == 2.
    assert summary["regimes"]["down_shock"]["interval_funding"] == -1.
    assert summary["regimes"]["up_normal"]["fees"] == 0.  # Parent is still open.
    assert summary["regimes"]["up_normal"]["coverage_hours"] == 1 / 12
    assert summary["open_at_end"]["raw_timer_heap_count"] == 2
    assert summary["open_at_end"]["active_parent_timer_count"] == 1


def test_real_run_case_small_native_bundle_writes_all_six_artifacts(tmp_path):
    # Tiny actual InputBundle/native strategy/kernel/collector integration; no historical PNL.
    from analysis.intrabar_inputs import InputBundle
    from backtest import engine
    import pandas as pd
    from engine.indicators import add_indicators
    frames = {}
    for tf in engine.ALL_TFS:
        frame = pd.DataFrame({"open": [100.], "high": [101.], "low": [99.], "close": [100.],
                              "volume": [1.], "turnover": [100.]},
                             index=pd.to_datetime([0], unit="ms", utc=True))
        frames[tf] = add_indicators(frame)
    bars = tuple({"ts": stamp, "open": 100., "high": 101., "low": 99., "close": 100.,
                  "volume": 10., "prior_volume": 10.} for stamp in (0, 300_000))
    marks = tuple({key: value for key, value in bar.items() if key not in ("volume", "prior_volume")} for bar in bars)
    data = InputBundle(bars, (), marks, frames, {"synthetic": True}, ())
    case = {**study.registry()[0], "start_ms": 0, "end_ms": 600_000, "id": "synthetic"}
    output = tmp_path / "synthetic"
    summary = study.run_case(data, case, output)
    assert {p.name for p in output.iterdir()} == set(study.ARTIFACTS)
    assert summary["net_return"] == summary["max_drawdown"] == 0.
    assert summary["valuation"]["count"] > 6
    assert summary["regimes"]["unknown"]["coverage_bars"] == 2
    assert summary["open_at_end"]["disposition"] == "OPEN_AT_END"
    assert summary["open_positions"] == []
    assert json.loads((output / "manifest.json").read_text())["input_manifest_sha256"]
    assert json.loads((output / "events.jsonl").read_text().splitlines()[-1])["kind"] == "replay_end"
