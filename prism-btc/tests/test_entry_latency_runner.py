"""Synthetic orchestration tests for a separately labelled post-hoc diagnostic."""
from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json

import numpy as np
import pytest

from analysis import entry_latency_diagnostic as r
from analysis.transition_retest import planned_registry


def fixture(tmp_path, monkeypatch):
    reference, output = tmp_path / "reference", tmp_path / "diagnostic"
    raw = dict(daily_nav=[[1_672_617_600_000, 10_000.]], metrics={"fixture": True},
               counters={}, hashes={"fixture": "synthetic"}, statistics={"daily_returns": [0.], "total_return": 0.})
    tapes = {name: dict(signals=[], contexts={}) for name in ("X0", "X3", "X4", "J4")}
    manifest = dict(transition={})
    main_contract = dict(fixture="synthetic main study")
    main_hash = r.digest(main_contract)
    monkeypatch.setattr(r, "REFERENCE_CONTRACT_HASH", main_hash)
    r.write_json(reference / "contract.json", dict(contract=main_contract, contract_hash=main_hash))
    r.write_json(reference / "report.json", dict(status="COMPLETE", contract_hash=main_hash, data_hash=r.digest(manifest)))
    main_rows = [{**row, "status": "OK", "result_hash": r.digest(raw)} for row in planned_registry()]
    r.write_json(reference / "registry.json", main_rows)
    r.write_json(reference / "data_manifest.json", manifest)
    r.write_json(reference / "signals.json", {name: [] for name in tapes})
    r.write_json(reference / "run_state.json", dict(status="COMPLETE", contract_hash=main_hash, data_hash=r.digest(manifest)))
    for row in r.registry():
        if row["entry_delay"] == 5:
            path = reference / f"trials/OOS/{row['name']}/c{row['cost']}/d5/{row['path']}/result.json"
            r.write_json(path, raw)
    monkeypatch.setattr(r, "validate_environment", lambda: None)
    monkeypatch.setattr(r, "load_inputs", lambda *args: (np.empty((0, 5)), np.empty((0, 2)), {}))
    monkeypatch.setattr(r, "prepare_tapes", lambda bars: (tapes, {}, []))
    monkeypatch.setattr(r, "summarize", lambda value: value)
    monkeypatch.setattr(r, "run_adaptive", lambda *args: deepcopy(raw))
    monkeypatch.setattr(r, "recheck_inputs", lambda *args: None)
    return output, reference, raw, tapes


def test_exact_32_registry_control_and_ideal_pairs():
    rows = r.registry()
    assert len(rows) == len({row["id"] for row in rows}) == 32
    assert Counter(row["entry_delay"] for row in rows) == {5: 16, 0: 16}
    assert set(row["name"] for row in rows) == {"X0_F", "X3_U", "X4_U", "J4_U"}
    assert Counter(row["cost"] for row in rows) == {1: 16, 2: 16}
    assert Counter(row["path"] for row in rows) == {"OHLC": 16, "OLHC": 16}
    for i in range(0, 32, 2):
        a, b = rows[i:i + 2]
        assert (a["name"], a["cost"], a["path"]) == (b["name"], b["cost"], b["path"])
        assert (a["entry_delay"], b["entry_delay"]) == (5, 0)


def test_contract_is_posthoc_separate_and_never_promotes(tmp_path, monkeypatch):
    output, reference, _, _ = fixture(tmp_path, monkeypatch)
    saved = r.preregister(output, reference)
    contract = saved["contract"]
    assert saved["contract_hash"] == r.digest(contract)
    assert contract["kind"] == "POST_HOC_EXECUTION_ASSUMPTION_SENSITIVITY"
    assert contract["use_for_promotion"] is False and contract["auto_activate"] is False
    assert contract["profitability_status"] == "INSUFFICIENT"
    assert contract["selection"] == "NONE" and contract["other_timing_minutes"] == 5
    assert len(contract["reference_hashes"]) == 5
    before = (output / "contract.json").read_bytes()
    assert r.preregister(output, reference) == saved  # Identical registration is idempotent.
    assert (output / "contract.json").read_bytes() == before
    report = json.loads((reference / "report.json").read_text())
    r.write_json(reference / "report.json", dict(**report, changed=True))
    with pytest.raises((ValueError, FileExistsError)):
        r.preregister(output, reference)
    assert (output / "contract.json").read_bytes() == before


@pytest.mark.parametrize("mutation", ["source", "reference", "checksum"])
def test_source_reference_and_contract_checksum_are_frozen(tmp_path, monkeypatch, mutation):
    output, reference, _, _ = fixture(tmp_path, monkeypatch)
    saved = r.preregister(output, reference)
    if mutation == "source":
        original = r.file_hash
        monkeypatch.setattr(r, "file_hash", lambda p: "changed" if str(p).endswith("entry_latency_diagnostic.py") else original(p))
    elif mutation == "reference":
        r.write_json(reference / "report.json", dict(changed=True))
    else:
        saved["contract"]["selection"] = "TAMPERED"
        r.write_json(output / "contract.json", saved)
    monkeypatch.setattr(r, "load_inputs", lambda *args: pytest.fail("must reject before reading inputs"))
    with pytest.raises(ValueError, match="source/reference changed|study linkage invalid"):
        r.run(output, reference, "unused", "unused")


def test_all_32_success_preserves_main_verdict_and_changes_only_entry_timing(tmp_path, monkeypatch):
    output, reference, raw, _ = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    configs = []
    def backend(*args):
        configs.append(asdict(args[4]))
        return deepcopy(raw)
    monkeypatch.setattr(r, "run_adaptive", backend)
    result = r.run(output, reference, "unused", "unused")
    assert len(configs) == 32
    for i in range(0, 32, 2):
        control, ideal = configs[i:i + 2]
        assert control["timing_ms"] == ideal["timing_ms"] == 300_000
        assert control["entry_latency_ms"] is None and ideal["entry_latency_ms"] == 0
        assert {k: v for k, v in control.items() if k not in ("entry_latency_ms", "resource_check")} == {
            k: v for k, v in ideal.items() if k not in ("entry_latency_ms", "resource_check")}
    assert result["status"] == "COMPLETE" and len(result["trial_summaries"]) == 32
    assert result["baseline_matches"] == result["ideal_entry_cases"] == 16
    assert result["main_verdict_unchanged"] is True
    assert result["kind"] == "POST_HOC_EXECUTION_ASSUMPTION_SENSITIVITY"
    assert result["use_for_promotion"] is False and result["auto_activate"] is False
    assert result["selection"] == "NONE"
    assert result["profitability_status"] == "INSUFFICIENT"
    rows = json.loads((output / "registry.json").read_text())
    assert all(row["status"] == "OK" for row in rows)
    assert all((output / "trials" / row["id"] / "result.json").exists() for row in rows)
    assert all("daily_returns" not in row["statistics"] for row in result["trial_summaries"].values())
    with pytest.raises(ValueError, match="fresh diagnostic"):
        r.run(output, reference, "unused", "unused")


@pytest.mark.parametrize("status", ["RUNNING", "INCOMPLETE", "INVALID_RESEARCH"])
def test_incomplete_reference_blocks_before_data_load(tmp_path, monkeypatch, status):
    output, reference, _, _ = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    r.write_json(reference / "run_state.json", dict(status=status))
    monkeypatch.setattr(r, "load_inputs", lambda *args: pytest.fail("no incomplete reference input read"))
    with pytest.raises(ValueError, match="study linkage invalid"):
        r.run(output, reference, "unused", "unused")
    assert not (output / "run_state.json").exists()


@pytest.mark.parametrize("identity", ["data", "signals"])
def test_data_and_signal_identity_rejected_before_execution(tmp_path, monkeypatch, identity):
    output, reference, _, tapes = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    if identity == "data":
        monkeypatch.setattr(r, "load_inputs", lambda *args: (np.empty((0, 5)), np.empty((0, 2)), {"changed": True}))
    else:
        tapes["X3"]["signals"].append({"new": "signal"})
    monkeypatch.setattr(r, "run_adaptive", lambda *args: pytest.fail("no changed-input execution"))
    with pytest.raises(ValueError, match="identity differs|changed input signals"):
        r.run(output, reference, "unused", "unused")
    assert all(row["status"] == "PLANNED" for row in json.loads((output / "registry.json").read_text()))


def test_baseline_mismatch_preserves_previous_control_and_ideal_results(tmp_path, monkeypatch):
    output, reference, raw, _ = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    calls = []
    def backend(*args):
        calls.append(1)
        result = deepcopy(raw)
        if len(calls) == 3:
            result["metrics"]["unexpected"] = True
        return result
    monkeypatch.setattr(r, "run_adaptive", backend)
    with pytest.raises(ValueError, match="baseline financial result changed"):
        r.run(output, reference, "unused", "unused")
    rows = json.loads((output / "registry.json").read_text())
    assert [row["status"] for row in rows[:3]] == ["OK", "OK", "INVALID_DIAGNOSTIC"]
    assert all(row["status"] == "PLANNED" for row in rows[3:])
    for row in rows[:2]:
        assert json.loads((output / "trials" / row["id"] / "result.json").read_text()) == raw
    assert (output / "trials" / rows[2]["id"] / "events.jsonl.gz").exists()
    assert not (output / "report.json").exists()


@pytest.mark.parametrize("error,status", [(r.ResourceLimit("budget fixture"), "INCOMPLETE"),
    (KeyboardInterrupt(), "INCOMPLETE"), (ValueError("execution fixture"), "INVALID_DIAGNOSTIC")])
def test_execution_failure_preserves_completed_row_and_checkpoint(tmp_path, monkeypatch, error, status):
    output, reference, raw, _ = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    calls = []
    def backend(*args):
        calls.append(1)
        if len(calls) == 2:
            raise error
        return deepcopy(raw)
    monkeypatch.setattr(r, "run_adaptive", backend)
    with pytest.raises(type(error)):
        r.run(output, reference, "unused", "unused")
    rows = json.loads((output / "registry.json").read_text())
    assert rows[0]["status"] == "OK" and rows[1]["status"] == status
    failure = json.loads((output / "failure.json").read_text())
    assert failure["checkpoint"]["trial"] == rows[1]["id"]
    assert failure["use_for_promotion"] is False and failure["auto_activate"] is False
    assert failure["profitability_status"] == "INSUFFICIENT"


def test_reference_mutation_during_run_prevents_complete_report(tmp_path, monkeypatch):
    output, reference, _, _ = fixture(tmp_path, monkeypatch)
    r.preregister(output, reference)
    monkeypatch.setattr(r, "recheck_inputs", lambda *args: r.write_json(reference / "report.json", {"changed": True}))
    with pytest.raises(ValueError, match="changed during execution|study linkage invalid"):
        r.run(output, reference, "unused", "unused")
    assert not (output / "report.json").exists()
    assert json.loads((output / "run_state.json").read_text())["status"] == "INVALID_DIAGNOSTIC"


def test_reference_helper_returns_exact_16_pinned_control_hashes(tmp_path, monkeypatch):
    _, reference, raw, _ = fixture(tmp_path, monkeypatch)
    manifest, controls = r.verify_reference(reference)
    assert manifest == {"transition": {}}
    assert len(controls) == 16
    assert set(controls.values()) == {r.digest(raw)}
    assert set(controls) == {f"OOS/{row['name']}/c{row['cost']}/d5/{row['path']}"
                             for row in r.registry() if row["entry_delay"] == 5}


@pytest.mark.parametrize("mutation", ["duplicate", "missing", "status", "missing_control", "result_hash", "result_file"])
def test_reference_inventory_and_pinned_result_tampering_rejected(tmp_path, monkeypatch, mutation):
    _, reference, _, _ = fixture(tmp_path, monkeypatch)
    rows = json.loads((reference / "registry.json").read_text())
    control = next(row for row in rows if row["id"] == "OOS/X0_F/c1/d5/OHLC")
    if mutation == "duplicate":
        rows[-1] = rows[0]
    elif mutation == "missing":
        rows.pop()
    elif mutation == "status":
        rows[0]["status"] = "PLANNED"
    elif mutation == "missing_control":
        control["id"] = "unrelated-but-unique"
    elif mutation == "result_hash":
        control["result_hash"] = "wrong"
    else:
        r.write_json(reference / "trials/OOS/X0_F/c1/d5/OHLC/result.json", {"tampered": True})
    r.write_json(reference / "registry.json", rows)
    with pytest.raises(ValueError, match="registry inventory/status|control result hash"):
        r.verify_reference(reference)


@pytest.mark.parametrize("file,field,value", [
    ("contract.json", "contract_hash", "wrong"),
    ("contract.json", "contract", {"changed": True}),
    ("report.json", "contract_hash", "wrong"),
    ("run_state.json", "contract_hash", "wrong"),
    ("report.json", "data_hash", "wrong"),
    ("run_state.json", "data_hash", "wrong"),
    ("report.json", "status", "INCOMPLETE"),
    ("run_state.json", "status", "RUNNING"),
])
def test_reference_linkage_chain_is_not_trusted_from_status_alone(tmp_path, monkeypatch, file, field, value):
    _, reference, _, _ = fixture(tmp_path, monkeypatch)
    original = json.loads((reference / file).read_text())
    original[field] = value
    r.write_json(reference / file, original)
    with pytest.raises(ValueError, match="study linkage invalid"):
        r.verify_reference(reference)


def test_reference_manifest_and_expected_contract_pin_are_checked(tmp_path, monkeypatch):
    _, reference, _, _ = fixture(tmp_path, monkeypatch)
    original = r.REFERENCE_CONTRACT_HASH
    monkeypatch.setattr(r, "REFERENCE_CONTRACT_HASH", "another-study")
    with pytest.raises(ValueError, match="study linkage invalid"):
        r.verify_reference(reference)
    monkeypatch.setattr(r, "REFERENCE_CONTRACT_HASH", original)
    r.write_json(reference / "data_manifest.json", {"different": "history"})
    with pytest.raises(ValueError, match="study linkage invalid"):
        r.verify_reference(reference)
