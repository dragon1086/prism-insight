"""B3 all-entries SHADOW worker (one market per process). Read-only market/holdings access.

Each tick: reconcile campaigns whose legacy row is gone (close with the recorded
strategy exit), then, within two minutes after a completed 5-minute boundary,
assemble current evidence and let the v3-ae policy decide an add. Any missing
input leaves the campaign waiting; nothing here can order, write holdings or send.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
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

    def once(self):
        now = self.clock()
        result = dict(contract="b3-ae-worker-v1", market=self.market, at=now, rows=[],
                      orders_submitted=0, status="OUTSIDE_REGULAR_SESSION")
        moment = _time(now)
        session_open = self.providers["session_open"](moment)
        boundary = moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)
        evaluate = session_open and moment - boundary <= EVALUATION_WINDOW
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
                if not evaluate:
                    continue
                try:
                    evidence, current = self._evidence(db, campaign, row, now, boundary.isoformat())
                    if evidence["status"] != "OK":
                        result["rows"].append(dict(campaign_id=campaign["campaign_id"], status="WAIT",
                                                   reason=evidence["reason_codes"]))
                        continue
                    decision = self.store.evaluate(campaign["campaign_id"], evidence["evidence"], now=current,
                                                   current_stop=row["stop_loss"] or campaign["plan"]["initial_stop"])
                    result["rows"].append(dict(campaign_id=campaign["campaign_id"], status=decision["action"],
                                               reason=decision["reason"], target=decision["target_allocation"]))
                except Exception as error:  # noqa: BLE001 - one campaign never stops the others
                    result["rows"].append(dict(campaign_id=campaign["campaign_id"], status="ERROR",
                                               error_type=type(error).__name__, detail=str(error)[:120]))
        finally:
            db.close()
        result["status"] = "COMPLETED" if session_open else result["status"]
        result["completed_at"] = self.clock()
        return result


def kr_providers():
    """KIS-backed KR providers; per-day caches for the calendar, trend closes and actions."""
    from cores.market_data.kis_source import KisSource
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

    return dict(session_open=session_open, intraday=intraday, quote=quote, market=market)


def us_providers():
    """Existing US providers (yfinance quote/5m bars, S&P/Nasdaq snapshot, NYSE session)."""
    from prism_core.oneil_runtime_inputs import (
        IntradayProvider,
        fetch_market_snapshot,
        fetch_quote,
        quote_input,
    )
    from prism_core.oneil_service import regular_session

    intraday_provider, exchanges, cache = IntradayProvider(), {}, {}
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

    return dict(session_open=lambda moment: regular_session(moment.isoformat()), intraday=intraday,
                quote=quote, market=market)
