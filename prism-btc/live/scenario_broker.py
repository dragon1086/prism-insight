"""MAIN-demo exchange evidence and explicitly enabled scenario execution.

The default adapter is read-only. The opt-in executor requires the durable MAIN
handoff policy for new risk, native protection and exact financial evidence.
Empty funding responses alone never mean settled zero funding. Legacy partial
close/order ledger mutation paths are not reused.

Sources checked 2026-10-03: Bybit v5 demo, execution/list, transaction-log docs.
Transaction cashFlow excludes fees/funding; funding > 0 is a receipt. REST
execution/list has no documented execPnl, so gross PnL requires tradeId joins.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from contextlib import suppress
from contextvars import ContextVar

from core.scenario_limit_prices import POLICY_VERSION, observed_quotes

from live.exchange_snapshot import read_complete
from live.liquidation_guard import capture as capture_liquidation
from live.shared_entry_coordinator import database_path, mutation_lock
from live.scenario_execution import ScenarioExecution, decimal


_MONITOR_CALL_TIMING = ContextVar('btc_incident_monitor_call_timing', default=None)
_MONITOR_METHODS = frozenset({
    'get_api_key_information', 'get_open_orders', 'get_order_history',
    'get_executions', 'get_transaction_log', 'get_positions',
    'get_wallet_balance', 'get_account_info', 'get_risk_limit', 'get_tickers',
    'get_funding_rate_history', 'get_instruments_info',
})


class BrokerNotReady(ValueError):
    """Sanitized fail-closed reason, without transport payload or credentials."""


class BrokerProtectionPending(BrokerNotReady):
    """Tightening acknowledged; prior native protection remains verified."""


class BrokerCancellationPending(BrokerNotReady):
    """Exact cancellation ACK saved; terminal order evidence is still pending."""


def _number(value, *, minimum=None):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise BrokerNotReady("numeric_evidence_missing") from None
    if isinstance(value, bool) or not math.isfinite(result) or (minimum is not None and result < minimum):
        raise BrokerNotReady("numeric_evidence_invalid")
    return result


def _verify_funding_execution(row, transaction, start_ms, end_ms):
    """One Funding execution is corroboration, never a second economic flow.

    Bybit transaction-log documents funding's opposite sign to execFee.
    Missing/ambiguous pairs remain fenced; downstream schedule coverage is
    still responsible for proving that all funding settlements are present.
    """
    if (transaction.get("type") != "SETTLEMENT" or transaction.get("symbol") != "BTCUSDT"
            or transaction.get("currency") != "USDT"
            or transaction.get("tradeId") != row["execId"]
            or row.get("side") not in {"Buy", "Sell"} or transaction.get("side") != row["side"]
            or not isinstance(row.get("orderId"), str) or not row["orderId"]
            or transaction.get("orderId") != row["orderId"]):
        raise BrokerNotReady("funding_execution_identity_mismatch")
    when, quantity = decimal(row.get("execTime")), decimal(row.get("execQty"))
    if (not start_ms <= when <= end_ms or when != decimal(transaction.get("transactionTime"))
            or quantity <= 0 or quantity != decimal(transaction.get("qty"))
            or decimal(transaction.get("fee")) != 0 or decimal(transaction.get("cashFlow")) != 0
            or decimal(row.get("execFee")) != -decimal(transaction.get("funding"))):
        raise BrokerNotReady("funding_execution_value_mismatch")


def read_evidence_pages(call, method, *, max_pages=100, **params):
    """Exhaust a bounded query, rejecting cursor cycles and contradictory IDs.

Exhausted pagination proves only a complete response, NOT financial finality.
"""
    key = {"get_executions": "execId", "get_transaction_log": "id",
           "get_order_history": "orderId"}.get(method)
    if key is None:
        raise BrokerNotReady("unsupported_evidence_method")
    rows, cursors, cursor = {}, set(), None
    for _ in range(max_pages):
        try:
            result = call(method, **params, **({"cursor": cursor} if cursor else {}))
            if not isinstance(result, dict) or result.get("retCode") != 0:
                raise BrokerNotReady("evidence_query_failed")
            body = result["result"]
            if not isinstance(body.get("list"), list):
                raise BrokerNotReady("evidence_rows_missing")
            for row in body["list"]:
                if not isinstance(row, dict) or not isinstance(row.get(key), str) or not row[key]:
                    raise BrokerNotReady("evidence_identity_missing")
                ident = row[key]
                if ident in rows and rows[ident] != row:
                    raise BrokerNotReady("evidence_identity_conflict")
                rows[ident] = row
            cursor = body.get("nextPageCursor")
            if not cursor:
                return list(rows.values())
            if not isinstance(cursor, str) or cursor in cursors:
                raise BrokerNotReady("evidence_cursor_cycle")
            cursors.add(cursor)
        except BrokerNotReady:
            raise
        except Exception:
            raise BrokerNotReady("evidence_transport_unknown") from None
    raise BrokerNotReady("evidence_page_bound")


class ScenarioDemoBroker(ScenarioExecution):
    environment = "demo"
    lane = "MAIN"
    capabilities = frozenset()
    execution_blockers = ("explicit_execution_enablement_required",)

    def __init__(self, conn, *, session=None, expected_main_uid, clock=time.time, execution_enabled=False, convergence_entry_enabled=False):
        database_path(conn)
        if type(convergence_entry_enabled) is not bool:
            raise ValueError('convergence_entry_flag_must_be_bool')
        self.convergence_entry_enabled = convergence_entry_enabled
        if not isinstance(expected_main_uid, str) or not expected_main_uid.isdigit() or int(expected_main_uid) <= 0:
            raise BrokerNotReady("explicit_main_uid_required")
        if session is None:
            from live.demo import _make_session
            session, error = _make_session()
            if session is None or error:
                raise BrokerNotReady("demo_session_unavailable")
        if getattr(session, "endpoint", None) != "https://api-demo.bybit.com":
            raise BrokerNotReady("demo_endpoint_required")
        self.conn, self.session = conn, session
        self.expected_main_uid, self.clock = expected_main_uid, clock
        with mutation_lock(conn):
            conn.execute("""CREATE TABLE IF NOT EXISTS llm_scenario_broker_evidence (
                kind TEXT NOT NULL, captured_at REAL NOT NULL, body TEXT NOT NULL)""")
            conn.execute("CREATE INDEX IF NOT EXISTS llm_scenario_broker_evidence_kind ON llm_scenario_broker_evidence(kind)")
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_broker_notices(event_id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_broker_incidents(scenario_id TEXT PRIMARY KEY,episode INTEGER NOT NULL,active INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_notice_positions(scenario_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)")
            conn.commit()
            self._init_execution(execution_enabled)
            if self.execution_enabled:
                from live.scenario_runtime import REQUIRED_CAPABILITIES
                self.capabilities=REQUIRED_CAPABILITIES | {"immediate_reduce_v1"}
                self.execution_blockers=()

    def _call(self, method, **params):
        trace = _MONITOR_CALL_TIMING.get()
        started = None
        if trace is not None:
            try:
                started = time.perf_counter_ns()
            except Exception:
                # Diagnostic failures cannot alter broker evidence.
                with suppress(Exception):
                    trace['measurement_failures'] += 1
        ok = False
        try:
            if getattr(self.session, "endpoint", None) != "https://api-demo.bybit.com":
                raise BrokerNotReady("demo_endpoint_changed")
            if not method.startswith("get_"):
                raise BrokerNotReady("execution_not_implemented")
            try:
                response = getattr(self.session, method)(**params)
            except Exception:
                raise BrokerNotReady("broker_transport_unknown") from None
            if not isinstance(response, dict) or response.get("retCode") != 0:
                raise BrokerNotReady("broker_query_failed")
            ok = True
            return response
        finally:
            if trace is not None and started is not None:
                try:
                    if len(trace['calls']) < 64:
                        elapsed = time.perf_counter_ns() - started
                        if elapsed < 0:
                            trace['measurement_failures'] += 1
                        else:
                            trace['calls'].append(dict(method=method if method in _MONITOR_METHODS else 'other',
                                                       elapsed_ms=round(elapsed / 1_000_000, 3), ok=ok))
                    else:
                        trace['dropped'] += 1
                except Exception:
                    # The counter is optional if the diagnostic state is damaged.
                    with suppress(Exception):
                        trace['measurement_failures'] += 1

    def _identity(self):
        identity = self._call("get_api_key_information").get("result", {}).get("userID")
        if str(identity) != self.expected_main_uid:
            raise BrokerNotReady("main_uid_mismatch")

    def _save(self, kind, body):
        text = json.dumps(body, allow_nan=False, sort_keys=True)
        if kind == 'financial':
            original = text
            try:
                from utils.btc_financial_references import compact, restore
                candidate = compact(text)
                if restore(candidate) == text:
                    text = candidate
            except Exception:
                # Optional deduplication must never interrupt broker evidence or protection.
                text = original
        self.conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)",
                          (kind, self.clock(), text))
        self.conn.commit()

    def _legacy_local(self):
        tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "btc_positions" in tables and self.conn.execute(
                "SELECT 1 FROM btc_positions WHERE (mode LIKE '%demo%' OR mode='swing') AND qty>0 LIMIT 1").fetchone():
            return True
        # Existing unknown reservations are not adopted as this scenario's orders.
        if "entry_reservations" in tables:
            # Schema versions vary: presence is conservatively fenced until the
            # authoritative reservation store can reconcile each local record.
            if self.conn.execute("SELECT 1 FROM entry_reservations WHERE state NOT IN ('FILLED','CANCELLED_CONFIRMED') LIMIT 1").fetchone():
                return True
        if "btc_meta" in tables:
            for (body,) in self.conn.execute("SELECT value FROM btc_meta WHERE (mode LIKE '%demo%' OR mode='swing') AND key LIKE '%pending%'"):
                try:
                    if json.loads(body):
                        return True
                except (TypeError, ValueError):
                    return True
        return False

    def capture_account(self):
        """Fresh read-only observation; NOT a validated trading risk context."""
        with mutation_lock(self.conn):
            self._identity()
            started = self.clock()
            positions = read_complete(self._call, "get_positions", category="linear", symbol="BTCUSDT")
            orders = read_complete(self._call, "get_open_orders", category="linear", symbol="BTCUSDT")
            if positions is None or orders is None:
                raise BrokerNotReady("account_snapshot_incomplete")
            rows = positions["result"]["list"]
            if len(rows) != 1 or rows[0].get("symbol") != "BTCUSDT" or rows[0].get("positionIdx") != 0:
                raise BrokerNotReady("one_way_position_evidence_required")
            position = rows[0]
            size = _number(position.get("size"), minimum=0)
            leverage = _number(position.get("leverage"), minimum=1)
            if leverage != 10:
                raise BrokerNotReady("fixed_10x_not_confirmed")
            wallets = self._call("get_wallet_balance", accountType="UNIFIED").get("result", {}).get("list")
            if not isinstance(wallets, list) or len(wallets) != 1:
                raise BrokerNotReady("wallet_evidence_missing")
            wallet = wallets[0]
            equity = _number(wallet.get("totalEquity"), minimum=0)
            if equity <= 0:
                raise BrokerNotReady("equity_invalid")
            liquidation = capture_liquidation(self._call, wallet, position if size else None, started)
            # totalEquity is USD; scenario prices/costs/budget are USDT.
            equity=equity/liquidation["fx"]
            ticker_started = self.clock()
            ticker_response = self._call("get_tickers", category="linear", symbol="BTCUSDT")
            ticker_received = self.clock()
            tickers = ticker_response.get("result", {}).get("list")
            if not isinstance(tickers, list) or len(tickers) != 1 or tickers[0].get("symbol") != "BTCUSDT":
                raise BrokerNotReady("ticker_evidence_missing")
            mark = _number(tickers[0].get("markPrice"), minimum=0)
            if mark <= 0 or not 0 <= self.clock() - started <= 10:
                raise BrokerNotReady("account_snapshot_stale")
            observed = dict(equity=equity, position=position, open_orders=orders["result"]["list"],
                mark_price=mark, liquidation=liquidation, exchange_flat=size == 0,
                legacy_fenced=bool(size or orders["result"]["list"] or self._legacy_local()),
                protection_confirmed=False, execution_ready=False,
                execution_blockers=list(self.execution_blockers), captured_at=math.floor(self.clock()*1000)/1000)
            known=self.children()
            known_ids={c["order_id"] for c in known if c["order_id"]}
            active=self._active()
            native = [o for o in observed["open_orders"] if active and known and
                o.get("symbol")=="BTCUSDT" and o.get("positionIdx")==0 and
                o.get("stopOrderType")=="StopLoss" and o.get("triggerBy")=="MarkPrice" and
                o.get("reduceOnly") is True and o.get("side")== ("Sell" if active["side"]=="LONG" else "Buy") and
                decimal(o.get("triggerPrice",0))==decimal(position.get("stopLoss",0))]
            known_ids.update(o.get("orderId") for o in native)
            signed=0.
            for child in known:
                if child.get("evidence"):
                    for fill in child["evidence"]["executions"]:
                        signed += float(fill["execQty"])*(1 if fill["side"]=="Buy" else -1)
            actual_signed=size*(1 if position.get("side")=="Buy" else -1)
            observed["legacy_fenced"] = bool(self._legacy_local() or
                any(o.get("orderId") not in known_ids for o in observed["open_orders"]) or
                not math.isclose(signed,actual_signed,abs_tol=1e-10))
            observed["native_stops"] = native
            observed["wallet"] = wallet
            observed["ticker"] = tickers[0]
            # Response time is not a quote-update or matching-engine timestamp.
            server_ms = ticker_response.get("time")
            valid_server_ms = (type(server_ms) in (int, float) and 0 < server_ms < 2**53
                               and server_ms == int(server_ms))
            observed["ticker_timing"] = dict(request_started_at=ticker_started,
                response_received_at=ticker_received,
                exchange_response_time_ms=int(server_ms) if valid_server_ms else None)
            if active:
                self._retain_native(observed,active)
            # Ordinary market drift must not stale every out-of-lock proposal;
            # executor rechecks mark, equity, available margin before writes.
            economic = dict(position={k:position.get(k) for k in ("side","size","avgPrice","stopLoss","leverage","positionIdx")},
                orders=sorted([{k:o.get(k) for k in ("orderId","orderStatus","qty","leavesQty","cumExecQty","price","triggerPrice","stopLoss")}
                               for o in observed["open_orders"]],key=lambda o:str(o["orderId"])))
            observed["account_version"] = hashlib.sha256(json.dumps(economic, sort_keys=True).encode()).hexdigest()
            self._save("account", observed)
            return observed

    def capture_financial_evidence(self, start_ms, end_ms):
        """Store actual trade/fee/funding rows, leaving finality explicitly unknown."""
        if (type(start_ms) is not int or type(end_ms) is not int or
                not 0 <= start_ms < end_ms or end_ms-start_ms > 7*86400*1000):
            raise BrokerNotReady("bounded_evidence_window_required")
        with mutation_lock(self.conn):
            self._identity()
            executions = read_evidence_pages(self._call, "get_executions", category="linear",
                symbol="BTCUSDT", startTime=start_ms, endTime=end_ms, limit=100)
            transactions = read_evidence_pages(self._call, "get_transaction_log", accountType="UNIFIED",
                category="linear", currency="USDT", startTime=start_ms, endTime=end_ms, limit=50)
            trade_rows, funding_rows, funding, unknown = {}, {}, [], []
            for row in transactions:
                if row.get("symbol") != "BTCUSDT":
                    continue
                if row.get("currency") != "USDT":
                    raise BrokerNotReady("unexpected_settlement_currency")
                when = _number(row.get("transactionTime"), minimum=0)
                if not start_ms <= when <= end_ms:
                    raise BrokerNotReady("transaction_outside_window")
                if row.get("type") == "TRADE":
                    ident = row.get("tradeId")
                    if not isinstance(ident, str) or not ident or ident in trade_rows:
                        raise BrokerNotReady("trade_transaction_identity_ambiguous")
                    trade_rows[ident] = row
                elif row.get("type") == "SETTLEMENT":
                    ident = row.get("tradeId")
                    if ident not in (None, ""):
                        if not isinstance(ident, str) or ident in funding_rows:
                            raise BrokerNotReady("funding_transaction_identity_ambiguous")
                        funding_rows[ident] = row
                    funding.append(dict(transaction_id=row["id"], timestamp=when,
                        funding_net=_number(row.get("funding")), raw=row))
                else:
                    unknown.append(row["id"])
            trades = []
            if set(trade_rows) & set(funding_rows):
                raise BrokerNotReady("financial_transaction_identity_collision")
            for row in executions:
                if row.get("symbol") == "BTCUSDT" and row.get("execType") == "Funding":
                    txn = funding_rows.pop(row["execId"], None)
                    if txn is None:
                        unknown.append(row["execId"])
                    else:
                        _verify_funding_execution(row, txn, start_ms, end_ms)
                    continue
                if row.get("symbol") != "BTCUSDT" or row.get("execType") != "Trade":
                    unknown.append(row["execId"])
                    continue
                when = _number(row.get("execTime"), minimum=0)
                if not start_ms <= when <= end_ms:
                    raise BrokerNotReady("execution_outside_window")
                fee = _number(row.get("execFee"))
                qty, price = _number(row.get("execQty"), minimum=0), _number(row.get("execPrice"), minimum=0)
                if qty <= 0 or price <= 0:
                    raise BrokerNotReady("execution_quantity_price_invalid")
                txn = trade_rows.pop(row["execId"], None)
                if txn is None:
                    unknown.append(row["execId"])
                    continue
                if txn.get("orderId") != row.get("orderId") or not row.get("orderId"):
                    raise BrokerNotReady("execution_transaction_order_mismatch")
                if not math.isclose(fee, _number(txn.get("fee")), rel_tol=0, abs_tol=1e-10):
                    raise BrokerNotReady("execution_transaction_fee_mismatch")
                trades.append(dict(execution_id=row["execId"], order_id=row["orderId"], timestamp=when,
                    quantity=qty, price=price, fee=fee, gross_pnl=_number(txn.get("cashFlow")), raw_execution=row, raw_transaction=txn))
            unknown.extend(trade_rows)
            result = dict(start_ms=start_ms, end_ms=end_ms, trades=trades, funding=funding,
                unmatched_ids=unknown, raw_executions=executions, raw_transactions=transactions,
                response_pages_complete=True, executions_complete=False,
                fees_complete=False, funding_complete=False, settlement_ready=False,
                reason="scenario_order_coverage_and_funding_finality_unverified")
            self._save("financial", result)
            self._fee_capture = (result, self.expected_main_uid, self.clock())
            return result

    def _funding_schedule(self,start_ms,end_ms,observed):
        rows=self._call("get_funding_rate_history",category="linear",symbol="BTCUSDT",
                        startTime=start_ms,endTime=end_ms,limit=200).get("result",{}).get("list")
        if not isinstance(rows,list) or len(rows)>=200:
            raise BrokerNotReady("funding_schedule_incomplete")
        # At most seven days / three events per day fits the documented bound.
        if end_ms-start_ms>7*86400000 or _number(observed["ticker"].get("nextFundingTime"))<=end_ms:
            raise BrokerNotReady("funding_schedule_future_boundary_unknown")
        events=[]
        for row in rows:
            if row.get("symbol")!="BTCUSDT":
                raise BrokerNotReady("funding_schedule_identity")
            when=int(row["fundingRateTimestamp"])
            if not start_ms<=when<=end_ms:
                raise BrokerNotReady("funding_schedule_range")
            events.append(dict(timestamp=when,rate=_number(row.get("fundingRate"))))
        instruments=self._call("get_instruments_info",category="linear",symbol="BTCUSDT")["result"]["list"]
        if len(instruments)!=1 or instruments[0].get("symbol")!="BTCUSDT":
            raise BrokerNotReady("funding_interval_missing")
        interval=int(_number(instruments[0].get("fundingInterval"),minimum=1))*60000
        next_time=int(observed["ticker"]["nextFundingTime"])
        expected=set()
        cursor=next_time-interval
        while cursor>=start_ms:
            if cursor<=end_ms:
                expected.add(cursor)
            cursor-=interval
        if {e["timestamp"] for e in events}!=expected:
            raise BrokerNotReady("funding_schedule_event_missing")
        # Optional source retention; never add audit fields to the business result.
        try:
            self._audit_funding_source = dict(raw_rows=rows, instruments=instruments,
                next_funding_time=next_time, observed_at=time.time())
        except Exception:
            self._audit_funding_source = None
        return dict(start_ms=start_ms,end_ms=end_ms,complete=True,events=events)

    def _accounting(self,observed,active,children):
        from live.scenario_accounting import reconcile_scenario
        start=int(active["created_at"]*1000)
        end=int(self.clock()*1000)
        if end<=start:
            end=start+1
        self._fee_capture = None
        evidence=self.capture_financial_evidence(start,end)
        owned=[]
        for c in children:
            if not c.get("evidence"):
                raise BrokerNotReady("child_accounting_evidence_missing")
            owned.append(dict(order_id=c["order_id"],role="entry" if c["kind"]=="entry" else "exit",
                side=c["request"]["side"],cumulative_qty=float(c["evidence"]["order"]["cumExecQty"]),terminal=c["status"]=="TERMINAL"))
        schedule=self._funding_schedule(start,end,observed)
        result=reconcile_scenario(active["scenario_id"],evidence,owned,observed,
            funding_schedule=schedule)
        from live.scenario_fee_projection import observed_fee_evidence
        fee_evidence = observed_fee_evidence(evidence, result, now=self.clock())
        from live.scenario_provenance import record
        record("accounting_observation", dict(financial_evidence=evidence, owned_orders=owned,
            children=children, observation=observed, funding_schedule=schedule,
            funding_source=getattr(self,"_audit_funding_source",None), result=result,
            observed_execution_fee_evidence=fee_evidence),
            scenario_id=active["scenario_id"])
        self._save("scenario_accounting",result)
        self._cache_fee_evidence(evidence, fee_evidence)
        # Preserve the pure reconciliation result; optional review facts are a sidecar.
        return dict(result, observed_execution_fee_evidence=fee_evidence)

    def _cache_fee_evidence(self, evidence, fees):
        """One prospective optional record; failure cannot invalidate accounting."""
        from live.scenario_fee_projection import verified_taker_rate
        try:
            captured = self._fee_capture
            if not captured or captured[0] is not evidence or captured[1] != self.expected_main_uid:
                return
            verified_taker_rate(fees, captured[2])
            body = dict(version=1, main_uid=self.expected_main_uid, environment=self.environment,
                endpoint=self.session.endpoint, symbol='BTCUSDT', captured_at=captured[2], evidence=fees)
            with mutation_lock(self.conn):
                self.conn.execute('SAVEPOINT optional_fee_cache')
                try:
                    rows = self.conn.execute("SELECT body FROM llm_scenario_broker_evidence WHERE kind='review_fee_sidecar_v1'").fetchall()
                    if len(rows) == 1:
                        old = json.loads(rows[0][0])
                        if (old.get('main_uid') == body['main_uid']
                                and old.get('evidence', {}).get('source_sha256') == fees['source_sha256']):
                            self.conn.execute('RELEASE optional_fee_cache')
                            return
                    self.conn.execute("DELETE FROM llm_scenario_broker_evidence WHERE kind='review_fee_sidecar_v1'")
                    self.conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)',
                        ('review_fee_sidecar_v1', captured[2], json.dumps(body, allow_nan=False, sort_keys=True)))
                    self.conn.execute('RELEASE optional_fee_cache')
                except Exception:
                    self.conn.execute('ROLLBACK TO optional_fee_cache')
                    self.conn.execute('RELEASE optional_fee_cache')
        except Exception:
            return  # Optional cache failure never invalidates confirmed accounting.

    def _flat_fee_evidence(self, observed_at):
        from live.scenario_control import read_control
        from live.scenario_fee_projection import verified_taker_rate
        unknown = dict(status='UNKNOWN')
        try:
            control = read_control(self.conn)
            if not control or control['main_uid'] != self.expected_main_uid:
                return unknown
            rows = self.conn.execute("SELECT captured_at,body FROM llm_scenario_broker_evidence WHERE kind='review_fee_sidecar_v1' LIMIT 2").fetchall()
            if len(rows) != 1:
                return unknown
            stamp, raw = rows[0]
            body = json.loads(raw)
            if (type(body.get('version')) is not int or body['version'] != 1
                    or body.get('main_uid') != self.expected_main_uid or body.get('environment') != 'demo'
                    or self.environment != 'demo' or body.get('endpoint') != 'https://api-demo.bybit.com'
                    or self.session.endpoint != body['endpoint'] or body.get('symbol') != 'BTCUSDT'
                    or type(body.get('captured_at')) not in (int, float)
                    or not math.isfinite(body['captured_at']) or stamp != body['captured_at']
                    or not 0 <= observed_at - stamp <= 86400):
                return unknown
            fees = body['evidence']
            verified_taker_rate(fees, stamp)
            verified_taker_rate(fees, observed_at)
            return fees
        except Exception:
            return unknown

    def _risk_accounting(self,observed,active,children):
        result=self._accounting(observed,active,children)
        if result.get("status")!="confirmed":
            raise BrokerNotReady("scenario_financial_evidence_pending")
        return result

    def _daily(self,observed):
        from datetime import datetime, timezone, timedelta
        from live.scenario_accounting import update_daily_risk
        when=datetime.fromtimestamp(observed["captured_at"],timezone(timedelta(hours=9)))
        start=int(when.replace(hour=0,minute=0,second=0,microsecond=0).timestamp()*1000)
        tables={r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "llm_scenario_daily_accounting" in tables:
            saved=self.conn.execute("SELECT body FROM llm_scenario_daily_accounting WHERE id=1").fetchone()
            if saved:
                start=int(float(json.loads(saved[0])["captured_at"])*1000)
        end=int(observed["captured_at"]*1000)
        rows=read_evidence_pages(self._call,"get_transaction_log",accountType="UNIFIED",currency="USDT",
            startTime=start,endTime=end,limit=50)
        flows=[]
        complete=True
        # External cashflows only; never classify cashFlow from TRADE as deposit.
        neutral={"TRADE","SETTLEMENT","FEE","INTEREST"}
        external={"TRANSFER_IN","TRANSFER_OUT","DEPOSIT","WITHDRAW"}
        for r in rows:
            if not start<=int(r.get("transactionTime",-1))<=end:
                complete=False
                continue
            if r.get("type") in neutral:
                continue
            if r.get("type") not in external or r.get("currency")!="USDT":
                complete=False
                continue
            amount=_number(r.get("change"))
            if (r["type"] in {"TRANSFER_IN","DEPOSIT"} and amount<0) or (r["type"] in {"TRANSFER_OUT","WITHDRAW"} and amount>0):
                complete=False
            flows.append(dict(id=r["id"],timestamp=int(r["transactionTime"]),amount=amount,verified=True))
        return update_daily_risk(self.conn,observed,dict(start_ms=start,end_ms=end,complete=complete,flows=flows))

    def context(self):
        from live import scenario_management_quarantine as quarantine
        quarantine_ready = True
        try:
            quarantine.refresh_known(self)
        except Exception:
            quarantine_ready = False
        observed=self.capture_account()
        active=self._active()
        from live.scenario_recovery import risk_fraction
        fraction = risk_fraction(self._recovery_state(), active["scenario_id"] if active else None)
        children=self.children(active["scenario_id"]) if active else []
        from live.scenario_entry_thesis import entry_thesis
        initial_entry_evidence = entry_thesis(self.conn, active, children, observed)
        pending=[self._pending_entry(c)
            for c in children if c["kind"]=="entry" and c["status"]=="LIVE" and c["evidence"]]
        daily=self._daily(observed)
        instrument=self._instrument()
        accounting=dict(positions=[],realized_loss=0,fees_paid=0,funding_paid=0)
        flat_fees = self._flat_fee_evidence(observed['captured_at']) if not active and observed.get('exchange_flat') is True else dict(status='UNKNOWN')
        financial_ok=True
        if active and children:
            try:
                accounting=self._risk_accounting(observed,active,children)
            except Exception:
                financial_ok=False
                size=float(observed["position"]["size"])
                accounting=dict(positions=[dict(price=float(observed["position"]["avgPrice"]),quantity=size)] if size else [],
                    realized_loss=None,fees_paid=None,funding_paid=None)
        protection=not float(observed["position"]["size"]) or bool(active and self._verify_protection(observed,active["hard_stop"],active["side"]))
        management = None
        management_block = None
        if quarantine_ready:
            try:
                management = quarantine.project(self, observed, active, self.children(active['scenario_id']) if active else [])
            except Exception:
                management = None  # Invalid authority never changes the ordinary UNKNOWN fence.
        if management is None:
            try:
                management_block = quarantine.projection_failure(self, observed)
            except Exception:
                management_block = 'INVALID'
        if management:
            financial_ok = False  # Administrative permission is not accounting evidence.
        from live.scenario_runner_economics import runner_economics
        review_finance = runner_economics(accounting=accounting,
            side=active["side"] if active else None, mark_price=observed["mark_price"],
            hard_stop=active["hard_stop"] if active else None,
            observed_at=observed["captured_at"], now=self.clock(), pending_entries=pending,
            initial_equity=active["initial_equity"] if active else observed["equity"],
            estimated_cost_rate=.002, slippage_bps=20, price_tick=instrument["tick"])
        from core.scenario_stop_policy import build_stop_policy
        from live.scenario_recovery import managed_permit
        state = self._recovery_state()
        breaker = state.get("breaker", {})
        from live.scenario_immediate_reduce import context as reduction_context
        reduction_status = reduction_context(self, active)
        stop_policy = build_stop_policy(active,
            hard_blocked=not self._new_risk_enabled() or not daily.get("new_risk_allowed", False),
            permit_valid=not breaker.get("blocked", False) or bool(
                set(breaker.get("reasons", [])) == {"three_losses"} and managed_permit(state, active)),
            accounting_status="confirmed" if financial_ok else "pending", protection_ok=protection,
            legacy_fenced=observed["legacy_fenced"], pending_entries=pending, positions=accounting["positions"],
            unknown_intents=any(c["status"] == "UNKNOWN" or not c["evidence"] for c in children))
        from live.scenario_incident_epoch import new_risk_blocked, historical
        incident_blocked = new_risk_blocked(self)
        return dict(historical_unsettled=historical(self.conn), incident_epoch_blocked=incident_blocked,
            entry_thesis=initial_entry_evidence, stop_policy=stop_policy, scenario_risk_fraction=fraction, conditional_entry_version=1, account_version=observed["account_version"],legacy_fenced=observed["legacy_fenced"],
            **({'management_quarantine': management} if management else {}),
            **({'management_quarantine_block': management_block} if management_block else {}),
            **reduction_status,
            account_captured_at=observed["captured_at"], runner_economics=review_finance,
            observed_execution_fee_evidence=accounting.get("observed_execution_fee_evidence", flat_fees),
            recorded_scenario_net=accounting.get("net_pnl") if accounting.get("accounting_complete") is True else None,
            target_status=[dict(target_id=c["local_id"],kind=c["kind"],status=c["status"],
                intent_id=c["intent_id"],
                logical_target_id=c["local_id"].partition(":")[2] if ":" in c["local_id"] else None,
                price=c["request"].get("price",c["request"].get("triggerPrice")),
                filled_quantity=c["evidence"]["order"].get("cumExecQty") if c["evidence"] else None,
                remaining_quantity=c["evidence"]["order"].get("leavesQty") if c["evidence"] else None)
                for c in children if c["kind"] in {"tp","partial_sl"}],
            accounting_status="confirmed" if financial_ok else "pending",
            execution_price_policy={"version": POLICY_VERSION},
            limit_price_quotes=observed_quotes(observed["ticker"], observed["captured_at"]),
            price_tick=float(instrument["tick"]),quantity_step=float(instrument["step"]),
            minimum_quantity=float(instrument["minimum"]),minimum_notional=float(instrument["notional"]),
            market_maximum_quantity=float(instrument["market_maximum"]),
            protection_ok=protection,new_risk_blocked=incident_blocked or not self._new_risk_enabled() or not financial_ok or not daily.get("new_risk_allowed",False),
            initial_equity=active["initial_equity"] if active else observed["equity"],mark_price=observed["mark_price"],
            previous_hard_stop=active["hard_stop"] if active else None,positions=accounting["positions"],pending_entries=pending,
            realized_loss=accounting["realized_loss"],fees_paid=accounting["fees_paid"],funding_paid=accounting["funding_paid"],
            estimated_cost_rate=.002,slippage_bps=20,
            **{k:daily[k] for k in ("day","day_start_equity","daily_net_pnl")})

    def _cleanup_flat_exits(self, active):
        """Cancel only owned partial exits after all possible entries ended.

        Full native stops are never cancelled here. The final account and exact
        child queries, not cancellation ACKs, remain settlement prerequisites.
        """
        with mutation_lock(self.conn):
            def verify_flat(child):
                current = self._active()
                observed = self.capture_account()
                children = self.children(active["scenario_id"])
                owned_exits = {(c["order_id"], c["link_id"]) for c in children
                    if c["kind"] in ("tp", "partial_sl") and c["status"] == "LIVE"
                    and c["request"].get("reduceOnly") is True}
                if (not self.execution_enabled or not current or
                        current["scenario_id"] != active["scenario_id"] or
                        not observed["exchange_flat"] or observed["legacy_fenced"] or
                        any(not c["evidence"] or c["status"] == "UNKNOWN" or
                            (c["kind"] not in ("tp", "partial_sl") and c["status"] != "TERMINAL")
                            for c in children)):
                    raise BrokerNotReady("flat_exit_cleanup_not_ready")
                # capture_account knows IDs from all historical scenarios.
                # Even a known foreign order can refill a flat account; only
                # this scenario's exact reduce-only partial exits may remain.
                if any((o.get("orderId"), o.get("orderLinkId")) not in owned_exits
                       or o.get("reduceOnly") is not True for o in observed["open_orders"]):
                    raise BrokerNotReady("flat_exit_cleanup_foreign_order")
                order = child["evidence"]["order"]
                if (child["scenario_id"] != active["scenario_id"] or
                        child["kind"] not in ("tp", "partial_sl") or
                        child["request"].get("reduceOnly") is not True or
                        order.get("reduceOnly") is not True or
                        order.get("orderId") != child["order_id"] or
                        order.get("orderLinkId") != child["link_id"]):
                    raise BrokerNotReady("flat_exit_cleanup_identity_conflict")

            for child in self.children(active["scenario_id"]):
                if child["kind"] in ("tp", "partial_sl") and child["status"] != "TERMINAL":
                    self._cancel(child, before_cancel=verify_flat)
            return self.capture_account()

    def reconcile(self):
        observed=self.capture_account()
        active=self._active()
        from live.scenario_provenance import bind
        bind(active.get("scenario_id") if active else None)
        if not active:
            if self.execution_enabled:
                self._daily(observed)
            flat_verified=bool(self.execution_enabled and
                observed["exchange_flat"] and not observed["open_orders"] and not observed["legacy_fenced"])
            from live.scenario_incident_epoch import monitor
            monitor(self)
            return dict(intents=[],settlement=None,observation=observed,notices=self._pending_notices(),
                protection_confirmed=flat_verified,
                protection_status="verified_flat_no_exposure" if flat_verified else "not_managed_by_scenario_adapter")
        # A crashed executor cannot still run while the shared mutation lock is
        # held. Unsubmitted children are abandoned, not silently retried.
        self.conn.execute("UPDATE llm_scenario_execution_batches SET status='INTERRUPTED' WHERE status='EXECUTING'")
        self.conn.commit()
        # Protection is attempted before unknown child recovery, not after it.
        protection=False
        if self.execution_enabled:
            try:
                observed=self._ensure_protection(observed,active["hard_stop"],active["side"])
                protection=True
            except Exception:
                protection=False
        unknown=False
        for child in self.children(active["scenario_id"]):
            try:
                self._query_child(child)
            except Exception:
                unknown=True
        observed=self.capture_account()
        if observed["legacy_fenced"] and not unknown:
            try:
                self._recover_parent_linked_native(active)
                observed=self.capture_account()
                if observed["legacy_fenced"]:
                    unknown=True
                    self._save("native_parent_recovery_blocked",dict(scenario_id=active["scenario_id"],
                        reason="position_change_without_exact_parent_evidence"))
            except Exception:
                unknown=True
                self._save("native_parent_recovery_blocked",dict(scenario_id=active["scenario_id"],
                    reason="native_parent_evidence_unverified"))
        try:
            daily=self._daily(observed)
            state=json.loads(self.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
            from live.scenario_incident_epoch import halted as incident_halted
            state['incident_halted'] = incident_halted(self.conn)
            from live.scenario_recovery import authorize_pending, managed_permit, normal_permission, risk_fraction
            from live.scenario_pending_renewal import load
            waiting_lease = load(self.conn, active)
            recovery_pending = authorize_pending(state, active, self.clock(),
                waiting_deadline=waiting_lease['until'] if waiting_lease else None)
            halted=state['incident_halted'] or not self._new_risk_enabled() or (state.get("breaker",{}).get("blocked",False) and not recovery_pending) or not daily.get("new_risk_allowed",False)
            permit = managed_permit(state, active)
            if permit:
                # Every managed stage retains its immutable scenario budget.
                # Only a verified NORMAL permit can retain later entry batches.
                if observed["legacy_fenced"] or unknown:
                    recovery_pending = False
                probe_children = self.children(active["scenario_id"])
                probe_accounting = dict(positions=[], realized_loss=0, fees_paid=0, funding_paid=0)
                if float(observed["position"]["size"]) or any(c["evidence"] and c["evidence"]["executions"] for c in probe_children):
                    probe_accounting = self._risk_accounting(observed, active, probe_children)
                from core.llm_scenario import risk_snapshot
                pending_risk = risk_snapshot(initial_equity=active["initial_equity"],
                    risk_fraction=risk_fraction(state, active["scenario_id"]), side=active["side"], hard_stop=active["hard_stop"],
                    positions=probe_accounting["positions"],
                    pending_entries=[self._pending_entry(c) for c in probe_children
                        if c["kind"] == "entry" and c["status"] == "LIVE" and c["evidence"]],
                    entries=[], realized_loss=probe_accounting["realized_loss"],
                    fees_paid=probe_accounting["fees_paid"], funding_paid=probe_accounting["funding_paid"],
                    estimated_cost_rate=.002, slippage_bps=20)
                if not pending_risk["within_budget"]:
                    recovery_pending = False
                originals = {r["id"]: r for r in permit.get("initial_entries", [])}
                normal = normal_permission(state, active, dict(
                    initial_equity=active["initial_equity"], legacy_fenced=observed["legacy_fenced"],
                    protection_ok=protection, accounting_status="confirmed",
                    new_risk_blocked=halted, recovery_hard_blocked=halted))
                for child in probe_children:
                    if child["kind"] != "entry" or child["status"] == "TERMINAL":
                        continue
                    if normal:
                        continue
                    original = originals.get(child["local_id"])
                    if (not original or child["intent_id"] != permit.get("action_id") or
                            decimal(child["request"]["price"]) != decimal(original["price"]) or
                            decimal(child["request"]["qty"]) > decimal(original["quantity"])):
                        recovery_pending = False
                halted = halted or not recovery_pending
        except Exception:
            halted=True
        expired=self.clock()>=active.get("expires_at",0)
        stop_hit=any(c["kind"]=="native_sl" and c["evidence"] and c["evidence"]["executions"] for c in self.children(active["scenario_id"]))
        if self.execution_enabled:
            for child in self.children(active["scenario_id"]):
                if child["status"] != "TERMINAL" and self._conditional_invalidated(child, observed, active):
                    try:
                        self._cancel(child)
                    except Exception:
                        unknown = True
        if self.execution_enabled and (halted or expired or not protection or stop_hit):
            for child in self.children(active["scenario_id"]):
                if child["kind"]=="entry" and child["status"]!="TERMINAL":
                    if expired and not (halted or not protection or stop_hit):
                        from live.scenario_pending_renewal import load, matching_child
                        waiting_lease = load(self.conn, active)
                        if (waiting_lease and self.clock() < waiting_lease['until']
                                and matching_child(child, waiting_lease['anchor'])):
                            continue
                    try:
                        self._cancel(child)
                    except Exception:
                        unknown=True
        latest=self.conn.execute("SELECT payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC LIMIT 1",(active["scenario_id"],)).fetchone()
        exiting=bool(latest and json.loads(latest[0])["action"]=="EXIT")
        reducing=bool(latest and json.loads(latest[0])["action"]=="REDUCE")
        if reducing and self.execution_enabled and not unknown:
            try:
                reduction = json.loads(latest[0])
                self._execute_reduce(reduction, reduction['action_id'])
            except Exception:
                unknown = True
        if self.execution_enabled and not exiting and not reducing and not (halted or expired or not protection or stop_hit or unknown):
            try:
                self._autochase(active,self.capture_account())
            except Exception:
                unknown=True
        observed=self.capture_account()
        for child in self.children(active["scenario_id"]):
            if child["kind"]=="native_sl" and not child["evidence"]:
                try:
                    self._query_child(child)
                except Exception:
                    unknown=True
        protection=self._verify_protection(observed,active["hard_stop"],active["side"])
        from live.scenario_management_quarantine import bound_exit
        quarantine_exit = bool(exiting and bound_exit(self, json.loads(latest[0])['action_id']))
        if exiting and self.execution_enabled and not stop_hit and (not unknown or quarantine_exit) and not observed["legacy_fenced"]:
            try:
                if self._continue_unsubmitted_exit(active):
                    observed=self.capture_account()
                    protection=self._verify_protection(observed,active["hard_stop"],active["side"])
            except Exception:
                unknown=True
        children=self.children(active["scenario_id"])
        if (stop_hit or not protection) and self.execution_enabled and not unknown and not observed["exchange_flat"] and not observed["legacy_fenced"]:
            # A fill racing the terminal hard stop must not restart the thesis.
            parent=self.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid LIMIT 1",(active["scenario_id"],)).fetchone()
            try:
                size=decimal(observed["position"]["size"])
                if size>self._instrument()["market_maximum"]:
                    raise BrokerNotReady("emergency_exit_split_required")
                emergency=[c for c in self.children(active["scenario_id"]) if c["kind"]=="exit" and c["local_id"].startswith("hard-stop-race:")]
                # One bounded residual attempt per reconciliation. Never issue
                # another until the prior IOC has exact terminal fill proof.
                pending_exit=any(c["kind"] in {"exit", "reduce"} and c["status"]!="TERMINAL"
                                 for c in self.children(active["scenario_id"]))
                if not pending_exit and (not emergency or emergency[-1]["status"]=="TERMINAL"):
                    self._submit(json.loads(parent[1]),parent[0],"exit","hard-stop-race:"+str(len(emergency)),
                        dict(side="Sell" if active["side"]=="LONG" else "Buy",orderType="Market",
                            qty=str(size),reduceOnly=True,closeOnTrigger=True))
            except Exception:
                unknown=True
        # GTC entries commonly fill after execute() returned. Synchronize the
        # most recent committed plan without invoking the LLM or adding risk.
        if protection and self.execution_enabled and not observed["exchange_flat"] and not stop_hit:
            plans=self.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid DESC",(active["scenario_id"],)).fetchall()
            for ident,body in plans:
                plan=json.loads(body)
                if plan["action"]=="EXIT":
                    break
                if plan["action"] == "REDUCE":
                    try:
                        self._execute_reduce(plan, ident)
                    except Exception:
                        unknown = True
                    break
                if plan["action"] in ("OPEN","ADJUST"):
                    if plan.get("stop_revision") and not self._stop_revision_evidence("stop_revision_confirmed", ident):
                        break
                    try:
                        self._sync_exits(plan,ident)
                    except Exception:
                        unknown=True
                    break
            children=self.children(active["scenario_id"])
        if (self.execution_enabled and not unknown and observed["exchange_flat"]
                and not observed["legacy_fenced"]
                and any(c["kind"] in ("tp", "partial_sl") and c["status"] != "TERMINAL" for c in children)
                and all(c["evidence"] and c["status"] != "UNKNOWN" and
                    (c["kind"] in ("tp", "partial_sl") or c["status"] == "TERMINAL")
                    for c in children)):
            try:
                observed = self._cleanup_flat_exits(active)
                protection = self._verify_protection(observed, active["hard_stop"], active["side"])
            except Exception:
                unknown = True
            children = self.children(active["scenario_id"])
        # Do not let historical REST delay any current protection, cancellation,
        # committed EXIT or owned-quantity emergency reduction above.
        from live.scenario_incident_epoch import monitor, halted as incident_halted
        monitor(self)
        if self.execution_enabled and incident_halted(self.conn):
            for child in self.children(active['scenario_id']):
                if child['kind'] == 'entry' and child['status'] != 'TERMINAL':
                    try:
                        self._cancel(child)
                    except Exception:
                        unknown = True
            # A cancellation can race a fill. Reconcile its exact evidence and
            # keep Full SL on the actual owned remainder, never reverse it.
            observed = self.capture_account()
            if not observed['exchange_flat']:
                try:
                    observed = self._ensure_protection(observed, active['hard_stop'], active['side'])
                except Exception:
                    unknown = True
            protection = self._verify_protection(observed, active['hard_stop'], active['side'])
            children = self.children(active['scenario_id'])
        items=[]
        for (ident,body) in self.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=?",(active["scenario_id"],)).fetchall():
            payload=json.loads(body)
            group=[c for c in children if c["intent_id"]==ident]
            if any(not c["evidence"] or c["status"]=="UNKNOWN" for c in group):
                continue
            if not group:
                if payload.get('pending_renewal'):
                    from live.scenario_pending_renewal import receipt, reject
                    proof = receipt(self.conn, ident)
                    if not proof and self.execution_enabled:
                        # Interrupted before local apply: retain old expiry, never
                        # infer an approval or submit/retry exchange work.
                        reject(self.conn, ident, 'EXECUTION_NOT_APPLIED', self.clock())
                        proof = receipt(self.conn, ident)
                    if not proof or proof.get('status') not in {'APPLIED', 'REJECTED'}:
                        continue
                cancelled=all(any(c["kind"]=="entry" and (c["local_id"]==entry or c["order_id"]==entry) and c["status"]=="TERMINAL" for c in children) for entry in payload.get("cancel_entry_ids",[]))
                adjusted=(payload["action"]=="ADJUST" and not payload.get("entries")
                    and (not payload.get("stop_revision") or self._stop_revision_evidence("stop_revision_confirmed", ident))
                    and self._verify_protection(observed,payload["hard_stop"],active["side"]))
                no_submit=payload["action"]=="OPEN" and observed["exchange_flat"] and not observed["open_orders"]
                batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(ident,)).fetchone()
                # Children are durable BEFORE any submit. An explicitly aborted
                # replacement with none cannot have placed its replacement; only
                # release it after exact old-order evidence proves flat/terminal.
                # Interrupted/crashed batches remain a distinct uncertainty.
                aborted_adjust=(payload["action"]=="ADJUST" and batch is not None and batch[0]=="ABORTED"
                    and not unknown and observed["exchange_flat"] and not observed["open_orders"]
                    and not observed["legacy_fenced"] and protection
                    and all(c["status"]=="TERMINAL" and c["evidence"] for c in children))
                reduced = False
                reduction_result = None
                if payload["action"] == "REDUCE":
                    from live.scenario_immediate_reduce import RESOLVED, result
                    reduction_result = result(self, ident)
                    reduced = reduction_result["status"] in RESOLVED
                terminal=cancelled and (reduced or payload["action"]=="WAIT" or adjusted or no_submit or aborted_adjust or (payload["action"]=="EXIT" and observed["exchange_flat"]))
                if terminal:
                    items.append(dict(intent_id=ident,terminal=True,protection_ok=protection,orders_reconciled=True,
                        executions_complete=True,execution_ids=[],filled_quantity=0.,exchange_order_ids=[],open_entries=[],
                        **({'reduction_result': reduction_result} if reduction_result else {}),
                        **({'pending_renewal_receipt': proof} if payload.get('pending_renewal') else {})))
                continue
            execution_ids=[e["execId"] for c in group for e in c["evidence"]["executions"]]
            open_entries=[self._pending_entry(c)
                for c in group if c["kind"]=="entry" and c["status"]=="LIVE"]
            all_children_submitted=all(any(c["kind"]=="entry" and c["local_id"]==r["id"] for c in group) for r in payload.get("entries",[]))
            batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(ident,)).fetchone()
            all_children_submitted=all_children_submitted or bool(batch and batch[0] in {"ABORTED","INTERRUPTED"})
            reduced = False
            reduction_result = None
            if payload["action"] == "REDUCE":
                from live.scenario_immediate_reduce import RESOLVED, result
                reduction_result = result(self, ident)
                reduced = reduction_result["status"] in RESOLVED
            terminal = reduced if payload['action'] == 'REDUCE' else all(c['status'] == 'TERMINAL' for c in group)
            items.append(dict(intent_id=ident,terminal=terminal and all_children_submitted,
                **({'reduction_result': reduction_result} if reduction_result else {}),
                protection_ok=protection,orders_reconciled=not unknown and all_children_submitted,executions_complete=not unknown,
                execution_ids=execution_ids,filled_quantity=sum(float(e["execQty"]) for c in group for e in c["evidence"]["executions"]),
                exchange_order_ids=[c["order_id"] for c in group],open_entries=open_entries,
                confirmed_hard_stop=float(observed["position"].get("stopLoss",0)) if protection and not observed["exchange_flat"] else None))
        settlement=None
        accounting=None
        financial_pending=False
        if not children and observed["exchange_flat"] and not observed["open_orders"]:
            settlement=dict(scenario_id=active["scenario_id"],flat_confirmed=True,orders_terminal=True,
                executions_complete=True,fees_complete=True,funding_complete=True,no_fills_confirmed=True,
                gross_pnl=0.,fees=0.,funding_net=0.,net_pnl=0.,execution_ids=[])
        elif not unknown:
            try:
                accounting=self._accounting(observed,active,children)
                settlement=accounting.get("settlement")
                financial_pending=accounting.get("status")!="confirmed"
            except Exception:
                financial_pending=True
        # A failed weakening must not permanently block future WAIT/EXIT. Only
        # release its intent after restoring the prior stored stronger Full SL
        # and independently reconciling all fills, fees and remaining orders.
        if (protection and not unknown and not financial_pending and accounting
                and accounting.get("status") == "confirmed" and not observed["legacy_fenced"]
                and all(c["evidence"] and c["status"] != "UNKNOWN" for c in children)
                and not any(c["kind"] == "entry" and c["status"] != "TERMINAL" for c in children)):
            from core.scenario_stop_policy import is_widening
            for ident, body, batch_status in self.conn.execute(
                    "SELECT i.id,i.payload,b.status FROM llm_scenario_intents i "
                    "JOIN llm_scenario_execution_batches b ON b.intent_id=i.id "
                    "WHERE i.scenario_id=? AND i.status!='TERMINAL' "
                    "AND b.status IN ('ABORTED','INTERRUPTED','ABORTED_RESTORED')", (active["scenario_id"],)).fetchall():
                plan = json.loads(body)
                if (plan.get("action") != "ADJUST" or not plan.get("stop_revision")
                        or self._stop_revision_evidence("stop_revision_confirmed", ident)
                        or not is_widening(active["side"], plan["hard_stop"], active["hard_stop"])):
                    continue
                attempt = self._stop_revision_evidence("stop_revision_attempt", ident)
                previous = attempt["old_stop"] if attempt else active["hard_stop"]
                if not self._verify_protection(observed, previous, active["side"]):
                    continue
                if batch_status != "ABORTED_RESTORED":
                    self.conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)", (
                        "stop_revision_aborted_restored", self.clock(), json.dumps(dict(
                            intent_id=ident, scenario_id=active["scenario_id"], status="ABORTED_RESTORED",
                            requested_stop=plan["hard_stop"], restored_stop=observed["position"]["stopLoss"],
                            accounting_status="confirmed", captured_at=observed["captured_at"]), sort_keys=True)))
                    self.conn.execute("UPDATE llm_scenario_execution_batches SET status='ABORTED_RESTORED' WHERE intent_id=?", (ident,))
                if not any(item["intent_id"] == ident for item in items):
                    items.append(dict(intent_id=ident, terminal=True, protection_ok=True,
                        orders_reconciled=True, executions_complete=True, execution_ids=[],
                        filled_quantity=0., exchange_order_ids=[], open_entries=[],
                        stop_revision_status="ABORTED_RESTORED"))
        # Notification enrichment is optional. Its SQL and rendering failures
        # must never roll back verified execution/accounting or prevent the
        # runtime from receiving the reconciliation result.
        self.conn.commit()
        notices=[]
        notice_error=None
        notice_savepoint=False
        try:
            self.conn.execute("SAVEPOINT scenario_notice_capture")
            notice_savepoint=True
            notices=self._notices(active,children,observed,protection,settlement,unknown or not protection or financial_pending,accounting)
            self.conn.execute("RELEASE SAVEPOINT scenario_notice_capture")
            notice_savepoint=False
        except Exception:
            notices=[]
            notice_error="optional_notice_capture_failed"
            if notice_savepoint:
                try:
                    self.conn.execute("ROLLBACK TO SAVEPOINT scenario_notice_capture")
                    self.conn.execute("RELEASE SAVEPOINT scenario_notice_capture")
                except Exception:
                    # Core evidence was committed before the optional savepoint.
                    # Fall back to a full rollback rather than leave partial
                    # notification writes available to a later unrelated commit.
                    self.conn.rollback()
        return dict(intents=items,settlement=settlement,observation=observed,scenario_id=active["scenario_id"],notices=notices,
                    notice_error=notice_error,
                    protection_confirmed=protection,
                    confirmed_hard_stop=float(observed["position"].get("stopLoss",0)) if protection and not observed["exchange_flat"] else None,
                    protection_status="confirmed" if protection else "unknown")

    def _notices(self,active,children,observed,protected,settlement,pending=False,accounting=None):
        """Immutable first-observation events; poll time never changes event IDs."""
        from live.scenario_notice import render_notice, closed_account_snapshot
        from live.scenario_notice_evidence import position_snapshot, economic_fingerprint, number, entry_plan_reference
        from live.scenario_recovery import risk_fraction
        fraction = risk_fraction(self._recovery_state(), active["scenario_id"])
        snapshot=position_snapshot(active,children,observed,protected,pending,accounting,
                                   scenario_risk_fraction=fraction)
        if snapshot and snapshot["quantity"] == 0 and closed_account_snapshot(snapshot, observed["captured_at"]) is None:
            snapshot=None  # Invalid optional flat account data must not block settlement notices.
        previous=self.conn.execute("SELECT revision,body FROM llm_scenario_notice_positions WHERE scenario_id=?",(active["scenario_id"],)).fetchone()
        before=json.loads(previous[1]) if previous else None
        fresh_rich_comparison=False
        fills=[(c,e) for c in children if c["evidence"] for e in c["evidence"]["executions"]]
        fills.sort(key=lambda item:(float(item[1]["execTime"]),item[1]["execId"]))
        fills_since_baseline=[e for _,e in fills if before is not None
            and before["timestamp"]<=float(e["execTime"])/1000<=observed["captured_at"]]
        entry_fills=[e for c,e in fills if c["kind"]=="entry"]
        entry_qty=sum(float(e["execQty"]) for e in entry_fills)
        avg=sum(float(e["execQty"])*float(e["execPrice"]) for e in entry_fills)/entry_qty if entry_qty else None
        entry_at=min((float(e["execTime"])/1000 for e in entry_fills),default=None)
        first_entry=entry_fills[0] if entry_fills else None
        candidates=[]
        incident=self.conn.execute("SELECT episode,active FROM llm_scenario_broker_incidents WHERE scenario_id=?",(active["scenario_id"],)).fetchone()
        episode,was_pending=incident if incident else (0,0)
        if pending and not was_pending:
            episode+=1
            self.conn.execute("INSERT OR REPLACE INTO llm_scenario_broker_incidents VALUES(?,?,1)",(active["scenario_id"],episode))
            candidates.append(dict(event_id=f'scenario-unknown-{active["scenario_id"]}-{episode}',kind="PENDING",timestamp=observed["captured_at"]))
        elif not pending and was_pending and (not observed["exchange_flat"] or settlement):
            self.conn.execute("UPDATE llm_scenario_broker_incidents SET active=0 WHERE scenario_id=?",(active["scenario_id"],))
            candidates.append(dict(event_id=f'scenario-recovered-{active["scenario_id"]}-{episode}',kind="RESOLVED",timestamp=observed["captured_at"],
                resolution_confirmed=True,resolution="FILLED_PROTECTED" if not observed["exchange_flat"] else
                    ("FLAT_SETTLED" if settlement.get("execution_ids") else "CANCELLED_UNFILLED")))
        for child,fill in fills:
            event_id="scenario-fill-"+fill["execId"]
            if self.conn.execute("SELECT 1 FROM llm_scenario_broker_notices WHERE event_id=?",(event_id,)).fetchone():
                continue  # Immutable old events do not need current-position enrichment.
            if child["kind"]!="entry" and settlement and settlement.get("execution_ids"):
                continue  # One CLOSED summary covers newly observed final exits.
            fresh_position=not observed["exchange_flat"] and 0<=observed["captured_at"]-float(fill["execTime"])/1000<=120
            event=dict(event_id=event_id,kind="FILLED" if child["kind"]=="entry" else "PARTIAL",
                timestamp=float(fill["execTime"])/1000,side=active["side"],fill_confirmed=True,
                price=float(fill["execPrice"]),quantity=float(fill["execQty"]),
                entry_price=avg if child["kind"]=="entry" else None,entry_timestamp=entry_at,
                hard_stop=number(observed["position"].get("stopLoss"),positive=True) if fresh_position else None,
                protection_confirmed=protected and fresh_position,
                exchange_leverage=number(observed["position"].get("leverage"),positive=True) if fresh_position else None,
                scenario_budget=active["initial_equity"]*fraction,
                scenario_initial_equity=active["initial_equity"],settlement_confirmed=False)
            if child["kind"] == "entry":
                event.update(entry_plan_reference(self.conn, active, child))
            if (child["kind"]!="entry" and accounting and accounting.get("status")=="confirmed"
                    and accounting.get("accounting_complete") is True):
                # Exact validated transaction cash flow, not average-entry math
                # or a claim of cost-inclusive per-fill settlement.
                gross_by_execution=accounting.get("execution_gross_pnl")
                value=gross_by_execution.get(fill["execId"]) if isinstance(gross_by_execution,dict) else None
                if isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value):
                    event["fill_gross_pnl"]=value
                    event["fill_gross_pnl_confirmed"]=True
            if child["kind"]=="entry" and not pending:
                # More fills belong to this same scenario, not a fresh cycle.
                # Unknown earlier evidence cannot establish the first entry.
                event["entry_stage"]="initial" if fill["execId"]==first_entry["execId"] else "additional"
            if snapshot and 0<=observed["captured_at"]-event["timestamp"]<=120:
                event.update(position_after=snapshot,position_snapshot_scope="post_observation_total",
                    remaining_quantity=snapshot["quantity"],account_snapshot=snapshot["account_snapshot"])
                # A reconciliation-wide baseline is not a per-fill position.
                # Never present a later observation as preceding an older fill.
                valid_before=before is not None and before["timestamp"]<=event["timestamp"]
                if valid_before and len(fills_since_baseline)==1:
                    event["position_before"]=before
                if (before is None or valid_before) and not self.conn.execute(
                    "SELECT 1 FROM llm_scenario_broker_notices WHERE event_id=?",(event["event_id"],)).fetchone():
                    fresh_rich_comparison=True
            if child["kind"] in {"tp","partial_sl","native_sl", "reduce"}:
                event["reason_code"]={"tp":"TAKE_PROFIT","partial_sl":"PARTIAL_STOP","native_sl":"HARD_STOP", "reduce":"IMMEDIATE_REDUCTION"}[child["kind"]]
            candidates.append(event)
        changed=bool(snapshot and (before is None or economic_fingerprint(snapshot)!=economic_fingerprint(before)))
        # First observation is a silent comparison baseline, not a new trade or
        # protection change. Only a verified later economic change is public.
        if changed and before is not None and not observed["exchange_flat"] and not fresh_rich_comparison:
            stop=float(observed["position"]["stopLoss"])
            revision=previous[0]+1 if previous else 1
            candidates.append(dict(event_id="scenario-position-"+hashlib.sha256(f'{active["scenario_id"]}:{revision}'.encode()).hexdigest()[:24],
                kind="PROTECTION",timestamp=observed["captured_at"],side=active["side"],hard_stop=stop,
                entry_timestamp=entry_at,
                protection_confirmed=True,exchange_leverage=float(observed["position"]["leverage"]),remaining_quantity=float(observed["position"]["size"]),
                position_before=before,position_after=snapshot,change_type="updated"))
        if settlement and settlement.get("execution_ids"):
            exit_fills=[e for c,e in fills if c["kind"]!="entry"]
            exit_qty=sum(float(e["execQty"]) for e in exit_fills)
            candidates.append(dict(event_id="scenario-closed-"+active["scenario_id"],kind="CLOSED",
                timestamp=max(float(e["execTime"])/1000 for e in exit_fills),quantity=exit_qty,
                entry_price=avg,entry_timestamp=entry_at,price=sum(float(e["execQty"])*float(e["execPrice"]) for e in exit_fills)/exit_qty,
                settlement_confirmed=True,flat_confirmed=True,orders_terminal=True,net_pnl=settlement["net_pnl"],
                scenario_initial_equity=active["initial_equity"],
                fees=settlement["fees"],funding=settlement["funding_net"]))
            if observed["exchange_flat"] and closed_account_snapshot(snapshot,candidates[-1]["timestamp"]) is not None:
                candidates[-1]["position_after"]=snapshot
        elif settlement and settlement.get("no_fills_confirmed"):
            candidates.append(dict(event_id="scenario-unfilled-"+active["scenario_id"],kind="RESOLVED",
                timestamp=observed["captured_at"],resolution_confirmed=True,resolution="CANCELLED_UNFILLED"))
        for event in candidates:
            render_notice(event)
            self.conn.execute("INSERT OR IGNORE INTO llm_scenario_broker_notices VALUES(?,?,?)",
                (event["event_id"],active["scenario_id"],json.dumps(event,allow_nan=False,sort_keys=True)))
        if changed:
            self.conn.execute("INSERT OR REPLACE INTO llm_scenario_notice_positions VALUES(?,?,?)",
                (active["scenario_id"],previous[0]+1 if previous else 1,json.dumps(snapshot,allow_nan=False,sort_keys=True)))
        return self._pending_notices()

    def _pending_notices(self):
        tables={r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "llm_scenario_outbox" in tables:
            return [json.loads(r[0]) for r in self.conn.execute("SELECT n.body FROM llm_scenario_broker_notices n WHERE NOT EXISTS (SELECT 1 FROM llm_scenario_outbox o WHERE o.event_id=n.event_id) ORDER BY n.rowid LIMIT 100")]
        return [json.loads(r[0]) for r in self.conn.execute("SELECT body FROM llm_scenario_broker_notices ORDER BY rowid LIMIT 100")]
