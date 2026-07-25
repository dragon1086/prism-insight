"""Fail-closed data-quality policy tests."""

from datetime import datetime, timezone
from uuid import uuid4

import pytest
from prism_core.data import (
    DataQualityGate,
    DataQualityStatus,
    QualityDisposition,
    QualitySkipRecord,
)


UTC = timezone.utc


def test_fresh_core_fields_accept_new_proposals() -> None:
    gate = DataQualityGate(core_fields={"price", "regime", "calendar", "evidence"})

    decision = gate.evaluate(
        {
            "price": DataQualityStatus.FRESH,
            "regime": DataQualityStatus.FRESH,
            "calendar": DataQualityStatus.FRESH,
            "evidence": DataQualityStatus.FRESH,
        }
    )

    assert decision.disposition is QualityDisposition.ACCEPT
    assert decision.reasons == ()
    assert decision.missing_fields == ()
    assert decision.stale_fields == ()


def test_default_gate_requires_every_proposal_core_field() -> None:
    decision = DataQualityGate().evaluate({})

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.missing_fields == ("calendar", "evidence", "price", "regime")


@pytest.mark.parametrize(
    ("status", "expected_reason", "expected_missing", "expected_stale"),
    [
        (DataQualityStatus.PARTIAL, "partial_core:price", ("price",), ()),
        (DataQualityStatus.STALE, "stale_core:price", (), ("price",)),
        (DataQualityStatus.UNAVAILABLE, "unavailable_core:price", ("price",), ()),
        (DataQualityStatus.CONFLICT, "conflict_core:price", (), ()),
    ],
)
def test_unusable_core_field_rejects_new_proposals(
    status: DataQualityStatus,
    expected_reason: str,
    expected_missing: tuple[str, ...],
    expected_stale: tuple[str, ...],
) -> None:
    gate = DataQualityGate(core_fields={"price"})

    decision = gate.evaluate({"price": status})

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.reasons == (expected_reason,)
    assert decision.missing_fields == expected_missing
    assert decision.stale_fields == expected_stale


@pytest.mark.parametrize("news_quality", [None, DataQualityStatus.PARTIAL])
def test_explicit_non_core_partial_field_is_report_only(
    news_quality: DataQualityStatus | None,
) -> None:
    gate = DataQualityGate(
        core_fields={"price"}, report_only_fields={"news_context"}
    )
    quality = {"price": DataQualityStatus.FRESH}
    if news_quality is not None:
        quality["news_context"] = news_quality

    decision = gate.evaluate(quality)

    assert decision.disposition is QualityDisposition.REPORT_ONLY
    assert decision.missing_fields == ("news_context",)
    assert decision.stale_fields == ()


def test_unclassified_partial_field_rejects_instead_of_becoming_report_only() -> None:
    gate = DataQualityGate(core_fields={"price"})

    decision = gate.evaluate(
        {
            "price": DataQualityStatus.FRESH,
            "unclassified": DataQualityStatus.PARTIAL,
        }
    )

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.reasons == ("partial_unclassified:unclassified",)


@pytest.mark.parametrize(
    "status",
    [
        DataQualityStatus.STALE,
        DataQualityStatus.UNAVAILABLE,
        DataQualityStatus.CONFLICT,
    ],
)
def test_unusable_report_only_field_still_rejects_new_proposals(
    status: DataQualityStatus,
) -> None:
    gate = DataQualityGate(core_fields={"price"}, report_only_fields={"news_context"})

    decision = gate.evaluate(
        {
            "price": DataQualityStatus.FRESH,
            "news_context": status,
        }
    )

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.reasons == (f"{status.value.lower()}_report_only:news_context",)
    assert decision.stale_fields == (
        ("news_context",) if status is DataQualityStatus.STALE else ()
    )


def test_core_failure_dominates_report_only_warning() -> None:
    gate = DataQualityGate(core_fields={"price"}, report_only_fields={"news_context"})

    decision = gate.evaluate(
        {
            "price": DataQualityStatus.STALE,
            "news_context": DataQualityStatus.PARTIAL,
        }
    )

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.stale_fields == ("price",)
    assert decision.missing_fields == ("news_context",)


def test_gate_exception_rejects_and_emits_a_sanitized_warning(caplog) -> None:
    gate = DataQualityGate(core_fields={"price"})

    decision = gate.evaluate({"price": "BROKEN"})  # type: ignore[dict-item]

    assert decision.disposition is QualityDisposition.REJECT
    assert decision.reasons == ("quality_gate_error:TypeError",)
    assert "quality gate rejected malformed input (TypeError)" in caplog.text


def test_field_classifications_cannot_overlap() -> None:
    with pytest.raises(ValueError, match="both core and report-only"):
        DataQualityGate(core_fields={"price"}, report_only_fields={"price"})


def test_explicit_empty_core_classification_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one core field"):
        DataQualityGate(core_fields=set())


class RecordingSkipSink:
    def __init__(self) -> None:
        self.records: list[QualitySkipRecord] = []

    def append(self, record: QualitySkipRecord) -> None:
        self.records.append(record)


def test_quality_failure_appends_audit_ready_skip_record() -> None:
    gate = DataQualityGate(core_fields={"price"})
    sink = RecordingSkipSink()
    snapshot_id = uuid4()
    evaluated_at = datetime(2026, 7, 25, tzinfo=UTC)

    decision = gate.evaluate_and_record(
        {},
        recorder=sink,
        request_id="quality-run-1",
        snapshot_id=snapshot_id,
        evaluated_at=evaluated_at,
    )

    assert decision.disposition is QualityDisposition.REJECT
    assert sink.records == [
        QualitySkipRecord(
            request_id="quality-run-1",
            snapshot_id=snapshot_id,
            evaluated_at=evaluated_at,
            disposition=QualityDisposition.REJECT,
            reasons=("missing_core:price",),
            missing_fields=("price",),
            stale_fields=(),
        )
    ]


def test_accept_does_not_create_a_skip_record() -> None:
    gate = DataQualityGate(core_fields={"price"})
    sink = RecordingSkipSink()

    decision = gate.evaluate_and_record(
        {"price": DataQualityStatus.FRESH},
        recorder=sink,
        request_id="quality-run-2",
        snapshot_id=uuid4(),
        evaluated_at=datetime(2026, 7, 25, tzinfo=UTC),
    )

    assert decision.disposition is QualityDisposition.ACCEPT
    assert sink.records == []


def test_report_only_records_that_new_proposal_generation_was_skipped() -> None:
    gate = DataQualityGate(core_fields={"price"}, report_only_fields={"news_context"})
    sink = RecordingSkipSink()

    decision = gate.evaluate_and_record(
        {"price": DataQualityStatus.FRESH},
        recorder=sink,
        request_id="quality-run-3",
        snapshot_id=uuid4(),
        evaluated_at=datetime(2026, 7, 25, tzinfo=UTC),
    )

    assert decision.disposition is QualityDisposition.REPORT_ONLY
    assert len(sink.records) == 1
    assert sink.records[0].skipped_action == "NEW_PROPOSAL"


def test_recorder_failure_propagates_instead_of_claiming_an_audited_skip() -> None:
    class FailingSink:
        def append(self, record: QualitySkipRecord) -> None:
            raise RuntimeError("audit unavailable")

    gate = DataQualityGate(core_fields={"price"})

    with pytest.raises(RuntimeError, match="audit unavailable"):
        gate.evaluate_and_record(
            {},
            recorder=FailingSink(),
            request_id="quality-run-4",
            snapshot_id=uuid4(),
            evaluated_at=datetime(2026, 7, 25, tzinfo=UTC),
        )
