"""Explicitly enqueue one local campaign for end-to-end smoke testing."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from messaging.batch_campaign_publisher import (
    BatchCampaignPublisher,
    COMPLETED,
    build_batch_campaign_event,
)


@dataclass(frozen=True)
class CampaignSmokeConfig:
    queue_path: Path
    trade_date: date
    nonce: str


async def enqueue_smoke(config: CampaignSmokeConfig) -> str | None:
    event = build_batch_campaign_event(
        market="KR",
        session="AFTERNOON",
        trade_date=config.trade_date,
        regime="UPTREND",
        status=COMPLETED,
        candidates=[
            {
                "ticker": "005930",
                "company_name": "삼성전자",
                "score": 91,
                "rationale": "Kakao local queue smoke",
            }
        ],
    )
    event["campaign_id"] = (
        f"smoke-kr-afternoon-{config.trade_date.isoformat()}-{config.nonce}"
    )
    async with BatchCampaignPublisher(database_path=config.queue_path) as publisher:
        return await publisher.publish(event)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Enqueue one local Kakao campaign smoke event"
    )
    parser.add_argument(
        "--confirm-enqueue",
        action="store_true",
        help="Required acknowledgement that sender may deliver a real message",
    )
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_enqueue:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "--confirm-enqueue is required",
                },
                ensure_ascii=False,
            )
        )
        return 2
    campaign_id = await enqueue_smoke(
        CampaignSmokeConfig(
            queue_path=Path(
                os.getenv(
                    "PRISM_CAMPAIGN_QUEUE_PATH",
                    "prism_campaign_queue.sqlite",
                )
            ).expanduser(),
            trade_date=datetime.now(ZoneInfo("Asia/Seoul")).date(),
            nonce=secrets.token_hex(4),
        )
    )
    print(
        json.dumps(
            {
                "status": "ok" if campaign_id else "error",
                "campaign_id": campaign_id,
            },
            ensure_ascii=False,
        )
    )
    return 0 if campaign_id else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
