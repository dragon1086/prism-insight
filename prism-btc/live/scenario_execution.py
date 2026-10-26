"""Durable, demo-only scenario order execution.

All hard and partial stops trigger on MarkPrice, matching the liquidation/risk
context (candles are LastPrice observations, not execution protection proof).
Native Full SL is attached to EVERY entry. Position SL changes are in-place;
partial TP/SL replacement never removes the native Full SL. API acceptance is
not execution evidence. UNKNOWN submissions are queried, never resubmitted.

Bybit v5 order/create, order/cancel and position/trading-stop documentation
checked 2026-10-03. Explicit broker enablement is not runtime capability approval.
"""
from __future__ import annotations

import hashlib
import json
import logging
import uuid
from decimal import Decimal, InvalidOperation, ROUND_DOWN

from live.scenario_pending_renewal import initialize as initialize_pending_leases
from live.scenario_entry_goal import GoalConsistencyRejection, stored_goal, validate_goal_plan


TERMINAL = frozenset({"Filled", "Cancelled", "Rejected", "Deactivated", "PartiallyFilledCanceled"})
LIVE = frozenset({"New", "PartiallyFilled", "Untriggered", "Triggered"})


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False)


class RestorationNotAccepted(Exception):
    """Fresh POST proof only; not an exchange order or terminal execution."""
    def __init__(self, proof):
        super().__init__('restoration_not_accepted')
        self.proof = proof
        self.created_this_invocation = False


def restoration_rejection(broker, method, request, code, source, response):
    if (method != 'place_order' or type(code) is not int or code not in {110092, 110093}
            or request.get('reduceOnly') is not True or request.get('orderType') != 'Market'
            or request.get('triggerBy') != 'MarkPrice' or not request.get('triggerPrice')
            or request.get('triggerDirection') not in {1, 2}
            or (source == 'EXPLICIT_RESPONSE' and (not isinstance(response.get('result', {}), dict)
                or response.get('result', {}).get('orderId')))):
        return None
    return dict(version=1, code=code, source=source, response=response,
        request=request, request_sha256=hashlib.sha256(encoded(request).encode()).hexdigest(),
        main_uid=broker.expected_main_uid, endpoint=broker.session.endpoint, captured_at=broker.clock())


def decimal(value):
    from live.scenario_broker import BrokerNotReady
    try:
        result = Decimal(str(value))
        if isinstance(value, bool) or not result.is_finite():
            raise InvalidOperation()
        return result
    except (InvalidOperation, ValueError):
        raise BrokerNotReady("invalid_exchange_number") from None


def text_number(value):
    return format(decimal(value), "f")


def stable_link(intent, kind, local_id):
    return "sc_" + hashlib.sha256(encoded([intent, kind, local_id]).encode()).hexdigest()[:32]


class ScenarioExecution:
    """Mixin; transport, identity and evidence persistence belong to broker."""

    def _init_execution(self, enabled):
        self.execution_enabled = enabled is True
        from live.scenario_incident_epoch import epoch
        self._incident_epoch_bound = epoch(self.conn)
        initialize_pending_leases(self.conn)
        self.conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_children(
            link_id TEXT PRIMARY KEY, intent_id TEXT NOT NULL, scenario_id TEXT NOT NULL,
            kind TEXT NOT NULL, local_id TEXT NOT NULL, request TEXT NOT NULL,
            status TEXT NOT NULL, order_id TEXT, evidence TEXT, created_at REAL NOT NULL)""")
        self.conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_exit_basis(intent_id TEXT PRIMARY KEY,quantity TEXT NOT NULL,entry_baseline TEXT NOT NULL)")
        self.conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_execution_batches(intent_id TEXT PRIMARY KEY,status TEXT NOT NULL)")
        from live.scenario_immediate_reduce import initialize
        initialize(self.conn)
        self.conn.commit()

    def prepare_reduce(self, payload, intent_id):
        from live.scenario_immediate_reduce import prepare
        return prepare(self, payload, intent_id)

    def _execute_reduce(self, payload, intent_id):
        from live.scenario_immediate_reduce import execute
        return execute(self, payload, intent_id)

    def _new_risk_enabled(self):
        from live.scenario_control import read_control
        control=read_control(self.conn)
        return bool(control and control["state"]=="active" and control["main_uid"]==self.expected_main_uid)

    def _fail(self, reason):
        from live.scenario_broker import BrokerNotReady
        raise BrokerNotReady(reason)

    def _active(self):
        try:
            row = self.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()
        except Exception:
            return None
        return json.loads(row[0]).get("active") if row else None

    def _recovery_state(self):
        table = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_state'").fetchone()
        if not table:
            return {}
        row = self.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()
        return json.loads(row[0]) if row else {}

    def children(self, scenario_id=None):
        rows = self.conn.execute(
            "SELECT link_id,intent_id,scenario_id,kind,local_id,request,status,order_id,evidence,created_at "
            "FROM llm_scenario_children WHERE (? IS NULL OR scenario_id=?) ORDER BY rowid",
            (scenario_id or None, scenario_id or None)).fetchall()
        keys = ("link_id","intent_id","scenario_id","kind","local_id","request","status","order_id","evidence","created_at")
        result = []
        for row in rows:
            item = dict(zip(keys,row))
            item["request"] = json.loads(item["request"])
            item["evidence"] = json.loads(item["evidence"]) if item["evidence"] else None
            result.append(item)
        if scenario_id is None:
            from live.scenario_incident_epoch import receipts
            historical = {r['scenario_id'] for r in receipts(self.conn)}
            result = [c for c in result if c['scenario_id'] not in historical]
        return result

    def _write(self, method, *, _restore_rejection=False, _single_attempt=False, _before_send=None, **params):
        from live.shared_entry_coordinator import mutation_lock
        from live.scenario_incident_epoch import guard_write, TRANSPORT
        from live.scenario_management_quarantine import _append
        with mutation_lock(self.conn):
            guard_write(self, method, params)
            call_id = uuid.uuid4().hex
            import os
            _append(self.conn, TRANSPORT, dict(call_id=call_id, state='SENDING',
                epoch_id=self._incident_epoch_bound, owner_pid=os.getpid(), method=method), self.clock())
            self.conn.commit()
            self._incident_transport_active = True
            try:
                return self._write_transport(method, _restore_rejection=_restore_rejection,
                    _single_attempt=_single_attempt, _before_send=_before_send, **params)
            finally:
                self._incident_transport_active = False
                _append(self.conn, TRANSPORT, dict(call_id=call_id, state='RETURNED'), self.clock())
                self.conn.commit()

    def _write_transport(self, method, *, _restore_rejection=False, _single_attempt=False, _before_send=None, **params):
        if not self.execution_enabled:
            self._fail("scenario_execution_disabled")
        self._identity()  # Includes endpoint recheck on every mutation.
        if method not in {"place_order", "cancel_order", "set_trading_stop", "amend_order"}:
            self._fail("unsupported_scenario_mutation")
        try:
            state = self._recovery_state()
            permit = state.get("recovery", {})
            active = state.get("active")
        except Exception:
            logging.getLogger(__name__).warning("AUDIT_GAP recovery_exchange_state_unavailable")
            if method == "place_order" and params.get("reduceOnly") is not True:
                self._fail("recovery_entry_audit_unavailable")
            permit, active = {}, None
        probe = bool(active and permit.get("phase") == "CONSUMED" and
                     permit.get("scenario_id") == active.get("scenario_id"))
        audit = dict(call_id=uuid.uuid4().hex, method=method,
                     permit_id=permit.get("permit_id"), scenario_id=permit.get("scenario_id"))
        if probe and not self._probe_exchange_journal("EXCHANGE_REQUEST", dict(audit, params=params)):
            if method == "place_order" and params.get("reduceOnly") is not True:
                self._fail("recovery_entry_audit_unavailable")
        from live.scenario_provenance import exchange_call, observed_time
        started = observed_time()
        attempted = restored = False
        try:
            # A final SDK rejection proves nonacceptance only if no earlier
            # hidden transport attempt could already have submitted this order.
            absent = object()
            previous_retry_settings = {}
            try:
                if _restore_rejection or _single_attempt:
                    for name, value in (('max_retries', 1), ('force_retry', False)):
                        previous_retry_settings[name] = getattr(self.session, name, absent)
                        setattr(self.session, name, value)
                operation = getattr(self.session, method)
                from live.scenario_incident_epoch import guard_write
                guard_write(self, method, params)  # No REST after this final freshness check.
                if _before_send is not None:
                    _before_send()
                attempted = True
                response = operation(**params)
            finally:
                for name, value in previous_retry_settings.items():
                    if value is absent:
                        delattr(self.session, name)
                    else:
                        setattr(self.session, name, value)
                restored = True
        except Exception as exc:
            exchange_call(method, params, started)
            if _restore_rejection and attempted and restored:
                try:
                    from pybit.exceptions import InvalidRequestError
                except ImportError:
                    InvalidRequestError = ()
                if isinstance(exc, InvalidRequestError):
                    proof = restoration_rejection(self, method, params, getattr(exc, 'status_code', None),
                        'PYBIT_INVALID_REQUEST', dict(message=getattr(exc, 'message', None),
                        request=getattr(exc, 'request', None), time=getattr(exc, 'time', None)))
                    if proof:
                        raise RestorationNotAccepted(proof) from exc
            if probe:
                self._probe_exchange_journal("EXCHANGE_ERROR", dict(audit, error="submission_unknown" if attempted else "submission_not_attempted"))
            self._fail("submission_unknown")
        exchange_call(method, params, started, response=response)
        if _restore_rejection and isinstance(response, dict):
            proof = restoration_rejection(self, method, params, response.get('retCode'), 'EXPLICIT_RESPONSE', response)
            if proof:
                raise RestorationNotAccepted(proof)
        if probe:
            # Logging failure after a POST must never turn into a repeated POST.
            # The durable child UNKNOWN/ACK reconciliation remains authoritative.
            self._probe_exchange_journal("EXCHANGE_RESPONSE", dict(audit, response=response))
        if not isinstance(response, dict) or response.get("retCode") != 0:
            self._fail("submission_unconfirmed")
        return response

    def _probe_exchange_journal(self, kind, body):
        from live.scenario_recovery import journal
        saved = False
        try:
            self.conn.execute("SAVEPOINT recovery_exchange_audit")
            saved = True
            journal(self.conn, kind, body, self.clock(), scenario_id=body["scenario_id"])
            self.conn.execute("RELEASE SAVEPOINT recovery_exchange_audit")
            saved = False
            self.conn.commit()
            return True
        except Exception:
            if saved:
                try:
                    self.conn.execute("ROLLBACK TO SAVEPOINT recovery_exchange_audit")
                    self.conn.execute("RELEASE SAVEPOINT recovery_exchange_audit")
                except Exception:
                    self.conn.rollback()
            logging.getLogger(__name__).warning("AUDIT_GAP recovery_exchange_journal_failed %s", kind)
            return False

    def _instrument(self):
        rows = self._call("get_instruments_info",category="linear",symbol="BTCUSDT").get("result",{}).get("list")
        if not isinstance(rows,list) or len(rows)!=1 or rows[0].get("symbol")!="BTCUSDT" or rows[0].get("status")!="Trading" or rows[0].get("settleCoin")!="USDT":
            self._fail("instrument_unverified")
        r = rows[0]
        try:
            result = dict(tick=decimal(r["priceFilter"]["tickSize"]),
                step=decimal(r["lotSizeFilter"]["qtyStep"]),
                minimum=decimal(r["lotSizeFilter"]["minOrderQty"]),
                maximum=decimal(r["lotSizeFilter"]["maxOrderQty"]),
                market_maximum=decimal(r["lotSizeFilter"]["maxMktOrderQty"]),
                notional=decimal(r["lotSizeFilter"]["minNotionalValue"]))
        except (KeyError,TypeError):
            self._fail("instrument_filters_missing")
        if any(v<=0 for v in result.values()):
            self._fail("instrument_filters_invalid")
        return result

    def _aligned(self, value, step):
        v = decimal(value)
        if v<=0 or v % step:
            self._fail("order_precision_invalid")
        return text_number(v)

    def _verify_entry_order_terms(self, child, order):
        """Reuse exact entry identity/terms for queried and final account rows."""
        if child['kind'] != 'entry':
            return
        request = child['request']
        if (order.get('orderLinkId') != child['link_id'] or order.get('symbol') != 'BTCUSDT'
                or order.get('side') != request['side'] or not order.get('orderId')
                or (child.get('order_id') and order['orderId'] != child['order_id'])
                or decimal(order.get('qty')) != decimal(request['qty'])):
            self._fail('exact_order_identity_conflict')
        if (decimal(order.get('price')) != decimal(request['price'])
                or order.get('orderType') != 'Limit' or order.get('reduceOnly') is not False
                or decimal(order.get('stopLoss')) != decimal(request['stopLoss'])
                or order.get('slTriggerBy') != 'MarkPrice'):
            self._fail('entry_order_terms_changed')
        if request.get('triggerPrice') is not None:
            if (decimal(order.get('triggerPrice')) != decimal(request['triggerPrice'])
                    or order.get('triggerBy') != 'MarkPrice'
                    or order.get('triggerDirection') != request['triggerDirection']
                    or order.get('positionIdx') != 0 or order.get('timeInForce') != 'GTC'
                    or request.get('slOrderType') != 'Market'
                    or ('slOrderType' in order and order['slOrderType'] != 'Market')):
                self._fail('conditional_entry_terms_changed')
        elif decimal(order.get('triggerPrice') or 0) != 0:
            self._fail('unexpected_entry_trigger')

    def _query_child(self, child):
        """Exact ID lookup plus exact cumulative execution reconciliation."""
        from live.scenario_broker import read_evidence_pages
        from live.exchange_snapshot import read_complete
        link = child["link_id"]
        # In-memory optimization evidence only. An attempted recheck invalidates
        # the old proof; ordinary exact reconciliation always performs its GETs.
        verified_terminals = getattr(self, '_quarantine_terminal_observations', None)
        if verified_terminals is None:
            verified_terminals = self._quarantine_terminal_observations = {}
        verified_terminals.pop(link, None)
        native=child["kind"]=="native_sl"
        exact={"orderId":child["order_id"]} if native else {"orderLinkId":link}
        result = read_complete(self._call,"get_open_orders",category="linear",symbol="BTCUSDT",**exact)
        if result is None:
            self._fail("exact_order_query_unknown")
        rows = result["result"]["list"]
        if not rows:
            rows = read_evidence_pages(self._call,"get_order_history",category="linear",symbol="BTCUSDT",**exact)
        from live.scenario_incident_epoch import sealed, record_late
        historical = sealed(self.conn, child['scenario_id'])
        if not rows and historical and child['status'] == 'UNKNOWN':
            return child  # Absence never becomes a terminal assertion.
        if len(rows)!=1:
            self._fail("exact_order_not_found_or_ambiguous")
        order = rows[0]
        request = child["request"]
        if ((not native and order.get("orderLinkId") != link) or order.get("symbol")!="BTCUSDT" or
                order.get("side") != request["side"] or not order.get("orderId") or
                (child["order_id"] and child["order_id"]!=order["orderId"]) or
                (child["kind"]=="entry" and decimal(order.get("qty")) != decimal(request["qty"])) or
                (not native and decimal(order.get("qty"))>decimal(request["qty"]))):
            self._fail("exact_order_identity_conflict")
        if order.get("orderStatus") not in TERMINAL | LIVE:
            self._fail("order_state_unknown")
        if native and request.get("parentOrderLinkId"):
            if (order.get("parentOrderLinkId")!=request["parentOrderLinkId"] or
                    order.get("positionIdx")!=0 or order.get("stopOrderType")!="StopLoss" or
                    order.get("triggerBy")!="MarkPrice" or order.get("orderType")!="Market" or
                    order.get("reduceOnly") is not True):
                self._fail("native_parent_identity_conflict")
        self._verify_entry_order_terms(child, order)
        if child["kind"] == "reduce" and (
                order.get("reduceOnly") is not True or order.get("orderType") != "Market"
                or order.get("timeInForce") != "IOC" or order.get("positionIdx") != 0
                or order.get("closeOnTrigger") is not True):
            self._fail("reduce_order_terms_changed")
        executions = read_evidence_pages(self._call,"get_executions",category="linear",symbol="BTCUSDT",orderId=order["orderId"])
        qty = Decimal(0)
        for row in executions:
            if (row.get("orderId")!=order["orderId"] or row.get("symbol")!="BTCUSDT" or
                    row.get("execType")!="Trade" or row.get("side")!=request["side"]):
                self._fail("execution_identity_conflict")
            amount = decimal(row.get("execQty"))
            if amount<=0 or decimal(row.get("execPrice"))<=0:
                self._fail("execution_value_invalid")
            if child["kind"] == "entry" and request.get("triggerPrice") is not None:
                fill_price, limit = decimal(row["execPrice"]), decimal(request["price"])
                if ((request["side"] == "Buy" and fill_price > limit) or
                        (request["side"] == "Sell" and fill_price < limit)):
                    self._fail("conditional_entry_fill_exceeded_limit")
            qty += amount
        if qty!=decimal(order.get("cumExecQty")) or qty>decimal(order["qty"]):
            self._fail("execution_quantity_unreconciled")
        if order["orderStatus"] in LIVE and decimal(order.get("leavesQty")) != decimal(order["qty"])-qty:
            self._fail("order_remaining_quantity_unreconciled")
        evidence = dict(order=order,executions=executions)
        status = "TERMINAL" if order["orderStatus"] in TERMINAL else "LIVE"
        if historical:
            result = dict(child, status=status, order_id=order['orderId'], evidence=evidence)
            if result != child:
                record_late(self, dict(link_id=link, evidence=evidence, scenario_id=child['scenario_id']))
            return result
        self.conn.execute("UPDATE llm_scenario_children SET status=?,order_id=?,evidence=? WHERE link_id=?",
            (status,order["orderId"],encoded(evidence),link))
        self.conn.commit()
        result = dict(child,status=status,order_id=order["orderId"],evidence=evidence)
        if status == 'TERMINAL' and not native:
            verified_terminals[link] = hashlib.sha256(encoded(dict(
                main_uid=self.expected_main_uid, endpoint=self.session.endpoint, child=result)).encode()).hexdigest()
        return result

    def _retain_native(self, observed, active):
        """Persist exact native IDs while visible; never infer unseen stop fills."""
        entries=[c for c in self.children(active["scenario_id"]) if c["kind"]=="entry"]
        if not entries or observed["legacy_fenced"]:
            return
        for order in observed.get("native_stops",[]):
            oid=order.get("orderId")
            if not isinstance(oid,str) or not oid:
                self._fail("native_stop_identity_missing")
            link="native_"+hashlib.sha256(oid.encode()).hexdigest()[:28]
            request=dict(qty=order["qty"],side=order["side"])
            self.conn.executemany("INSERT OR IGNORE INTO llm_scenario_children VALUES(?,?,?,?,?,?,'UNKNOWN',?,NULL,?)",
                [(link,entries[0]["intent_id"],active["scenario_id"],"native_sl",oid,encoded(request),oid,self.clock())])
        self.conn.commit()

    def _recover_parent_linked_native(self, active):
        """Recover unseen attached SL only from Bybit's exact parent order link.

        parentOrderLinkId survives futures trading-stop updates. Price/time
        resemblance is NOT ownership evidence. Missing links remain fenced.
        """
        from live.scenario_broker import read_evidence_pages
        children=self.children(active["scenario_id"])
        parents={c["link_id"]:c for c in children if c["kind"]=="entry" and
                 c["evidence"] and c["evidence"]["executions"] and c["status"]!="UNKNOWN"}
        if not parents:
            return
        start=int(active["created_at"]*1000)
        end=int(self.clock()*1000)
        if not 0<=end-start<=7*86400*1000:
            self._fail("native_parent_history_window_unavailable")
        rows=read_evidence_pages(self._call,"get_order_history",category="linear",symbol="BTCUSDT",
                                 startTime=start,endTime=end,limit=50)
        known={c["order_id"] for c in self.children() if c["order_id"]}
        for order in rows:
            parent=parents.get(order.get("parentOrderLinkId"))
            oid=order.get("orderId")
            if not parent or oid in known:
                continue
            # All linked descendants must identify themselves as the attached
            # native stop, not an unrelated TP or an inconsistent exchange row.
            if order.get("stopOrderType")!="StopLoss":
                continue
            if (order.get("symbol")!="BTCUSDT" or order.get("positionIdx")!=0 or
                    order.get("side")!=("Sell" if active["side"]=="LONG" else "Buy") or
                    order.get("reduceOnly") is not True or order.get("triggerBy")!="MarkPrice" or
                    order.get("orderType")!="Market" or not start<=decimal(order.get("createdTime"))<=end):
                self._fail("native_parent_identity_conflict")
            link="native_"+hashlib.sha256(oid.encode()).hexdigest()[:28]
            request=dict(qty=order["qty"],side=order["side"],parentOrderLinkId=parent["link_id"])
            child=dict(link_id=link,intent_id=parent["intent_id"],scenario_id=active["scenario_id"],
                       kind="native_sl",local_id=oid,request=request,status="UNKNOWN",order_id=oid,
                       evidence=None,created_at=self.clock())
            verified=self._query_child(child)
            first_fill=min(decimal(e["execTime"]) for e in parent["evidence"]["executions"])
            if any(not first_fill<=decimal(e.get("execTime"))<=end for e in verified["evidence"]["executions"]):
                self._fail("native_parent_execution_time_conflict")
            self.conn.execute("INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,?,?,?,?)",
                (link,parent["intent_id"],active["scenario_id"],"native_sl",oid,encoded(request),
                 verified["status"],oid,encoded(verified["evidence"]),self.clock()))
            self.conn.commit()
            self._save("native_parent_recovery",dict(scenario_id=active["scenario_id"],order_id=oid,
                parent_order_link_id=parent["link_id"],execution_ids=[e["execId"] for e in verified["evidence"]["executions"]]))
            known.add(oid)

    def _submit(self, payload, intent_id, kind, local_id, request, *, single_attempt=False):
        from live.scenario_incident_epoch import sealed, require
        require(not sealed(self.conn, payload['scenario_id']), 'sealed_scenario')
        link = stable_link(intent_id,kind,local_id)
        request = dict(request,category="linear",symbol="BTCUSDT",positionIdx=0,orderLinkId=link)
        previous = next((c for c in self.children() if c["link_id"]==link),None)
        if previous:
            if previous["request"] != request:
                self._fail("child_intent_changed")
            return self._query_child(previous)  # Never repeat UNKNOWN POST.
        batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(intent_id,)).fetchone()
        if kind=="entry" and batch and batch[0] in {"ABORTED","INTERRUPTED"}:
            self._fail("unsubmitted_children_abandoned")
        def before_send():
            # Persist only after local guards pass, but always before transport.
            self.conn.execute("INSERT INTO llm_scenario_children VALUES(?,?,?,?,?,?,'UNKNOWN',NULL,NULL,?)",
                (link,intent_id,payload["scenario_id"],kind,local_id,encoded(request),self.clock()))
            self.conn.commit()  # Crash after this point can only recover by exact ID.
            if kind == "entry":
                from live.scenario_incident_epoch import guard_write
                try:
                    guard_write(self, "place_order", request)
                except Exception as exc:
                    # Only this invocation's unattempted entry may be archived.
                    from live.scenario_management_quarantine import _append
                    self.conn.execute("SAVEPOINT entry_pretransport_abort")
                    try:
                        child = self.conn.execute("SELECT * FROM llm_scenario_children WHERE link_id=?", (link,)).fetchone()
                        require(child is not None, 'pretransport_child_missing')
                        _append(self.conn, "entry_pretransport_abort", dict(version=1,
                            child=list(child), transport_attempted=False,
                            basis="FINAL_LOCAL_GUARD_BEFORE_SDK", error_type=type(exc).__name__), self.clock())
                        batch = self.conn.execute("INSERT INTO llm_scenario_execution_batches VALUES(?,'ABORTED') "
                            "ON CONFLICT(intent_id) DO UPDATE SET status='ABORTED' WHERE status IN ('EXECUTING','COMPLETE')", (intent_id,))
                        require(batch.rowcount == 1, 'pretransport_batch_conflict')
                        deleted = self.conn.execute("DELETE FROM llm_scenario_children WHERE link_id=? AND intent_id=? "
                            "AND status='UNKNOWN' AND order_id IS NULL AND evidence IS NULL AND request=?",
                            (link, intent_id, encoded(request)))
                        require(deleted.rowcount == 1, 'pretransport_child_conflict')
                        self.conn.execute("RELEASE entry_pretransport_abort")
                        self.conn.commit()
                    except Exception:
                        self.conn.rollback()
                        raise
                    raise
        if kind != "entry":
            before_send()
        try:
            response = self._write("place_order", _before_send=before_send if kind == "entry" else None, _single_attempt=single_attempt, _restore_rejection=(payload.get('action') == 'REDUCE'
                and kind == 'partial_sl' and local_id.startswith('reduce:')), **request)
        except RestorationNotAccepted as exc:
            exc.created_this_invocation = True
            raise
        order_id = response.get("result",{}).get("orderId")
        if not isinstance(order_id,str) or not order_id:
            self._fail("order_ack_identity_missing")
        self.conn.execute("UPDATE llm_scenario_children SET order_id=? WHERE link_id=?",(order_id,link))
        self.conn.commit()
        return self._query_child(next(c for c in self.children() if c["link_id"]==link))

    def _cancel(self, child, *, before_cancel=None):
        from live.scenario_incident_epoch import sealed, require
        require(not sealed(self.conn, child['scenario_id']), 'sealed_scenario')
        child = self._query_child(child)
        if child["status"] == "TERMINAL":
            return child
        if before_cancel is not None:
            before_cancel(child)
        # Cancel ACK is never sufficient to release outstanding risk.
        acknowledgement = None
        try:
            acknowledgement = self._write("cancel_order",category="linear",symbol="BTCUSDT",orderLinkId=child["link_id"],orderId=child["order_id"])
        except Exception:
            logging.getLogger(__name__).warning("Cancel acknowledgement unknown; exact terminal query required")
        child = self._query_child(child)
        if child["status"]!="TERMINAL":
            result = acknowledgement.get("result") if acknowledgement else None
            if (child["status"] == "LIVE" and isinstance(result, dict)
                    and child["order_id"] and result.get("orderId") == child["order_id"]
                    and result.get("orderLinkId") == child["link_id"]):
                from live.scenario_broker import BrokerCancellationPending
                self._save("cancel_ack_pending", dict(link_id=child["link_id"],
                    order_id=child["order_id"], acknowledgement=acknowledgement,
                    evidence=child["evidence"], terminal_confirmed=False))
                raise BrokerCancellationPending("cancel_not_confirmed")
            self._fail("cancel_not_confirmed")
        return child

    def _pending_entry(self, child):
        row = dict(id=child["local_id"], price=float(child["request"]["price"]),
                   quantity=float(child["evidence"]["order"]["leavesQty"]))
        if child["request"].get("triggerPrice") is not None:
            row["trigger_price"] = float(child["request"]["triggerPrice"])
            row["order_status"] = child["evidence"]["order"]["orderStatus"]
            parent = self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=?",
                                       (child["intent_id"],)).fetchone()
            if not parent:
                self._fail("conditional_parent_missing")
            from live.scenario_pending_renewal import effective_deadline
            row['expires_at'] = effective_deadline(self.conn, self._active(), json.loads(parent[0]), child)
        return row

    def _conditional_invalidated(self, child, observed, active):
        """Original reservation deadline survives later WAIT/ADJUST revisions."""
        if child["kind"] != "entry" or child["request"].get("triggerPrice") is None:
            return False
        parent = self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=?",
                                   (child["intent_id"],)).fetchone()
        if not parent:
            return True
        payload = json.loads(parent[0])
        mark, stop = decimal(observed["mark_price"]), decimal(active["hard_stop"])
        from live.scenario_pending_renewal import effective_deadline
        return (self.clock() >= effective_deadline(self.conn, active, payload, child) or
                (active["side"] == "LONG" and mark <= stop) or
                (active["side"] == "SHORT" and mark >= stop))

    def _verify_protection(self, observed, stop, side):
        pos = observed["position"]
        if decimal(pos["size"]) == 0:
            return True
        expected = "Buy" if side=="LONG" else "Sell"
        if pos.get("side")!=expected:
            return False
        actual = decimal(pos.get("stopLoss",0))
        if actual<=0 or (side=="LONG" and actual<decimal(stop)) or (side=="SHORT" and actual>decimal(stop)):
            return False
        # Position row alone does not prove trigger source; require native SL.
        return any(o.get("stopOrderType")=="StopLoss" and o.get("triggerBy")=="MarkPrice"
            and o.get("orderType") == "Market"
            and o.get("reduceOnly") is True and decimal(o.get("triggerPrice",0))==actual
            and decimal(o.get("qty",0))>=decimal(pos["size"])
            and o.get("side")== ("Sell" if expected=="Buy" else "Buy")
            for o in observed["open_orders"])

    def _ensure_protection(self, observed, stop, side):
        if self._verify_protection(observed,stop,side):
            return observed
        if observed["position"].get("side")!=("Buy" if side=="LONG" else "Sell"):
            self._fail("protection_position_side_conflict")
        # An atomic native update is retriable to the same/tighter stop; it does
        # not cancel the prior stop, and works even while entry ACK is UNKNOWN.
        actual = decimal(observed["position"].get("stopLoss",0))
        desired = decimal(stop)
        if actual>0:
            desired = max(actual,desired) if side=="LONG" else min(actual,desired)
        self._write("set_trading_stop",category="linear",symbol="BTCUSDT",positionIdx=0,
            tpslMode="Full",stopLoss=text_number(desired),slTriggerBy="MarkPrice",slOrderType="Market")
        fresh = self.capture_account()
        if not self._verify_protection(fresh,desired,side):
            self._fail("native_protection_not_confirmed")
        return fresh

    def _stop_revision_evidence(self, kind, intent_id):
        row = self.conn.execute(
            "SELECT body FROM llm_scenario_broker_evidence WHERE kind=? "
            "AND json_extract(body,'$.intent_id')=? ORDER BY rowid DESC LIMIT 1", (kind, intent_id)).fetchone()
        return json.loads(row[0]) if row else None

    def _revise_stop(self, payload, intent_id, observed, instrument):
        """One-shot weakening: fresh proof, durable attempt, exact readback.

        An ambiguous write leaves the old stronger stored stop untouched. Normal
        reconciliation therefore restores protection instead of retrying risk.
        """
        from core.scenario_stop_policy import build_stop_policy, validate_stop_revision
        from live.scenario_recovery import managed_permit
        if self._stop_revision_evidence("stop_revision_attempt", intent_id):
            self._fail("stop_revision_already_attempted")
        state = self._recovery_state()
        active = state["active"]
        own = self.children(active["scenario_id"])
        initial = self.conn.execute(
            "SELECT payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid LIMIT 1",
            (active["scenario_id"],)).fetchone()
        anchor = active.get("stop_policy", {})
        first = json.loads(initial[0]) if initial else {}
        if (first.get("action") != "OPEN" or first.get("action_id") != anchor.get("initial_action_id")
                or first.get("hard_stop") != anchor.get("initial_hard_stop")):
            self._fail("stop_revision_anchor_unverified")
        decisions = self.conn.execute(
            "SELECT context,proposal FROM llm_scenario_decisions WHERE context IS NOT NULL "
            "AND json_extract(proposal,'$.action_id')=? AND json_extract(proposal,'$.scenario_id')=? LIMIT 2",
            (payload["action_id"], payload["scenario_id"])).fetchall()
        if len(decisions) != 1 or json.loads(decisions[0][1]).get("stop_revision") != payload.get("stop_revision"):
            self._fail("stop_revision_input_unverified")
        decision = json.loads(decisions[0][0])
        if (decision.get("account_version") != observed["account_version"]
                or not isinstance(decision.get("input_captured_at"), (int, float))
                or not 0 <= self.clock() - decision["input_captured_at"] <= 120
                or decimal(observed["position"].get("stopLoss", 0)) != decimal(active["hard_stop"])):
            self._fail("stop_revision_stale_input_or_protection")
        accounting = self._risk_accounting(observed, active, own)
        pending = [self._pending_entry(c) for c in own if c["kind"] == "entry" and c["status"] != "TERMINAL"]
        from live.scenario_incident_epoch import pending as pending_intents, new_risk_blocked
        unknown = any(c["status"] == "UNKNOWN" or not c["evidence"] for c in own) or bool(
            set(pending_intents(self.conn, ('PENDING',))) - {intent_id})
        hard_blocked = new_risk_blocked(self) or not self._new_risk_enabled() or not self._daily(observed).get("new_risk_allowed", False)
        breaker = state.get("breaker", {})
        permit_valid = not breaker.get("blocked", False) or bool(
            set(breaker.get("reasons", [])) == {"three_losses"} and managed_permit(state, active))
        policy = build_stop_policy(active, hard_blocked=hard_blocked, permit_valid=permit_valid,
            accounting_status="confirmed", protection_ok=self._verify_protection(observed, active["hard_stop"], active["side"]),
            legacy_fenced=observed["legacy_fenced"], pending_entries=pending,
            positions=accounting["positions"], unknown_intents=unknown)
        validate_stop_revision(payload, dict(stop_policy=policy, input_id=decision.get("input_id"),
            previous_hard_stop=active["hard_stop"], side=active["side"], mark_price=observed["mark_price"],
            positions=accounting["positions"], pending_entries=pending))
        after_risk = self._preflight(payload, observed, instrument, own, stop_revision=True)
        before_risk = self._preflight(dict(payload, hard_stop=active["hard_stop"]),
            observed, instrument, own, stop_revision=True)
        before = encoded(state)
        attempt = dict(intent_id=intent_id, scenario_id=active["scenario_id"],
            old_stop=active["hard_stop"], new_stop=payload["hard_stop"],
            input_id=decision.get("input_id"), quantity=observed["position"]["size"],
            account_version=observed["account_version"], stop_policy=anchor,
            captured_at=observed["captured_at"], stop_revision=payload["stop_revision"],
            risk_before=before_risk, risk_after=after_risk,
            consumed_costs={k: accounting[k] for k in ("realized_loss", "fees_paid", "funding_paid")})
        self._save("stop_revision_attempt", attempt)
        self._write("set_trading_stop", category="linear", symbol="BTCUSDT", positionIdx=0,
            tpslMode="Full", stopLoss=text_number(payload["hard_stop"]), slTriggerBy="MarkPrice", slOrderType="Market")
        fresh = self.capture_account()
        if (decimal(fresh["position"].get("stopLoss", 0)) != decimal(payload["hard_stop"])
                or decimal(fresh["position"]["size"]) != decimal(observed["position"]["size"])
                or fresh["legacy_fenced"]
                or not self._verify_protection(fresh, payload["hard_stop"], active["side"])
                or not any(order.get("stopOrderType") == "StopLoss"
                    and order.get("triggerBy") == "MarkPrice" and order.get("orderType") == "Market"
                    and order.get("reduceOnly") is True
                    and order.get("side") == ("Sell" if active["side"] == "LONG" else "Buy")
                    and decimal(order.get("triggerPrice", 0)) == decimal(payload["hard_stop"])
                    and decimal(order.get("qty", 0)) == decimal(fresh["position"]["size"])
                    for order in fresh["open_orders"])):
            self._fail("stop_revision_readback_unconfirmed")
        if encoded(self._recovery_state()) != before:
            self._fail("stop_revision_state_changed")
        state["active"]["hard_stop"] = payload["hard_stop"]
        state["version"] = state.get("version", 0) + 1
        try:
            self.conn.execute("UPDATE llm_scenario_state SET body=? WHERE id=1", (encoded(state),))
            self.conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)", (
                "stop_revision_confirmed", self.clock(), encoded(dict(attempt, confirmed_at=fresh["captured_at"]))))
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return fresh

    def _registered_goal(self, payload, active=None):
        active = self._active() if active is None else active
        contract = active.get('entry_contract') if active else None
        table = self.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_entry_contracts'").fetchone()
        stored = self.conn.execute('SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?',
                                  (payload['scenario_id'],)).fetchone() if table else None
        from live.scenario_entry_contract import goal_record_marker, has_goal_contract, registered_contract
        marked = 'host_entry_contract_id' in payload or has_goal_contract(self.conn, active)
        try:
            stored_contract = json.loads(stored[0]) if stored else {}
        except (ValueError, TypeError):
            if marked:
                raise
            return None
        if marked or goal_record_marker(stored_contract):
            if not active or stored_contract != contract or not registered_contract(self.conn, active):
                self._fail('entry_goal_registered_record_mismatch')
            return stored_goal(self.conn, contract)
        return None

    def _validate_registered_goal(self, payload, active=None):
        if payload.get('action') not in {'OPEN', 'ADJUST'} or not (
                payload.get('entries') or payload.get('take_profits')):
            return  # Never gate EXIT/cancel/SL-only protection on optional goal history.
        validate_goal_plan(payload, self._registered_goal(payload, active))

    def _preflight(self, payload, observed, instrument, children, *, stop_revision=False, pending_renewal=False):
        self._validate_registered_goal(payload)
        from core.llm_scenario import risk_snapshot
        stop = decimal(payload["hard_stop"])
        self._aligned(stop,instrument["tick"])
        side=payload["side"]
        mark=decimal(observed["mark_price"])
        if side not in ("LONG","SHORT") or (side=="LONG" and stop>=mark) or (side=="SHORT" and stop<=mark):
            self._fail("stop_crossed_market")
        entries=payload.get("entries",[])
        conditionals = [row for row in entries if "trigger_price" in row]
        if conditionals:
            if payload.get("chase", {}).get("max_bps") or payload.get("chase", {}).get("max_reprices"):
                self._fail("conditional_entry_chase_forbidden")
            if not self.clock() < payload["expires_at"] <= (int(self.clock() // 300) + 1) * 300:
                self._fail("conditional_entry_expiry_invalid")
            armed = sum(1 for order in observed["open_orders"] if
                        order.get("orderStatus") in LIVE and decimal(order.get("triggerPrice") or 0) > 0)
            native_reserved = 0 if any(order.get("stopOrderType") == "StopLoss" and
                order.get("orderStatus") in LIVE for order in observed["open_orders"]) else 1
            # Leave capacity for the mandatory Full SL and the intended partial
            # stops when an armed entry fills; existing conditional exits remain
            # counted conservatively until their cancellation is confirmed.
            if armed + len(conditionals) + native_reserved + len(payload.get("partial_stops", [])) > 10:
                self._fail("conditional_order_capacity")
        for row in entries:
            self._aligned(row["price"],instrument["tick"])
            self._aligned(row["quantity"],instrument["step"])
            qty,price=decimal(row["quantity"]),decimal(row["price"])
            if "trigger_price" in row:
                self._aligned(row["trigger_price"], instrument["tick"])
                trigger = decimal(row["trigger_price"])
                if ((side == "LONG" and not mark < trigger <= price) or
                        (side == "SHORT" and not mark > trigger >= price)):
                    self._fail("conditional_entry_price_direction")
            if not instrument["minimum"]<=qty<=instrument["maximum"] or qty*price<instrument["notional"]:
                self._fail("entry_size_invalid")
        active=self._active()
        if not active or active["scenario_id"]!=payload["scenario_id"]:
            self._fail("active_scenario_unconfirmed")
        if active["side"] != side or (decimal(observed["position"]["size"]) and
                observed["position"].get("side") != ("Buy" if side == "LONG" else "Sell")):
            self._fail("opposite_entry_forbidden")
        state=json.loads(self.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
        from live.scenario_incident_epoch import new_risk_blocked
        hard_blocked = new_risk_blocked(self) or not self._new_risk_enabled() or not self._daily(observed).get("new_risk_allowed",False)
        if hard_blocked:
            self._fail("new_risk_halted")
        if not stop_revision and ((side=="LONG" and stop<decimal(active["hard_stop"])) or (side=="SHORT" and stop>decimal(active["hard_stop"]))):
            self._fail("stale_parent_stop")
        existing=[c for c in children if c["kind"]=="entry" and c["status"]!="TERMINAL"]
        if any(c["request"]["side"] != ("Buy" if side == "LONG" else "Sell") for c in existing):
            self._fail("opposite_pending_entry_forbidden")
        pending=[dict(price=float(c["request"]["price"]),quantity=float(c["evidence"]["order"]["leavesQty"])) for c in existing]
        size=float(observed["position"]["size"])
        positions=[dict(price=float(observed["position"]["avgPrice"]),quantity=size)] if size else []
        # Any financial uncertainty on an existing sequence blocks additions,
        # but does not block protection or reductions.
        accounting=self._risk_accounting(observed,active,children) if (size or any(c["evidence"] and c["evidence"]["executions"] for c in children)) else dict(realized_loss=0,fees_paid=0,funding_paid=0)
        if size:
            positions=accounting["positions"]
        from live.scenario_recovery import authorize_entry, managed_permit, risk_fraction
        fraction = risk_fraction(state, active["scenario_id"])
        if not stop_revision and (state.get("breaker", {}).get("blocked", False) or managed_permit(state, active) or fraction < .02):
            authority_context = dict(
                initial_equity=active["initial_equity"], new_risk_blocked=hard_blocked,
                protection_ok=not size or self._verify_protection(observed, active["hard_stop"], side),
                accounting_status='confirmed', legacy_fenced=observed['legacy_fenced'])
            if pending_renewal:
                from live.scenario_pending_renewal import retention_allowed
                allowed = retention_allowed(state, active, authority_context)
            else:
                allowed = authorize_entry(state, payload, authority_context, self.clock())
            if not allowed:
                self._fail("new_risk_halted")
        risk=risk_snapshot(initial_equity=active["initial_equity"],side=side,hard_stop=float(stop),
            risk_fraction=fraction,
            positions=positions,pending_entries=pending,entries=entries,
            realized_loss=accounting["realized_loss"],fees_paid=accounting["fees_paid"],funding_paid=accounting["funding_paid"],
            estimated_cost_rate=.002,slippage_bps=20)
        if not risk["within_budget"]:
            self._fail("fresh_risk_budget_exceeded")
        data=observed["liquidation"]
        fx=decimal(data["fx"])
        notional=sum(decimal(r["quantity"])*max(decimal(r["price"]),mark,stop) for r in entries+pending+positions)
        tier=next((t for t in data["tiers"] if notional<=decimal(t[0])),None)
        if not tier or decimal(tier[2])<10:
            self._fail("risk_tier_unverified")
        maintenance=decimal(tier[1])
        for r in entries + pending if pending_renewal else entries:
            distance=abs(decimal(r["price"])-stop)/decimal(r["price"])
            if Decimal(".1")-maintenance-Decimal(".002")<distance*2:
                self._fail("liquidation_stop_buffer_insufficient")
        if size and data.get("liq"):
            if abs(mark-decimal(data["liq"])) < abs(mark-stop)*2:
                self._fail("existing_liquidation_buffer_insufficient")
        new_notional=sum(decimal(r["quantity"])*max(decimal(r["price"]),mark) for r in entries)
        if (new_notional*Decimal(".102")*fx>=decimal(data["available"]) or
            decimal(data["margin_balance"]) - decimal(risk["remaining_risk"])*2*fx <= max(decimal(data["current_mm"]),notional*maintenance*fx)):
            self._fail("fresh_margin_insufficient")
        if not 0<=self.clock()-observed["captured_at"]<=10:
            self._fail("pre_submit_snapshot_stale")
        return risk

    def _renew_pending(self, payload, intent_id):
        """Local-only lease apply; original exchange requests remain immutable."""
        from live import scenario_pending_renewal as renewal
        from live.scenario_entry_contract import contract_context
        from core.scenario_pending_renewal import compile_renewal
        previous = renewal.receipt(self.conn, intent_id)
        if previous:
            return previous
        try:
            now = self.clock()
            state, active = self._recovery_state(), self._active()
            grant = payload['pending_renewal']
            if (not active or payload['action'] != 'WAIT' or payload.get('cancel_entry_ids')
                    or not now < payload['expires_at'] or active['revision'] != payload['revision']):
                self._fail('pending_renewal_stale_intent')
            decisions = self.conn.execute(
                "SELECT slot,context,proposal FROM llm_scenario_decisions WHERE json_extract(proposal,'$.action_id')=?",
                (intent_id,)).fetchall()
            if len(decisions) != 1 or not all(decisions[0]):
                self._fail('pending_renewal_decision_missing')
            decision_slot, context_body, proposal_body = decisions[0]
            original_context, decision = json.loads(context_body), json.loads(proposal_body)
            tape = self.conn.execute('SELECT body FROM llm_scenario_review_tapes WHERE slot=?', (decision_slot,)).fetchone()
            outcome = json.loads(tape[0]).get('outcome', {}) if tape else {}
            reviewed = outcome.get('result', {})
            selected = reviewed.get('selected', {})
            if (outcome.get('status') != 'COMPLETE' or reviewed.get('calls') != 2
                    or selected.get('id') != 'WAIT_RENEWAL'
                    or (selected.get('raw_proposal') or {}).get('pending_renewal') != {'offer_id': grant['offer_id']}
                    or (selected.get('effective_plan') or {}).get('pending_renewal') != grant):
                self._fail('pending_renewal_selected_review_missing')
            assessment = decision.get('thesis_assessment') or {}
            if (decision.get('pending_renewal') != {'offer_id': grant['offer_id']}
                    or decision.get('action') != 'WAIT' or assessment.get('status') != 'INTACT'
                    or assessment.get('contract_id') != grant['contract_id']
                    or not 0 <= now - original_context['input_captured_at'] <= 120):
                self._fail('pending_renewal_approval_unverified')
            stored = self.conn.execute('SELECT body FROM llm_scenario_entry_contracts WHERE scenario_id=?',
                                       (active['scenario_id'],)).fetchone()
            if not stored or json.loads(stored[0]) != active.get('entry_contract'):
                self._fail('pending_renewal_original_contract_unverified')
            if active['entry_contract'].get('version') == 2:
                from live.scenario_entry_contract import registered_contract
                if not registered_contract(self.conn, active):
                    self._fail('pending_renewal_original_contract_unverified')
                stored_goal(self.conn, active['entry_contract'])
            for child in self.children(active['scenario_id']):
                self._query_child(child)
            context = self.context()
            if context['account_version'] != original_context.get('account_version'):
                self._fail('pending_renewal_approved_account_changed')
            context.update(contract_context(active, True))
            context.update(now=self.clock(), scenario_id=active['scenario_id'], side=active['side'],
                           pending_renewal_version=1)
            own = self.children(active['scenario_id'])
            offered = renewal.offer(self.conn, state, context, own, self.clock())
            if offered != grant:
                self._fail('pending_renewal_reservation_changed')
            context['pending_renewal_offer'] = offered
            _, risk = compile_renewal({'offer_id': offered['offer_id']}, context)
            if risk != payload.get('risk'):
                self._fail('pending_renewal_risk_changed')
            observed = self.capture_account()
            if observed['account_version'] != context['account_version']:
                self._fail('pending_renewal_account_changed')
            observed_orders = {row['orderId']: row for row in observed['open_orders']}
            for child in own:
                if child['kind'] == 'entry':
                    current = observed_orders.get(child['order_id'], {})
                    self._verify_entry_order_terms(child, current)
                    if (current.get('orderStatus') != 'Untriggered'
                            or decimal(current.get('cumExecQty', -1)) != 0
                            or decimal(current.get('leavesQty', -1)) != decimal(child['request']['qty'])):
                        self._fail('pending_renewal_latest_order_changed')
            check = dict(scenario_id=active['scenario_id'], side=active['side'],
                         hard_stop=active['hard_stop'], entries=[])
            self._preflight(check, observed, self._instrument(), own, pending_renewal=True)
            # Fresh price may have crossed a trigger after the last child read.
            context.update(mark_price=observed['mark_price'], now=self.clock())
            compile_renewal({'offer_id': offered['offer_id']}, context)
            applied_at = self.clock()
            if (not applied_at < payload['expires_at']
                    or not 0 <= applied_at - original_context['input_captured_at'] <= 120
                    or not 0 <= applied_at - observed['captured_at'] <= 10):
                self._fail('pending_renewal_response_expired')
            return renewal.apply(self.conn, active, grant, intent_id, applied_at, clock=self.clock,
                approval_deadline=min(payload['expires_at'], original_context['input_captured_at'] + 120,
                                      observed['captured_at'] + 10))
        except Exception:
            self.conn.rollback()
            # No exchange mutation is performed by this path. Explicit rejection
            # leaves the old lease untouched; no automatic approval or retry.
            renewal.reject(self.conn, intent_id, 'FRESH_REAPPROVAL_REJECTED', self.clock())
            return renewal.receipt(self.conn, intent_id)

    def execute(self,payload,intent_id):
        from live.shared_entry_coordinator import mutation_lock
        from live.scenario_provenance import bind
        bind(payload.get("scenario_id"), intent_id)
        with mutation_lock(self.conn):
            batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(intent_id,)).fetchone()
            if batch and batch[0]=="INTERRUPTED":
                self._fail("interrupted_execution_requires_reconciliation")
            if batch and batch[0]=="ABORTED_RESTORED":
                self._fail("stop_revision_aborted_restored")
            self.conn.execute("INSERT OR IGNORE INTO llm_scenario_execution_batches VALUES(?,'EXECUTING')",(intent_id,))
            self.conn.commit()
            try:
                result=self._execute(payload,intent_id)
            except Exception:
                self.conn.execute("UPDATE llm_scenario_execution_batches SET status='ABORTED' WHERE intent_id=? AND status!='INTERRUPTED'",(intent_id,))
                self.conn.commit()
                raise
            self.conn.execute("UPDATE llm_scenario_execution_batches SET status='COMPLETE' WHERE intent_id=? AND status NOT IN ('ABORTED','INTERRUPTED')",(intent_id,))
            self.conn.commit()
            return result

    def _execute(self,payload,intent_id):
        from live.shared_entry_coordinator import mutation_lock
        with mutation_lock(self.conn):
            if not self.execution_enabled:
                self._fail("scenario_execution_disabled")
            try:
                row=self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=?",(intent_id,)).fetchone()
            except Exception:
                self._fail("persisted_intent_required")
            if not row or json.loads(row[0])!=payload:
                self._fail("persisted_intent_required")
            if payload.get("leverage",10)!=10:
                self._fail("fixed_10x_required")
            observed=self.capture_account()
            active=self._active()
            if not active or active["scenario_id"]!=payload["scenario_id"]:
                self._fail("active_scenario_required")
            from live import scenario_management_quarantine as quarantine
            if quarantine.bound_exit(self, intent_id):
                return quarantine.execute_exit(self, payload, intent_id)
            if quarantine._approval(self):
                # No unbound management or new-risk action may use the exception.
                self._fail('management_quarantine_bound_exit_required')
            if payload["action"] == "REDUCE":
                return self._execute_reduce(payload, intent_id)
            # Maintain existing protection before potentially failing recovery.
            own=self.children(active["scenario_id"])
            baseline=sum((decimal(fill["execQty"]) for c in own if c["kind"]=="entry" and c["evidence"]
                          for fill in c["evidence"]["executions"]),Decimal(0))
            self.conn.executemany("INSERT OR IGNORE INTO llm_scenario_exit_basis VALUES(?,?,?)",
                [(intent_id,text_number(observed["position"]["size"]),text_number(baseline))])
            self.conn.commit()
            if own:
                observed=self._ensure_protection(observed,active["hard_stop"],active["side"])
            elif observed["legacy_fenced"]:
                self._fail("legacy_exposure_or_unknown_order")
            self._validate_registered_goal(payload, active)
            for c in own:
                self._query_child(c)
            own=self.children(active["scenario_id"])
            if payload["action"] == "EXIT" and any(
                    c["kind"] == "reduce" and c["status"] != "TERMINAL" for c in own):
                self._fail("reduce_result_pending")
            if payload.get("stop_revision") and payload.get("action") == "ADJUST":
                from core.scenario_stop_policy import is_widening
                if is_widening(active["side"], payload["hard_stop"], active["hard_stop"]) and any(
                        c["kind"] == "entry" and c["status"] != "TERMINAL" for c in own):
                    self._fail("stop_revision_pending_entry")
            selected=set(payload.get("cancel_entry_ids",[]))
            if payload["action"]=="EXIT":
                selected={c["local_id"] for c in own if c["kind"]=="entry"}
            for c in own:
                if c["kind"]=="entry" and (c["local_id"] in selected or c["order_id"] in selected):
                    self._cancel(c)
            if payload["action"]=="WAIT":
                if payload.get('pending_renewal'):
                    return self._renew_pending(payload, intent_id)
                return
            if payload["action"]=="EXIT":
                for c in self.children(active["scenario_id"]):
                    if c["kind"] in {"tp","partial_sl"} and c["status"]!="TERMINAL":
                        self._cancel(c)
                observed=self.capture_account()
                size=decimal(observed["position"]["size"])
                if size:
                    if observed["legacy_fenced"] or observed["position"].get("side") != ("Buy" if active["side"]=="LONG" else "Sell"):
                        self._fail("exit_exposure_unreconciled")
                    instrument=self._instrument()
                    if size>instrument["market_maximum"]:
                        self._fail("exit_quantity_requires_split")
                    return self._submit(payload,intent_id,"exit","full",dict(side="Sell" if active["side"]=="LONG" else "Buy",
                        orderType="Market",qty=text_number(size),reduceOnly=True,closeOnTrigger=True))
                return
            if self.clock()>=payload["expires_at"]:
                self._fail("expired_scenario_action")
            instrument=self._instrument()
            desired=payload["hard_stop"]
            old=active["hard_stop"]
            widening = (active["side"]=="LONG" and desired<old) or (active["side"]=="SHORT" and desired>old)
            if widening:
                observed=self._revise_stop(payload,intent_id,self.capture_account(),instrument)
            elif desired!=old:
                # Pending old SL attachments cannot later overwrite a tighter SL.
                for c in own:
                    if c["kind"]=="entry" and c["status"]!="TERMINAL":
                        self._cancel(c)
                observed=self._ensure_protection(self.capture_account(),desired,active["side"])
            for index,entry in enumerate(payload.get("entries",[])):
                link=stable_link(intent_id,"entry",entry["id"])
                existing=next((c for c in self.children() if c["link_id"]==link),None)
                if existing:
                    self._query_child(existing)
                    continue
                observed=self.capture_account()
                if observed["legacy_fenced"]:
                    self._fail("legacy_exposure_or_unknown_order")
                # Validate all not-yet-submitted child risk before each write.
                remaining=dict(payload,entries=payload["entries"][index:])
                self._preflight(remaining,observed,instrument,self.children(active["scenario_id"]))
                request = dict(
                    side="Buy" if payload["side"]=="LONG" else "Sell",orderType="Limit",
                    qty=self._aligned(entry["quantity"],instrument["step"]),price=self._aligned(entry["price"],instrument["tick"]),
                    timeInForce="GTC",reduceOnly=False,stopLoss=self._aligned(desired,instrument["tick"]),
                    tpslMode="Full",slTriggerBy="MarkPrice",slOrderType="Market")
                if "trigger_price" in entry:
                    request.update(triggerPrice=self._aligned(entry["trigger_price"], instrument["tick"]),
                                   triggerBy="MarkPrice", triggerDirection=1 if payload["side"] == "LONG" else 2)
                self._submit(payload,intent_id,"entry",entry["id"],request)
            self._sync_exits(payload,intent_id)

    def _continue_unsubmitted_exit(self, active):
        """Finish cancellation-first EXIT, never replay an ambiguous market POST.

        The child record is committed before every POST. Absence of that child
        plus a persisted interrupted/aborted batch proves the market leg has
        not been attempted. This only resumes the latest committed close intent;
        no entry batch, superseded decision or existing exit child is retried.
        Caller holds the shared mutation lock and has reconciled all children.
        """
        row=self.conn.execute("SELECT id,payload,status FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",
                              (active["scenario_id"],)).fetchone()
        if not row or row[2]=="TERMINAL":
            return False
        ident,body,_=row
        payload=json.loads(body)
        if payload["action"]!="EXIT":
            return False
        from live import scenario_management_quarantine as quarantine
        if quarantine.bound_exit(self, ident):
            quarantine.execute_exit(self, payload, ident)
            return True
        batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(ident,)).fetchone()
        if not batch or batch[0] not in {"ABORTED","INTERRUPTED"}:
            return False
        if any(c["intent_id"]==ident for c in self.children(active["scenario_id"])):
            return False
        observed=self.capture_account()
        if observed["legacy_fenced"]:
            self._fail("exit_exposure_unreconciled")
        self._save("exit_continuation",dict(intent_id=ident,scenario_id=active["scenario_id"],
            basis="durable_batch_without_submitted_child",previous_batch_status=batch[0]))
        self._execute(payload,ident)
        self.conn.execute("UPDATE llm_scenario_execution_batches SET status='COMPLETE' WHERE intent_id=?",(ident,))
        self.conn.commit()
        return True

    def _sync_exits(self,payload,intent_id):
        """Rebuild partial exits against confirmed current size; Full SL stays."""
        observed=self._ensure_protection(self.capture_account(),payload["hard_stop"],payload["side"])
        size=decimal(observed["position"]["size"])
        if not size:
            return
        instrument=self._instrument()
        # Revision + current filled position quantity fixes idempotency. All
        # prior partial exits cancel exactly before replacements, but native
        # Full SL is never cancelled and continues protecting every unit.
        children=self.children(payload["scenario_id"])
        entered=sum((decimal(e["execQty"]) for c in children if c["kind"]=="entry" and c["evidence"]
                     for e in c["evidence"]["executions"]),Decimal(0))
        basis=self.conn.execute("SELECT quantity,entry_baseline FROM llm_scenario_exit_basis WHERE intent_id=?",(intent_id,)).fetchone()
        if not basis:
            self._fail("exit_allocation_basis_missing")
        allocation=decimal(basis[0])+entered-decimal(basis[1])
        if allocation<0:
            self._fail("exit_allocation_basis_conflict")
        generation=hashlib.sha256(encoded([intent_id,str(entered)]).encode()).hexdigest()[:16]
        for child in self.children(payload["scenario_id"]):
            if child["kind"] in ("tp","partial_sl") and child["status"]!="TERMINAL" and not child["local_id"].startswith(generation+":"):
                self._cancel(child)
        observed=self._ensure_protection(self.capture_account(),payload["hard_stop"],payload["side"])
        size=decimal(observed["position"]["size"])
        children=self.children(payload["scenario_id"])
        for field,kind in (("take_profits","tp"),("partial_stops","partial_sl")):
            allocated=Decimal(0)
            for row in payload.get(field,[]):
                current_id=generation+":"+row["id"]
                existing=next((c for c in children if c["intent_id"]==intent_id and c["kind"]==kind and c["local_id"]==current_id),None)
                if existing:
                    self._query_child(existing)
                    # A target filled during this generation is never recreated.
                    allocated+=decimal(existing["request"]["qty"])
                    continue
                consumed=sum((decimal(e["execQty"]) for c in children if c["kind"]==kind and
                    c["intent_id"]==intent_id and c["local_id"].partition(":")[2]==row["id"] and c["evidence"]
                    for e in c["evidence"]["executions"]),Decimal(0))
                quantity=(max(Decimal(0),min(size-allocated,allocation*decimal(row["fraction"])-consumed))/instrument["step"]).to_integral_value(rounding=ROUND_DOWN)*instrument["step"]
                allocated+=quantity
                if quantity<instrument["minimum"]:
                    continue
                if allocated>size or quantity>instrument["maximum"]:
                    self._fail("partial_exit_quantity_invalid")
                request=dict(side="Sell" if payload["side"]=="LONG" else "Buy",
                    qty=text_number(quantity),reduceOnly=True,orderType="Limit",timeInForce="GTC",
                    price=self._aligned(row["price"],instrument["tick"]))
                if kind=="partial_sl":
                    request.pop("price")
                    request.update(orderType="Market",closeOnTrigger=True,
                        triggerPrice=self._aligned(row["price"],instrument["tick"]),triggerBy="MarkPrice",
                        triggerDirection=2 if payload["side"]=="LONG" else 1)
                self._submit(payload,intent_id,kind,current_id,request)

    def chase(self,intent_id,entry_id,new_price):
        """Bounded explicit repricing, only after exact cancellation evidence."""
        from live.shared_entry_coordinator import mutation_lock
        with mutation_lock(self.conn):
            row=self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE id=?",(intent_id,)).fetchone()
            if not row:
                self._fail("chase_parent_missing")
            payload=json.loads(row[0])
            from live.scenario_recovery import managed_permit, normal_permission
            state = self._recovery_state()
            active = state.get("active")
            if managed_permit(state, active) or state.get("breaker", {}).get("blocked", False):
                observed = self.capture_account()
                ctx = dict(initial_equity=active.get("initial_equity") if active else None,
                    legacy_fenced=observed["legacy_fenced"], accounting_status="confirmed",
                    protection_ok=not float(observed["position"]["size"]) or
                        self._verify_protection(observed, active["hard_stop"], active["side"]),
                    recovery_hard_blocked=not self._new_risk_enabled() or
                        not self._daily(observed).get("new_risk_allowed", False))
                if not normal_permission(state, active, ctx):
                    self._fail("recovery_probe_chase_forbidden")
            newest=self.conn.execute("SELECT id FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",(payload["scenario_id"],)).fetchone()
            if not newest or newest[0]!=intent_id:
                self._fail("chase_stale_revision")
            if self.clock()>=payload["expires_at"]:
                self._fail("chase_expired")
            original=next((e for e in payload["entries"] if e["id"]==entry_id),None)
            if original is None:
                self._fail("chase_entry_missing")
            if "trigger_price" in original:
                self._fail("conditional_entry_chase_forbidden")
            rows=[c for c in self.children(payload["scenario_id"]) if c["intent_id"]==intent_id and
                c["kind"]=="entry" and (c["local_id"]==entry_id or c["local_id"].startswith(entry_id+":chase:"))]
            if not rows or len(rows)-1>=payload["chase"]["max_reprices"]:
                self._fail("chase_limit")
            if abs(decimal(new_price)/decimal(original["price"])-1)*10000>decimal(payload["chase"]["max_bps"]):
                self._fail("chase_price_bound")
            latest=rows[-1]
            latest=self._query_child(latest)
            if latest["status"]!="LIVE":
                self._fail("chase_requires_live_remainder")
            self._validate_registered_goal(dict(payload, entries=[dict(original, price=float(decimal(new_price)))]), active)
            cancelled=self._cancel(latest)
            quantity=decimal(latest["request"]["qty"])-decimal(cancelled["evidence"]["order"]["cumExecQty"])
            if quantity<=0:
                return
            replacement=dict(id=entry_id+":chase:"+str(len(rows)),price=float(decimal(new_price)),quantity=float(quantity))
            observed=self.capture_account()
            if observed["legacy_fenced"]:
                self._fail("chase_unknown_exposure")
            self._preflight(dict(payload,entries=[replacement]),observed,self._instrument(),self.children(payload["scenario_id"]))
            request=dict(latest["request"],price=text_number(new_price),qty=text_number(quantity))
            for key in ("category","symbol","positionIdx","orderLinkId"):
                request.pop(key,None)
            return self._submit(payload,intent_id,"entry",replacement["id"],request)

    def _autochase(self,active,observed):
        """The plan's positive chase allowance authorizes <= one reprice/minute."""
        from decimal import ROUND_UP
        row=self.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",(active["scenario_id"],)).fetchone()
        if not row:
            return
        ident,raw=row
        plan=json.loads(raw)
        if plan["action"] not in {"OPEN","ADJUST"} or not plan.get("chase",{}).get("max_reprices") or self.clock()>=plan["expires_at"]:
            return
        instrument=self._instrument()
        quote=observed["ticker"].get("ask1Price" if active["side"]=="LONG" else "bid1Price")
        if quote in (None,"") or decimal(quote)<=0:
            return
        price=(decimal(quote)/instrument["tick"]).to_integral_value(rounding=ROUND_UP if active["side"]=="LONG" else ROUND_DOWN)*instrument["tick"]
        children=self.children(active["scenario_id"])
        for entry in plan.get("entries",[]):
            if "trigger_price" in entry:
                continue
            related=[c for c in children if c["intent_id"]==ident and c["kind"]=="entry" and
                (c["local_id"]==entry["id"] or c["local_id"].startswith(entry["id"]+":chase:"))]
            if not related or len(related)-1>=plan["chase"]["max_reprices"]:
                continue
            latest=related[-1]
            if latest["status"]!="LIVE" or self.clock()-latest["created_at"]<60:
                continue
            old=decimal(latest["request"]["price"])
            if (active["side"]=="LONG" and price<=old) or (active["side"]=="SHORT" and price>=old):
                continue
            if abs(price/decimal(entry["price"])-1)*10000>decimal(plan["chase"]["max_bps"]):
                continue
            goal = self._registered_goal(plan, active)  # Source/auth failures remain UNKNOWN.
            try:
                validate_goal_plan(dict(plan, entries=[dict(entry, price=float(price))]), goal)
            except GoalConsistencyRejection:
                continue  # Known out-of-goal quote; no cancellation/write or UNKNOWN evidence.
            return self.chase(ident,entry["id"],price)
