from __future__ import annotations

from types import SimpleNamespace

import pytest
import telegram

from telegram_config import (
    build_buy_analysis_failure_alert,
    send_buy_analysis_failure_alert,
)


def test_public_failure_alert_removes_internal_paths_and_error_codes() -> None:
    message = build_buy_analysis_failure_alert(
        failed=1,
        total=3,
        market="KR",
        detail=(
            "/root/prism-insight/pdf_reports/"
            "044490_태웅_20260902_afternoon_gpt-5.6-luna.pdf: "
            "scenario_llm_empty_or_invalid"
        ),
    )

    assert "태웅(044490)" in message
    assert "매매 시나리오 생성 실패" in message
    assert "운영 시스템에서 자동 확인 중" in message
    for forbidden in (
        "/root",
        "pdf_reports",
        ".pdf",
        ".log",
        "gpt-5.6",
        "scenario_llm_empty_or_invalid",
    ):
        assert forbidden not in message


@pytest.mark.asyncio
async def test_failure_alert_returns_and_logs_telegram_message_id(
    monkeypatch, caplog
) -> None:
    sent = []

    class FakeBot:
        def __init__(self, **_kwargs):
            pass

        async def send_message(self, **kwargs):
            sent.append(kwargs)
            return SimpleNamespace(message_id=9182)

    monkeypatch.setattr(telegram, "Bot", FakeBot)
    config = SimpleNamespace(
        use_telegram=True,
        bot_token="redacted-test-token",
        channel_id="channel-1",
    )

    with caplog.at_level("INFO"):
        message_id = await send_buy_analysis_failure_alert(
            config,
            failed=1,
            total=3,
            market="KR",
            detail="044490_태웅_report.pdf: scenario_llm_empty_or_invalid",
        )

    assert message_id == 9182
    assert sent[0]["chat_id"] == "channel-1"
    assert "message_id=9182" in caplog.text
    assert ".pdf" not in sent[0]["text"]
