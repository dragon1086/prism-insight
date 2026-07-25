"""Explicit one-message Kakao REST contract smoke utility."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Sequence
from dataclasses import dataclass, field

from kakao_bot.adapters.kakao.rest_client import DEFAULT_BASE_URL, KakaoRestClient
from kakao_bot.adapters.kakao.skill_response import simple_text
from kakao_bot.domain.models import MessageSendResult
from kakao_bot.ports.repositories import KakaoMessageSender


@dataclass(frozen=True)
class RestSmokeConfig:
    token: str = field(repr=False)
    room_id: str
    message: str = "PRISM Kakao 봇 연결 확인"
    base_url: str = DEFAULT_BASE_URL


async def run_smoke(
    config: RestSmokeConfig,
    *,
    sender: KakaoMessageSender | None = None,
) -> MessageSendResult:
    resolved_sender = sender or KakaoRestClient(
        config.token,
        base_url=config.base_url,
        max_attempts=1,
    )
    return await resolved_sender.send_message(
        config.room_id,
        simple_text(config.message),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send one explicit Kakao REST contract smoke message"
    )
    parser.add_argument("--room-id", required=True)
    parser.add_argument(
        "--message",
        default="PRISM Kakao 봇 연결 확인",
    )
    parser.add_argument(
        "--confirm-send",
        action="store_true",
        help="Required acknowledgement that this sends a real message",
    )
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.confirm_send:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "--confirm-send is required",
                },
                ensure_ascii=False,
            )
        )
        return 2
    token = os.getenv("KAKAO_BOT_TOKEN", "").strip()
    if not token:
        print(
            json.dumps(
                {
                    "status": "error",
                    "message": "KAKAO_BOT_TOKEN is required",
                },
                ensure_ascii=False,
            )
        )
        return 2
    result = await run_smoke(
        RestSmokeConfig(
            token=token,
            room_id=args.room_id,
            message=args.message,
            base_url=os.getenv("KAKAO_REST_BASE_URL", DEFAULT_BASE_URL),
        )
    )
    print(
        json.dumps(
            {
                "status": "ok" if result.success else "error",
                "http_status": result.status_code,
                "error_code": (getattr(result.error_code, "value", result.error_code)),
                "error_message": result.error_message,
            },
            ensure_ascii=False,
        )
    )
    return 0 if result.success else 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
