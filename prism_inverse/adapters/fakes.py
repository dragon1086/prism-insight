"""In-memory implementations for tests and replay wiring."""

from __future__ import annotations

from datetime import date
from typing import Optional

from prism_inverse.app.inputs import snapshot_from_bars, upto
from prism_inverse.domain.models import Bar, EtfSnapshot, EtfSpec, Instrument, Position
from prism_inverse.ports import Unavailable


class FakeMarketData:
    def __init__(self, bars: dict[str, list[Bar]], fail: frozenset = frozenset()):
        self.bars, self.fail = bars, fail

    def daily_bars(self, instrument: Instrument, start: date, end: date) -> list[Bar]:
        if instrument.code in self.fail or instrument.code not in self.bars:
            raise Unavailable(f"no bars for {instrument.code}")
        return [b for b in self.bars[instrument.code] if start <= b.day <= end]


class FakeEtfInfo:
    """Snapshot from the ETF's own bars, optionally overridden per code."""

    def __init__(self, etf_bars: dict[str, list[Bar]], overrides: Optional[dict[str, EtfSnapshot]] = None):
        self.etf_bars, self.overrides = etf_bars, overrides or {}

    def snapshot(self, spec: EtfSpec, day: date) -> EtfSnapshot:
        if spec.code in self.overrides:
            return self.overrides[spec.code]
        if spec.code not in self.etf_bars:
            raise Unavailable(f"no quote for {spec.code}")
        return snapshot_from_bars(spec, upto(self.etf_bars[spec.code], day))


class FakeLongLane:
    def __init__(self, tickers: frozenset = frozenset()):
        self._tickers = tickers

    def tickers(self, market: str) -> frozenset:
        return self._tickers


class FakeEvents:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    def emit(self, event_type: str, **fields) -> None:
        self.events.append((event_type, fields))


class MemoryStore:
    def __init__(self):
        self.runs, self.decisions, self.snapshots, self.market = {}, [], [], []
        self.positions: dict[str, dict] = {}
        self.signal_days: list[tuple] = []
        self.virtual: dict = {}

    def start_run(self, run_id, market, day, mode, policy_version, config_hash):
        self.runs[run_id] = {"status": "running", "market": market, "day": day}

    def finish_run(self, run_id, status, error=None):
        self.runs[run_id].update(status=status, error=error)

    def record_snapshot(self, run_id, spec, snap, error):
        self.snapshots.append((run_id, spec.code, snap, error))

    def record_market(self, run_id, underlying, pulse_today, pulse_yesterday, leader):
        self.market.append((run_id, underlying, pulse_today, pulse_yesterday, leader))

    def record_decision(self, run_id, decision_id, trace_id, d):
        self.decisions.append((run_id, decision_id, trace_id, d))

    def open_positions(self, market):
        return [r["pos"] for r in self.positions.values() if r["market"] == market and r["closed"] is None]

    def attempts(self, market):
        out: dict[str, int] = {}
        for r in self.positions.values():
            if r["market"] == market:
                out[r["underlying"]] = max(out.get(r["underlying"], 0), r["pos"].attempt_no)
        return out

    def open_position(self, market, pos: Position, trace_id):
        self.positions[pos.position_id] = {"market": market, "pos": pos, "closed": None,
                                           "underlying": pos.underlying, "trace_id": trace_id}

    def close_position(self, position_id, day, price, reason):
        self.positions[position_id]["closed"] = (day, price, reason)

    def record_signal_day(self, run_id, day, family, status, inputs, error):
        self.signal_days.append((run_id, day, family, status, inputs, error))

    def active_virtual_trades(self):
        return [t for t in self.virtual.values() if t.status != "CLOSED"]

    def save_virtual_trade(self, t):
        self.virtual[t.trade_id] = t

    def add_virtual_trade(self, t):
        return self.virtual.setdefault(t.trade_id, t) is t

    def live_positions(self, market):
        return [p for p in getattr(self, "live", {}).values() if p.market == market and p.status not in ("CLOSED", "REJECTED")]

    def save_live_position(self, p):
        self.__dict__.setdefault("live", {})[p.position_id] = p
