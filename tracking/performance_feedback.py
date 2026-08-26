"""Candidate-vs-actual trigger performance feedback (read-only).

Candidate forward returns live in the analysis performance trackers as decimal
ratios.  Executed outcomes live in the trading-history tables as percentage
points.  This module keeps those semantics separate and gives KR/US callers one
auditable contract without adding tables or write paths.
"""

from __future__ import annotations

import os
import sqlite3
import statistics
from typing import Any

_MARKET_TABLES = {
    "KR": {
        "candidate": "analysis_performance_tracker",
        "actual": "trading_history",
        "returns": ("tracked_7d_return", "tracked_14d_return", "tracked_30d_return"),
    },
    "US": {
        "candidate": "us_analysis_performance_tracker",
        "actual": "us_trading_history",
        "returns": ("return_7d", "return_14d", "return_30d"),
    },
}

_VALID_MODES = {"off", "shadow", "actual"}


def _market(value: str) -> str:
    market = str(value or "").strip().upper()
    if market not in _MARKET_TABLES:
        raise ValueError(f"unsupported market: {value!r}")
    return market


def _table_exists(cursor, table: str) -> bool:
    return cursor.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _safe_mean(values: list[float]) -> float | None:
    return statistics.mean(values) if values else None


def _safe_median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def candidate_trigger_stats(cursor, market: str, trigger_type: str) -> dict[str, Any] | None:
    """Return watched-candidate fixed-horizon stats in percentage points."""
    config = _MARKET_TABLES[_market(market)]
    table = config["candidate"]
    r7, r14, r30 = config["returns"]
    if not trigger_type or not _table_exists(cursor, table):
        return None
    try:
        rows = cursor.execute(
            f"""
            SELECT {r7}, {r14}, {r30}
            FROM {table}
            WHERE trigger_type=?
              AND COALESCE(was_traded, 0)=0
              AND {r30} IS NOT NULL
            """,
            (trigger_type,),
        ).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None

    values_7 = [float(row[0]) * 100.0 for row in rows if row[0] is not None]
    values_14 = [float(row[1]) * 100.0 for row in rows if row[1] is not None]
    values_30 = [float(row[2]) * 100.0 for row in rows if row[2] is not None]
    return {
        "source": "candidate_tracker",
        "n": len(values_30),
        "positive_rate_30d": (
            sum(value > 0 for value in values_30) / len(values_30) if values_30 else None
        ),
        "avg_7d_pct": _safe_mean(values_7),
        "median_7d_pct": _safe_median(values_7),
        "avg_14d_pct": _safe_mean(values_14),
        "median_14d_pct": _safe_median(values_14),
        "avg_30d_pct": _safe_mean(values_30),
        "median_30d_pct": _safe_median(values_30),
    }


def actual_trigger_stats(cursor, market: str, trigger_type: str) -> dict[str, Any] | None:
    """Return executed-trade realized stats from trading history."""
    config = _MARKET_TABLES[_market(market)]
    table = config["actual"]
    if not trigger_type or not _table_exists(cursor, table):
        return None
    try:
        rows = cursor.execute(
            f"SELECT profit_rate FROM {table} WHERE trigger_type=? ORDER BY sell_date, id",
            (trigger_type,),
        ).fetchall()
    except sqlite3.Error:
        return None
    returns = [float(row[0]) for row in rows if row[0] is not None]
    if not returns:
        return None
    wins = [value for value in returns if value > 0]
    losses = [value for value in returns if value < 0]
    gross_loss = abs(sum(losses))
    return {
        "source": "trading_history",
        "n": len(returns),
        "win_rate": sum(value > 0 for value in returns) / len(returns),
        "avg_return_pct": statistics.mean(returns),
        "median_return_pct": statistics.median(returns),
        "avg_win_pct": _safe_mean(wins),
        "avg_loss_pct": _safe_mean(losses),
        "profit_factor": sum(wins) / gross_loss if gross_loss else None,
    }


def get_trigger_feedback(cursor, market: str, trigger_type: str) -> dict[str, Any]:
    market_key = _market(market)
    return {
        "market": market_key,
        "trigger_type": trigger_type,
        "candidate_trigger": candidate_trigger_stats(cursor, market_key, trigger_type),
        "actual_trigger": actual_trigger_stats(cursor, market_key, trigger_type),
    }


def trigger_feedback_mode(value: str | None = None) -> str:
    raw = value if value is not None else os.getenv("TRIGGER_PERFORMANCE_FEEDBACK", "shadow")
    mode = str(raw or "shadow").strip().lower()
    return mode if mode in _VALID_MODES else "shadow"


def resolve_actual_adjustment(
    feedback: dict[str, Any],
    *,
    mode: str | None = None,
    min_samples: int = 5,
) -> dict[str, Any]:
    """Return would/applied adjustment; Candidate stats never affect it."""
    resolved_mode = trigger_feedback_mode(mode)
    actual = feedback.get("actual_trigger") or {}
    n = int(actual.get("n") or 0)
    win_rate = actual.get("win_rate")
    would_adjust = 0
    if resolved_mode != "off" and n >= min_samples and win_rate is not None:
        if float(win_rate) < 0.35:
            would_adjust = -1
        elif float(win_rate) > 0.65:
            would_adjust = 1
    return {
        "mode": resolved_mode,
        "min_samples": min_samples,
        "actual_n": n,
        "actual_win_rate": win_rate,
        "would_adjust": would_adjust,
        "applied_adjust": would_adjust if resolved_mode == "actual" else 0,
    }


def feedback_log_payload(
    feedback: dict[str, Any],
    adjustment: dict[str, Any],
    *,
    ticker: str | None = None,
    sector: str | None = None,
) -> dict[str, Any]:
    """Small JSON-serializable payload for later operational analysis."""
    return {
        "schema_version": 1,
        "event": "trigger_performance_feedback",
        "market": feedback.get("market"),
        "ticker": ticker,
        "sector": sector,
        "trigger_type": feedback.get("trigger_type"),
        "candidate": feedback.get("candidate_trigger"),
        "actual": feedback.get("actual_trigger"),
        "mode": adjustment.get("mode"),
        "would_adjust": adjustment.get("would_adjust"),
        "applied_adjust": adjustment.get("applied_adjust"),
        "min_samples": adjustment.get("min_samples"),
    }


def format_trigger_feedback(feedback: dict[str, Any], *, language: str = "ko") -> list[str]:
    """Human-facing lines with explicit Candidate/Actual provenance."""
    actual = feedback.get("actual_trigger")
    candidate = feedback.get("candidate_trigger")
    lines: list[str] = []
    if language == "en":
        if actual:
            pf = actual.get("profit_factor")
            pf_text = f", PF {pf:.2f}" if pf is not None else ""
            lines.append(
                f"Actual trades: n={actual['n']}, win rate {actual['win_rate'] * 100:.0f}%{pf_text}"
            )
        if candidate:
            lines.append(
                "Watched candidates: "
                f"30d positive rate {candidate['positive_rate_30d'] * 100:.0f}% "
                f"(n={candidate['n']})"
            )
        return lines

    if actual:
        pf = actual.get("profit_factor")
        pf_text = f", PF {pf:.2f}" if pf is not None else ""
        lines.append(
            f"실제 매매: {actual['n']}건, 승률 {actual['win_rate'] * 100:.0f}%{pf_text}"
        )
    if candidate:
        lines.append(
            "관찰 후보: "
            f"30일 상승 비율 {candidate['positive_rate_30d'] * 100:.0f}% "
            f"(n={candidate['n']})"
        )
    return lines


__all__ = [
    "actual_trigger_stats",
    "candidate_trigger_stats",
    "feedback_log_payload",
    "format_trigger_feedback",
    "get_trigger_feedback",
    "resolve_actual_adjustment",
    "trigger_feedback_mode",
]
