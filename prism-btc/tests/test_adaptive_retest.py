"""Runner contract fixtures only: no market outcomes or strategy optimization."""
from collections import Counter
from copy import deepcopy
import gzip
import json

import numpy as np
import pytest

from analysis import adaptive_retest as r


def test_exact_registry_and_policy_configuration():
    rows = r.planned_registry()
    assert Counter(x["phase"] for x in rows) == {"TRAIN": 26, "OOS": 104, "PARTIAL": 16}
    assert len({x["id"] for x in rows}) == 146
    assert len(r.POLICIES) == 13 and len(r.COMPARISONS) == 11
    assert r.POLICIES[-1] == "J_H_FLEX"
    for row in rows:
        c = r.policy_config(row)
        assert c["start_ms"] == (r.TRAIN if row["phase"] == "TRAIN" else r.OOS)
        assert c["end_ms"] == (r.OOS if row["phase"] == "TRAIN" else r.END)
        assert c["lanes"] == (("S", "C") if row["name"].startswith("J") else (row["name"][0],))
        assert c["profile"] == row["name"].split("_")[1]
        assert c["allocation"] == ("flex" if row["name"].endswith("FLEX") else "fixed")
        assert c["partial"] == (row["phase"] == "PARTIAL")
        assert c["timing_ms"] == row["timing"] * 60_000
        assert c["cost_multiple"] == row["cost"] and c["path"] == row["path"]


def test_registry_guard_and_no_optimizer(monkeypatch):
    contract = r.make_contract()
    assert contract["selection"] == "NONE_NO_AFTER_RESULT_TUNING"
    assert contract["primary"] == ["J_H_FLEX", "J_F"]
    assert contract["bootstrap"]["columns"] == 22
    assert contract["paired"]["fixed_lots"] == 20
    assert contract["auto_activate"] is False
    assert contract["profitability_status"] == "INSUFFICIENT"
    monkeypatch.setattr(r, "POLICIES", ())
    with pytest.raises(RuntimeError, match="146"):
        r.planned_registry()


def test_preregistration_immutable_and_source_change_fails(tmp_path, monkeypatch):
    saved = r.preregister(tmp_path)
    assert r.canonical(r.verify_contract(tmp_path)) == r.canonical(saved)
    before = (tmp_path / "contract.json").read_bytes()
    original = r.file_hash
    monkeypatch.setattr(r, "file_hash", lambda p: "changed" if str(p).endswith("adaptive_signals.py") else original(p))
    with pytest.raises(ValueError, match="source changed"):
        r.verify_contract(tmp_path)
    with pytest.raises((ValueError, FileExistsError)):
        r.preregister(tmp_path)
    assert (tmp_path / "contract.json").read_bytes() == before


def bootstrap_results(n=1096):
    times = [r.OOS + (i+1) * r.DAY for i in range(n)]
    return {f"OOS/{name}/c1/d5/{path}": {
        "daily_nav": [[t, 10_000.] for t in times],
        "statistics": {"daily_returns": (np.sin(np.arange(n) / (j+1)) * (j+1) * .0001 * (k+1)).tolist()}}
        for k, path in enumerate(r.PATHS) for j, name in enumerate(r.POLICIES)}


def test_joint_bootstrap_passes_all_22_columns_to_one_maximum(monkeypatch):
    results = bootstrap_results()
    actual = r.bootstrap_max_error
    seen = []
    def capture(matrix, times, **kwargs):
        assert matrix["OHLC"] == matrix["OLHC"]
        assert len(matrix["OHLC"]) == 22
        assert len([k for k in matrix["OHLC"] if k.startswith("OHLC/")]) == 11
        assert kwargs == dict(draws=2000, seed=20260906, block_days=30, require_full_period=False)
        seen.append((matrix, times, kwargs))
        return actual(matrix, times, **kwargs)
    monkeypatch.setattr(r, "bootstrap_max_error", capture)
    result = r.joint_bounds(results)
    assert len(seen) == 1
    expected = actual(seen[0][0], seen[0][1], **seen[0][2])["paths"]["OHLC"]
    assert result["q95_max_error"] == expected["q95_max_error"]
    assert result["lower_bounds"] == expected["lower_bounds"]
    assert len(result["columns"]) == 22


def test_bootstrap_mismatched_calendar_rejected():
    results = bootstrap_results()
    results["OOS/S_P/c1/d5/OHLC"]["daily_nav"][0][0] += r.DAY
    with pytest.raises(ValueError, match="calendars"):
        r.joint_bounds(results)


def test_bootstrap_rejects_matching_but_truncated_calendars():
    with pytest.raises(ValueError, match="1096"):
        r.joint_bounds(bootstrap_results(45))


def signal(lane="S", ts=r.TRAIN, direction=1):
    return dict(signal_id=f"{lane}:{ts}:{direction}", lane=lane, available_at=ts,
                direction=direction, reference_price=100., stop_distance=2., strong=True,
                max_hold_ms=7_200_000 if lane == "C" else None)


def test_cohort_horizon_first_future_exit_c_deadline_and_terminal_bound():
    ts = r.TRAIN
    context = {ts: {"S": {"exit_long": True}}, ts+4*3_600_000: {"S": {"exit_long": True}},
               ts+8*3_600_000: {"S": {"exit_long": True, "exit_short": True}}}
    signals = [signal(), signal(direction=-1), signal("C"), signal("C", r.END-r.BAR),
               signal(ts=r.TRAIN-r.BAR), signal(ts=r.END)]
    rows = r.cohort_plan(signals, context)
    assert len(rows) == 4
    assert rows[0]["end_ms"] == ts+4*3_600_000+2*r.BAR
    assert rows[1]["end_ms"] == ts+8*3_600_000+2*r.BAR
    assert rows[2]["end_ms"] == ts+7_200_000+3*r.BAR
    assert rows[3]["end_ms"] == r.END
    assert r.cohort_plan([signal()], {})[0]["end_ms"] == r.END


def test_cohort_slicing_keeps_prior_mark_and_context_without_future():
    ts = r.TRAIN
    times = np.arange(ts-2*r.BAR, ts+10*r.BAR, r.BAR)
    bars = np.column_stack((times, np.ones((len(times), 4)) * 100))
    funding = np.array([[ts-r.BAR, .001], [ts, .002], [ts+3*r.BAR, .003], [ts+4*r.BAR, .004]])
    contexts = {ts-2*r.BAR: {"S": {"trend_long": True}}, ts: {"S": {"trend_long": False}},
                ts+4*r.BAR: {"S": {"trend_long": True}}}
    contexts = dict(reversed(list(contexts.items())))
    bs, fs, ctx = r.cohort_inputs(bars, funding, contexts, signal(), ts+4*r.BAR)
    assert bs[:, 0].tolist() == list(range(ts-r.BAR, ts+4*r.BAR, r.BAR))
    assert fs.tolist() == [[ts, .002], [ts+3*r.BAR, .003]]
    assert list(ctx) == [ts-2*r.BAR, ts]
    assert np.shares_memory(bs, bars)


def paired_rows(kind="filled", sid="id"):
    rows = []
    for i, profile in enumerate(r.PROFILES):
        fills = [] if kind == "empty" else [dict(lots=20, price=100., timestamp=r.TRAIN)]
        if kind == "mismatch" and profile == "H":
            fills[0]["price"] = 101.
        rows.append(dict(signal_id=sid, lane="S", path="OHLC", year=2022, profile=profile,
                         entry_fills=fills, entry_identity=r.digest(fills), net_r=float(i)))
    return rows


def test_paired_excludes_no_fill_and_mismatch_from_20lot_effects():
    rows = paired_rows() + paired_rows("empty", "empty") + paired_rows("mismatch", "bad")
    result = r.paired_summary(rows)
    group = result["groups"]["S/OHLC/2022"]
    assert group["raw_cohorts"] == 3 and group["matched_entries"] == 2
    assert group["filled20"] == 1 and group["entry_mismatches"] == 1
    assert group["mean_net_r"] == {"F": 0., "P": 1., "Q": 2., "H": 3.}
    assert group["mean_paired_delta"] == {"P-F": 1., "Q-P": 1., "H-Q": 1., "H-F": 3.}
    assert result["uncertainty"] == "DESCRIPTIVE_ONLY_OVERLAPPING_COHORTS"
    with pytest.raises(ValueError, match="incomplete"):
        r.paired_summary(rows[:-1])


def test_paired_record_uses_net_nav_and_fixed_initial_r():
    raw = dict(metrics=dict(final_nav=10_001., initial_nav=10_000., fees=.1, funding=-.02,
                            slippage=.1, completed_campaigns=0),
               final_positions={"S": {"lots": 7}}, entry_fills=[{"lots": 20}], hashes={"ledger": "x"})
    row = r.paired_record(raw, signal(), "H", "OHLC")
    assert row["net_pnl"] == 1.
    assert row["net_r"] == 25.
    assert row["remaining_lots"] == 7


def test_duplicate_paired_record_is_rejected():
    rows = paired_rows()
    with pytest.raises(ValueError, match="duplicate"):
        r.paired_summary(rows + [rows[0]])


def gate_fixture():
    good = dict(total_return=.8, mtm_mdd=.1, completed_campaigns=60, cagr=.22,
                positive_month_fraction=.6, yearly_returns={"2023": .2, "2024": .2, "2025": .2},
                top5_removed_return=.3, top5_share=.3)
    results = {row["id"]: {"statistics": deepcopy(good)} for row in r.planned_registry()}
    for key, result in results.items():
        if "/J_F/" in key:
            result["statistics"]["total_return"] = .3
    bounds = {"lower_bounds": {path+"/J_H_FLEX-J_F": .001 for path in r.PATHS}}
    return results, bounds


def test_gate_all_checks_pass_only_fixed_primary():
    results, bounds = gate_fixture()
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["improvement_status"] == "HISTORICAL_IMPROVEMENT"
    assert outcome["high_growth_status"] == "HISTORICAL_TARGET_MET"
    assert not outcome["failed_checks"] and not outcome["failed_growth_checks"]


@pytest.mark.parametrize("trial,field,value,reason", [
    ("PARTIAL/J_H_FLEX/c2/d30/OLHC", "total_return", -.1, "partial_positive"),
    ("PARTIAL/J_H_FLEX/c2/d30/OLHC", "mtm_mdd", .3, "partial_mdd25"),
    ("OOS/J_H_FLEX/c2/d30/OLHC", "yearly_returns", {"2023": .1, "2024": -.1, "2025": -.1}, "full_two_positive_years"),
    ("OOS/J_H_FLEX/c1/d5/OHLC", "completed_campaigns", 59, "campaigns60"),
])
def test_improvement_failures_preserved(trial, field, value, reason):
    results, bounds = gate_fixture()
    results[trial]["statistics"][field] = value
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["improvement_status"] == "NOT_PROVEN"
    assert any(reason in key for key in outcome["failed_checks"])


def test_growth_failure_is_separate_from_improvement_and_lower_zero_fails():
    results, bounds = gate_fixture()
    results["OOS/J_H_FLEX/c1/d5/OHLC"]["statistics"]["cagr"] = .19
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["high_growth_status"] == "NOT_MET"
    assert outcome["improvement_status"] == "HISTORICAL_IMPROVEMENT"
    bounds["lower_bounds"]["OHLC/J_H_FLEX-J_F"] = 0.
    assert r.evaluate_gates(results, bounds)["improvement_status"] == "NOT_PROVEN"


@pytest.mark.parametrize("error,status", [(ValueError("bad backend"), "INVALID_RESEARCH"),
                                          (KeyboardInterrupt(), "INCOMPLETE"),
                                          (r.ResourceLimit("budget"), "INCOMPLETE")])
def test_failed_second_trial_preserves_first_result_and_registry(tmp_path, monkeypatch, error, status):
    saved = r.preregister(tmp_path)
    r.write_json(tmp_path / "synthetic_profile.json", dict(kind="SYNTHETIC_NOT_MARKET", seconds=.001,
                 source_hashes=saved["contract"]["source_hashes"]))
    monkeypatch.setattr(r, "validate_environment", lambda: None)
    monkeypatch.setattr(r, "load_inputs", lambda *args: (np.empty((0, 5)), np.empty((0, 2)), {}))
    monkeypatch.setattr(r, "build_signal_tape", lambda *args: dict(signals=[], contexts={}, metadata={}))
    monkeypatch.setattr(r, "summarize", lambda x: x)
    calls = []
    def backend(*args):
        calls.append(1)
        if len(calls) == 2:
            raise error
        return dict(daily_nav=[[r.OOS, 10_000.]], fixture="first-success",
                    metrics={}, counters={}, statistics={}, hashes={})
    monkeypatch.setattr(r, "run_adaptive", backend)
    with pytest.raises(type(error)):
        r.run_experiment(tmp_path, "unused", "unused")
    registry = json.loads((tmp_path / "registry.json").read_text())
    assert registry[0]["status"] == "OK"
    assert registry[1]["status"] == status
    assert all(row["status"] == "PLANNED" for row in registry[2:])
    first = tmp_path / "trials" / registry[0]["id"] / "result.json"
    assert json.loads(first.read_text())["fixture"] == "first-success"
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["status"] == status and failure["auto_activate"] is False
    with pytest.raises(ValueError, match="fresh run"):
        r.run_experiment(tmp_path, "unused", "unused")


def test_cohort_failure_preserves_completed_records_and_marks_running(tmp_path, monkeypatch):
    saved = r.preregister(tmp_path)
    r.write_json(tmp_path / "synthetic_profile.json", dict(kind="SYNTHETIC_NOT_MARKET", seconds=.001,
                 source_hashes=saved["contract"]["source_hashes"]))
    monkeypatch.setattr(r, "validate_environment", lambda: None)
    times = np.arange(r.TRAIN-r.BAR, r.TRAIN+30*r.BAR, r.BAR)
    bars = np.column_stack((times, np.ones((len(times), 4)) * 100.))
    monkeypatch.setattr(r, "load_inputs", lambda *args: (bars, np.empty((0, 2)), {}))
    monkeypatch.setattr(r, "build_signal_tape", lambda *args: dict(signals=[signal("C")], contexts={}, metadata={}))
    monkeypatch.setattr(r, "summarize", lambda x: x)
    calls = []
    raw = dict(daily_nav=[[r.OOS, 10_000.]], metrics={}, counters={}, statistics={}, hashes={})
    def backend(*args):
        calls.append(1)
        if len(calls) == 149:  # All146 portfolios and two paired profiles already completed.
            raise r.ResourceLimit("cohort resource fixture")
        return raw
    monkeypatch.setattr(r, "run_adaptive", backend)
    monkeypatch.setattr(r, "paired_record", lambda raw, sig, profile, path: dict(profile=profile, path=path))
    with pytest.raises(r.ResourceLimit):
        r.run_experiment(tmp_path, "unused", "unused")
    registry = json.loads((tmp_path / "registry.json").read_text())
    assert all(row["status"] == "OK" for row in registry)
    cohort = json.loads((tmp_path / "cohort_registry.json").read_text())[0]
    assert cohort["status"] == "INCOMPLETE"
    with gzip.open(tmp_path / "paired_records.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    assert [row["profile"] for row in records] == ["F", "P"]
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["checkpoint"]["profile"] == "Q"
    assert failure["checkpoint"]["path"] == "OHLC"
