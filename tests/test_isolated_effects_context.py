import hashlib
import json

import pytest

from prism_core.isolated_strategy_effects import EffectsFailure, EffectsPipelineContext
from cores.corporate_status import classify_kis_status


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def context(payload):
    raw = encoded(payload)
    return EffectsPipelineContext("case", hashlib.sha256(raw.encode()).hexdigest(), raw)


def envelope(value):
    return {"value": value, "source": "synthetic-unit-fixture", "observed_at": "2026-09-10T00:00:00Z",
            "as_of": "2026-09-10T00:00:00Z", "value_hash": hashlib.sha256(encoded(value).encode()).hexdigest()}


def test_required_context_missing_never_becomes_neutral():
    ctx = context({})
    with pytest.raises(EffectsFailure, match="missing"):
        ctx.read("quote", "005930", "2026-09-10T00:00:00Z")


def test_context_hash_source_clock_and_value_are_bound():
    payload = {"quote": {"005930": envelope(100)}}
    assert context(payload).read("quote", "005930", "2026-09-10T00:01:00Z") == 100
    with pytest.raises(EffectsFailure):
        context(payload).read("quote", "005930", "2026-09-10T00:03:00Z")
    with pytest.raises(EffectsFailure):
        context(payload).read("quote", "005930", "2026-09-09T23:59:00Z")
    payload["quote"]["005930"]["value"] = 101
    with pytest.raises(EffectsFailure):
        context(payload)


@pytest.mark.parametrize("kind,value", [
    ("quote", 0), ("quote", True), ("corporate_status", None),
    ("corporate_event", {"should_exit": None, "reason": "unknown"}),
])
def test_context_rejects_invalid_source_values(kind, value):
    with pytest.raises(EffectsFailure):
        context({kind: {"005930": envelope(value)}})


def test_corporate_false_is_explicit_source_record_not_default():
    value = {"should_exit": False, "reason": "synthetic fixture, no event"}
    assert context({"corporate_event": {"005930": envelope(value)}}).read(
        "corporate_event", "005930", "2026-09-10T00:00:00Z") == value


@pytest.mark.parametrize("code, expected", [("00", False), ("51", True)])
def test_corporate_envelope_preserves_actual_status_classifier_output(code, expected):
    should_exit, reason = classify_kis_status(code)
    assert should_exit is expected
    value = {"should_exit": should_exit, "reason": reason}
    result = context({"corporate_event": {"005930": envelope(value)}}).read(
        "corporate_event", "005930", "2026-09-10T00:00:00Z")
    assert result == value
    if not expected:
        assert result["reason"] == ""


@pytest.mark.parametrize("should_exit,reason", [(True, ""), (True, "  "), (1, "risk"), (0, ""), ("False", "")])
def test_corporate_exit_requires_strict_boolean_and_meaningful_true_reason(should_exit, reason):
    with pytest.raises(EffectsFailure):
        context({"corporate_event": {"005930": envelope({"should_exit": should_exit, "reason": reason})}})


def test_recent_retrieval_does_not_refresh_old_exchange_quote():
    record = envelope(100)
    record["as_of"] = "2026-09-09T23:00:00Z"
    with pytest.raises(EffectsFailure, match="stale"):
        context({"quote": {"005930": record}}).read("quote", "005930", "2026-09-10T00:00:00Z")


@pytest.mark.parametrize("adjustment", [-4, 4, 0.5, True])
def test_registered_journal_cannot_expand_production_adjustment_range(adjustment):
    with pytest.raises(EffectsFailure):
        context({"journal": {"005930": envelope({"context": "fixture", "adjustment": adjustment, "reasons": []})}})
