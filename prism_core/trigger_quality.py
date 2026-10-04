"""Data-driven trigger priority for final screening selection (KR/US shared).

PRISM's north star is trend following that depends on a few big winners, so the
limited final slots should go first to triggers that historically find runners
without bleeding stop-out cost. This module turns PRISM's own read-only history
into one bounded weight per trigger type:

* Runner component: share of analysed candidates (analysis performance tracker,
  traded or not) whose best tracked 7/14/30-day return reached
  ``RUNNER_THRESHOLD``. Daily prices are not stored offline, so the three
  tracked checkpoints are the runner proxy.
* P&L component: realized ``profit_rate`` of executed trades in full-slot units
  (``prism_core.slot_weight``), which carries the real stop-out cost.

Both components are shrunk toward the market-wide mean with ``PRIOR_STRENGTH``
pseudo-observations, so a trigger with few samples stays close to weight 1.0.
The weight is ``1 + WEIGHT_SPAN * tanh(quality)`` and therefore always stays
inside ``(1 - WEIGHT_SPAN, 1 + WEIGHT_SPAN)``; no trigger is ever removed.

The weight acts only in final selection: it scales scores wherever candidates
of different triggers already compete (top-down pool, fill pass), and a
trigger at or below ``GUARANTEE_FLOOR`` loses its per-trigger guaranteed pick
but still competes for the remaining slots. It never changes trigger
detection, scores written to JSON, BUY prompts or gates.
Any data problem fails open to an empty map (every trigger weight 1.0), which
reproduces the legacy selection exactly. Kill switch: TRIGGER_QUALITY_PRIORITY.
"""
from __future__ import annotations

import datetime as _dt
import logging
import math
import os
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from prism_core.slot_weight import weighted_profit_rate

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ENV_FLAG = "TRIGGER_QUALITY_PRIORITY"
VERSION = "trigger_quality_v1"

WINDOW_DAYS = 180
# Best tracked 7/14/30d close at or above +20% counts as a runner. The +30%
# within 60 sessions definition needs daily prices that are not stored offline;
# +20% inside ~21 sessions is the closest checkpoint-based proxy.
RUNNER_THRESHOLD = 0.20
# A trigger needs about PRIOR_STRENGTH observations before its own rate moves
# halfway away from the market-wide mean.
PRIOR_STRENGTH = 20.0
# One quality unit = runner-rate edge of 10 percentage points, or an average
# realized edge of 3 percentage points per full slot.
RUNNER_SCALE = 0.10
PNL_SCALE = 3.0
WEIGHT_SPAN = 0.30
# Weight at or below this loses the per-trigger guaranteed pick (still competes
# in the weighted fill). 0.95 = quality <= -0.17 units after shrinkage, i.e.
# measurably below the market mean rather than noise around it.
GUARANTEE_FLOOR = 0.95

_OFF_VALUES = {"0", "false", "no", "off"}

# Fully static SQL per market; only bound parameters vary.
_CANDIDATE_SQL = {
    "KR": (
        "SELECT trigger_type, tracked_7d_return, tracked_14d_return, tracked_30d_return "
        "FROM analysis_performance_tracker "
        "WHERE trigger_type IS NOT NULL AND trigger_type <> '' "
        "AND tracked_30d_return IS NOT NULL "
        "AND analyzed_date >= ? AND analyzed_date < ?"
    ),
    "US": (
        "SELECT trigger_type, return_7d, return_14d, return_30d "
        "FROM us_analysis_performance_tracker "
        "WHERE trigger_type IS NOT NULL AND trigger_type <> '' "
        "AND return_30d IS NOT NULL "
        "AND analysis_date >= ? AND analysis_date < ?"
    ),
}
_ACTUAL_SQL = {
    "KR": (
        "SELECT trigger_type, profit_rate, scenario FROM trading_history "
        "WHERE trigger_type IS NOT NULL AND trigger_type <> '' "
        "AND profit_rate IS NOT NULL AND sell_date >= ? AND sell_date < ?"
    ),
    "US": (
        "SELECT trigger_type, profit_rate, scenario FROM us_trading_history "
        "WHERE trigger_type IS NOT NULL AND trigger_type <> '' "
        "AND profit_rate IS NOT NULL AND sell_date >= ? AND sell_date < ?"
    ),
}

_CACHE: dict[tuple[str, str, str], "TriggerQualitySnapshot"] = {}


@dataclass(frozen=True)
class TriggerQualitySnapshot:
    """Per-batch trigger weights plus the evidence used to compute them."""

    market: str
    as_of: str
    status: str
    weights: dict[str, float] = field(default_factory=dict)
    details: dict[str, dict[str, Any]] = field(default_factory=dict)
    prior: dict[str, Any] = field(default_factory=dict)
    reason: str | None = None

    def weight(self, trigger: str) -> float:
        return self.weights.get(trigger, 1.0)

    def to_metadata(self) -> dict[str, Any]:
        return {
            "version": VERSION,
            "status": self.status,
            "as_of": self.as_of,
            "window_days": WINDOW_DAYS,
            "prior_strength": PRIOR_STRENGTH,
            "runner_threshold": RUNNER_THRESHOLD,
            "weights": dict(self.weights),
            "prior": dict(self.prior),
            "reason": self.reason,
        }


def priority_enabled(value: str | None = None) -> bool:
    raw = value if value is not None else os.getenv(ENV_FLAG, "true")
    return str(raw or "true").strip().lower() not in _OFF_VALUES


def _db_path(db_path: str | Path | None) -> Path:
    if db_path is not None:
        return Path(db_path)
    configured = os.getenv("TRIGGER_QUALITY_DB") or os.getenv("STOCK_TRACKING_DB")
    return Path(configured) if configured else PROJECT_ROOT / "stock_tracking_db.sqlite"


def _as_of_date(as_of: Any) -> _dt.date:
    if isinstance(as_of, _dt.datetime):
        return as_of.date()
    if isinstance(as_of, _dt.date):
        return as_of
    if as_of:
        text = str(as_of).strip()[:10].replace("-", "")
        return _dt.datetime.strptime(text, "%Y%m%d").date()
    return _dt.date.today()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _shrunk(total: float, n: int, prior: float) -> float:
    return (total + PRIOR_STRENGTH * prior) / (n + PRIOR_STRENGTH)


def _read_rows(connection: sqlite3.Connection, sql: str, window: tuple[str, str]) -> list[tuple]:
    try:
        return connection.execute(sql, window).fetchall()
    except sqlite3.OperationalError as error:
        # A missing table or column leaves this component at the prior.
        logger.info("[TRIGGER_QUALITY] source unavailable: %s", error)
        return []


def compute_weights(
    candidate_rows: Iterable[tuple],
    actual_rows: Iterable[tuple],
) -> tuple[dict[str, float], dict[str, dict[str, Any]], dict[str, Any]]:
    """Pure computation from (trigger, r7, r14, r30) and (trigger, pnl, scenario)."""
    runners: dict[str, list[int]] = {}
    for trigger, *returns in candidate_rows:
        values = [v for v in (_finite(r) for r in returns) if v is not None]
        if not values:
            continue
        stats = runners.setdefault(str(trigger), [0, 0])
        stats[0] += 1
        stats[1] += int(max(values) >= RUNNER_THRESHOLD)

    pnl: dict[str, list[float]] = {}
    for trigger, profit_rate, scenario in actual_rows:
        if _finite(profit_rate) is None:
            continue
        stats = pnl.setdefault(str(trigger), [0, 0.0])
        stats[0] += 1
        stats[1] += weighted_profit_rate(profit_rate, scenario)

    candidate_n = sum(v[0] for v in runners.values())
    trade_n = sum(int(v[0]) for v in pnl.values())
    prior_rate = sum(v[1] for v in runners.values()) / candidate_n if candidate_n else None
    prior_pnl = sum(v[1] for v in pnl.values()) / trade_n if trade_n else None
    prior = {
        "candidate_n": candidate_n,
        "runner_rate": None if prior_rate is None else round(prior_rate, 4),
        "trade_n": trade_n,
        "avg_pnl_pct": None if prior_pnl is None else round(prior_pnl, 4),
    }

    weights: dict[str, float] = {}
    details: dict[str, dict[str, Any]] = {}
    for trigger in sorted(set(runners) | set(pnl)):
        n_c, hits = runners.get(trigger, [0, 0])
        n_t, pnl_sum = pnl.get(trigger, [0, 0.0])
        n_t = int(n_t)
        runner_units = 0.0
        pnl_units = 0.0
        shrunk_rate = None
        shrunk_pnl = None
        if prior_rate is not None and n_c:
            shrunk_rate = _shrunk(hits, n_c, prior_rate)
            runner_units = (shrunk_rate - prior_rate) / RUNNER_SCALE
        if prior_pnl is not None and n_t:
            shrunk_pnl = _shrunk(pnl_sum, n_t, prior_pnl)
            pnl_units = (shrunk_pnl - prior_pnl) / PNL_SCALE
        quality = 0.5 * runner_units + 0.5 * pnl_units
        weight = round(1.0 + WEIGHT_SPAN * math.tanh(quality), 4)
        weights[trigger] = weight
        details[trigger] = {
            "weight": weight,
            "n": n_c + n_t,
            "candidate_n": n_c,
            "runners": hits,
            "runner_rate": round(hits / n_c, 4) if n_c else None,
            "shrunk_runner_rate": None if shrunk_rate is None else round(shrunk_rate, 4),
            "trade_n": n_t,
            "avg_pnl_pct": round(pnl_sum / n_t, 4) if n_t else None,
            "shrunk_avg_pnl_pct": None if shrunk_pnl is None else round(shrunk_pnl, 4),
            "quality": round(quality, 4),
        }
    return weights, details, prior


def _compute_snapshot(market: str, as_of: _dt.date, path: Path) -> TriggerQualitySnapshot:
    as_of_text = as_of.isoformat()
    if not path.is_file():
        return TriggerQualitySnapshot(market, as_of_text, "unavailable", reason="db_missing")
    window = ((as_of - _dt.timedelta(days=WINDOW_DAYS)).isoformat(), as_of_text)
    connection = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        candidate_rows = _read_rows(connection, _CANDIDATE_SQL[market], window)
        actual_rows = _read_rows(connection, _ACTUAL_SQL[market], window)
    finally:
        connection.close()
    weights, details, prior = compute_weights(candidate_rows, actual_rows)
    if not weights:
        return TriggerQualitySnapshot(market, as_of_text, "unavailable", prior=prior, reason="no_history")
    return TriggerQualitySnapshot(market, as_of_text, "ok", weights, details, prior)


def load_trigger_quality(
    market: str,
    as_of: Any = None,
    *,
    db_path: str | Path | None = None,
    use_cache: bool = True,
) -> TriggerQualitySnapshot:
    """Return the day's trigger weights; never raises (fail-open to weight 1.0)."""
    market_key = str(market or "").strip().upper()
    try:
        as_of_date = _as_of_date(as_of)
    except ValueError:
        as_of_date = _dt.date.today()
    as_of_text = as_of_date.isoformat()
    if not priority_enabled():
        return TriggerQualitySnapshot(market_key, as_of_text, "disabled")
    if market_key not in _CANDIDATE_SQL:
        return TriggerQualitySnapshot(market_key, as_of_text, "unavailable", reason="unsupported_market")
    path = _db_path(db_path)
    key = (market_key, as_of_text, str(path))
    if use_cache and key in _CACHE:
        return _CACHE[key]
    try:
        snapshot = _compute_snapshot(market_key, as_of_date, path)
    except Exception as error:  # noqa: BLE001 - priority must never block screening
        logger.warning("[TRIGGER_QUALITY] fail-open (weight 1.0): %s", type(error).__name__)
        snapshot = TriggerQualitySnapshot(market_key, as_of_text, "unavailable", reason=type(error).__name__)
    _CACHE[key] = snapshot
    return snapshot


def log_snapshot(snapshot: TriggerQualitySnapshot, log: logging.Logger | None = None) -> None:
    target = log or logger
    if snapshot.status != "ok":
        target.info(
            "[TRIGGER_QUALITY] market=%s status=%s reason=%s -> all weights 1.0",
            snapshot.market, snapshot.status, snapshot.reason,
        )
        return
    target.info(
        "[TRIGGER_QUALITY] market=%s as_of=%s prior_runner=%s prior_pnl=%s",
        snapshot.market, snapshot.as_of,
        snapshot.prior.get("runner_rate"), snapshot.prior.get("avg_pnl_pct"),
    )
    for trigger, detail in sorted(snapshot.details.items(), key=lambda item: -item[1]["weight"]):
        target.info(
            "[TRIGGER_QUALITY] trigger=%s weight=%.3f n=%d runners=%d/%d trades=%d avg_pnl=%s",
            trigger, detail["weight"], detail["n"], detail["runners"], detail["candidate_n"],
            detail["trade_n"], detail["avg_pnl_pct"],
        )


def weighted_score(score: Any, weight: float) -> float:
    """Scale a selection score by a trigger weight, preserving sign semantics."""
    value = _finite(score)
    if value is None:
        return 0.0
    return value * weight if value >= 0 else value / weight


def guaranteed_pick_triggers(
    trigger_names: Iterable[str],
    weights: Mapping[str, float] | None,
) -> list[str]:
    """Triggers that keep the per-trigger guaranteed pick, in registration order.

    The per-trigger top-1 pass guarantees every trigger one slot in
    registration order, which is how weak triggers consumed the few final
    slots. A trigger whose shrunk evidence is materially below average
    (weight <= GUARANTEE_FLOOR) loses only that guarantee: its candidates still
    compete for every remaining slot by ``weight * score`` in the fill pass, so
    it is picked whenever it is the best available. Neutral weights (fail-open,
    disabled, no history) return the legacy list unchanged.
    """
    names = list(trigger_names)
    if not weights:
        return names
    return [name for name in names if weights.get(name, 1.0) > GUARANTEE_FLOOR]


def _cell(frame: Any, ticker: Any, columns: Iterable[str]) -> Any:
    for column in columns:
        if column in frame.columns:
            value = frame.loc[[ticker], column].iloc[0]
            return value.item() if hasattr(value, "item") else value
    return None


def _candidate(name: str, ticker: Any, frame: Any, weights: Mapping[str, float], score_column: str,
               price_columns: Iterable[str], name_columns: Iterable[str]) -> dict[str, Any]:
    weight = float(weights.get(name, 1.0))
    score = _finite(_cell(frame, ticker, [score_column]))
    return {
        "ticker": str(ticker),
        "name": str(_cell(frame, ticker, name_columns) or ""),
        "trigger": name,
        "score": score,
        "weight": round(weight, 4),
        "weighted_score": weighted_score(score, weight) if score is not None else None,
        "reference_price": _finite(_cell(frame, ticker, price_columns)),
    }


def selection_record(
    trigger_candidates: Mapping[str, Any],
    guaranteed: Iterable[str],
    before_guarantee: Iterable[Any],
    selected: Iterable[Any],
    fill_picks: Iterable[tuple[str, Any]],
    weights: Mapping[str, float] | None,
    score_column: str,
    *,
    max_selections: int,
    price_columns: Iterable[str] = ("Close",),
    name_columns: Iterable[str] = ("stock_name", "CompanyName"),
) -> dict[str, Any] | None:
    """Evidence of what the weighted selection changed (no I/O; None when weights are neutral).

    ``displaced``: candidates the legacy per-trigger pass (every trigger guaranteed, in
    registration order, until ``max_selections``) would have taken for a trigger that lost
    its guarantee, when they did not end up selected. ``fill_picks``: candidates chosen by
    the weighted fill. Reference prices let a later close judge both groups. The legacy
    pass is replayed from the same top-down picks (``before_guarantee``).
    """
    if not weights:
        return None
    keep, taken, chosen = set(guaranteed), set(before_guarantee), set(selected)
    displaced = []
    for name, frame in trigger_candidates.items():
        if len(taken) >= max_selections:
            break
        if frame is None or frame.empty:
            continue
        ordered = frame.sort_values(score_column, ascending=False) if score_column in frame.columns else frame
        legacy_pick = next((ticker for ticker in ordered.index if ticker not in taken), None)
        if legacy_pick is None:
            continue
        taken.add(legacy_pick)
        if name not in keep and legacy_pick not in chosen:
            displaced.append(_candidate(name, legacy_pick, ordered, weights, score_column, price_columns,
                                        name_columns))
    picks = [_candidate(name, ticker, trigger_candidates[name], weights, score_column, price_columns, name_columns)
             for name, ticker in fill_picks if name in trigger_candidates]
    return {
        "version": VERSION,
        "excluded_triggers": [name for name in trigger_candidates if name not in keep],
        "displaced": displaced,
        "fill_picks": picks,
        "selected_count": len(chosen),
    }


def emit_selection_record(
    record: Mapping[str, Any] | None,
    *,
    market: str,
    trade_date: Any,
    trigger_mode: str | None = None,
    log: logging.Logger | None = None,
) -> None:
    """Log and append the selection evidence to the observability spool (fail-open)."""
    if not record:
        return
    target = log or logger
    for item in record.get("displaced") or []:
        target.info(
            "[TRIGGER_QUALITY] displaced ticker=%s trigger=%s score=%s weight=%s price=%s trade_date=%s",
            item["ticker"], item["trigger"], item["score"], item["weight"], item["reference_price"], trade_date,
        )
    from observability.events import emit_event
    emit_event(
        "trigger_quality.selection",
        service=f"prism-{str(market).lower()}-trigger-quality",
        market=str(market).upper(),
        attributes={"trade_date": str(trade_date), "trigger_mode": trigger_mode, **dict(record)},
    )


__all__ = [
    "ENV_FLAG",
    "GUARANTEE_FLOOR",
    "TriggerQualitySnapshot",
    "compute_weights",
    "emit_selection_record",
    "guaranteed_pick_triggers",
    "load_trigger_quality",
    "log_snapshot",
    "priority_enabled",
    "selection_record",
    "weighted_score",
]
