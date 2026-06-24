# forecast_stats.py
"""Empirical, point-in-time forecast statistics from Prism's own track record.

This module turns the realized outcomes that Prism has accumulated in
``stock_tracking_db.sqlite`` into **honest base rates** for the subscriber-facing
insight image:

  - :func:`get_stock_scenario` — the most recent analysis row for a ticker
    (its own ``target_price`` / ``stop_loss`` / ``buy_score`` / ``trigger_type``).
  - :func:`get_forecast_distribution` — among PAST analyses with a *completed*
    30-day outcome that share this stock's profile (buy-score band, optionally
    the same trigger), what fraction rose / went sideways / fell. Returned with
    the sample size ``n`` and which conditioning *tier* produced it.

Design rules (so the numbers stay defensible):
  - **Point-in-time**: only rows with ``tracking_status='completed'`` (their
    forward window has already elapsed) feed the distribution — never the row
    being rendered. No look-ahead.
  - **Cohort frequency, not a per-stock prophecy.** The caller must label it as
    "stocks Prism picked under these conditions historically resolved like X".
  - **ratio units**: the stored returns are ratios (``-0.42`` == -42%), so the
    ±move threshold is a ratio too (``0.10`` == ±10%).
  - Tiered fallback with a minimum sample floor; the tier is reported so the
    caption can be honest about how specific the cohort is.

Everything is best-effort: any failure returns ``None`` / empty so the image
still renders without the forecast overlay.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Repo root holds the single shared tracking DB (KR + US tables both live here).
# This file is cores/llm/features/forecast_stats.py -> parents[3] == repo root.
_DB_NAME = "stock_tracking_db.sqlite"

# ±move threshold (ratio) and horizon that define 상승 / 횡보 / 하락.
DEFAULT_THRESHOLD = 0.10
DEFAULT_HORIZON = 30
# Minimum cohort size before a tier is trusted; otherwise fall back broader.
MIN_SAMPLE = 30

# Per-market column mapping for the two tracker tables.
_SCHEMA = {
    "kr": {
        "table": "analysis_performance_tracker",
        "price": "analyzed_price",
        "ret30": "tracked_30d_return",
        "date": "analyzed_date",
        "hit_target": None,   # KR has no explicit hit flag; proxy via close.
    },
    "us": {
        "table": "us_analysis_performance_tracker",
        "price": "analysis_price",
        "ret30": "return_30d",
        "date": "analysis_date",
        "hit_target": "hit_target",
    },
}


def _market_key(market: Optional[str]) -> str:
    """Normalise a market hint to 'kr' or 'us' (default 'kr')."""
    if isinstance(market, str) and market.strip().lower() in (
        "us", "usa", "united states"
    ):
        return "us"
    return "kr"


def _db_path() -> Optional[Path]:
    """Locate ``stock_tracking_db.sqlite``: repo root first, then a few parents."""
    here = Path(__file__).resolve()
    candidates = [here.parents[3] / _DB_NAME]  # repo root
    # Defensive: also probe the immediate ancestors in case of an unusual layout.
    candidates += [p / _DB_NAME for p in here.parents[1:5]]
    for c in candidates:
        try:
            if c.is_file() and c.stat().st_size > 0:
                return c
        except Exception:  # noqa: BLE001
            continue
    return None


def _connect() -> Optional[sqlite3.Connection]:
    p = _db_path()
    if p is None:
        return None
    try:
        conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        return conn
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FORECAST] DB open failed: %s", exc)
        return None


def score_band(score: Optional[float]) -> Optional[str]:
    """Bucket a buy_score (0..~9 scale) into a quality band.

    Bands are chosen from the observed monotonic break in realized outcomes:
    low (<5) historically averages negative, mid [5,6) is the inflection, and
    high (>=6) is where the strong positive skew lives. Returns None if no score.
    """
    if score is None:
        return None
    try:
        s = float(score)
    except (TypeError, ValueError):
        return None
    if s < 5:
        return "low"
    if s < 6:
        return "mid"
    return "high"


_BAND_RANGE = {"low": (-1e9, 5.0), "mid": (5.0, 6.0), "high": (6.0, 1e9)}
_BAND_KO = {"low": "신중", "mid": "보통", "high": "우수"}


def get_stock_scenario(ticker: str, market: Optional[str] = None) -> Optional[dict]:
    """Return the most recent tracker row's scenario fields for *ticker*.

    Keys: analyzed_price, target_price, stop_loss, buy_score, trigger_type.
    Returns None if the ticker has no row (or on any error).
    """
    sc = _SCHEMA[_market_key(market)]
    conn = _connect()
    if conn is None:
        return None
    try:
        row = conn.execute(
            f"SELECT {sc['price']} AS analyzed_price, target_price, stop_loss, "
            f"buy_score, trigger_type "
            f"FROM {sc['table']} WHERE ticker = ? "
            f"ORDER BY {sc['date']} DESC, id DESC LIMIT 1",
            (str(ticker),),
        ).fetchone()
        if row is None:
            return None
        return {
            "analyzed_price": row["analyzed_price"],
            "target_price": row["target_price"],
            "stop_loss": row["stop_loss"],
            "buy_score": row["buy_score"],
            "trigger_type": row["trigger_type"],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FORECAST] scenario lookup failed for %s: %s", ticker, exc)
        return None
    finally:
        conn.close()


def _pctile(sorted_vals, q):
    """Linear-interpolated percentile (q in 0..1) of an ascending list."""
    if not sorted_vals:
        return None
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac


def _distribution_for(conn, sc, where_sql, params, threshold):
    """Compute (n, up%, side%, down%, avg, pcts) for completed matching rows."""
    rows = conn.execute(
        f"SELECT {sc['ret30']} AS r FROM {sc['table']} "
        f"WHERE tracking_status = 'completed' AND {sc['ret30']} IS NOT NULL {where_sql}",
        params,
    ).fetchall()
    vals = [r["r"] for r in rows if r["r"] is not None]
    n = len(vals)
    if n == 0:
        return None
    up = sum(1 for v in vals if v > threshold)
    dn = sum(1 for v in vals if v < -threshold)
    sd = n - up - dn
    avg = sum(vals) / n
    sv = sorted(vals)
    pcts = {f"p{int(q*100)}": _pctile(sv, q)
            for q in (0.10, 0.25, 0.50, 0.75, 0.90)}
    return {
        "n": n,
        "up": round(100.0 * up / n),
        "side": round(100.0 * sd / n),
        "down": round(100.0 * dn / n),
        "avg": avg,
        "pcts": pcts,
    }


def get_forecast_distribution(
    market: Optional[str],
    buy_score: Optional[float],
    trigger_type: Optional[str] = None,
    *,
    threshold: float = DEFAULT_THRESHOLD,
    min_sample: int = MIN_SAMPLE,
) -> Optional[dict]:
    """Tiered empirical up/sideways/down distribution for this stock's profile.

    Tier order (first with n >= ``min_sample`` wins):
      1. same score band AND same trigger_type
      2. same score band
      3. all completed rows (global base rate)

    Returns a dict {up, side, down, n, avg, tier, band, threshold} (percentages
    are ints summing to ~100), or None when even the global cohort is empty.
    """
    sc = _SCHEMA[_market_key(market)]
    band = score_band(buy_score)
    conn = _connect()
    if conn is None:
        return None
    try:
        tiers = []
        if band is not None:
            lo, hi = _BAND_RANGE[band]
            if trigger_type:
                tiers.append((
                    "band+trigger",
                    f"AND buy_score >= ? AND buy_score < ? AND trigger_type = ?",
                    (lo, hi, str(trigger_type)),
                ))
            tiers.append((
                "band",
                f"AND buy_score >= ? AND buy_score < ?",
                (lo, hi),
            ))
        tiers.append(("global", "", ()))

        last = None
        for tier, where_sql, params in tiers:
            d = _distribution_for(conn, sc, where_sql, params, threshold)
            if d is None:
                continue
            last = {**d, "tier": tier, "band": band, "threshold": threshold}
            if d["n"] >= min_sample:
                return last
        return last  # best available even if below the floor (caller shows n)
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FORECAST] distribution failed: %s", exc)
        return None
    finally:
        conn.close()


def get_target_reach_rate(
    market: Optional[str],
    buy_score: Optional[float] = None,
    *,
    min_sample: int = MIN_SAMPLE,
) -> Optional[dict]:
    """Historical rate at which the analysis target was reached (30d window).

    US uses the explicit ``hit_target`` flag (intraday-aware). KR has no flag, so
    we approximate with a *close-based* proxy: the 30d close return reached the
    target's implied gain (``target/price - 1``). Conditioned on the score band
    when ``buy_score`` is given, with a global fallback. Returns {rate, n, proxy}
    or None.
    """
    key = _market_key(market)
    sc = _SCHEMA[key]
    band = score_band(buy_score)
    conn = _connect()
    if conn is None:
        return None
    try:
        band_clause, band_params = "", []
        if band is not None:
            lo, hi = _BAND_RANGE[band]
            band_clause = "AND buy_score >= ? AND buy_score < ?"
            band_params = [lo, hi]

        def _rate(clause, params):
            if sc["hit_target"]:
                rows = conn.execute(
                    f"SELECT {sc['hit_target']} AS h FROM {sc['table']} "
                    f"WHERE tracking_status='completed' AND {sc['hit_target']} IS NOT NULL {clause}",
                    params,
                ).fetchall()
                vals = [r["h"] for r in rows if r["h"] is not None]
                if not vals:
                    return None
                return {"rate": round(100.0 * sum(1 for v in vals if v) / len(vals)),
                        "n": len(vals), "proxy": False}
            # KR close-based proxy
            rows = conn.execute(
                f"SELECT {sc['ret30']} AS r, target_price AS t, {sc['price']} AS p "
                f"FROM {sc['table']} WHERE tracking_status='completed' "
                f"AND {sc['ret30']} IS NOT NULL AND target_price > 0 AND {sc['price']} > 0 {clause}",
                params,
            ).fetchall()
            hit = tot = 0
            for r in rows:
                tot += 1
                if r["r"] >= (r["t"] / r["p"] - 1.0):
                    hit += 1
            if tot == 0:
                return None
            return {"rate": round(100.0 * hit / tot), "n": tot, "proxy": True}

        d = _rate(band_clause, band_params)
        if d and d["n"] >= min_sample:
            return d
        g = _rate("", [])
        return g or d
    except Exception as exc:  # noqa: BLE001
        logger.warning("[FORECAST] target-reach failed: %s", exc)
        return None
    finally:
        conn.close()
