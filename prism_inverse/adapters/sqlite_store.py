"""Lane DB (default ``runtime/inverse_lane.sqlite``), separate from the trading DB.

Deleting it never affects existing trading. No account numbers are stored. NULL stays NULL.
Order and fill tables arrive with the broker adapter (S5); this schema is version 1.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

from prism_inverse.domain.models import Decision, EtfSnapshot, EtfSpec, LeaderState, Position

SCHEMA_VERSION = 1
DEFAULT_PATH = Path(__file__).resolve().parents[2] / "runtime" / "inverse_lane.sqlite"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS inv_runs (
  run_id TEXT PRIMARY KEY, market TEXT, trade_date TEXT, mode TEXT, policy_version TEXT,
  config_hash TEXT, started_at TEXT, finished_at TEXT, status TEXT, error TEXT);
CREATE TABLE IF NOT EXISTS inv_market_state (
  run_id TEXT, underlying TEXT, pulse_today TEXT, pulse_yesterday TEXT,
  leader_status TEXT, leader_since TEXT, prior_gain REAL, drop_pct REAL, volume_ratio REAL);
CREATE TABLE IF NOT EXISTS inv_etf_snapshots (
  run_id TEXT, etf TEXT, lane TEXT, multiplier INTEGER, underlying TEXT, price REAL, tick REAL,
  value_20d REAL, premium_pct REAL, tracking_error REAL, error TEXT);
CREATE TABLE IF NOT EXISTS inv_decisions (
  decision_id TEXT PRIMARY KEY, run_id TEXT, trace_id TEXT, position_id TEXT, lane TEXT, etf TEXT,
  underlying TEXT, action TEXT, reason_code TEXT, underlying_close REAL, stop REAL, target REAL,
  qty INTEGER, inputs_json TEXT);
CREATE TABLE IF NOT EXISTS inv_positions (
  position_id TEXT PRIMARY KEY, trace_id TEXT, market TEXT, lane TEXT, etf TEXT, underlying TEXT,
  multiplier INTEGER, qty INTEGER, attempt_no INTEGER, entry_price REAL, entry_underlying REAL,
  stop REAL, target REAL, opened_at TEXT, closed_at TEXT, exit_price REAL, exit_reason TEXT);
"""


class SqliteStore:
    def __init__(self, path: Path = DEFAULT_PATH):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.executescript(_SCHEMA)
        if self.db.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0:
            self.db.execute("INSERT INTO schema_version VALUES (?)", (SCHEMA_VERSION,))
        self.db.commit()

    def _exec(self, sql: str, args: tuple) -> None:
        with self.db:
            self.db.execute(sql, args)

    def start_run(self, run_id, market, day: date, mode, policy_version, config_hash):
        self._exec("INSERT INTO inv_runs (run_id, market, trade_date, mode, policy_version, config_hash, "
                   "started_at, status) VALUES (?,?,?,?,?,?,?,?)",
                   (run_id, market, day.isoformat(), mode, policy_version, config_hash, _now(), "running"))

    def finish_run(self, run_id, status, error: Optional[str] = None):
        self._exec("UPDATE inv_runs SET finished_at=?, status=?, error=? WHERE run_id=?",
                   (_now(), status, error, run_id))

    def record_snapshot(self, run_id, spec: EtfSpec, snap: Optional[EtfSnapshot], error):
        s = snap or EtfSnapshot()
        self._exec("INSERT INTO inv_etf_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (run_id, spec.code, spec.lane, spec.multiplier, spec.underlying.code, s.price, s.tick,
                    s.value_20d, s.premium_pct, s.tracking_error, error))

    def record_market(self, run_id, underlying, pulse_today, pulse_yesterday, leader: Optional[LeaderState]):
        lv = leader or LeaderState(underlying, None, None)
        self._exec("INSERT INTO inv_market_state VALUES (?,?,?,?,?,?,?,?,?)",
                   (run_id, underlying, pulse_today, pulse_yesterday, lv.status,
                    lv.since.isoformat() if lv.since else None, lv.prior_gain, lv.drop_pct, lv.volume_ratio))

    def record_decision(self, run_id, decision_id, trace_id, d: Decision):
        self._exec("INSERT INTO inv_decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (decision_id, run_id, trace_id, d.position_id, d.lane, d.etf, d.underlying, d.action,
                    d.reason, d.underlying_close, d.stop_underlying, d.target_underlying, d.qty,
                    json.dumps(d.inputs, default=str, ensure_ascii=False)))

    def open_positions(self, market) -> list[Position]:
        rows = self.db.execute(
            "SELECT position_id, etf, lane, multiplier, qty, entry_price, entry_underlying, stop, target, "
            "opened_at, attempt_no, underlying FROM inv_positions WHERE market=? AND closed_at IS NULL "
            "ORDER BY opened_at", (market,)).fetchall()
        return [Position(r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], date.fromisoformat(r[9]),
                         r[10], r[11]) for r in rows]

    def attempts(self, market) -> dict[str, int]:
        rows = self.db.execute("SELECT underlying, MAX(attempt_no) FROM inv_positions WHERE market=? "
                               "GROUP BY underlying", (market,)).fetchall()
        return {u: n for u, n in rows if u}

    def open_position(self, market, pos: Position, trace_id):
        self._exec("INSERT INTO inv_positions (position_id, trace_id, market, lane, etf, underlying, multiplier, "
                   "qty, attempt_no, entry_price, entry_underlying, stop, target, opened_at) "
                   "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (pos.position_id, trace_id, market, pos.lane, pos.etf, pos.underlying, pos.multiplier,
                    pos.qty, pos.attempt_no, pos.entry_price, pos.entry_underlying, pos.stop_underlying,
                    pos.target_underlying, pos.opened.isoformat()))

    def close_position(self, position_id, day: date, price, reason):
        self._exec("UPDATE inv_positions SET closed_at=?, exit_price=?, exit_reason=? WHERE position_id=?",
                   (day.isoformat(), price, reason, position_id))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
