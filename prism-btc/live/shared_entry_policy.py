"""Explicit, DEMO-only shared entry policy management. Never submits orders.

Pause blocks new entries, not recovery or protection. Probe uses a read-only
database and GET snapshots; enable alone binds the verified account identities.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sqlite3
import time
from types import SimpleNamespace
import uuid

from live import shared_entry_coordinator as coordinator, tracking

KEY = "shared_entry_policy_v1"
FIELDS = {"version", "state", "policy_id", "heat", "slippage", "main_uid", "swing_uid"}


def fraction(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 < value < 1:
        raise ValueError("invalid_policy_fraction")
    return float(value)


def _read(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='btc_meta'").fetchone():
        return None
    row = conn.execute("SELECT value FROM btc_meta WHERE mode='demo' AND key=?", (KEY,)).fetchone()
    if row is None:
        return None
    try:
        policy = json.loads(row[0])
        if (not isinstance(policy, dict) or set(policy) != FIELDS
                or type(policy["version"]) is not int or policy["version"] != 1
                or policy["state"] not in ("active", "paused")
                or not isinstance(policy["policy_id"], str)
                or str(uuid.UUID(policy["policy_id"])) != policy["policy_id"]):
            raise ValueError
        # A paused tombstone can safely replace corrupt/unconfigured policy.
        if policy["state"] == "paused" and all(policy[k] is None for k in ("heat", "slippage", "main_uid", "swing_uid")):
            return policy
        fraction(policy["heat"])
        fraction(policy["slippage"])
        uids = [policy[k] for k in ("main_uid", "swing_uid")]
        if any(not isinstance(uid, str) or not uid.isascii() or not uid.isdigit() or int(uid) <= 0
               or str(int(uid)) != uid for uid in uids) or uids[0] == uids[1]:
            raise ValueError
        return policy
    except (ValueError, TypeError, KeyError, AttributeError):
        raise ValueError("shared_entry_policy_malformed") from None


def _conflict(values=None):
    values = coordinator.configuration_environment() if values is None else values
    if any(key.startswith(coordinator.PREFIX) for key in values):
        raise ValueError("shared_entry_policy_source_conflict")


def runtime_configuration(conn, *, values=None):
    policy = _read(conn)
    if policy is None:
        return False, None
    _conflict(values)
    if policy["state"] != "active":
        raise ValueError("shared_entry_policy_paused")
    return True, coordinator.Config(*(policy[k] for k in ("heat", "slippage", "main_uid", "swing_uid")))


def policy_status(conn):
    """Sanitized read-only API: never returns account IDs or broker payloads."""
    try:
        values = coordinator.configuration_environment()
        policy = _read(conn)
        if policy is None:
            try:
                legacy = coordinator.configuration(values=values)
            except (ValueError, KeyError, TypeError):
                return {"state": "error", "source": "legacy_env", "reason": "legacy_policy_invalid",
                        "policy_id": None, "heat": None, "slippage": None}
            return {"state": "active" if legacy else "unconfigured", "source": "legacy_env" if legacy else "none",
                    "reason": "legacy_policy_active" if legacy else "runtime_policy_absent", "policy_id": None,
                    "heat": legacy.heat if legacy else None, "slippage": legacy.slippage if legacy else None}
        _conflict(values)
        return {k: policy[k] for k in ("state", "policy_id", "heat", "slippage")} | {
            "source": "runtime", "reason": "shared_entry_policy_" + policy["state"]}
    except (ValueError, sqlite3.Error) as exc:
        reason = str(exc) if isinstance(exc, ValueError) else "shared_entry_policy_database_error"
        return {"state": "error", "source": "runtime", "reason": reason,
                "policy_id": None, "heat": None, "slippage": None}


class _EmptyReservations:
    def active(self):
        return ()


def _flat_local(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='entry_reservations'").fetchone():
        if conn.execute("SELECT 1 FROM entry_reservations WHERE state IS NULL OR state NOT IN ('FILLED','CANCELLED_CONFIRMED') LIMIT 1").fetchone():
            raise ValueError("policy_pending_reservations")
    for mode in ("demo", "swing"):
        if tracking.load_open_positions(conn, mode):
            raise ValueError("policy_local_position_not_flat")
        retirements = tracking.get_meta(conn, "stop_retirements_v1", mode)
        if retirements is not None and retirements != []:
            raise ValueError("policy_unresolved_lifecycle")
        tp = tracking.get_meta(conn, "tp_intent", mode) if mode == "demo" else None
        if tp and (not isinstance(tp, dict) or tp.get("state") not in ("FULFILLED", "FLAT_TERMINAL")):
            raise ValueError("policy_unresolved_lifecycle")
        keys = ("pending_order", "pending_reduce", "stop_submission_intent") if mode == "demo" else (
            "swing_entry_pending", "swing_close_pending", "swing_stop_intent")
        for key in keys:
            value = tracking.get_meta(conn, key, mode)
            if value and (not isinstance(value, dict) or value.get("status") not in ("SETTLED", "CONFIRMED_CLOSED")):
                raise ValueError("policy_unresolved_lifecycle")


def _verify_historical_native(conn, session):
    """Read-only parent/execution proof, ONLY after both accounts are flat."""
    native = tracking.get_meta(conn, "native_entry_intent", "demo")
    if native is None:
        return
    try:
        if not isinstance(native, dict) or native.get("side") not in ("long", "short"):
            raise ValueError
        link = native["link_id"]
        if not isinstance(link, str) or not link:
            raise ValueError
        qty, price = coordinator._positive(native["qty"]), coordinator._positive(native["price"])
        parents = coordinator._pages(session, "get_order_history", "orderId", orderLinkId=link)
        if len(parents) != 1:
            raise ValueError
        parent = parents[0]
        if (parent.get("orderLinkId") != link or (native.get("order_id") and parent["orderId"] != native["order_id"])
                or parent.get("symbol") != "BTCUSDT" or parent.get("positionIdx") != 0
                or parent.get("side") != ("Buy" if native["side"] == "long" else "Sell")
                or parent.get("reduceOnly") is not False or parent.get("orderType") != "Limit"
                or not math.isclose(coordinator._positive(parent["qty"]), qty, rel_tol=0, abs_tol=1e-9)
                or not math.isclose(coordinator._positive(parent["price"]), price, rel_tol=0, abs_tol=1e-8)):
            raise ValueError
        cumulative = coordinator._nonnegative(parent["cumExecQty"])
        status = parent.get("orderStatus")
        if (status not in ("Filled", "Cancelled", "Rejected", "PartiallyFilledCanceled")
                or coordinator._nonnegative(parent["leavesQty"]) != 0 or cumulative > qty
                or (status == "Filled" and not math.isclose(cumulative, qty, rel_tol=0, abs_tol=1e-9))
                or (status == "Rejected" and cumulative != 0)):
            raise ValueError
        executions = coordinator._pages(session, "get_executions", "execId", orderId=parent["orderId"])
        filled = 0.
        for execution in executions:
            if (execution.get("orderId") != parent["orderId"] or execution.get("orderLinkId") != link
                    or execution.get("symbol") != "BTCUSDT" or execution.get("side") != parent["side"]
                    or execution.get("execType") != "Trade"):
                raise ValueError
            filled += coordinator._positive(execution["execQty"])
            coordinator._positive(execution["execPrice"])
        if not math.isclose(filled, cumulative, rel_tol=0, abs_tol=1e-9):
            raise ValueError
    except (ValueError, KeyError, TypeError):
        raise ValueError("policy_native_terminal_unconfirmed") from None


def _preflight(conn, heat, slippage, demo, sessions):
    started = time.monotonic()
    if demo is not True:
        raise ValueError("explicit_demo_required")
    fraction(heat)
    fraction(slippage)
    _conflict()
    if conn.in_transaction:
        raise ValueError("policy_transaction_active")
    _flat_local(conn)
    coordinator.require_demo_sessions(sessions)
    uids = []
    for lane in ("main", "swing"):
        uid = coordinator._call(sessions[lane], "get_api_key_information")["result"].get("userID")
        if isinstance(uid, bool) or not isinstance(uid, (str, int)) or not str(uid).isascii() or not str(uid).isdigit() or int(uid) <= 0:
            raise ValueError("policy_account_identity_unknown")
        uids.append(str(int(uid)))
    if uids[0] == uids[1]:
        raise ValueError("distinct_account_uid_required")
    config = coordinator.Config(heat, slippage, *uids)
    adapter = SimpleNamespace(conn=conn, sess=sessions["main"], mode="demo")
    _, lanes = coordinator.snapshot(adapter, "main", config, _EmptyReservations(), sessions=sessions, check_policy=False)
    if any(lane.qty or lane.orders for lane in lanes):
        raise ValueError("policy_broker_not_flat")
    _verify_historical_native(conn, sessions["main"])
    if time.monotonic() - started > 10:
        raise ValueError("policy_snapshot_too_old")
    return config


def manage(conn, action, *, heat=None, slippage=None, demo=False, sessions=None):
    """Caller supplies a file-backed connection; SQL writes are application-owned."""
    if action == "status":
        return policy_status(conn)
    if action not in ("probe", "enable", "pause"):
        raise ValueError("unknown_policy_action")
    coordinator.database_path(conn)
    if conn.in_transaction:
        raise ValueError("policy_transaction_active")
    with coordinator.mutation_lock(conn):
        if action == "pause":
            try:
                policy = _read(conn)
            except ValueError:
                policy = None
            policy = policy or dict(version=1, policy_id=str(uuid.uuid4()), heat=None, slippage=None, main_uid=None, swing_uid=None)
            policy["state"] = "paused"
        else:
            old = _read(conn)
            config = _preflight(conn, heat, slippage, demo, sessions)
            if old and old["main_uid"] is not None and (old["main_uid"], old["swing_uid"]) != (config.main_uid, config.swing_uid):
                raise ValueError("policy_account_binding_changed")
            if action == "probe":
                return {"state": "ready", "reason": "flat_demo_snapshot_verified", "heat": heat, "slippage": slippage}
            policy = dict(version=1, state="active", policy_id=str(uuid.uuid4()), heat=config.heat,
                          slippage=config.slippage, main_uid=config.main_uid, swing_uid=config.swing_uid)
            if old and all(old[k] == v for k, v in policy.items() if k != "policy_id"):
                return policy_status(conn)
        # The external GETs above have completed before the short transaction.
        with conn:
            conn.execute("INSERT INTO btc_meta(mode,key,value) VALUES('demo',?,?) ON CONFLICT(mode,key) DO UPDATE SET value=excluded.value",
                         (KEY, json.dumps(policy, allow_nan=False, sort_keys=True)))
            public = {key: policy[key] for key in ("state", "policy_id", "heat", "slippage")}
            tracking.log_event(conn, "shared_policy_control",
                               json.dumps({"action": action, **public}, sort_keys=True),
                               mode="demo", commit=False)
        return policy_status(conn)


def _make_sessions():
    # Do not mutate process environment while reading existing credentials.
    from pybit.unified_trading import HTTP
    values = coordinator.configuration_environment()
    if any(key.startswith(coordinator.PREFIX) for key in values):
        raise ValueError("shared_entry_policy_source_conflict")
    sessions = {}
    for lane, prefix in (("main", "BYBIT_DEMO_"), ("swing", "BYBIT_SWING_DEMO_")):
        if not values.get(prefix + "API_KEY") or not values.get(prefix + "API_SECRET"):
            raise ValueError("policy_demo_credentials_unavailable")
        sessions[lane] = HTTP(demo=True, api_key=values[prefix + "API_KEY"], api_secret=values[prefix + "API_SECRET"])
    return sessions


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "probe", "enable", "pause"))
    parser.add_argument("--root-db", required=True, type=Path)
    parser.add_argument("--heat", type=float)
    parser.add_argument("--slippage", type=float)
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.action in ("probe", "enable"):
            if not args.demo:
                raise ValueError("explicit_demo_required")
            fraction(args.heat)
            fraction(args.slippage)
        mode = "ro" if args.action in ("status", "probe") else "rw"
        with closing(sqlite3.connect(args.root_db.resolve(strict=True).as_uri() + "?mode=" + mode, uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            if mode == "ro":
                conn.execute("PRAGMA query_only=ON")
            result = manage(conn, args.action, heat=args.heat, slippage=args.slippage, demo=args.demo,
                            sessions=_make_sessions() if args.action in ("probe", "enable") else None)
        print(json.dumps(result, sort_keys=True))
        return 1 if result["state"] == "error" else 0
    except Exception as exc:
        # Never interpolate exceptions from SDKs: they may contain credentials.
        safe_reasons = {
            "explicit_demo_required", "invalid_policy_fraction", "shared_entry_policy_source_conflict",
            "shared_entry_policy_malformed", "policy_transaction_active", "policy_pending_reservations",
            "policy_local_position_not_flat", "policy_unresolved_lifecycle", "shared_entry_demo_endpoint_required",
            "policy_account_identity_unknown", "distinct_account_uid_required", "policy_broker_not_flat",
            "policy_demo_credentials_unavailable", "durable_shared_database_required",
            "policy_account_binding_changed",
            "policy_native_terminal_unconfirmed",
            "policy_snapshot_too_old",
        }
        reason = str(exc) if type(exc) is ValueError and str(exc) in safe_reasons else "policy_operation_failed"
        print(json.dumps({"state": "error", "reason": reason}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
