"""Preregistration and orchestration fixtures, never historical strategy outcomes."""
from collections import Counter
from copy import deepcopy
import gzip
import json

import numpy as np
import pytest

from analysis import transition_retest as r


def test_registry_and_all_policy_mappings():
    rows = r.planned_registry()
    assert Counter(row["phase"] for row in rows) == {"TRAIN": 32, "OOS": 128, "PARTIAL": 32}
    assert len({row["id"] for row in rows}) == 192
    assert len(r.POLICIES) == 16 and len(r.COMPARISONS) == 17
    assert {row["name"] for row in rows if row["phase"] == "PARTIAL"} == {"X3_U", "J_U", "X4_U", "J4_U"}
    for row in rows:
        variant, config = r.policy_config(row)
        assert variant == row["name"].split("_")[0]
        assert config["start_ms"] == (r.TRAIN if row["phase"] == "TRAIN" else r.OOS)
        assert config["end_ms"] == (r.OOS if row["phase"] == "TRAIN" else r.END)
        assert config["timing_ms"] == row["delay"] * 60_000
        assert config["cost_multiple"] == row["cost"]
        assert config["path"] == row["path"]
        assert config["partial"] == (row["phase"] == "PARTIAL")
        assert config["allocation"] == ("flex" if variant in ("J", "J4") else "fixed")
        if variant in ("J", "J4"):
            assert config["lanes"] == ("S", "C")
            assert config["profile"] == "F"
            assert config["lane_profiles"] == (("S", "F"), ("C", "U"))
        else:
            assert config["lanes"] == (("S",) if variant == "S" else ("C",))
            assert config["profile"] == row["name"].split("_")[1]
            assert "lane_profiles" not in config


def test_contract_has_fixed_primary_and_never_promotes(monkeypatch):
    contract = r.make_contract()
    assert contract["primary"] == ["X3_U", "X0_F"]
    assert contract["selection"] == "NONE"
    assert contract["auto_activate"] is False
    assert contract["profitability_status"] == "INSUFFICIENT"
    assert contract["bootstrap"]["columns"] == 34
    assert contract["paired"]["fixed_lots"] == 20
    assert contract["paired"]["risk_share"] == 1
    monkeypatch.setattr(r, "POLICIES", ())
    with pytest.raises(RuntimeError, match="192"):
        r.planned_registry()


@pytest.mark.parametrize("mutation", ["source", "checksum"])
def test_frozen_contract_tampering_fails(tmp_path, monkeypatch, mutation):
    saved = r.preregister(tmp_path)
    assert r.canonical(saved) == r.canonical(r.verify_contract(tmp_path))
    before = (tmp_path / "contract.json").read_bytes()
    if mutation == "source":
        old = r.file_hash
        monkeypatch.setattr(r, "file_hash", lambda p: "changed" if str(p).endswith("transition_signals.py") else old(p))
    else:
        altered = deepcopy(saved)
        altered["contract"]["selection"] = "TAMPERED"
        r.write_json(tmp_path / "contract.json", altered)
    with pytest.raises(ValueError, match="mismatch"):
        r.verify_contract(tmp_path)
    if mutation == "source":
        with pytest.raises((ValueError, FileExistsError)):
            r.preregister(tmp_path)
        assert (tmp_path / "contract.json").read_bytes() == before


def signal(lane="C"):
    return dict(signal_id=f"{lane}:fixture", lane=lane, available_at=r.TRAIN,
                direction=1, reference_price=100., stop_distance=1., strong=False,
                max_hold_ms=7_200_000, risk_share=1.)


def tape_fixture():
    context = {r.TRAIN: {"C": {"trend_long": True}}}
    variants = {name: dict(signals=[signal()], contexts=context) for name in ("X0", "X1", "X1A", "X2", "X3", "X4")}
    variants["X2"]["signals"] = [dict(signal(), risk_share=.25)]
    variants["X4"]["signals"] = [dict(signal(), risk_share=.5)]
    return dict(variants=variants, metadata={}, episodes=[])


@pytest.mark.parametrize("mismatch", [None, "signal", "context", "inventory", "secondary_signal", "secondary_context"])
def test_prepare_tapes_identity_guard_and_joint_swing_priority(monkeypatch, mismatch):
    built = tape_fixture()
    if mismatch == "signal":
        built["variants"]["X2"]["signals"][0]["stop_distance"] = 2.
    elif mismatch == "context":
        built["variants"]["X2"]["contexts"] = {r.TRAIN: {"C": {"trend_long": False}}}
    elif mismatch == "inventory":
        built["variants"].pop("X1A")
    elif mismatch == "secondary_signal":
        built["variants"]["X4"]["signals"][0]["stop_distance"] = 2.
    elif mismatch == "secondary_context":
        built["variants"]["X4"]["contexts"] = {r.TRAIN: {"C": {"trend_long": False}}}
    monkeypatch.setattr(r, "build_transition_tapes", lambda bars: built)
    monkeypatch.setattr(r, "build_signal_tape", lambda bars: dict(signals=[signal("S"), signal()],
        contexts={r.TRAIN: {"S": {"trend_short": False}, "C": {"obsolete": True}}}))
    if mismatch:
        with pytest.raises(ValueError):
            r.prepare_tapes([])
        return
    tapes, metadata, episodes = r.prepare_tapes([])
    assert [s["lane"] for s in tapes["J"]["signals"]] == ["S", "C"]
    assert set(tapes["J"]["contexts"][r.TRAIN]) == {"S", "C"}
    assert "obsolete" not in tapes["J"]["contexts"][r.TRAIN]["C"]
    assert tapes["S"]["signals"] == [signal("S")]
    assert metadata["variants"]["X1"]["context_hash"] == metadata["variants"]["X2"]["context_hash"]
    assert metadata["variants"]["X3"]["context_hash"] == metadata["variants"]["X4"]["context_hash"]
    assert [s["lane"] for s in tapes["J4"]["signals"]] == ["S", "C"]
    assert tapes["J4"]["signals"][-1]["risk_share"] == .5
    assert tapes["J4"]["contexts"] == tapes["J"]["contexts"]
    assert episodes == []


def bootstrap_fixture(days=1096):
    dates = [r.OOS + (i + 1) * r.DAY for i in range(days)]
    return {f"OOS/{name}/c1/d5/{path}": dict(daily_nav=[[ts, 10_000.] for ts in dates],
            statistics=dict(daily_returns=(np.sin(np.arange(days) / (i + 1)) * .0001 * (j + 1)).tolist()))
            for i, name in enumerate(r.POLICIES) for j, path in enumerate(r.PATHS)}


def test_bootstrap_recomputes_joint_34_column_family(monkeypatch):
    original = r.bootstrap_max_error
    seen = []
    def capture(matrix, times, **kwargs):
        assert matrix["OHLC"] == matrix["OLHC"]
        assert len(matrix["OHLC"]) == 34
        assert len([k for k in matrix["OHLC"] if k.startswith("OHLC/")]) == 17
        assert len(times) == 1096
        seen.append((matrix, times, kwargs))
        return original(matrix, times, **kwargs)
    monkeypatch.setattr(r, "bootstrap_max_error", capture)
    result = r.joint_bounds(bootstrap_fixture())
    expected = original(seen[0][0], seen[0][1], draws=2000, seed=20260906,
                        block_days=30, require_full_period=False)["paths"]["OHLC"]
    assert len(seen) == 1
    assert result["lower_bounds"] == expected["lower_bounds"]
    assert result["q95_max_error"] == expected["q95_max_error"]


@pytest.mark.parametrize("bad", ["short", "shift", "missing"])
def test_bootstrap_rejects_incomplete_comparison_evidence(bad):
    results = bootstrap_fixture(45 if bad == "short" else 1096)
    if bad == "shift":
        results["OOS/X0_F/c1/d5/OHLC"]["daily_nav"][0][0] += r.DAY
    elif bad == "missing":
        results.pop("OOS/X1_K/c1/d5/OLHC")
    with pytest.raises((ValueError, KeyError)):
        r.joint_bounds(results)


def paired_rows(identifier="one", lots=20):
    return [dict(variant="X1", signal_id=identifier, path="OHLC", year=2022,
                 profile=profile, entry_identity=str(lots), entered_lots=lots, net_r=float(i))
            for i, profile in enumerate(r.PROFILES)]


def test_paired_summary_excludes_unfilled_and_partial_entry_cohorts():
    result = r.paired_summary(paired_rows() + paired_rows("none", 0) + paired_rows("partial", 8))
    group = result["groups"]["X1/OHLC/2022"]
    assert (group["raw"], group["filled20"], group["nofill"], group["mismatches"]) == (3, 1, 1, 0)
    assert group["mean_net_r"] == {"F": 0., "H": 1., "K": 2., "U": 3.}
    assert group["mean_delta"] == {"K-H": 1., "U-K": 1., "U-F": 3.}
    assert result["uncertainty"] == "DESCRIPTIVE_ONLY_OVERLAPPING_COHORTS"


@pytest.mark.parametrize("bad", ["mismatch", "missing", "duplicate"])
def test_paired_comparison_rejects_invalid_evidence(bad):
    rows = paired_rows()
    if bad == "mismatch":
        rows[-1]["entry_identity"] = "different-price"
    elif bad == "missing":
        rows.pop()
    else:
        rows.append(rows[0])
    with pytest.raises(ValueError):
        r.paired_summary(rows)


def gates():
    stats = dict(total_return=.8, mtm_mdd=.1, completed_campaigns=60, cagr=.22,
                 positive_month_fraction=.6, yearly_returns={"2023": .2, "2024": .2, "2025": .2},
                 top5_removed_return=.3, top5_share=.3)
    results = {row["id"]: dict(statistics=deepcopy(stats)) for row in r.planned_registry()}
    for key, value in results.items():
        if "/X0_F/" in key or "/S_F/" in key:
            value["statistics"]["total_return"] = .3
    bounds = dict(lower_bounds={f"{p}/{a}-{b}": .001 for p in r.PATHS for a, b in r.COMPARISONS})
    return results, bounds


def test_full_gate_pass_and_growth_independence():
    results, bounds = gates()
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["improvement_status"] == outcome["joint_status"] == "HISTORICAL_IMPROVEMENT"
    assert outcome["high_growth_status"] == "HISTORICAL_TARGET_MET"
    results["OOS/X3_U/c1/d5/OHLC"]["statistics"]["cagr"] = .19
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["improvement_status"] == "HISTORICAL_IMPROVEMENT"
    assert outcome["high_growth_status"] == "NOT_MET"


@pytest.mark.parametrize("candidate", ["X3_U", "X4_U"])
@pytest.mark.parametrize("cost,delay,path", r.CELLS)
@pytest.mark.parametrize("phase,field,value", [
    ("OOS", "total_return", 0.), ("OOS", "mtm_mdd", .251),
    ("OOS", "yearly_returns", {"2023": .1, "2024": -.1, "2025": -.1}),
    ("PARTIAL", "total_return", 0.), ("PARTIAL", "mtm_mdd", .251),
])
def test_every_primary_and_secondary_stress_cell_is_required(candidate, cost, delay, path, phase, field, value):
    results, bounds = gates()
    results[f"{phase}/{candidate}/c{cost}/d{delay}/{path}"]["statistics"][field] = value
    outcome = r.evaluate_gates(results, bounds)
    affected = outcome if candidate == "X3_U" else outcome["secondary"]
    unaffected = outcome["secondary"] if candidate == "X3_U" else outcome
    assert affected["improvement_status"] == "NOT_PROVEN"
    assert unaffected["improvement_status"] == "HISTORICAL_IMPROVEMENT"


@pytest.mark.parametrize("path", r.PATHS)
def test_zero_adjusted_lower_and_joint_failure_are_not_success(path):
    results, bounds = gates()
    bounds["lower_bounds"][f"{path}/X3_U-X0_F"] = 0.
    bounds["lower_bounds"][f"{path}/J_U-S_F"] = 0.
    result = r.evaluate_gates(results, bounds)
    assert result["improvement_status"] == result["joint_status"] == "NOT_PROVEN"


@pytest.mark.parametrize("path", r.PATHS)
def test_secondary_zero_lower_does_not_change_primary_designation(path):
    results, bounds = gates()
    bounds["lower_bounds"][f"{path}/X4_U-X0_F"] = 0.
    bounds["lower_bounds"][f"{path}/J4_U-S_F"] = 0.
    outcome = r.evaluate_gates(results, bounds)
    assert outcome["improvement_status"] == "HISTORICAL_IMPROVEMENT"
    assert outcome["secondary"]["improvement_status"] == outcome["secondary"]["joint_status"] == "NOT_PROVEN"


def fake_run_setup(tmp_path, monkeypatch, cohort=False):
    saved = r.preregister(tmp_path)
    r.write_json(tmp_path / "synthetic_profile.json", dict(kind="SYNTHETIC_NOT_MARKET", seconds=.001,
                 source_hashes=saved["contract"]["source_hashes"]))
    monkeypatch.setattr(r, "validate_environment", lambda: None)
    times = np.arange(r.TRAIN - r.BAR, r.TRAIN + 30 * r.BAR, r.BAR)
    bars = np.column_stack((times, np.ones((len(times), 4)) * 100.))
    monkeypatch.setattr(r, "load_inputs", lambda *args: (bars, np.empty((0, 2)), {}))
    tapes = {v: dict(signals=[], contexts={}) for v in ("X0", "X1", "X1A", "X2", "X3", "X4", "S", "J", "J4")}
    if cohort:
        tapes["X1"]["signals"] = [signal()]
    monkeypatch.setattr(r, "prepare_tapes", lambda bars: (tapes, {}, []))
    monkeypatch.setattr(r, "summarize", lambda raw: raw)


@pytest.mark.parametrize("error,status", [(ValueError("bad fixture"), "INVALID_RESEARCH"),
    (KeyboardInterrupt(), "INCOMPLETE"), (r.ResourceLimit("budget fixture"), "INCOMPLETE")])
def test_failure_preserves_prior_trial_and_refuses_resume(tmp_path, monkeypatch, error, status):
    fake_run_setup(tmp_path, monkeypatch)
    calls = []
    def backend(*args):
        calls.append(1)
        if len(calls) == 2:
            raise error
        return dict(daily_nav=[[r.OOS, 10_000.]], metrics={}, counters={}, statistics={}, hashes={}, fixture="saved")
    monkeypatch.setattr(r, "run_adaptive", backend)
    with pytest.raises(type(error)):
        r.run_experiment(tmp_path, "unused", "unused")
    registry = json.loads((tmp_path / "registry.json").read_text())
    assert registry[0]["status"] == "OK" and registry[1]["status"] == status
    assert all(row["status"] == "PLANNED" for row in registry[2:])
    assert json.loads((tmp_path / "trials" / registry[0]["id"] / "result.json").read_text())["fixture"] == "saved"
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["auto_activate"] is False and failure["profitability_status"] == "INSUFFICIENT"
    with pytest.raises(ValueError, match="fresh output"):
        r.run_experiment(tmp_path, "unused", "unused")


def test_partial_cohort_records_survive_budget_failure(tmp_path, monkeypatch):
    fake_run_setup(tmp_path, monkeypatch, cohort=True)
    calls = []
    raw = dict(daily_nav=[[r.OOS, 10_000.]], metrics=dict(initial_nav=10_000., final_nav=10_001.,
        fees=.1, slippage=.1, funding=0.), counters={}, statistics={}, hashes={}, entry_fills=[dict(lots=20)], final_positions={})
    def backend(*args):
        calls.append(1)
        if len(calls) == 195:
            raise r.ResourceLimit("paired budget fixture")
        return raw
    monkeypatch.setattr(r, "run_adaptive", backend)
    with pytest.raises(r.ResourceLimit):
        r.run_experiment(tmp_path, "unused", "unused")
    assert all(row["status"] == "OK" for row in json.loads((tmp_path / "registry.json").read_text()))
    assert json.loads((tmp_path / "cohort_registry.json").read_text())[0]["status"] == "INCOMPLETE"
    with gzip.open(tmp_path / "paired_records.jsonl.gz", "rt") as stream:
        records = [json.loads(line) for line in stream]
    assert [row["profile"] for row in records] == ["F", "H"]
    assert all(row["net_r"] == 50. for row in records)
    failure = json.loads((tmp_path / "failure.json").read_text())
    assert failure["checkpoint"]["profile"] == "K"


def test_policy_input_error_cannot_be_normal_result():
    with pytest.raises(ValueError, match="missing U"):
        r.summarize(dict(counters=dict(policy_errors=1)))


def test_complete_fixture_report_never_activates_even_when_historical_gates_pass(tmp_path, monkeypatch):
    fake_run_setup(tmp_path, monkeypatch)
    raw = dict(daily_nav=[[r.OOS, 10_000.]], metrics={}, counters={}, statistics={}, hashes={})
    monkeypatch.setattr(r, "run_adaptive", lambda *args: raw)
    monkeypatch.setattr(r, "joint_bounds", lambda results: {"fixture": True})
    monkeypatch.setattr(r, "evaluate_gates", lambda results, bounds: dict(
        improvement_status="HISTORICAL_IMPROVEMENT", high_growth_status="HISTORICAL_TARGET_MET"))
    checks = []
    monkeypatch.setattr(r, "recheck_inputs", lambda *args: checks.append("selected-inputs"))
    report = r.run_experiment(tmp_path, "unused", "unused")
    assert report["status"] == "COMPLETE"
    assert report["auto_activate"] is False
    assert report["profitability_status"] == "INSUFFICIENT"
    assert report["selection"] == "NONE"
    assert checks == ["selected-inputs"]
    assert json.loads((tmp_path / "run_state.json").read_text())["status"] == "COMPLETE"


@pytest.mark.parametrize("field,value", [("kind", "MARKET"), ("seconds", 0.),
                                        ("seconds", -1.), ("source_hashes", {})])
def test_invalid_synthetic_profile_blocks_before_data_read(tmp_path, monkeypatch, field, value):
    fake_run_setup(tmp_path, monkeypatch)
    profile_path = tmp_path / "synthetic_profile.json"
    profile = json.loads(profile_path.read_text())
    profile[field] = value
    r.write_json(profile_path, profile)
    monkeypatch.setattr(r, "load_inputs", lambda *args: pytest.fail("must not load market data"))
    with pytest.raises(ValueError, match="synthetic profile"):
        r.run_experiment(tmp_path, "unused", "unused")
