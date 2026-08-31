"""Build a deterministic BTC strategy evidence packet from decision and bar data."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pandas as pd

PACKET_SCHEMA_VERSION = 1
ANALYSIS_CONTRACT_VERSION = "btc-strategy-evidence-v1"
HORIZONS = {"30m": 1, "3h": 6, "6h": 12, "24h": 48, "7d": 336}
_TF_FIELDS = {"trend", "candle_position", "trend_strength"}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _json_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    try:
        parsed = json.loads(str(value or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, Mapping) else {}


def _safe_market_snapshot(value: Any) -> dict[str, Any]:
    raw = _json_mapping(value)
    tf_states = _json_mapping(raw.get("tf_states"))
    return {
        "evaluated_at": raw.get("evaluated_at"),
        "bar_close": raw.get("bar_close"),
        "alignment_score": raw.get("alignment_score"),
        "tf_states": {
            str(tf): {
                key: state.get(key)
                for key in sorted(_TF_FIELDS)
                if key in state
            }
            for tf, raw_state in sorted(tf_states.items())
            if (state := _json_mapping(raw_state))
        },
    }


def _safe_context(value: Any, allowed: set[str]) -> dict[str, Any]:
    raw = _json_mapping(value)
    return {key: raw.get(key) for key in sorted(allowed) if key in raw}


def _outcomes(bars: pd.DataFrame, ts: pd.Timestamp, base: float) -> dict[str, Any]:
    future = bars[bars.index > ts]
    results = {}
    for label, count in HORIZONS.items():
        if base <= 0 or len(future) < count:
            results[label] = {
                "status": "MISSING",
                "return_pct": None,
                "mfe_pct": None,
                "mae_pct": None,
            }
            continue
        window = future.iloc[:count]
        results[label] = {
            "status": "OK",
            "return_pct": round((float(window.close.iloc[-1]) / base - 1.0) * 100.0, 6),
            "mfe_pct": round((float(window.high.max()) / base - 1.0) * 100.0, 6),
            "mae_pct": round((float(window.low.min()) / base - 1.0) * 100.0, 6),
        }
    return results


def build_evidence_packet(
    decisions: Iterable[Mapping[str, Any]],
    bars_30m: pd.DataFrame,
) -> dict[str, Any]:
    """Return a secret-minimized, order-independent packet."""
    deduped: dict[str, dict[str, Any]] = {}
    duplicate_count = 0
    for raw in decisions:
        row = dict(raw)
        decision_id = str(row.get("decision_id") or "")
        if not decision_id:
            continue
        if decision_id in deduped:
            duplicate_count += 1
        deduped[decision_id] = row
    rows = []
    matured = Counter()
    modes = Counter()
    strategies = Counter()
    for raw in sorted(deduped.values(), key=lambda row: (str(row.get("ts")), str(row.get("decision_id")))):
        market = _safe_market_snapshot(raw.get("market_snapshot"))
        ts = pd.Timestamp(raw.get("ts"))
        ts = ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
        outcome = _outcomes(bars_30m, ts, float(market.get("bar_close") or 0))
        for label, value in outcome.items():
            matured[label] += value["status"] == "OK"
        mode = str(raw.get("mode") or "unknown")
        strategy = str(raw.get("strategy_id") or "unknown")
        modes[mode] += 1
        strategies[strategy] += 1
        rows.append(
            {
                "decision_id": str(raw.get("decision_id")),
                "ts": ts.isoformat().replace("+00:00", "Z"),
                "mode": mode,
                "schema_version": raw.get("schema_version"),
                "strategy_id": strategy,
                "code_version": raw.get("code_version"),
                "config_hash": raw.get("config_hash"),
                "input_hash": raw.get("input_hash"),
                "signal": {
                    "side": raw.get("signal_side"),
                    "strength": raw.get("signal_strength"),
                    "reason_code": raw.get("signal_reason_code"),
                },
                "entry": {
                    "status": raw.get("entry_status"),
                    "rejection_code": raw.get("entry_rejection_code"),
                    "context": _safe_context(
                        raw.get("entry_context"),
                        {"side", "limit_price", "qty", "leverage", "sl_price", "liq_price", "initial_risk", "current_tranche"},
                    ),
                },
                "market_snapshot": market,
                "position_context": _safe_context(
                    raw.get("position_context"),
                    {"n_open", "effective_n_open", "dust_position_count", "effective_total_qty", "equity", "peak_equity", "drawdown_pct", "pending_order"},
                ),
                "outcomes": outcome,
            }
        )
    packet = {
        "packet_schema_version": PACKET_SCHEMA_VERSION,
        "analysis_contract_version": ANALYSIS_CONTRACT_VERSION,
        "decision_count": len(rows),
        "duplicate_decision_count": duplicate_count,
        "coverage": {
            "mode_distribution": dict(sorted(modes.items())),
            "strategy_distribution": dict(sorted(strategies.items())),
            "matured_horizons": {label: matured[label] for label in HORIZONS},
        },
        "cost_contract": {
            "source": "captured config hash + canonical PRISM-BTC backtest constants",
            "comparison_requires_same_costs": True,
        },
        "decisions": rows,
        "readiness": {
            "automatic_shadow_forbidden": True,
            "automatic_live_forbidden": True,
            "causal_interpretation": "observational labels; strategy claims require preregistered replay",
        },
    }
    packet["packet_id"] = hashlib.sha256(_canonical(packet).encode("utf-8")).hexdigest()[:24]
    return packet


def _read_decisions(path: Path, mode: str | None) -> list[dict[str, Any]]:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    query = "SELECT * FROM btc_decision_log"
    params: tuple[Any, ...] = ()
    if mode:
        query += " WHERE mode=?"
        params = (mode,)
    query += " ORDER BY ts, decision_id"
    try:
        rows = [dict(row) for row in connection.execute(query, params).fetchall()]
    except sqlite3.OperationalError as error:
        if "no such table" not in str(error).lower():
            raise
        rows = []
    connection.close()
    return rows


def _read_bars(path: Path) -> pd.DataFrame:
    connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    frame = pd.read_sql_query(
        "SELECT open_time, open, high, low, close FROM klines "
        "WHERE timeframe='30m' AND confirmed=1 ORDER BY open_time",
        connection,
    )
    connection.close()
    frame.index = pd.to_datetime(frame.pop("open_time"), unit="ms", utc=True)
    return frame


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root-db", type=Path, required=True)
    parser.add_argument("--market-db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--mode", choices=("shadow", "demo", "live", "swing"))
    args = parser.parse_args(argv)
    packet = build_evidence_packet(
        _read_decisions(args.root_db, args.mode), _read_bars(args.market_db)
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(packet, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"packet_id": packet["packet_id"], "decision_count": packet["decision_count"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
