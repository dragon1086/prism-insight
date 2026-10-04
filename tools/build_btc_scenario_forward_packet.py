"""Read-only, deterministic BTC demo MAIN operational evidence; not a performance test.

No broker imports, network calls, writes to the source DB, inferred fills, or
promotion decisions. Stored settlement assertions are NOT independent accounting.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import sqlite3


QUERIES = {
    "llm_scenario_control": "SELECT * FROM llm_scenario_control",
    "llm_scenario_decisions": "SELECT * FROM llm_scenario_decisions",
    "llm_scenario_intents": "SELECT * FROM llm_scenario_intents",
    "llm_scenario_children": "SELECT * FROM llm_scenario_children",
    "llm_scenario_settlements": "SELECT * FROM llm_scenario_settlements",
    "llm_scenario_broker_evidence": "SELECT * FROM llm_scenario_broker_evidence",
    "llm_scenario_outbox": "SELECT * FROM llm_scenario_outbox",
}
KINDS = {"entry", "tp", "partial_sl", "native_sl", "exit"}
OUTCOMES = {"wait", "submitted", "pending", "reconciled", "halted", "blocked",
            "lock_busy", "stale_proposal", "reused_scenario", "duplicate", "error",
            "intent_pending", "duplicate_slot", "execution_disabled", "fenced"}
STATUSES = {"QUEUED", "SENDING", "SENT", "UNKNOWN", "FAILED", "SUPPRESSED"}
TERMINAL = {"Filled", "Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def number(value):
    if value is None or isinstance(value, bool):
        raise ValueError("numeric_evidence_missing")
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("numeric_evidence_invalid")
    return result


def require(condition):
    if not condition:
        raise ValueError("evidence_invalid")


def object_json(raw):
    value = json.loads(raw)
    require(isinstance(value, dict))
    return value


def boundary(value):
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None)
    return parsed.timestamp()


def _read(db):
    if not db.is_file():
        raise FileNotFoundError
    with sqlite3.connect(db.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")  # All source tables share one SQLite snapshot.
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        require(set(QUERIES) | {"btc_meta"} <= names)
        data = {name: sorted((dict(r) for r in conn.execute(query)), key=canonical)
                for name, query in QUERIES.items()}
        data["binding"] = [dict(r) for r in conn.execute(
            "SELECT value FROM btc_meta WHERE mode='demo' AND key='shared_entry_policy_v1'")]
        conn.rollback()
    return data


def _scope(data):
    require(len(data["binding"]) == len(data["llm_scenario_control"]) == 1)
    binding = object_json(data["binding"][0]["value"])
    control = object_json(data["llm_scenario_control"][0]["body"])
    main, swing = binding.get("main_uid"), binding.get("swing_uid")
    require(all(isinstance(v, str) and v.isascii() and v.isdigit() and int(v) > 0
                for v in (main, swing)))
    require(main != swing and control.get("main_uid") == main and control.get("version") == 1
            and control.get("state") in {"active", "paused", "transition"})


def _scenario(sid, intents, children, settlement, proposals, asof, start, seen):
    issues = set()
    fills = {}
    starts = []
    open_starts = []
    for intent in intents:
        payload = object_json(intent["payload"])
        require(payload.get("action_id") == intent["id"] and payload.get("scenario_id") == sid)
        candidates = [decision for decision in proposals.get((intent["id"], sid), [])
                      if decision["input_link_valid"] and decision["input_id"] == payload.get("input_id")
                      and decision["action"] == payload.get("action")]
        if not candidates:
            issues.add("MISSING_DECISION_ACTION_LINK")
        elif len(candidates) != 1:
            issues.add("AMBIGUOUS_DECISION_ACTION_LINK")
        else:
            decision = candidates[0]
            starts.append(decision["slot_start"])
            if (payload.get("action") == decision["action"] == "OPEN"
                    and decision["input_link_valid"] and payload.get("input_id") == decision["input_id"]):
                open_starts.append(decision["slot_start"])
    owned = {r["id"] for r in intents}
    for child in children:
        require(child["intent_id"] in owned and child["kind"] in KINDS)
        starts.append(float(number(child["created_at"])))
        request = object_json(child["request"])
        if not child["evidence"]:
            issues.add("MISSING_CHILD_EXECUTION_EVIDENCE")
            continue
        proof = object_json(child["evidence"])
        order = proof["order"]
        oid = child["order_id"]
        require(isinstance(oid, str) and bool(oid) and order.get("orderId") == oid
                and order.get("symbol") == "BTCUSDT" and order.get("side") == request.get("side"))
        require(request.get("side") in {"Buy", "Sell"})
        if child["kind"] != "native_sl":
            require(order.get("orderLinkId") == child["link_id"]
                    and request.get("orderLinkId") == child["link_id"]
                    and request.get("symbol") == "BTCUSDT")
        quantity = Decimal(0)
        local = set()
        for fill in proof["executions"]:
            eid = fill.get("execId")
            require(isinstance(eid, str) and bool(eid) and fill.get("orderId") == oid
                    and fill.get("symbol") == "BTCUSDT" and fill.get("execType") == "Trade"
                    and fill.get("side") == request["side"])
            qty, price, timestamp = (number(fill[k]) for k in ("execQty", "execPrice", "execTime"))
            require(qty > 0 and price > 0 and timestamp >= 0)
            identity = (sid, child["link_id"], canonical(fill))
            require(eid not in seen or seen[eid] == identity)
            seen[eid] = identity
            if eid in local:
                continue  # Identical source-page repetition is counted once.
            local.add(eid)
            quantity += qty
            require(asof is not None and timestamp / 1000 <= number(asof))
            if fill.get("execFee") is None:
                issues.add("MISSING_EXECUTION_FEE")
            else:
                number(fill["execFee"])
            fills[eid] = (child["kind"], qty, float(timestamp / 1000), fill["side"], price,
                          number(fill["execFee"]) if fill.get("execFee") is not None else None)
        require(quantity == number(order["cumExecQty"]) and quantity <= number(order["qty"]))
    entry = sum((v[1] for v in fills.values() if v[0] == "entry"), Decimal(0))
    exits = sum((v[1] for v in fills.values() if v[0] != "entry"), Decimal(0))
    roles_at_time = {}
    for kind, _, when, _, _, _ in fills.values():
        roles_at_time.setdefault(when, set()).add("entry" if kind == "entry" else "exit")
    # Execution IDs are identifiers, not exchange sequencing evidence.
    require(all(len(roles) == 1 for roles in roles_at_time.values()))
    position = average = gross = Decimal(0)
    for _, (kind, qty, _, side, price, _) in sorted(fills.items(), key=lambda item: (item[1][2], item[0])):
        signed = qty if side == "Buy" else -qty
        require((position == 0 or position * signed > 0) if kind == "entry"
                else position * signed < 0 and qty <= abs(position))
        if kind == "entry":
            average = (abs(position) * average + qty * price) / (abs(position) + qty)
        else:
            gross += (price - average) * qty * (1 if position > 0 else -1)
        position += signed
    recorded = settlement is not None
    amounts = None
    if recorded:
        record = object_json(settlement["evidence"])
        require(record.get("scenario_id") == sid)
        require(all(record.get(k) is True for k in ("flat_confirmed", "orders_terminal",
                "executions_complete", "fees_complete", "funding_complete")))
        require(entry == exits and set(record["execution_ids"]) == set(fills)
                and len(record["execution_ids"]) == len(fills)
                and (("no_fills_confirmed" not in record or record["no_fills_confirmed"] is False) if fills
                     else record.get("no_fills_confirmed") is True))
        require(all(c["evidence"] and object_json(c["evidence"])["order"].get("orderStatus") in TERMINAL
                    for c in children))
        amounts = {k: str(number(record[k])) for k in ("gross_pnl", "fees", "funding_net", "net_pnl")}
        require(all(v[5] is not None for v in fills.values()))
        require(abs(number(amounts["gross_pnl"]) - gross) <= Decimal("0.00000001")
                and abs(number(amounts["fees"]) - sum((v[5] for v in fills.values()), Decimal(0)))
                <= Decimal("0.00000001"))
        require(abs(number(amounts["net_pnl"]) - (number(amounts["gross_pnl"])
                - number(amounts["fees"]) + number(amounts["funding_net"]))) <= Decimal("0.00000001"))
        if not fills:
            require(all(number(v) == 0 for v in amounts.values()))
    first = min(starts) if starts else None
    # All rows remain included; the boundary never selects only completed winners.
    cohort = "NOT_SET" if start is None else (
        "CARRY_IN" if first is not None and first < start else
        "PROSPECTIVE" if open_starts and min(open_starts) >= start else "START_UNKNOWN")
    return dict(scenario_ref=digest(sid), intent_refs=sorted(digest(v) for v in owned),
                execution_refs=sorted(digest(v) for v in fills), entry_quantity=str(entry),
                exit_quantity=str(exits), remaining_quantity=str(entry-exits),
                lifecycle=("CANCELLED_UNFILLED" if recorded and not fills else "FLAT_SETTLEMENT_RECORDED"
                           if recorded else "NO_EXECUTIONS_OBSERVED" if not fills else "OPEN_OR_UNSETTLED"),
                stored_settlement_checks_passed=recorded, recorded_settlement_amounts=amounts,
                independent_accounting_complete=False, settlement_timestamp=None,
                first_observed_time=first, open_decision_slot_start=min(open_starts) if open_starts else None,
                cohort=cohort, issues=sorted(issues))


def build_packet(db, prospective_start=None):
    start = boundary(prospective_start)
    packet = dict(schema_version=1, packet_kind="BTC_SCENARIO_OPERATIONAL_AUDIT",
                  scope="BTCUSDT_DEMO_MAIN", status="INPUT_UNAVAILABLE", verdict="CONTINUE_CAPTURE",
                  profitability_proven=False, auto_promotion=False, prospective_start=start,
                  exporter_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  trading_code_version=None, source_asof=None,
                  decision_scope="GLOBAL_STORED_HISTORY_NOT_POST_BOUNDARY",
                  insufficiency_reasons=["MISSING_FUNDING_SCHEDULE", "MISSING_FINANCIAL_SCENARIO_FK",
                                         "MISSING_TRADING_CODE_VERSION", "MISSING_SETTLEMENT_TIMESTAMP"],
                  scenarios=None, decision_outcomes=None, notice_statuses=None, notice_receipts=None,
                  notice_scope="GLOBAL_OUTBOX_NOT_SCENARIO_LINKED",
                  lifecycle_sequence_proven=False, next_scenario_sequence_proven=False)
    stage = "read_snapshot"
    try:
        data = _read(Path(db))
        packet["input_sha256"] = digest(data)
        stage = "validate_scope"
        _scope(data)
        stage = "source_timestamps"
        timestamps = [float(number(r["captured_at"])) for r in data["llm_scenario_broker_evidence"]]
        asof = max(timestamps) if timestamps else None
        packet["source_asof"] = asof
        stage = "decision_links"
        proposals, outcomes = {}, Counter()
        for row in data["llm_scenario_decisions"]:
            proposal = object_json(row["proposal"]) if row["proposal"] else {}
            if proposal.get("action_id") and proposal.get("scenario_id"):
                key = (proposal["action_id"], proposal["scenario_id"])
                slot_start = float(number(row["slot"]) * 300)
                context = object_json(row["context"]) if row.get("context") else {}
                proposals.setdefault(key, []).append(dict(slot_start=slot_start, action=proposal.get("action"),
                    input_id=proposal.get("input_id"), input_link_valid=(
                        isinstance(proposal.get("input_id"), str) and bool(proposal["input_id"])
                        and proposal["input_id"] == context.get("input_id"))))
            result = object_json(row["outcome"]) if row["outcome"] else {}
            outcome = result.get("status", "MISSING")
            outcomes[outcome if outcome in OUTCOMES or outcome == "MISSING" else "OTHER"] += 1
        intents, children = data["llm_scenario_intents"], data["llm_scenario_children"]
        settlements = data["llm_scenario_settlements"]
        sids = {r["scenario_id"] for r in intents + children + settlements}
        for rows, key in ((intents, "id"), (children, "link_id"), (settlements, "scenario_id")):
            require(len({r[key] for r in rows}) == len(rows))
        seen = {}
        stage = "scenario_validation"
        scenarios = [_scenario(sid, [r for r in intents if r["scenario_id"] == sid],
                     [r for r in children if r["scenario_id"] == sid],
                     next((r for r in settlements if r["scenario_id"] == sid), None),
                     proposals, asof, start, seen) for sid in sorted(sids)]
        statuses = Counter()
        receipts = 0
        stage = "notice_statuses"
        for row in data["llm_scenario_outbox"]:
            status = row["status"] if row["status"] in STATUSES else "OTHER"
            statuses[status] += 1
            receipts += int(status == "SENT" and type(row["message_id"]) is int and row["message_id"] > 0)
        packet.update(status="AVAILABLE", scenarios=scenarios, decision_outcomes=dict(outcomes),
                      notice_statuses=dict(statuses), notice_receipts=receipts)
    except FileNotFoundError:
        packet["input_error"] = "DATABASE_MISSING"
        packet["input_stage"] = stage
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        packet["input_error"] = "SCHEMA_BINDING_OR_EVIDENCE_INVALID"
        packet["input_stage"] = stage
    packet["packet_id"] = digest(packet)
    return packet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prospective-start", help="Timezone-aware ISO timestamp; retain all carry-in rows")
    args = parser.parse_args()
    if args.db.resolve() == args.output.resolve():
        parser.error("output_must_differ_from_source_database")
    result = build_packet(args.db, args.prospective_start)
    with args.output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return 0 if result["status"] == "AVAILABLE" else 2


if __name__ == "__main__":
    raise SystemExit(main())
