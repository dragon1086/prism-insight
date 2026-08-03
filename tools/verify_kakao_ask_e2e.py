"""End-to-end check of the Kakao `질문` (ask) command.

Drives the real runtime path with the real classes:

    Gateway dispatch (MESSAGE_CREATE)
      -> GatewayDispatchHandler -> GatewayInboundService   (room + dedupe)
      -> MessageCommandHandler  -> CommandService          (gate, quota, enqueue)
      -> AnalysisService.run_once -> PrismReportAdapter.answer()
                                     -> Firecrawl + LLM + daily_facts grounding
      -> outbox -> DeliveryService (sender_main) -> rendered SkillResponse

Only two things are substituted, and only because they are outside the process:

  * the Kakao REST transport — sending needs a real bot token and would post
    into a live chat room. The fakes capture exactly the JSON the real client
    would have POSTed, so the rendering is still the production rendering.
  * the ticker resolver — ask never touches it; a spy proves that.

Everything else, including the Firecrawl call, is real. Pass `--offline` to
stub the retrieval layer when you only want to check the plumbing.

Usage:
    .venv/bin/python tools/verify_kakao_ask_e2e.py
    .venv/bin/python tools/verify_kakao_ask_e2e.py --offline
    .venv/bin/python tools/verify_kakao_ask_e2e.py --question "..."
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from kakao_bot.adapters.kakao.gateway_inbound_handler import GatewayDispatchHandler
from kakao_bot.adapters.kakao.gateway_protocol import GatewayDispatch
from kakao_bot.adapters.kakao.message_command_handler import MessageCommandHandler
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.application.analysis_service import AnalysisService
from kakao_bot.application.command_service import CommandService
from kakao_bot.application.gateway_inbound_service import GatewayInboundService
from kakao_bot.domain.models import ApprovalStatus, MessageSendResult
from kakao_bot.ports.analysis import TickerResolution
from kakao_bot.runtime.sender_main import SenderRuntimeConfig, run_sender_once

ROOM = "e2e-room"
USER = "e2e-user"
DEFAULT_QUESTION = "오늘 코스피 시장 분위기 어때?"

_checks: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    _checks.append((ok, label))
    mark = "PASS" if ok else "FAIL"
    print(f"  [{mark}] {label}" + (f" — {detail}" if detail else ""))


class SpyResolver:
    """Ask must never reach ticker resolution."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def resolve(self, query: str, *, market: str | None) -> TickerResolution:
        self.calls.append(query)
        return TickerResolution(error_message="resolver should not run for ask")


class CapturingCallbackSender:
    """Stands in for KakaoRestClient.callback (the immediate ack)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def callback(self, callback_token: str, skill_response: dict):
        self.calls.append((callback_token, skill_response))
        return MessageSendResult(success=True, status_code=200)


class CapturingMessageSender:
    """Stands in for KakaoRestClient.send_message (the outbox delivery)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def send_message(self, room_id: str, skill_response: dict):
        self.calls.append((room_id, skill_response))
        return MessageSendResult(success=True, status_code=200)


def dispatch(sequence: int, event_id: str, content: str) -> GatewayDispatch:
    return GatewayDispatch(
        sequence=sequence,
        event_type="MESSAGE_CREATE",
        data={
            "id": event_id,
            "isChannelChatroom": False,
            "botGroupKey": ROOM,
            "botUserKey": USER,
            "content": content,
            "timestamp": datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.%f000Z"
            ),
            "callbackToken": "e2e-callback-token",
        },
    )


def install_offline_stubs() -> None:
    """Replace only the outbound network calls, keeping every seam real."""

    import cores.market_facts_cache as mfc
    import report_generator as rg

    async def fake_daily_facts(market):
        return {
            "grounded_facts": "[확정값] KOSPI 3,120.45 (-0.82%)",
            "period_label": "2026-08-03",
        }

    async def fake_search(search_query, analysis_prompt, **kwargs):
        assert "grounded_facts" in kwargs, "grounding was not passed through"
        return (
            "📉 오늘 코스피는 외국인 순매도가 이어지며 약세였습니다.\n\n"
            "- **지수**: 3,120.45 (-0.82%)\n"
            "- 반도체 업종이 지수 하락을 주도했습니다.\n"
            "- 금리 우려가 투자심리를 눌렀습니다."
        )

    mfc.daily_facts = fake_daily_facts
    rg.generate_firecrawl_search_response = fake_search


def text_of(response: dict) -> str:
    for output in response["template"]["outputs"]:
        if "simpleText" in output:
            return output["simpleText"]["text"]
    return ""


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument(
        "--offline",
        action="store_true",
        help="stub Firecrawl/daily_facts; exercise the plumbing only",
    )
    parser.add_argument(
        "--no-grounding",
        action="store_true",
        help=(
            "make daily_facts return {} (its own documented degraded mode). "
            "Needed to run live on Python 3.14: importing krx_data_client "
            "applies nest_asyncio, which breaks the OpenAI SDK on 3.14. "
            "Production runs 3.12 (see Dockerfile) and is unaffected."
        ),
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.offline:
        install_offline_stubs()
    elif args.no_grounding:
        import cores.market_facts_cache as mfc

        async def no_facts(market):
            return {}

        mfc.daily_facts = no_facts

    from kakao_bot.adapters.prism.report_adapter import PrismReportAdapter

    tmp = Path(tempfile.mkdtemp(prefix="kakao-ask-e2e-"))
    db = tmp / "kakao_bot.sqlite"
    if args.offline:
        mode = "OFFLINE (stubbed retrieval)"
    elif args.no_grounding:
        mode = "LIVE, no grounding (real Firecrawl + real LLM)"
    else:
        mode = "LIVE (real Firecrawl + real LLM + KRX grounding)"
    print(f"\n=== Kakao ask E2E — {mode} ===")
    print(f"질문: {args.question}\nDB: {db}\n")

    resolver = SpyResolver()
    callback_sender = CapturingCallbackSender()

    # ---- 1. inbound: a real gateway dispatch becomes an enqueued job --------
    print("[1/4] Gateway dispatch → command → ack")
    with SQLiteKakaoRepository(db) as repository:
        repository.discover_room(ROOM)
        repository.set_room_approval(ROOM, ApprovalStatus.APPROVED)

        handler = GatewayDispatchHandler(
            GatewayInboundService(repository),
            message_handler=MessageCommandHandler(
                CommandService(repository, resolver),
                callback_sender,
            ),
        )
        await handler(dispatch(1, "evt-1", f"질문 {args.question}"))

        check(len(callback_sender.calls) == 1, "the room got an immediate ack")
        ack = text_of(callback_sender.calls[0][1]) if callback_sender.calls else ""
        check(bool(ack.strip()), "ack is not empty", ack.replace("\n", " ")[:60])
        check(resolver.calls == [], "ticker resolver was never called")

        jobs = repository.list_analysis_jobs()
        check(len(jobs) == 1, "exactly one job was enqueued")
        if jobs:
            check(jobs[0]["kind"] == "ask", "job kind is 'ask'", jobs[0]["kind"])
            stored = jobs[0]["payload"] or {}
            check(
                stored.get("question") == args.question,
                "the question survived the round trip",
            )

        # Redelivery must not enqueue a second job.
        await handler(dispatch(1, "evt-1", f"질문 {args.question}"))
        check(
            len(repository.list_analysis_jobs()) == 1,
            "a redelivered event did not double-enqueue",
        )

    # ---- 2. worker: the real adapter answers the question -------------------
    print("\n[2/4] Analysis worker → PrismReportAdapter.answer()")
    started = time.monotonic()
    with SQLiteKakaoRepository(db) as repository:
        service = AnalysisService(repository, PrismReportAdapter())
        result = await asyncio.to_thread(
            service.run_once, lease_seconds=900, limit=1
        )
    elapsed = time.monotonic() - started

    check(result.claimed == 1, "the worker claimed the job")
    check(
        result.completed == 1,
        "the job completed",
        f"completed={result.completed} failed={result.failed} in {elapsed:.1f}s",
    )
    print(f"       answer latency: {elapsed:.1f}s")

    with SQLiteKakaoRepository(db) as repository:
        [job] = repository.list_analysis_jobs()
        check(job["status"] == "COMPLETED", "job row is COMPLETED", job["status"])
        if job["status"] != "COMPLETED":
            print(f"       error_code: {job['error_code']}")
            print(f"       summary: {(job['summary'] or '')[:300]}")
        [out] = repository.list_outbox()
        check(
            out["message_type"] == "ask_result",
            "outbox holds an ask_result",
            out["message_type"],
        )

    # ---- 3. sender: the outbox row renders into a Kakao SkillResponse -------
    print("\n[3/4] Sender → rendered SkillResponse")
    message_sender = CapturingMessageSender()
    sent = await run_sender_once(
        SenderRuntimeConfig(token="e2e-token", database_path=db),
        sender=message_sender,
    )
    check(sent.sent == 1, "the delivery was sent", f"claimed={sent.claimed}")
    check(len(message_sender.calls) == 1, "the transport received one message")

    if not message_sender.calls:
        return 1
    room_id, response = message_sender.calls[0]
    check(room_id == ROOM, "delivered to the right room")

    body = text_of(response)
    check(args.question[:12] in body, "the card echoes the question")
    check(len(body) > 40, "the answer has real content", f"{len(body)} chars")
    check(len(body) <= 1000, "the bubble fits Kakao's SimpleText limit")
    check("**" not in body and "##" not in body, "markdown was stripped")

    outputs = response["template"]["outputs"]
    check(len(outputs) == 2, "a follow-up card came with it")
    card = outputs[1].get("listCard", {})
    check(
        [i["title"] for i in card.get("items", [])] == ["도움말"],
        "follow-ups offer no ticker actions",
    )

    # ---- 4. show the actual bubble ------------------------------------------
    print("\n[4/4] What the room actually sees")
    print("-" * 68)
    print(body)
    print("-" * 68)
    print(json.dumps(outputs[1], ensure_ascii=False, indent=2)[:600])

    failed = [label for ok, label in _checks if not ok]
    print(f"\n=== {len(_checks) - len(failed)}/{len(_checks)} checks passed ===")
    if failed:
        for label in failed:
            print(f"  FAILED: {label}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
