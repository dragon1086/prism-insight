"""Read-only daily trend snapshots, separate from prompts and trading gates."""
from __future__ import annotations

import hashlib
import math
import os
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from prism_core.trend_quality_research import digest, timestamp

_MARKETS = {"KR": ("Asia/Seoul", time(15, 30)), "US": ("America/New_York", time(16))}
_BAR_FIELDS = ("close_at", "open", "high", "low", "close", "completed")


def capture_enabled():
    # User-authorized observation only: no extra provider call or decision input.
    return os.getenv("TREND_RESEARCH_CAPTURE_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def cache_snapshot(agent, ticker, stock_frame, benchmark_frame, *, market, source):
    """Observe already-fetched adjusted frames; never change trading or prompts."""
    if not capture_enabled():
        return
    try:
        snapshot = build_snapshot(
            stock_frame, benchmark_frame, market=market, source=source,
            observed_at=datetime.now().astimezone().isoformat(),
            adjustment_policy="point_in_time_adjusted",
        )
        if not hasattr(agent, "_trend_research_snapshots"):
            agent._trend_research_snapshots = {}
        agent._trend_research_snapshots[ticker] = snapshot
    except Exception:  # noqa: BLE001 - optional capture must never fail a trade
        # Diagnostic capture must not interfere with the decision path.
        return


def _completed_rows(frame, market, asof):
    """Daily index values are session labels, not instants to timezone-shift."""
    zone, closing = _MARKETS[market]
    labels, rows = [], []
    for index, row in frame.iterrows():
        label = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
        closed = datetime.combine(label, closing, ZoneInfo(zone))
        if closed > asof:
            continue
        labels.append(label)
        rows.append((label, closed, row))
    if len(labels) != len(set(labels)) or labels != sorted(labels):
        raise ValueError("INVALID_INDEX_ORDER")
    return rows


def build_snapshot(stock_frame, benchmark_frame, *, market, source, observed_at,
                   adjustment_policy="point_in_time_adjusted"):
    """Keep at most 60 completed bars; a benchmark is NOT an official calendar.

    US early-close bars are deliberately withheld until normal 16:00 local close.
    The caller must declare actual adjustment provenance, not assume it from OHLC.
    """
    result = {"observed_at": observed_at, "bar_interval": "1d", "source": source,
              "adjustment_policy": adjustment_policy, "gaps_checked": False,
              "gap_reference": "benchmark_observed_sessions_not_official_calendar",
              "bars": [], "data_quality_flags": []}
    flags = result["data_quality_flags"]
    try:
        asof = timestamp(observed_at)
        if market not in _MARKETS:
            raise ValueError("UNSUPPORTED_MARKET")
        stock = _completed_rows(stock_frame, market, asof)[-60:]
        benchmark = _completed_rows(benchmark_frame, market, asof)
        if not stock:
            raise ValueError("BARS_MISSING")
        if not benchmark:
            flags.append("BENCHMARK_REFERENCE_MISSING")
        else:
            stock_dates = {r[0] for r in stock}
            reference = {r[0] for r in benchmark if r[0] >= stock[0][0]}
            result["gaps_checked"] = True
            if min(r[0] for r in benchmark) > stock[0][0]:
                flags.append("BENCHMARK_REFERENCE_INCOMPLETE")
            if stock_dates != reference:
                flags.append("OBSERVED_SESSION_GAP")
        aliases = {"open": ("Open", "open", "시가"), "high": ("High", "high", "고가"),
                   "low": ("Low", "low", "저가"), "close": ("Close", "close", "종가")}
        for _, closed, row in stock:
            values = {}
            for field, names in aliases.items():
                raw = next((row[name] for name in names if name in row), None)
                value = float(raw)
                if isinstance(raw, bool) or not math.isfinite(value) or value <= 0:
                    raise ValueError("INVALID_OHLC")
                values[field] = value
            if not (values["low"] <= min(values["open"], values["close"])
                    <= max(values["open"], values["close"]) <= values["high"]):
                raise ValueError("INVALID_OHLC")
            result["bars"].append({"close_at": closed.isoformat(), **values, "completed": True})
        if len(stock) < 60:
            flags.append("FEWER_THAN_60_COMPLETED_BARS")
        if adjustment_policy not in {"point_in_time_adjusted", "no_actions_verified"}:
            flags.append("ADJUSTMENT_PROVENANCE_MISSING")
        if not isinstance(source, str) or not source:
            flags.append("SOURCE_MISSING")
    except (ValueError, TypeError, AttributeError, KeyError, OverflowError) as error:
        allowed = {"UNSUPPORTED_MARKET", "BARS_MISSING", "INVALID_INDEX_ORDER", "INVALID_OHLC"}
        flags.append(str(error) if str(error) in allowed else "INVALID_SNAPSHOT_INPUT")
        result["bars"] = []
    result["source_hash"] = digest(result["bars"])
    result["status"] = "MISSING" if flags else "OK"
    return result


def features_from_events(packet, events):
    """Exact decision-id join only; missing/ambiguous evidence stays MISSING."""
    refs = {row["decision_ref"] for row in packet.get("analysis_rows", [])}
    matched = {}
    for event in events:
        if event.get("event_type") != "candidate.evaluated" or not event.get("decision_id"):
            continue
        ref = hashlib.sha256(str(event["decision_id"]).strip().encode()).hexdigest()[:16]
        if ref in refs:
            matched.setdefault(ref, []).append(event)
    rows = []
    for ref in sorted(refs):
        candidates = matched.get(ref, [])
        flags = []
        row = {"decision_ref": ref, "data_quality_flags": flags, "bars": []}
        if len(candidates) != 1:
            flags.append("MATCHING_EVENT_MISSING" if not candidates else "AMBIGUOUS_MATCHING_EVENTS")
        else:
            event = candidates[0]
            attrs = event.get("attributes") or {}
            snapshot = attrs.get("research_context") or {}
            for key in ("bar_interval", "source_hash", "adjustment_policy", "gaps_checked", "gap_reference", "observed_at"):
                row[key] = snapshot.get(key)
            row["bars"] = [{key: bar[key] for key in _BAR_FIELDS if key in bar}
                           for bar in snapshot.get("bars", [])[:60]]
            flags.extend(snapshot.get("data_quality_flags") or [])
            row.update(market=event.get("market"), session=attrs.get("trigger_mode"),
                       session_date=attrs.get("trading_date"),
                       regime=attrs.get("effective_entry_regime") or attrs.get("regime"),
                       policy_version=event.get("policy_version"))
            try:
                decided = timestamp(event.get("timestamp"))
                row["decided_at"] = decided.isoformat().replace("+00:00", "Z")
                # Exchange-local decision date is only the resampling block label;
                # it is never an inferred session/mode or a join key.
                if row["market"] in _MARKETS:
                    row["session_date"] = decided.astimezone(ZoneInfo(_MARKETS[row["market"]][0])).date().isoformat()
                if timestamp(snapshot.get("observed_at")) > decided:
                    flags.append("OBSERVATION_AFTER_DECISION")
            except (ValueError, TypeError):
                flags.append("TIMESTAMP_METADATA_MISSING")
            if not snapshot or "data_quality_flags" not in snapshot:
                flags.append("SNAPSHOT_METADATA_MISSING")
            if (row["market"] not in _MARKETS or row["session"] not in {"morning", "afternoon"}
                    or not all(row.get(k) for k in ("session_date", "regime", "policy_version"))):
                flags.append("STRATIFICATION_METADATA_MISSING")
        row["status"] = "MISSING" if flags else "OK"
        rows.append(row)
    return {"rows": rows}
