"""Exact, order-free two-pass review transcripts, including every clock read."""
from copy import deepcopy
import json
import math
from types import SimpleNamespace

from backtest.scenario_tape import TapeMismatch, digest

VERSION = 1
MAX_OPERATIONS = 128
MAX_BYTES = 8_000_000


def _error(exc):
    return dict(type=type(exc).__name__, message=str(exc))


def _outcome(run):
    try:
        return dict(status="COMPLETE", result=deepcopy(run()))
    except Exception as exc:
        return dict(status="FAILED", error=_error(exc))


def _bound(value):
    if len(json.dumps(value, allow_nan=False).encode()) > MAX_BYTES:
        raise TapeMismatch("review_tape_size")


def _inputs(snapshot, context, contract, bindings):
    return deepcopy(dict(snapshot=snapshot, context=context, contract=contract,
                         bindings=bindings or {}))


def record_review(review, snapshot, context, contract, *, generate, record, clock,
                  bindings=None):
    """Return a sealed COMPLETE/FAILED envelope; external record must be durable.

    No clocks are synthesized: record latency changes subsequent captured clock
    values, thus the actual post-record request timeout is replayed exactly.
    A crashed process has no sealed envelope and cannot be replayed as complete.
    """
    inputs = _inputs(snapshot, context, contract, bindings)
    _bound(inputs)
    operations, calls, previous_time, retained_bytes = [], 0, None, 0

    def append(operation):
        nonlocal retained_bytes
        if len(operations) >= MAX_OPERATIONS:
            raise TapeMismatch("review_tape_operation_limit")
        _bound(operation)
        retained_bytes += len(json.dumps(operation, allow_nan=False).encode())
        if retained_bytes > MAX_BYTES:
            raise TapeMismatch("review_tape_size")
        operations.append(deepcopy(operation))

    def recorded_clock():
        nonlocal previous_time
        value = clock()
        if type(value) not in (int, float) or not math.isfinite(value) or (previous_time is not None and value < previous_time):
            raise TapeMismatch("review_tape_invalid_clock")
        previous_time = value
        append(dict(kind="clock", value=value))
        return value

    def recorded_event(event):
        operation = dict(kind="record", event=deepcopy(event))
        try:
            record(deepcopy(event))
        except Exception as exc:
            operation["error"] = _error(exc)
            raise
        finally:
            append(operation)

    def recorded_generate(**request):
        nonlocal calls
        if calls >= 2:
            raise TapeMismatch("review_tape_call_limit")
        calls += 1
        operation = dict(kind="generate", request=deepcopy(request))
        try:
            response = generate(**deepcopy(request))
            if not isinstance(response.text, str):
                raise ValueError("review_tape_nontext_response")
            operation["text"] = response.text
            from live.scenario_oauth import usage_counts
            usage = usage_counts(getattr(response,'usage',None))
            if usage is not None:
                operation['usage'] = usage
            return SimpleNamespace(text=response.text, **({'usage':usage} if usage is not None else {}))
        except Exception as exc:
            operation["error"] = _error(exc)
            raise
        finally:
            append(operation)

    outcome = _outcome(lambda: review(snapshot, context, contract, generate=recorded_generate,
                                      record=recorded_event, clock=recorded_clock))
    if inputs != _inputs(snapshot, context, contract, bindings):
        raise TapeMismatch("review_tape_input_mutated")
    envelope = dict(version=VERSION, kind="EXACT_ORDER_FREE_REVIEW", inputs=inputs,
                    inputs_sha256=digest(inputs), operations=operations, outcome=outcome,
                    calls=calls, executable=False)
    _bound(envelope)
    envelope["sha256"] = digest(envelope)
    return envelope


def replay_review(envelope, review, snapshot, context, contract, *, bindings=None,
                  economic_clock=None, economic_record=None, operation_complete=None):
    """No external generator, recorder or wall clock exists on the frozen path."""
    _bound(envelope)
    frozen = deepcopy(envelope)
    proof = frozen.pop("sha256", None)
    if proof != digest(frozen) or frozen.get("version") != VERSION or frozen.get("kind") != "EXACT_ORDER_FREE_REVIEW":
        raise TapeMismatch("review_tape_envelope")
    _bound(frozen)
    inputs = _inputs(snapshot, context, contract, bindings)
    if frozen.get("inputs") != inputs or frozen.get("inputs_sha256") != digest(inputs):
        raise TapeMismatch("review_tape_inputs")
    operations = frozen["operations"]
    if not isinstance(operations, list) or len(operations) > MAX_OPERATIONS:
        raise TapeMismatch("review_tape_operation_limit")
    index, calls, previous_time = 0, 0, None
    mismatches = []

    def take(kind, key=None, value=None, *, raise_error=True):
        nonlocal index
        if index >= len(operations):
            mismatches.append("missing")
            raise TapeMismatch("review_tape_missing_operation")
        operation = operations[index]
        index += 1
        if operation.get("kind") != kind or (key is not None and operation.get(key) != value):
            mismatches.append("changed")
            raise TapeMismatch("review_tape_operation_mismatch")
        if raise_error and "error" in operation:
            error = operation["error"]
            # Names/messages are evidence, never imported or evaluated as code.
            raise type(error["type"], (Exception,), {})(error["message"])
        return operation

    def replay_clock():
        nonlocal previous_time
        value = take("clock")["value"]
        if type(value) not in (int, float) or not math.isfinite(value) or (previous_time is not None and value < previous_time):
            mismatches.append("clock")
            raise TapeMismatch("review_tape_invalid_clock")
        previous_time = value
        if economic_clock is not None and economic_clock() != value:
            mismatches.append("economic_clock")
            raise TapeMismatch("review_tape_economic_clock")
        return value

    def replay_record(event):
        try:
            operation = take("record", "event", event, raise_error=False)
            if economic_record is not None:
                economic_record(deepcopy(event))
            if "error" in operation:
                error=operation["error"]
                raise type(error["type"], (Exception,), {})(error["message"])
        finally:
            if operation_complete is not None:
                operation_complete("record_return")

    def replay_generate(**request):
        nonlocal calls
        calls += 1
        if calls > 2:
            mismatches.append("calls")
            raise TapeMismatch("review_tape_call_limit")
        try:
            operation = take("generate", "request", request)
            return SimpleNamespace(text=operation['text'], **({'usage':deepcopy(operation['usage'])} if 'usage' in operation else {}))
        finally:
            if operation_complete is not None:
                operation_complete("generate_return")

    outcome = _outcome(lambda: review(snapshot, context, contract, generate=replay_generate,
                                      record=replay_record, clock=replay_clock))
    if (mismatches or index != len(operations) or calls != frozen["calls"]
            or outcome != frozen["outcome"] or inputs != _inputs(snapshot, context, contract, bindings)):
        raise TapeMismatch("review_tape_replay_mismatch")
    return deepcopy(outcome)
