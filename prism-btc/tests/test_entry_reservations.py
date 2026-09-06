from __future__ import annotations

import multiprocessing
import sqlite3

import pytest

from core.portfolio_risk import ProposedEntry
from live.entry_reservations import EntryReservationStore, LockBusy, execution_mutex


def reserve(store, intent="main-1", lane="main", eligibility=lambda pending: True):
    return store.reserve(intent_id=intent, order_link_id="link-" + intent,
                         proposed=ProposedEntry(lane, "long", 2., 100., 90.),
                         eligibility=eligibility)


def update(store, state, qty=0., **kwargs):
    return store.update(intent_id="main-1", order_link_id="link-main-1",
                        state=state, confirmed_filled_qty=qty, **kwargs)


@pytest.fixture
def store(tmp_path):
    return EntryReservationStore(tmp_path / "reservations.sqlite")


def test_duplicate_reopen_and_terminal_tombstone(store):
    assert reserve(store).created
    reopened = EntryReservationStore(store.path)
    assert not reserve(reopened).created
    assert reopened.active()[0].remaining_qty == 2.
    update(reopened, "CANCELLED_CONFIRMED", evidence_ref="order:cancel:1")
    assert reopened.active() == ()
    assert not reserve(reopened).created
    with pytest.raises(ValueError, match="conflicting_intent"):
        reserve(reopened, lane="swing")


def test_unknown_ack_never_release_and_partial_is_remaining_only(store):
    reserve(store)
    assert update(store, "SUBMITTED_UNKNOWN").remaining_qty == 2.
    assert EntryReservationStore(store.path).active()[0].remaining_qty == 2.
    assert update(store, "ACK", order_id="exchange-1").remaining_qty == 2.
    partial = update(store, "PARTIAL", .5, evidence_ref="execution-1")
    assert partial.remaining_qty == 1.5
    assert update(store, "PARTIAL", .5, evidence_ref="execution-1") == partial
    seen = []
    reserve(store, "swing-1", "swing", lambda pending: seen.extend(pending) is None)
    assert len(seen) == 1 and seen[0].remaining_qty == 1.5
    filled = update(store, "FILLED", 2., evidence_ref="execution-2")
    assert filled.remaining_qty == 0
    assert filled.order_id == "exchange-1"
    assert [row.lane for row in store.active()] == ["swing"]


def test_partial_cancel_releases_only_with_final_evidence(store):
    reserve(store)
    update(store, "PARTIAL", .5, evidence_ref="fill-1")
    with pytest.raises(ValueError, match="confirmation_evidence_required"):
        update(store, "CANCELLED_CONFIRMED", .5)
    final = update(store, "CANCELLED_CONFIRMED", .5, evidence_ref="final-1")
    assert final.confirmed_filled_qty == .5 and final.remaining_qty == 0
    assert not store.active()


@pytest.mark.parametrize("state,qty,evidence", [
    ("PARTIAL", .5, None), ("PARTIAL", float("nan"), "bad"),
    ("FILLED", float("inf"), "bad"), ("FILLED", 3., "bad"),
    ("FILLED", 1., "bad"), ("PARTIAL", 0., "bad"),
    ("PARTIAL", 2., "bad"), ("ACK", .5, "bad"),
    ("BOGUS", 0., "bad"), ("PARTIAL", True, "bad"),
])
def test_rejects_invalid_confirmation_without_mutation(store, state, qty, evidence):
    before = reserve(store).reservation
    with pytest.raises(ValueError):
        update(store, state, qty, evidence_ref=evidence)
    assert store.get("main-1") == before


def test_rejects_stale_conflicting_identity_and_evidence(store):
    reserve(store)
    before = update(store, "PARTIAL", 1., order_id="exchange-1", evidence_ref="fill-1")
    for state, qty, kwargs in [
        ("PARTIAL", .5, {"evidence_ref": "stale"}),
        ("ACK", 0., {}),
        ("PARTIAL", 1.5, {"evidence_ref": "fill-1"}),
        ("PARTIAL", 1., {"order_id": "exchange-2"}),
    ]:
        with pytest.raises(ValueError):
            update(store, state, qty, **kwargs)
        assert store.get("main-1") == before
    with pytest.raises(ValueError, match="unknown_order_identity"):
        store.update(intent_id="main-1", order_link_id="wrong", state="PARTIAL",
                     confirmed_filled_qty=1.)
    update(store, "FILLED", 2., evidence_ref="fill-2")
    with pytest.raises(ValueError, match="conflicting_terminal_update"):
        update(store, "CANCELLED_CONFIRMED", 2., evidence_ref="cancel")


def test_callback_denial_exception_and_sql_conflict_roll_back(store):
    with pytest.raises(ValueError, match="entry_ineligible"):
        reserve(store, eligibility=lambda pending: False)
    assert not store.active()

    def broken(pending):
        raise RuntimeError("snapshot_invalid")

    with pytest.raises(RuntimeError, match="snapshot_invalid"):
        reserve(store, eligibility=broken)
    assert not store.active()
    reserve(store)
    with pytest.raises(sqlite3.IntegrityError):
        store.reserve(intent_id="other", order_link_id="link-main-1",
                      proposed=ProposedEntry("main", "long", 2., 100., 90.),
                      eligibility=lambda pending: True)
    assert len(store.active()) == 1


def _try_lock(path, results):
    try:
        with execution_mutex(path):
            results.put("acquired")
    except LockBusy as exc:
        results.put(str(exc))


def test_process_lock_contention_and_release(tmp_path):
    path = tmp_path / "execution.lock"
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    with execution_mutex(path):
        child = ctx.Process(target=_try_lock, args=(path, results))
        child.start()
        assert results.get(timeout=10) == "lock_busy"
        child.join(timeout=10)
        assert child.exitcode == 0
    assert path.exists()  # Keeping the inode avoids split-lock races.
    child = ctx.Process(target=_try_lock, args=(path, results))
    child.start()
    assert results.get(timeout=10) == "acquired"
    child.join(timeout=10)
    assert child.exitcode == 0
    with pytest.raises(RuntimeError):
        with execution_mutex(path):
            raise RuntimeError("crash")
    with execution_mutex(path):
        pass


def _reserve_contender(path, intent, results):
    store = EntryReservationStore(path)
    try:
        reserve(store, intent, eligibility=lambda pending: not pending)
        results.put("reserved")
    except ValueError as exc:
        results.put(str(exc))


def test_atomic_reserve_across_processes(store):
    ctx = multiprocessing.get_context("spawn")
    results = ctx.Queue()
    children = [ctx.Process(target=_reserve_contender,
                            args=(store.path, f"intent-{i}", results)) for i in range(2)]
    for child in children:
        child.start()
    assert sorted(results.get(timeout=10) for _ in children) == ["entry_ineligible", "reserved"]
    for child in children:
        child.join(timeout=10)
        assert child.exitcode == 0
    assert len(store.active()) == 1


def _hold_lock_until_killed(path, ready):
    import time

    with execution_mutex(path):
        ready.put("locked")
        time.sleep(60)


def test_process_death_releases_lock_without_unlink(tmp_path):
    path = tmp_path / "execution.lock"
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Queue()
    child = ctx.Process(target=_hold_lock_until_killed, args=(path, ready))
    child.start()
    try:
        assert ready.get(timeout=10) == "locked"
        with pytest.raises(LockBusy):
            with execution_mutex(path):
                pass
    finally:
        child.terminate()
        child.join(timeout=10)
    assert not child.is_alive()
    assert path.exists()
    with execution_mutex(path):
        pass


def test_reservation_survives_failure_after_commit_before_broker_call(store):
    with pytest.raises(RuntimeError):
        reserve(store)
        raise RuntimeError("process_failed_before_broker")
    reopened = EntryReservationStore(store.path)
    assert not reserve(reopened).created
    assert reopened.active()[0].state == "RESERVED"
