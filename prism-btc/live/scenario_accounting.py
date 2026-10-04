"""Exact owned-order financial reconciliation; missing evidence never means zero.

Pure settlement calculations plus a SQLite KST daily-risk ledger. All monetary
arithmetic uses Decimal. Callers must collect complete, identity-checked exchange
responses; this module neither queries the exchange nor submits orders.
"""
from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo


class AccountingPending(ValueError):
    pass


def _d(value):
    try:
        if value is None or isinstance(value, bool):
            raise ValueError
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise AccountingPending("numeric_evidence_missing") from None


def _require(condition, reason):
    if not condition:
        raise AccountingPending(reason)


def reconcile_scenario(scenario_id, evidence, owned_orders, snapshot, *, funding_schedule):
    """Reconcile cumulative fills from scenario start through the fresh snapshot.

    owned_orders: order_id, role(entry/exit), side, cumulative_qty, terminal.
    funding_schedule: complete, start_ms, end_ms, events(timestamp,rate).
    Empty events are valid ONLY with complete public schedule coverage.
    """
    try:
        return _reconcile(scenario_id, evidence, owned_orders, snapshot, funding_schedule)
    except (AccountingPending, KeyError, TypeError) as exc:
        return dict(status="pending", reasons=[str(exc)], settlement=None,
                    accounting_complete=False, new_risk_blocked=True,
                    realized_loss=None, fees_paid=None, funding_paid=None)


def _reconcile(scenario_id, evidence, owned_orders, snapshot, schedule):
    _require(evidence.get("response_pages_complete") is True, "financial_pages_incomplete")
    start, end = _d(evidence["start_ms"]), _d(evidence["end_ms"])
    _require(start < end, "financial_window_invalid")
    orders = {}
    for order in owned_orders:
        ident = order["order_id"]
        _require(isinstance(ident, str) and ident and ident not in orders, "order_identity_ambiguous")
        _require(order["role"] in ("entry", "exit") and order["side"] in ("Buy", "Sell"), "order_role_invalid")
        _require(type(order["terminal"]) is bool, "order_terminal_missing")
        orders[ident] = order
    _require(not evidence.get("unmatched_ids"), "financial_rows_unmatched")
    trades, seen = [], set()
    sums = {ident: Decimal(0) for ident in orders}
    gross = fees = losses = fee_debits = Decimal(0)
    for trade in evidence["trades"]:
        oid, eid = trade["order_id"], trade["execution_id"]
        _require(oid in orders, "unowned_execution")
        _require(isinstance(eid, str) and eid and eid not in seen, "execution_identity_ambiguous")
        seen.add(eid)
        raw, txn = trade["raw_execution"], trade["raw_transaction"]
        _require(raw.get("execId") == eid and raw.get("orderId") == oid and
                 txn.get("tradeId") == eid and txn.get("orderId") == oid and
                 raw.get("execType") == "Trade" and txn.get("type") == "TRADE" and
                 raw.get("symbol") == txn.get("symbol") == "BTCUSDT" and
                 txn.get("currency") == "USDT" and raw.get("side") == orders[oid]["side"],
                 "trade_identity_mismatch")
        qty, price, timestamp = _d(trade["quantity"]), _d(trade["price"]), _d(trade["timestamp"])
        fee, pnl = _d(trade["fee"]), _d(trade["gross_pnl"])
        _require(qty > 0 and price > 0 and start <= timestamp <= end, "trade_value_invalid")
        _require(qty == _d(raw.get("execQty")) and price == _d(raw.get("execPrice")) and
                 timestamp == _d(raw.get("execTime")) and fee == _d(raw.get("execFee")) == _d(txn.get("fee")) and
                 pnl == _d(txn.get("cashFlow")), "normalized_trade_mismatch")
        # TRADE rows must explicitly declare their funding component.
        _require(_d(txn.get("funding")) == 0, "trade_funding_unexpected")
        sums[oid] += qty
        gross += pnl
        fees += fee
        fee_debits += max(fee, Decimal(0))
        trades.append((timestamp, eid, oid, qty, price, pnl))
    for oid, order in orders.items():
        _require(sums[oid] == _d(order["cumulative_qty"]), "order_execution_coverage_mismatch")
    position = average = Decimal(0)
    timeline, lots = [], []
    for timestamp, eid, oid, qty, price, pnl in sorted(trades):
        order = orders[oid]
        signed = qty if order["side"] == "Buy" else -qty
        if order["role"] == "entry":
            _require(position == 0 or position * signed > 0, "entry_reverses_position")
            _require(pnl == 0, "entry_cashflow_unexpected")
            average = (abs(position) * average + qty * price) / (abs(position) + qty)
            lots.append([qty, price])
        else:
            _require(position * signed < 0 and qty <= abs(position), "exit_exceeds_owned_position")
            expected = (price-average) * qty * (1 if position > 0 else -1)
            _require(abs(expected-pnl) <= Decimal("0.00000001"), "realized_cashflow_mismatch")
            # Pro-rata lots preserve exchange average cost while preventing
            # winning lots from replenishing the losing-lot risk budget.
            proportion = qty / abs(position)
            for lot in lots:
                closed_qty = lot[0] * proportion
                lot_pnl = (price-lot[1]) * closed_qty * (1 if position > 0 else -1)
                losses += max(-lot_pnl, Decimal(0))
                lot[0] -= closed_qty
        position += signed
        timeline.append((timestamp, position))
    observed = snapshot["position"]
    _require(observed.get("symbol") == "BTCUSDT" and observed.get("positionIdx") == 0, "position_identity_invalid")
    size = _d(observed.get("size"))
    _require(size >= 0 and size == abs(position) and
             (size == 0 or observed.get("side") == ("Buy" if position > 0 else "Sell")), "position_quantity_mismatch")
    _require(schedule.get("complete") is True and _d(schedule["start_ms"]) <= start and
             _d(schedule["end_ms"]) >= end, "funding_schedule_coverage_missing")
    events, expected_funding = set(), {}
    for event in sorted(schedule["events"], key=lambda row: _d(row["timestamp"])):
        timestamp = _d(event["timestamp"])
        _require(timestamp not in events, "funding_schedule_duplicate")
        events.add(timestamp)
        if not start <= timestamp <= end:
            continue
        _require(all(t != timestamp for t, _ in timeline), "funding_execution_boundary_ambiguous")
        exposure = Decimal(0)
        for t, amount in timeline:
            if t < timestamp:
                exposure = amount
        if exposure:
            expected_funding[timestamp] = (exposure, _d(event["rate"]))
    funding_net = funding_paid = Decimal(0)
    funding_ids = set()
    for row in evidence["funding"]:
        raw = row["raw"]
        timestamp, amount = _d(row["timestamp"]), _d(row["funding_net"])
        ident = row["transaction_id"]
        _require(isinstance(ident, str) and ident and ident not in funding_ids and raw.get("id") == ident,
                 "funding_identity_ambiguous")
        funding_ids.add(ident)
        _require(raw.get("type") == "SETTLEMENT" and raw.get("symbol") == "BTCUSDT" and
                 raw.get("currency") == "USDT" and _d(raw.get("funding")) == amount and
                 _d(raw.get("transactionTime")) == timestamp and _d(raw.get("cashFlow")) == 0 and
                 _d(raw.get("fee")) == 0, "funding_transaction_mismatch")
        _require(timestamp in expected_funding, "funding_amount_or_coverage_mismatch")
        exposure, rate = expected_funding.pop(timestamp)
        expected_sign = -exposure * rate
        _require((expected_sign == 0 and amount == 0) or expected_sign * amount > 0,
                 "funding_sign_mismatch")
        if raw.get("qty") not in (None, ""):
            _require(abs(_d(raw["qty"])) == abs(exposure), "funding_quantity_mismatch")
        funding_net += amount
        funding_paid += max(-amount, Decimal(0))
    _require(not expected_funding, "funding_settlement_missing")
    result = dict(status="confirmed", reasons=[], accounting_complete=True, new_risk_blocked=False,
                  positions=[dict(price=float(price), quantity=float(qty)) for qty, price in lots if qty > 0],
                  realized_loss=float(losses), fees_paid=float(fee_debits), funding_paid=float(funding_paid),
                  gross_pnl=float(gross), fees=float(fees), funding_net=float(funding_net),
                  net_pnl=float(gross-fees+funding_net), settlement=None)
    # Optional notice metadata: reuse exact checked cashFlow, not a second PnL
    # calculation or an allocation of scenario fees/funding to individual fills.
    result["execution_gross_pnl"] = {eid: float(pnl) for _, eid, _, _, _, pnl in trades}
    if position == 0 and all(order["terminal"] for order in orders.values()) and snapshot.get("open_orders") == []:
        result["settlement"] = dict(scenario_id=scenario_id, flat_confirmed=True, orders_terminal=True,
            executions_complete=True, fees_complete=True, funding_complete=True, execution_ids=sorted(seen),
            no_fills_confirmed=not seen, **{key: result[key] for key in ("gross_pnl", "fees", "funding_net", "net_pnl")})
    return result


def update_daily_risk(conn, snapshot, flows_evidence):
    """Persist baseline at activation; never invent a missed midnight snapshot.

    A late rollover carries only the adverse cross-midnight observation interval
    (not the previous day's accumulated losses) into the new monitoring window.
    Prior-day totals are retained in an audit ledger. Verified external flows are
    removed from equity change. Missing flow proof freezes the previous sample
    and blocks new risk, without affecting protective order management.
    """
    conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_daily_accounting (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL)")
    conn.execute("CREATE TABLE IF NOT EXISTS llm_scenario_daily_accounting_history (day TEXT PRIMARY KEY, body TEXT NOT NULL)")
    row = conn.execute("SELECT body FROM llm_scenario_daily_accounting WHERE id=1").fetchone()
    state = json.loads(row[0]) if row else None
    try:
        now, equity = _d(snapshot["captured_at"]), _d(snapshot["equity"])
        _require(equity > 0 and now > 0, "daily_snapshot_invalid")
        local = datetime.fromtimestamp(float(now), ZoneInfo("Asia/Seoul"))
        day = local.date().isoformat()
        if state is None:
            state = dict(day=day, day_start_equity=str(equity), baseline_kind="activation",
                         monitored_from=str(now), captured_at=str(now), last_equity=str(equity),
                         daily_net_pnl="0", halted=False, reset_ids=[])
        else:
            old_time = _d(state["captured_at"])
            _require(now >= old_time, "daily_snapshot_time_reversed")
            _require(flows_evidence.get("complete") is True and
                     _d(flows_evidence["start_ms"]) <= old_time*1000 and
                     _d(flows_evidence["end_ms"]) >= now*1000, "daily_flow_coverage_missing")
            flow_sum, ids = Decimal(0), set()
            for flow in flows_evidence["flows"]:
                ident = flow["id"]
                _require(isinstance(ident, str) and ident and ident not in ids and flow.get("verified") is True,
                         "daily_flow_identity_unverified")
                ids.add(ident)
                timestamp = _d(flow["timestamp"])
                _require(old_time*1000 <= timestamp <= now*1000, "daily_flow_outside_window")
                if timestamp > old_time*1000:
                    flow_sum += _d(flow["amount"])
            interval_pnl = equity-_d(state["last_equity"])-flow_sum
            pnl = _d(state["daily_net_pnl"]) + interval_pnl
            if pnl <= -_d(state["day_start_equity"])*Decimal("0.04"):
                state["halted"] = True
            if state["day"] != day:
                exact_midnight = local.hour == local.minute == local.second == local.microsecond == 0
                outgoing = dict(state, outgoing_net_pnl=str(pnl), closed_at=str(now),
                                cross_midnight_interval_pnl=str(interval_pnl),
                                boundary_observation_exact=exact_midnight)
                conn.execute("INSERT INTO llm_scenario_daily_accounting_history VALUES(?,?)",
                             (state["day"], json.dumps(outgoing)))
                state.update(day=day, day_start_equity=str(equity), monitored_from=str(now),
                             baseline_kind="midnight" if exact_midnight else "late_rollover_carry_loss")
                pnl = Decimal(0) if exact_midnight else min(interval_pnl, Decimal(0))
            state.update(captured_at=str(now), last_equity=str(equity), daily_net_pnl=str(pnl))
        if _d(state["daily_net_pnl"]) <= -_d(state["day_start_equity"])*Decimal("0.04"):
            state["halted"] = True
        conn.execute("INSERT OR REPLACE INTO llm_scenario_daily_accounting VALUES(1,?)", (json.dumps(state),))
        conn.commit()
        return dict(day=state["day"], day_start_equity=float(state["day_start_equity"]),
                    daily_net_pnl=float(state["daily_net_pnl"]), daily_proof_complete=True,
                    new_risk_allowed=not state["halted"], baseline_kind=state["baseline_kind"],
                    monitored_from=float(state["monitored_from"]))
    except (AccountingPending, KeyError, TypeError) as exc:
        return dict(daily_proof_complete=False, new_risk_allowed=False, reason=str(exc))


def authorize_manual_reset(conn, *, authorization_id, operator, reason, reviewed_at, authorized=False):
    """Audit an explicit operator reset; never erase losses or rebase equity."""
    _require(authorized is True and all(isinstance(x, str) and x.strip() for x in
             (authorization_id, operator, reason)), "explicit_reset_authorization_required")
    row = conn.execute("SELECT body FROM llm_scenario_daily_accounting WHERE id=1").fetchone()
    _require(row is not None, "daily_baseline_missing")
    state = json.loads(row[0])
    _require(_d(reviewed_at) >= _d(state["captured_at"]), "reset_review_stale")
    _require(authorization_id not in state["reset_ids"], "reset_authorization_reused")
    _require(_d(state["daily_net_pnl"]) > -_d(state["day_start_equity"])*Decimal("0.04"), "daily_loss_limit_still_exceeded")
    state["halted"] = False
    state["reset_ids"].append(authorization_id)
    state["last_reset"] = dict(operator=operator, reason=reason, reviewed_at=str(reviewed_at), authorization_id=authorization_id)
    conn.execute("UPDATE llm_scenario_daily_accounting SET body=? WHERE id=1", (json.dumps(state),))
    conn.commit()
    return dict(authorized=True, authorization_id=authorization_id)
