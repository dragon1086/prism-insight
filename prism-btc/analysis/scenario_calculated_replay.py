"""Native full-tick economic time and exact tapes, isolated from live workers.

The guard's ordinary replay_review remains pure. Only this economic runner
replays advances and durable review events, once, before the guard is entered.
"""
from copy import deepcopy
import json
import math
from pathlib import Path
import time

from backtest.scenario_tape import TapeMismatch, digest

ALLOCATION_PROFILES = {
    "allocation-single-economic-v1": "allocation-single",
    "allocation-calculated-economic-v1": "allocation-calculated",
}
REJECTION_PROFILES = {
    "deployed-main-v3": "economic-reject-calculated-v1",
    "economic-reject-single-v1": "economic-reject-single-v1",
    "economic-reject-calculated-v1": "economic-reject-calculated-v1",
    "economic-reject-goal-v2": "economic-reject-calculated-v1",
}
RESEARCH_PROFILES = {**ALLOCATION_PROFILES, **REJECTION_PROFILES}
ECONOMIC_PROFILES = {"single-pass-economic-v4", "calculated-economic-v4", *RESEARCH_PROFILES}
REVIEW_PROFILES = {"calculated-economic-v4", *RESEARCH_PROFILES}


class EconomicTape:
    def __init__(self, path, mode, contract):
        self.path, self.mode, self.contract = Path(path), mode, deepcopy(contract)
        self.ticks, self.rows, self.used = [], [], set()
        self.stream = None
        header = dict(kind="economic_header", schema=4, contract=contract,
                      contract_hash=digest(contract))
        if mode == "record":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.stream = self.path.open("x", encoding="utf-8")
            self._write(header)
        else:
            lines = self.path.read_text(encoding="utf-8").splitlines()
            if not lines or json.loads(lines[0]) != header:
                raise TapeMismatch("economic_tape_contract")
            for line in lines[1:]:
                row = json.loads(line)
                proof = row.pop("sha256", None)
                if proof != digest(row) or row.get("kind") != "economic_tick":
                    raise TapeMismatch("economic_tape_corrupted")
                if any(tick["at_ms"] == row["at_ms"] for tick in self.ticks):
                    raise TapeMismatch("economic_duplicate_tick")
                self.ticks.append(row)
            self.rows = [row["decision"] for row in self.ticks if row.get("decision")]

    def _write(self, row):
        # Nested review inputs retain order used by their lossless projection.
        self.stream.write(json.dumps(row, allow_nan=False) + "\n")
        self.stream.flush()

    def begin(self, at):
        if self.mode == "record":
            self.current = dict(kind="economic_tick", at_ms=at, trace=[], decision=None)
        else:
            index = len(self.used)
            if index >= len(self.ticks) or self.ticks[index]["at_ms"] != at:
                raise TapeMismatch("economic_missing_tick")
            self.current = deepcopy(self.ticks[index])
            self.used.add(index)

    def finish(self, outcome):
        if self.mode == "record":
            self.current["outcome"] = deepcopy(outcome)
            self.ticks.append(deepcopy(self.current))
            if self.current["decision"]:
                self.rows.append(deepcopy(self.current["decision"]))
            self._write(dict(self.current, sha256=digest(self.current)))
        elif self.current["outcome"] != outcome:
            raise TapeMismatch("economic_runtime_outcome")

    def transcript_hash(self):
        return digest(dict(contract=self.contract, ticks=self.ticks))

    def assert_exhausted(self):
        if self.mode == "frozen" and len(self.used) != len(self.ticks):
            raise TapeMismatch("economic_unused_ticks")

    def close(self):
        if self.stream:
            self.stream.close()


class EconomicClock:
    def __init__(self, driver, tape, *, monotonic=time.monotonic):
        self.driver, self.tape, self.monotonic = driver, tape, monotonic
        self.active = False
        self.fatal = None

    def begin(self):
        self.anchor_ms = self.driver.session.ts_ms
        self.wall_start = self.monotonic()
        self.index = 0
        self.active = True

    def sync(self, label="clock"):
        if not self.active:
            return self.driver.session.clock()
        try:
            trace = self.tape.current["trace"]
            if self.tape.mode == "frozen":
                if self.index >= len(trace) or trace[self.index]["label"] != label:
                    raise TapeMismatch("economic_clock_operation")
                target = trace[self.index]["at_ms"]
                self.index += 1
            else:
                elapsed = self.monotonic() - self.wall_start
                if not math.isfinite(elapsed) or elapsed < 0:
                    raise TapeMismatch("economic_invalid_elapsed")
                target = self.anchor_ms + int(elapsed * 1000)
                trace.append(dict(label=label, at_ms=target))
            if type(target) is not int or target < self.driver.session.ts_ms:
                raise TapeMismatch("economic_clock_reversal")
            if target >= self.driver.end or target >= self.tape.current["at_ms"] + 300000:
                raise TapeMismatch("economic_clock_coverage")
            self.driver.advance_to(target)
            return target / 1000
        except Exception as exc:
            self.fatal = str(exc)
            raise

    def __call__(self):
        return self.sync()

    def finish(self):
        self.active = False
        if self.fatal is None and sum(operation["label"]=="http_dispatch" for operation in self.tape.current["trace"]) != len(self.tape.current.get("dispatches",[])):
            raise TapeMismatch("economic_dispatch_count")
        if self.tape.mode == "frozen" and self.index != len(self.tape.current["trace"]):
            raise TapeMismatch("economic_unused_clock_operations")


class EconomicDecisions:
    def __init__(self, tape, driver, mode, clock, *, policy=None, model_generator=None,
                 before_http=None):
        self.tape, self.driver, self.mode, self.clock = tape, driver, mode, clock
        self.policy, self.model_generator = policy, model_generator
        self.before_http = before_http
        self.fatal = None
        self.calls = self.model_calls = self.failures = self.input_failures = 0
        self.fixture_generator_calls = 0
        self.initial_http_calls = getattr(model_generator,"actual_http_calls",None)

    def _generate(self, **request):
        if self.mode=="fixture" and self.model_generator is None:
            raise ValueError("fixture_oauth_forbidden")
        request["retry_transport"] = False
        prepared_hash = digest(request)
        try:
            self.clock.sync("http_prepare")
            if self.before_http is not None:
                self.before_http(deepcopy(dict(profile=self.tape.contract["policy_profile"],
                    slot_ms=self.tape.current["at_ms"], request=request)))
            now = self.clock.sync("http_dispatch")
            request["timeout"] = min(request["timeout"], 75 - (now-self.decision_started))
            dispatch = dict(request=deepcopy(request),request_sha256=digest(request),
                prepared_request_sha256=prepared_hash,budget_started_at=self.decision_started,
                at_ms=self.driver.session.ts_ms,authorized=request["timeout"]>0)
            self.tape.current.setdefault("dispatches",[]).append(dispatch)
            if not dispatch["authorized"]:
                raise ValueError("economic_dispatch_deadline")
            if self.mode=="fixture":
                self.fixture_generator_calls += 1
            else:
                self.model_calls += 1
            if self.model_generator is None:
                from live.scenario_oauth import generate_scenario
                return generate_scenario(**request)
            return self.model_generator(**request)
        finally:
            self.clock.sync("generate_return")

    def _complete(self, label):
        if label=="generate_return":
            trace=self.tape.current["trace"]
            dispatched = False
            for operation in ("http_prepare","http_dispatch"):
                if self.clock.index<len(trace) and trace[self.clock.index]["label"]==operation:
                    self.clock.sync(operation)
                    dispatched |= operation=="http_dispatch"
            if dispatched:
                row=self.tape.current["decision"]
                requests=([operation["request"] for operation in row["envelope"]["operations"]
                           if operation["kind"]=="generate"] if "envelope" in row
                          else [response["request"] for response in row["responses"]])
                expected=deepcopy(requests[self.dispatch_index])
                expected["retry_transport"]=False
                prepared_hash=digest(expected)
                expected["timeout"]=min(expected["timeout"],75-(self.driver.session.clock()-self.decision_started))
                dispatch=dict(request=expected,request_sha256=digest(expected),
                    prepared_request_sha256=prepared_hash,budget_started_at=self.decision_started,
                    at_ms=self.driver.session.ts_ms,authorized=expected["timeout"]>0)
                if self.tape.current.get("dispatches",[])[self.dispatch_index]!=dispatch:
                    raise TapeMismatch("economic_dispatch_mismatch")
                self.dispatch_index+=1
        return self.clock.sync(label)

    def _inputs(self, snapshot, context):
        inputs = deepcopy(dict(snapshot=snapshot, context=context))
        self.decision_started = context["now"]
        self.dispatch_index = 0
        self.record_index = 0
        row = self.tape.current["decision"]
        if self.mode == "frozen":
            if row is None or row["inputs"] != inputs or row["inputs_sha256"] != digest(inputs):
                self.fatal = "economic_exact_input_mismatch"
                raise TapeMismatch(self.fatal)
            return row
        row = dict(inputs=inputs, inputs_sha256=digest(inputs))
        self.tape.current["decision"] = row
        self.calls += 1
        return row

    def calculated(self, snapshot, context, *, record):
        from analysis.scenario_review_tape import record_review, replay_review
        from live.scenario_calculated_review import research_review
        from live.scenario_preview import response_contract
        from live.scenario_provenance import review_policy_binding
        row = self._inputs(snapshot, context)
        research_mode = RESEARCH_PROFILES.get(self.tape.contract["policy_profile"], "legacy")
        if research_mode != "legacy":
            from functools import partial
            research_review = partial(research_review, research_mode=research_mode)
        contract = response_contract(context)
        bindings = (review_policy_binding() if research_mode == "legacy"
                    else review_policy_binding(research_mode=research_mode))
        def durable(event):
            effect=dict(event_sha256=digest(event),durable_applied=False)
            row.setdefault("record_effects",[]).append(effect)
            try:
                record(event)
                effect["durable_applied"]=True
            finally:
                self.clock.sync("record_return")
        if self.mode == "frozen":
            envelope = deepcopy(row["envelope"])
            def replay_durable(event):
                effects=row.get("record_effects",[])
                if self.record_index>=len(effects) or effects[self.record_index]["event_sha256"]!=digest(event):
                    raise TapeMismatch("economic_record_effect")
                effect=effects[self.record_index]
                self.record_index+=1
                if type(effect["durable_applied"]) is not bool:
                    raise TapeMismatch("economic_record_effect")
                if effect["durable_applied"]:
                    record(event)
            replay_review(envelope, research_review, snapshot, context, contract,
                bindings=bindings, economic_clock=self.clock, economic_record=replay_durable,
                operation_complete=self._complete)
            if self.record_index!=len(row.get("record_effects",[])):
                raise TapeMismatch("economic_unused_record_effects")
        else:
            envelope = record_review(research_review, snapshot, context, contract,
                generate=self._generate, record=durable, clock=self.clock, bindings=bindings)
            row["envelope"] = deepcopy(envelope)
        if envelope["outcome"]["status"] != "COMPLETE":
            self.failures += 1
            self.fatal = envelope["outcome"]["error"]["message"]
        return envelope

    def __call__(self, snapshot, context):
        from live.scenario_llm import propose
        from live.scenario_preview import response_contract
        row = self._inputs(snapshot, context)
        if self.mode == "frozen":
            if "policy_proposal" in row:
                from live.scenario_recovery import record_model_request, record_model_wire
                record_model_request(**row["policy_request"])
                record_model_wire(json.dumps(row["policy_proposal"],sort_keys=True),model="fixture")
                self.clock.sync("policy_return")
                return deepcopy(row["policy_proposal"])
            # Single-pass economic replay captures the same generator boundary;
            # audit events are recreated by propose(), not independently injected.
            raw = iter(row["responses"])
            def generate(**request):
                request["retry_transport"] = False
                recorded = next(raw)
                if recorded["request"] != request:
                    raise TapeMismatch("economic_request_mismatch")
                self._complete("generate_return")
                if "error" in recorded:
                    raise RuntimeError(recorded["error"])
                from types import SimpleNamespace
                return SimpleNamespace(text=recorded["text"])
        else:
            row["responses"] = []
            def generate(**request):
                request["retry_transport"] = False
                entry = dict(request=deepcopy(request))
                row["responses"].append(entry)
                try:
                    response = self._generate(**request)
                    entry["text"] = response.text
                    return response
                except Exception as exc:
                    entry["error"] = type(exc).__name__
                    raise
        try:
            if self.policy:
                from live.scenario_recovery import record_model_request, record_model_wire
                row["policy_request"] = dict(system_prompt="SYNTHETIC_FIXTURE_POLICY_NOT_LLM",
                    user_prompt=json.dumps(dict(snapshot=snapshot,context=context),sort_keys=True),
                    response_schema={},model="fixture",effort=None,fast=None)
                record_model_request(**row["policy_request"])
                proposal = self.policy(snapshot, context)
                row["policy_proposal"] = deepcopy(proposal)
                record_model_wire(json.dumps(proposal,sort_keys=True),model="fixture")
                self.clock.sync("policy_return")
            else:
                proposal = propose(snapshot, context, response_contract(context),
                                   generate=generate, clock=self.clock)
        except Exception as exc:
            self.failures += 1
            self.fatal = str(exc)
            row.setdefault("error", dict(type=type(exc).__name__, message=str(exc)))
            raise
        return proposal
