"""Layered moderation for the PRISM-INSIGHT Telegram discussion room.

The moderation path is deliberately fail-open:

1. Deterministic signals inspect links, recruitment language and per-user spam.
2. Suspicious candidates are persisted and may be reviewed by an LLM.
3. The LLM can raise or lower the *review priority*, but a single ambiguous
   message never causes a permanent ban.
4. The operator receives an alert before a delete/restrict/ban action.

The module is inert until both ``TELEGRAM_MODERATION_ENABLED=true`` and an
explicit ``TELEGRAM_DISCUSSION_CHAT_ID`` are configured.  This prevents a bot
restart from accidentally moderating a different chat or channel.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import sqlite3
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from telegram import ChatPermissions

logger = logging.getLogger(__name__)

_ACTION_ALLOW = "allow"
_ACTION_DELETE = "delete"
_ACTION_RESTRICT = "restrict"
_ACTION_BAN = "ban"
_ADMIN_STATUSES = frozenset({"administrator", "creator", "owner"})


COMMUNITY_NOTICE = """📌 프리즘 인사이트 토론방 이용 안내

이곳은 오픈소스 PRISM-INSIGHT와 주식·AI 등 다양한 주제를 자유롭게 이야기하는 공개 토론방입니다. 가벼운 잡담도 괜찮습니다. 🙂

다만 모두가 안전하게 이용할 수 있도록 아래 행위는 금지합니다.

🚫 리딩방·투자방·오픈채팅 등 외부 커뮤니티 홍보 및 초대
🚫 투자상품·유료 서비스·추천인 코드 등의 광고 및 홍보
🚫 참여자에게 개인 메시지(DM)로 투자 권유, 입금 요청 또는 외부방 가입 유도
🚫 반복 도배, 스팸, 사칭 및 악의적인 분쟁 유발

위반 메시지는 별도 경고 없이 삭제될 수 있으며, 반복적이거나 위험도가 높은 행위에는 일시적인 이용 제한 또는 퇴장 조치가 적용될 수 있습니다.

⚠️ 프리즘 인사이트 운영진은 개인 메시지를 통해 투자 권유, 입금 요청 또는 별도의 투자방 가입을 권유하지 않습니다.
의심스러운 DM이나 링크를 받았다면 응답하거나 송금하지 말고 운영자에게 알려주세요."""


def community_notice() -> str:
    """Return the reviewed, human-readable community notice."""
    return COMMUNITY_NOTICE


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _chat_id(value: str | None) -> str | int | None:
    if not value or not value.strip():
        return None
    raw = value.strip()
    try:
        return int(raw)
    except ValueError:
        return raw


def _id_set(*values: str | None) -> frozenset[int]:
    result: set[int] = set()
    for value in values:
        for part in (value or "").split(","):
            try:
                result.add(int(part.strip()))
            except (TypeError, ValueError):
                continue
    return frozenset(result)


@dataclass(frozen=True)
class ModerationConfig:
    enabled: bool = False
    target_chat_id: str | int | None = None
    admin_chat_id: str | int | None = None
    admin_ids: frozenset[int] = frozenset()
    db_path: str = "telegram_moderation.sqlite"
    announcement_interval_hours: float = 24.0
    llm_enabled: bool = False
    llm_model: str = "gpt-5.4-mini"
    llm_min_score: int = 4
    delete_score: int = 5
    restrict_score: int = 8
    restrict_minutes: int = 60
    repeat_restrict_minutes: int = 1440
    auto_restrict: bool = True
    auto_ban: bool = False
    ban_after_violations: int = 3
    admin_alert_cooldown_seconds: int = 60
    join_alert_window_minutes: int = 15
    join_alert_threshold: int = 5
    join_alert_cooldown_seconds: int = 900
    join_context_minutes: int = 60

    @classmethod
    def from_env(cls) -> ModerationConfig:
        return cls(
            enabled=_env_bool("TELEGRAM_MODERATION_ENABLED", False),
            target_chat_id=_chat_id(
                os.getenv("TELEGRAM_DISCUSSION_CHAT_ID")
                or os.getenv("TELEGRAM_MODERATION_CHAT_ID")
            ),
            admin_chat_id=_chat_id(os.getenv("TELEGRAM_MODERATION_ADMIN_CHAT_ID")),
            admin_ids=_id_set(
                os.getenv("TELEGRAM_ADMIN_IDS"),
                os.getenv("TELEGRAM_MODERATION_ADMIN_IDS"),
            ),
            db_path=os.getenv(
                "TELEGRAM_MODERATION_DB_PATH", "telegram_moderation.sqlite"
            ),
            announcement_interval_hours=max(
                1.0, _env_float("TELEGRAM_MODERATION_ANNOUNCEMENT_HOURS", 24.0)
            ),
            llm_enabled=_env_bool("TELEGRAM_MODERATION_LLM_ENABLED", False),
            llm_model=os.getenv("TELEGRAM_MODERATION_LLM_MODEL", "gpt-5.4-mini"),
            llm_min_score=max(1, _env_int("TELEGRAM_MODERATION_LLM_MIN_SCORE", 4)),
            delete_score=max(1, _env_int("TELEGRAM_MODERATION_DELETE_SCORE", 5)),
            restrict_score=max(1, _env_int("TELEGRAM_MODERATION_RESTRICT_SCORE", 8)),
            restrict_minutes=max(
                1, _env_int("TELEGRAM_MODERATION_RESTRICT_MINUTES", 60)
            ),
            repeat_restrict_minutes=max(
                1, _env_int("TELEGRAM_MODERATION_REPEAT_RESTRICT_MINUTES", 1440)
            ),
            auto_restrict=_env_bool("TELEGRAM_MODERATION_AUTO_RESTRICT", True),
            auto_ban=_env_bool("TELEGRAM_MODERATION_AUTO_BAN", False),
            ban_after_violations=max(
                2, _env_int("TELEGRAM_MODERATION_BAN_AFTER_VIOLATIONS", 3)
            ),
            admin_alert_cooldown_seconds=max(
                0, _env_int("TELEGRAM_MODERATION_ALERT_COOLDOWN_SECONDS", 60)
            ),
            join_alert_window_minutes=max(
                1, _env_int("TELEGRAM_MODERATION_JOIN_WINDOW_MINUTES", 15)
            ),
            join_alert_threshold=max(
                2, _env_int("TELEGRAM_MODERATION_JOIN_ALERT_THRESHOLD", 5)
            ),
            join_alert_cooldown_seconds=max(
                60, _env_int("TELEGRAM_MODERATION_JOIN_ALERT_COOLDOWN_SECONDS", 900)
            ),
            join_context_minutes=max(
                1, _env_int("TELEGRAM_MODERATION_JOIN_CONTEXT_MINUTES", 60)
            ),
        )

    @property
    def active(self) -> bool:
        return self.enabled and self.target_chat_id is not None


@dataclass(frozen=True)
class ModerationAssessment:
    score: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ModerationDecision:
    action: str
    score: int
    reasons: tuple[str, ...]
    violation_count: int = 0
    duration_minutes: int | None = None
    llm_label: str | None = None
    llm_confidence: float | None = None


@dataclass(frozen=True)
class JoinBurst:
    """Recent join activity grouped into one room-level incident candidate."""

    count: int
    user_ids: tuple[int, ...]
    inviter_ids: tuple[int, ...]
    shared_invite_count: int
    participants: tuple[str, ...] = ()


_INVITE_LINK_RE = re.compile(
    r"(?:https?://)?(?:t\.me/(?:\+|joinchat/)|telegram\.me/|"
    r"open\.kakao\.com/|chat\.whatsapp\.com/|discord(?:\.gg|\.com/invite)/)",
    re.IGNORECASE,
)
_EXTERNAL_ROOM_RE = re.compile(
    r"리딩\s*방|투자\s*방|오픈\s*채팅|단톡\s*방|텔레그램\s*방|"
    r"카톡\s*방|디스코드|외부\s*(?:방|채널|커뮤니티)|무료\s*방|vip\s*방",
    re.IGNORECASE,
)
_DIRECT_CONTACT_RE = re.compile(
    r"(?:\bdm\b|디엠|개인\s*(?:톡|메시지)|쪽지|연락\s*(?:주세요|바랍니다)|"
    r"문의\s*주세요|친구\s*추가|메시지\s*주세요)",
    re.IGNORECASE,
)
_PROMOTION_RE = re.compile(
    r"수익\s*보장|원금\s*보장|확정\s*수익|고수익|추천인|추천\s*코드|"
    r"입금|송금|가입\s*(?:하세요|해\s*주세요)|선착순|vip\s*회원|"
    r"급등주\s*추천|무료\s*추천",
    re.IGNORECASE,
)
_FINANCIAL_RECRUITMENT_RE = re.compile(
    r"(?:코인|선물|주식|투자|수익).{0,24}(?:모집|회원|방|가입|추천|입금)|"
    r"(?:모집|회원|방|가입|추천|입금).{0,24}(?:코인|선물|주식|투자|수익)",
    re.IGNORECASE,
)
_CONTACT_DETAIL_RE = re.compile(
    r"(?:01[016789]-?\d{3,4}-?\d{4}|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,})"
)


def normalize_text(text: str) -> str:
    """Normalize text for hashing/pattern detection without storing full content."""
    return re.sub(r"\s+", " ", (text or "").replace("\u200b", "").strip().lower())


def assess_text(
    text: str,
    *,
    duplicate_count: int = 0,
    burst_count: int = 0,
    joined_recently: bool = False,
) -> ModerationAssessment:
    """Score policy-relevant behavior; ordinary stock conversation scores zero."""
    normalized = normalize_text(text)
    score = 0
    reasons: list[str] = []

    if _INVITE_LINK_RE.search(normalized):
        score += 4
        reasons.append("외부 커뮤니티 초대 링크")
    if _EXTERNAL_ROOM_RE.search(normalized):
        score += 3
        reasons.append("외부방·리딩방 유도 표현")
    if _DIRECT_CONTACT_RE.search(normalized):
        score += 2
        reasons.append("개인 연락 유도")
    if _PROMOTION_RE.search(normalized):
        score += 3
        reasons.append("투자·유료 서비스 홍보 표현")
    if _FINANCIAL_RECRUITMENT_RE.search(normalized):
        score += 3
        reasons.append("금융상품 모집 패턴")
    if _CONTACT_DETAIL_RE.search(normalized):
        score += 2
        reasons.append("연락처 포함")
    if duplicate_count >= 3:
        score += 5
        reasons.append("동일 메시지 반복 도배")
    elif duplicate_count >= 2:
        score += 2
        reasons.append("동일 메시지 반복")
    if burst_count >= 8:
        score += 5
        reasons.append("단시간 다량 발송")
    elif burst_count >= 5:
        score += 2
        reasons.append("단시간 반복 발송")
    if joined_recently and score > 0:
        score += 2
        reasons.append("집단 유입 직후 활동")

    return ModerationAssessment(score=score, reasons=tuple(dict.fromkeys(reasons)))


class ModerationStore:
    """Small SQLite audit store for identities, observations and actions."""

    def __init__(self, db_path: str | Path):
        self.path = Path(db_path)
        if self.path.parent != Path(""):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.path), timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS moderation_users (
                    chat_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    first_seen_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    message_count INTEGER NOT NULL DEFAULT 0,
                    violation_count INTEGER NOT NULL DEFAULT 0,
                    risk_score REAL NOT NULL DEFAULT 0,
                    last_action TEXT,
                    PRIMARY KEY (chat_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS moderation_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    text_hash TEXT NOT NULL,
                    text_excerpt TEXT NOT NULL DEFAULT '',
                    deterministic_score INTEGER NOT NULL DEFAULT 0,
                    deterministic_reasons TEXT NOT NULL DEFAULT '[]',
                    llm_label TEXT,
                    llm_confidence REAL,
                    llm_reason TEXT,
                    action TEXT NOT NULL DEFAULT 'allow',
                    UNIQUE(chat_id, message_id)
                );
                CREATE INDEX IF NOT EXISTS idx_moderation_messages_user_time
                    ON moderation_messages(chat_id, user_id, created_at);

                CREATE TABLE IF NOT EXISTS moderation_join_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    chat_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    username TEXT,
                    display_name TEXT,
                    inviter_id INTEGER,
                    invite_link_hint TEXT NOT NULL DEFAULT '',
                    update_id INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_moderation_join_events_chat_time
                    ON moderation_join_events(chat_id, joined_at);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_moderation_join_events_update
                    ON moderation_join_events(chat_id, update_id)
                    WHERE update_id IS NOT NULL;
                """
            )

    def record_observation(
        self,
        *,
        chat_id: str | int,
        user_id: int,
        message_id: int,
        username: str,
        display_name: str,
        text: str,
        assessment: ModerationAssessment,
        is_violation: bool,
        now: datetime,
    ) -> dict[str, Any]:
        normalized = normalize_text(text)
        excerpt = text.strip()[:500] if assessment.score > 0 else ""
        chat_key = str(chat_id)
        stamp = now.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO moderation_messages
                    (chat_id, user_id, message_id, created_at, text_hash,
                     text_excerpt, deterministic_score, deterministic_reasons)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_key,
                    user_id,
                    message_id,
                    stamp,
                    hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    excerpt,
                    assessment.score,
                    json.dumps(assessment.reasons, ensure_ascii=False),
                ),
            )
            row = conn.execute(
                "SELECT id FROM moderation_messages WHERE chat_id=? AND message_id=?",
                (chat_key, message_id),
            ).fetchone()
            conn.execute(
                """
                INSERT INTO moderation_users
                    (chat_id, user_id, username, display_name, first_seen_at,
                     last_seen_at, message_count, violation_count, risk_score)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    last_seen_at=excluded.last_seen_at,
                    message_count=moderation_users.message_count + 1,
                    violation_count=moderation_users.violation_count + excluded.violation_count,
                    risk_score=moderation_users.risk_score + excluded.risk_score
                """,
                (
                    chat_key,
                    user_id,
                    username or None,
                    display_name,
                    stamp,
                    stamp,
                    1 if is_violation else 0,
                    float(assessment.score if is_violation else 0),
                ),
            )
            user_row = conn.execute(
                "SELECT violation_count FROM moderation_users WHERE chat_id=? AND user_id=?",
                (chat_key, user_id),
            ).fetchone()
        return {
            "row_id": int(row["id"]) if row else None,
            "violation_count": int(user_row["violation_count"]) if user_row else 0,
        }

    def recent_suspicious_messages(
        self, chat_id: str | int, user_id: int, limit: int = 3
    ) -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT text_excerpt FROM moderation_messages
                WHERE chat_id=? AND user_id=? AND text_excerpt <> ''
                ORDER BY id DESC LIMIT ?
                """,
                (str(chat_id), user_id, limit),
            ).fetchall()
        return [str(row["text_excerpt"]) for row in rows if row["text_excerpt"]]

    def record_join_event(
        self,
        *,
        chat_id: str | int,
        user_id: int,
        joined_at: datetime,
        username: str,
        display_name: str,
        inviter_id: int | None,
        invite_link_hint: str = "",
        update_id: int | None = None,
    ) -> None:
        """Persist one join event and seed the user's identity record."""
        chat_key = str(chat_id)
        stamp = joined_at.isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO moderation_join_events
                    (chat_id, user_id, joined_at, username, display_name,
                     inviter_id, invite_link_hint, update_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    chat_key,
                    user_id,
                    stamp,
                    username or None,
                    display_name,
                    inviter_id,
                    invite_link_hint[:120],
                    update_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO moderation_users
                    (chat_id, user_id, username, display_name, first_seen_at, last_seen_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, user_id) DO UPDATE SET
                    username=excluded.username,
                    display_name=excluded.display_name,
                    last_seen_at=excluded.last_seen_at
                """,
                (chat_key, user_id, username or None, display_name, stamp, stamp),
            )

    def recent_join_burst(
        self, chat_id: str | int, now: datetime, window_minutes: int
    ) -> JoinBurst:
        since = (now - timedelta(minutes=window_minutes)).isoformat()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, inviter_id, invite_link_hint, username, display_name
                FROM moderation_join_events
                WHERE chat_id=? AND joined_at>=?
                ORDER BY joined_at ASC
                """,
                (str(chat_id), since),
            ).fetchall()
        user_ids = tuple(dict.fromkeys(int(row["user_id"]) for row in rows))
        participants = tuple(
            dict.fromkeys(
                (
                    f"@{row['username']}"
                    if row["username"]
                    else str(row["display_name"] or row["user_id"])
                )
                for row in rows
            )
        )
        inviter_ids = tuple(
            dict.fromkeys(
                int(row["inviter_id"])
                for row in rows
                if row["inviter_id"] is not None
                and int(row["inviter_id"]) not in user_ids
            )
        )
        invite_hints = [
            str(row["invite_link_hint"])
            for row in rows
            if row["invite_link_hint"]
        ]
        shared_invite_count = 0
        if invite_hints:
            counts: dict[str, int] = {}
            for hint in invite_hints:
                counts[hint] = counts.get(hint, 0) + 1
            shared_invite_count = max(counts.values())
        return JoinBurst(
            count=len(rows),
            user_ids=user_ids,
            inviter_ids=inviter_ids,
            shared_invite_count=shared_invite_count,
            participants=participants,
        )

    def user_joined_recently(
        self, chat_id: str | int, user_id: int, now: datetime, window_minutes: int
    ) -> bool:
        since = (now - timedelta(minutes=window_minutes)).isoformat()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM moderation_join_events
                WHERE chat_id=? AND user_id=? AND joined_at>=?
                LIMIT 1
                """,
                (str(chat_id), user_id, since),
            ).fetchone()
        return row is not None

    def recent_pattern_counts(
        self,
        chat_id: str | int,
        user_id: int,
        text: str,
        now: datetime,
    ) -> tuple[int, int]:
        normalized = normalize_text(text)
        text_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
        since = (now - timedelta(minutes=5)).isoformat()
        since_minute = (now - timedelta(minutes=1)).isoformat()
        with self._connect() as conn:
            duplicate = conn.execute(
                """
                SELECT COUNT(*) AS n FROM moderation_messages
                WHERE chat_id=? AND user_id=? AND text_hash=? AND created_at>=?
                """,
                (str(chat_id), user_id, text_hash, since),
            ).fetchone()["n"]
            burst = conn.execute(
                """
                SELECT COUNT(*) AS n FROM moderation_messages
                WHERE chat_id=? AND user_id=? AND created_at>=?
                """,
                (str(chat_id), user_id, since_minute),
            ).fetchone()["n"]
        return int(duplicate) + 1, int(burst) + 1

    def save_llm_result(
        self,
        row_id: int | None,
        *,
        label: str | None,
        confidence: float | None,
        reason: str | None,
        action: str,
    ) -> None:
        if row_id is None:
            return
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE moderation_messages
                SET llm_label=?, llm_confidence=?, llm_reason=?, action=?
                WHERE id=?
                """,
                (label, confidence, reason, action, row_id),
            )
            conn.execute(
                """
                UPDATE moderation_users SET last_action=?
                WHERE (chat_id, user_id) = (
                    SELECT chat_id, user_id FROM moderation_messages WHERE id=?
                )
                """,
                (action, row_id),
            )

    def prune(self, older_than_days: int = 90) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM moderation_messages WHERE created_at < ?", (cutoff,))
            conn.execute("DELETE FROM moderation_join_events WHERE joined_at < ?", (cutoff,))
            conn.execute(
                """
                DELETE FROM moderation_users
                WHERE last_seen_at < ? AND NOT EXISTS (
                    SELECT 1 FROM moderation_messages m
                    WHERE m.chat_id=moderation_users.chat_id
                      AND m.user_id=moderation_users.user_id
                )
                """,
                (cutoff,),
            )


class CommunityModerator:
    """Async Telegram moderation coordinator with deterministic + LLM stages."""

    def __init__(
        self,
        config: ModerationConfig,
        store: ModerationStore | None = None,
    ):
        self.config = config
        self.store = store
        self._state_lock = asyncio.Lock()
        self._recent: dict[tuple[str, int], deque[tuple[datetime, str]]] = defaultdict(
            deque
        )
        self._last_alert: dict[tuple[str, int], datetime] = {}
        self._last_join_burst_alert: dict[str, datetime] = {}

    @classmethod
    def from_env(cls) -> CommunityModerator:
        config = ModerationConfig.from_env()
        store = None
        if config.active:
            try:
                store = ModerationStore(config.db_path)
            except Exception:
                logger.exception("moderation store unavailable")
        if config.enabled and not config.target_chat_id:
            logger.warning(
                "Moderation enabled without TELEGRAM_DISCUSSION_CHAT_ID; actions disabled"
            )
        return cls(config, store)

    @property
    def active(self) -> bool:
        return self.config.active

    def prune_state(self) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=10)
        for key in list(self._recent):
            recent = self._recent[key]
            while recent and recent[0][0] < cutoff:
                recent.popleft()
            if not recent:
                self._recent.pop(key, None)
        for key, stamp in list(self._last_alert.items()):
            if stamp < cutoff:
                self._last_alert.pop(key, None)
        for key, stamp in list(self._last_join_burst_alert.items()):
            if stamp < cutoff:
                self._last_join_burst_alert.pop(key, None)
        if self.store:
            try:
                self.store.prune()
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation store prune failed: %s", exc)

    @staticmethod
    def _is_member_status(member: Any) -> bool:
        status = getattr(member, "status", "")
        if status in _ADMIN_STATUSES or status == "member":
            return True
        return status == "restricted" and bool(getattr(member, "is_member", False))

    async def _notify_join_burst(
        self, bot: Any, chat_id: str | int, burst: JoinBurst
    ) -> None:
        if not self.config.admin_chat_id:
            logger.warning(
                "Join burst detected in %s: %s users in %s minutes",
                chat_id,
                burst.count,
                self.config.join_alert_window_minutes,
            )
            return
        key = str(chat_id)
        now = datetime.now(timezone.utc)
        async with self._state_lock:
            previous = self._last_join_burst_alert.get(key)
            if previous and (
                now - previous
            ).total_seconds() < self.config.join_alert_cooldown_seconds:
                return
            self._last_join_burst_alert[key] = now
        participants = ", ".join(burst.participants[:12])
        inviters = ", ".join(str(item) for item in burst.inviter_ids[:8]) or "확인 안 됨"
        shared = (
            f"동일 초대 링크 {burst.shared_invite_count}명"
            if burst.shared_invite_count >= 2
            else "동일 초대 링크 패턴 없음"
        )
        alert = (
            "🚨 토론방 신규 유입 급증 감지\n"
            f"최근 {self.config.join_alert_window_minutes}분 신규 입장: {burst.count}명\n"
            f"참여자: {participants or '식별 정보 없음'}\n"
            f"초대자 후보: {inviters}\n"
            f"초대 링크: {shared}\n"
            "입장만으로 조치하지 않고 후속 메시지 패턴을 관찰합니다."
        )
        try:
            await bot.send_message(
                chat_id=self.config.admin_chat_id,
                text=alert,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001 — alert failure must not affect joins
            logger.warning("join burst operator alert failed: %s", exc)

    async def moderate_chat_member_update(self, bot: Any, update: Any) -> bool:
        """Record joins and alert on room-level surges; never ban on join alone."""
        if not self.active or not self.store:
            return False
        change = getattr(update, "chat_member", None)
        chat = getattr(change, "chat", None) or getattr(update, "effective_chat", None)
        old_member = getattr(change, "old_chat_member", None)
        new_member = getattr(change, "new_chat_member", None)
        if not change or not chat or not old_member or not new_member:
            return False
        if str(chat.id) != str(self.config.target_chat_id):
            return False
        if self._is_member_status(old_member) or not self._is_member_status(new_member):
            return False
        user = getattr(new_member, "user", None)
        if not user or getattr(user, "is_bot", False):
            return False

        actor = getattr(change, "from_user", None)
        inviter_id = getattr(actor, "id", None)
        if inviter_id == getattr(user, "id", None):
            inviter_id = None
        invite = getattr(change, "invite_link", None)
        invite_value = getattr(invite, "invite_link", None) or ""
        invite_hint = (
            hashlib.sha256(str(invite_value).encode("utf-8")).hexdigest()[:12]
            if invite_value
            else ""
        )
        now = datetime.now(timezone.utc)
        try:
            await asyncio.to_thread(
                self.store.record_join_event,
                chat_id=chat.id,
                user_id=user.id,
                joined_at=now,
                username=getattr(user, "username", "") or "",
                display_name=getattr(user, "full_name", "") or "",
                inviter_id=inviter_id,
                invite_link_hint=invite_hint,
                update_id=getattr(update, "update_id", None),
            )
            burst = await asyncio.to_thread(
                self.store.recent_join_burst,
                chat.id,
                now,
                self.config.join_alert_window_minutes,
            )
        except Exception as exc:  # noqa: BLE001 — join logging must not block the bot
            logger.warning("moderation join event save failed: %s", exc)
            return False
        if burst.count >= self.config.join_alert_threshold:
            await self._notify_join_burst(bot, chat.id, burst)
        return False

    async def _pattern_counts(
        self, chat_id: str | int, user_id: int, text: str, now: datetime
    ) -> tuple[int, int]:
        normalized = normalize_text(text)
        key = (str(chat_id), user_id)
        async with self._state_lock:
            recent = self._recent[key]
            cutoff = now - timedelta(minutes=5)
            while recent and recent[0][0] < cutoff:
                recent.popleft()
            recent.append((now, normalized))
            duplicate = sum(1 for _, value in recent if value == normalized)
            burst = sum(1 for stamp, _ in recent if stamp >= now - timedelta(minutes=1))
        if self.store:
            try:
                stored_duplicate, stored_burst = await asyncio.to_thread(
                    self.store.recent_pattern_counts, chat_id, user_id, text, now
                )
                duplicate = max(duplicate, stored_duplicate)
                burst = max(burst, stored_burst)
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation pattern lookup failed: %s", exc)
        return duplicate, burst

    async def _is_exempt(self, bot: Any, chat_id: str | int, user_id: int) -> bool | None:
        if user_id in self.config.admin_ids:
            return True
        try:
            member = await bot.get_chat_member(chat_id, user_id)
            return getattr(member, "status", "") in _ADMIN_STATUSES
        except Exception as exc:  # noqa: BLE001 — API failure means no moderation action
            logger.warning("moderation admin lookup failed for %s: %s", user_id, exc)
            return None

    async def _llm_review(
        self,
        text: str,
        assessment: ModerationAssessment,
        recent_messages: list[str],
    ) -> dict[str, Any] | None:
        if not self.config.llm_enabled:
            return None
        client = None
        try:
            from openai import AsyncOpenAI

            from cores.llm.capabilities import resolve_openai_api_key

            api_key = resolve_openai_api_key()
            if not api_key:
                logger.info("moderation LLM skipped: no OpenAI key")
                return None
            kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 10.0}
            if os.getenv("PRISM_OPENAI_AUTH_MODE", "api_key") == "chatgpt_oauth":
                try:
                    from cores.chatgpt_proxy.constants import CHATGPT_BASE_URL

                    kwargs["base_url"] = CHATGPT_BASE_URL
                except ImportError:
                    pass
            client = AsyncOpenAI(**kwargs)
            system = (
                "당신은 한국어 공개 투자 토론방의 안전 검토기입니다. "
                "정치성향, 나이, 이름, 말투만으로 위험하다고 추정하지 말고 "
                "외부방 유도·투자 권유·입금 요청·사칭·반복 스팸 같은 행동 증거만 평가하세요. "
                "일반적인 종목 질문이나 인사말은 benign입니다. "
                "반드시 JSON 하나만 반환하세요: "
                '{"label":"benign|suspicious|high_risk",'
                '"confidence":0.0,"reason":"짧은 한국어 근거"}'
            )
            recent = "\n".join(f"- {item[:240]}" for item in recent_messages[:3])
            user = (
                f"현재 메시지:\n{text[:1200]}\n\n"
                f"결정론적 점수: {assessment.score}\n"
                f"결정론적 근거: {', '.join(assessment.reasons) or '없음'}\n"
                f"같은 사용자의 최근 의심 메시지:\n{recent or '- 없음'}"
            )
            call_args: dict[str, Any] = {
                "model": self.config.llm_model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "response_format": {"type": "json_object"},
                "max_completion_tokens": 350,
            }
            if self.config.llm_model.startswith("gpt-5"):
                call_args["reasoning_effort"] = "none"
            else:
                call_args["temperature"] = 0
            response = await client.chat.completions.create(**call_args)
            raw = response.choices[0].message.content or ""
            parsed = json.loads(raw.strip().removeprefix("```json").removesuffix("```").strip())
            label = str(parsed.get("label", "")).strip().lower()
            if label not in {"benign", "suspicious", "high_risk"}:
                return None
            confidence = max(0.0, min(1.0, float(parsed.get("confidence", 0.0))))
            return {
                "label": label,
                "confidence": confidence,
                "reason": str(parsed.get("reason", ""))[:300],
            }
        except Exception as exc:  # noqa: BLE001 — LLM failure falls back to rules
            logger.warning("moderation LLM review failed: %s", exc)
            return None
        finally:
            if client is not None:
                try:
                    await client.close()
                except Exception as exc:  # noqa: BLE001 — client cleanup is best-effort
                    logger.debug("moderation LLM client cleanup failed: %s", exc)

    def _decide(
        self,
        assessment: ModerationAssessment,
        violation_count: int,
        llm_result: dict[str, Any] | None,
    ) -> ModerationDecision:
        label = llm_result.get("label") if llm_result else None
        confidence = llm_result.get("confidence") if llm_result else None
        score = assessment.score
        reasons = list(assessment.reasons)
        if llm_result and llm_result.get("reason"):
            reasons.append(f"LLM: {llm_result['reason']}")
        reasons_tuple = tuple(dict.fromkeys(reasons))

        # A high-confidence benign verdict may clear a borderline deterministic hit,
        # but it cannot override a strong evidence score or a repeat offender.
        if (
            label == "benign"
            and float(confidence or 0) >= 0.85
            and score < self.config.restrict_score
            and violation_count <= 1
        ):
            return ModerationDecision(
                _ACTION_ALLOW, score, reasons_tuple, violation_count, llm_label=label,
                llm_confidence=confidence,
            )

        high_risk = label == "high_risk" and float(confidence or 0) >= 0.65
        suspicious = label == "suspicious" and float(confidence or 0) >= 0.65
        should_delete = score >= self.config.delete_score or suspicious or high_risk
        if not should_delete:
            return ModerationDecision(
                _ACTION_ALLOW, score, reasons_tuple, violation_count, llm_label=label,
                llm_confidence=confidence,
            )

        if (
            self.config.auto_ban
            and high_risk
            and violation_count >= self.config.ban_after_violations
        ):
            action = _ACTION_BAN
            duration = None
        elif (
            self.config.auto_restrict
            and (score >= self.config.restrict_score or high_risk or violation_count >= 2)
        ):
            action = _ACTION_RESTRICT
            duration = (
                self.config.repeat_restrict_minutes
                if violation_count >= 2
                else self.config.restrict_minutes
            )
        else:
            action = _ACTION_DELETE
            duration = None
        return ModerationDecision(
            action, score, reasons_tuple, violation_count, duration,
            llm_label=label, llm_confidence=confidence,
        )

    async def _notify_operator(
        self, bot: Any, chat_id: str | int, user: Any, text: str,
        decision: ModerationDecision, *, llm_reviewed: bool,
    ) -> None:
        if not self.config.admin_chat_id:
            return
        key = (str(chat_id), int(user.id))
        now = datetime.now(timezone.utc)
        async with self._state_lock:
            previous = self._last_alert.get(key)
            if previous and (now - previous).total_seconds() < self.config.admin_alert_cooldown_seconds:
                return
            self._last_alert[key] = now
        action_kr = {
            _ACTION_DELETE: "메시지 삭제",
            _ACTION_RESTRICT: f"{decision.duration_minutes}분 발언 제한",
            _ACTION_BAN: "퇴장(영구 차단)",
            _ACTION_ALLOW: "관찰만",
        }.get(decision.action, decision.action)
        username = f"@{user.username}" if getattr(user, "username", None) else "아이디 없음"
        label = decision.llm_label or "미실행"
        confidence = (
            f"{float(decision.llm_confidence):.0%}" if decision.llm_confidence is not None else "-"
        )
        alert = (
            "🛡️ 토론방 moderation 알림\n"
            f"사용자: {getattr(user, 'full_name', '') or '이름 없음'} ({username}, id={user.id})\n"
            f"결정론 점수: {decision.score} · LLM: {label} ({confidence})\n"
            f"예정 조치: {action_kr}\n"
            f"근거: {' / '.join(decision.reasons[:4]) or '없음'}\n"
            f"메시지: {text[:400]}"
        )
        if llm_reviewed:
            alert += "\n(결정론적 탐지 후 LLM 2차 검토 완료)"
        try:
            await bot.send_message(
                chat_id=self.config.admin_chat_id,
                text=alert,
                disable_web_page_preview=True,
            )
        except Exception as exc:  # noqa: BLE001 — alert failure must not affect moderation
            logger.warning("moderation operator alert failed: %s", exc)

    async def moderate_update(self, bot: Any, update: Any) -> bool:
        """Moderate one group message; return True when downstream handlers must stop."""
        if not self.active:
            return False
        chat = getattr(update, "effective_chat", None)
        message = getattr(update, "effective_message", None)
        user = getattr(update, "effective_user", None)
        if not chat or not message or not user:
            return False
        if getattr(chat, "type", None) not in {"group", "supergroup"}:
            return False
        if str(chat.id) != str(self.config.target_chat_id):
            return False
        if getattr(user, "is_bot", False):
            return False
        text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
        if not text.strip():
            return False

        exempt = await self._is_exempt(bot, chat.id, user.id)
        if exempt is not False:
            return False

        now = datetime.now(timezone.utc)
        duplicate_count, burst_count = await self._pattern_counts(chat.id, user.id, text, now)
        joined_recently = False
        if self.store:
            try:
                joined_recently = await asyncio.to_thread(
                    self.store.user_joined_recently,
                    chat.id,
                    user.id,
                    now,
                    self.config.join_context_minutes,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation join context lookup failed: %s", exc)
        assessment = assess_text(
            text,
            duplicate_count=duplicate_count,
            burst_count=burst_count,
            joined_recently=joined_recently,
        )
        is_candidate = assessment.score >= self.config.llm_min_score
        if self.store:
            recent = await asyncio.to_thread(
                self.store.recent_suspicious_messages, chat.id, user.id
            )
            try:
                observation = await asyncio.to_thread(
                    self.store.record_observation,
                    chat_id=chat.id,
                    user_id=user.id,
                    message_id=message.message_id,
                    username=getattr(user, "username", "") or "",
                    display_name=getattr(user, "full_name", "") or "",
                    text=text,
                    assessment=assessment,
                    is_violation=assessment.score >= self.config.delete_score,
                    now=now,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation observation save failed: %s", exc)
                observation = {"row_id": None, "violation_count": 0}
        else:
            recent = []
            observation = {"row_id": None, "violation_count": 0}

        llm_result = None
        if is_candidate:
            llm_result = await self._llm_review(text, assessment, recent)
        decision = self._decide(
            assessment, int(observation.get("violation_count", 0)), llm_result
        )
        if self.store:
            try:
                await asyncio.to_thread(
                    self.store.save_llm_result,
                    observation.get("row_id"),
                    label=llm_result.get("label") if llm_result else None,
                    confidence=llm_result.get("confidence") if llm_result else None,
                    reason=llm_result.get("reason") if llm_result else None,
                    action=decision.action,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation LLM result save failed: %s", exc)

        if decision.action == _ACTION_ALLOW:
            if is_candidate:
                await self._notify_operator(
                    bot, chat.id, user, text, decision, llm_reviewed=llm_result is not None
                )
            return False

        await self._notify_operator(
            bot, chat.id, user, text, decision, llm_reviewed=llm_result is not None
        )
        try:
            await message.delete()
        except Exception as exc:  # noqa: BLE001 — continue to restriction if possible
            logger.warning("moderation message delete failed: %s", exc)

        if decision.action == _ACTION_RESTRICT and decision.duration_minutes:
            try:
                await bot.restrict_chat_member(
                    chat_id=chat.id,
                    user_id=user.id,
                    permissions=ChatPermissions(can_send_messages=False),
                    until_date=now + timedelta(minutes=decision.duration_minutes),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation restrict failed for %s: %s", user.id, exc)
        elif decision.action == _ACTION_BAN:
            try:
                await bot.ban_chat_member(
                    chat_id=chat.id, user_id=user.id, revoke_messages=True
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("moderation ban failed for %s: %s", user.id, exc)
        return True
