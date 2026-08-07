from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from kakao_bot.adapters.kakao.campaign_renderer import render_campaign_delivery
from kakao_bot.adapters.persistence.sqlite import SQLiteKakaoRepository
from kakao_bot.domain.models import ApprovalStatus, ClaimedOutboundDelivery
from kakao_bot.runtime.campaign_consumer_main import (
    ConsumerRuntimeConfig,
    run_consumer_once,
)
from messaging.batch_campaign_publisher import (
    build_batch_decision_event,
    build_batch_portfolio_event,
    build_batch_report_event,
)
from messaging.local_campaign_queue import SQLiteBatchCampaignQueue

NOW = datetime(2026, 8, 7, 6, 30, tzinfo=timezone.utc)


def _common() -> dict[str, object]:
    return {
        "market": "KR",
        "session": "AFTERNOON",
        "trade_date": "20260807",
        "regime": "UPTREND",
        "occurred_at": NOW,
    }


def test_story_event_ids_are_stable_and_report_requires_pdf():
    report = build_batch_report_event(
        **_common(),
        ticker="005930",
        company_name="삼성전자",
        summary="핵심 요약",
        artifact_path="/srv/reports/005930.pdf",
    )
    decision = build_batch_decision_event(**_common(), message="신규 진입 보류")
    portfolio = build_batch_portfolio_event(
        **_common(), message="배치 종료 후 가격 기준 포트폴리오"
    )

    assert report["campaign_id"] == "kr-afternoon-2026-08-07"
    assert report["event_id"] == "kr-afternoon-2026-08-07:report:005930"
    assert report["event_type"] == "BATCH_CAMPAIGN_REPORT_READY"
    assert decision["event_id"].endswith(":decision")
    assert portfolio["event_id"].endswith(":portfolio")


def test_consumer_plans_report_decision_portfolio_in_event_order(tmp_path, monkeypatch):
    queue_path = tmp_path / "campaigns.sqlite"
    database_path = tmp_path / "kakao.sqlite"
    artifact = tmp_path / "005930.pdf"
    artifact.write_bytes(b"%PDF-1.7 test")
    monkeypatch.setenv("KAKAO_BOT_PUBLIC_BASE_URL", "https://bot.example")
    monkeypatch.setenv("KAKAO_REPORT_LINK_TTL_HOURS", "72")

    with SQLiteKakaoRepository(database_path) as repository:
        repository.discover_room("room-1")
        repository.set_room_approval("room-1", ApprovalStatus.APPROVED)
    with SQLiteBatchCampaignQueue(queue_path) as queue:
        queue.enqueue(
            build_batch_report_event(
                **_common(),
                ticker="005930",
                company_name="삼성전자",
                summary="실제 텔레그램 요약",
                artifact_path=str(artifact),
            )
        )
        queue.enqueue(
            build_batch_decision_event(**_common(), message="가상운용 판단: 관망")
        )
        queue.enqueue(
            build_batch_portfolio_event(
                **_common(), message="배치 종료 후 가격 기준 포트폴리오"
            )
        )

    result = run_consumer_once(
        ConsumerRuntimeConfig(
            queue_path=queue_path,
            database_path=database_path,
            lease_owner="consumer-1",
        ),
        now=NOW,
    )

    assert result.consumed == 3
    with SQLiteKakaoRepository(database_path) as repository:
        deliveries = repository.list_outbox()
        assert [row["message_type"] for row in deliveries] == [
            "campaign_report",
            "campaign_decision",
            "campaign_portfolio",
        ]
        report_url = deliveries[0]["payload"]["pdf_url"]
        assert report_url.startswith("https://bot.example/kakao/reports/")
        token = report_url.rsplit("/", 1)[-1]
        assert repository.resolve_report_link(token, now=NOW) == str(artifact)
        assert (
            repository.resolve_report_link(token, now=NOW + timedelta(hours=73)) is None
        )


def _claimed(message_type: str, payload: dict[str, object]):
    return ClaimedOutboundDelivery(
        delivery_key="story:1",
        room_id="room-1",
        message_type=message_type,
        payload=payload,
        attempt_count=1,
        lease_owner="sender-1",
        lease_expires_at=NOW,
        created_at=NOW,
    )


def test_story_renderers_are_transparent_and_noninteractive():
    report = render_campaign_delivery(
        _claimed(
            "campaign_report",
            {
                "ticker": "005930",
                "company_name": "삼성전자",
                "message": "실제 텔레그램 요약",
                "pdf_url": "https://bot.example/kakao/reports/token",
            },
        )
    )
    decision = render_campaign_delivery(
        _claimed("campaign_decision", {"message": "가상운용 판단: 관망"})
    )
    portfolio = render_campaign_delivery(
        _claimed(
            "campaign_portfolio",
            {"message": "배치 종료 후 가격 기준 포트폴리오"},
        )
    )

    report_text = str(report)
    assert "실제 텔레그램 요약" in report_text
    assert "전체 리포트" in report_text
    assert "messageText" not in report_text
    assert "가상운용" in str(decision)
    assert "실시간" not in str(portfolio)
    assert "배치 종료 후 가격 기준" in str(portfolio)


def test_decision_event_can_be_keyed_per_stock_without_colliding():
    first = build_batch_decision_event(
        **_common(), decision_key="01", message="삼성전자 관망"
    )
    second = build_batch_decision_event(
        **_common(), decision_key="02", message="SK하이닉스 진입 보류"
    )

    assert first["campaign_id"] == second["campaign_id"]
    assert first["event_id"].endswith(":decision:01")
    assert second["event_id"].endswith(":decision:02")
