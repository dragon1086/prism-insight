from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from kakao_bot.adapters.prism.batch_campaign_mapper import (
    BatchCampaignPayloadError,
    map_batch_campaign_payload,
)
from kakao_bot.domain.models import (
    CampaignStatus,
    Market,
    Regime,
    Session,
)
from messaging.batch_campaign_publisher import build_batch_campaign_event


def test_maps_completed_producer_event_to_batch_campaign():
    occurred_at = datetime(
        2026,
        7,
        23,
        15,
        5,
        tzinfo=timezone(timedelta(hours=9)),
    )
    payload = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date="20260723",
        regime="CORRECTION",
        status="COMPLETED",
        candidates=[
            {
                "code": "005930",
                "name": "삼성전자",
                "buy_score": 91,
                "trigger_type": "closing_confirmation",
            }
        ],
        occurred_at=occurred_at,
    )

    mapped = map_batch_campaign_payload(payload)

    assert mapped.campaign_id == "kr-afternoon-2026-07-23"
    assert mapped.market is Market.KR
    assert mapped.session is Session.AFTERNOON
    assert mapped.trade_date == date(2026, 7, 23)
    assert mapped.regime is Regime.CORRECTION
    assert mapped.status is CampaignStatus.COMPLETED
    assert mapped.created_at == datetime(2026, 7, 23, 6, 5, tzinfo=timezone.utc)
    [candidate] = mapped.candidates
    assert candidate.ticker == "005930"
    assert candidate.company_name == "삼성전자"
    assert candidate.score == 91.0
    assert candidate.rationale == "closing_confirmation"
    assert mapped.skip_reason is None


def test_maps_skipped_producer_event_to_batch_campaign():
    payload = build_batch_campaign_event(
        market="US",
        session="MORNING",
        trade_date=date(2026, 7, 23),
        regime="CORRECTION",
        status="SKIPPED",
        skip_reason="MARKET_PULSE_CORRECTION",
        occurred_at=datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc),
    )

    mapped = map_batch_campaign_payload(payload)

    assert mapped.campaign_id == "us-morning-2026-07-23"
    assert mapped.market is Market.US
    assert mapped.session is Session.MORNING
    assert mapped.status is CampaignStatus.SKIPPED
    assert mapped.candidates == ()
    assert mapped.skip_reason == "MARKET_PULSE_CORRECTION"


def test_maps_completed_event_with_unknown_fail_open_regime():
    payload = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date="20260723",
        regime=None,
        status="COMPLETED",
        candidates=[{"code": "005930", "name": "삼성전자"}],
    )

    mapped = map_batch_campaign_payload(payload)

    assert mapped.regime is Regime.UNKNOWN


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("event_type", "BATCH_CAMPAIGN_SKIPPED"),
        ("market", "JP"),
        ("session", "NOON"),
        ("trade_date", "20260723"),
        ("occurred_at", "2026-07-23T06:05:00"),
    ],
)
def test_rejects_invalid_contract_fields(field, value):
    payload = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date=date(2026, 7, 23),
        regime="UPTREND",
        status="COMPLETED",
        candidates=[{"ticker": "005930", "company_name": "삼성전자"}],
    )
    payload[field] = value

    with pytest.raises(BatchCampaignPayloadError):
        map_batch_campaign_payload(payload)


@pytest.mark.parametrize(
    "mutation",
    [
        {"candidates": []},
        {"candidates": [{"ticker": "", "company_name": "삼성전자"}]},
        {"skip_reason": "not-valid-for-completed"},
    ],
)
def test_rejects_malformed_completed_fields(mutation):
    payload = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date=date(2026, 7, 23),
        regime="UPTREND",
        status="COMPLETED",
        candidates=[{"ticker": "005930", "company_name": "삼성전자"}],
    )
    payload.update(mutation)

    with pytest.raises(BatchCampaignPayloadError):
        map_batch_campaign_payload(payload)


def test_rejects_skipped_event_without_reason():
    payload = build_batch_campaign_event(
        market="KR",
        session="MORNING",
        trade_date=date(2026, 7, 23),
        regime="CORRECTION",
        status="SKIPPED",
        skip_reason="MARKET_PULSE_CORRECTION",
    )
    payload["skip_reason"] = ""

    with pytest.raises(BatchCampaignPayloadError):
        map_batch_campaign_payload(payload)
