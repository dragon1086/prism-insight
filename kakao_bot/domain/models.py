"""Transport- and persistence-independent Kakao bot domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Mapping


class Market(str, Enum):
    KR = "KR"
    US = "US"


class Session(str, Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"


class Regime(str, Enum):
    UNKNOWN = "UNKNOWN"
    UPTREND = "UPTREND"
    UNDER_PRESSURE = "UNDER_PRESSURE"
    CORRECTION = "CORRECTION"


class CampaignStatus(str, Enum):
    COLLECTING = "COLLECTING"
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class DeliveryStatus(str, Enum):
    PENDING = "PENDING"
    SENDING = "SENDING"
    SENT = "SENT"
    DEAD = "DEAD"


class RoomLifecycleType(str, Enum):
    ENTRANCE = "ENTRANCE"
    CHAT_JOIN = "CHAT_JOIN"
    LEAVE = "LEAVE"


class MessageSendErrorCode(str, Enum):
    CONNECT_ERROR = "connect_error"
    AMBIGUOUS_NETWORK_ERROR = "ambiguous_network_error"
    NETWORK_ERROR = "network_error"
    RATE_LIMITED = "rate_limited"
    SERVER_ERROR = "server_error"
    HTTP_ERROR = "http_error"


@dataclass(frozen=True)
class CampaignCandidate:
    ticker: str
    company_name: str
    score: float | None = None
    rationale: str | None = None

    def as_payload(self) -> dict[str, object]:
        return {
            "ticker": self.ticker,
            "company_name": self.company_name,
            "score": self.score,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class BatchCampaign:
    campaign_id: str
    market: Market
    session: Session
    trade_date: date
    regime: Regime
    status: CampaignStatus
    candidates: tuple[CampaignCandidate, ...] = ()
    skip_reason: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.campaign_id.strip():
            raise ValueError("campaign_id must not be empty")
        if self.status is CampaignStatus.SKIPPED:
            if self.candidates:
                raise ValueError("a skipped campaign cannot contain candidates")
            if not self.skip_reason:
                raise ValueError("a skipped campaign requires skip_reason")
        elif self.skip_reason is not None:
            raise ValueError("skip_reason is only valid for skipped campaigns")


@dataclass(frozen=True)
class Room:
    room_id: str
    approval_status: ApprovalStatus = ApprovalStatus.PENDING


@dataclass(frozen=True)
class RoomSubscription:
    """Room delivery preferences.

    The product default is one daily KR afternoon campaign. Persistence
    deliberately overrides these defaults to all-off while a room is pending.
    """

    room_id: str
    kr_morning: bool = False
    kr_afternoon: bool = True
    us_morning: bool = False
    us_afternoon: bool = False
    rest_notices: bool = False

    @classmethod
    def all_disabled(cls, room_id: str) -> "RoomSubscription":
        return cls(room_id=room_id, kr_afternoon=False)

    def is_enabled(self, market: Market, session: Session) -> bool:
        slots = {
            (Market.KR, Session.MORNING): self.kr_morning,
            (Market.KR, Session.AFTERNOON): self.kr_afternoon,
            (Market.US, Session.MORNING): self.us_morning,
            (Market.US, Session.AFTERNOON): self.us_afternoon,
        }
        return slots[(market, session)]


@dataclass(frozen=True)
class OutboundDelivery:
    delivery_key: str
    room_id: str
    message_type: str
    payload: Mapping[str, object]
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


@dataclass(frozen=True)
class ClaimedOutboundDelivery:
    """An outbox delivery exclusively leased to one sender worker."""

    delivery_key: str
    room_id: str
    message_type: str
    payload: Mapping[str, object]
    attempt_count: int
    lease_owner: str
    lease_expires_at: datetime
    created_at: datetime
    expires_at: datetime | None = None


class AnalysisJobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class AnalysisJob:
    """A new on-demand `/report` analysis job awaiting a worker."""

    job_id: str
    room_id: str
    user_id: str
    ticker: str
    company_name: str
    market: str
    # `질문` has no ticker and `평가` needs an average price and a holding
    # period, so the job carries its own kind and free-form input instead of
    # every command being forced through the report shape.
    kind: str = "report"
    payload: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.market not in ("kr", "us"):
            raise ValueError("market must be 'kr' or 'us'")


@dataclass(frozen=True)
class ClaimedAnalysisJob:
    """An analysis job exclusively leased to one worker."""

    job_id: str
    room_id: str
    user_id: str
    ticker: str
    company_name: str
    market: str
    attempt_count: int
    requested_at: datetime
    kind: str = "report"
    payload: Mapping[str, object] | None = None


@dataclass(frozen=True)
class MessageSendResult:
    """Transport-neutral result used by the outbox delivery use case."""

    success: bool
    status_code: int | None = None
    error_code: MessageSendErrorCode | str | None = None
    error_message: str | None = None
    retryable: bool = False
    ambiguous: bool = False


@dataclass(frozen=True)
class InboundMessage:
    event_id: str
    sequence: int
    room_id: str
    user_id: str
    nickname: str | None
    text: str
    callback_token: str | None = field(repr=False)
    occurred_at: datetime


@dataclass(frozen=True)
class RoomLifecycleEvent:
    event_id: str
    sequence: int
    room_id: str
    event_type: RoomLifecycleType
    occurred_at: datetime
