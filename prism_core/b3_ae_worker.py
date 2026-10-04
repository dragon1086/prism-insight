"""B3 all-entries worker (one market per process). Read-only market/holdings access.

Each tick: reconcile campaigns whose legacy row is gone (close with the recorded
strategy exit), then, within two minutes after a completed 5-minute boundary,
assemble current evidence and let the v3-ae policy book a virtual (SHADOW) add.
For micro-split LIVE campaigns it also evaluates the holding's scenario-based
``add_plan`` (5-minute closes in session, daily-close confirmations once after the
close) and hands a qualified add to the injected LIVE executor; the fixed ladder
never orders. Any missing input leaves the campaign waiting.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from prism_core.oneil_adaptive_policy import _hash, _time

TABLES = {"KR": ("stock_holdings", "trading_history"), "US": ("us_stock_holdings", "us_trading_history")}
# Complete literal statements per market; no query text is built at runtime.
_PORTFOLIO_SQL = {
    "KR": "SELECT id, ticker, scenario FROM stock_holdings WHERE account_key=?",
    "US": "SELECT id, ticker, scenario FROM us_stock_holdings WHERE account_key=?",
}
_HOLDING_SQL = {
    "KR": "SELECT id, ticker, account_key, scenario, stop_loss FROM stock_holdings "
          "WHERE id=? AND ticker=? AND account_key=?",
    "US": "SELECT id, ticker, account_key, scenario, stop_loss FROM us_stock_holdings "
          "WHERE id=? AND ticker=? AND account_key=?",
}
_HISTORY_SQL = {
    "KR": "SELECT buy_date, sell_date, sell_price, exit_kind FROM trading_history "
          "WHERE ticker=? AND account_key=? ORDER BY sell_date",
    "US": "SELECT buy_date, sell_date, sell_price, exit_kind FROM us_trading_history "
          "WHERE ticker=? AND account_key=? ORDER BY sell_date",
}
# Stop/trend-exit loop state (tools/hardstop_seller.py, tools/trend_exit_seller.py); optional tables.
_LOOP_SQL = ("SELECT submitted_ts FROM loop_a_inflight_orders WHERE ticker=? AND market=?",
             "SELECT submitted_ts FROM loop_b_inflight_orders WHERE ticker=? AND market=?")
_BREACH_SQL = "SELECT last_breach_date FROM loop_b_position_state WHERE ticker=? AND market=?"
DB_LOCAL = ZoneInfo("Asia/Seoul")  # legacy rows store server-local (KST) timestamps for both markets
EVALUATION_WINDOW = timedelta(seconds=90)  # live-capture clock allows 120 s incl. fetch


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def _identity(plan, position_id):
    return dict(symbol=plan["symbol"], position_id=position_id,
                source_decision_ref=plan["source_decision_ref"],
                price_basis_ref=plan["setup"]["price_basis_ref"])


def _local(text):
    return datetime.strptime(str(text)[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=DB_LOCAL)


class B3AeWorker:
    def __init__(self, market, *, store, holdings_db, providers, clock=utc_now, max_slots=10):
        if market not in TABLES:
            raise ValueError("unsupported market")
        self.market, self.store, self.holdings_db = market, store, str(holdings_db)
        self.providers, self.clock, self.max_slots = providers, clock, max_slots
        self._close_done, self._announced = set(), set()

    def _reader(self):
        db = sqlite3.connect(f"file:{self.holdings_db}?mode=ro", uri=True, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _portfolio(self, db, account_key, now):
        rows = db.execute(_PORTFOLIO_SQL[self.market], (account_key,)).fetchall()
        positions, sectors = [], []
        for row in rows:
            scenario = json.loads(row["scenario"] or "{}")
            positions.append(dict(position_id=f"legacy:{self.market}:{row['id']}", symbol=row["ticker"],
                                  source_decision_ref=scenario.get("_decision_id"), account_key=account_key))
            if scenario.get("sector"):
                sectors.append(scenario["sector"])
        return dict(observed_at=now, source_ref=_hash([positions, sectors, now]), account_key=account_key,
                    positions=positions, slots_used=len(positions), max_slots=self.max_slots,
                    scenario_sectors=sectors, max_same_sector=3, sector_concentration_ratio=.3,
                    minimum_holdings_for_ratio=4)

    def _reconcile_exit(self, db, campaign):
        """Legacy row gone: close with the first recorded strategy exit after the entry."""
        entered = _time(campaign["plan"]["created_at"])
        for row in db.execute(_HISTORY_SQL[self.market], (campaign["symbol"], campaign["account_key"])):
            try:
                bought, sold = _local(row["buy_date"]), _local(row["sell_date"])
            except (TypeError, ValueError):
                continue
            if abs(bought - entered) <= timedelta(minutes=10) and sold >= bought:
                return self.store.close(market=self.market, account_key=campaign["account_key"],
                                        position_id=campaign["position_id"], exit_price=row["sell_price"],
                                        exit_at=sold.astimezone(timezone.utc).isoformat(),
                                        reason=f"RECONCILED:{row['exit_kind'] or 'exit'}")
        return None

    def _evidence(self, db, campaign, row, now, as_of):
        from prism_core.oneil_input_bridge import assemble_evidence
        from prism_core.oneil_runtime_inputs import current_gates

        plan, position_id = campaign["plan"], campaign["position_id"]
        scenario = json.loads(row["scenario"] or "{}")
        started = self.clock()
        intraday = self.providers["intraday"](plan["symbol"], as_of, started)
        quote = dict(_identity(plan, position_id), **self.providers["quote"](plan, position_id, self.clock()))
        market = self.providers["market"]()
        current = self.clock()  # after every input, so none is observed in the future
        gates = current_gates(plan=plan, position_id=position_id, scenario=scenario, quote=quote,
                              portfolio=self._portfolio(db, campaign["account_key"], current),
                              market=market, now=current, phase="ADD")
        return assemble_evidence(plan=plan, setup_input=dict(status="OK", setup=plan["setup"]),
                                 intraday_input=intraday, quote=quote, gates=gates, now=current), current

    def _b3_step(self, db, campaign, row, now, boundary):
        """B3 v3-ae ladder: SHADOW virtual ledger only (never a LIVE order since 2026-10-02)."""
        evidence, current = self._evidence(db, campaign, row, now, boundary.isoformat())
        if evidence["status"] != "OK":
            return dict(campaign_id=campaign["campaign_id"], status="WAIT", reason=evidence["reason_codes"])
        decision = self.store.evaluate(campaign["campaign_id"], evidence["evidence"], now=current,
                                       current_stop=row["stop_loss"] or campaign["plan"]["initial_stop"])
        return dict(campaign_id=campaign["campaign_id"], status=decision["action"], reason=decision["reason"],
                    target=decision["target_allocation"], mode=campaign.get("mode", "SHADOW"))

    def _plans_on(self):
        from prism_core import micro_split_live
        return ("add_inputs" in self.providers and "live_add" in self.providers
                and micro_split_live.plan_adds_enabled(self.market))

    def _sell_day_block(self, db, symbol, session_date, now):
        """A stop/trend-exit loop order or a trend breach today blocks adds (read-only, tables optional)."""
        from prism_core.add_plan import SESSIONS
        zone = SESSIONS[self.market][0]
        for sql in _LOOP_SQL:
            try:
                rows = db.execute(sql, (symbol, self.market)).fetchall()
            except sqlite3.Error:
                continue
            for (stamp,) in rows:
                try:
                    if _time(stamp).astimezone(zone).date().isoformat() == session_date:
                        return "LOOP_SELL_ORDER"
                except (TypeError, ValueError):
                    continue
        try:
            row = db.execute(_BREACH_SQL, (symbol, self.market)).fetchone()
        except sqlite3.Error:
            row = None
        if row and row[0] in {session_date, _time(now).date().isoformat()}:
            return "TREND_BREACH"
        return None

    def _plan_step(self, db, campaign, row, phase, now=None):
        """Evaluate the holding's add_plan (LIVE micro-split) and hand a qualified add to the LIVE executor."""
        from observability.events import emit_event
        from prism_core import add_plan, micro_split_live

        now = now or self.clock()
        cid, symbol = campaign["campaign_id"], campaign["symbol"]
        out = dict(campaign_id=cid, kind="add_plan", phase=phase)
        block = micro_split_live.record(json.loads(row["scenario"] or "{}"))
        today = _time(now).astimezone(add_plan.SESSIONS[self.market][0]).date().isoformat()
        plan = add_plan.plan_for_session(block or {}, today) or {}
        if block is None or plan.get("status") != "ACTIVE" or plan.get("valid_for") != today:
            return dict(out, status="WAIT", reason="NO_ACTIVE_PLAN_FOR_SESSION")
        legs = block["legs"]
        # After today's first add the acceleration rail needs the matched volume pace, so the
        # provider fetches it once the price reaches the acceleration level (+8% over the entry).
        added_today = any(leg.get("kind") == "ADD" and leg.get("session") == today for leg in legs)
        pace_above = add_plan.acceleration_price(legs[0]["price"]) if added_today else None
        evidence = self.providers["add_inputs"](symbol, now, phase, plan, pace_above=pace_above)
        current = self.clock()
        state = dict(allocation=block["allocation"], legs=legs, initial_entry=legs[0]["price"],
                     initial_stop=campaign["plan"]["initial_stop"],
                     current_stop=row["stop_loss"] or campaign["plan"]["initial_stop"],
                     fee_rate=campaign["plan"]["fee_rate"],
                     entry_session=add_plan.entry_session_date(self.market, legs[0]["at"]).isoformat(),
                     blocked=self._sell_day_block(db, symbol, (evidence or {}).get("session_date") or today, current))
        decision = add_plan.evaluate_plan(plan, state, evidence, now=current)
        out.update(status=decision["action"], reason=decision["reason"])
        attributes = {"campaign_id": cid, "slot_allocation": float(block["allocation"]),
                      "plan_hash": plan.get("plan_hash"), "phase": phase, "reason": decision["reason"]}
        service = f"prism-{self.market.lower()}-micro-split"
        if decision["action"] == "INVALIDATED":
            key = (plan.get("plan_hash"), decision["reason"])
            if key not in self._announced:
                self._announced.add(key)
                self.store.record_event(cid, current, "add_plan.invalidated", attributes)
                emit_event("micro_split.add_plan_invalidated", service=service, market=self.market, ticker=symbol,
                           position_id=campaign["position_id"], attributes=attributes)
            return out
        if decision["action"] != "ADD":
            out["reasons"] = decision.get("reasons")
            return out
        meta = {k: decision[k] for k in ("scenario_id", "scenario_type", "lens", "plan_hash", "trigger_price",
                                         "rationale")}
        meta["session"] = decision["session_date"]
        if decision.get("rail"):
            meta.update(rail=decision["rail"], acceleration=decision.get("acceleration"))
        attributes.update(scenario_id=decision["scenario_id"], scenario_type=decision["scenario_type"],
                          lens=decision["lens"], target_allocation=decision["target_allocation"],
                          price=decision["price"], trigger_price=decision["trigger_price"],
                          risk_clipped=decision["risk_clipped"], rail=decision.get("rail"),
                          acceleration=decision.get("acceleration"))
        self.store.record_event(cid, current, "add_plan.qualified", attributes)
        emit_event("micro_split.add_plan_qualified", service=service, market=self.market, ticker=symbol,
                   position_id=campaign["position_id"], attributes=attributes)
        live = self.providers["live_add"](campaign, dict(target_allocation=decision["target_allocation"],
                                                         price=decision["limit_price"], bar_end=decision["key"],
                                                         add_plan=meta), current)
        out["live"] = {k: live.get(k) for k in ("status", "allocation", "cash")}
        out["submitted"] = bool((live.get("broker") or {}).get("success"))
        self.store.record_event(cid, current, "add_plan.executed", dict(attributes, live=out["live"]))
        return out

    def once(self):
        now = self.clock()
        result = dict(contract="b3-ae-worker-v1", market=self.market, at=now, rows=[],
                      orders_submitted=0, status="OUTSIDE_REGULAR_SESSION")
        moment = _time(now)
        session_open = self.providers["session_open"](moment)
        boundary = moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)
        evaluate = session_open and moment - boundary <= EVALUATION_WINDOW
        plans_on = self._plans_on()
        close_day = self.providers["close_ready"](moment) if plans_on and "close_ready" in self.providers else None
        db = self._reader()
        try:
            for campaign in self.store.active(self.market):
                match = re.fullmatch(rf"legacy:{self.market}:(\d+)", campaign["position_id"])
                row = None if match is None else db.execute(
                    _HOLDING_SQL[self.market],
                    (int(match.group(1)), campaign["symbol"], campaign["account_key"])).fetchone()
                if row is None:
                    closed = self._reconcile_exit(db, campaign)
                    result["rows"].append(dict(campaign_id=campaign["campaign_id"],
                                               status="CLOSED" if closed else "LEGACY_ROW_MISSING"))
                    continue
                live = plans_on and campaign.get("mode") == "LIVE"
                steps = []
                if evaluate:
                    steps.append((self._b3_step, (now, boundary)))
                    if live:
                        steps.append((self._plan_step, ("INTRADAY", None)))
                if live and close_day and (campaign["campaign_id"], close_day) not in self._close_done:
                    steps.append((self._close_step, (close_day,)))
                for step, args in steps:
                    try:
                        row_result = step(db, campaign, row, *args)
                    except Exception as error:  # noqa: BLE001 - one campaign never stops the others
                        row_result = dict(campaign_id=campaign["campaign_id"], status="ERROR",
                                          error_type=type(error).__name__, detail=str(error)[:120])
                    result["orders_submitted"] += int(bool(row_result.pop("submitted", False)))
                    result["rows"].append(row_result)
        finally:
            db.close()
        result["status"] = "COMPLETED" if session_open or close_day else result["status"]
        result["completed_at"] = self.clock()
        return result

    def _close_step(self, db, campaign, row, close_day):
        """Daily-close confirmation once per session after the close data is complete."""
        out = self._plan_step(db, campaign, row, "CLOSE", self.clock())
        if not str(out.get("reason", "")).startswith("EVIDENCE_"):
            self._close_done.add((campaign["campaign_id"], close_day))
        return out


def kr_providers():
    """KIS-backed KR providers; per-day caches for the calendar, trend closes and actions."""
    from cores.market_data.kis_source import KisSource
    from prism_core import add_plan
    from prism_core import b3_ae_inputs_kr as K
    from prism_core.oneil_intraday_inputs import build_intraday_inputs

    source, market, daily = KisSource(), K.MarketSnapshot(), {}

    def calendar(moment):
        key = ("calendar", moment.astimezone(K.SEOUL).date())
        if key not in daily:
            daily.clear()
            daily[key] = K.xkrx_sessions(moment)
        return daily[key]

    def session_open(moment):
        try:
            today = calendar(moment)["sessions"][-1]
        except ValueError:
            return False
        return _time(today["open_at"]) <= moment < _time(today["close_at"])

    def intraday(symbol, as_of, started):
        moment = _time(started)
        cal = calendar(moment)
        day = moment.astimezone(K.SEOUL).date()
        if ("closes", symbol) not in daily:
            daily[("closes", symbol)] = K.prior_closes(source, symbol, cal["sessions"])
            daily[("action", symbol)] = K.corporate_action_today(source, symbol, day)
        rows = K.minute_rows(source, symbol, moment)
        bars = K.five_minute_bars(rows, day, corporate_action_today=daily[("action", symbol)])
        return build_intraday_inputs(
            symbol=symbol, bars=bars, calendar=cal, as_of=as_of, retrieved_at=utc_now(),
            retrieval_started_at=started, price_basis_ref=K.PRICE_BASIS,
            source_ref=_hash(["kis-domestic-minute-5m", symbol, as_of]), kind="LIVE_CAPTURE",
            volume_required=False, market="KR", prior_session_closes=daily[("closes", symbol)])

    def quote(plan, position_id, now):
        return K.quote(source, plan["symbol"])

    def close_ready(moment):
        """Today's session date once its daily bar is final and reserved orders are accepted (16:00-23:30)."""
        try:
            today = calendar(moment)["sessions"][-1]
        except ValueError:
            return None
        local = moment.astimezone(K.SEOUL)
        ready = _time(today["close_at"]) + timedelta(minutes=30) <= moment and local.hour * 60 + local.minute < 1410
        return today["trade_date"] if ready else None

    def add_inputs(symbol, now, phase, plan, pace_above=None):
        """Add-plan evidence: completed KIS daily bars, today's 5m bars, quote, matched volume pace.

        The pace is fetched for plans that use it, and for the acceleration rail once the quote
        reaches ``pace_above`` (the worker passes it only after today's first add).
        """
        moment = _time(now)
        try:
            sessions = calendar(moment)["sessions"]
        except ValueError:
            return dict(status="MISSING", reason="NOT_A_SESSION")
        today = sessions[-1]
        if ("add_daily", symbol) not in daily:
            daily[("add_daily", symbol)] = K.daily_bars(source, symbol, sessions[0]["trade_date"],
                                                        sessions[-2]["trade_date"])
        out = dict(status="OK", phase=phase, session_date=today["trade_date"], open_at=today["open_at"],
                   close_at=today["close_at"],
                   daily=[b for b in daily[("add_daily", symbol)] if b["date"] < today["trade_date"]])
        if phase == "CLOSE":
            bars = [b for b in K.daily_bars(source, symbol, today["trade_date"], today["trade_date"])
                    if b["date"] == today["trade_date"]]
            if not bars:
                return dict(status="MISSING", reason="CLOSE_BAR_UNAVAILABLE")
            return dict(out, today_daily=bars[-1], price=bars[-1]["close"])
        opened, cutoff = _time(today["open_at"]), min(moment, _time(today["close_at"]))
        day = moment.astimezone(K.SEOUL).date()
        rows = K.five_minute_bars(K.minute_rows(source, symbol, moment), day, corporate_action_today=False)
        out["today_bars"] = [
            dict(start_at=b["provider_timestamp"], end_at=(_time(b["provider_timestamp"]) + timedelta(minutes=5))
                 .isoformat(), **{k: b[k] for k in ("open", "high", "low", "close", "volume")})
            for b in rows if opened <= _time(b["provider_timestamp"])
            and _time(b["provider_timestamp"]) + timedelta(minutes=5) <= cutoff]
        out["price"] = K.quote(source, symbol)["price"]
        accelerating = pace_above is not None and float(out["price"] or 0) >= pace_above
        if out["today_bars"] and (add_plan.needs_intraday_pace(plan) or accelerating):
            if ("add_curves", symbol) not in daily:
                daily[("add_curves", symbol)] = K.prior_minute_volumes(source, symbol, sessions[:-1])
            end = out["today_bars"][-1]["end_at"]
            elapsed = (_time(end) - opened).total_seconds() / 60
            out["prior_cumulative"] = dict(end_at=end, samples=[
                sum(v for m, v in curve if m < elapsed) for curve in daily[("add_curves", symbol)]])
        return out

    return dict(session_open=session_open, intraday=intraday, quote=quote, market=market,
                close_ready=close_ready, add_inputs=add_inputs)


def us_providers():
    """Existing US providers (yfinance quote/5m bars, S&P/Nasdaq snapshot, NYSE session)."""
    from prism_core import add_plan
    from prism_core.oneil_runtime_inputs import (
        IntradayProvider,
        fetch_market_snapshot,
        fetch_quote,
        quote_input,
    )
    from prism_core.oneil_service import regular_session
    from tools.collect_trend_replay_data import fetch

    intraday_provider, exchanges, cache, plan_cache = IntradayProvider(), {}, {}, {}
    NY = ZoneInfo("America/New_York")
    names = {"NMS": "NASDAQ", "NGM": "NASDAQ", "NCM": "NASDAQ", "NYQ": "NYSE", "ASE": "NYSE"}

    def quote(plan, position_id, now):
        response = fetch_quote(plan["symbol"])
        if response.get("exchange") in names:
            exchanges[plan["symbol"]] = names[response["exchange"]]
        value = quote_input(plan=plan, position_id=position_id, response=response, now=now)
        return {k: value[k] for k in ("price", "observed_at", "source_ref")}

    def intraday(symbol, as_of, started):
        if symbol not in exchanges:
            response = fetch_quote(symbol)
            exchanges[symbol] = names.get(response.get("exchange"), "NYSE")
        return intraday_provider(symbol, as_of, exchanges[symbol])

    def market():
        at = datetime.now(timezone.utc)
        if not cache or (at - _time(cache["value"]["observed_at"])).total_seconds() >= 60:
            cache["value"] = fetch_market_snapshot()
        return cache["value"]

    def schedule(moment):
        """Today's NYSE regular session (trade_date, open, close) or None."""
        import pandas_market_calendars as calendars
        local = moment.astimezone(NY).date()
        frame = calendars.get_calendar("NYSE").schedule(local, local)
        if not len(frame):
            return None
        row = frame.iloc[0]
        return local.isoformat(), row["market_open"].to_pydatetime(), row["market_close"].to_pydatetime()

    def close_ready(moment):
        """Today's session date once its daily bar is final (16:15-20:00 New York)."""
        today = schedule(moment)
        if today is None:
            return None
        ready = today[2] + timedelta(minutes=15) <= moment and moment.astimezone(NY).hour < 20
        return today[0] if ready else None

    def _rows(symbol, interval, start, end):
        response = fetch(dict(market="US", ticker=symbol, interval=interval, start=start.isoformat(),
                              end=end.isoformat()))
        if response.get("status") != "received":
            raise ValueError(f"US_{interval}_UNAVAILABLE")
        return [r for r in response["raw_rows"]
                if all(r.get(k) is not None for k in ("open", "high", "low", "close", "volume"))]

    def _bar(row, **extra):
        return dict(extra, **{k: row[k] for k in ("open", "high", "low", "close", "volume")})

    def add_inputs(symbol, now, phase, plan, pace_above=None):
        """Add-plan evidence from yfinance: completed daily bars, today's regular 5m bars, quote, volume pace.

        Prior sessions (daily bars and their 5m volume) are fetched once per symbol and trading day.
        The pace is fetched for plans that use it, and for the acceleration rail once the quote
        reaches ``pace_above`` (the worker passes it only after today's first add).
        """
        moment = _time(now)
        today = schedule(moment)
        if today is None:
            return dict(status="MISSING", reason="NOT_A_SESSION")
        trade_date, opened, closed = today
        key = ("add_prior", symbol)
        if plan_cache.get(key, {}).get("date") != trade_date:
            rows = _rows(symbol, "1d", opened - timedelta(days=45), opened)
            prior = [_bar(r, date=_time(r["provider_timestamp"]).astimezone(NY).date().isoformat()) for r in rows]
            plan_cache[key] = dict(date=trade_date, daily=[b for b in prior if b["date"] < trade_date], minutes=None)
        prior = plan_cache[key]
        out = dict(status="OK", phase=phase, session_date=trade_date, open_at=opened.isoformat(),
                   close_at=closed.isoformat(), daily=prior["daily"])
        if phase == "CLOSE":
            bars = [_bar(r, date=trade_date) for r in _rows(symbol, "1d", opened - timedelta(days=1), moment)
                    if _time(r["provider_timestamp"]).astimezone(NY).date().isoformat() == trade_date]
            if not bars:
                return dict(status="MISSING", reason="CLOSE_BAR_UNAVAILABLE")
            return dict(out, today_daily=bars[-1], price=bars[-1]["close"])
        step, cutoff = timedelta(minutes=5), min(moment, closed)
        out["today_bars"] = [_bar(r, start_at=r["provider_timestamp"],
                                  end_at=(_time(r["provider_timestamp"]) + step).isoformat())
                             for r in _rows(symbol, "5m", opened, moment)
                             if opened <= _time(r["provider_timestamp"])
                             and _time(r["provider_timestamp"]) + step <= cutoff]
        price = fetch_quote(symbol).get("regularMarketPrice")
        if not price:
            return dict(status="MISSING", reason="QUOTE_UNAVAILABLE")
        out["price"] = price
        accelerating = pace_above is not None and float(price) >= pace_above
        if out["today_bars"] and (add_plan.needs_intraday_pace(plan) or accelerating) and len(prior["daily"]) >= 20:
            if prior["minutes"] is None:
                first = datetime.combine(date.fromisoformat(prior["daily"][-20]["date"]), opened.astimezone(NY).timetz())
                prior["minutes"] = _rows(symbol, "5m", first.astimezone(timezone.utc), opened)
            elapsed = _time(out["today_bars"][-1]["end_at"]) - opened
            samples = {}
            for row in prior["minutes"]:
                local = _time(row["provider_timestamp"]).astimezone(NY)
                session_open = local.replace(hour=opened.astimezone(NY).hour, minute=opened.astimezone(NY).minute,
                                             second=0, microsecond=0)
                day = local.date().isoformat()
                samples.setdefault(day, 0.0)
                if session_open <= local and local + step <= session_open + elapsed:
                    samples[day] += row["volume"]
            out["prior_cumulative"] = dict(end_at=out["today_bars"][-1]["end_at"], samples=[
                samples[b["date"]] for b in prior["daily"][-20:] if b["date"] in samples])
        return out

    return dict(session_open=lambda moment: regular_session(moment.isoformat()), intraday=intraday,
                quote=quote, market=market, close_ready=close_ready, add_inputs=add_inputs)
