"""KR inverse signal recorder: after the close, evaluate three signals and keep virtual trades.

No order path. Each run (1) advances open virtual trades with today's bars (fills pending
next-open entries/exits), then (2) evaluates KQ_F1, KQ_DRIVER, K200_DRIVER for ``day``. A signal
whose inputs are unavailable or lack a bar on ``day`` is MISSING, never "not fired".
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence

from prism_inverse.app.cycle import HISTORY_DAYS, trace_id
from prism_inverse.domain import signals as S
from prism_inverse.domain.models import Bar, Instrument
from prism_inverse.domain.virtual import CLOSED, VirtualTrade, advance
from prism_inverse.ports import EventSink, MarketData, SignalStore, Unavailable, Unsupported

SIGNALS_VERSION = "inverse-signals-kr-v1"
KOSDAQ, K200 = "229200", "069500"
# family -> (underlying the rules read, inverse ETF the virtual trade uses)
FAMILY_MAP = {"KQ_F1": (KOSDAQ, "251340"), "KQ_DRIVER": (KOSDAQ, "251340"), "K200_DRIVER": (K200, "114800")}
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


def evaluate(day: date, sectors: Sequence[str], bench: str, bars: dict, errors: dict) -> dict:
    """family -> (status, inputs, error). ``bars``: code -> bars up to ``day``; ``errors``: code -> fetch error."""
    def missing(codes):
        bad = [f"{c}: {errors[c]}" if c in errors else f"{c}: NO_BAR_ON_{day.isoformat()}"
               for c in codes if c in errors or not bars.get(c) or bars[c][-1].day != day]
        return "; ".join(bad) or None

    out = {}
    err = missing([KOSDAQ])
    if err:
        out["KQ_F1"] = (MISSING, {}, err)
    else:
        a = _ind(bars[KOSDAQ])
        fired = len(a["c"]) - 1 in S.kq_f1(a)
        out["KQ_F1"] = (FIRED if fired else NOT_FIRED, {
            "underlying": KOSDAQ, "close": _last(a, "c"), "ma50": _last(a, "ma50"), "atr": _last(a, "atr"),
            "peak": _last(a, "peak")}, None)

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


def run_signals(day: date, sectors: Sequence[str], bench: str, data: MarketData, store: SignalStore, events: EventSink,
                mode: str = "dry-run") -> SignalResult:
    run_id = f"invsig-KR-{day.isoformat()}-{uuid.uuid4().hex[:8]}"
    result = SignalResult(run_id)
    store.start_run(run_id, "KR", day, mode, SIGNALS_VERSION, config_hash(sectors, bench))
    try:
        codes = list(dict.fromkeys([*sectors, bench, KOSDAQ, *(e for _, e in FAMILY_MAP.values())]))
        bars = {}
        for code in codes:
            try:
                bars[code] = [b for b in data.daily_bars(Instrument("KR", code, "etf"),
                                                         day - timedelta(days=HISTORY_DAYS), day) if b.day <= day]
            except (Unavailable, Unsupported) as exc:
                result.data_errors[code] = f"{type(exc).__name__}: {exc}"
        calendar = sorted({b.day for series in bars.values() for b in series})
        by_day = {code: {b.day: b for b in series} for code, series in bars.items()}

        active = {}
        for t in store.active_virtual_trades():
            new = advance(t, calendar, by_day.get(t.etf, {}), by_day.get(t.underlying, {}))
            if new != t:
                store.save_virtual_trade(new)
            if t.entry_date is None and new.entry_date is not None:
                _emit(events, "inverse.virtual_entry", new, mode)
            if t.status != CLOSED and new.status == CLOSED:
                _emit(events, "inverse.virtual_exit", new, mode)
            result.trades.append(new)
            if new.status != CLOSED:
                active[new.family] = new

        result.days = evaluate(day, sectors, bench, bars, result.data_errors)
        for fam, (status, inputs, error) in result.days.items():
            underlying, etf = FAMILY_MAP[fam]
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
            events.emit("inverse.signal_evaluated", market="KR", ticker=etf, trace_id=trace_id("KR", tid),
                        decision_id=f"{run_id}-{fam}",
                        attributes={"family": fam, "status": status, "error": error, "mode": mode,
                                    "policy_version": SIGNALS_VERSION, **inputs})
        store.finish_run(run_id, "ok")
    except Exception as exc:
        store.finish_run(run_id, "error", f"{type(exc).__name__}: {exc}")
        raise
    return result


def _emit(events: EventSink, event_type: str, t: VirtualTrade, mode: str) -> None:
    events.emit(event_type, market="KR", ticker=t.etf, trace_id=trace_id("KR", t.trade_id), position_id=t.trade_id,
                attributes={**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in asdict(t).items()},
                            "mode": mode, "policy_version": SIGNALS_VERSION, "virtual": True})
