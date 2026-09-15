import asyncio
import logging
import sys
from types import SimpleNamespace

import pytest
from live import broker_recovery, tracking
from live import swing_entry_notice as notice

from . import test_swing_entry_recovery as recovery_fixtures

recovered = recovery_fixtures.recovered


def record(conn):
    return tracking.get_meta(conn, notice.PREFIX + "1", "swing")


@pytest.fixture
def sender(monkeypatch):
    calls = []

    async def send(mode, message):
        calls.append((mode, message))
        return {"status": "delivered", "chat_id": -100123, "message_id": 77}

    monkeypatch.setattr(notice, "_send_public", send)
    return calls


def gate(conn, backend, monkeypatch, *, protected=True, retired=True):
    monkeypatch.setattr(broker_recovery, "reconcile_stop_retirements", lambda *a: retired)
    backend.last_protection_confirmed = protected
    backend.check_stop = lambda *a: None
    broker_recovery.reconcile_swing(conn, "demo", "2026-09-15", backend)


def test_recovery_gate_delivers_once_with_original_fill(recovered, monkeypatch, sender):
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    assert record(conn)["status"] == "pending" and not sender
    gate(conn, backend, monkeypatch)
    gate(conn, backend, monkeypatch)
    assert len(sender) == 1 and sender[0][0] == "demo"
    message = sender[0][1]
    for expected in ("지연", "새 진입이 아닙니다", "11/15 07:13:20 KST", "100.00", "1.000000", "95.00"):
        assert expected in message
    assert "exact-entry" not in message and "parent" not in message
    assert record(conn)["message_id"] == 77
    assert not notice.claim_normal(conn, 1)


@pytest.mark.parametrize("protected,retired", [(False, True), (True, False)])
def test_safety_gate_blocks_notice(recovered, monkeypatch, sender, protected, retired):
    conn, backend, _ = recovered
    with pytest.raises(broker_recovery.RecoveryPending):
        gate(conn, backend, monkeypatch, protected=protected, retired=retired)
    assert record(conn)["status"] == "pending" and not sender


def test_normal_claim_consumes_recovery_queue(recovered, sender):
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    assert notice.claim_normal(conn, 1)
    assert not notice.claim_normal(conn, 1)
    notice.drain(conn, "demo")
    assert not sender
    assert notice.claim_normal(conn, 999)  # legacy normal path without queue
    assert not notice.claim_normal(conn, 999)


def test_recovered_capture_is_once_and_outside_execution_lock(recovered, monkeypatch, sender):
    from live import position_snapshot
    from live.entry_reservations import execution_mutex
    from live.shared_entry_coordinator import database_path
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    captures = []
    def capture_unlocked(source, pos):
        assert source is backend
        with execution_mutex(str(database_path(conn)) + ".btc-execution.lock"):
            captures.append(pos.id)
        return {}
    monkeypatch.setattr(position_snapshot, "capture_swing_snapshot", capture_unlocked)
    gate(conn, backend, monkeypatch)
    gate(conn, backend, monkeypatch)
    assert captures == [1] and len(sender) == 1
    assert "account_snapshot" in record(conn)


@pytest.mark.parametrize("crash", [False, True])
def test_restart_never_retries_unknown_or_claimed(recovered, monkeypatch, tmp_path, crash):
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    calls = []

    async def unknown(*args):
        calls.append(args)
        if crash:
            raise KeyboardInterrupt("crash after request")
        raise TimeoutError("accepted but response lost")

    monkeypatch.setattr(notice, "_send_public", unknown)
    if crash:
        with pytest.raises(KeyboardInterrupt):
            notice.drain(conn, "demo")
    else:
        notice.drain(conn, "demo")
    fresh = tracking.get_connection(tmp_path / "root.sqlite")
    try:
        notice.drain(fresh, "demo")
        assert len(calls) == 1
        assert record(fresh)["status"] == ("attempted" if crash else "unknown")
    finally:
        fresh.close()


@pytest.mark.parametrize("change", ["closed", "qty", "receipt"])
def test_stale_queue_is_superseded(recovered, sender, change):
    conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    if change == "closed":
        conn.execute("DELETE FROM btc_positions WHERE mode='swing'")
        conn.commit()
    elif change == "qty":
        pos = tracking.load_open_positions(conn, "swing")[0]
        pos.qty = .5
        tracking.save_position(conn, pos)
    else:
        tracking.set_meta(conn, "swing_entry_receipt:exact-entry", {}, "swing")
    notice.drain(conn, "demo")
    assert record(conn)["status"] == "superseded" and not sender


def test_pending_survives_restart_without_historical_backfill(recovered, sender, tmp_path):
    _conn, backend, _ = recovered
    assert backend.recover_pending_entry()
    fresh = tracking.get_connection(tmp_path / "root.sqlite")
    try:
        notice.drain(fresh, "shadow")
        assert record(fresh)["status"] == "pending"
        notice.drain(fresh, "demo")
        assert len(sender) == 1
        fresh.execute("DELETE FROM btc_meta WHERE key LIKE 'swing_entry_notice:%'")
        fresh.commit()
        notice.drain(fresh, "demo")
        assert len(sender) == 1
    finally:
        fresh.close()


def test_outbox_failure_rolls_back_position_and_receipt(recovered, monkeypatch):
    conn, backend, _ = recovered

    def fail(*a, **kw):
        raise ValueError("cannot enqueue")

    monkeypatch.setattr(notice, "enqueue", fail)
    assert not backend.recover_pending_entry()
    assert tracking.load_open_positions(conn, "swing") == []
    assert tracking.get_meta(conn, "swing_entry_receipt:exact-entry", "swing") is None
    assert tracking.get_meta(conn, "swing_entry_pending", "swing")


def test_direct_bot_public_route_single_attempt(monkeypatch):
    from live import telegram_reporter
    calls = []
    logger = logging.getLogger("httpx")
    monkeypatch.setattr(logger, "level", logging.INFO)

    class Bot:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, **kw):
            assert logger.getEffectiveLevel() >= logging.WARNING
            calls.append(kw)
            raise TimeoutError("unknown")

    monkeypatch.setitem(sys.modules, "telegram", SimpleNamespace(Bot=Bot))
    monkeypatch.setattr(telegram_reporter, "_load_env", lambda: None)
    monkeypatch.setattr(telegram_reporter, "_resolve_channel", lambda *a, **kw: -100123)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "private-chat")
    with pytest.raises(TimeoutError):
        asyncio.run(notice._send_public("demo", "message"))
    assert calls == [{"chat_id": -100123, "text": "message"}]
    assert logger.level == logging.INFO


def test_missing_public_channel_is_not_delivery(monkeypatch):
    from live import telegram_reporter
    monkeypatch.setattr(telegram_reporter, "_load_env", lambda: None)
    monkeypatch.setattr(telegram_reporter, "_resolve_channel", lambda *a, **kw: None)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    assert asyncio.run(notice._send_public("demo", "message"))["status"] == "unconfigured"


@pytest.mark.parametrize("message_id,chat_id", [(0, -100123), (True, -100123), (1, None)])
def test_invalid_delivery_receipt_is_unknown(monkeypatch, message_id, chat_id):
    from live import telegram_reporter

    class Bot:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def send_message(self, **kw):
            return SimpleNamespace(message_id=message_id, chat=SimpleNamespace(id=chat_id))

    monkeypatch.setitem(sys.modules, "telegram", SimpleNamespace(Bot=Bot))
    monkeypatch.setattr(telegram_reporter, "_load_env", lambda: None)
    monkeypatch.setattr(telegram_reporter, "_resolve_channel", lambda *a, **kw: -100123)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test")
    assert asyncio.run(notice._send_public("demo", "message"))["status"] == "unknown"


@pytest.mark.parametrize("claim_failure", [False, True])
def test_normal_entry_after_recovery_does_not_duplicate_or_break_trading(
        recovered, monkeypatch, sender, claim_failure):
    from live import swing

    from .test_swing import _make_tf_data

    conn, recovery_backend, _ = recovered
    backend = swing.VirtualBackend(conn)

    def recovered_open(*args):
        assert recovery_backend.recover_pending_entry()
        pos = tracking.load_open_positions(conn, "swing")[0]
        backend.last_open_snapshot = {"position_id": pos.id, "qty": pos.qty,
                                     "entry_price": pos.entry_price, "entry_fee": pos.entry_fee,
                                     "execution_time_ms": 1700000000000,
                                     "requested_native_stop": pos.sl_price}
        if claim_failure:
            # Persisted malformed state simulates a notice-only failure.
            conn.execute("UPDATE btc_meta SET value='invalid-json' WHERE key=?", (notice.PREFIX + "1",))
            conn.commit()
        return pos.entry_price

    backend.open = recovered_open
    normal = []
    monkeypatch.setattr(swing, "_notify", lambda *args: normal.append(args) or True)
    data = _make_tf_data()
    pos = swing._try_entry(conn, backend, data["30m"].iloc[-1], str(data["30m"].index[-1]),
                           data["4h"], data["1d"], 10000., "demo")
    assert pos is not None and pos.id == 1
    assert len(normal) == (0 if claim_failure else 1)
    notice.drain(conn, "demo")
    assert not sender
