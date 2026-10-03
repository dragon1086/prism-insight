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

from live.exchange_snapshot import read_complete
from live.liquidation_guard import capture as capture_liquidation
from live.shared_entry_coordinator import database_path, mutation_lock
from live.scenario_execution import ScenarioExecution, decimal


class BrokerNotReady(ValueError):
    """Sanitized fail-closed reason, without transport payload or credentials."""


def _number(value, *, minimum=None):
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise BrokerNotReady("numeric_evidence_missing") from None
    if isinstance(value, bool) or not math.isfinite(result) or (minimum is not None and result < minimum):
        raise BrokerNotReady("numeric_evidence_invalid")
    return result


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

    def __init__(self, conn, *, session=None, expected_main_uid, clock=time.time, execution_enabled=False):
        database_path(conn)
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
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_broker_notices(event_id TEXT PRIMARY KEY,scenario_id TEXT NOT NULL,body TEXT NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_broker_incidents(scenario_id TEXT PRIMARY KEY,episode INTEGER NOT NULL,active INTEGER NOT NULL)")
            conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_notice_positions(scenario_id TEXT PRIMARY KEY,revision INTEGER NOT NULL,body TEXT NOT NULL)")
            conn.commit()
            self._init_execution(execution_enabled)
            if self.execution_enabled:
                from live.scenario_runtime import REQUIRED_CAPABILITIES
                self.capabilities=REQUIRED_CAPABILITIES
                self.execution_blockers=()

    def _call(self, method, **params):
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
        return response

    def _identity(self):
        identity = self._call("get_api_key_information").get("result", {}).get("userID")
        if str(identity) != self.expected_main_uid:
            raise BrokerNotReady("main_uid_mismatch")

    def _save(self, kind, body):
        self.conn.execute("INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)",
                          (kind, self.clock(), json.dumps(body, allow_nan=False, sort_keys=True)))
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
            tickers = self._call("get_tickers", category="linear", symbol="BTCUSDT").get("result", {}).get("list")
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
            trade_rows, funding, unknown = {}, [], []
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
                    funding.append(dict(transaction_id=row["id"], timestamp=when,
                        funding_net=_number(row.get("funding")), raw=row))
                else:
                    unknown.append(row["id"])
            trades = []
            for row in executions:
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
        return dict(start_ms=start_ms,end_ms=end_ms,complete=True,events=events)

    def _accounting(self,observed,active,children):
        from live.scenario_accounting import reconcile_scenario
        start=int(active["created_at"]*1000)
        end=int(self.clock()*1000)
        if end<=start:
            end=start+1
        evidence=self.capture_financial_evidence(start,end)
        owned=[]
        for c in children:
            if not c.get("evidence"):
                raise BrokerNotReady("child_accounting_evidence_missing")
            owned.append(dict(order_id=c["order_id"],role="entry" if c["kind"]=="entry" else "exit",
                side=c["request"]["side"],cumulative_qty=float(c["evidence"]["order"]["cumExecQty"]),terminal=c["status"]=="TERMINAL"))
        result=reconcile_scenario(active["scenario_id"],evidence,owned,observed,
            funding_schedule=self._funding_schedule(start,end,observed))
        self._save("scenario_accounting",result)
        return result

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
        observed=self.capture_account()
        active=self._active()
        children=self.children(active["scenario_id"]) if active else []
        pending=[dict(id=c["local_id"],price=float(c["request"]["price"]),quantity=float(c["evidence"]["order"]["leavesQty"]))
            for c in children if c["kind"]=="entry" and c["status"]=="LIVE" and c["evidence"]]
        daily=self._daily(observed)
        instrument=self._instrument()
        accounting=dict(positions=[],realized_loss=0,fees_paid=0,funding_paid=0)
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
        return dict(account_version=observed["account_version"],legacy_fenced=observed["legacy_fenced"],
            target_status=[dict(target_id=c["local_id"],kind=c["kind"],status=c["status"],
                intent_id=c["intent_id"],
                logical_target_id=c["local_id"].partition(":")[2] if ":" in c["local_id"] else None,
                price=c["request"].get("price",c["request"].get("triggerPrice")),
                filled_quantity=c["evidence"]["order"].get("cumExecQty") if c["evidence"] else None,
                remaining_quantity=c["evidence"]["order"].get("leavesQty") if c["evidence"] else None)
                for c in children if c["kind"] in {"tp","partial_sl"}],
            accounting_status="confirmed" if financial_ok else "pending",
            price_tick=float(instrument["tick"]),quantity_step=float(instrument["step"]),
            minimum_quantity=float(instrument["minimum"]),minimum_notional=float(instrument["notional"]),
            protection_ok=protection,new_risk_blocked=not self._new_risk_enabled() or not financial_ok or not daily.get("new_risk_allowed",False),
            initial_equity=active["initial_equity"] if active else observed["equity"],mark_price=observed["mark_price"],
            previous_hard_stop=active["hard_stop"] if active else None,positions=accounting["positions"],pending_entries=pending,
            realized_loss=accounting["realized_loss"],fees_paid=accounting["fees_paid"],funding_paid=accounting["funding_paid"],
            estimated_cost_rate=.002,slippage_bps=20,
            **{k:daily[k] for k in ("day","day_start_equity","daily_net_pnl")})

    def reconcile(self):
        observed=self.capture_account()
        active=self._active()
        if not active:
            if self.execution_enabled:
                self._daily(observed)
            flat_verified=bool(self.execution_enabled and
                observed["exchange_flat"] and not observed["open_orders"] and not observed["legacy_fenced"])
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
        try:
            daily=self._daily(observed)
            state=json.loads(self.conn.execute("SELECT body FROM llm_scenario_state WHERE id=1").fetchone()[0])
            halted=not self._new_risk_enabled() or state.get("breaker",{}).get("blocked",False) or not daily.get("new_risk_allowed",False)
        except Exception:
            halted=True
        expired=self.clock()>=active.get("expires_at",0)
        stop_hit=any(c["kind"]=="native_sl" and c["evidence"] and c["evidence"]["executions"] for c in self.children(active["scenario_id"]))
        if self.execution_enabled and (halted or expired or not protection or stop_hit):
            for child in self.children(active["scenario_id"]):
                if child["kind"]=="entry" and child["status"]!="TERMINAL":
                    try:
                        self._cancel(child)
                    except Exception:
                        unknown=True
        if self.execution_enabled and not (halted or expired or not protection or stop_hit or unknown):
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
                if not emergency or emergency[-1]["status"]=="TERMINAL":
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
                if plan["action"] in ("OPEN","ADJUST"):
                    try:
                        self._sync_exits(plan,ident)
                    except Exception:
                        unknown=True
                    break
            children=self.children(active["scenario_id"])
        items=[]
        for (ident,body) in self.conn.execute("SELECT id,payload FROM llm_scenario_intents WHERE scenario_id=?",(active["scenario_id"],)).fetchall():
            payload=json.loads(body)
            group=[c for c in children if c["intent_id"]==ident]
            if any(not c["evidence"] or c["status"]=="UNKNOWN" for c in group):
                continue
            if not group:
                cancelled=all(any(c["kind"]=="entry" and (c["local_id"]==entry or c["order_id"]==entry) and c["status"]=="TERMINAL" for c in children) for entry in payload.get("cancel_entry_ids",[]))
                adjusted=payload["action"]=="ADJUST" and not payload.get("entries") and self._verify_protection(observed,payload["hard_stop"],active["side"])
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
                terminal=cancelled and (payload["action"]=="WAIT" or adjusted or no_submit or aborted_adjust or (payload["action"]=="EXIT" and observed["exchange_flat"]))
                if terminal:
                    items.append(dict(intent_id=ident,terminal=True,protection_ok=protection,orders_reconciled=True,
                        executions_complete=True,execution_ids=[],filled_quantity=0.,exchange_order_ids=[],open_entries=[]))
                continue
            execution_ids=[e["execId"] for c in group for e in c["evidence"]["executions"]]
            open_entries=[dict(id=c["local_id"],price=float(c["request"]["price"]),quantity=float(c["evidence"]["order"]["leavesQty"]))
                for c in group if c["kind"]=="entry" and c["status"]=="LIVE"]
            all_children_submitted=all(any(c["kind"]=="entry" and c["local_id"]==r["id"] for c in group) for r in payload.get("entries",[]))
            batch=self.conn.execute("SELECT status FROM llm_scenario_execution_batches WHERE intent_id=?",(ident,)).fetchone()
            all_children_submitted=all_children_submitted or bool(batch and batch[0] in {"ABORTED","INTERRUPTED"})
            items.append(dict(intent_id=ident,terminal=all(c["status"]=="TERMINAL" for c in group) and all_children_submitted,
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
        from live.scenario_notice import render_notice
        from live.scenario_notice_evidence import position_snapshot, economic_fingerprint
        snapshot=position_snapshot(active,children,observed,protected,pending,accounting)
        previous=self.conn.execute("SELECT revision,body FROM llm_scenario_notice_positions WHERE scenario_id=?",(active["scenario_id"],)).fetchone()
        before=json.loads(previous[1]) if previous else None
        fresh_rich_comparison=False
        fills=[(c,e) for c in children if c["evidence"] for e in c["evidence"]["executions"]]
        entry_fills=[e for c,e in fills if c["kind"]=="entry"]
        entry_qty=sum(float(e["execQty"]) for e in entry_fills)
        avg=sum(float(e["execQty"])*float(e["execPrice"]) for e in entry_fills)/entry_qty if entry_qty else None
        entry_at=min((float(e["execTime"])/1000 for e in entry_fills),default=None)
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
            event=dict(event_id="scenario-fill-"+fill["execId"],kind="FILLED" if child["kind"]=="entry" else "PARTIAL",
                timestamp=float(fill["execTime"])/1000,side=active["side"],fill_confirmed=True,
                price=float(fill["execPrice"]),quantity=float(fill["execQty"]),entry_price=avg,entry_timestamp=entry_at,
                hard_stop=float(observed["position"].get("stopLoss",0)) or active["hard_stop"],
                protection_confirmed=protected,exchange_leverage=float(observed["position"]["leverage"]),
                scenario_budget=active["initial_equity"]*.02,settlement_confirmed=False)
            if snapshot and 0<=observed["captured_at"]-event["timestamp"]<=120:
                event.update(position_after=snapshot,position_snapshot_scope="post_observation_total",
                    remaining_quantity=snapshot["quantity"],account_snapshot=snapshot["account_snapshot"])
                # A reconciliation-wide baseline is not a per-fill position.
                # Never present a later observation as preceding an older fill.
                valid_before=before is not None and before["timestamp"]<=event["timestamp"]
                if valid_before:
                    event["position_before"]=before
                if (before is None or valid_before) and not self.conn.execute(
                    "SELECT 1 FROM llm_scenario_broker_notices WHERE event_id=?",(event["event_id"],)).fetchone():
                    fresh_rich_comparison=True
            if child["kind"] in {"tp","partial_sl","native_sl"}:
                event["reason_code"]={"tp":"TAKE_PROFIT","partial_sl":"PARTIAL_STOP","native_sl":"HARD_STOP"}[child["kind"]]
            candidates.append(event)
        changed=bool(snapshot and (before is None or economic_fingerprint(snapshot)!=economic_fingerprint(before)))
        if changed and not observed["exchange_flat"] and not fresh_rich_comparison:
            stop=float(observed["position"]["stopLoss"])
            revision=previous[0]+1 if previous else 1
            candidates.append(dict(event_id="scenario-position-"+hashlib.sha256(f'{active["scenario_id"]}:{revision}'.encode()).hexdigest()[:24],
                kind="PROTECTION",timestamp=observed["captured_at"],side=active["side"],hard_stop=stop,
                protection_confirmed=True,exchange_leverage=float(observed["position"]["leverage"]),remaining_quantity=float(observed["position"]["size"]),
                position_before=before,position_after=snapshot,change_type="updated" if before else "initial_protection"))
        if settlement and settlement.get("execution_ids"):
            exit_fills=[e for c,e in fills if c["kind"]!="entry"]
            exit_qty=sum(float(e["execQty"]) for e in exit_fills)
            candidates.append(dict(event_id="scenario-closed-"+active["scenario_id"],kind="CLOSED",
                timestamp=max(float(e["execTime"])/1000 for e in exit_fills),quantity=exit_qty,
                entry_price=avg,entry_timestamp=entry_at,price=sum(float(e["execQty"])*float(e["execPrice"]) for e in exit_fills)/exit_qty,
                settlement_confirmed=True,flat_confirmed=True,orders_terminal=True,net_pnl=settlement["net_pnl"],
                fees=settlement["fees"],funding=settlement["funding_net"]))
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
