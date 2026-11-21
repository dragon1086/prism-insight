"""The stage-one coordinator uses fake HTTP/replays only; no network."""
from io import BytesIO
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import tools.btc_research_model_call as research_model_call


@pytest.fixture(autouse=True)
def offline_runtime_readiness(monkeypatch):
    monkeypatch.setattr(research_model_call, '_runtime_readiness', lambda phase, timeout: {'status': 'PASSED', 'phase': phase})
    monkeypatch.setattr(research_model_call, '_readiness_still_safe', lambda proof, phase, timeout: None)

from tools import btc_calculated_economic_study as study  # noqa: E402
from tests.test_scenario_replay import inputs  # noqa: E402
from tests.test_calculated_observed_data import replay_packet  # noqa: E402

ENDPOINT = "http://127.0.0.1:18741/v1/responses"


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, allow_nan=False))


def data_files(tmp_path, duration_ms=2400000):
    bundle, market = inputs(duration_ms)
    def rows(frame):
        return [dict(open_time=int(at.value // 1_000_000),
                     **{key: float(row[key]) for key in ("open", "high", "low", "close", "volume")})
                for at, row in frame.iterrows()]
    content = dict(schema_version=1, symbol="BTCUSDT", source_interval_ms=60000,
        start_ms=bundle["start_ms"], end_ms=bundle["end_ms"], source=rows(market.source),
        mark=rows(market.mark), warmup={tf: rows(frame) for tf, frame in market.warmup.items()},
        funding=bundle["funding"], funding_interval_ms=28800000)
    full = dict(content, data_hash=study.digest(content), coverage=market.manifest(), provenance={"fixture": True})
    path, observed = tmp_path / "market.json", tmp_path / "observed.json"
    save(path, full)
    save(observed, replay_packet(bundle))
    return path, observed, study.sha(observed)


def prepared(tmp_path):
    market, observed, pin = data_files(tmp_path)
    root = tmp_path / "study"
    registry = study.prepare(root, market, observed, pin, "fixture-source")
    return root, registry


class Response(BytesIO):
    status = 200

    def geturl(self):
        return ENDPOINT


def fake_factory(opened):
    def opener(request, timeout):
        opened.append(request)
        return Response(json.dumps(dict(status="completed", model=json.loads(request.data)['model'], output=[
            dict(type="message", role="assistant", content=[dict(type="output_text", text='{"action":"WAIT"}')])])).encode())
    return lambda guard, arm, record: study.GuardedModelCall(guard, arm, record, open_url=opener, monotonic=lambda: 0.)


def fake_replay(*, fail_arm=None, mismatch_arm=None, over_cap=False):
    def replay(bundle, market, output, *, mode, tape_path, **kwargs):
        output = Path(output)
        name = output.parent.name
        if mode == "frozen":
            output.mkdir()
            report = json.loads((output.parent / "fresh/report.json").read_text())
            report["execution_metadata"].update(mode="frozen", actual_model_calls=0)
            if name == mismatch_arm:
                report["result_hash"] = "mismatch"
            for filename in ("exchange.json", "equity.json"):
                save(output / filename, json.loads((output.parent / "fresh" / filename).read_text()))
            save(output / "report.json", report)
            return report
        output.mkdir()
        slots = kwargs["max_decisions"]
        registry = json.loads((output.parent.parent / 'registry.json').read_text())
        call_count = slots if name.startswith("A_") else slots * 2
        if over_cap:
            call_count = slots * 6 + 1
        for index in range(call_count):
            kwargs["before_http"]({"slot": index})
            schema = {"type": "object", "properties": {}}
            if name.startswith("B_"):
                schema["properties"] = {"wait_proposal" if index % 2 == 0 else "selection_token": {}}
            kwargs["model_generator"](system_prompt="fixture", user_prompt="fixture", model=registry['model'],
                reasoning_effort=registry['reasoning'], fast_tier=False, timeout=10, retry_transport=False, response_schema=schema)
        contract = study.contract_for(bundle, kwargs["path"], 1., kwargs["initial_equity"],
            origin="luna", policy_profile=kwargs["policy_profile"], initial_state=kwargs["initial_state"],
            observed_manifest=study.ObservedScenarioData(kwargs["observed_packet"], kwargs["observed_packet_sha256"]).manifest(),
            market_input_mode="observed", allow_input_gaps=False)
        equity, end = kwargs["initial_equity"], bundle["end_ms"]
        exchange = dict(simulation=True, timestamp_ms=end, equity=str(equity), cash=str(equity),
            position="0", average_price="0", executions=[], transactions=[])
        coverage = dict(planned_slots=slots, processed_slots=slots, model_free_drain_status="COMPLETE",
            model_free_drain_end_ms=end, final_economic_time_ms=end, eligible=True)
        report = dict(simulated_only=True, mode="HISTORICAL_LLM_RESEARCH", decisions=slots,
            decision_outcomes={"wait": slots}, contract=contract, contract_hash=study.digest(contract),
            exchange_hash=study.digest(exchange), economic=dict(final_equity=equity, net_change=0,
                fees=0, funding_net=0, open_quantity=0, max_drawdown_pct=0, completed_scenarios=0,
                terminal_mark_price="100.0"),
            readiness=dict(profitability_proven=False, insufficiency_reasons=[]), execution_status="COMPLETE",
            failure_evidence=None, coverage=coverage, model_failures=0, input_preparation_failures=0,
            protection_outcomes={})
        ticks = [dict(kind="economic_tick", at_ms=bundle["start_ms"] + index * 300000,
                      trace=[], decision=None, outcome={"status": "wait"}) for index in range(slots)]
        report["tape_hash"] = study.digest(dict(contract=contract, ticks=ticks))
        if name == fail_arm:
            report.update(execution_status="STOPPED", failure_evidence={"reason": "fixture_failure"}, model_failures=1)
            coverage["eligible"] = False
            kwargs["failure_callback"](report["failure_evidence"])
        report["result_hash"] = study.digest(report)
        report["execution_metadata"] = dict(mode="fresh", actual_model_calls=call_count)
        save(output / "report.json", report)
        save(output / "exchange.json", exchange)
        save(output / "equity.json", [[bundle["start_ms"], equity], [end, equity]])
        header = dict(kind="economic_header", schema=4, contract=contract, contract_hash=study.digest(contract))
        lines = [header] + [dict(tick, sha256=study.digest(tick)) for tick in ticks]
        Path(tape_path).write_text("".join(json.dumps(line) + "\n" for line in lines))
        return report
    return replay


@pytest.fixture(autouse=True)
def endpoint(monkeypatch):
    monkeypatch.setenv("PRISM_BTC_SCENARIO_OAUTH_URL", ENDPOINT)


def test_prepare_freezes_full_input_first_four_slots_and_no_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(study, "GuardedModelCall", lambda *a, **kw: pytest.fail("prepare must not construct model"))
    root, registry = prepared(tmp_path)
    assert registry["max_http_calls"] == 24 and registry["slots"] == 4
    assert registry["run_order"] == list(study.ORDER)
    assert registry["purpose"] == "DEVELOPMENT_SMOKE_NOT_PROFITABILITY_PROOF"
    assert not (root / "call_guard").exists()
    inputs_pins = registry["original_inputs"]
    assert study.sha(inputs_pins["observed_path"]) == inputs_pins["observed_sha256"]
    assert registry["contract_comparison"]["expected_common_contract"]["observed_data"]["provider_manifest"]["slots"] == 8
    assert len(registry["schedule"]) == 4
    full = json.loads(Path(inputs_pins["market_path"]).read_text())
    pilot = json.loads((root / "pilot.json").read_text())
    for field in ("source", "mark", "funding", "warmup"):
        assert full[field] == pilot[field]
    assert pilot["end_ms"] - pilot["start_ms"] == 1200000
    assert pilot["data_hash"] != full["data_hash"]
    assert not registry["next_stage_automatic"]
    assert {"tools/" + name for name in ("btc_calculated_economic_study.py", "btc_research_call_guard.py",
        "btc_research_model_call.py", "compare_btc_scenario_replays.py")} <= set(registry["source_manifest"])


def test_all_four_arms_exactly24_fake_http_calls_and_frozen_zero(tmp_path):
    root, _ = prepared(tmp_path)
    opened = []
    result = study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    assert result["status"] == "COMPLETE_DEVELOPMENT_SMOKE"
    assert result["call_guard"]["actual_http_calls"] == 24 == len(opened)
    assert result["comparison"]["comparison_complete"]
    assert not result["profitability_proven"] and not result["automatic_promotion"]
    assert not result["next_stage_automatic"]
    events = [json.loads(line) for line in (root / "model_events.jsonl").read_text().splitlines()]
    assert sum(event["kind"] == "HTTP_REQUEST" for event in events) == 24
    assert all("headers" not in event for event in events)
    with pytest.raises(FileExistsError):
        study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    assert len(opened) == 24


@pytest.mark.parametrize("failure", ["last_slot", "parity", "cap"])
def test_first_failure_latches_all_arms_and_keeps_original_reports(tmp_path, failure):
    root, _ = prepared(tmp_path)
    opened = []
    replay = fake_replay(fail_arm="A_OHLC" if failure == "last_slot" else None,
                         mismatch_arm="A_OHLC" if failure == "parity" else None, over_cap=failure == "cap")
    result = study.run(root, replay=replay, model_factory=fake_factory(opened))
    assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
    assert len(opened) <= 24
    assert not (root / "B_OHLC").exists()
    assert not result["comparison"]["comparison_complete"]
    if failure != "cap":
        assert (root / "A_OHLC/fresh/report.json").exists()
    if failure == "last_slot":
        assert (root / "A_OHLC/frozen/report.json").exists()
        assert result["per_arm_frozen_parity"]["A_OHLC"] == "VERIFIED_FAILED_ZERO_MODEL_CALLS"
    with pytest.raises(FileExistsError):
        study.run(root, replay=replay, model_factory=fake_factory(opened))


@pytest.mark.parametrize("mutation", ["market", "observed", "pilot", "registry", "source"])
def test_changed_pins_stop_before_any_model_dispatch(tmp_path, monkeypatch, mutation):
    root, registry = prepared(tmp_path)
    if mutation == "source":
        original = study.source_manifest()
        monkeypatch.setattr(study, "source_manifest", lambda: dict(original, unknown="tampered"))
    else:
        path = {"market": Path(registry["original_inputs"]["market_path"]),
                "observed": Path(registry["original_inputs"]["observed_path"]),
                "pilot": root / "pilot.json", "registry": root / "registry.json"}[mutation]
        path.write_text(path.read_text() + " ")
    opened = []
    result = study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
    assert not opened


def test_gap_in_original_fullpacket_does_not_get_hidden_by_four_slot_slice(tmp_path):
    market, observed, _ = data_files(tmp_path)
    value = json.loads(observed.read_text())
    value["rows"][-1] = dict(slot=value["rows"][-1]["slot"], status="MISSING", reason="NO_RETAINED_INPUT")
    value["rows_sha256"] = study.digest(value["rows"])
    save(observed, value)
    with pytest.raises(ValueError, match="observed_input_gap"):
        study.prepare(tmp_path / "study", market, observed, study.sha(observed), "fixture-source")
    assert not (tmp_path / "study").exists()


def test_oracle_uses_immutable_prior_minute_mark_instead_of_future_open_or_report(tmp_path):
    root, registry = prepared(tmp_path)
    bundle, market = study.load_bundle(root / "pilot.json")
    at = bundle["start_ms"] + 300000
    bundle["funding"] = [dict(timestamp=at, rate=.0001)]
    previous = market.mark.index.view("i8") // 1_000_000 == at - 60000
    future = market.mark.index.view("i8") // 1_000_000 == at
    market.mark.loc[previous, "close"] = 97.
    market.mark.loc[future, "open"] = 999.
    assert study._oracle(market, bundle)["funding_events"] == [dict(timestamp_ms=at, rate="0.0001", mark_price="97.0")]
    assert registry["economic_oracle_inputs"]["terminal_mark_price"] == "100.0"


def test_cli_registry_pin_is_file_sha_and_matches_call_guard(tmp_path, monkeypatch, capsys):
    market, observed, pin = data_files(tmp_path)
    root = tmp_path / "study"
    monkeypatch.setattr(sys, "argv", ["study", "prepare", "--root", str(root), "--market", str(market),
        "--observed", str(observed), "--observed-sha256", pin, "--source-reference", "fixture-source"])
    study.main()
    output = json.loads(capsys.readouterr().out)
    file_sha = study.sha(root / "registry.json")
    assert output["registry_sha256"] == file_sha
    assert json.loads((root / "registry.sha256.json").read_text())["sha256"] == file_sha
    guard = study.CallGuard(root / "guard_check", file_sha, 24)
    assert guard.snapshot()["registry_sha256"] == file_sha
    assert output["registry_canonical_sha256"] == study.digest(json.loads((root / "registry.json").read_text()))
    assert guard.snapshot()["actual_http_calls"] == 0


def test_corrupted_registry_after_fresh_preserves_stopped_result_with_unavailable_comparison(tmp_path):
    root, _ = prepared(tmp_path)
    normal = fake_replay()
    def replay(*args, **kwargs):
        report = normal(*args, **kwargs)
        if kwargs["mode"] == "fresh":
            (root / "registry.json").write_text("{")
        return report
    opened = []
    result = study.run(root, replay=replay, model_factory=fake_factory(opened))
    assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
    assert result["comparison"] is None and result["comparison_status"] == "UNAVAILABLE"
    assert result["coordinator_error"]["reason"] == "registry_changed"
    assert (root / "study_result.json").exists()
    assert (root / "A_OHLC/fresh/report.json").exists()
    assert not (root / "A_OHLC/frozen").exists() and not (root / "B_OHLC").exists()
    assert len(opened) == 4


def test_comparator_exception_cannot_erase_stopped_summary(tmp_path, monkeypatch):
    root, _ = prepared(tmp_path)
    def fail_compare(*args):
        raise ValueError("comparison_failure")
    monkeypatch.setattr(study, "compare_calculated", fail_compare)
    result = study.run(root, replay=fake_replay(fail_arm="A_OHLC"), model_factory=fake_factory([]))
    assert result["status"] == "STOPPED" and result["comparison_status"] == "UNAVAILABLE"
    assert result["comparison"] is None
    assert result["coordinator_error"]["reason"] == "fresh_arm_stopped"
    assert (root / "study_result.json").exists()


def test_transport_failure_stops_other_arms_preserves_calls_and_omits_unsafe_error_text(tmp_path):
    root, _ = prepared(tmp_path)
    opened = []
    normal_factory = fake_factory(opened)
    def factory(guard, arm, record):
        if arm != "B_OHLC":
            return normal_factory(guard, arm, record)
        def opener(*args, **kwargs):
            opened.append("transport_failure")
            raise OSError("authorization=DO_NOT_LOG_THIS")
        return study.GuardedModelCall(guard, arm, record, open_url=opener, monotonic=lambda: 0.)
    result = study.run(root, replay=fake_replay(), model_factory=factory)
    assert result["status"] == "STOPPED" and result["call_guard"]["actual_http_calls"] == 5
    assert result["per_arm_actual_http_calls"] == {"A_OHLC": 4, "B_OHLC": 1}
    assert result["coordinator_error"] == {"error_type": "ValueError", "reason": "scenario_oauth_failed"}
    assert not (root / "B_OLHC").exists()
    assert "DO_NOT_LOG_THIS" not in (root / "model_events.jsonl").read_text()
    assert "DO_NOT_LOG_THIS" not in (root / "study_result.json").read_text()


def test_durable_record_failure_is_stopped_and_summary_is_best_effort(tmp_path, monkeypatch):
    root, _ = prepared(tmp_path)
    normal_fsync = study.os.fsync
    def fsync(fd):
        path = root / "model_events.jsonl"
        if path.exists() and study.os.fstat(fd).st_ino == path.stat().st_ino:
            raise OSError("fixture_io_failure")
        return normal_fsync(fd)
    monkeypatch.setattr(study.os, "fsync", fsync)
    result = study.run(root, replay=fake_replay(fail_arm="A_OHLC"), model_factory=fake_factory([]))
    assert result["status"] == "STOPPED"
    assert result["diagnostic_record_status"] == "FAILED"
    assert result["call_guard"]["actual_http_calls"] == 0
    assert (root / "study_result.json").exists()


@pytest.mark.parametrize("stage", [1, 2])
def test_real_runner_coordinator_zero_network_wait_http_artifacts_and_strict_comparison(tmp_path, stage):
    from tests.test_scenario_calculated_replay import wire
    root, _ = prepared(tmp_path) if stage == 1 else prepare_stage_two(tmp_path, stage_two_fixture(tmp_path))
    wall, opened = [0.], []
    def opener(request, timeout):
        payload = json.loads(request.data)
        frozen_input = json.loads(payload["input"][0]["content"])
        ctx = frozen_input["contract_context"]
        properties = payload["text"]["format"]["schema"]["properties"]
        assert "selection_token" not in properties  # no-candidate B uses one draft
        wait = wire(ctx, "WAIT")
        response = (dict(wait_proposal=wait, candidates=[],
                         no_candidate_reason="관측 fixture, 거래 없음") if "wait_proposal" in properties else wait)
        opened.append((request, timeout))
        wall[0] += 1.
        return Response(json.dumps(dict(status="completed", model=payload['model'], output=[
            dict(type="message", role="assistant", content=[dict(type="output_text", text=json.dumps(response))])])).encode())
    def factory(guard, arm, record):
        return study.GuardedModelCall(guard, arm, record, open_url=opener, monotonic=lambda: wall[0])
    def replay(*args, **kwargs):
        if kwargs["mode"] == "fresh":
            kwargs["clock_monotonic"] = lambda: wall[0]
        return study.run_replay(*args, **kwargs)
    result = study.run(root, replay=replay, model_factory=factory)
    assert result["status"] == "COMPLETE_DEVELOPMENT_SMOKE", result
    assert result["comparison"]["comparison_complete"]
    expected_calls = 16 if stage == 1 else 96
    assert result["call_guard"]["actual_http_calls"] == len(opened) == expected_calls
    assert sum(result["per_arm_actual_http_calls"].values()) == expected_calls
    assert sum(result["per_arm_model_generator_dispatches"].values()) == expected_calls
    assert not result["profitability_proven"]
    for name in study.ORDER:
        fresh = json.loads((root / name / "fresh/report.json").read_text())
        frozen = json.loads((root / name / "frozen/report.json").read_text())
        assert fresh["result_hash"] == frozen["result_hash"]
        assert fresh["exchange_hash"] == frozen["exchange_hash"]
        assert fresh["final_state_hash"] == frozen["final_state_hash"]
        assert fresh["economic_clock_trace_hash"] == frozen["economic_clock_trace_hash"]
        assert fresh["runtime_event_hash"] == frozen["runtime_event_hash"]
        assert fresh["execution_metadata"]["actual_model_calls"] == (4 if stage == 1 else 24)
        assert frozen["execution_metadata"]["actual_model_calls"] == 0
        assert fresh["economic"]["open_quantity"] == 0


def test_real_runner_pre_http_durable_delay_counts_dispatch_but_zero_http_and_preserves_failed_parity(tmp_path):
    root, _ = prepared(tmp_path)
    wall, opened = [0.], []
    def factory(guard, arm, record):
        def durable(event):
            record(event)
            if event["kind"] == "HTTP_REQUEST":
                wall[0] += 76.
        def opener(*args, **kwargs):
            opened.append(True)
            pytest.fail("pre-HTTP durable deadline forbids opener entry")
        return study.GuardedModelCall(guard, arm, durable, open_url=opener, monotonic=lambda: wall[0])
    def replay(*args, **kwargs):
        if kwargs["mode"] == "fresh":
            kwargs["clock_monotonic"] = lambda: wall[0]
        return study.run_replay(*args, **kwargs)
    result = study.run(root, replay=replay, model_factory=factory)
    assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
    assert result["call_guard"]["reserved_attempts"] == 1
    assert result["call_guard"]["actual_http_calls"] == 0
    assert not opened and not (root / "B_OHLC").exists()
    fresh = json.loads((root / "A_OHLC/fresh/report.json").read_text())
    frozen = json.loads((root / "A_OHLC/frozen/report.json").read_text())
    assert fresh["execution_metadata"]["model_generator_dispatches"] == 1
    assert fresh["execution_metadata"]["actual_model_calls"] == 0
    assert frozen["execution_metadata"]["actual_model_calls"] == 0
    assert fresh["result_hash"] == frozen["result_hash"]
    assert result["per_arm_frozen_parity"]["A_OHLC"] == "VERIFIED_FAILED_ZERO_MODEL_CALLS"
    assert not result["comparison"]["comparison_complete"]


def stage_two_fixture(tmp_path, slots=28):
    market, observed, pin = data_files(tmp_path, duration_ms=slots * 300000)
    previous = tmp_path / "stage_one"
    study.prepare(previous, market, observed, pin, "fixture-stage-one")
    result = study.run(previous, replay=fake_replay(), model_factory=fake_factory([]))
    assert result["status"] == "COMPLETE_DEVELOPMENT_SMOKE"
    external_pin = study.sha(previous / "registry.json")
    return market, observed, pin, previous, external_pin


def prepare_stage_two(tmp_path, fixture):
    market, observed, pin, previous, external_pin = fixture
    root = tmp_path / "stage_two"
    registry = study.prepare(root, market, observed, pin, "fixture-stage-two", stage=2,
        predecessor_root=previous, predecessor_registry_sha256=external_pin)
    return root, registry


def test_predecessor_wrong_response_model_still_rejected_with_valid_response_hash(tmp_path):
    import hashlib
    _, _, _, previous, pin = stage_two_fixture(tmp_path)
    path = previous / 'model_events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()]
    event = next(row for row in events if row['kind'] == 'HTTP_RESPONSE')
    response = json.loads(event['raw_response'])
    response['model'] = 'wrong-fixture-model'
    event['raw_response'] = json.dumps(response)
    event['raw_sha256'] = hashlib.sha256(event['raw_response'].encode()).hexdigest()
    path.write_text(''.join(json.dumps(row)+'\n' for row in events))
    with pytest.raises(ValueError, match='predecessor_wire_model_result_mismatch'):
        study.predecessor_manifest(previous, pin)


def test_stage_two_fixed_next24_pins_predecessor_read_only_and_no_prepare_calls(tmp_path):
    fixture = stage_two_fixture(tmp_path)
    previous = fixture[3]
    before = {str(p.relative_to(previous)): study.sha(p) for p in previous.rglob("*") if p.is_file()}
    root, registry = prepare_stage_two(tmp_path, fixture)
    after = {str(p.relative_to(previous)): study.sha(p) for p in previous.rglob("*") if p.is_file()}
    assert before == after == registry["predecessor"]["artifacts"]
    assert not (root / "call_guard").exists() and not (root / "model_events.jsonl").exists()
    assert registry["slots"] == 24 and registry["max_http_calls"] == 144
    prior = json.loads((previous / "registry.json").read_text())
    assert registry["schedule"][0]["slot"] == prior["schedule"][-1]["slot"] + 1
    assert registry["schedule"][-1]["slot"] - registry["schedule"][0]["slot"] == 23
    assert registry["original_inputs"] == prior["original_inputs"]
    assert registry["initial_state"] == "recovery-stage0" and registry["initial_equity"] == 10000
    assert registry["contract_comparison"]["expected_arm_contracts"] == prior["contract_comparison"]["expected_arm_contracts"]
    assert registry["inference_limits"]["development_period_count"] == 1
    assert registry["inference_limits"]["historical_not_untouched"] is True
    assert registry["inference_limits"]["independent_oos_samples"] == registry["inference_limits"]["forward_samples"] == 0
    assert registry["inference_limits"]["power"] == "UNCOMPUTED"
    assert len(registry["trial_ledger"]) == 2 and not registry["next_stage_automatic"]


@pytest.mark.parametrize("fault", ["missing_root", "missing_external_pin", "wrong_external_pin", "not_completed",
    "report", "exchange", "curve", "tape_missing", "ledger", "comparator_pin", "wrong_inputs", "core_changed",
    "tape_truncated", "tape_corrupted", "tape_missing_tick", "response_missing", "result_missing", "response_hash", "result_text"])
def test_stage_two_rejects_unverified_predecessor_before_writing_or_calls(tmp_path, fault, monkeypatch):
    market, observed, pin, previous, external_pin = stage_two_fixture(tmp_path)
    root = tmp_path / "stage_two"
    if fault == "missing_root":
        previous = None
    elif fault == "missing_external_pin":
        external_pin = None
    elif fault == "wrong_external_pin":
        external_pin = "f" * 64
    elif fault == "not_completed":
        value = json.loads((previous / "study_result.json").read_text())
        value["status"] = "STOPPED"
        save(previous / "study_result.json", value)
    elif fault in {"report", "exchange", "curve"}:
        filename = {"report": "report.json", "exchange": "exchange.json", "curve": "equity.json"}[fault]
        save(previous / "A_OHLC/fresh" / filename, None)
    elif fault == "tape_missing":
        (previous / "B_OHLC/tape.jsonl").unlink()
    elif fault in {"tape_truncated", "tape_corrupted", "tape_missing_tick"}:
        path = previous / "B_OHLC/tape.jsonl"
        lines = path.read_text().splitlines()
        if fault == "tape_truncated":
            lines[-1] = lines[-1][:-5]
        elif fault == "tape_missing_tick":
            lines.pop()
        else:
            value = json.loads(lines[-1])
            value["at_ms"] += 1
            lines[-1] = json.dumps(value)
        path.write_text("\n".join(lines) + "\n")
    elif fault in {"response_missing", "result_missing", "response_hash", "result_text"}:
        path = previous / "model_events.jsonl"
        events = [json.loads(line) for line in path.read_text().splitlines()]
        kind = "HTTP_RESPONSE" if fault.startswith("response") else "MODEL_RESULT"
        index = next(i for i, event in enumerate(events) if event["kind"] == kind)
        if fault.endswith("missing"):
            events.pop(index)
        elif fault == "response_hash":
            events[index]["raw_sha256"] = "f" * 64
        else:
            events[index]["text"] = "tampered"
        path.write_text("".join(json.dumps(event) + "\n" for event in events))
    elif fault == "ledger":
        path = previous / "call_guard/attempts.jsonl"
        path.write_text(path.read_text().splitlines()[0] + "\n")
    elif fault == "comparator_pin":
        value = json.loads((previous / "registry.json").read_text())
        value["source_manifest"]["tools/compare_btc_scenario_replays.py"] = "f" * 64
        save(previous / "registry.json", value)
        external_pin = study.sha(previous / "registry.json")
    elif fault == "wrong_inputs":
        path = observed
        path.write_text(path.read_text() + " ")
        pin = study.sha(path)
    elif fault == "core_changed":
        original = study.contract_for
        def contract(*args, **kwargs):
            result = original(*args, **kwargs)
            result["source_hashes"] = dict(result["source_hashes"], unknown="changed")
            return result
        monkeypatch.setattr(study, "contract_for", contract)
    with pytest.raises((ValueError, TypeError, KeyError, RuntimeError)):
        study.prepare(root, market, observed, pin, "fixture-stage-two", stage=2,
            predecessor_root=previous, predecessor_registry_sha256=external_pin)
    assert not root.exists()


def test_stage_two_exactly144_fake_opener_entries_and_no_automatic_third_stage(tmp_path):
    root, _ = prepare_stage_two(tmp_path, stage_two_fixture(tmp_path))
    opened = []
    result = study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    assert result["status"] == "COMPLETE_DEVELOPMENT_SMOKE", result
    assert result["call_guard"]["actual_http_calls"] == len(opened) == 144
    assert result["comparison"]["comparison_complete"]
    assert not result["automatic_promotion"] and not result["profitability_proven"]
    assert not result["next_stage_automatic"]


@pytest.mark.parametrize("fault", ["first_error", "cap", "changed_predecessor", "arbitrary_slots", "arbitrary_cap"])
def test_stage_two_fail_stop_and_fixed_caps_no_next_arm(tmp_path, fault):
    root, registry = prepare_stage_two(tmp_path, stage_two_fixture(tmp_path))
    if fault == "changed_predecessor":
        path = Path(registry["predecessor"]["root"]) / "model_events.jsonl"
        path.write_text(path.read_text() + " ")
    elif fault in {"arbitrary_slots", "arbitrary_cap"}:
        registry["slots" if fault == "arbitrary_slots" else "max_http_calls"] = 25 if fault == "arbitrary_slots" else 145
        save(root / "registry.json", registry)
    opened = []
    if fault in {"arbitrary_slots", "arbitrary_cap"}:
        with pytest.raises(ValueError):
            study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    else:
        result = study.run(root, replay=fake_replay(fail_arm="A_OHLC" if fault == "first_error" else None,
                                                 over_cap=fault == "cap"), model_factory=fake_factory(opened))
        assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
        assert not (root / "B_OHLC").exists()
        assert len(opened) <= 144
    if fault in {"changed_predecessor", "arbitrary_slots", "arbitrary_cap"}:
        assert not opened


@pytest.mark.parametrize("wrong_offset", [0, 5])
def test_stage_two_self_consistent_wrong_offset_rejected_before_dispatch(tmp_path, wrong_offset):
    fixture = stage_two_fixture(tmp_path, slots=29)
    root, registry = prepare_stage_two(tmp_path, fixture)
    pilot = json.loads((root / "pilot.json").read_text())
    original = json.loads(fixture[0].read_text())
    start = original["start_ms"] + wrong_offset * 300000
    pilot.update(start_ms=start, end_ms=start + 24 * 300000)
    pilot["data_hash"] = study.digest({k: v for k, v in pilot.items() if k not in {"data_hash", "coverage", "provenance"}})
    observed = study.ObservedScenarioData(fixture[1], fixture[2])
    schedule = observed.schedule(pilot["start_ms"], pilot["end_ms"])
    common = registry["contract_comparison"]["expected_common_contract"]
    common.update(start_ms=pilot["start_ms"], end_ms=pilot["end_ms"], data_hash=pilot["data_hash"])
    registry.update(schedule=schedule, pilot_data_hash=pilot["data_hash"], expected_data_hash=pilot["data_hash"],
                    start=study.datetime.fromtimestamp(start / 1000, study.timezone.utc).isoformat().replace("+00:00", "Z"),
                    end=study.datetime.fromtimestamp((start + 24 * 300000) / 1000, study.timezone.utc).isoformat().replace("+00:00", "Z"))
    save(root / "pilot.json", pilot)
    registry["pilot_file_sha256"] = study.sha(root / "pilot.json")
    save(root / "registry.json", registry)
    save(root / "registry.sha256.json", {"sha256": study.sha(root / "registry.json")})
    opened = []
    result = study.run(root, replay=fake_replay(), model_factory=fake_factory(opened))
    assert result["status"] == "STOPPED" and result["call_guard"]["stopped"]
    assert result["coordinator_error"]["reason"] == "fixed_stage_window_mismatch"
    assert not opened


def test_predecessor_rejects_runtime_readiness_tampering(tmp_path):
    _, _, _, previous, pin = stage_two_fixture(tmp_path)
    path = previous / 'model_events.jsonl'
    events = [json.loads(line) for line in path.read_text().splitlines()]
    request = next(event for event in events if event['kind'] == 'HTTP_REQUEST')
    assert request['runtime_readiness']['status'] == 'PASSED'
    request['runtime_readiness']['phase'] = 'TAMPERED'
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))
    with pytest.raises(ValueError, match='predecessor_runtime_readiness_mismatch'):
        study.predecessor_manifest(previous, pin)
