from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from prism_app.dashboard_export import DashboardExporter
from prism_core.paper import (
    OrderReconciler,
    OrderState,
    PaperLedger,
    PaperOrder,
    SimulatedBroker,
    Side,
)
from prism_core.telegram.config import TelegramConfig
from prism_core.telegram.publisher import PublicationStatus, TelegramPublisher
from prism_core.storage.migrations import DatabaseKind, migrate_database


PROJECT_ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 7, 26, 8, 0, tzinfo=timezone.utc)


class NetworkTrap:
    def __init__(self) -> None:
        self.calls = 0

    async def send_message(self, *, chat_id: str, text: str) -> None:
        self.calls += 1
        raise AssertionError("network transport must not be called")


def _ready_telegram_config() -> TelegramConfig:
    return TelegramConfig(
        enabled=True,
        bot_token="fixture-token",
        allowed_chat_id="fixture-chat",
        allowed_user_id="fixture-user",
    )


def _walk_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        return set(value) | set().union(
            *(_walk_keys(item) for item in value.values()), set()
        )
    if isinstance(value, list):
        return set().union(*(_walk_keys(item) for item in value), set())
    return set()


def test_phase1_end_to_end_acceptance_is_fail_closed_in_every_ci_matrix_job() -> None:
    workflow = (PROJECT_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert "Run Phase 1 end-to-end acceptance tests" in workflow
    assert "python -m pytest tests/e2e -q" in workflow
    assert (
        "tests/e2e/test_phase1_no_external_effects.py::"
        "test_phase1_end_to_end_acceptance_is_fail_closed_in_every_ci_matrix_job"
        in workflow
    )


def test_disabled_telegram_refuses_construction_before_any_network_call() -> None:
    trap = NetworkTrap()

    with pytest.raises(ValueError, match="enabled complete configuration"):
        TelegramPublisher(TelegramConfig(enabled=False), transport=trap)

    assert trap.calls == 0


@pytest.mark.asyncio
async def test_telegram_dry_run_generates_payload_without_transport_effect() -> None:
    trap = NetworkTrap()
    publisher = TelegramPublisher(_ready_telegram_config(), transport=trap)

    result = await publisher.publish_report(
        report_job_id="phase1-kr-daily",
        request_id="phase1-dry-run",
        text="[KR DAILY] fixture acceptance report",
        dry_run=True,
    )
    payload = json.loads(result.serialized_payload)

    assert result.status is PublicationStatus.DRY_RUN
    assert payload["chat_id"] == "fixture-chat"
    assert payload["request_id"] == "phase1-dry-run"
    assert payload["dedupe_key"] == "report:phase1-kr-daily"
    assert "fixture-token" not in result.serialized_payload
    assert trap.calls == 0


def test_phase1_application_and_internal_paper_have_zero_broker_boundary_violations() -> None:
    result = subprocess.run(
        [sys.executable, "tools/audit_broker_boundaries.py"],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    phase1_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for root in (PROJECT_ROOT / "prism_app", PROJECT_ROOT / "prism_core" / "paper")
        for path in root.rglob("*.py")
    ).lower()

    assert result.returncode == 0, result.stdout + result.stderr
    assert "violations: 0" in result.stdout.lower()
    assert "pass: configured ast broker boundary checks" in result.stdout.lower()
    assert "domestic_stock_trading" not in phase1_sources
    assert "us_stock_trading" not in phase1_sources
    assert "async_buy_stock" not in phase1_sources
    assert "async_sell_stock" not in phase1_sources
    assert "orderintent" not in phase1_sources


def test_internal_paper_partial_fill_survives_restart_and_unknown_reconciliation(
    tmp_path: Path,
) -> None:
    path = tmp_path / "paper.sqlite"
    ledger = PaperLedger(path)
    ledger.create_book(
        book_id="swing-us",
        strategy_id="SWING_V1",
        market="US",
        currency="USD",
        created_at=NOW,
    )
    ledger.deposit(
        entry_id="deposit-1",
        book_id="swing-us",
        currency="USD",
        amount=Decimal("2000"),
        effective_at=NOW,
    )
    broker = SimulatedBroker(ledger)
    broker.create(
        PaperOrder(
            order_id="order-1",
            book_id="swing-us",
            proposal_id="proposal-1",
            security_id="security-1",
            side=Side.BUY,
            quantity=Decimal("10"),
            limit_price=Decimal("100"),
            occurred_at=NOW,
        )
    )
    broker.accept("order-1", occurred_at=NOW)
    partial = broker.fill(
        "order-1",
        fill_id="fill-1",
        quantity=Decimal("3"),
        price=Decimal("99"),
        fee=Decimal("0.30"),
        occurred_at=NOW,
    )

    recovered_ledger = PaperLedger(path)
    recovered_broker = SimulatedBroker(recovered_ledger)
    recovered_broker.mark_unknown("order-1", occurred_at=NOW + timedelta(seconds=1))
    reconciled = OrderReconciler(recovered_ledger).resolve(
        "order-1",
        observed_state=OrderState.PARTIALLY_FILLED,
        occurred_at=NOW + timedelta(seconds=2),
    )
    completed = recovered_broker.fill(
        "order-1",
        fill_id="fill-2",
        quantity=Decimal("7"),
        price=Decimal("100"),
        fee=Decimal("0.70"),
        occurred_at=NOW + timedelta(seconds=3),
    )

    assert partial.state is OrderState.PARTIALLY_FILLED
    assert reconciled.state is OrderState.PARTIALLY_FILLED
    assert completed.state is OrderState.FILLED
    assert recovered_ledger.fill_count("order-1") == 2
    assert recovered_ledger.position("swing-us", "security-1").quantity == Decimal("10")
    assert [event.state for event in recovered_ledger.order_history("order-1")] == [
        OrderState.CREATED,
        OrderState.ACCEPTED,
        OrderState.PARTIALLY_FILLED,
        OrderState.UNKNOWN,
        OrderState.PARTIALLY_FILLED,
        OrderState.FILLED,
    ]


def test_dashboard_export_contains_only_research_internal_paper_and_ops_data() -> None:
    connections = []
    for kind in DatabaseKind:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        migrate_database(connection, kind)
        connections.append(connection)
    try:
        payload = DashboardExporter(*connections).build(as_of=NOW, generated_at=NOW)
    finally:
        for connection in connections:
            connection.close()

    assert payload["local_only"] is True
    assert payload["bind_host"] == "127.0.0.1"
    assert payload["paper"]["environment"] == "INTERNAL_PAPER"
    assert _walk_keys(payload).isdisjoint(
        {
            "account",
            "account_summary",
            "real_portfolio",
            "real_trading",
            "broker",
            "order_intent",
            "balance",
            "holdings",
        }
    )
