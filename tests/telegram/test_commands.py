from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Mapping

import pytest

from prism_app.query_service import QueryService
from prism_app.telegram_bot import (
    InboundTelegramUpdate,
    TelegramBotApiUpdateSource,
    TelegramConversationApp,
    TelegramPollBatch,
    TelegramPollingError,
)
from prism_core.telegram.commands import (
    ALLOWED_COMMANDS,
    CommandName,
    CommandValidationError,
    DeniedCommandError,
    ParsedTelegramQuery,
    parse_telegram_query,
)
from prism_core.telegram.config import TelegramConfig
from prism_core.telegram.publisher import FakeTelegramTransport
from prism_core.reporting.leadership_tracking import LeadershipRepository
from prism_core.reporting.leadership_tracking import MarketTrackingSnapshot
from prism_core.storage.database import open_database
from prism_core.storage.migrations import DatabaseKind, migrate_database
from prism_core.strategies.contracts import Market
from tests.app.test_daily_pipeline import _leadership_snapshot
from tests.reporting.test_leadership_tracking import valid_snapshot_payload


def test_explicit_command_allowlist_is_closed_and_exact():
    assert ALLOWED_COMMANDS == frozenset(
        {
            CommandName.HELP,
            CommandName.STATUS,
            CommandName.DAILY,
            CommandName.WEEKLY,
            CommandName.SYMBOL,
            CommandName.PORTFOLIO,
            CommandName.PAPER,
            CommandName.HEALTH,
        }
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/help", ParsedTelegramQuery(CommandName.HELP)),
        (" /status ", ParsedTelegramQuery(CommandName.STATUS)),
        ("/daily kr", ParsedTelegramQuery(CommandName.DAILY, ("KR",))),
        ("/weekly US", ParsedTelegramQuery(CommandName.WEEKLY, ("US",))),
        ("/symbol 005930", ParsedTelegramQuery(CommandName.SYMBOL, ("005930",))),
        ("/symbol brk.b", ParsedTelegramQuery(CommandName.SYMBOL, ("BRK.B",))),
        ("/portfolio@prism_bot", ParsedTelegramQuery(CommandName.PORTFOLIO)),
        ("/paper", ParsedTelegramQuery(CommandName.PAPER)),
        ("/health", ParsedTelegramQuery(CommandName.HEALTH)),
    ],
)
def test_allowlisted_commands_are_normalized_with_strict_arguments(raw, expected):
    assert parse_telegram_query(raw) == expected


@pytest.mark.parametrize("raw", ["/buy AAPL", "/sell", "/cancel 1", "/live"])
def test_mutating_slash_commands_are_explicitly_denied(raw):
    with pytest.raises(DeniedCommandError, match="read-only"):
        parse_telegram_query(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "/daily",
        "/daily EU",
        "/daily KR extra",
        "/symbol",
        "/symbol AAPL extra",
        "/symbol ../secrets",
        "/help now",
        "/unknown",
        "",
        "   ",
    ],
)
def test_malformed_or_unknown_commands_fail_closed(raw):
    with pytest.raises(CommandValidationError):
        parse_telegram_query(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("daily kr", ParsedTelegramQuery(CommandName.DAILY, ("KR",), natural_language=True)),
        ("Show me the weekly US report", ParsedTelegramQuery(CommandName.WEEKLY, ("US",), natural_language=True)),
        ("symbol aapl", ParsedTelegramQuery(CommandName.SYMBOL, ("AAPL",), natural_language=True)),
        ("How is the portfolio?", ParsedTelegramQuery(CommandName.PORTFOLIO, natural_language=True)),
        ("system health", ParsedTelegramQuery(CommandName.HEALTH, natural_language=True)),
    ],
)
def test_natural_language_can_only_select_the_same_fixed_read_only_queries(raw, expected):
    assert parse_telegram_query(raw) == expected


def test_unstructured_natural_language_is_a_stored_evidence_search_not_a_tool_name():
    parsed = parse_telegram_query("Why did semiconductor leadership weaken?")

    assert parsed == ParsedTelegramQuery(
        CommandName.SEARCH,
        ("Why did semiconductor leadership weaken?",),
        natural_language=True,
    )
    assert CommandName.SEARCH not in ALLOWED_COMMANDS


def test_query_service_latest_leadership_and_search_are_read_only_and_pit_visible(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        ingested = LeadershipRepository(connection).ingest(_leadership_snapshot())
        service = QueryService(connection)

        latest = service.latest_leadership(Market.KR)
        hits = service.search_reports("constructive close", limit=3)
        injection = service.search_reports("%' OR 1=1 --", limit=3)
        report_count = connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0]

    assert latest.snapshot_id == ingested.snapshot_id
    assert len(hits) == 1
    assert hits[0].snapshot_id == ingested.snapshot_id
    assert hits[0].as_of == _leadership_snapshot().as_of
    assert "Constructive close" in hits[0].content
    assert injection == ()
    assert report_count == 1


def test_query_service_latest_leadership_fails_visibly_when_market_is_unavailable(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)

        with pytest.raises(KeyError, match="US"):
            QueryService(connection).latest_leadership(Market.US)


class _UpdateRequester:
    def __init__(self, response: Mapping[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str], float]] = []

    async def post_form(self, *, url, fields, timeout_seconds):
        self.calls.append((url, fields, timeout_seconds))
        return self.response


def _ready_config() -> TelegramConfig:
    return TelegramConfig(
        enabled=True,
        bot_token="super-secret-token",
        allowed_chat_id="100",
        allowed_user_id="200",
    )


@pytest.mark.asyncio
async def test_bot_api_update_source_performs_bounded_long_poll_and_ignores_non_text():
    requester = _UpdateRequester(
        {
            "ok": True,
            "result": [
                {
                    "update_id": 7,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/help",
                    },
                },
                {"update_id": 8, "message": {"chat": {"id": 100}}},
            ],
        }
    )
    source = TelegramBotApiUpdateSource.from_config(
        _ready_config(), requester=requester
    )

    batch = await source.poll(offset=7, timeout_seconds=2.0)

    assert batch.updates == (InboundTelegramUpdate(7, 100, 200, "/help"),)
    assert batch.next_offset == 9
    url, fields, timeout = requester.calls[0]
    assert url.endswith("/getUpdates")
    assert fields == {
        "allowed_updates": '["message"]',
        "offset": "7",
        "timeout": "2",
    }
    assert timeout == 7.0
    assert "super-secret-token" not in repr(source)


class _PollingSource:
    def __init__(self) -> None:
        self.offsets: list[int | None] = []

    async def poll(self, *, offset, timeout_seconds):
        self.offsets.append(offset)
        if len(self.offsets) == 1:
            return TelegramPollBatch(
                updates=(
                    InboundTelegramUpdate(10, "100", "200", "/help"),
                    InboundTelegramUpdate(11, "wrong", "200", "/help"),
                ),
                next_offset=12,
            )
        return TelegramPollBatch((), offset)


@pytest.mark.asyncio
async def test_conversation_app_advances_long_poll_offset_and_answers_only_authorized_updates(
    tmp_path: Path,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        transport = FakeTelegramTransport()
        app = TelegramConversationApp(
            _ready_config(),
            query_service=QueryService(connection),
            transport=transport,
        )
        source = _PollingSource()

        first_count = await app.poll_once(source, timeout_seconds=1.0)
        second_count = await app.poll_once(source, timeout_seconds=1.0)

    assert first_count == 2
    assert second_count == 0
    assert source.offsets == [None, 12]
    assert len(transport.sent) == 1
    assert "read-only commands" in transport.sent[0].text


@pytest.mark.asyncio
async def test_fromless_text_update_is_drained_without_blocking_authorized_messages():
    requester = _UpdateRequester(
        {
            "ok": True,
            "result": [
                {
                    "update_id": 30,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 999},
                        "text": "   ",
                    },
                },
                {
                    "update_id": 31,
                    "message": {
                        "chat": {"id": 100},
                        "text": "anonymous group message",
                        "sender_chat": {"id": -1000},
                    },
                },
                {
                    "update_id": 32,
                    "message": {
                        "chat": {"id": 100},
                        "from": {"id": 200},
                        "text": "/help",
                    },
                },
            ],
        }
    )
    source = TelegramBotApiUpdateSource.from_config(
        _ready_config(), requester=requester
    )

    batch = await source.poll(offset=None, timeout_seconds=1.0)

    assert batch.updates == (InboundTelegramUpdate(32, 100, 200, "/help"),)
    assert batch.next_offset == 33


class _RecoveringPollingSource:
    def __init__(self, stop_event: asyncio.Event) -> None:
        self.calls = 0
        self.stop_event = stop_event

    async def poll(self, *, offset, timeout_seconds):
        self.calls += 1
        if self.calls == 1:
            raise TelegramPollingError("transient fixture")
        self.stop_event.set()
        return TelegramPollBatch((), offset)


@pytest.mark.asyncio
async def test_run_polling_backs_off_after_transient_poll_failure_and_resumes(
    tmp_path: Path,
):
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        app = TelegramConversationApp(
            _ready_config(), query_service=QueryService(connection)
        )
        stop_event = asyncio.Event()
        source = _RecoveringPollingSource(stop_event)

        await app.run_polling(
            source,
            timeout_seconds=1.0,
            stop_event=stop_event,
            sleep=fake_sleep,
        )

    assert source.calls == 2
    assert sleeps == [1.0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("/help", "read-only commands"),
        ("/status", "US Leadership Evidence"),
        ("/daily kr", "KR Leadership Evidence"),
        ("/weekly us", "Available: NO"),
        ("/symbol nvda", "evidence-leader-1"),
        ("/portfolio", "no broker or account query"),
        ("/paper", "no broker or account query"),
        ("/health", "Task 24"),
    ],
)
async def test_every_allowlisted_command_has_a_bounded_read_only_answer(
    tmp_path: Path,
    text: str,
    expected: str,
):
    with open_database(tmp_path / "research.sqlite") as connection:
        migrate_database(connection, DatabaseKind.RESEARCH)
        repository = LeadershipRepository(connection)
        repository.ingest(
            MarketTrackingSnapshot.model_validate_json(
                json.dumps(valid_snapshot_payload(market="KR", slot=13))
            )
        )
        repository.ingest(
            MarketTrackingSnapshot.model_validate_json(
                json.dumps(valid_snapshot_payload(market="US", slot=7))
            )
        )
        transport = FakeTelegramTransport()
        app = TelegramConversationApp(
            _ready_config(),
            query_service=QueryService(connection),
            transport=transport,
        )

        handled = await app.handle_update(
            InboundTelegramUpdate(20, "100", "200", text)
        )

    assert handled is True
    assert len(transport.sent) == 1
    assert expected in transport.sent[0].text
