"""Durable one-shot reductions; never cancel native Full SL or refill exit quotas."""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal, ROUND_DOWN

RESOLVED = frozenset({"COMPLETE", "RESTORE_FAILED", "SKIPPED_FAILED"})


def _wire(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


def initialize(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_reductions(
        intent_id TEXT PRIMARY KEY, body TEXT NOT NULL, sha256 TEXT NOT NULL)""")


def _save(broker, ident, body, *, commit=True):
    raw = _wire(body)
    broker.conn.execute("INSERT OR REPLACE INTO llm_scenario_reductions VALUES(?,?,?)",
                        (ident, raw, hashlib.sha256(raw.encode()).hexdigest()))
    if commit:
        broker.conn.commit()


def load(broker, ident):
    row = broker.conn.execute("SELECT body,sha256 FROM llm_scenario_reductions WHERE intent_id=?", (ident,)).fetchone()
    if not row:
        broker._fail("reduce_preparation_missing")
    if hashlib.sha256(row[0].encode()).hexdigest() != row[1]:
        broker._fail("reduce_preparation_corrupt")
    return json.loads(row[0])


def _rejected(broker, body, ident):
    """Validate archived no-order facts before skipping any source on restart."""
    from live.scenario_execution import stable_link, decimal
    records = body.get('rejected_restorations', {})
    if not isinstance(records, dict):
        broker._fail('reduce_rejection_corrupt')
    for source_id, item in records.items():
        source = next((s for s in body['sources'] if s['link_id'] == source_id), None)
        proof = item.get('proof', {})
        request = proof.get('request', {})
        expected = stable_link(ident, 'partial_sl', 'reduce:' + source_id)
        if (not source or source['kind'] != 'partial_sl' or proof.get('version') != 1
                or proof.get('source') not in {'PYBIT_INVALID_REQUEST', 'EXPLICIT_RESPONSE', 'PREFLIGHT_MARK_CROSSED'}
                or proof.get('request_sha256') != hashlib.sha256(_wire(request).encode()).hexdigest()
                or request.get('orderLinkId') != expected or request.get('reduceOnly') is not True
                or request.get('orderType') != 'Market' or request.get('triggerBy') != 'MarkPrice'
                or proof.get('main_uid') != broker.expected_main_uid
                or proof.get('endpoint') != 'https://api-demo.bybit.com'):
            broker._fail('reduce_rejection_corrupt')
        terms = {k: v for k, v in source['request'].items()
                 if k not in {'orderLinkId', 'category', 'symbol', 'positionIdx', 'qty'}}
        if (any(request.get(k) != v for k, v in terms.items())
                or request.get('category') != 'linear' or request.get('symbol') != 'BTCUSDT'
                or request.get('positionIdx') != 0
                or not 0 < decimal(request.get('qty')) <= decimal(source['unfilled'])
                or not 0 <= decimal(proof.get('captured_at')) <= decimal(broker.clock())):
            broker._fail('reduce_rejection_corrupt')
        if proof['source'] != 'PREFLIGHT_MARK_CROSSED':
            child = item.get('child', {})
            if (type(proof.get('code')) is not int or proof['code'] not in {110092, 110093}
                    or child.get('link_id') != expected or child.get('intent_id') != ident
                    or child.get('scenario_id') != body['payload']['scenario_id']
                    or child.get('kind') != 'partial_sl' or child.get('local_id') != 'reduce:' + source_id
                    or child.get('request') != request or child.get('status') != 'UNKNOWN'
                    or child.get('order_id') is not None or child.get('evidence') is not None):
                broker._fail('reduce_rejection_corrupt')
            if proof['source'] == 'EXPLICIT_RESPONSE' and proof.get('response', {}).get('retCode') != proof['code']:
                broker._fail('reduce_rejection_corrupt')
        else:
            mark, trigger = decimal(proof.get('mark_price')), decimal(request.get('triggerPrice'))
            if not ((request.get('triggerDirection') == 1 and mark >= trigger)
                    or (request.get('triggerDirection') == 2 and mark <= trigger)):
                broker._fail('reduce_rejection_corrupt')
        if any(c['link_id'] == expected for c in broker.children()):
            broker._fail('reduce_rejection_child_conflict')
    return records


def _archive_rejection(broker, body, ident, source, proof, *, created=False):
    from live.scenario_execution import encoded
    child = None
    if created:
        child = next((c for c in broker.children() if c['link_id'] == proof['request']['orderLinkId']), None)
        if (not child or child['intent_id'] != ident or child['scenario_id'] != body['payload']['scenario_id']
                or child['kind'] != 'partial_sl' or child['local_id'] != 'reduce:' + source['link_id']
                or child['status'] != 'UNKNOWN' or child['order_id'] is not None
                or child['evidence'] is not None or child['request'] != proof['request']):
            broker._fail('reduce_rejection_child_conflict')
    body.setdefault('rejected_restorations', {})[source['link_id']] = dict(child=child, proof=proof)
    broker.conn.execute('SAVEPOINT reduce_rejection')
    try:
        _save(broker, ident, body, commit=False)
        if child:
            deleted = broker.conn.execute("DELETE FROM llm_scenario_children WHERE link_id=? AND intent_id=? AND status='UNKNOWN' AND order_id IS NULL AND evidence IS NULL AND request=?",
                (child['link_id'], ident, encoded(child['request'])))
            if deleted.rowcount != 1:
                broker._fail('reduce_rejection_delete_conflict')
        _rejected(broker, body, ident)
        broker.conn.execute('RELEASE reduce_rejection')
        broker.conn.commit()
    except Exception:
        try:
            broker.conn.execute('ROLLBACK TO reduce_rejection')
            broker.conn.execute('RELEASE reduce_rejection')
        except Exception:
            broker.conn.rollback()
        raise


def result(broker, ident):
    body = load(broker, ident)
    _rejected(broker, body, ident)
    return dict(status=body["status"], intent_id=ident,
                failed_source_ids=body.get("failed_source_ids", []),
                restored_exit_targets_complete=body["status"] == "COMPLETE")


def context(broker, active):
    if not active:
        return {}
    # A standalone read-only broker can expose finance before runtime schema
    # exists. Missing history disables REDUCE; it is not an empty success log.
    required = {"llm_scenario_intents", "llm_scenario_reductions"}
    present = {row[0] for row in broker.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?)", tuple(sorted(required)))}
    if present != required:
        return dict(immediate_reduce_available=False,
                    immediate_reduce_result=dict(status="UNAVAILABLE", reason="runtime_schema_missing"))
    rows = broker.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC",
                               (active["scenario_id"],)).fetchall()
    for ident, raw in rows:
        action = json.loads(raw)["action"]
        if action == "WAIT":
            continue
        if action == "REDUCE":
            outcome = result(broker, ident)
            return dict(immediate_reduce_result=outcome,
                        immediate_reduce_available=outcome["status"] == "COMPLETE")
        break
    return {}


def prepare(broker, payload, ident):
    """No network or commit: caller atomically commits this with the intent."""
    from live.scenario_execution import decimal
    active = broker._active()
    plan = payload.get("reduce_plan") or {}
    if not active or payload.get("action") != "REDUCE" or active["scenario_id"] != payload["scenario_id"]:
        broker._fail("reduce_active_required")
    q, d, r = (decimal(plan.get(k)) for k in ("quantity_before", "requested_quantity", "target_remainder"))
    if not 0 < d < q or q - d != r:
        broker._fail("reduce_quantity_invalid")
    if broker.conn.execute("SELECT 1 FROM llm_scenario_reductions WHERE intent_id=?", (ident,)).fetchone():
        broker._fail("reduce_already_prepared")
    sources = []
    children = broker.children(active["scenario_id"])
    if any(not c["evidence"] or c["status"] == "UNKNOWN" or
           (c["kind"] in {"entry", "reduce", "exit"} and c["status"] != "TERMINAL") for c in children):
        broker._fail("reduce_unreconciled_children")
    for child in children:
        if child["kind"] not in {"tp", "partial_sl"} or child["status"] != "LIVE":
            continue
        order = child["evidence"]["order"]
        if (child["request"].get("reduceOnly") is not True or order.get("reduceOnly") is not True
                or child["request"].get("side") != ("Sell" if active["side"] == "LONG" else "Buy")):
            broker._fail("reduce_source_terms_invalid")
        quota = decimal(order["leavesQty"])
        if quota <= 0:
            broker._fail("reduce_source_quota_invalid")
        sources.append(dict(link_id=child["link_id"], order_id=child["order_id"],
                            kind=child["kind"], request=child["request"],
                            quota=str(quota), filled=str(decimal(order["cumExecQty"]))))
    group_quotas = {kind: sum((decimal(s["quota"]) for s in sources if s["kind"] == kind), Decimal(0))
                    for kind in ("tp", "partial_sl")}
    if any(total > q for total in group_quotas.values()):
        # A fill in the other exit group can leave reduce-only quotas above current size.
        owned_fills = {}
        for child in children:
            if child["kind"] != "entry":
                continue
            for fill in child["evidence"].get("executions", []):
                if fill.get("execType") != "Trade":
                    continue
                exec_id = fill.get("execId")
                quantity = decimal(fill.get("execQty"))
                if (not isinstance(exec_id, str) or not exec_id or exec_id in owned_fills or quantity <= 0
                        or fill.get("orderId") != child["order_id"]
                        or fill.get("symbol") != "BTCUSDT"
                        or fill.get("side") != ("Buy" if active["side"] == "LONG" else "Sell")):
                    broker._fail("reduce_source_quota_invalid")
                owned_fills[exec_id] = quantity
        owned = sum(owned_fills.values(), Decimal(0))
        if q > owned or any(total > owned for total in group_quotas.values()):
            broker._fail("reduce_source_quota_invalid")
    native_stops = [dict(link_id=c["link_id"], order_id=c["order_id"])
                    for c in children if c["kind"] == "native_sl" and c["status"] == "LIVE"]
    if not native_stops:
        broker._fail("reduce_native_protection_missing")
    body = dict(version=1, status="PREPARED", payload=payload, side=active["side"],
                native_stops=native_stops,
                hard_stop=active["hard_stop"], quantity_before=str(q), requested_quantity=str(d),
                target_remainder=str(r), sources=sources)
    _save(broker, ident, body, commit=False)


def _failed(active):
    return bool(active.get("entry_contract_failure") or active.get("entry_contract_model_failure")
                or (active.get("entry_contract_observation") or {}).get("status") == "FAILED"
                or (active.get("last_thesis_assessment") or {}).get("status") == "FAILED")


def _observation(broker, body, *, require_accounting=True):
    from live.scenario_execution import decimal
    active = broker._active()
    for child in broker.children(active["scenario_id"]):
        if child["status"] != "TERMINAL":
            broker._query_child(child)
    observed = broker.capture_account()
    if observed["legacy_fenced"]:
        broker._fail("reduce_external_exposure")
    q = decimal(observed["position"]["size"])
    if q and (observed["position"].get("side") != ("Buy" if body["side"] == "LONG" else "Sell")
              or not broker._verify_protection(observed, body["hard_stop"], body["side"])):
        broker._fail("reduce_protection_unconfirmed")
    own = broker.children(active["scenario_id"])
    if any(c["status"] == "UNKNOWN" or not c["evidence"] or
           (c["kind"] == "entry" and c["status"] != "TERMINAL") for c in own):
        broker._fail("reduce_unreconciled_children")
    if require_accounting:
        accounting = broker._risk_accounting(observed, active, own)
        if not accounting or accounting.get("status") != "confirmed":
            broker._fail("reduce_accounting_unconfirmed")
        if sum((decimal(p["quantity"]) for p in accounting["positions"]), Decimal(0)) != q:
            broker._fail("reduce_accounting_quantity_conflict")
    return observed, q


def execute(broker, payload, ident):
    """Restart-safe state machine: cancellation, one IOC, exact result, restore."""
    from live.scenario_execution import decimal, text_number
    body = load(broker, ident)
    _rejected(broker, body, ident)
    if body["payload"] != payload:
        broker._fail("reduce_preparation_changed")
    if body["status"] in RESOLVED:
        return body
    latest = broker.conn.execute("SELECT id FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",
                                 (payload["scenario_id"],)).fetchone()
    if not latest or latest[0] != ident:
        broker._fail("reduce_superseded_intent")
    if _failed(broker._active()):
        # Query every possibly submitted child before handing off. Do not add
        # restoration POSTs that could introduce a new UNKNOWN ahead of EXIT.
        # There is no new reduction or restoration POST in this handoff.
        # Financial finality must not impose a stronger fence than full EXIT.
        _observation(broker, body, require_accounting=False)
        if any(c["kind"] == "reduce" and c["status"] != "TERMINAL"
               for c in broker.children(payload["scenario_id"])):
            broker._fail("reduce_result_pending")
        body["status"] = "SKIPPED_FAILED"
        _save(broker, ident, body)
        return body
    instrument = broker._instrument()
    q0, d0, r0 = (decimal(body[k]) for k in ("quantity_before", "requested_quantity", "target_remainder"))
    fraction = decimal(payload.get("reduce_fraction"))
    expected = (q0 * fraction / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"]
    if (not 0 < fraction < 1 or d0 != expected or q0 - d0 != r0 or q0 % instrument["step"]
            or d0 < instrument["minimum"] or r0 < instrument["minimum"]
            or d0 > instrument["market_maximum"]):
        broker._fail("reduce_quantity_invalid")
    own = {c["link_id"]: c for c in broker.children(payload["scenario_id"])}
    for source in body["sources"]:
        child = own.get(source["link_id"])
        if not child or child["order_id"] != source["order_id"] or child["request"] != source["request"]:
            broker._fail("reduce_source_changed")
    if body["status"] == "PREPARED":
        _, before = _observation(broker, body)
        if before > decimal(body["quantity_before"]):
            broker._fail("reduce_position_increased")
        # Even expired intents finish cancellation/protection recovery; no new
        # reduction will be submitted after the original deadline.
        for source in body["sources"]:
            broker._cancel(own[source["link_id"]])
        _, q1 = _observation(broker, body)
        if q1 > before:
            broker._fail("reduce_position_increased")
        remaining = []
        own = {c["link_id"]: c for c in broker.children(payload["scenario_id"])}
        for source in body["sources"]:
            child = own[source["link_id"]]
            filled = decimal(child["evidence"]["order"]["cumExecQty"]) - decimal(source["filled"])
            if filled < 0 or filled > decimal(source["quota"]):
                broker._fail("reduce_source_fill_conflict")
            remaining.append(dict(source, unfilled=str(decimal(source["quota"]) - filled)))
        quantity = max(Decimal(0), q1 - decimal(body["target_remainder"]))
        if quantity and (quantity % instrument["step"] or quantity < instrument["minimum"]
                         or quantity > instrument["market_maximum"]):
            # Dust cannot be rounded up into the retained remainder.
            quantity = Decimal(0)
        body.update(status="CANCELLED", quantity_after_cancels=str(q1),
                    market_quantity=str(quantity), sources=remaining)
        _save(broker, ident, body)
    if body["status"] == "CANCELLED":
        own = broker.children(payload["scenario_id"])
        market = next((c for c in own if c["intent_id"] == ident and c["kind"] == "reduce"), None)
        if market:
            market = broker._query_child(market)
        else:
            _, size = _observation(broker, body)
            quantity = min(decimal(body["market_quantity"]), max(Decimal(0), size - decimal(body["target_remainder"])))
            if broker.clock() >= payload["expires_at"]:
                quantity = Decimal(0)
            if quantity >= instrument["minimum"] and quantity <= instrument["market_maximum"]:
                market = broker._submit(payload, ident, "reduce", "once", dict(
                    side="Sell" if body["side"] == "LONG" else "Buy", orderType="Market",
                    timeInForce="IOC", qty=text_number(quantity), reduceOnly=True, closeOnTrigger=True))
        if market and market["status"] != "TERMINAL":
            broker._fail("reduce_result_pending")
        _, size = _observation(broker, body)
        basis = decimal(body["quantity_after_cancels"])
        if size > basis:
            broker._fail("reduce_position_increased")
        body.update(status="RESTORING", remaining_quantity=str(size))
        _save(broker, ident, body)
    if body["status"] == "RESTORING":
        _, size = _observation(broker, body)
        basis = decimal(body["quantity_after_cancels"])
        original_remaining = decimal(body["remaining_quantity"])
        # Fixed restoration quotas survive retries and racing restored fills.
        allocated = {"tp": Decimal(0), "partial_sl": Decimal(0)}
        totals = {kind: sum((decimal(s["unfilled"]) for s in body["sources"] if s["kind"] == kind), Decimal(0))
                  for kind in allocated}
        for source in body["sources"]:
            observed, size = _observation(broker, body)
            if source['link_id'] in body.get('rejected_restorations', {}):
                continue
            quota = decimal(source["unfilled"])
            kind = source["kind"]
            denominator = max(basis, totals[kind])
            quantity = (quota * original_remaining / denominator / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"] if denominator else Decimal(0)
            quantity = min(quantity, quota)
            allocated[kind] += quantity
            if allocated[kind] > original_remaining or quantity > instrument["maximum"]:
                broker._fail("reduce_restore_quota_invalid")
            local_id = "reduce:" + source["link_id"]
            previous = next((c for c in broker.children(payload["scenario_id"]) if
                             c["intent_id"] == ident and c["kind"] == kind and c["local_id"] == local_id), None)
            if previous:
                broker._query_child(previous)
                _, size = _observation(broker, body)
                continue
            # A restored exit can fill before the next sibling is submitted.
            # Never reserve that closed quantity again in either exit group.
            outstanding = sum((decimal(c["evidence"]["order"]["leavesQty"])
                               for c in broker.children(payload["scenario_id"])
                               if c["intent_id"] == ident and c["kind"] == kind
                               and c["status"] == "LIVE"), Decimal(0))
            quantity = min(quantity, max(Decimal(0), size - outstanding))
            quantity = (quantity / instrument["step"]).to_integral_value(rounding=ROUND_DOWN) * instrument["step"]
            if not size or quantity < instrument["minimum"]:
                continue
            request = {k: v for k, v in source["request"].items()
                       if k not in {"orderLinkId", "category", "symbol", "positionIdx", "qty"}}
            request["qty"] = text_number(quantity)
            from live.scenario_execution import RestorationNotAccepted, stable_link
            full_request = dict(request, category='linear', symbol='BTCUSDT', positionIdx=0,
                orderLinkId=stable_link(ident, kind, local_id))
            if kind == 'partial_sl' and request.get('triggerBy') == 'MarkPrice':
                mark, trigger = decimal(observed['mark_price']), decimal(request['triggerPrice'])
                if not 0 <= broker.clock() - observed['captured_at'] <= 10:
                    broker._fail('reduce_restore_mark_stale')
                if ((request.get('triggerDirection') == 1 and mark >= trigger)
                        or (request.get('triggerDirection') == 2 and mark <= trigger)):
                    proof = dict(version=1, source='PREFLIGHT_MARK_CROSSED', code=None,
                        request=full_request, request_sha256=hashlib.sha256(_wire(full_request).encode()).hexdigest(),
                        main_uid=broker.expected_main_uid, endpoint=broker.session.endpoint,
                        captured_at=observed['captured_at'], mark_price=str(mark))
                    _archive_rejection(broker, body, ident, source, proof)
                    continue
            try:
                broker._submit(payload, ident, kind, local_id, request)
            except RestorationNotAccepted as exc:
                if not exc.created_this_invocation or exc.proof['request'] != full_request:
                    raise
                _archive_rejection(broker, body, ident, source, exc.proof, created=True)
            _, size = _observation(broker, body)
        failed = list(body.get('rejected_restorations', {}))
        for source in body["sources"]:
            child = next((c for c in broker.children(payload["scenario_id"])
                          if c["intent_id"] == ident and c["kind"] == source["kind"]
                          and c["local_id"] == "reduce:" + source["link_id"]), None)
            if child and child["status"] == "TERMINAL":
                order = child["evidence"]["order"]
                if decimal(order["cumExecQty"]) < decimal(order["qty"]):
                    failed.append(source["link_id"])
        body.update(status="RESTORE_FAILED" if failed else "COMPLETE", failed_source_ids=failed)
        _save(broker, ident, body)
    return body
