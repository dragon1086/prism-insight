"""Regression tests for layered Telegram discussion-room moderation."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from telegram_moderation import (
    _ACTION_ALLOW,
    _ACTION_BAN,
    _ACTION_RESTRICT,
    CommunityModerator,
    ModerationConfig,
    ModerationStore,
    assess_text,
    community_notice,
)


def test_notice_is_readable_and_contains_safety_boundaries():
    notice = community_notice()
    assert "프리즘 인사이트 토론방 이용 안내" in notice
    assert "외부 커뮤니티 홍보 및 초대" in notice
    assert "개인 메시지(DM)" in notice
    assert "운영진은 개인 메시지" in notice
    assert notice.count("\n\n") >= 4


def test_normal_stock_conversation_is_not_flagged():
    assessment = assess_text("하이닉스 275만 원까지 갈 수 있을까요?")
    assert assessment.score == 0
    assert assessment.reasons == ()


def test_external_investment_recruitment_is_high_risk():
    assessment = assess_text(
        "수익보장 리딩방입니다. 오픈채팅 들어오세요 https://open.kakao.com/o/example"
    )
    assert assessment.score >= 10
    assert "외부 커뮤니티 초대 링크" in assessment.reasons
    assert "투자·유료 서비스 홍보 표현" in assessment.reasons


def test_store_persists_identity_and_violation_count(tmp_path):
    store = ModerationStore(tmp_path / "moderation.sqlite")
    assessment = assess_text("오픈채팅 가입하세요 https://open.kakao.com/o/example")
    now = datetime.now(timezone.utc)
    first = store.record_observation(
        chat_id=-100,
        user_id=7,
        message_id=11,
        username="risk_user",
        display_name="Risk User",
        text="오픈채팅 가입하세요 https://open.kakao.com/o/example",
        assessment=assessment,
        is_violation=True,
        now=now,
    )
    second = store.record_observation(
        chat_id=-100,
        user_id=7,
        message_id=12,
        username="risk_user",
        display_name="Risk User",
        text="오픈채팅 가입하세요 https://open.kakao.com/o/example",
        assessment=assessment,
        is_violation=True,
        now=now,
    )
    assert first["violation_count"] == 1
    assert second["violation_count"] == 2
    assert store.recent_suspicious_messages(-100, 7)


def test_join_burst_is_persisted_and_shared_invite_is_counted(tmp_path):
    store = ModerationStore(tmp_path / "moderation.sqlite")
    now = datetime.now(timezone.utc)
    for offset, user_id in enumerate((10, 11, 12)):
        store.record_join_event(
            chat_id=-100,
            user_id=user_id,
            joined_at=now,
            username=f"user{user_id}",
            display_name=f"User {user_id}",
            inviter_id=99,
            invite_link_hint="same-link-hash",
            update_id=offset,
        )
    # Telegram retries can redeliver the same update; it must not inflate the burst.
    store.record_join_event(
        chat_id=-100,
        user_id=10,
        joined_at=now,
        username="user10",
        display_name="User 10",
        inviter_id=99,
        invite_link_hint="same-link-hash",
        update_id=0,
    )

    burst = store.recent_join_burst(-100, now, 15)

    assert burst.count == 3
    assert burst.user_ids == (10, 11, 12)
    assert burst.inviter_ids == (99,)
    assert burst.shared_invite_count == 3
    assert burst.participants == ("@user10", "@user11", "@user12")
    assert store.user_joined_recently(-100, 10, now, 60) is True


def test_recent_join_context_only_increases_an_already_suspicious_message_score():
    ordinary = assess_text("하이닉스 전망이 궁금합니다", joined_recently=True)
    suspicious = assess_text("오픈채팅 가입하세요", joined_recently=True)

    assert ordinary.score == 0
    assert suspicious.score >= 5
    assert "집단 유입 직후 활동" in suspicious.reasons


def test_decision_ladder_deletes_then_restricts_then_optionally_bans():
    config = ModerationConfig(
        enabled=True,
        target_chat_id=-100,
        auto_restrict=True,
        auto_ban=True,
        ban_after_violations=3,
    )
    moderator = CommunityModerator(config)
    assessment = assess_text(
        "수익보장 리딩방 오픈채팅 https://open.kakao.com/o/example"
    )
    assert moderator._decide(assessment, 1, None).action == _ACTION_RESTRICT
    assert moderator._decide(assessment, 2, None).action == _ACTION_RESTRICT
    assert moderator._decide(
        assessment,
        3,
        {"label": "high_risk", "confidence": 0.99, "reason": "반복 모집"},
    ).action == _ACTION_BAN


def test_benign_llm_verdict_can_clear_borderline_hit():
    config = ModerationConfig(enabled=True, target_chat_id=-100)
    moderator = CommunityModerator(config)
    assessment = assess_text("외부방 이야기는 하지 말아주세요", duplicate_count=1)
    decision = moderator._decide(
        assessment,
        1,
        {"label": "benign", "confidence": 0.95, "reason": "경고 문장"},
    )
    assert decision.action == _ACTION_ALLOW


class _FakeBot:
    def __init__(self):
        self.restricted = []
        self.alerts = []

    async def get_chat_member(self, chat_id, user_id):
        return SimpleNamespace(status="member")

    async def restrict_chat_member(self, **kwargs):
        self.restricted.append(kwargs)

    async def send_message(self, **kwargs):
        self.alerts.append(kwargs)


class _FakeMessage:
    message_id = 1
    text = "수익보장 리딩방 오픈채팅 https://open.kakao.com/o/example"
    caption = None

    def __init__(self):
        self.deleted = False

    async def delete(self):
        self.deleted = True


class _FakeLLMClient:
    def __init__(self, **kwargs):
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    async def _create(self, **kwargs):
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"label":"high_risk","confidence":0.94,"reason":"외부방 모집"}'
                    )
                )
            ]
        )

    async def close(self):
        return None


@pytest.mark.asyncio
async def test_llm_review_parses_structured_verdict(monkeypatch):
    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", _FakeLLMClient)
    monkeypatch.setattr(
        "cores.llm.capabilities.resolve_openai_api_key", lambda: "test-key"
    )
    config = ModerationConfig(
        enabled=True, target_chat_id=-100, llm_enabled=True
    )
    moderator = CommunityModerator(config)

    result = await moderator._llm_review(
        "오픈채팅으로 오세요",
        assess_text("오픈채팅으로 오세요"),
        [],
    )

    assert result == {
        "label": "high_risk",
        "confidence": 0.94,
        "reason": "외부방 모집",
    }


@pytest.mark.asyncio
async def test_flagged_message_is_deleted_restricted_and_reported(tmp_path):
    config = ModerationConfig(
        enabled=True,
        target_chat_id=-100,
        admin_chat_id=-200,
        db_path=str(tmp_path / "moderation.sqlite"),
        llm_enabled=False,
    )
    moderator = CommunityModerator(config, ModerationStore(config.db_path))
    message = _FakeMessage()
    user = SimpleNamespace(
        id=7, username="risk_user", full_name="Risk User", is_bot=False
    )
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=-100, type="supergroup"),
        effective_message=message,
        effective_user=user,
    )
    bot = _FakeBot()

    handled = await moderator.moderate_update(bot, update)

    assert handled is True
    assert message.deleted is True
    assert len(bot.restricted) == 1
    assert len(bot.alerts) == 1


@pytest.mark.asyncio
async def test_join_update_alerts_operator_without_restricting_new_member(tmp_path):
    config = ModerationConfig(
        enabled=True,
        target_chat_id=-100,
        admin_chat_id=-200,
        db_path=str(tmp_path / "moderation.sqlite"),
        join_alert_threshold=3,
    )
    moderator = CommunityModerator(config, ModerationStore(config.db_path))
    bot = _FakeBot()
    for update_id, user_id in enumerate((10, 11, 12), start=1):
        user = SimpleNamespace(
            id=user_id,
            username=f"user{user_id}",
            full_name=f"User {user_id}",
            is_bot=False,
        )
        change = SimpleNamespace(
            chat=SimpleNamespace(id=-100, type="supergroup"),
            old_chat_member=SimpleNamespace(status="left", user=user),
            new_chat_member=SimpleNamespace(status="member", user=user),
            from_user=SimpleNamespace(id=99),
            invite_link=SimpleNamespace(invite_link="https://t.me/+shared"),
        )
        update = SimpleNamespace(chat_member=change, update_id=update_id)
        await moderator.moderate_chat_member_update(bot, update)

    assert len(bot.alerts) == 1
    assert "신규 유입 급증" in bot.alerts[0]["text"]
    assert bot.restricted == []
