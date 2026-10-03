"""MAIN-demo exchange evidence boundary. Execution remains intentionally disabled.

This adapter can read actual Bybit account and paginated financial evidence. It
does NOT implement the child-order execution/protection state machine yet and
therefore advertises no runtime execution capabilities. Empty funding responses
never mean settled zero funding. Never bypass this gate to reuse legacy orders.

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


class ScenarioDemoBroker:
    environment = "demo"
    lane = "MAIN"
    capabilities = frozenset()
    execution_blockers = (
        "child_intent_submit_recovery_not_implemented",
        "partial_fill_protection_and_tp_sl_replacement_not_verified",
        "funding_schedule_coverage_and_finality_unverified",
        "kst_daily_account_net_pnl_baseline_not_reconciled",
        "fresh_pre_submit_margin_and_adverse_price_gate_not_implemented",
    )

    def __init__(self, conn, *, session=None, expected_main_uid, clock=time.time):
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
            conn.commit()

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
                "SELECT 1 FROM btc_positions WHERE mode LIKE '%demo%' AND qty>0 LIMIT 1").fetchone():
            return True
        # Existing unknown reservations are not adopted as this scenario's orders.
        if "entry_reservations" in tables:
            # Schema versions vary: presence is conservatively fenced until the
            # authoritative reservation store can reconcile each local record.
            if self.conn.execute("SELECT 1 FROM entry_reservations LIMIT 1").fetchone():
                return True
        if "btc_meta" in tables:
            for (body,) in self.conn.execute("SELECT value FROM btc_meta WHERE mode LIKE '%demo%' AND key LIKE '%pending%'"):
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
                execution_blockers=list(self.execution_blockers), captured_at=self.clock())
            economic = dict(position=position, orders=orders["result"]["list"], equity=equity, mark_price=mark)
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

    def reconcile(self):
        observed = self.capture_account()
        return dict(intents=[], settlement=None, observation=observed,
                    protection_status="not_managed_by_scenario_adapter")

    def context(self):
        raise BrokerNotReady("execution_context_incomplete_use_capture_account_for_observation")

    def execute(self, payload, intent_id):
        raise BrokerNotReady("scenario_execution_not_implemented")
