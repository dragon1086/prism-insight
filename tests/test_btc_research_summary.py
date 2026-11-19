from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from tools import render_btc_research_summary as summary
from tools.btc_research_call_guard import CallGuard


def dump(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def report():
    value = dict(simulated_only=True, mode="HISTORICAL_LLM_RESEARCH", execution_status="COMPLETE",
        economic=dict(completed_scenarios=2, wins=1, losses=1, net_change=1., fees=.2,
                      funding_net=.01, max_drawdown_pct=.3, open_quantity=0.,
                      pending_intents=0, unknown_exchange_children=0),
        execution_metadata=dict(mode="fresh", actual_model_calls=None),
        coverage=dict(planned_slots=9, model_review_records=2),
        execution_inventory=[dict(execId=f"fill-{i}", execType="Trade") for i in range(4)],
        closed_trade_detail=[dict(net_usdt=2., net_return_on_initial_notional=.02),
                             dict(net_usdt=-1., net_return_on_initial_notional=-.01)])
    value.update({key: "a" * 64 for key in summary.HASH_FIELDS})
    value["contract_hash"] = summary.digest({"registered": True})
    return value


def tape(path, groups=((0,), (1, 2)), error=False, missing=False):
    header = dict(kind="economic_header", schema=4, contract={"registered": True},
                  contract_hash=summary.digest({"registered": True}))
    rows = [header]
    for group in groups:
        ops = [dict(kind="generate", request={"unique_input": i}, text=f"answer-{i}") for i in group]
        if error:
            ops.append(dict(kind="generate", request={"unique_input": "error"},
                            error={"message": "private model text must not be printed"}))
        if missing:
            ops.append(dict(kind="generate", request={"unique_input": "missing"}))
        envelope = dict(operations=ops, outcome={"status": "FAILED" if error or missing else "COMPLETE"})
        envelope["sha256"] = summary.digest(envelope)
        row = dict(kind="economic_tick", decision={"envelope": envelope})
        row["sha256"] = summary.digest(row)
        rows.append(row)
    body = "\n".join(json.dumps(row) for row in rows) + "\n"
    path.write_bytes(gzip.compress(body.encode(), mtime=0) if path.suffix == ".gz" else body.encode())
    return rows


def tree_hashes(path):
    return {str(p.relative_to(path)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in path.rglob("*") if p.is_file()}


def study(path, cached):
    dump(path / "registry.json", {"max_http": 2})
    guard = CallGuard(path / "http-guard",
        hashlib.sha256((path / "registry.json").read_bytes()).hexdigest(), 2)
    for _ in range(2):
        attempt = guard.reserve("one-arm", "DRAFT", "a" * 64)
        guard.finish(attempt, "ok", actual_http_calls=1)
    guard.stop("completed")
    dump(path / "cache-certificate.json", dict(tape_path=str(cached),
        tape_sha256=hashlib.sha256(cached.read_bytes()).hexdigest(),
        reused_response_count=1, missing_response_attempts=1))
    dump(path / "cache-used.json", [summary.digest({"unique_input": 0})])


def test_stages_reuse_and_http_have_separate_scopes_without_writes(tmp_path, monkeypatch):
    current, cached = tmp_path / "current.gz", tmp_path / "cached.gz"
    tape(current)
    tape(cached, groups=((0,),))
    study(tmp_path, cached)
    before = tree_hashes(tmp_path)
    monkeypatch.setattr(CallGuard, "__init__", lambda *a, **kw: pytest.fail("must not reopen ledger"))
    facts = summary.summarize(report(), tape=current, study=tmp_path)
    assert (facts["reviews"], facts["replies"], facts["stages"]) == (2, 3, {1: 1, 2: 1})
    assert facts["report_http"] is None
    assert facts["study"] == dict(http=2, unknown_http=0, cached_matches=1, prior_missing_responses=1)
    assert facts["fills"] == 4 and facts["closed"] == 2
    assert facts["mean_winner"] == .02 and facts["mean_loser"] == -.01
    assert "2.000000" in summary.render(facts)
    assert tree_hashes(tmp_path) == before


def test_missing_evidence_is_not_zero_or_a_pass():
    facts = summary.summarize(report())
    assert facts["reviews"] is None and facts["report_http"] is None
    assert facts["comparison"]["status"] == "MISSING"
    assert "미확인" in summary.render(facts)


def test_frozen_execution_is_not_a_new_model_call():
    value = report()
    value["execution_metadata"] = dict(mode="frozen", actual_model_calls=0)
    facts = summary.summarize(value)
    assert facts["report_http"] == 0
    assert "저장 응답 재생" in summary.render(facts)


def test_stopped_partial_results_and_generation_errors_remain_visible(tmp_path):
    path = tmp_path / "tape"
    tape(path, groups=((0,),), error=True, missing=True)
    value = report()
    value["execution_status"] = "STOPPED"
    value["coverage"]["model_review_records"] = 1
    value["economic"]["open_quantity"] = .01
    facts = summary.summarize(value, tape=path)
    assert (facts["replies"], facts["generation_errors"], facts["missing_responses"]) == (1, 1, 1)
    assert facts["failed_reviews"] == 1 and facts["open_quantity"] == .01
    text = summary.render(facts)
    assert "STOPPED" in text and "private model text" not in text


@pytest.mark.parametrize("key", summary.HASH_FIELDS)
def test_any_reported_hash_difference_remains_failed(key):
    original, frozen = report(), report()
    frozen["execution_metadata"] = dict(mode="frozen", actual_model_calls=0)
    frozen[key] = "b" * 64
    facts = summary.summarize(original, frozen=frozen)
    assert facts["comparison"]["status"] == "FAILED"
    assert facts["comparison"]["differences"] == [key]
    assert "FAILED" in summary.render(facts)


def test_matching_hashes_are_only_report_comparison_not_new_replay():
    frozen = report()
    frozen["execution_metadata"] = dict(mode="frozen", actual_model_calls=0)
    facts = summary.summarize(report(), frozen=frozen)
    assert facts["comparison"]["status"] == "MATCH_REPORTED_HASHES"
    assert "독립 재생을 새로 실행했다는 뜻이 아닙니다" in summary.render(facts)
    del frozen["runtime_event_hash"]
    assert summary.summarize(report(), frozen=frozen)["comparison"]["status"] == "MISSING"


def test_frozen_evidence_must_be_a_zero_call_frozen_report():
    with pytest.raises(ValueError, match="not_frozen"):
        summary.summarize(report(), frozen=report())


def test_repeated_hashes_cannot_hide_different_economic_results():
    frozen = report()
    frozen["execution_metadata"] = dict(mode="frozen", actual_model_calls=0)
    frozen["economic"]["net_change"] = 1000.
    result = summary.summarize(report(), frozen=frozen)["comparison"]
    assert result["status"] == "FAILED" and result["differences"] == ["economic"]


def test_missing_optional_frozen_trade_details_are_not_a_match():
    frozen = report()
    frozen["execution_metadata"] = dict(mode="frozen", actual_model_calls=0)
    del frozen["closed_trade_detail"]
    assert summary.summarize(report(), frozen=frozen)["comparison"]["status"] == "MISSING"


def test_tape_tampering_and_wrong_run_are_rejected(tmp_path):
    path = tmp_path / "tape"
    rows = tape(path)
    rows[1]["decision"]["envelope"]["operations"][0]["text"] = "tampered"
    path.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="row_hash"):
        summary.summarize(report(), tape=path)
    tape(path)
    wrong = report()
    wrong["contract_hash"] = "c" * 64
    with pytest.raises(ValueError, match="wrong_report"):
        summary.summarize(wrong, tape=path)


def test_cache_certificate_does_not_turn_a_changed_reply_into_reuse(tmp_path):
    current, cached = tmp_path / "current", tmp_path / "cached"
    rows = tape(current)
    tape(cached, groups=((0,),))
    study(tmp_path, cached)
    rows[1]["decision"]["envelope"]["operations"][0]["text"] = "new reply"
    env = rows[1]["decision"]["envelope"]
    env["sha256"] = summary.digest({k: v for k, v in env.items() if k != "sha256"})
    rows[1]["sha256"] = summary.digest({k: v for k, v in rows[1].items() if k != "sha256"})
    current.write_text("\n".join(json.dumps(row) for row in rows))
    with pytest.raises(ValueError, match="cached_response_changed"):
        summary.summarize(report(), tape=current, study=tmp_path)


def test_active_ledger_is_only_inspected_and_unknown_attempt_stays_unknown(tmp_path):
    dump(tmp_path / "registry.json", {"max_http": 2})
    guard = CallGuard(tmp_path / "http-guard",
        hashlib.sha256((tmp_path / "registry.json").read_bytes()).hexdigest(), 2)
    guard.reserve("arm", "DRAFT", "a" * 64)
    before = tree_hashes(tmp_path)
    facts = summary.summarize(report(), study=tmp_path)
    assert facts["study"]["http"] == 0 and facts["study"]["unknown_http"] == 1
    assert tree_hashes(tmp_path) == before


@pytest.mark.parametrize("field,value", [("wins", True), ("losses", -1), ("net_change", float("nan"))])
def test_invalid_numbers_are_not_rendered_as_valid_results(field, value):
    source = report()
    source["economic"][field] = value
    with pytest.raises(ValueError):
        summary.summarize(source)


def test_fill_deduplication_and_conflicting_identity():
    source = report()
    source["execution_inventory"].append(deepcopy(source["execution_inventory"][0]))
    assert summary.summarize(source)["fills"] == 4
    source["execution_inventory"][-1]["execQty"] = "different"
    with pytest.raises(ValueError, match="fill_identity"):
        summary.summarize(source)


def test_zero_trades_are_not_missing_trades():
    source = report()
    source["economic"].update(completed_scenarios=0, wins=0, losses=0)
    source["closed_trade_detail"] = []
    text = summary.render(summary.summarize(source))
    assert "해당 없음 / 해당 없음" in text


def test_flat_quantity_does_not_claim_the_scenario_is_settled():
    source = report()
    source["economic"]["unclosed_scenario"] = True
    facts = summary.summarize(source)
    assert facts["open_quantity"] == 0 and facts["unclosed_scenario"] is True
    assert "미종료 시나리오 | 있음" in summary.render(facts)


def test_standalone_cli_runs_from_outside_repo_and_never_changes_input(tmp_path):
    dump(tmp_path / "report.json", report())
    path = tmp_path / "tape.gz"
    tape(path)
    before = tree_hashes(tmp_path)
    result = subprocess.run([sys.executable, str(Path(summary.__file__).resolve()),
        "--run", str(tmp_path), "--tape", str(path)], cwd=tmp_path,
        capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "테이프 판단 기록 | 9 / 2" in result.stdout
    assert "미확인" in result.stdout
    assert tree_hashes(tmp_path) == before


def test_cli_rejects_unsafe_report_without_echoing_private_fields(tmp_path, capsys):
    value = report()
    value.update(simulated_only=False, mode="private account payload")
    dump(tmp_path / "report.json", value)
    assert summary.main(["--run", str(tmp_path)]) == 1
    captured = capsys.readouterr()
    assert not captured.out and "private account payload" not in captured.err
