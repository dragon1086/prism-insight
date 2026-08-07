"""Map Prism's channel-neutral batch campaign payload into Kakao domain types."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone

from kakao_bot.domain.models import (
    BatchCampaign,
    BatchStoryEvent,
    BatchStoryKind,
    CampaignCandidate,
    CampaignStatus,
    Market,
    Regime,
    Session,
)

_STORY_TYPES = {
    "BATCH_CAMPAIGN_REPORT_READY": BatchStoryKind.REPORT,
    "BATCH_CAMPAIGN_DECISION_READY": BatchStoryKind.DECISION,
    "BATCH_CAMPAIGN_PORTFOLIO_SNAPSHOT": BatchStoryKind.PORTFOLIO,
}


class BatchCampaignPayloadError(ValueError):
    """Raised when a producer payload violates the batch campaign v1 contract."""


def is_batch_story_payload(payload: Mapping[str, object]) -> bool:
    return payload.get("event_type") in _STORY_TYPES


def map_batch_story_payload(payload: Mapping[str, object]) -> BatchStoryEvent:
    if not isinstance(payload, Mapping):
        raise BatchCampaignPayloadError("payload must be a mapping")
    if (
        type(payload.get("schema_version")) is not int
        or payload.get("schema_version") != 1
    ):
        raise BatchCampaignPayloadError("schema_version must be integer 1")
    event_type = _required_string(payload, "event_type")
    try:
        kind = _STORY_TYPES[event_type]
    except KeyError as exc:
        raise BatchCampaignPayloadError(
            f"unsupported batch story event_type: {event_type}"
        ) from exc

    market = _parse_enum(payload, "market", Market)
    session = _parse_enum(payload, "session", Session)
    regime = _parse_enum(payload, "regime", Regime)
    common = {
        "event_id": _required_string(payload, "event_id"),
        "campaign_id": _required_string(payload, "campaign_id"),
        "market": market,
        "session": session,
        "trade_date": _parse_trade_date(payload.get("trade_date")),
        "regime": regime,
        "kind": kind,
        "message": _required_string(payload, "message"),
        "created_at": _parse_occurred_at(payload.get("occurred_at")),
    }
    if kind is BatchStoryKind.REPORT:
        return BatchStoryEvent(
            **common,
            ticker=_required_string(payload, "ticker"),
            company_name=_required_string(payload, "company_name"),
            artifact_path=_required_string(payload, "artifact_path"),
        )
    return BatchStoryEvent(**common)


def map_batch_campaign_payload(payload: Mapping[str, object]) -> BatchCampaign:
    """Validate and map a schema-version-1 campaign event."""

    if not isinstance(payload, Mapping):
        raise BatchCampaignPayloadError("payload must be a mapping")
    if type(payload.get("schema_version")) is not int:  # bool is not a version
        raise BatchCampaignPayloadError("schema_version must be integer 1")
    if payload["schema_version"] != 1:
        raise BatchCampaignPayloadError(
            f"unsupported schema_version: {payload['schema_version']}"
        )

    status_text = _required_string(payload, "status")
    try:
        status = CampaignStatus(status_text)
    except ValueError as exc:
        raise BatchCampaignPayloadError(
            f"unsupported campaign status: {status_text}"
        ) from exc
    if status not in (
        CampaignStatus.COLLECTING,
        CampaignStatus.COMPLETED,
        CampaignStatus.SKIPPED,
    ):
        raise BatchCampaignPayloadError(f"unsupported campaign status: {status_text}")

    event_type = _required_string(payload, "event_type")
    expected_event_type = f"BATCH_CAMPAIGN_{status.value}"
    if event_type != expected_event_type:
        raise BatchCampaignPayloadError(
            f"event_type {event_type!r} does not match status {status.value!r}"
        )

    market = _parse_enum(payload, "market", Market)
    session = _parse_enum(payload, "session", Session)
    regime = _parse_enum(payload, "regime", Regime)
    trade_date = _parse_trade_date(payload.get("trade_date"))
    occurred_at = _parse_occurred_at(payload.get("occurred_at"))
    campaign_id = _required_string(payload, "campaign_id")

    if status in (CampaignStatus.COLLECTING, CampaignStatus.COMPLETED):
        if "skip_reason" in payload:
            raise BatchCampaignPayloadError(
                "screening campaign must not include skip_reason"
            )
        candidates = _parse_candidates(payload.get("candidates"))
        skip_reason = None
        display_message = _optional_string(payload, "display_message")
    else:
        if "candidates" in payload:
            raise BatchCampaignPayloadError(
                "skipped campaign must not include candidates"
            )
        candidates = ()
        skip_reason = _required_string(payload, "skip_reason")
        display_message = None

    return BatchCampaign(
        campaign_id=campaign_id,
        market=market,
        session=session,
        trade_date=trade_date,
        regime=regime,
        status=status,
        candidates=candidates,
        skip_reason=skip_reason,
        display_message=display_message,
        created_at=occurred_at,
    )


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BatchCampaignPayloadError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_string(payload: Mapping[str, object], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise BatchCampaignPayloadError(f"{field} must be a non-empty string or null")
    return value.strip()


def _parse_enum(payload, field, enum_type):
    value = _required_string(payload, field)
    try:
        return enum_type(value)
    except ValueError as exc:
        raise BatchCampaignPayloadError(f"unsupported {field}: {value}") from exc


def _parse_trade_date(value: object) -> date:
    if not isinstance(value, str):
        raise BatchCampaignPayloadError("trade_date must be an ISO date string")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise BatchCampaignPayloadError(f"invalid trade_date: {value}") from exc
    if parsed.isoformat() != value:
        raise BatchCampaignPayloadError(f"trade_date must use YYYY-MM-DD: {value}")
    return parsed


def _parse_occurred_at(value: object) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise BatchCampaignPayloadError("occurred_at must be an ISO timestamp string")

    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise BatchCampaignPayloadError(f"invalid occurred_at: {value}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BatchCampaignPayloadError("occurred_at must include a timezone offset")
    return parsed.astimezone(timezone.utc)


def _parse_candidates(value: object) -> tuple[CampaignCandidate, ...]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not value
    ):
        raise BatchCampaignPayloadError(
            "completed campaign requires at least one candidate"
        )
    if len(value) > 5:
        raise BatchCampaignPayloadError(
            "completed campaign supports at most five candidates"
        )

    candidates = []
    for index, raw_candidate in enumerate(value):
        if not isinstance(raw_candidate, Mapping):
            raise BatchCampaignPayloadError(f"candidate {index} must be a mapping")
        ticker = _required_string(raw_candidate, "ticker")
        company_name = _required_string(raw_candidate, "company_name")
        score = _optional_score(raw_candidate.get("score"), index=index)
        rationale = raw_candidate.get("rationale")
        if rationale is not None and not isinstance(rationale, str):
            raise BatchCampaignPayloadError(
                f"candidate {index} rationale must be a string or null"
            )
        candidates.append(
            CampaignCandidate(
                ticker=ticker,
                company_name=company_name,
                score=score,
                rationale=rationale.strip() if rationale else None,
            )
        )
    return tuple(candidates)


def _optional_score(value: object, *, index: int) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise BatchCampaignPayloadError(
            f"candidate {index} score must be a finite number or null"
        )
    score = float(value)
    if not math.isfinite(score):
        raise BatchCampaignPayloadError(
            f"candidate {index} score must be a finite number or null"
        )
    return score
