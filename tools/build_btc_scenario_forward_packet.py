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
import importlib.util
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
OPTIONAL = {
    "llm_scenario_broker_notices": "SELECT * FROM llm_scenario_broker_notices",
    "llm_scenario_audit_manifests": "SELECT * FROM llm_scenario_audit_manifests",
    "llm_scenario_audit_events": "SELECT * FROM llm_scenario_audit_events",
}


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
        for name, query in OPTIONAL.items():
            data[name] = sorted((dict(r) for r in conn.execute(query)), key=canonical) if name in names else []
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
                last_execution_timestamp=max((v[2] for v in fills.values()), default=None),
                first_observed_time=first, open_decision_slot_start=min(open_starts) if open_starts else None,
                cohort=cohort, issues=sorted(issues))


def _notice_links(sid, data):
    links, conflict = [], False
    notices = [r for r in data["llm_scenario_broker_notices"] if r.get("scenario_id") == sid]
    for row in notices:
        try:
            event = object_json(row["body"])
            require(event.get("event_id") == row["event_id"])
            require(sum(r.get("event_id") == row["event_id"] for r in data["llm_scenario_broker_notices"]) == 1)
            matches = [r for r in data["llm_scenario_outbox"] if r["event_id"] == row["event_id"]]
            require(len(matches) <= 1)
            outbox = matches[0] if matches else {}
            require(not outbox or outbox["kind"] == event["kind"])
            kind = event["kind"] if event["kind"] in {"FILLED", "PARTIAL", "PROTECTION", "CLOSED", "PENDING", "RESOLVED"} else "OTHER"
            status = outbox.get("status", "NOT_ENQUEUED")
            links.append(dict(event_ref=digest(row["event_id"]), kind=kind,
                status=status if status in STATUSES or status == "NOT_ENQUEUED" else "OTHER",
                receipt_confirmed=status == "SENT" and type(outbox.get("message_id")) is int and outbox["message_id"] > 0))
        except (ValueError, TypeError, KeyError):
            conflict = True
    return dict(notice_links=links, notice_link_status="CONFLICT" if conflict else "LINKED" if links else "MISSING")


def _accounting_source(sid, body, scenario, ledger_children):
    """Re-run the production pure calculator, plus raw funding-source checks.

    This is source re-reconciliation, NOT an independent accounting algorithm.
    The existing exporter separately verifies average-cost PnL and execution fees.
    """
    evidence, owned = body["financial_evidence"], body["owned_orders"]
    schedule, source = body["funding_schedule"], body["funding_source"]
    raw, instruments = source["raw_rows"], source["instruments"]
    start, end = number(schedule["start_ms"]), number(schedule["end_ms"])
    require(schedule.get("complete") is True and 0 < end-start <= 7*86400000 and len(raw) < 200)
    require(len(instruments) == 1 and instruments[0].get("symbol") == "BTCUSDT")
    interval = number(instruments[0]["fundingInterval"]) * 60000
    cursor = number(source["next_funding_time"])
    require(interval >= 60000 and cursor > end and cursor-end <= interval)
    expected = set()
    cursor -= interval
    while cursor >= start:
        if cursor <= end:
            expected.add(cursor)
        cursor -= interval
    actual = {}
    for row in raw:
        require(row.get("symbol") == "BTCUSDT")
        when = number(row["fundingRateTimestamp"])
        require(when not in actual and start <= when <= end)
        actual[when] = number(row["fundingRate"])
    normalized = {number(r["timestamp"]): number(r["rate"]) for r in schedule["events"]}
    require(len(normalized) == len(schedule["events"]) and actual == normalized and set(actual) == expected)
    require(number(evidence["start_ms"]) == start and number(evidence["end_ms"]) == end)
    trades = evidence["trades"]
    require(len({r["execution_id"] for r in trades}) == len(trades))
    require({digest(r["execution_id"]) for r in trades} == set(scenario["execution_refs"]))
    children = body["children"]
    require(all(r.get("scenario_id") == sid for r in children))
    ledger_by_order = {r["order_id"]: r for r in ledger_children}
    require({r["order_id"] for r in children} == set(ledger_by_order))
    for child in children:
        stored = ledger_by_order[child["order_id"]]
        require(child["intent_id"] == stored["intent_id"] and child["link_id"] == stored["link_id"]
                and child["kind"] == stored["kind"])
        require(child["evidence"]["executions"] == object_json(stored["evidence"])["executions"])
    require({r["order_id"] for r in children} == {r["order_id"] for r in owned})
    raw_executions = {}
    for child in children:
        proof = child["evidence"]
        for execution in proof["executions"]:
            require(execution["execId"] not in raw_executions)
            raw_executions[execution["execId"]] = execution
    require(set(raw_executions) == {r["execution_id"] for r in trades})
    require(all(raw_executions[r["execution_id"]] == r["raw_execution"] for r in trades))
    source_path = Path(__file__).resolve().parents[1] / "prism-btc/live/scenario_accounting.py"
    spec = importlib.util.spec_from_file_location("_packet_accounting", source_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.reconcile_scenario(sid, evidence, owned, body["observation"], funding_schedule=schedule)
    require(result.get("accounting_complete") is True and result == body["result"])
    if scenario["stored_settlement_checks_passed"]:
        require(result.get("settlement", {}).get("scenario_id") == sid)
        for key, value in scenario["recorded_settlement_amounts"].items():
            require(abs(number(result[key])-number(value)) <= Decimal("0.00000001"))
    return dict(status="SOURCE_RECONCILIATION_VERIFIED", algorithm="PRODUCTION_CALCULATOR_RERUN_PLUS_EXPORTER_PNL_AND_RAW_SOURCE_CHECKS",
                calculator_source_sha256=hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source_ref=digest(body), funding_coverage="RAW_SOURCE_VERIFIED",
                financial_refs=sorted(digest(r["execution_id"]) for r in trades),
                funding_refs=sorted(digest(r["transaction_id"]) for r in evidence["funding"]))


def _accounting_candidate(sid, body, scenario, ledger_children):
    current = {r["order_id"]: r for r in ledger_children}
    past = body["children"]
    require(len({r["order_id"] for r in past}) == len(past))
    require({r["order_id"] for r in past} <= set(current))
    past_executions = {}
    for child in past:
        stored = current[child["order_id"]]
        require(child["scenario_id"] == sid and all(child[k] == stored[k] for k in ("intent_id", "link_id", "kind")))
        current_execs = {r["execId"]: r for r in object_json(stored["evidence"])["executions"]}
        for execution in child["evidence"]["executions"]:
            eid = execution["execId"]
            require(eid not in past_executions and current_execs.get(eid) == execution)
            past_executions[eid] = execution
    trades = body["financial_evidence"]["trades"]
    require(len({r["execution_id"] for r in trades}) == len(trades))
    pending = body.get("result", {}).get("status") == "pending" and body["result"].get("accounting_complete") is False
    trade_ids = {r["execution_id"] for r in trades}
    require(trade_ids <= set(past_executions) if pending else trade_ids == set(past_executions))
    require(all(past_executions[r["execution_id"]] == r["raw_execution"] for r in trades))
    refs = {digest(eid) for eid in past_executions}
    require(refs <= set(scenario["execution_refs"]))
    complete = refs == set(scenario["execution_refs"]) and {r["order_id"] for r in past} == set(current)
    if pending:
        return False  # Captured missing fees/funding is unproven, not contradictory.
    if scenario["stored_settlement_checks_passed"]:
        complete = complete and body.get("result", {}).get("settlement") is not None
        complete = complete and all(r.get("terminal") is True for r in body["owned_orders"])
        complete = complete and body["observation"].get("open_orders") == []
    return complete


def _event_loaded_status(manifest, kind):
    """Unused judgement imports must not invalidate a protection-only run.

    Old manifests have no domain evidence: they stay UNKNOWN even if an old
    overall assertion says VERIFIED. Intent commitment requires both domains.
    """
    domains = {"decision_input": ("judgment_loaded_code_status",),
               "intent_committed": ("judgment_loaded_code_status", "execution_loaded_code_status"),
               "exchange_call": ("execution_loaded_code_status",),
               "accounting_observation": ("execution_loaded_code_status",),
               "settlement_recorded": ("execution_loaded_code_status",)}
    fields = domains.get(kind)
    if fields is None:
        return "UNKNOWN"
    states = {manifest.get(field, "UNKNOWN") for field in fields}
    return "MIXED" if "MIXED" in states else "VERIFIED" if states == {"VERIFIED"} else "UNKNOWN"


def _audit_links(sid, scenario, data):
    rows = [r for r in data["llm_scenario_audit_events"] if r.get("scenario_id") == sid]
    # Initial decision and exchange mutation events can precede a scenario FK.
    # Only exact durable IDs/input IDs may attach them; proximity never does.
    ledger_children = [r for r in data["llm_scenario_children"] if r["scenario_id"] == sid]
    decisions = [r for r in data["llm_scenario_decisions"] if r.get("proposal")
                 and object_json(r["proposal"]).get("scenario_id") == sid]
    for row in data["llm_scenario_audit_events"]:
        if row.get("scenario_id") is not None:
            continue
        try:
            body = object_json(row["body"])
            if row["kind"] == "decision_input" and any(
                    r["slot"] == row["decision_slot"] and body.get("input_id")
                    == object_json(r["proposal"]).get("input_id")
                    == object_json(r["context"]).get("input_id") for r in decisions):
                rows.append(row)
            elif row["kind"] == "exchange_call":
                request = body.get("request", {})
                ids = {value for value in (request.get("orderId"), body.get("order_id")) if value}
                link = request.get("orderLinkId")
                matches = [c for c in data["llm_scenario_children"] if
                           (ids and c["order_id"] in ids) or (link and c["link_id"] == link)]
                if matches and {c["scenario_id"] for c in matches} == {sid}:
                    rows.append(row)
        except (ValueError, TypeError, KeyError):
            continue  # Unlinked optional events cannot establish any evidence.
    result = dict(provenance_status="MISSING", provenance_issues=[], audit_event_refs=[], code_versions=[],
                  code_version_status="OBSERVED_CHECKOUT_ONLY_NOT_VERIFIED_DEPLOYMENT",
                  policy_hashes=[], execution_hashes=[], policy_groups=[], loaded_code_status="UNKNOWN",
                  loaded_code_status_basis="EVENT_RELEVANT_DOMAINS", manifest_loaded_code_statuses=[],
                  loaded_code_verification_scope="SELECTED_FUNCTION_BYTECODE_AND_LITERAL_CONFIG_ONLY",
                  audit_links=[], judgement_policy_hashes=[], execution_policy_hashes=[],
                  environment_hashes=[],
                  accounting_source_check=dict(status="MISSING", funding_coverage="MISSING", financial_refs=[], funding_refs=[]))
    manifests, policies, executions, versions, groups, loaded = {}, set(), set(), set(), set(), set()
    accounting_candidates = []
    try:
        for row in data["llm_scenario_audit_manifests"]:
            ident = row["manifest_id"]
            require(ident not in manifests)
            manifest = object_json(row["body"])
            require(ident == digest(manifest))
            manifests[ident] = manifest
        require(len({r["event_id"] for r in data["llm_scenario_audit_events"]}) == len(data["llm_scenario_audit_events"]))
        for row in rows:
            body = object_json(row["body"])
            require(body.get("schema_version") == 1)
            require(not body.get("scenario_id") or body["scenario_id"] == sid)
            require(not row.get("intent_id") or digest(row["intent_id"]) in scenario["intent_refs"])
            manifest = manifests.get(row["manifest_id"])
            if manifest is None:
                result["provenance_issues"].append("MISSING_MANIFEST")
                continue
            event_loaded_status = _event_loaded_status(manifest, row["kind"])
            loaded.add(event_loaded_status)
            overall = manifest.get("loaded_code_status", "UNKNOWN")
            result["manifest_loaded_code_statuses"].append(overall if overall in {"VERIFIED", "MIXED", "UNKNOWN"} else "UNKNOWN")
            for field, values in (("policy_hash", policies), ("execution_hash", executions)):
                value = manifest.get(field)
                require(isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value))
                values.add(value)
            revision = manifest.get("git_revision")
            if revision is not None:
                require(isinstance(revision, str) and len(revision) == 40 and all(c in "0123456789abcdef" for c in revision))
                versions.add(revision)
            environment_hash = digest({k: manifest.get(k) for k in ("python", "libraries")})
            result["environment_hashes"].append(environment_hash)
            groups.add(digest({k: manifest.get(k) for k in ("policy_hash", "execution_hash", "git_revision", "source_hashes", "loaded_code_status",
                "judgment_loaded_code_status", "execution_loaded_code_status", "python", "libraries")}))
            result["audit_event_refs"].append(digest(row["event_id"]))
            link = dict(event_ref=digest(row["event_id"]), run_ref=digest(row["run_id"]),
                        loaded_code_status=event_loaded_status,
                        kind=row["kind"] if row["kind"] in {"decision_input", "intent_committed", "exchange_call", "accounting_observation", "settlement_recorded"} else "OTHER",
                        manifest_ref=digest(row["manifest_id"]), observed_at=float(number(row["observed_at"])))
            if row["kind"] == "decision_input":
                durable = [r for r in data["llm_scenario_decisions"] if r["slot"] == row["decision_slot"]]
                require(len(durable) == 1)
                decision = durable[0]
                context = object_json(decision["context"])
                snapshot = object_json(decision["snapshot"])
                proposal = object_json(decision["proposal"]) if decision.get("proposal") else {}
                require(isinstance(body["input_id"], str) and body["input_id"])
                require(body["input_id"] == context.get("input_id"))
                require(not proposal or proposal.get("input_id") == body["input_id"])
                require(canonical(body["snapshot"]) == canonical(snapshot) and canonical(body["context"]) == canonical(context))
                associated = {value for value in (context.get("scenario_id"), proposal.get("scenario_id")) if value}
                require(associated == {sid})
                require(body["input_id_hash"] == digest(body["input_id"]))
                require(body["snapshot_hash"] == digest(body["snapshot"]) and body["context_hash"] == digest(body["context"]))
                link.update(input_ref=digest(body["input_id"]), snapshot_hash=body["snapshot_hash"], context_hash=body["context_hash"])
                result["judgement_policy_hashes"].append(manifest["policy_hash"])
            if row["kind"] == "intent_committed":
                matching = [r for r in data["llm_scenario_intents"] if r["id"] == row["intent_id"] and r["scenario_id"] == sid]
                require(len(matching) == 1 and body["payload"] == object_json(matching[0]["payload"]))
                for name in ("payload", "risk", "initial_equity", "pending_entries"):
                    require(body[name + "_hash"] == digest(body[name]))
                link.update(intent_ref=digest(row["intent_id"]), risk_hash=body["risk_hash"],
                            pending_entries_hash=body["pending_entries_hash"], payload_hash=body["payload_hash"])
            if row["kind"] == "exchange_call":
                require(body["request_hash"] == digest(body["request"]))
                require(body["status"] in {"ACK_ONLY", "UNKNOWN"})
                ids = {value for value in (body["request"].get("orderId"), body.get("order_id")) if value}
                order_link = body["request"].get("orderLinkId")
                matches = [c for c in data["llm_scenario_children"] if
                           (ids and c["order_id"] in ids) or (order_link and c["link_id"] == order_link)]
                native = (body.get("method") == "set_trading_stop" and row.get("scenario_id") == sid
                          and body["request"].get("symbol") == "BTCUSDT" and body["request"].get("positionIdx") == 0
                          and not ids and not order_link)
                require(native or (matches and {c["scenario_id"] for c in matches} == {sid}))
                require(not ids or ids <= {c["order_id"] for c in matches})
                require(not order_link or {c["link_id"] for c in matches} == {order_link})
                link.update(status=body["status"], request_hash=body["request_hash"])
                result["execution_policy_hashes"].append(manifest["execution_hash"])
            result["audit_links"].append(link)
            if row["kind"] == "settlement_recorded":
                require(scenario["stored_settlement_checks_passed"] and body["settlement"]["scenario_id"] == sid)
                require(sorted(digest(v) for v in body["settlement"]["execution_ids"]) == scenario["execution_refs"])
                for key, value in scenario["recorded_settlement_amounts"].items():
                    require(number(body["settlement"][key]) == number(value))
                detected = float(number(body["detected_at"]))
                require(detected == float(number(row["observed_at"])) and detected >= (scenario["last_execution_timestamp"] or 0))
                require(scenario["settlement_timestamp"] in (None, detected))
                scenario["settlement_timestamp"] = detected
            if row["kind"] == "accounting_observation":
                # Historical snapshots may precede unfilled replacement children
                # or terminal cancellation. Validate shared immutable IDs first;
                # only full-current coverage can prove cumulative accounting.
                if _accounting_candidate(sid, body, scenario, ledger_children):
                    checked = _accounting_source(sid, body, scenario, ledger_children)
                    accounting_candidates.append((number(row["observed_at"]), checked))
                else:
                    link["accounting_scope"] = "HISTORICAL_PREFIX_NOT_CURRENT_PROOF"
        if accounting_candidates:
            latest = max(at for at, _ in accounting_candidates)
            tied = [value for at, value in accounting_candidates if at == latest]
            require(len({canonical(value) for value in tied}) == 1)
            result["accounting_source_check"] = tied[0]
        result.update(code_versions=sorted(versions), policy_hashes=sorted(policies), execution_hashes=sorted(executions), policy_groups=sorted(groups))
        result["loaded_code_status"] = "MIXED" if "MIXED" in loaded else "VERIFIED" if loaded == {"VERIFIED"} else "UNKNOWN"
        if rows:
            result["provenance_status"] = "PARTIAL"
            if result["loaded_code_status"] != "VERIFIED":
                result["provenance_issues"].append("LOADED_CODE_NOT_PROVEN")
            # Merely observing an active carry-in cannot complete its lifecycle.
            # Decision/intent/run coverage still requires explicit audit below.
            result["provenance_issues"].append("FULL_LIFECYCLE_RUN_COVERAGE_NOT_PROVEN")
    except (ValueError, TypeError, KeyError, InvalidOperation, OverflowError):
        result["provenance_status"] = "CONFLICT"
        result["provenance_issues"].append("AUDIT_IDENTITY_OR_SOURCE_CONFLICT")
        result["accounting_source_check"] = dict(status="CONFLICT", funding_coverage="MISSING", financial_refs=[], funding_refs=[])
        scenario["settlement_timestamp"] = None
    result["provenance_issues"] = sorted(set(result["provenance_issues"]))
    for key in ("judgement_policy_hashes", "execution_policy_hashes", "environment_hashes", "manifest_loaded_code_statuses"):
        result[key] = sorted(set(result[key]))
    return result


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
        for sid, scenario in zip(sorted(sids), scenarios):
            scenario.update(_notice_links(sid, data))
            scenario.update(_audit_links(sid, scenario, data))
        statuses = Counter()
        receipts = 0
        stage = "notice_statuses"
        for row in data["llm_scenario_outbox"]:
            status = row["status"] if row["status"] in STATUSES else "OTHER"
            statuses[status] += 1
            receipts += int(status == "SENT" and type(row["message_id"]) is int and row["message_id"] > 0)
        packet.update(status="AVAILABLE", scenarios=scenarios, decision_outcomes=dict(outcomes),
                      notice_statuses=dict(statuses), notice_receipts=receipts,
                      notice_scope="GLOBAL_OUTBOX_WITH_OPTIONAL_EXACT_SCENARIO_LINKS")
        missing = set()
        for scenario in scenarios:
            if scenario["provenance_status"] != "COMPLETE":
                missing.add("FULL_LIFECYCLE_PROVENANCE_NOT_COMPLETE")
            if not scenario["independent_accounting_complete"]:
                missing.add("INDEPENDENT_ACCOUNTING_NOT_COMPLETE")
            if scenario["accounting_source_check"]["funding_coverage"] != "RAW_SOURCE_VERIFIED":
                missing.update(("MISSING_FUNDING_SCHEDULE", "MISSING_FINANCIAL_SCENARIO_FK"))
            if not scenario["code_versions"] or scenario["loaded_code_status"] != "VERIFIED":
                missing.add("MISSING_TRADING_CODE_VERSION")
            if scenario["settlement_timestamp"] is None:
                missing.add("MISSING_SETTLEMENT_TIMESTAMP")
        packet["insufficiency_reasons"] = sorted(missing) if scenarios else packet["insufficiency_reasons"]
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
