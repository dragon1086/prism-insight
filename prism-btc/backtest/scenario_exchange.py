"""Deterministic, network-free Bybit-shaped session for the real scenario broker.

Prices are point events, never retrospectively applied to earlier orders. The
caller supplies a shared event capacity in integer quantity lots, computed from
previously closed volume (not the current candle's eventual volume). OHLC/OLHC
paths remain assumptions; omitted mark prices explicitly select a Last proxy.
This is a linear one-way execution model, not a liquidation/queue reconstruction.
"""
from __future__ import annotations

import copy
import hashlib
import heapq
import json
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR


def _d(value):
    if isinstance(value, bool):
        raise ValueError("boolean numeric input")
    value = Decimal(str(value))
    if not value.is_finite():
        raise ValueError("non-finite numeric input")
    return value


def _s(value):
    return format(_d(value), "f")


def _money(value):
    # Stable exchange-style USDT precision also survives broker float parsing.
    return value.quantize(Decimal('.00000001'))


def _reply(rows=None, **fields):
    return dict(retCode=0, result=copy.deepcopy(
        dict(list=rows, nextPageCursor="") if rows is not None else fields))


class ScenarioExchange:
    """Local-only session: all mutations queue ACK latency; reads never advance time."""

    simulation = True
    endpoint = "https://api-demo.bybit.com"  # broker identity guard; no HTTP client exists
    LIVE = {"New", "PartiallyFilled", "Untriggered", "Triggered"}

    def __init__(self, *, initial_equity=10000, start_ms=0, price=100,
                 uid="42", maker_fee=.0002, taker_fee=.00055,
                 slippage_bps=5, spread=.0001, latency_ms=100, entry_latency_ms=None,
                 cancel_latency_ms=None, amend_latency_ms=None,
                 market_latency_ms=None, quantity_step=.001, price_tick=.1,
                 funding_schedule=(), funding_interval_ms=28_800_000,
                 funding_events=None, config=None):
        if config is not None:
            maker_fee, taker_fee = config.maker_fee, config.taker_fee
            slippage_bps = config.slippage * 10000
            spread = config.spread
            entry_latency_ms, cancel_latency_ms = config.entry_latency_ms, config.cancel_latency_ms
            amend_latency_ms, market_latency_ms = config.amend_latency_ms, config.market_latency_ms
        if funding_events is not None:
            funding_schedule = [(r["timestamp"], r["rate"]) if isinstance(r, dict) else r for r in funding_events]
        if type(start_ms) is not int or start_ms < 0:
            raise ValueError("invalid start timestamp")
        self.ts_ms = start_ms
        self.price = self.mark_price = _d(price)
        self.cash = self.initial_equity = _d(initial_equity)
        self.step, self.tick = _d(quantity_step), _d(price_tick)
        if min(self.cash, self.price, self.step, self.tick) <= 0:
            raise ValueError("positive equity, price, and instrument precision required")
        self.uid = str(uid)
        self.maker_fee, self.taker_fee = _d(maker_fee), _d(taker_fee)
        self.slippage = _d(slippage_bps) / 10000
        self.spread = _d(spread)
        if not (0 <= self.maker_fee < 1 and 0 <= self.taker_fee < 1 and 0 <= self.slippage < 1):
            raise ValueError("invalid execution costs")
        if not 0 <= self.spread < 1 or self.slippage + self.spread / 2 >= 1:
            raise ValueError("invalid spread")
        self.latencies = dict(entry=entry_latency_ms or latency_ms,
                              cancel=cancel_latency_ms or latency_ms,
                              amend=amend_latency_ms or latency_ms,
                              market=market_latency_ms or latency_ms)
        # An explicit zero must not silently fall back to a realistic latency.
        for item in (latency_ms, entry_latency_ms, cancel_latency_ms, amend_latency_ms, market_latency_ms):
            if item is not None and (type(item) is not int or item <= 0):
                raise ValueError("positive integer latency required")
        if type(funding_interval_ms) is not int or funding_interval_ms <= 0:
            raise ValueError("invalid funding interval")
        self.funding_interval_ms = funding_interval_ms
        self.funding_schedule = sorted((int(t), _d(r)) for t, r in funding_schedule)
        if len({t for t, _ in self.funding_schedule}) != len(self.funding_schedule):
            raise ValueError("duplicate funding timestamp")
        self.orders, self.executions, self.transactions, self.writes = {}, [], [], []
        self.position = Decimal(0)
        self.average = Decimal(0)
        self.stop = Decimal(0)
        self._native_id = None
        self._queue, self._seq, self._oid = [], 0, 0
        self.mark_proxy_used = False
        self.funding_price_proxy_used = False
        self.capacity_used_lots = 0

    def now(self):
        return self.ts_ms / 1000

    clock = now

    def elapse(self, ts_ms):
        """Advance timers without inventing a new executable market observation."""
        if ts_ms == self.ts_ms:
            return 0
        if any(self.ts_ms < t <= ts_ms for t, _ in self.funding_schedule):
            self.funding_price_proxy_used = True
        return self.advance(ts_ms, self.price, self.mark_price, 0, _match=False)

    set_time = elapse

    @property
    def equity(self):
        return self.cash + self.position * (self.mark_price - self.average)

    def _id(self):
        self._oid += 1
        return f"sim-order-{self._oid:08d}"

    def _enqueue(self, kind, data, latency):
        self._seq += 1
        heapq.heappush(self._queue, (self.ts_ms + latency, self._seq, kind, copy.deepcopy(data)))

    def _find(self, params):
        rows = [o for o in self.orders.values()
                if (not params.get("orderId") or o["orderId"] == params["orderId"])
                and (not params.get("orderLinkId") or o["orderLinkId"] == params["orderLinkId"])]
        if len(rows) != 1:
            raise ValueError("exact order identity required")
        return rows[0]

    def place_order(self, **params):
        if params.get("symbol", "BTCUSDT") != "BTCUSDT" or params.get("positionIdx", 0) != 0:
            raise ValueError("only one-way BTCUSDT supported")
        if params.get("side") not in {"Buy", "Sell"} or params.get("orderType") not in {"Limit", "Market"}:
            raise ValueError("unsupported order")
        quantity = _d(params["qty"])
        if quantity <= 0 or quantity % self.step:
            raise ValueError("invalid quantity")
        if params.get("orderType") == "Limit" and (_d(params.get("price", 0)) <= 0 or _d(params["price"]) % self.tick):
            raise ValueError("invalid limit price")
        link = params.get("orderLinkId", "")
        if link and any(o["orderLinkId"] == link for o in self.orders.values()):
            raise ValueError("duplicate orderLinkId")
        ident = self._id()
        row = dict(params, orderId=ident, orderLinkId=link, symbol="BTCUSDT", positionIdx=0,
                   qty=_s(quantity), cumExecQty="0", leavesQty=_s(quantity),
                   orderStatus="Untriggered" if params.get("triggerPrice") else "New",
                   reduceOnly=bool(params.get("reduceOnly", False)), createdTime=str(self.ts_ms),
                   updatedTime=str(self.ts_ms), avgPrice="0", cumExecValue="0", cumExecFee="0")
        self.orders[ident] = row
        latency = self.latencies["market" if params["orderType"] == "Market" else "entry"]
        row["_active_at"] = self.ts_ms + latency
        self.writes.append((self.ts_ms, "place_order", copy.deepcopy(params)))
        return _reply(orderId=ident, orderLinkId=link)

    def cancel_order(self, **params):
        order = self._find(params)
        self._enqueue("cancel", order["orderId"], self.latencies["cancel"])
        self.writes.append((self.ts_ms, "cancel_order", copy.deepcopy(params)))
        return _reply(orderId=order["orderId"], orderLinkId=order["orderLinkId"])

    def amend_order(self, **params):
        order = self._find(params)
        allowed = {k: params[k] for k in ("price", "qty", "triggerPrice") if k in params}
        for key, value in allowed.items():
            step = self.step if key == "qty" else self.tick
            if _d(value) <= 0 or _d(value) % step:
                raise ValueError("invalid amendment precision")
        self._enqueue("amend", (order["orderId"], allowed), self.latencies["amend"])
        self.writes.append((self.ts_ms, "amend_order", copy.deepcopy(params)))
        return _reply(orderId=order["orderId"])

    def set_trading_stop(self, **params):
        if params.get("tpslMode") != "Full" or params.get("slTriggerBy") != "MarkPrice":
            raise ValueError("Full MarkPrice stop required")
        stop = _d(params["stopLoss"])
        if stop <= 0 or stop % self.tick:
            raise ValueError("invalid stop")
        self._enqueue("stop", (stop, 1 if self.position > 0 else -1), self.latencies["amend"])
        self.writes.append((self.ts_ms, "set_trading_stop", copy.deepcopy(params)))
        return _reply()

    def _sync_native(self, parent_order_link_id=None):
        native = self.orders.get(self._native_id)
        if not self.position:
            if native and native["orderStatus"] in self.LIVE:
                native.update(orderStatus="Cancelled", leavesQty="0", updatedTime=str(self.ts_ms))
            self._native_id, self.stop = None, Decimal(0)
            return
        if not self.stop:
            return
        if not native or native["orderStatus"] not in self.LIVE:
            ident = self._id()
            native = dict(orderId=ident, orderLinkId="", symbol="BTCUSDT", positionIdx=0,
                parentOrderLinkId=parent_order_link_id or "",
                side="Sell" if self.position > 0 else "Buy", reduceOnly=True,
                stopOrderType="StopLoss", triggerBy="MarkPrice", orderType="Market",
                triggerDirection=2 if self.position > 0 else 1,
                orderStatus="Untriggered", cumExecQty="0", cumExecValue="0", cumExecFee="0",
                avgPrice="0", createdTime=str(self.ts_ms), _active_at=self.ts_ms)
            self.orders[ident] = native
            self._native_id = ident
        native.update(triggerPrice=_s(self.stop), qty=_s(_d(native["cumExecQty"]) + abs(self.position)),
                      leavesQty=_s(abs(self.position)), updatedTime=str(self.ts_ms))
        if ((self.position > 0 and self.mark_price <= self.stop) or
                (self.position < 0 and self.mark_price >= self.stop)):
            native["_triggered"] = True
            if native["orderStatus"] == "Untriggered":
                native["orderStatus"] = "Triggered"

    def _fill(self, order, lots, price, maker):
        qty = lots * self.step
        signed = qty * (1 if order["side"] == "Buy" else -1)
        old = self.position
        closing = min(abs(old), qty) if old * signed < 0 else Decimal(0)
        gross = _money(closing * (price - self.average) * (1 if old > 0 else -1))
        fee = _money(qty * price * (self.maker_fee if maker else self.taker_fee))
        self.cash += gross - fee
        self.position += signed
        if old == 0 or old * signed > 0:
            self.average = (abs(old) * self.average + qty * price) / abs(self.position)
        elif self.position == 0:
            self.average = Decimal(0)
        elif old * self.position < 0:
            self.average = price
        filled = _d(order["cumExecQty"]) + qty
        value = _d(order["cumExecValue"]) + qty * price
        order.update(cumExecQty=_s(filled), leavesQty=_s(_d(order["qty"]) - filled),
            cumExecValue=_s(value), avgPrice=_s(value / filled),
            cumExecFee=_s(_d(order["cumExecFee"]) + fee), updatedTime=str(self.ts_ms),
            orderStatus="Filled" if filled == _d(order["qty"]) else "PartiallyFilled")
        eid = f"sim-exec-{len(self.executions) + 1:08d}"
        self.executions.append(dict(execId=eid, orderId=order["orderId"], orderLinkId=order["orderLinkId"],
            symbol="BTCUSDT", side=order["side"], execType="Trade", execQty=_s(qty),
            execPrice=_s(price), execFee=_s(fee), execTime=str(self.ts_ms), isMaker=maker))
        self.transactions.append(dict(id="txn-" + eid, type="TRADE", symbol="BTCUSDT", currency="USDT",
            tradeId=eid, orderId=order["orderId"], cashFlow=_s(gross), fee=_s(fee), funding="0",
            change=_s(gross - fee), transactionTime=str(self.ts_ms)))
        if not order["reduceOnly"] and order.get("stopLoss"):
            self.stop = _d(order["stopLoss"])
        self._sync_native(order["orderLinkId"] if not order["reduceOnly"] else None)

    def advance(self, ts_ms, price, mark_price=None, capacity_lots=0, *, _match=True):
        """Process one forward point event; return lots consumed from shared capacity.

        Funding precedes mutations and fills. Missed funding timestamps use only
        the previous known mark and are labelled as a proxy, never today's price.
        A stop already triggered remains active across capacity-limited events.
        """
        if type(ts_ms) is not int or ts_ms <= self.ts_ms:
            raise ValueError("events must advance strictly in time")
        if type(capacity_lots) is not int or capacity_lots < 0:
            raise ValueError("nonnegative integer lot capacity required")
        price, mark = _d(price), _d(price if mark_price is None else mark_price)
        if min(price, mark) <= 0:
            raise ValueError("positive prices required")
        for when, rate in self.funding_schedule:
            if self.ts_ms < when <= ts_ms and self.position:
                funding_mark = mark if when == ts_ms else self.mark_price
                self.funding_price_proxy_used |= when != ts_ms
                amount = _money(-self.position * funding_mark * rate)
                self.cash += amount
                self.transactions.append(dict(id=f"funding-{when}", type="SETTLEMENT", symbol="BTCUSDT",
                    currency="USDT", funding=_s(amount), fee="0", cashFlow="0", change=_s(amount), qty=_s(abs(self.position)),
                    transactionTime=str(when)))
        self.ts_ms, self.price, self.mark_price = ts_ms, price, mark
        self.mark_proxy_used |= mark_price is None
        while self._queue and self._queue[0][0] <= ts_ms:
            _, _, kind, payload = heapq.heappop(self._queue)
            if kind == "stop":
                stop, sign = payload
                if self.position * sign > 0:
                    self.stop = stop
                    self._sync_native()
                continue
            ident = payload if kind == "cancel" else payload[0]
            order = self.orders[ident]
            if order["orderStatus"] not in self.LIVE:
                continue
            if kind == "cancel":
                order.update(orderStatus="Cancelled", leavesQty="0", updatedTime=str(ts_ms))
            else:
                changes = payload[1]
                if "qty" in changes and _d(changes["qty"]) < _d(order["cumExecQty"]):
                    continue  # rejected late amendment must not undo earlier fills
                order.update(changes)
                if "price" in changes:
                    order["_rested"] = False
                order["leavesQty"] = _s(_d(order["qty"]) - _d(order["cumExecQty"]))
                order["updatedTime"] = str(ts_ms)
                if _d(order["leavesQty"]) == 0:
                    order["orderStatus"] = "Filled"
        # The production accounting contract rejects trades at the exact funding
        # millisecond. The driver must deliver a separate execution point +1ms.
        if not _match or any(t == ts_ms for t, _ in self.funding_schedule):
            return 0
        remaining = capacity_lots
        # Stable acceptance order within stop / reductions / entries.
        def priority(order):
            return (0 if order.get("stopOrderType") == "StopLoss" else 1 if order["reduceOnly"] else 2, order["orderId"])
        for order in sorted(list(self.orders.values()), key=priority):
            if order["orderStatus"] not in self.LIVE or order["_active_at"] > ts_ms:
                continue
            if order.get("triggerPrice") and not order.get("_triggered"):
                reference = mark if order.get("triggerBy") == "MarkPrice" else price
                hit = reference >= _d(order["triggerPrice"]) if order.get("triggerDirection") == 1 else reference <= _d(order["triggerPrice"])
                if not hit:
                    continue
                order["_triggered"] = True
                order["orderStatus"] = "Triggered"
            sign = 1 if order["side"] == "Buy" else -1
            if order["reduceOnly"] and self.position * sign >= 0:
                order.update(orderStatus="Cancelled", leavesQty="0")
                continue
            market = order["orderType"] == "Market"
            quote = price * (1 + sign * self.spread / 2)
            executable = price * (1 + sign * (self.slippage + self.spread / 2)) if market else _d(order["price"])
            if market:
                executable = (executable / self.tick).to_integral_value(rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR) * self.tick
            crossed = market or (quote <= executable if sign > 0 else quote >= executable)
            maker = not market and bool(order.get("_rested"))
            if crossed and not market and not maker:
                slipped = price * (1 + sign * (self.slippage + self.spread / 2))
                slipped = (slipped / self.tick).to_integral_value(rounding=ROUND_CEILING if sign > 0 else ROUND_FLOOR) * self.tick
                executable = min(executable, slipped) if sign > 0 else max(executable, slipped)
            order["_rested"] = True
            ioc = order.get("timeInForce") == "IOC" or (market and not order.get("triggerPrice"))
            if crossed:
                lots = min(remaining, int(_d(order["leavesQty"]) / self.step))
                if order["reduceOnly"]:
                    lots = min(lots, int(abs(self.position) / self.step))
                elif self.position * sign < 0:
                    # Strategy is one-way; do not silently flip an existing thesis.
                    order.update(orderStatus="Rejected", leavesQty="0")
                    continue
                else:
                    available = max(Decimal(0), self.equity - abs(self.position) * mark / 10)
                    lots = min(lots, int(available / (self.step * executable * (Decimal('.1') + self.taker_fee))))
                if lots:
                    self._fill(order, lots, executable, maker=maker)
                    remaining -= lots
                    # An attached native stop is created by a new entry fill,
                    # therefore absent from this event's initial order list.
                    # Latch the trigger in _sync_native even if no capacity is
                    # left, and prioritize its reduction before another entry.
                    native = self.orders.get(self._native_id)
                    if not order["reduceOnly"] and native and native.get("_triggered") and remaining:
                        stop_lots = min(remaining, int(abs(self.position) / self.step))
                        stop_sign = -1 if self.position > 0 else 1
                        stop_price = price * (1 + stop_sign * (self.slippage + self.spread / 2))
                        stop_price = (stop_price / self.tick).to_integral_value(
                            rounding=ROUND_CEILING if stop_sign > 0 else ROUND_FLOOR) * self.tick
                        if stop_lots:
                            self._fill(native, stop_lots, stop_price, maker=False)
                            remaining -= stop_lots
            if ioc and order["orderStatus"] in self.LIVE:
                order.update(orderStatus="PartiallyFilledCanceled" if _d(order["cumExecQty"]) else "Cancelled", leavesQty="0")
        self.capacity_used_lots += capacity_lots - remaining
        return capacity_lots - remaining

    def get_api_key_information(self, **params):
        return _reply(userID=self.uid)

    def get_account_info(self, **params):
        return _reply(marginMode="REGULAR_MARGIN")

    def get_positions(self, **params):
        return _reply([dict(symbol="BTCUSDT", positionIdx=0, size=_s(abs(self.position)),
            side="Buy" if self.position > 0 else "Sell" if self.position < 0 else "",
            avgPrice=_s(self.average), stopLoss=_s(self.stop), leverage="10",
            markPrice=_s(self.mark_price), positionIM=_s(abs(self.position) * self.mark_price / 10),
            unrealisedPnl=_s(self.position * (self.mark_price - self.average)), liqPrice="")])

    def get_wallet_balance(self, **params):
        im = abs(self.position) * self.mark_price / 10
        mm = abs(self.position) * self.mark_price * Decimal('.005')
        return _reply([dict(totalEquity=_s(self.equity), totalWalletBalance=_s(self.cash),
            totalMarginBalance=_s(self.equity), totalAvailableBalance=_s(max(Decimal(0), self.equity - im)),
            totalInitialMargin=_s(im), totalMaintenanceMargin=_s(mm),
            accountIMRate=_s(im / self.equity) if self.equity > 0 else "1",
            accountMMRate=_s(mm / self.equity) if self.equity > 0 else "1",
            coin=[dict(coin="USDT", equity=_s(self.equity), usdValue=_s(self.equity))])])

    def get_risk_limit(self, **params):
        return _reply([dict(symbol="BTCUSDT", riskLimitValue="1000000000", maintenanceMargin=".005",
                            initialMargin=".01", maxLeverage="100")])

    def get_instruments_info(self, **params):
        return _reply([dict(symbol="BTCUSDT", status="Trading", settleCoin="USDT",
            fundingInterval=str(self.funding_interval_ms // 60000),
            priceFilter=dict(tickSize=_s(self.tick)), lotSizeFilter=dict(qtyStep=_s(self.step),
            minOrderQty=_s(self.step), maxOrderQty="100", maxMktOrderQty="100", minNotionalValue="5"))])

    def get_tickers(self, **params):
        next_funding = (self.ts_ms // self.funding_interval_ms + 1) * self.funding_interval_ms
        return _reply([dict(symbol="BTCUSDT", lastPrice=_s(self.price), markPrice=_s(self.mark_price),
            bid1Price=_s(self.price * (1-self.spread/2)), ask1Price=_s(self.price * (1+self.spread/2)), nextFundingTime=str(next_funding))])

    def _rows(self, rows, params, time_key=None):
        return [self._public(row) for row in rows if
            all(not params.get(key) or row.get(key) == params[key] for key in ("orderId", "orderLinkId", "symbol"))
            and (not time_key or params.get("startTime", 0) <= int(row[time_key]) <= params.get("endTime", self.ts_ms))]

    @staticmethod
    def _public(row):
        return {k: copy.deepcopy(v) for k, v in row.items() if not k.startswith("_")}

    def get_open_orders(self, **params):
        return _reply(self._rows([o for o in self.orders.values() if o["orderStatus"] in self.LIVE], params))

    def get_order_history(self, **params):
        return _reply(self._rows(list(self.orders.values()), params, "createdTime"))

    def get_executions(self, **params):
        return _reply(self._rows(self.executions, params, "execTime"))

    def get_transaction_log(self, **params):
        return _reply(self._rows(self.transactions, params, "transactionTime"))

    def get_funding_rate_history(self, **params):
        return _reply([dict(symbol="BTCUSDT", fundingRateTimestamp=str(t), fundingRate=_s(r))
            for t, r in self.funding_schedule if params.get("startTime", 0) <= t <= min(self.ts_ms, params.get("endTime", self.ts_ms))])

    def snapshot(self):
        return dict(simulation=True, timestamp_ms=self.ts_ms, equity=_s(self.equity), cash=_s(self.cash),
            position=_s(self.position), average_price=_s(self.average),
            orders=[self._public(o) for o in self.orders.values()], executions=copy.deepcopy(self.executions),
            transactions=copy.deepcopy(self.transactions), mark_proxy_used=self.mark_proxy_used,
            funding_price_proxy_used=self.funding_price_proxy_used, capacity_used_lots=self.capacity_used_lots)

    def digest(self):
        return hashlib.sha256(json.dumps(self.snapshot(), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


OfflineBybitSession = ScenarioExchange
