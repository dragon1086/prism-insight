"""Batch campaign ingestion and room delivery planning."""

from __future__ import annotations

from dataclasses import dataclass

from kakao_bot.domain.models import (
    BatchCampaign,
    CampaignStatus,
    OutboundDelivery,
)
from kakao_bot.ports.repositories import KakaoRepository


@dataclass(frozen=True)
class CampaignPlanResult:
    campaign_created: bool
    deliveries_created: int


class BatchCampaignService:
    def __init__(self, repository: KakaoRepository) -> None:
        self._repository = repository

    def ingest_and_plan(self, campaign: BatchCampaign) -> CampaignPlanResult:
        campaign_created = self._repository.save_campaign(campaign)

        is_rest_notice = campaign.status is CampaignStatus.SKIPPED
        room_ids = self._repository.list_delivery_targets(
            campaign.market,
            campaign.session,
            require_rest_notices=is_rest_notice,
        )

        created = 0
        for room_id in room_ids:
            delivery = self._delivery_for(
                campaign,
                room_id=room_id,
                is_rest_notice=is_rest_notice,
            )
            created += int(self._repository.enqueue_outbound(delivery))

        return CampaignPlanResult(campaign_created, created)

    @staticmethod
    def _delivery_for(
        campaign: BatchCampaign,
        *,
        room_id: str,
        is_rest_notice: bool,
    ) -> OutboundDelivery:
        common_payload: dict[str, object] = {
            "campaign_id": campaign.campaign_id,
            "market": campaign.market.value,
            "session": campaign.session.value,
            "trade_date": campaign.trade_date.isoformat(),
            "regime": campaign.regime.value,
        }

        if is_rest_notice:
            common_payload["reason"] = campaign.skip_reason
            message_type = "campaign_rest_notice"
            delivery_kind = "rest"
        else:
            common_payload["candidates"] = [
                candidate.as_payload() for candidate in campaign.candidates
            ]
            if campaign.display_message:
                common_payload["display_message"] = campaign.display_message
            message_type = "signal_campaign"
            delivery_kind = "candidates"

        return OutboundDelivery(
            delivery_key=(
                f"campaign:{campaign.campaign_id}:room:{room_id}:{delivery_kind}"
            ),
            room_id=room_id,
            message_type=message_type,
            payload=common_payload,
        )
