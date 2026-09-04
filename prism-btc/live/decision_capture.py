"""Versioned, fail-open metadata for replayable BTC strategy decisions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

from backtest.engine import MAKER_FEE, SLIPPAGE_SL, TAKER_FEE
from core.entries import EntryEvaluation
from engine import config, sizing
from engine.regime import RegimeSnapshot
from engine.signal import Signal, trend_strength
from live import tracking

DECISION_SCHEMA_VERSION = 2
DEFAULT_STRATEGY_ID = "main_trend_v1"
_EFFECTIVE_QTY_EPS = 1e-8


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:24]


def _config_snapshot() -> dict[str, Any]:
    return {
        "entry_score_min": config.ENTRY_SCORE_MIN,
        "ts_min": config.TS_MIN,
        "ts_gate_tfs": list(config.TS_GATE_TFS),
        "tf_weights": dict(config.TF_WEIGHTS),
        "entry_trigger_tf": "4h",
        "risk_per_trade": sizing.RISK_PER_TRADE,
        "leverage_mode": sizing.LEVERAGE_MODE,
        "fixed_leverage": sizing.FIXED_LEVERAGE,
        "tranche_fractions": list(sizing.TRANCHE_FRACS),
        "maker_fee": MAKER_FEE,
        "taker_fee": TAKER_FEE,
        "stop_slippage": SLIPPAGE_SL,
    }


def _signal_reason_code(signal: Signal) -> str:
    reason = str(signal.reason or "").lower()
    if signal.side != "none":
        return "SIGNAL_ACCEPTED"
    if "score=" in reason or "횡보관망" in reason:
        return "SCORE_BELOW_MIN"
    if "추세강도" in reason:
        return "TREND_STRENGTH_GATE"
    if "장기tf" in reason:
        return "LONG_TF_MISALIGNED"
    if "4h 타이밍" in reason:
        return "ENTRY_TF_MISALIGNED"
    if "미확정" in reason:
        return "ENTRY_CADENCE_NOT_READY"
    return "SIGNAL_NONE_OTHER"


def _entry_reason_code(reason: str | None) -> str | None:
    normalized = str(reason or "").strip().lower()
    if not normalized or normalized == "accepted":
        return None
    if normalized.startswith("cooldown"):
        return "COOLDOWN"
    if normalized == "4h_hardcap":
        return "FOUR_HOUR_HARDCAP"
    if normalized.startswith("pyramid_"):
        return "PYRAMID_GATE"
    if normalized == "max_tranches":
        return "MAX_TRANCHES"
    if "sizing" in normalized or "버퍼" in normalized or "sl" in normalized:
        return "SIZING_OR_LIQUIDATION_BUFFER"
    if normalized == "pending_order":
        return "PENDING_ORDER"
    if normalized == "max_positions":
        return "MAX_POSITIONS"
    if normalized == "failure_guard_c1":
        return "FAILURE_GUARD_C1"
    return "ENTRY_REJECTED_OTHER"


def _market_snapshot(
    snapshot: RegimeSnapshot,
    bar_close: float,
    factor_snapshot: dict[str, Any] | None,
) -> dict[str, Any]:
    states = {}
    for timeframe, state in sorted(snapshot.tf_states.items()):
        states[timeframe] = {
            "trend": state.trend,
            "candle_position": state.candle_position,
            "ma10": state.ma10,
            "ma35": state.ma35,
            "close": state.close,
            "atr14": state.atr14,
            "trend_strength": round(trend_strength(state), 6),
        }
    return {
        "evaluated_at": snapshot.evaluated_at,
        "bar_close": float(bar_close),
        "alignment_score": round(float(snapshot.alignment_score), 4),
        "tf_states": states,
        "ohlcv_factors": factor_snapshot or {
            "schema_version": 1,
            "status": "unavailable",
            "timeframes": {},
        },
    }


def _position_context(
    positions: Iterable[Any],
    *,
    equity: float,
    peak_equity: float | None,
    pending: bool,
) -> dict[str, Any]:
    rows = list(positions)
    effective = [row for row in rows if abs(float(getattr(row, "qty", 0) or 0)) > _EFFECTIVE_QTY_EPS]
    sides = {
        side: sum(str(getattr(row, "side", "")) == side for row in effective)
        for side in ("long", "short")
    }
    peak = float(peak_equity) if peak_equity not in (None, 0) else float(equity)
    drawdown = max(0.0, (peak - float(equity)) / peak) if peak > 0 else 0.0
    return {
        "n_open": len(rows),
        "effective_n_open": len(effective),
        "dust_position_count": len(rows) - len(effective),
        "effective_total_qty": round(
            sum(abs(float(getattr(row, "qty", 0) or 0)) for row in effective), 12
        ),
        "side_counts": sides,
        "pending_order": bool(pending),
        "equity": round(float(equity), 6),
        "peak_equity": round(peak, 6),
        "drawdown_pct": round(drawdown * 100.0, 6),
    }


def capture_signal_decision(
    conn,
    *,
    ts: str,
    mode: str,
    strategy_id: str,
    snapshot: RegimeSnapshot,
    signal: Signal,
    bar_close: float,
    positions: Iterable[Any],
    equity: float,
    peak_equity: float | None,
    pending: bool,
    code_version: str | None,
    factor_snapshot: dict[str, Any] | None = None,
) -> str:
    """Upsert the signal stage and return its stable decision ID."""
    config_snapshot = _config_snapshot()
    market = _market_snapshot(snapshot, bar_close, factor_snapshot)
    position = _position_context(
        positions, equity=equity, peak_equity=peak_equity, pending=pending
    )
    config_hash = _hash(config_snapshot)
    input_hash = _hash(
        {"market": market, "position": position, "config_hash": config_hash}
    )
    decision_id = _hash(
        {"strategy_id": strategy_id, "mode": mode, "ts": str(ts)}
    )
    now = tracking._utcnow()
    signal_code = _signal_reason_code(signal)
    tracking.upsert_decision_log(
        conn,
        {
            "decision_id": decision_id,
            "ts": str(ts),
            "mode": mode,
            "schema_version": DECISION_SCHEMA_VERSION,
            "strategy_id": strategy_id,
            "code_version": code_version,
            "config_hash": config_hash,
            "input_hash": input_hash,
            "signal_side": signal.side,
            "signal_strength": float(signal.strength),
            "signal_reason_code": signal_code,
            "signal_reason": str(signal.reason or "")[:200],
            "entry_status": (
                "SIGNAL_REJECTED" if signal.side == "none" else "PENDING_EVALUATION"
            ),
            "entry_rejection_code": signal_code if signal.side == "none" else None,
            "entry_reason": str(signal.reason or "")[:200]
            if signal.side == "none"
            else None,
            "market_snapshot": _canonical(market),
            "position_context": _canonical(position),
            "entry_context": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    return decision_id


def finalize_entry_decision(
    conn,
    decision_id: str,
    evaluation: EntryEvaluation | None,
    *,
    current_tranche: int,
    forced_reason: str | None = None,
) -> None:
    """Finalize hardcap/cooldown/pyramid/sizing outcome for one signal."""
    reason = forced_reason or (evaluation.reason if evaluation is not None else "not_evaluated")
    intent = evaluation.intent if evaluation is not None and forced_reason is None else None
    if intent is None:
        tracking.update_decision_entry(
            conn,
            decision_id,
            entry_status="REJECTED",
            rejection_code=_entry_reason_code(reason),
            reason=reason,
            entry_context={"current_tranche": int(current_tranche)},
        )
        return
    result = intent.sizing
    tracking.update_decision_entry(
        conn,
        decision_id,
        entry_status="ACCEPTED",
        rejection_code=None,
        reason="accepted",
        entry_context={
            "side": intent.side,
            "limit_price": float(intent.limit_price),
            "qty": float(result.qty),
            "leverage": float(result.leverage),
            "sl_price": float(result.sl_price),
            "tp1_price": float(result.tp1_price),
            "tp2_price": float(result.tp2_price),
            "tp3_price": float(result.tp3_price),
            "liq_price": float(result.liq_price),
            "initial_risk": float(intent.initial_risk),
            "current_tranche": int(current_tranche),
        },
    )


__all__ = [
    "DECISION_SCHEMA_VERSION",
    "DEFAULT_STRATEGY_ID",
    "capture_signal_decision",
    "finalize_entry_decision",
]
