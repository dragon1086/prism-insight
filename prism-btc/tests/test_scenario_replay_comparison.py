import copy
import importlib.util
import json
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "replay_comparison", Path(__file__).resolve().parents[2] / "tools/compare_btc_scenario_replays.py")
comparison = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(comparison)


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def fixture(root, *, name="A", path="OHLC", multiplier=1):
    contract = dict(path=path, initial_equity=1000, start_ms=0, end_ms=300000,
                    kind="SIMULATED_LLM_SCENARIO", decision_origin="luna", decision_interval_ms=300000,
                    model="gpt-6-luna", effort="high", tier="fast", runtime_versions={"python": "test"},
                    data_hash="data", prompt_hash="prompt", source_hashes={"a": "source"},
                    costs=dict(maker_fee=.0002 * multiplier, taker_fee=.00055 * multiplier,
                               slippage=.0005 * multiplier, spread=.0001, participation=.01))
    executions = [dict(execId="one", orderId="order1", execQty="2", execPrice="100",
                       execFee="1", execTime="10", side="Buy"),
                  dict(execId="two", orderId="order2", execQty="1", execPrice="110",
                       execFee="1", execTime="20", side="Sell")]
    transactions = [dict(id="t" + row["execId"], type="TRADE", tradeId=row["execId"],
                         orderId=row["orderId"], transactionTime=row["execTime"],
                         fee="1", funding="0", cashFlow=str(gross), change=str(gross - 1))
                    for row, gross in zip(executions, [0, 10])]
    transactions.append(dict(id="f", type="SETTLEMENT", transactionTime="30", fee="0",
                             cashFlow="0", funding=".5", change=".5"))
    exchange = dict(simulation=True, timestamp_ms=300000, equity="1012.5", cash="1008.5", position="1",
                    average_price="100", executions=executions, transactions=transactions)
    economic = dict(final_equity=1012.5, net_change=12.5, fees=2, funding_net=.5,
                    open_quantity=1, max_drawdown_pct=1, completed_scenarios=1,
                    wins=1, losses=0, largest_winner_removed_net=0)
    report = dict(simulated_only=True, mode="HISTORICAL_LLM_RESEARCH", decisions=1,
                  decision_outcomes={"WAIT": 1}, contract=contract, contract_hash=comparison.digest(contract),
                  exchange_hash=comparison.digest(exchange), economic=economic)
    report["result_hash"] = comparison.digest(report)
    for mode in ("fresh", "frozen"):
        data = dict(report, execution_metadata=dict(mode=mode, actual_model_calls=1 if mode == "fresh" else 0))
        for filename, value in (("report.json", data), ("exchange.json", exchange),
                                ("equity.json", [[0, 1000], [15, 990], [300000, 1012.5]])):
            save(root / name / mode / filename, value)
    return dict(name=name, path=path, cost_multiplier=multiplier)


def registry_for(tmp_path, variants):
    contract = comparison.read(tmp_path / "A/fresh/report.json")["contract"]
    return dict(variants=variants, start="1970-01-01T00:00:00Z", end="1970-01-01T00:05:00Z",
                initial_equity=1000, model="gpt-6-luna", reasoning="high", service_tier="priority",
                source_reference="source-commit", source_pin_reference="source-commit",
                expected_data_hash=contract["data_hash"], expected_prompt_hash=contract["prompt_hash"],
                expected_source_hashes_sha256=comparison.digest(contract["source_hashes"]),
                expected_runtime_versions=contract["runtime_versions"],
                expected_contract_common_hash=comparison.digest({k: v for k, v in contract.items()
                                                               if k not in {"path", "costs"}}))


def registered(tmp_path):
    variants = [fixture(tmp_path), fixture(tmp_path, name="B", path="OLHC"),
                fixture(tmp_path, name="C", multiplier=2)]
    save(tmp_path / "registry.json", registry_for(tmp_path, variants))
    return variants


def rehash(root, mode="fresh"):
    folder = root / "A" / mode
    report = comparison.read(folder / "report.json")
    report["contract_hash"] = comparison.digest(report["contract"])
    report["exchange_hash"] = comparison.digest(comparison.read(folder / "exchange.json"))
    report["result_hash"] = comparison.digest({k: v for k, v in report.items()
                                              if k not in {"result_hash", "execution_metadata"}})
    save(folder / "report.json", report)


def test_all_registered_paths_costs_accounting_and_frozen(tmp_path):
    registered(tmp_path)
    result = comparison.compare(tmp_path)
    assert result["comparison_complete"]
    assert len(result["arms"]) == 3
    assert result["arms"][0]["accounting"] == dict(gross_realized="10", fees_counted_once="2",
        funding_net="0.5", unrealized="4.0", net_equity_change="12.5")
    assert "CLOSED_SCENARIOS_LT_60" in result["arms"][0]["insufficiency_reasons"]
    assert not result["profitability_proven"] and not result["automatic_promotion"]


@pytest.mark.parametrize("mutation", ["hash", "fee", "duplicate", "quantity", "time", "funding", "curve"])
def test_corruption_rejected(tmp_path, mutation):
    registered(tmp_path)
    folder = tmp_path / "A" / "fresh"
    exchange = comparison.read(folder / "exchange.json")
    if mutation == "hash":
        exchange["cash"] = "999"
    elif mutation == "fee":
        exchange["transactions"][0]["fee"] = "2"
    elif mutation == "duplicate":
        exchange["executions"].append(copy.deepcopy(exchange["executions"][0]))
    elif mutation == "quantity":
        exchange["executions"][0]["execQty"] = "3"
    elif mutation == "time":
        exchange["transactions"][0]["transactionTime"] = "11"
    elif mutation == "funding":
        exchange["transactions"][0]["funding"] = "1"
    else:
        save(folder / "equity.json", [[0, 1000], [300000, 1012.5]])
    save(folder / "exchange.json", exchange)
    if mutation != "hash":
        rehash(tmp_path)
    result = comparison.compare(tmp_path)
    assert result["arms"][0]["status"] == "INVALID"
    assert not result["comparison_complete"]


def test_cohort_mismatch_and_frozen_mismatch(tmp_path):
    registered(tmp_path)
    for mode in ("fresh", "frozen"):
        folder = tmp_path / "A" / mode
        report = comparison.read(folder / "report.json")
        report["contract"]["data_hash"] = "different"
        save(folder / "report.json", report)
        rehash(tmp_path, mode)
    assert comparison.compare(tmp_path)["arms"][0]["error"] == "registry_data_mismatch"
    for mode in ("fresh", "frozen"):
        report = comparison.read(tmp_path / "A" / mode / "report.json")
        report["contract"]["data_hash"] = "data"
        save(tmp_path / "A" / mode / "report.json", report)
        rehash(tmp_path, mode)
    report = comparison.read(tmp_path / "A/frozen/report.json")
    report["economic"]["wins"] = 0
    save(tmp_path / "A/frozen/report.json", report)
    rehash(tmp_path, "frozen")
    assert comparison.compare(tmp_path)["arms"][0]["error"] == "fresh_frozen_mismatch"


def test_missing_is_not_zero_and_frozen_calls_rejected(tmp_path):
    registered(tmp_path)
    (tmp_path / "B/fresh/report.json").unlink()
    (tmp_path / "C/frozen/report.json").unlink()
    report = comparison.read(tmp_path / "A/frozen/report.json")
    report["execution_metadata"]["actual_model_calls"] = 1
    save(tmp_path / "A/frozen/report.json", report)
    result = comparison.compare(tmp_path)
    assert [r["status"] for r in result["arms"]] == ["INVALID", "MISSING", "VERIFIED_FRESH"]
    assert "reported_metrics" not in result["arms"][1]


def test_output_never_overwritten(tmp_path, monkeypatch):
    registered(tmp_path)
    output = tmp_path / "result.json"
    monkeypatch.setattr("sys.argv", ["compare", "--root", str(tmp_path), "--output", str(output)])
    comparison.main()
    original = output.read_bytes()
    with pytest.raises(ValueError, match="never_overwrite"):
        comparison.main()
    assert output.read_bytes() == original


def test_losing_short_retains_funding_cost_and_unrealized_loss(tmp_path):
    variants = [fixture(tmp_path)]
    save(tmp_path / "registry.json", registry_for(tmp_path, variants))
    for mode in ("fresh", "frozen"):
        folder = tmp_path / "A" / mode
        exchange = comparison.read(folder / "exchange.json")
        exchange["executions"][0]["side"] = "Sell"
        exchange["executions"][1]["side"] = "Buy"
        exchange["transactions"][1].update(cashFlow="-10", change="-11")
        exchange["transactions"][2].update(funding="-.5", change="-.5")
        exchange.update(position="-1", cash="987.5", equity="983.5")
        save(folder / "exchange.json", exchange)
        report = comparison.read(folder / "report.json")
        report["economic"].update(final_equity=983.5, net_change=-16.5, funding_net=-.5,
                                  open_quantity=-1, max_drawdown_pct=1.65, wins=0, losses=1)
        save(folder / "report.json", report)
        save(folder / "equity.json", [[0, 1000], [300000, 983.5]])
        rehash(tmp_path, mode)
    result = comparison.compare(tmp_path)
    assert result["comparison_complete"]
    assert result["arms"][0]["accounting"]["net_equity_change"] == "-16.5"
    assert result["arms"][0]["accounting"]["unrealized"] == "-4.0"


@pytest.mark.parametrize("key,value", [
    ("start", "1969-12-31T23:55:00Z"), ("end", "1970-01-01T00:10:00Z"),
    ("initial_equity", 2000), ("model", "other"), ("reasoning", "low"),
    ("service_tier", "default"), ("source_reference", "other"),
    ("expected_data_hash", "other"), ("expected_prompt_hash", "other"),
    ("expected_source_hashes_sha256", "other"), ("expected_runtime_versions", {}),
    ("expected_contract_common_hash", "other")])
def test_same_wrong_cohort_cannot_pass_registry(tmp_path, key, value):
    registered(tmp_path)
    registry = comparison.read(tmp_path / "registry.json")
    registry[key] = value
    save(tmp_path / "registry.json", registry)
    result = comparison.compare(tmp_path)
    assert all(row["status"] == "INVALID" for row in result["arms"])
    assert not result["comparison_complete"]


@pytest.mark.parametrize("mutation", ["mode", "origin", "kind", "terminal", "curve", "decisions", "outcomes"])
def test_matching_rehashed_incomplete_or_nonfresh_runs_rejected(tmp_path, mutation):
    registered(tmp_path)
    for mode in ("fresh", "frozen"):
        folder = tmp_path / "A" / mode
        report = comparison.read(folder / "report.json")
        if mutation == "mode":
            report["execution_metadata"]["mode"] = "fixture"
        elif mutation == "origin":
            report["contract"]["decision_origin"] = "fixture"
        elif mutation == "kind":
            report["mode"] = "SYNTHETIC_FIXTURE"
        elif mutation == "terminal":
            exchange = comparison.read(folder / "exchange.json")
            exchange["timestamp_ms"] = 0
            save(folder / "exchange.json", exchange)
        elif mutation == "curve":
            save(folder / "equity.json", [[0, 1000], [15, 990], [20, 1012.5]])
        elif mutation == "decisions":
            report["decisions"] = 0
        else:
            report["decision_outcomes"] = {"WAIT": 0}
        save(folder / "report.json", report)
        rehash(tmp_path, mode)
    result = comparison.compare(tmp_path)
    assert result["arms"][0]["status"] == "INVALID"
    assert not result["comparison_complete"]


@pytest.mark.parametrize("filename", ["report.json", "exchange.json", "equity.json"])
def test_wrong_artifact_shape_is_invalid(tmp_path, filename):
    registered(tmp_path)
    save(tmp_path / "A/fresh" / filename, None)
    assert comparison.compare(tmp_path)["arms"][0]["status"] == "INVALID"


def test_missing_pin_is_invalid(tmp_path):
    registered(tmp_path)
    registry = comparison.read(tmp_path / "registry.json")
    del registry["expected_contract_common_hash"]
    save(tmp_path / "registry.json", registry)
    assert all(row["status"] == "INVALID" for row in comparison.compare(tmp_path)["arms"])


@pytest.mark.parametrize("key", ["spread", "participation"])
def test_fixed_cost_change_cannot_hide_in_common_contract_exclusion(tmp_path, key):
    registered(tmp_path)
    for name in ("A", "B", "C"):
        folder = tmp_path / name / "fresh"
        report = comparison.read(folder / "report.json")
        report["contract"]["costs"][key] = .02
        report["contract_hash"] = comparison.digest(report["contract"])
        report["result_hash"] = comparison.digest({k: v for k, v in report.items()
                                                  if k not in {"result_hash", "execution_metadata"}})
        save(folder / "report.json", report)
    assert all(row["status"] == "INVALID" for row in comparison.compare(tmp_path)["arms"])


def test_empty_curve_point_is_invalid(tmp_path):
    registered(tmp_path)
    save(tmp_path / "A/fresh/equity.json", [[]])
    assert comparison.compare(tmp_path)["arms"][0]["error"] == "invalid_equity_curve_shape"
