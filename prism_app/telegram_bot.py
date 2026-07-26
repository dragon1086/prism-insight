"""Small allowlisted read-only Telegram conversational application."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol

from prism_app.query_service import QueryService
from prism_core.telegram.auth import TelegramAuthorizer
from prism_core.telegram.commands import (
    CommandName,
    CommandValidationError,
    DeniedCommandError,
    ParsedTelegramQuery,
    parse_telegram_query,
)
from prism_core.telegram.config import TelegramConfig
from prism_core.telegram.publisher import (
    PublicationStatus,
    StdlibTelegramBotApiRequester,
    TelegramBotApiRequester,
    TelegramPublisher,
    TelegramTransport,
)
from prism_core.strategies.contracts import Market


HELP_TEXT = """PRISM read-only commands:
/help /status /daily KR|US /weekly KR|US /symbol <ticker>
/portfolio /paper /health
Questions search stored snapshots, reports, and evidence only.
Orders, risk changes, credentials, kill-switch changes, and policy/prompt changes are denied."""


class InboundAuditStatus(str, Enum):
    UNAUTHORIZED = "unauthorized"
    DENIED = "denied"
    INVALID = "invalid"
    ANSWERED = "answered"
    UNAVAILABLE = "unavailable"
    DUPLICATE = "duplicate"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True)
class InboundTelegramUpdate:
    update_id: int
    chat_id: str | int = field(repr=False)
    user_id: str | int = field(repr=False)
    text: str = field(repr=False)

    def __post_init__(self) -> None:
        if type(self.update_id) is not int or self.update_id < 0:
            raise ValueError("update_id must be a non-negative integer")
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("text must be non-empty")


@dataclass(frozen=True)
class InboundAuditRecord:
    update_id: int
    status: InboundAuditStatus
    command: str | None = None


class InboundAuditSink(Protocol):
    def append(self, record: InboundAuditRecord) -> None: ...


class InMemoryInboundAuditSink:
    def __init__(self) -> None:
        self.records: list[InboundAuditRecord] = []

    def append(self, record: InboundAuditRecord) -> None:
        self.records.append(record)


class TelegramPollingError(RuntimeError):
    """A sanitized long-polling failure."""


@dataclass(frozen=True)
class TelegramPollBatch:
    """Processable text updates plus the offset that drains every seen envelope."""

    updates: tuple[InboundTelegramUpdate, ...]
    next_offset: int | None

    def __post_init__(self) -> None:
        if any(not isinstance(update, InboundTelegramUpdate) for update in self.updates):
            raise TypeError("poll batch updates must be inbound Telegram updates")
        update_ids = tuple(update.update_id for update in self.updates)
        if update_ids != tuple(sorted(set(update_ids))):
            raise ValueError("poll batch update IDs must be unique and ordered")
        if self.next_offset is not None and (
            type(self.next_offset) is not int
            or self.next_offset < 0
            or (update_ids and self.next_offset <= update_ids[-1])
        ):
            raise ValueError("poll batch next offset must follow every returned update")


class TelegramUpdateSource(Protocol):
    async def poll(
        self,
        *,
        offset: int | None,
        timeout_seconds: float,
    ) -> TelegramPollBatch: ...


class TelegramReadQueryService(Protocol):
    """The exact read-only QueryService surface consumed by Telegram."""

    def latest_leadership(self, market: Market) -> Any: ...

    def weekly_report_readiness(self) -> Any: ...

    def search_reports(self, query: str, *, limit: int = 3) -> tuple[Any, ...]: ...


class TelegramBotApiUpdateSource:
    """Opt-in bounded Bot API long polling with no credential-bearing errors."""

    def __init__(
        self,
        *,
        bot_token: str,
        requester: TelegramBotApiRequester,
    ) -> None:
        self._bot_token = bot_token
        self._requester = requester

    @classmethod
    def from_config(
        cls,
        config: TelegramConfig,
        *,
        requester: TelegramBotApiRequester | None = None,
    ) -> TelegramBotApiUpdateSource:
        if not config.is_ready or config.bot_token is None:
            raise ValueError("Telegram long polling requires enabled complete configuration")
        return cls(
            bot_token=config.bot_token,
            requester=requester or StdlibTelegramBotApiRequester(),
        )

    async def poll(
        self,
        *,
        offset: int | None,
        timeout_seconds: float,
    ) -> TelegramPollBatch:
        if offset is not None and (type(offset) is not int or offset < 0):
            raise ValueError("poll offset must be a non-negative integer")
        if not isinstance(timeout_seconds, (int, float)) or not 1 <= timeout_seconds <= 50:
            raise ValueError("poll timeout must be between 1 and 50 seconds")
        fields = {
            "allowed_updates": json.dumps(["message"], separators=(",", ":")),
            "timeout": str(int(timeout_seconds)),
        }
        if offset is not None:
            fields["offset"] = str(offset)
        try:
            response = await self._requester.post_form(
                url=f"https://api.telegram.org/bot{self._bot_token}/getUpdates",
                fields=fields,
                timeout_seconds=float(timeout_seconds) + 5.0,
            )
            return _parse_updates_response(response)
        except TelegramPollingError:
            raise
        except Exception:
            raise TelegramPollingError("Telegram long polling failed") from None

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


def _parse_updates_response(response: Mapping[str, Any]) -> TelegramPollBatch:
    if response.get("ok") is not True or not isinstance(response.get("result"), list):
        raise TelegramPollingError("Telegram long polling returned a malformed response")
    updates: list[InboundTelegramUpdate] = []
    max_update_id: int | None = None
    for raw in response["result"]:
        if (
            not isinstance(raw, Mapping)
            or type(raw.get("update_id")) is not int
            or raw["update_id"] < 0
        ):
            raise TelegramPollingError("Telegram update envelope is malformed")
        max_update_id = (
            raw["update_id"]
            if max_update_id is None
            else max(max_update_id, raw["update_id"])
        )
        message = raw.get("message")
        if (
            not isinstance(message, Mapping)
            or not isinstance(message.get("text"), str)
            or not message["text"].strip()
        ):
            continue
        chat = message.get("chat")
        sender = message.get("from")
        if not isinstance(chat, Mapping) or not isinstance(sender, Mapping):
            continue
        chat_id = chat.get("id")
        user_id = sender.get("id")
        if not isinstance(chat_id, (str, int)) or not isinstance(user_id, (str, int)):
            continue
        try:
            updates.append(
                InboundTelegramUpdate(
                    update_id=raw["update_id"],
                    chat_id=chat_id,
                    user_id=user_id,
                    text=message["text"],
                )
            )
        except ValueError:
            raise TelegramPollingError("Telegram text update is malformed") from None
    return TelegramPollBatch(
        tuple(sorted(updates, key=lambda item: item.update_id)),
        None if max_update_id is None else max_update_id + 1,
    )


class TelegramConversationApp:
    """Authorize first, then dispatch only a closed set of QueryService reads."""

    def __init__(
        self,
        config: TelegramConfig,
        *,
        query_service: QueryService | TelegramReadQueryService,
        transport: TelegramTransport | None = None,
        audit_sink: InboundAuditSink | None = None,
    ) -> None:
        self._authorizer = TelegramAuthorizer(config)
        self._query_service = query_service
        self._publisher = TelegramPublisher(config, transport=transport)
        self.fake_transport = self._publisher.fake_transport
        self._audit_sink = audit_sink or InMemoryInboundAuditSink()
        self._next_offset: int | None = None

    @property
    def audit_records(self) -> tuple[InboundAuditRecord, ...]:
        if not isinstance(self._audit_sink, InMemoryInboundAuditSink):
            raise TypeError("audit records are owned by the injected audit sink")
        return tuple(self._audit_sink.records)

    async def handle_update(self, update: InboundTelegramUpdate) -> bool:
        if not self._authorizer.is_allowed(
            chat_id=update.chat_id,
            user_id=update.user_id,
        ):
            self._audit(update, InboundAuditStatus.UNAUTHORIZED)
            return False

        try:
            query = parse_telegram_query(update.text)
        except DeniedCommandError:
            self._audit(update, InboundAuditStatus.DENIED)
            await self._reply(update.update_id, "Denied: PRISM Telegram is read-only.")
            return True
        except CommandValidationError:
            self._audit(update, InboundAuditStatus.INVALID)
            await self._reply(
                update.update_id,
                "Unsupported read-only query. Use /help for allowed commands.",
            )
            return True

        try:
            answer = self._answer(query)
        except Exception:
            self._audit(update, InboundAuditStatus.UNAVAILABLE, query.command.value)
            await self._reply(
                update.update_id,
                "Stored read-only data is unavailable for this query.",
            )
            return True

        publication_status = await self._reply(update.update_id, answer)
        audit_status = {
            PublicationStatus.SENT: InboundAuditStatus.ANSWERED,
            PublicationStatus.DUPLICATE: InboundAuditStatus.DUPLICATE,
            PublicationStatus.RATE_LIMITED: InboundAuditStatus.RATE_LIMITED,
        }.get(publication_status, InboundAuditStatus.UNAVAILABLE)
        self._audit(update, audit_status, query.command.value)
        return True

    async def poll_once(
        self,
        source: TelegramUpdateSource,
        *,
        timeout_seconds: float = 30.0,
    ) -> int:
        batch = await source.poll(
            offset=self._next_offset,
            timeout_seconds=timeout_seconds,
        )
        handled = 0
        for update in batch.updates:
            if self._next_offset is not None and update.update_id < self._next_offset:
                continue
            await self.handle_update(update)
            self._next_offset = update.update_id + 1
            handled += 1
        if batch.next_offset is not None and (
            self._next_offset is None or batch.next_offset > self._next_offset
        ):
            self._next_offset = batch.next_offset
        return handled

    async def run_polling(
        self,
        source: TelegramUpdateSource,
        *,
        timeout_seconds: float = 30.0,
        stop_event: asyncio.Event | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        """Run one explicitly composed worker; Task 24 owns process supervision."""

        backoff_seconds = 1.0
        sleep_call = sleep or asyncio.sleep
        while stop_event is None or not stop_event.is_set():
            try:
                await self.poll_once(source, timeout_seconds=timeout_seconds)
            except TelegramPollingError:
                await sleep_call(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 30.0)
            else:
                backoff_seconds = 1.0

    def _answer(self, query: ParsedTelegramQuery) -> str:
        if query.command is CommandName.HELP:
            return HELP_TEXT
        if query.command is CommandName.SEARCH:
            hits = self._query_service.search_reports(query.arguments[0], limit=3)
            if not hits:
                return "No matching stored report evidence was found."
            lines = ["Stored report evidence (data only; never executed):"]
            for hit in hits:
                lines.extend(
                    (
                        f"- As of: {hit.as_of}",
                        f"- Report: {hit.report_kind} ({hit.report_id})",
                        hit.content,
                    )
                )
            return "\n".join(lines)
        if query.command is CommandName.WEEKLY:
            readiness = self._query_service.weekly_report_readiness()
            return (
                f"[{query.arguments[0]} WEEKLY]\n"
                f"Available: {'YES' if readiness.available else 'NO'}\n"
                f"Reason: {readiness.reason}\n"
                f"Missing: {', '.join(readiness.missing_inputs)}"
            )
        if query.command in {CommandName.DAILY, CommandName.STATUS}:
            markets = (
                (Market(query.arguments[0]),)
                if query.command is CommandName.DAILY
                else (Market.KR, Market.US)
            )
            sections: list[str] = []
            for market in markets:
                try:
                    stored = self._query_service.latest_leadership(market)
                except (KeyError, LookupError):
                    sections.append(f"[{market.value}] stored report unavailable")
                    continue
                sections.append(stored.rendered_markdown.rstrip())
            return "\n\n".join(sections)
        if query.command is CommandName.SYMBOL:
            return self._symbol_answer(query.arguments[0])
        if query.command in {CommandName.PORTFOLIO, CommandName.PAPER}:
            return (
                "[PAPER] Stored internal-paper snapshot unavailable; "
                "no broker or account query was attempted."
            )
        if query.command is CommandName.HEALTH:
            return (
                "[HEALTH] Durable job/heartbeat read contract is unavailable until Task 24."
            )
        raise CommandValidationError("query is not read-only allowlisted")

    def _symbol_answer(self, symbol: str) -> str:
        for market in (Market.KR, Market.US):
            try:
                stored = self._query_service.latest_leadership(market)
            except (KeyError, LookupError):
                continue
            for leader in stored.snapshot.leaders:
                if leader.symbol == symbol:
                    return (
                        f"[{market.value} SYMBOL] {leader.symbol} — {leader.name}\n"
                        f"As of: {stored.snapshot.as_of.isoformat()}\n"
                        f"Quality: {stored.snapshot.quality.value}\n"
                        f"Decision: {leader.decision_status.value}\n"
                        f"Strategies: {', '.join(item.value for item in leader.strategies) or 'NONE'}\n"
                        f"Evidence: {', '.join(leader.evidence_refs) or 'NONE'}"
                    )
        return f"No stored leadership evidence found for {symbol}."

    async def _reply(self, update_id: int, text: str) -> PublicationStatus:
        result = await self._publisher.publish_report(
            report_job_id=f"telegram-reply:{update_id}",
            request_id=f"telegram-update-{update_id}",
            text=text[:4096],
        )
        return result.status

    def _audit(
        self,
        update: InboundTelegramUpdate,
        status: InboundAuditStatus,
        command: str | None = None,
    ) -> None:
        self._audit_sink.append(InboundAuditRecord(update.update_id, status, command))
