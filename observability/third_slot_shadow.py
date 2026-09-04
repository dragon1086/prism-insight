"""Fail-open KR weak-regime third-slot counterfactual observability."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from observability.events import DEFAULT_SPOOL_PATH, emit_event

POLICY_VERSION = "kr-weak-regime-third-slot-v1"
EVALUATION_EVENT = "screening.third_slot_shadow_evaluated"
OUTCOME_EVENT = "screening.third_slot_shadow_outcome"
HORIZONS = (1, 3, 5, 10)
_ROLES = ("LIVE_SELECTED", "LIVE_SELECTED", "SHADOW_THIRD")


def _stable(*parts: Any, length: int = 32) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:length]


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _yyyymmdd(value: Any) -> str | None:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    text = str(value or "").strip().split(" ", 1)[0]
    text = text.replace("-", "")
    if len(text) == 8 and text.isdigit():
        return text
    return None


def shadow_enabled(value: str | None = None) -> bool:
    raw = (
        value
        if value is not None
        else os.getenv("REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED", "false")
    )
    return str(raw or "").strip().lower() in {"1", "true", "yes", "on"}


def _normalize_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(candidates) != 3:
        raise ValueError("exactly three candidates are required")
    normalized = []
    seen = set()
    for index, candidate in enumerate(candidates, start=1):
        ticker = str(candidate.get("ticker") or "").strip().upper()
        price = _number(candidate.get("screening_price"))
        role = str(candidate.get("role") or _ROLES[index - 1])
        rank = int(candidate.get("rank") or index)
        if not ticker or price is None or ticker in seen:
            raise ValueError("candidate ticker and positive price must be unique")
        if rank != index or role != _ROLES[index - 1]:
            raise ValueError("candidate rank/role contract violated")
        seen.add(ticker)
        score = candidate.get("score")
        try:
            score = float(score) if score is not None else None
        except (TypeError, ValueError):
            score = None
        normalized.append(
            {
                "rank": rank,
                "role": role,
                "ticker": ticker,
                "company_name": str(candidate.get("company_name") or ""),
                "trigger_type": str(candidate.get("trigger_type") or ""),
                "screening_price": price,
                "score": score,
            }
        )
    return normalized


def build_evaluation_context(
    *,
    trade_date: str,
    trigger_mode: str,
    regime: str,
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    normalized_date = _yyyymmdd(trade_date)
    normalized_mode = str(trigger_mode or "").strip().lower()
    normalized_regime = str(regime or "").strip().lower()
    if normalized_date is None:
        raise ValueError("trade_date must be YYYYMMDD")
    if normalized_mode not in {"morning", "afternoon"}:
        raise ValueError("trigger_mode must be morning or afternoon")
    if normalized_regime not in {"sideways", "moderate_bear"}:
        raise ValueError("third-slot shadow is limited to weak regimes")
    normalized_candidates = _normalize_candidates(candidates)
    experiment_ref = _stable(
        "third-slot-experiment",
        POLICY_VERSION,
        normalized_date,
        normalized_mode,
    )
    return {
        "shadow_schema_version": 1,
        "mode": "SHADOW",
        "policy_version": POLICY_VERSION,
        "experiment_ref": experiment_ref,
        "reason_code": "WEAK_REGIME_TOTAL_CAP_2",
        "trade_date": normalized_date,
        "trigger_mode": normalized_mode,
        "regime": normalized_regime,
        "baseline_total_slots": 2,
        "counterfactual_total_slots": 3,
        "ranking_contract": "per-trigger-leader-then-overall-score-v1",
        "candidates": normalized_candidates,
        "trading_impact": "none",
        "analysis_requested": False,
        "order_requested": False,
        "message_requested": False,
    }


def emit_evaluation(
    *,
    trade_date: str,
    trigger_mode: str,
    regime: str,
    candidates: list[dict[str, Any]],
    spool_path: str | os.PathLike[str] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any] | None:
    if not (shadow_enabled() if enabled is None else bool(enabled)):
        return None
    try:
        context = build_evaluation_context(
            trade_date=trade_date,
            trigger_mode=trigger_mode,
            regime=regime,
            candidates=candidates,
        )
        third = context["candidates"][2]
        experiment_ref = context["experiment_ref"]
        event_id = _stable("third-slot-evaluation", experiment_ref)
        target_path = Path(spool_path or DEFAULT_SPOOL_PATH)
        existing = next(
            (
                event
                for event in _load_events(target_path)
                if event.get("event_id") == event_id
            ),
            None,
        )
        if existing is not None:
            return existing
        return emit_event(
            EVALUATION_EVENT,
            event_id=event_id,
            service="prism-kr-third-slot-shadow",
            market="KR",
            ticker=third["ticker"],
            trace_id=_stable("third-slot-trace", experiment_ref),
            decision_id=experiment_ref,
            attributes=context,
            spool_path=target_path,
        )
    except Exception:  # noqa: BLE001 — observability must not affect screening
        return None


def _load_events(path: Path) -> list[dict[str, Any]]:
    events = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(event, dict):
                    events.append(event)
    except OSError:
        pass
    return events


def _deduplicated(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id = {}
    for event in events:
        event_id = str(event.get("event_id") or "")
        if not event_id:
            continue
        existing = by_id.get(event_id)
        if existing is None or str(event.get("timestamp") or "") >= str(
            existing.get("timestamp") or ""
        ):
            by_id[event_id] = event
    return list(by_id.values())


def _frame_rows(frame: Any, *, after: str, through: str) -> list[dict[str, Any]]:
    if frame is None or not hasattr(frame, "iterrows"):
        return []
    columns = {str(column).lower(): column for column in getattr(frame, "columns", [])}
    close_column = columns.get("close") or columns.get("종가")
    high_column = columns.get("high") or columns.get("고가")
    low_column = columns.get("low") or columns.get("저가")
    if close_column is None or high_column is None or low_column is None:
        return []
    rows_by_date = {}
    for index, row in frame.iterrows():
        row_date = _yyyymmdd(index)
        if row_date is None or not (after < row_date <= through):
            continue
        close = _number(row[close_column])
        high = _number(row[high_column])
        low = _number(row[low_column])
        if close is None or high is None or low is None:
            continue
        rows_by_date[row_date] = {
            "date": row_date,
            "close": close,
            "high": high,
            "low": low,
        }
    return [rows_by_date[key] for key in sorted(rows_by_date)]


def _default_price_loader(ticker: str, start: str, end: str):
    from krx_data_client import get_market_ohlcv_by_date

    return get_market_ohlcv_by_date(start, end, ticker)


def _outcome_key(
    experiment_ref: str, ticker: str, horizon: int
) -> tuple[str, str, int]:
    return experiment_ref, ticker, horizon


def track_matured_outcomes(
    *,
    spool_path: str | os.PathLike[str] = DEFAULT_SPOOL_PATH,
    as_of: str | date | datetime | None = None,
    price_loader: Callable[[str, str, str], Any] | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Append exact 1/3/5/10-trading-day outcomes for shadow experiments."""
    path = Path(spool_path)
    through = _yyyymmdd(as_of or date.today())
    if through is None:
        raise ValueError("as_of must be YYYYMMDD")
    events = _deduplicated(_load_events(path))
    evaluations = [
        event for event in events if event.get("event_type") == EVALUATION_EVENT
    ]
    existing = set()
    for event in events:
        if event.get("event_type") != OUTCOME_EVENT:
            continue
        attributes = event.get("attributes") or {}
        try:
            key = _outcome_key(
                str(attributes.get("experiment_ref") or ""),
                str(event.get("ticker") or "").upper(),
                int(attributes.get("horizon_trading_days")),
            )
        except (TypeError, ValueError):
            continue
        existing.add(key)

    stats = {"evaluations": len(evaluations), "emitted": 0, "pending": 0, "errors": 0}
    loader = price_loader or _default_price_loader
    for evaluation in evaluations:
        attributes = evaluation.get("attributes") or {}
        experiment_ref = str(attributes.get("experiment_ref") or "")
        trade_date = _yyyymmdd(attributes.get("trade_date"))
        candidates = attributes.get("candidates") or []
        if not experiment_ref or trade_date is None or len(candidates) != 3:
            stats["errors"] += 1
            continue
        start = (
            datetime.strptime(trade_date, "%Y%m%d").date() + timedelta(days=1)
        ).strftime("%Y%m%d")
        for candidate in candidates:
            ticker = str(candidate.get("ticker") or "").upper()
            base_price = _number(candidate.get("screening_price"))
            missing = [
                horizon
                for horizon in HORIZONS
                if _outcome_key(experiment_ref, ticker, horizon) not in existing
            ]
            if not missing:
                continue
            if not ticker or base_price is None:
                stats["errors"] += 1
                continue
            try:
                rows = _frame_rows(
                    loader(ticker, start, through),
                    after=trade_date,
                    through=through,
                )
            except Exception:  # noqa: BLE001 — one ticker must not stop the batch
                stats["errors"] += 1
                continue
            for horizon in missing:
                if len(rows) < horizon:
                    stats["pending"] += 1
                    continue
                window = rows[:horizon]
                outcome = window[-1]
                context = {
                    "shadow_schema_version": 1,
                    "mode": "SHADOW",
                    "policy_version": POLICY_VERSION,
                    "experiment_ref": experiment_ref,
                    "trade_date": trade_date,
                    "trigger_mode": attributes.get("trigger_mode"),
                    "regime": attributes.get("regime"),
                    "candidate_role": candidate.get("role"),
                    "candidate_rank": candidate.get("rank"),
                    "trigger_type": candidate.get("trigger_type"),
                    "horizon_trading_days": horizon,
                    "outcome_date": outcome["date"],
                    "screening_price": base_price,
                    "close_price": outcome["close"],
                    "return_pct": round(
                        (outcome["close"] / base_price - 1.0) * 100.0, 6
                    ),
                    "mfe_pct": round(
                        (max(row["high"] for row in window) / base_price - 1.0)
                        * 100.0,
                        6,
                    ),
                    "mae_pct": round(
                        (min(row["low"] for row in window) / base_price - 1.0)
                        * 100.0,
                        6,
                    ),
                    "trading_impact": "none",
                }
                if dry_run:
                    stats["emitted"] += 1
                    continue
                event = emit_event(
                    OUTCOME_EVENT,
                    event_id=_stable(
                        "third-slot-outcome", experiment_ref, ticker, horizon
                    ),
                    service="prism-kr-third-slot-shadow-tracker",
                    market="KR",
                    ticker=ticker,
                    trace_id=_stable("third-slot-trace", experiment_ref),
                    decision_id=experiment_ref,
                    attributes=context,
                    spool_path=path,
                )
                if event is None:
                    stats["errors"] += 1
                else:
                    stats["emitted"] += 1
                    existing.add(_outcome_key(experiment_ref, ticker, horizon))
    return stats


__all__ = [
    "EVALUATION_EVENT",
    "HORIZONS",
    "OUTCOME_EVENT",
    "POLICY_VERSION",
    "build_evaluation_context",
    "emit_evaluation",
    "shadow_enabled",
    "track_matured_outcomes",
]
