"""Inverse signal recorders (KR and US): after the close, evaluate the signals and keep virtual trades.

No order path. Each run (1) advances the market's open virtual trades with today's bars (fills
pending next-open entries/exits), then (2) evaluates every family for ``day``. A family whose
inputs are unavailable or lack a bar on ``day`` is MISSING, never "not fired".

KR (v3): KQ_F1, KQ_X10, KQ_DRIVER, K200_DRIVER, plus S18 KQ_FR (KOSDAQ crash switch + failed
rally) and K200_SEMI (semiconductor-sector crash switch + KOSPI200 re-break, -2x ETF).
US (v1): US_2BR_<sector> for 28 sector ETFs (crash switch + re-break), judged as synthetic -2x.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Callable, Mapping, Optional, Sequence

from prism_inverse.app.cycle import HISTORY_DAYS, trace_id
from prism_inverse.domain import signals as S
from prism_inverse.domain.models import Bar, Instrument
from prism_inverse.domain.virtual import CLOSED, KR_RULES, US_RULES, Rules, VirtualTrade, advance
from prism_inverse.ports import EventSink, MarketData, SignalStore, Unavailable, Unsupported

SIGNALS_VERSION = "inverse-signals-kr-v4"  # v3: S18 exits (1.5/3/trail 3/breakeven/10d/no-progress), + KQ_FR, K200_SEMI; v4: + KQ_RFAIL (observe)
US_SIGNALS_VERSION = "inverse-signals-us-v1"
KOSDAQ, K200, SEMI = "229200", "069500", "091160"
# family -> (underlying the rules read, inverse ETF the virtual trade uses)
FAMILY_MAP = {"KQ_F1": (KOSDAQ, "251340"), "KQ_DRIVER": (KOSDAQ, "251340"), "K200_DRIVER": (K200, "114800"),
              "KQ_X10": (KOSDAQ, "251340"), "KQ_FR": (KOSDAQ, "251340"), "K200_SEMI": (K200, "252670"),
              "KQ_RFAIL": (KOSDAQ, "251340")}   # S20 rally-fail, observe only (not in live_cycle.KR_LIVE_FAMILIES)
FIRED, NOT_FIRED, MISSING = "FIRED", "NOT_FIRED", "MISSING"


@dataclass
class SignalResult:
    run_id: str
    days: dict = field(default_factory=dict)        # family -> (status, inputs, error)
    trades: list = field(default_factory=list)      # VirtualTrade after this run
    data_errors: dict = field(default_factory=dict)


def config_hash(sectors: Sequence[str], bench: str) -> str:
    blob = json.dumps({"v": SIGNALS_VERSION, "sectors": list(sectors), "bench": bench, "map": FAMILY_MAP},
                      sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _ffill_closes(bars: Sequence[Bar], calendar: Sequence[date]) -> list[Optional[float]]:
    """Close on each calendar day; gaps after the first bar carry the last close (research ``ohlc``)."""
    by_day = {b.day: b.close for b in bars}
    out, last = [], None
    for d in calendar:
        last = by_day.get(d, last)
        out.append(last)
    return out


def _ind(bars: Sequence[Bar]) -> dict:
    return S.ind([b.open for b in bars], [b.high for b in bars], [b.low for b in bars], [b.close for b in bars])


def _last(a: dict, key: str):
    return a[key][-1] if a[key] else None


def _missing(day: date, bars: dict, errors: dict, codes) -> Optional[str]:
    bad = [f"{c}: {errors[c]}" if c in errors else f"{c}: NO_BAR_ON_{day.isoformat()}"
           for c in codes if c in errors or not bars.get(c) or bars[c][-1].day != day]
    return "; ".join(bad) or None


def evaluate(day: date, sectors: Sequence[str], bench: str, bars: dict, errors: dict) -> dict:
    """family -> (status, inputs, error). ``bars``: code -> bars up to ``day``; ``errors``: code -> fetch error."""
    def missing(codes):
        return _missing(day, bars, errors, codes)

    out = {}
    err = missing([KOSDAQ])
    if err:
        out["KQ_F1"] = out["KQ_X10"] = out["KQ_FR"] = out["KQ_RFAIL"] = (MISSING, {}, err)
    else:
        a = _ind(bars[KOSDAQ])
        t = len(a["c"]) - 1
        base = {"underlying": KOSDAQ, "close": _last(a, "c"), "atr": _last(a, "atr")}
        out["KQ_F1"] = (FIRED if t in S.kq_f1(a) else NOT_FIRED,
                        {**base, "ma50": _last(a, "ma50"), "peak": _last(a, "peak")}, None)
        out["KQ_X10"] = (FIRED if t in S.kq_x10(a) else NOT_FIRED, {**base, "ma10": _last(a, "ma10")}, None)
        out["KQ_FR"] = (FIRED if t in S.kq_fr(a) else NOT_FIRED,
                        {**base, "ma5": _last(a, "ma5"), "hi20": _last(a, "hi20"), "switch": S.crash_switch(a)[t]}, None)
        out["KQ_RFAIL"] = (FIRED if t in S.kq_rally_fail(a) else NOT_FIRED,
                           {**base, "ma20": _last(a, "ma20"), "high": _last(a, "h"), "switch": S.crash_switch(a)[t]}, None)

    err = missing([K200, SEMI])
    if err:
        out["K200_SEMI"] = (MISSING, {}, err)
    else:
        a, sa = _ind(bars[K200]), _ind(bars[SEMI])
        sw_by_day = dict(zip((b.day for b in bars[SEMI]), S.crash_switch(sa)))
        sw, on = [], False                      # the sector switch on each KOSPI200 session (carried over gaps)
        for b in bars[K200]:
            on = sw_by_day.get(b.day, on)
            sw.append(on)
        t = len(a["c"]) - 1
        out["K200_SEMI"] = (FIRED if t in S.switched_second_breaks(a, sw) else NOT_FIRED,
                            {"underlying": K200, "close": _last(a, "c"), "atr": _last(a, "atr"), "ma50": _last(a, "ma50"),
                             "peak": _last(a, "peak"), "switch": sw[t], "switch_source": SEMI,
                             "semi_close": _last(sa, "c"), "semi_hi20": _last(sa, "hi20"), "semi_atr": _last(sa, "atr")}, None)

    err = missing([bench, *sectors])  # every sector feeds the leader rank, so one gap blocks both
    if err:
        for fam in ("KQ_DRIVER", "K200_DRIVER"):
            out[fam] = (MISSING, {}, err)
        return out
    calendar = [b.day for b in bars[bench]]
    closes = {g: _ffill_closes(bars[g], calendar) for g in sectors}
    panel = S.sector_panel(closes, [b.close for b in bars[bench]])
    breaks = S.sector_breaks(closes, panel, rearm=True)
    t = len(calendar) - 1
    breaking = sorted(g for g, ix in breaks.items() if t in ix)
    for fam, code in (("KQ_DRIVER", KOSDAQ), ("K200_DRIVER", bench)):
        err = missing([code])
        if err:
            out[fam] = (MISSING, {}, err)
            continue
        g, r = S.driver_at(closes, panel["leader"], _ffill_closes(bars[code], calendar), t)
        a = _ind(bars[code])
        inputs = {"underlying": code, "driver": g, "corr": r, "breaking_sectors": breaking,
                  "close": _last(a, "c"), "ma50": _last(a, "ma50"), "atr": _last(a, "atr"), "peak": _last(a, "peak")}
        if g is not None:
            inputs.update(driver_close=closes[g][t], driver_ma50=panel["ma50"][g][t],
                          driver_peak=panel["peak"][g][t], driver_weak=panel["weak"][g][t])
        out[fam] = (FIRED if g is not None and t in breaks[g] else NOT_FIRED, inputs, None)
    return out


def us_family(ticker: str) -> str:
    return f"US_2BR_{ticker}"


def us_family_map(sectors: Mapping[str, str]) -> dict:
    return {us_family(t): (t, t) for t in sectors}


def us_config_hash(sectors: Mapping[str, str]) -> str:
    blob = json.dumps({"v": US_SIGNALS_VERSION, "sectors": dict(sectors)}, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def evaluate_us(day: date, sectors: Mapping[str, str], bars: dict, errors: dict) -> dict:
    """S17/S18 per sector ETF: crash switch (3 ATR under the 20-day high, off above MA20) + re-armed second break."""
    out = {}
    for ticker in sectors:
        fam, err = us_family(ticker), _missing(day, bars, errors, [ticker])
        if err:
            out[fam] = (MISSING, {}, err)
            continue
        a = _ind(bars[ticker])
        t, sw = len(a["c"]) - 1, S.crash_switch(a)
        out[fam] = (FIRED if t in S.switched_second_breaks(a, sw) else NOT_FIRED,
                    {"underlying": ticker, "close": _last(a, "c"), "atr": _last(a, "atr"), "ma50": _last(a, "ma50"),
                     "peak": _last(a, "peak"), "hi20": _last(a, "hi20"), "switch": sw[t]}, None)
    return out


def run_signals(day: date, sectors: Sequence[str], bench: str, data: MarketData, store: SignalStore, events: EventSink,
                mode: str = "dry-run") -> SignalResult:
    codes = list(dict.fromkeys([*sectors, bench, KOSDAQ, SEMI, *(e for _, e in FAMILY_MAP.values())]))
    return _run("KR", day, [Instrument("KR", c, "etf") for c in codes], FAMILY_MAP, KR_RULES, SIGNALS_VERSION,
                config_hash(sectors, bench), lambda bars, errors: evaluate(day, sectors, bench, bars, errors),
                data, store, events, mode)


def run_us_signals(day: date, sectors: Mapping[str, str], data: MarketData, store: SignalStore, events: EventSink,
                   mode: str = "dry-run") -> SignalResult:
    """``sectors``: ticker -> KIS exchange code (the lane never guesses an exchange)."""
    return _run("US", day, [Instrument("US", t, "etf", x) for t, x in sectors.items()], us_family_map(sectors),
                US_RULES, US_SIGNALS_VERSION, us_config_hash(sectors),
                lambda bars, errors: evaluate_us(day, sectors, bars, errors), data, store, events, mode)


def _run(market: str, day: date, instruments: Sequence[Instrument], family_map: dict, rules: Rules, version: str,
         cfg_hash: str, evaluate_fn: Callable[[dict, dict], dict], data: MarketData, store: SignalStore,
         events: EventSink, mode: str) -> SignalResult:
    run_id = f"invsig-{market}-{day.isoformat()}-{uuid.uuid4().hex[:8]}"
    result = SignalResult(run_id)
    store.start_run(run_id, market, day, mode, version, cfg_hash)
    try:
        bars = {}
        for inst in instruments:
            try:
                bars[inst.code] = [b for b in data.daily_bars(inst, day - timedelta(days=HISTORY_DAYS), day) if b.day <= day]
            except (Unavailable, Unsupported) as exc:
                result.data_errors[inst.code] = f"{type(exc).__name__}: {exc}"
        calendar = sorted({b.day for series in bars.values() for b in series})
        by_day = {code: {b.day: b for b in series} for code, series in bars.items()}

        active = {}
        for t in store.active_virtual_trades():
            if t.family not in family_map:      # the other market's book advances in its own run
                continue
            new = advance(t, calendar, by_day.get(t.etf, {}), by_day.get(t.underlying, {}), rules)
            if new != t:
                store.save_virtual_trade(new)
            if t.entry_date is None and new.entry_date is not None:
                _emit(events, "inverse.virtual_entry", new, market, version, mode)
            if t.status != CLOSED and new.status == CLOSED:
                _emit(events, "inverse.virtual_exit", new, market, version, mode)
            result.trades.append(new)
            if new.status != CLOSED:
                active[new.family] = new

        result.days = evaluate_fn(bars, result.data_errors)
        for fam, (status, inputs, error) in result.days.items():
            underlying, etf = family_map[fam]
            tid = f"{fam}-{day.isoformat()}"
            if status == FIRED:
                held = active.get(fam)
                if held is not None:
                    inputs["virtual"] = "ALREADY_RECORDED" if held.trade_id == tid else f"SKIPPED_OPEN:{held.trade_id}"
                elif inputs.get("atr") is None:
                    inputs["virtual"] = "MISSING_ATR"
                else:
                    trade = VirtualTrade(tid, fam, etf, underlying, day, inputs["atr"])
                    if store.add_virtual_trade(trade):  # False: a re-run of an old date, keep the recorded trade
                        result.trades.append(trade)
                        inputs["virtual"] = f"QUEUED:{tid}"
                    else:
                        inputs["virtual"] = "ALREADY_RECORDED"
            store.record_signal_day(run_id, day, fam, status, inputs, error)
            events.emit("inverse.signal_evaluated", market=market, ticker=etf, trace_id=trace_id(market, tid),
                        decision_id=f"{run_id}-{fam}",
                        attributes={"family": fam, "status": status, "error": error, "mode": mode,
                                    "policy_version": version, **inputs})
        store.finish_run(run_id, "ok")
    except Exception as exc:
        store.finish_run(run_id, "error", f"{type(exc).__name__}: {exc}")
        raise
    return result


def _emit(events: EventSink, event_type: str, t: VirtualTrade, market: str, version: str, mode: str) -> None:
    events.emit(event_type, market=market, ticker=t.etf, trace_id=trace_id(market, t.trade_id), position_id=t.trade_id,
                attributes={**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(t).items()},
                            "mode": mode, "policy_version": version, "virtual": True})
