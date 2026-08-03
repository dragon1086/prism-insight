"""Operator CLI for room approval and subscription management."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path

from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import (
    ApprovalStatus,
    Room,
    RoomSubscription,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

_TOGGLE_FIELDS = (
    "kr_morning",
    "kr_afternoon",
    "us_morning",
    "us_afternoon",
    "rest_notices",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage the PRISM Kakao bot")
    parser.add_argument(
        "--database",
        default=os.getenv("KAKAO_BOT_DATABASE_PATH", "kakao_bot.sqlite"),
        help="Path to the dedicated Kakao SQLite database",
    )
    parser.add_argument(
        "--queue",
        default=os.getenv(
            "PRISM_CAMPAIGN_QUEUE_PATH",
            "prism_campaign_queue.sqlite",
        ),
        help="Path to the channel-neutral campaign queue",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("rooms", help="List discovered rooms")

    for command in ("approve", "reject"):
        subparser = commands.add_parser(command)
        subparser.add_argument("room_id")

    subscription = commands.add_parser(
        "subscription",
        help="Update an approved room subscription",
    )
    subscription.add_argument("room_id")
    for field_name in _TOGGLE_FIELDS:
        subscription.add_argument(
            f"--{field_name.replace('_', '-')}",
            choices=("on", "off"),
        )

    commands.add_parser("outbox", help="Show outbox status counts")
    commands.add_parser("campaigns", help="Show campaign queue status counts")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with SQLiteKakaoRepository(Path(args.database).expanduser()) as repository:
            if args.command == "rooms":
                _write(
                    {
                        "status": "ok",
                        "rooms": [
                            _room_payload(room) for room in repository.list_rooms()
                        ],
                    }
                )
                return 0
            if args.command in {"approve", "reject"}:
                status = (
                    ApprovalStatus.APPROVED
                    if args.command == "approve"
                    else ApprovalStatus.REJECTED
                )
                repository.set_room_approval(args.room_id, status)
                _write(
                    {
                        "status": "ok",
                        **_room_payload(repository.get_room(args.room_id)),
                    }
                )
                return 0
            if args.command == "subscription":
                current = repository.get_subscription(args.room_id)
                values = asdict(current)
                for field_name in _TOGGLE_FIELDS:
                    requested = getattr(args, field_name)
                    if requested is not None:
                        values[field_name] = requested == "on"
                configured = RoomSubscription(**values)
                repository.configure_subscription(configured)
                _write(
                    {
                        "status": "ok",
                        "subscription": asdict(configured),
                    }
                )
                return 0
            if args.command == "outbox":
                counts = Counter(row["status"] for row in repository.list_outbox())
                _write(
                    {
                        "status": "ok",
                        "outbox": {
                            key: counts.get(key, 0)
                            for key in ("PENDING", "SENDING", "SENT", "DEAD")
                        },
                    }
                )
                return 0
            if args.command == "campaigns":
                with SQLiteBatchCampaignQueue(Path(args.queue).expanduser()) as queue:
                    counts = Counter(row["status"] for row in queue.list_entries())
                _write(
                    {
                        "status": "ok",
                        "campaigns": {
                            key: counts.get(key, 0)
                            for key in (
                                "PENDING",
                                "SENDING",
                                "CONSUMED",
                                "DEAD",
                            )
                        },
                    }
                )
                return 0
    except (LookupError, PermissionError, ValueError) as exc:
        _write(
            {
                "status": "error",
                "error": type(exc).__name__,
                "message": str(exc),
            }
        )
        return 2
    raise AssertionError(f"unhandled admin command: {args.command}")


def _room_payload(room: Room) -> dict[str, str]:
    return {
        "room_id": room.room_id,
        "approval_status": room.approval_status.value,
    }


def _write(payload: Mapping[str, object]) -> None:
    print(
        json.dumps(
            dict(payload),
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
