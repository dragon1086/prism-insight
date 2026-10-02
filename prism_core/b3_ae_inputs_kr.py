"""KR inputs for the B3 all-entries SHADOW: KIS quote, 1m->5m bars, XKRX calendar, trend, market.

Read-only KIS/market reads. Every value carries the time it was observed; a
missing or unverifiable input stays missing (the policy then waits, never adds).
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from prism_core.oneil_adaptive_policy import _hash

SEOUL = ZoneInfo("Asia/Seoul")
_MINUTE_URL = "/uapi/domestic-stock/v1/quotations/inquire-time-dailychartprice"
_MINUTE_TR = "FHKST03010230"
PRICE_BASIS = "kis-domestic-raw-krw"


def _iso(moment):
    return moment.astimezone(timezone.utc).isoformat()


def xkrx_sessions(now, count=21):
    """The last ``count`` XKRX regular sessions ending with today's (KST)."""
    import pandas_market_calendars as calendars

    local = now.astimezone(SEOUL)
    schedule = calendars.get_calendar("XKRX").schedule(
        (local - timedelta(days=45)).date(), local.date())
    rows = [dict(trade_date=day.date().isoformat(), open_at=_iso(row["market_open"]),
                 close_at=_iso(row["market_close"])) for day, row in schedule.iterrows()]
    if not rows or rows[-1]["trade_date"] != local.date().isoformat():
        raise ValueError("NOT_AN_XKRX_SESSION")
    return dict(calendar_ref=_hash(["XKRX", rows[-count:]]), sessions=rows[-count:])


def minute_rows(source, ticker, now, *, pages=14, pause=0.0):
    """Today's 1-minute rows {HHMMSS: row}; the label is the minute start (verified live)."""
    local = now.astimezone(SEOUL)
    day, hour, out = local.strftime("%Y%m%d"), local.strftime("%H%M%S"), {}
    for _ in range(pages):
        if pause and out:
            time.sleep(pause)  # bulk history walks share the KIS quota with the sell loops
        body = source._fetch(_MINUTE_URL, _MINUTE_TR, {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker, "FID_INPUT_HOUR_1": hour,
            "FID_INPUT_DATE_1": day, "FID_PW_DATA_INCU_YN": "N", "FID_FAKE_TICK_INCU_YN": ""})
        rows = [r for r in (getattr(body, "output2", None) or []) if r.get("stck_bsop_date") == day]
        fresh = [r for r in rows if r.get("stck_cntg_hour") and r["stck_cntg_hour"] not in out]
        if not fresh:
            break
        for row in fresh:
            out[row["stck_cntg_hour"]] = row
        hour = min(out)
        if hour <= "090000":
            break
    return out


def five_minute_bars(rows, trade_date, *, corporate_action_today):
    """Aggregate 1-minute rows into 5-minute provider bars (bucket start, OHLCV).

    Buckets without any traded minute are omitted, never filled; the builder then
    reports a regular-session gap. ``corporate_action_today`` None = unknown.
    """
    buckets = {}
    for label, row in sorted(rows.items()):
        hour, minute = int(label[:2]), int(label[2:4])
        start = datetime(trade_date.year, trade_date.month, trade_date.day, hour, minute - minute % 5,
                         tzinfo=SEOUL)
        values = {k: float(row[f]) for k, f in (("open", "stck_oprc"), ("high", "stck_hgpr"),
                                                  ("low", "stck_lwpr"), ("close", "stck_prpr"))}
        volume = float(row.get("cntg_vol") or 0)
        bucket = buckets.get(start)
        if bucket is None:
            buckets[start] = dict(values, volume=volume)
        else:
            bucket.update(high=max(bucket["high"], values["high"]), low=min(bucket["low"], values["low"]),
                          close=values["close"], volume=bucket["volume"] + volume)
    action = None if corporate_action_today is None else (1 if corporate_action_today else 0)
    return [dict(provider_timestamp=_iso(start), dividends=action, stock_splits=action, **bucket)
            for start, bucket in sorted(buckets.items())]


def corporate_action_today(source, ticker, trade_date):
    """True/False from KIS 락구분 for today; None when it cannot be confirmed."""
    day = trade_date.strftime("%Y%m%d")
    try:
        frame = source.corporate_action_flags(ticker, (trade_date - timedelta(days=7)).strftime("%Y%m%d"), day)
    except Exception:  # noqa: BLE001 - unknown stays unknown
        return None
    today = frame[frame.index.strftime("%Y%m%d") == day]
    if today.empty:
        return None
    row = today.iloc[-1]
    return not (row["LockCode"] in ("00", "") and row["Modified"] != "Y")


def prior_closes(source, ticker, sessions):
    """Completed raw daily closes for the sessions before today {trade_date: close}."""
    days = [s["trade_date"] for s in sessions[:-1]]
    frame = source.price_history(ticker, days[0].replace("-", ""), days[-1].replace("-", ""), adjusted=False)
    closes = {ix.date().isoformat(): float(row["Close"]) for ix, row in frame.iterrows()}
    return {day: closes[day] for day in days if day in closes}


def quote(source, ticker):
    """Current KIS price; observed_at is the retrieval time (no exchange timestamp)."""
    output, observed = source._current_quote(ticker)
    price = float(output["stck_prpr"])
    if price <= 0:
        raise ValueError("KR_QUOTE_UNAVAILABLE")
    observed = observed.astimezone(timezone.utc).isoformat()
    return dict(price=str(int(price)) if price.is_integer() else str(price), observed_at=observed,
                source_ref=_hash(["kis-domestic-current-price", ticker, output.get("stck_prpr"), observed]))


class MarketSnapshot:
    """KR regime/pulse/pilot with explicit computation times; regime cached 15 min, pulse 5 min."""

    def __init__(self, clock=lambda: datetime.now(timezone.utc)):
        self.clock, self._regime, self._pulse = clock, None, None

    def __call__(self):
        now = self.clock()
        if self._regime is None or now - self._regime[0] > timedelta(minutes=15):
            from cores.data_prefetch import prefetch_macro_intelligence_data
            data = prefetch_macro_intelligence_data(now.astimezone(SEOUL).strftime("%Y%m%d")) or {}
            computed = data.get("computed_regime") or {}
            self._regime = (now, computed.get("market_regime"), computed.get("distribution_days"))
        if self._pulse is None or now - self._pulse[0] > timedelta(minutes=5):
            from cores import regime_policy
            self._pulse = (now, regime_policy.get_market_pulse_state("kr", use_cache=False),
                           bool(regime_policy.pilot_reexposure_active("kr", use_cache=False)))
        asof = dict(regime=_iso(self._regime[0]), pulse=_iso(self._pulse[0]))
        value = dict(observed_at=now.isoformat(), regime=self._regime[1], market_pulse=self._pulse[1],
                     distribution_days=self._regime[2], pilot_reexposure_active=self._pulse[2],
                     source_asof=asof)
        value["source_ref"] = _hash(["kr-market-snapshot", value])
        return value


def daily_bars(source, ticker, start_day, end_day):
    """Completed raw daily OHLCV [{date, open, high, low, close, volume}] between two ISO dates."""
    frame = source.price_history(ticker, start_day.replace("-", ""), end_day.replace("-", ""), adjusted=False)
    return [dict(date=ix.date().isoformat(), open=float(row["Open"]), high=float(row["High"]),
                 low=float(row["Low"]), close=float(row["Close"]), volume=float(row["Volume"]))
            for ix, row in frame.iterrows()]


PACE_SESSIONS = 12  # add_plan.MIN_PACE_SAMPLES (10) plus room for two incomplete sessions
PACE_PAGE_PAUSE = 0.15


def _pace_cache_path(ticker, sessions):
    root = Path(os.getenv("B3_AE_CACHE_DIR") or Path(__file__).resolve().parents[1] / "runtime/b3-ae-cache")
    return root / f"kr-pace-{ticker}-{sessions[-1]['trade_date']}-{len(sessions)}.json"


def prior_minute_volumes(source, ticker, sessions):
    """Per prior session [(minutes since open, volume)] from 1-minute rows; incomplete sessions are skipped.

    Feeds the add-plan volume pace (same elapsed time versus prior sessions). Only the
    last PACE_SESSIONS sessions are walked, paced, and cached on disk for the trading day
    so a worker restart does not repeat ~14 KIS calls per session.
    """
    sessions = list(sessions)[-PACE_SESSIONS:]
    if not sessions:
        return []
    path = _pace_cache_path(ticker, sessions)
    try:
        return [[tuple(point) for point in curve] for curve in json.loads(path.read_text())]
    except (OSError, ValueError, TypeError):
        pass
    curves = []
    for session in sessions:
        opened = datetime.fromisoformat(session["open_at"]).astimezone(SEOUL)
        rows = minute_rows(source, ticker, datetime.fromisoformat(session["close_at"]) - timedelta(seconds=1),
                           pause=PACE_PAGE_PAUSE)
        if not rows or min(rows) > opened.strftime("%H%M%S"):
            continue
        curve = []
        for label, row in rows.items():
            minute = int(label[:2]) * 60 + int(label[2:4]) - (opened.hour * 60 + opened.minute)
            if minute >= 0:
                curve.append((minute, float(row.get("cntg_vol") or 0)))
        curves.append(sorted(curve))
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(curves))
    except OSError:
        pass  # the in-memory day cache still holds the result
    return curves
