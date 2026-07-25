"""Immutable provenance models for public AgentNews context boards.

AgentNews content is retained as untrusted research-priority context.  The model
never interprets body instructions or promotes board claims into executable data.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum

from prism_core.data.contracts import DataQualityStatus


class AgentNewsBoard(str, Enum):
    KR = "KR"
    US = "US"

    @property
    def url(self) -> str:
        return {
            AgentNewsBoard.KR: "https://agentnews.md/finance-ko.md",
            AgentNewsBoard.US: "https://agentnews.md/finance.md",
        }[self]


class AgentNewsContentRole(str, Enum):
    RESEARCH_PRIORITY_CONTEXT = "RESEARCH_PRIORITY_CONTEXT"


class AgentNewsTrust(str, Enum):
    UNTRUSTED = "UNTRUSTED"


class AgentNewsModelError(ValueError):
    """The public response cannot form a safe AgentNews snapshot."""


_UPDATED_LINE = re.compile(r'^updated:\s*["\']?([^"\']+)["\']?\s*$')


def _parse_source_updated_at(raw_body: bytes) -> datetime | None:
    try:
        text = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AgentNewsModelError("AgentNews body must be valid UTF-8 Markdown") from exc
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    closing_index = next(
        (index for index, line in enumerate(lines[1:65], start=1) if line.strip() == "---"),
        None,
    )
    if closing_index is None:
        raise AgentNewsModelError("AgentNews front matter is not bounded")
    values = []
    for line in lines[1:closing_index]:
        match = _UPDATED_LINE.fullmatch(line)
        if match:
            values.append(match.group(1).strip())
    if not values:
        return None
    if len(values) != 1:
        raise AgentNewsModelError("AgentNews front matter has duplicate updated fields")
    try:
        parsed = datetime.fromisoformat(values[0].replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentNewsModelError("AgentNews updated timestamp is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise AgentNewsModelError("AgentNews updated timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AgentNewsSnapshot:
    board: AgentNewsBoard
    url: str
    fetched_at: datetime
    freshness_evaluated_at: datetime
    source_updated_at: datetime | None
    content_hash: str
    quality: DataQualityStatus
    _raw_body: bytes = field(repr=False)
    provider: str = field(default="agentnews", init=False)
    source_record_id: str = field(init=False)
    role: AgentNewsContentRole = field(
        default=AgentNewsContentRole.RESEARCH_PRIORITY_CONTEXT, init=False
    )
    trust: AgentNewsTrust = field(default=AgentNewsTrust.UNTRUSTED, init=False)
    material_claims_require_original_source: bool = field(default=True, init=False)
    executable: bool = field(default=False, init=False)
    fallback_reason: str | None = None

    def __post_init__(self) -> None:
        if self.url != self.board.url:
            raise AgentNewsModelError("AgentNews URL does not match the allowlisted board")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise AgentNewsModelError("fetched_at must be timezone-aware")
        if (
            self.freshness_evaluated_at.tzinfo is None
            or self.freshness_evaluated_at.utcoffset() is None
            or self.freshness_evaluated_at < self.fetched_at
        ):
            raise AgentNewsModelError(
                "freshness_evaluated_at must be aware and not precede fetched_at"
            )
        expected_hash = hashlib.sha256(self._raw_body).hexdigest()
        if self.content_hash != expected_hash:
            raise AgentNewsModelError("AgentNews content hash does not match the raw body")
        object.__setattr__(
            self,
            "source_record_id",
            f"agentnews:{self.board.value.lower()}:{self.content_hash}",
        )

    @classmethod
    def from_markdown(
        cls,
        *,
        board: AgentNewsBoard,
        url: str,
        raw_body: bytes,
        fetched_at: datetime,
        freshness_window: timedelta,
    ) -> AgentNewsSnapshot:
        if url != board.url:
            raise AgentNewsModelError("AgentNews URL does not match the allowlisted board")
        if fetched_at.tzinfo is None or fetched_at.utcoffset() is None:
            raise AgentNewsModelError("fetched_at must be timezone-aware")
        if freshness_window <= timedelta(0):
            raise AgentNewsModelError("freshness_window must be positive")
        if not raw_body:
            raise AgentNewsModelError("AgentNews body must not be empty")
        source_updated_at = _parse_source_updated_at(raw_body)
        if source_updated_at is None:
            quality = DataQualityStatus.PARTIAL
        elif source_updated_at > fetched_at:
            raise AgentNewsModelError("AgentNews updated timestamp cannot be after fetched_at")
        elif fetched_at - source_updated_at > freshness_window:
            quality = DataQualityStatus.STALE
        else:
            quality = DataQualityStatus.FRESH
        return cls(
            board=board,
            url=url,
            fetched_at=fetched_at,
            freshness_evaluated_at=fetched_at,
            source_updated_at=source_updated_at,
            content_hash=hashlib.sha256(raw_body).hexdigest(),
            quality=quality,
            _raw_body=bytes(raw_body),
        )

    @property
    def raw_body(self) -> bytes:
        return bytes(self._raw_body)

    def as_stale_fallback(
        self, *, reason: str, evaluated_at: datetime
    ) -> AgentNewsSnapshot:
        if not reason:
            raise AgentNewsModelError("fallback reason must not be empty")
        return replace(
            self,
            quality=DataQualityStatus.STALE,
            fallback_reason=reason,
            freshness_evaluated_at=evaluated_at,
        )
