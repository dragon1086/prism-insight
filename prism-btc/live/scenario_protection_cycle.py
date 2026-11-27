"""Model-free broker reconciliation for the demo runner and isolated replay."""
from live.entry_reservations import LockBusy
from live.shared_entry_coordinator import mutation_lock


def reconcile_protection(conn, broker, *, queue_notices=False):
    """Reconcile under the trading lock, retaining notices only for the demo runner."""
    with mutation_lock(conn):
        evidence = broker.reconcile()
        if queue_notices:
            _retain_notices(conn, evidence)
    return evidence


def _retain_notices(conn, evidence):
    from live.scenario_outbox import enqueue

    notices = evidence.get("notices", []) if isinstance(evidence, dict) else []
    if isinstance(notices, list):
        for notice in notices[:100]:
            conn.execute("SAVEPOINT protection_notice")
            try:
                enqueue(conn, notice["event_id"], notice)
                conn.execute("RELEASE protection_notice")
            except Exception:
                conn.execute("ROLLBACK TO protection_notice")
                conn.execute("RELEASE protection_notice")
        conn.commit()


def run_protection_cycle(conn, broker):
    if getattr(broker, "environment", None) != "demo" or getattr(broker, "lane", None) != "MAIN":
        return {"status": "blocked", "reason": "demo_main_required"}
    try:
        evidence = reconcile_protection(conn, broker, queue_notices=True)
        if not isinstance(evidence, dict) or evidence.get("protection_confirmed") is not True:
            return {"status": "blocked", "reason": "protection_not_verified_by_scenario_adapter",
                    "model_called": False}
        observation = evidence.get("observation")
        verified_flat = (
            isinstance(observation, dict)
            and observation.get("exchange_flat") is True
            and observation.get("open_orders") == []
            and observation.get("legacy_fenced") is False
            and evidence.get("protection_status") == "verified_flat_no_exposure"
        )
        return {"status": "protection_checked", "model_called": False, "verified_flat": verified_flat}
    except LockBusy:
        return {"status": "lock_busy", "model_called": False}
    except Exception:
        return {"status": "blocked", "reason": "protection_unconfirmed", "model_called": False}
