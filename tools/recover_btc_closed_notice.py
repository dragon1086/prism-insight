"""Audit one persisted demo MAIN closure; optionally enqueue, never send or trade."""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prism-btc"))
from live.scenario_control import existing_account_bindings, read_control
from live.scenario_notice import render_notice
from live.shared_entry_coordinator import mutation_lock

TERMINAL = {"Filled", "Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"}
KINDS = {"entry", "exit", "tp", "partial_sl", "native_sl"}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def number(value):
    require(not isinstance(value, bool), "invalid_number")
    result = Decimal(str(value))
    require(result.is_finite(), "nonfinite_number")
    return result


def equal(left, right):
    require(abs(number(left) - number(right)) <= Decimal(".00000001"), "financial_mismatch")


def object_json(value):
    result = json.loads(value)
    require(isinstance(result, dict), "object_required")
    return result


def audit(conn, sid, *, now):
    """Only exact scenario queries. The caller owns a consistent transaction."""
    main, _ = existing_account_bindings(conn)
    control = read_control(conn)
    require(control is not None and control["main_uid"] == main, "demo_main_binding_required")
    eid = "scenario-closed-" + sid
    for table in ("llm_scenario_broker_notices", "llm_scenario_outbox"):
        require(conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone(),
                "notice_schema_required")
    if conn.execute("SELECT 1 FROM llm_scenario_broker_notices WHERE event_id=? "
                    "UNION ALL SELECT 1 FROM llm_scenario_outbox WHERE event_id=? LIMIT 1",
                    (eid, eid)).fetchone():
        return None
    record = conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id=?", (sid,)).fetchone()
    require(record is not None, "persisted_settlement_required")
    settlement = object_json(record[0])
    require(settlement.get("scenario_id") == sid and all(settlement.get(key) is True for key in
            ("flat_confirmed", "orders_terminal", "executions_complete", "fees_complete", "funding_complete")),
            "complete_settlement_required")
    require(settlement.get("no_fills_confirmed", False) is False, "unfilled_not_closure")
    intents = {}
    for row in conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=?", (sid,)):
        payload = object_json(row[1])
        require(payload.get("scenario_id") == sid and payload.get("action_id") == row[0], "intent_identity")
        intents[row[0]] = payload
    opens = [payload for payload in intents.values() if payload.get("action") == "OPEN"]
    require(len(opens) == 1 and opens[0].get("side") in {"LONG", "SHORT"}, "original_open_required")
    entry_side = "Buy" if opens[0]["side"] == "LONG" else "Sell"
    children = conn.execute("SELECT * FROM llm_scenario_children WHERE scenario_id=?", (sid,)).fetchall()
    require(bool(children), "owned_children_required")
    fills, order_ids = {}, set()
    for child in children:
        kind, oid = child["kind"], child["order_id"]
        require(kind in KINDS and child["intent_id"] in intents and child["status"] == "TERMINAL",
                "child_ownership_or_status")
        require(isinstance(oid, str) and oid and oid not in order_ids, "order_identity")
        order_ids.add(oid)
        request, evidence = object_json(child["request"]), object_json(child["evidence"])
        order = evidence["order"]
        side = entry_side if kind == "entry" else ("Sell" if entry_side == "Buy" else "Buy")
        require(request["side"] == order["side"] == side and order["orderId"] == oid
                and order["symbol"] == "BTCUSDT" and order["orderStatus"] in TERMINAL, "order_proof")
        if kind != "native_sl":
            require(order["orderLinkId"] == request["orderLinkId"] == child["link_id"]
                    and request["symbol"] == "BTCUSDT"
                    and request["reduceOnly"] is (kind != "entry")
                    and order["reduceOnly"] is (kind != "entry"), "order_role_proof")
            require(number(order["qty"]) <= number(request["qty"]), "order_quantity")
            if kind == "entry":
                require(number(order["qty"]) == number(request["qty"]), "entry_quantity")
        else:
            require(order["reduceOnly"] is True, "native_exit_proof")
        quantity = Decimal(0)
        # Local retention time is not exchange order creation time (native SLs
        # can be discovered after execution). Do not use it as a fill bound.
        created = number(order["createdTime"]) if "createdTime" in order else Decimal(0)
        require(0 <= created <= number(now) * 1000, "order_time_bounds")
        for fill in evidence["executions"]:
            ident = fill["execId"]
            require(isinstance(ident, str) and ident and ident not in fills, "execution_identity")
            require(fill["orderId"] == oid and fill["side"] == side and fill["symbol"] == "BTCUSDT"
                    and fill["execType"] == "Trade", "execution_order_proof")
            qty, price, when, fee = (number(fill[key]) for key in ("execQty", "execPrice", "execTime", "execFee"))
            require(qty > 0 and price > 0 and created <= when <= number(now) * 1000, "execution_values_or_time")
            quantity += qty
            fills[ident] = (kind, qty, price, when, fee, side)
        require(quantity == number(order["cumExecQty"]) and quantity <= number(order["qty"]), "fill_coverage")
    ids = settlement["execution_ids"]
    require(isinstance(ids, list) and len(ids) == len(set(ids)) and set(ids) == set(fills), "settlement_execution_coverage")
    entries = [row for row in fills.values() if row[0] == "entry"]
    exits = [row for row in fills.values() if row[0] != "entry"]
    entry_qty = sum((row[1] for row in entries), Decimal(0))
    require(entry_qty > 0 and entry_qty == sum((row[1] for row in exits), Decimal(0)), "not_fully_closed")
    roles = {}
    for kind, _, _, when, _, _ in fills.values():
        roles.setdefault(when, set()).add(kind == "entry")
    require(all(len(value) == 1 for value in roles.values()), "ambiguous_same_time_roles")
    position = average = gross = Decimal(0)
    for kind, qty, price, _, _, side in sorted(fills.values(), key=lambda row: row[3]):
        signed = qty if side == "Buy" else -qty
        if kind == "entry":
            require(position == 0 or position * signed > 0, "entry_reversal")
            average = (abs(position) * average + qty * price) / (abs(position) + qty)
        else:
            require(position * signed < 0 and qty <= abs(position), "exit_exceeds_position")
            gross += (price - average) * qty * (1 if position > 0 else -1)
        position += signed
    fees = sum((row[4] for row in fills.values()), Decimal(0))
    equal(settlement["gross_pnl"], gross)
    equal(settlement["fees"], fees)
    funding = number(settlement["funding_net"])
    equal(settlement["net_pnl"], gross - fees + funding)
    return dict(event_id=eid, kind="CLOSED", recovery_confirmation=True,
                timestamp=float(max(row[3] for row in exits) / 1000),
                entry_timestamp=float(min(row[3] for row in entries) / 1000), quantity=float(entry_qty),
                entry_price=float(sum(row[1] * row[2] for row in entries) / entry_qty),
                price=float(sum(row[1] * row[2] for row in exits) / entry_qty),
                settlement_confirmed=True, flat_confirmed=True, orders_terminal=True,
                fees=float(fees), funding=float(funding), net_pnl=float(number(settlement["net_pnl"])))


def recover(db, sid, *, apply=False, now=None):
    require(isinstance(sid, str) and 0 < len(sid) <= 180, "explicit_scenario_required")
    db = Path(db).resolve()
    require(db.is_file(), "existing_database_required")
    result = {"scenario_ref": hashlib.sha256(sid.encode()).hexdigest()[:16], "sent": False}
    with sqlite3.connect(db.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True) as conn:
        conn.row_factory = sqlite3.Row
        if not apply:
            conn.execute("PRAGMA query_only=ON")
        def run():
            conn.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
            event = audit(conn, sid, now=time.time() if now is None else now)
            if event is None:
                conn.rollback()
                return dict(result, status="SKIPPED_EXISTING_NOTICE")
            preview = render_notice(event)
            if apply:
                conn.execute("INSERT INTO llm_scenario_broker_notices VALUES(?,?,?)",
                             (event["event_id"], sid, json.dumps(event, sort_keys=True, allow_nan=False)))
                conn.execute("INSERT INTO llm_scenario_outbox(event_id,kind,body,status) VALUES(?,?,?,'QUEUED')",
                             (event["event_id"], "CLOSED", preview))
                conn.commit()
            else:
                conn.rollback()
            return dict(result, status="QUEUED_NOT_SENT" if apply else "DRY_RUN_ELIGIBLE", preview=preview)
        if apply:
            with mutation_lock(conn):
                return run()
        return run()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--apply", action="store_true", help="Enqueue only; requires operator authorization")
    args = parser.parse_args()
    try:
        result = recover(args.db, args.scenario_id, apply=args.apply)
    except (ValueError, TypeError, KeyError, ArithmeticError, sqlite3.Error, OSError):
        print(json.dumps({"status": "REJECTED", "reason": "evidence_validation_failed", "sent": False}))
        return 2
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
