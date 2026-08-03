from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.inbound_service import InboundService
from kakao_bot.application.batch_campaign_service import BatchCampaignService
from kakao_bot.domain.errors import RoomNotApprovedError
from kakao_bot.domain.models import (
    ApprovalStatus,
    BatchCampaign,
    CampaignCandidate,
    CampaignStatus,
    Market,
    OutboundDelivery,
    Regime,
    RoomSubscription,
    Session,
)


def campaign(
    campaign_id: str,
    *,
    market: Market = Market.KR,
    session: Session = Session.AFTERNOON,
    status: CampaignStatus = CampaignStatus.COMPLETED,
    skip_reason: str | None = None,
) -> BatchCampaign:
    candidates = (
        ()
        if status is CampaignStatus.SKIPPED
        else (
            CampaignCandidate(
                ticker="005930",
                company_name="삼성전자",
                score=91.0,
            ),
        )
    )
    return BatchCampaign(
        campaign_id=campaign_id,
        market=market,
        session=session,
        trade_date=date(2026, 7, 23),
        regime=Regime.CORRECTION,
        status=status,
        candidates=candidates,
        skip_reason=skip_reason,
    )


@pytest.fixture
def repository(tmp_path):
    with SQLiteKakaoRepository(tmp_path / "kakao_bot.sqlite") as repo:
        yield repo


def approve_with_defaults(
    repository: SQLiteKakaoRepository,
    room_id: str,
    *,
    rest_notices: bool = False,
) -> None:
    repository.discover_room(room_id)
    repository.set_room_approval(room_id, ApprovalStatus.APPROVED)
    if rest_notices:
        repository.configure_subscription(
            RoomSubscription(room_id=room_id, rest_notices=True)
        )


def test_subscription_product_defaults_to_kr_afternoon_only():
    subscription = RoomSubscription(room_id="room-1")

    assert not subscription.is_enabled(Market.KR, Session.MORNING)
    assert subscription.is_enabled(Market.KR, Session.AFTERNOON)
    assert not subscription.is_enabled(Market.US, Session.MORNING)
    assert not subscription.is_enabled(Market.US, Session.AFTERNOON)
    assert subscription.rest_notices is False


def test_discovery_is_pending_all_off_and_requires_approval_to_configure(
    repository,
):
    room = repository.discover_room("room-1")

    assert room.approval_status is ApprovalStatus.PENDING
    assert repository.get_subscription("room-1") == RoomSubscription.all_disabled(
        "room-1"
    )
    with pytest.raises(RoomNotApprovedError):
        repository.configure_subscription(RoomSubscription(room_id="room-1"))

    repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
    assert repository.get_subscription("room-1") == RoomSubscription(room_id="room-1")


def test_sqlite_connection_enables_required_safety_settings(repository):
    settings = repository.database_settings()

    assert settings["journal_mode"] == "wal"
    assert settings["foreign_keys"] is True
    assert settings["busy_timeout"] == 5_000


def test_completed_kr_afternoon_campaign_is_delivered_to_approved_opt_in_room(
    repository,
):
    approve_with_defaults(repository, "room-1")

    result = BatchCampaignService(repository).ingest_and_plan(
        campaign("kr-afternoon-1")
    )

    assert result.campaign_created is True
    assert result.deliveries_created == 1
    [delivery] = repository.list_outbox()
    assert delivery["room_id"] == "room-1"
    assert delivery["message_type"] == "signal_campaign"
    assert delivery["payload"]["campaign_id"] == "kr-afternoon-1"
    assert delivery["payload"]["session"] == "AFTERNOON"


def test_delivery_planning_excludes_room_without_current_approval(repository):
    approve_with_defaults(repository, "room-1")
    repository.set_room_approval("room-1", ApprovalStatus.REJECTED)

    result = BatchCampaignService(repository).ingest_and_plan(
        campaign("kr-afternoon-rejected-room")
    )

    assert result.deliveries_created == 0
    assert repository.list_outbox() == ()


def test_kr_morning_is_disabled_by_default(repository):
    approve_with_defaults(repository, "room-1")

    result = BatchCampaignService(repository).ingest_and_plan(
        campaign(
            "kr-morning-1",
            market=Market.KR,
            session=Session.MORNING,
        )
    )

    assert result.deliveries_created == 0
    assert repository.list_outbox() == ()


@pytest.mark.parametrize("session", [Session.MORNING, Session.AFTERNOON])
def test_us_sessions_are_disabled_by_default(repository, session):
    approve_with_defaults(repository, "room-1")

    result = BatchCampaignService(repository).ingest_and_plan(
        campaign(
            f"us-{session.value.lower()}-1",
            market=Market.US,
            session=session,
        )
    )

    assert result.deliveries_created == 0
    assert repository.list_outbox() == ()


def test_inbound_campaign_and_outbox_writes_are_idempotent(repository):
    inbound = InboundService(repository)
    occurred_at = datetime(2026, 7, 23, 5, 0, tzinfo=timezone.utc)
    assert inbound.accept("gateway-event-1", occurred_at=occurred_at) is True
    assert inbound.accept("gateway-event-1", occurred_at=occurred_at) is False

    approve_with_defaults(repository, "room-1")
    service = BatchCampaignService(repository)
    first = service.ingest_and_plan(campaign("kr-afternoon-duplicate"))
    second = service.ingest_and_plan(campaign("kr-afternoon-duplicate"))

    assert first.campaign_created is True
    assert first.deliveries_created == 1
    assert second.campaign_created is False
    assert second.deliveries_created == 0
    assert len(repository.list_outbox()) == 1

    duplicate_delivery = OutboundDelivery(
        delivery_key="manual:room-1:duplicate",
        room_id="room-1",
        message_type="test",
        payload={"ok": True},
    )
    assert repository.enqueue_outbound(duplicate_delivery) is True
    assert repository.enqueue_outbound(duplicate_delivery) is False


def test_skipped_campaign_only_creates_rest_notice_for_explicit_opt_in(
    repository,
):
    approve_with_defaults(repository, "no-rest-room")
    approve_with_defaults(repository, "rest-room", rest_notices=True)

    result = BatchCampaignService(repository).ingest_and_plan(
        campaign(
            "kr-afternoon-skipped",
            status=CampaignStatus.SKIPPED,
            skip_reason="MARKET_PULSE_CORRECTION",
        )
    )

    assert result.deliveries_created == 1
    [delivery] = repository.list_outbox()
    assert delivery["room_id"] == "rest-room"
    assert delivery["message_type"] == "campaign_rest_notice"
    assert delivery["payload"]["reason"] == "MARKET_PULSE_CORRECTION"
    assert "candidates" not in delivery["payload"]
