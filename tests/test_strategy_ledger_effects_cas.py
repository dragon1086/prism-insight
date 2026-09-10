"""Optional normalized-unit CAS preserves historical default sell events."""
import json
import sqlite3

import pytest

from prism_core.strategy_ledger import LedgerError, StrategyLedger


@pytest.fixture
def ledger(tmp_path):
    ledger = StrategyLedger(tmp_path / "ledger.sqlite")
    ledger.create_book("book", "KR", mode="SHADOW")
    ledger.apply_target("entry", "book", "campaign", "005930", 100, 100, "2026-09-10T00:00:00Z")
    return ledger


def test_default_sell_payload_and_retry_remain_unchanged(ledger):
    assert ledger.sell("sell", "campaign", 110, "2026-09-10T01:00:00Z", quantity=".005")["event_applied"]
    assert not ledger.sell("sell", "campaign", 110, "2026-09-10T01:00:00Z", quantity=".005")["event_applied"]
    with sqlite3.connect(ledger.path) as db:
        payload = json.loads(db.execute("SELECT payload FROM events WHERE id='sell'").fetchone()[0])
    assert set(payload) == {"kind", "campaign_id", "price", "occurred_at", "normalized_units", "fee_rate", "slippage_rate", "source_hash"}


def test_partial_cas_duplicate_after_hash_change_is_idempotent(ledger):
    guard = ledger.campaign_guard("campaign")
    assert guard["normalized_units"] == "0.01"
    assert guard["book_id"] == "book" and guard["symbol"] == "005930"
    args = dict(quantity=".005", expected_campaign_hash=guard["campaign_hash"])
    assert ledger.sell("sell", "campaign", 110, "2026-09-10T01:00:00Z", **args)["event_applied"]
    assert ledger.campaign_guard("campaign")["campaign_hash"] != guard["campaign_hash"]
    assert not ledger.sell("sell", "campaign", 110, "2026-09-10T01:00:00Z", **args)["event_applied"]
    before = ledger.snapshot("book")
    with pytest.raises(LedgerError, match="campaign revision"):
        ledger.sell("stale", "campaign", 110, "2026-09-10T02:00:00Z", **args)
    assert ledger.snapshot("book") == before


@pytest.mark.parametrize("invalid", [True, "x" * 64, "a", 42])
def test_invalid_optional_hash_fails_before_mutation(ledger, invalid):
    before = ledger.snapshot("book")
    with pytest.raises(LedgerError):
        ledger.sell("sell", "campaign", 110, "2026-09-10T01:00:00Z", expected_campaign_hash=invalid)
    assert ledger.snapshot("book") == before
