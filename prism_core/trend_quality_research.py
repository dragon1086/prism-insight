"""Offline, fixed-hypothesis drop-filter diagnostics; never a trading gate.

Daily bars are immutable caller-provided snapshots. No provider, database, broker,
or agent is called. Effects are paired closed-ledger diagnostics, NOT a portfolio
backtest or a replay of changed BUY/SELL agent decisions.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from itertools import pairwise

VERSION = "trend-quality-research-v1"
SUPPORTED_TRIGGERS = (
    "갭 상승 모멘텀 상위주", "일중 상승률 상위주",
    "Gap Up Momentum Top", "Intraday Rise Top",
)
TRIALS = (
    ("baseline", "keep all eligible scoped rows"),
    ("H1", "ADX14 >= 20"),
    ("H2", "ADX14 >= 25 and +DI14 > -DI14"),
    ("H3", "ADX14 strictly rising across last 3 completed bars and +DI14 > -DI14"),
    ("H4", "ER20 >= 0.30 and close minus close20 > 0"),
)
MIN_ROWS = 30
MIN_DATES = 20
BOOTSTRAPS = 1000
SEED = 20260910
EMBARGO_DAYS = 30
MAX_SNAPSHOT_AGE_HOURS = 4
MAX_COMPLETED_BAR_AGE_DAYS = 7


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def registry():
    """Fresh serializable copy; callers cannot mutate the registered constants."""
    return {"version": VERSION, "trials": list(TRIALS),
            "triggers": list(SUPPORTED_TRIGGERS), "bar_interval": "1d",
            "adx_period": 14, "er_period": 20, "train_fraction": 0.7,
            "embargo_calendar_days": EMBARGO_DAYS, "min_rows": MIN_ROWS,
            "min_dates": MIN_DATES, "bootstrap_repetitions": BOOTSTRAPS,
            "bootstrap_seed": SEED, "multiple_testing_family": 4,
            "max_snapshot_age_hours": MAX_SNAPSHOT_AGE_HOURS,
            "max_completed_bar_age_days": MAX_COMPLETED_BAR_AGE_DAYS,
            "interval": "98.75% Bonferroni date-block bootstrap exploratory interval",
            "optimization": "none", "promotion": "never automatic"}


def timestamp(value):
    if not isinstance(value, str):
        raise TypeError("timestamp required")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("timezone required")
    return result.astimezone(timezone.utc)


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("finite numeric value required")
    return float(value)


def compute_features(snapshot):
    """Wilder seed: mean DX at indices 14..27; subsequent recursive means.

    All bars must have closed at/before decision time. Reject a contaminated
    snapshot rather than silently truncating future bars. Prefix invariance is
    obtained by supplying the historical prefix, not a post-decision series.
    """
    reasons = []
    if snapshot.get("bar_interval") != "1d":
        reasons.append("UNSUPPORTED_BAR_INTERVAL")
    if snapshot.get("gaps_checked") is not True:
        reasons.append("GAPS_METADATA_MISSING")
    if snapshot.get("adjustment_policy") not in ("point_in_time_adjusted", "no_actions_verified"):
        reasons.append("ADJUSTMENT_PROVENANCE_MISSING")
    if snapshot.get("data_quality_flags") != []:
        reasons.append("DATA_QUALITY_FLAGS_OR_STATUS_MISSING")
    bars = snapshot.get("bars", [])
    if not bars:
        reasons.append("BARS_MISSING")
    try:
        if snapshot.get("source_hash") != digest(bars):
            reasons.append("SOURCE_HASH_MISMATCH")
        asof = timestamp(snapshot.get("decided_at"))
        observed = timestamp(snapshot.get("observed_at"))
        if observed > asof:
            reasons.append("FUTURE_OBSERVATION")
        if asof - observed > timedelta(hours=MAX_SNAPSHOT_AGE_HOURS):
            reasons.append("STALE_SNAPSHOT")
        previous = None
        values = []
        for bar in bars:
            at = timestamp(bar.get("close_at"))
            if previous is not None and at <= previous:
                raise ValueError("BAR_TIMESTAMPS_NOT_STRICTLY_INCREASING")
            if at > asof:
                raise ValueError("FUTURE_BAR")
            if at > observed:
                raise ValueError("BAR_AFTER_OBSERVATION")
            if bar.get("completed") is not True:
                raise ValueError("INCOMPLETE_BAR")
            high, low, close = (number(bar.get(k)) for k in ("high", "low", "close"))
            if not 0 < low <= close <= high:
                raise ValueError("INVALID_OHLC")
            values.append((high, low, close))
            previous = at
        if previous is not None and asof - previous > timedelta(days=MAX_COMPLETED_BAR_AGE_DAYS):
            reasons.append("STALE_COMPLETED_BAR_WINDOW")
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        # Error reasons contain only constants/type validation text, never input payload.
        reasons.append(str(error) if str(error) in {
            "BAR_TIMESTAMPS_NOT_STRICTLY_INCREASING", "FUTURE_BAR",
            "INCOMPLETE_BAR", "INVALID_OHLC", "BAR_AFTER_OBSERVATION"} else "INVALID_BAR_OR_TIMESTAMP")
    if reasons:
        return {"status": "MISSING", "reasons": sorted(set(reasons))}
    if len(values) < 30:
        return {"status": "MISSING", "reasons": ["WARMUP_REQUIRES_30_COMPLETED_BARS"]}
    indicators = compute_wilder_values(values)
    return {"status": "OK", "reasons": [], **indicators,
            "source_hash": snapshot["source_hash"],
            "input_hash": digest({k: snapshot.get(k) for k in (
                "decision_ref", "decided_at", "bar_interval", "source_hash",
                "adjustment_policy", "gaps_checked", "data_quality_flags")}),
            "bar_count": len(values), "last_close_at": bars[-1]["close_at"]}


def compute_wilder_values(values):
    """Pure numeric core; callers separately enforce observed/reconstructed lineage."""
    if len(values) < 30:
        raise ValueError("WARMUP_REQUIRES_30_COMPLETED_BARS")
    values = [tuple(number(v) for v in bar) for bar in values]
    if any(len(bar) != 3 or not 0 < bar[1] <= bar[2] <= bar[0] for bar in values):
        raise ValueError("INVALID_OHLC")
    tr, plus, minus = [], [], []
    for (h0, l0, c0), (high, low, _) in pairwise(values):
        up, down = high - h0, l0 - low
        tr.append(max(high - low, abs(high - c0), abs(low - c0)))
        plus.append(up if up > down and up > 0 else 0.0)
        minus.append(down if down > up and down > 0 else 0.0)
    smooth = [sum(series[:14]) for series in (tr, plus, minus)]
    dxs, adxs = [], []
    pdi = mdi = 0.0
    for i in range(13, len(tr)):
        if i > 13:
            smooth = [s - s / 14 + series[i] for s, series in zip(smooth, (tr, plus, minus))]
        pdi = 100 * smooth[1] / smooth[0] if smooth[0] else 0.0
        mdi = 100 * smooth[2] / smooth[0] if smooth[0] else 0.0
        dxs.append(100 * abs(pdi - mdi) / (pdi + mdi) if pdi + mdi else 0.0)
        if len(dxs) == 14:
            adxs.append(sum(dxs) / 14)
        elif len(dxs) > 14:
            adxs.append((adxs[-1] * 13 + dxs[-1]) / 14)
    closes = [v[2] for v in values[-21:]]
    net = closes[-1] - closes[0]
    path = sum(abs(b - a) for a, b in pairwise(closes))
    return {"adx14": adxs[-1],
            "plus_di14": pdi, "minus_di14": mdi,
            "adx_rising_3": adxs[-3] < adxs[-2] < adxs[-1],
            "er20": abs(net) / path if path else 0.0, "net20": net}


def passes(trial, feature):
    if trial == "baseline":
        return True
    if feature.get("status") != "OK":
        return None
    direction = feature["plus_di14"] > feature["minus_di14"]
    return {"H1": feature["adx14"] >= 20,
            "H2": feature["adx14"] >= 25 and direction,
            "H3": feature["adx_rising_3"] and direction,
            "H4": feature["er20"] >= 0.30 and feature["net20"] > 0}[trial]


def _metrics(rows, trial):
    paired = [r for r in rows if r["strategy_return_pct"] is not None
              and r["feature"]["status"] == "OK"]
    kept = [r for r in paired if passes(trial, r["feature"])]
    dropped = [r for r in paired if not passes(trial, r["feature"])]
    winners = [r for r in paired if r["strategy_return_pct"] > 0]
    losses = [r for r in paired if r["strategy_return_pct"] < 0]
    effects = {r["decision_ref"]: (0.0 if r in kept else -r["strategy_return_pct"]) for r in paired}
    blocks = defaultdict(list)
    for r in paired:
        blocks[r["session_date"]].append(effects[r["decision_ref"]])
    sufficient = len(paired) >= MIN_ROWS and len(blocks) >= MIN_DATES
    interval = None
    if sufficient:
        rng = random.Random(SEED)
        keys = sorted(blocks)
        draws = []
        for _ in range(BOOTSTRAPS):
            sample = [v for key in rng.choices(keys, k=len(keys)) for v in blocks[key]]
            draws.append(statistics.mean(sample))
        draws.sort()
        interval = [draws[6], draws[993]]
    ranked = sorted(paired, key=lambda r: (-r["strategy_return_pct"], r["decision_ref"]))
    remaining = ranked[1:] if winners else ranked
    return {"status": "EXPLORATORY_ONLY" if sufficient else "INSUFFICIENT",
            "paired_closed_strategy_count": len(paired), "session_dates": len(blocks),
            "kept_count": len(kept), "dropped_count": len(dropped),
            "kept_mean_price_return_pct": statistics.mean(r["strategy_return_pct"] for r in kept) if kept else None,
            "dropped_mean_price_return_pct": statistics.mean(r["strategy_return_pct"] for r in dropped) if dropped else None,
            "winner_preservation_rate": sum(r in kept for r in winners) / len(winners) if winners else None,
            "losing_trade_removal_rate": sum(r in dropped for r in losses) / len(losses) if losses else None,
            "paired_equal_opportunity_delta_pp": statistics.mean(effects.values()) if effects else None,
            "delta_after_highest_winner_removal_pp": statistics.mean(effects[r["decision_ref"]] for r in remaining) if remaining else None,
            "highest_winner_removed": ranked[0]["decision_ref"] if winners else None,
            "date_block_adjusted_interval_pp": interval,
            "interpretation": "No trade=0 comparator; no capital recycling, costs, portfolio MDD or changed-agent replay."}


def build_study(packet, features=None):
    """Join canonical sanitized Packet rows by exact decision_ref, fail closed."""
    if packet.get("packet_schema_version") != 3 or packet.get("analysis_contract_version") != "entry-quality-harness-v2":
        raise ValueError("canonical entry-quality Packet schema 3 / harness v2 required")
    if not packet.get("packet_id"):
        raise ValueError("packet_id required")
    snapshots = {}
    for snapshot in (features or {}).get("rows", []):
        ref = snapshot.get("decision_ref")
        if not ref or ref in snapshots:
            raise ValueError("feature decision_ref missing or duplicate")
        snapshots[ref] = snapshot
    rows, seen = [], set()
    for candidate in sorted(packet.get("analysis_rows", []), key=lambda r: r.get("decision_ref", "")):
        ref = candidate.get("decision_ref")
        if not ref or ref in seen:
            raise ValueError("candidate decision_ref missing or duplicate")
        seen.add(ref)
        snapshot = snapshots.get(ref)
        reasons = []
        if not candidate.get("eligible_for_analysis"):
            reasons.append("PACKET_EXCLUDED")
        if candidate.get("trigger_type") not in SUPPORTED_TRIGGERS:
            reasons.append("OUT_OF_SCOPE_TRIGGER")
        try:
            if timestamp(candidate.get("decided_at")) > timestamp(packet.get("as_of")):
                reasons.append("DECISION_AFTER_PACKET_AS_OF")
        except (ValueError, TypeError):
            reasons.append("INVALID_DECISION_OR_PACKET_AS_OF")
        if snapshot is None:
            reasons.append("FEATURE_SNAPSHOT_MISSING")
        else:
            for field in ("decided_at", "regime", "policy_version"):
                if snapshot.get(field) != candidate.get(field) or candidate.get(field) in (None, "UNKNOWN"):
                    reasons.append("JOIN_CONTEXT_MISMATCH")
            if snapshot.get("market") not in ("KR", "US") or snapshot.get("session") not in ("morning", "afternoon"):
                reasons.append("STRATIFICATION_MISSING")
            if candidate.get("market") and candidate["market"] != snapshot.get("market"):
                reasons.append("MARKET_MISMATCH")
            if packet.get("market") in ("KR", "US") and packet["market"] != snapshot.get("market"):
                reasons.append("MARKET_MISMATCH")
            try:
                session_date = date.fromisoformat(snapshot.get("session_date", ""))
                if abs((timestamp(candidate["decided_at"]).date() - session_date).days) > 1:
                    reasons.append("INVALID_SESSION_DATE")
            except (ValueError, TypeError, KeyError):
                reasons.append("INVALID_SESSION_DATE")
        feature = compute_features(snapshot) if not reasons else {"status": "MISSING", "reasons": sorted(set(reasons))}
        outcome = candidate.get("outcomes", {})
        result = None
        closed_at = None
        try:
            result = number(outcome.get("strategy_return_pct"))
            if not candidate.get("entry", {}).get("observed"):
                raise ValueError("not a linked closed strategy entry")
            # Packet v3 has no exact exit timestamp. Its as_of is a conservative
            # upper bound, not decision time plus holding time (entry can lag).
            closed_at = timestamp(outcome.get("strategy_closed_at") or packet.get("as_of"))
            if closed_at < timestamp(candidate["decided_at"]):
                raise ValueError("exit precedes decision")
            if closed_at > timestamp(packet.get("as_of")):
                raise ValueError("unmatured")
        except (ValueError, TypeError, KeyError):
            result = None
        rows.append({"decision_ref": ref, "feature": feature,
                     "stratum": [snapshot.get(k) if snapshot else None for k in
                                 ("market", "session", "regime", "policy_version")]
                                + [candidate.get("trigger_type")],
                     "session_date": snapshot.get("session_date") if snapshot else None,
                     "decided_at": candidate.get("decided_at"),
                     "closed_at": closed_at.isoformat() if result is not None else None,
                     "strategy_return_pct": result,
                     "candidate_outcomes": "not used in closed-strategy diagnostics"})
    # Split whole dates globally to prevent correlated contemporaneous leakage.
    dates = sorted({r["session_date"] for r in rows if r["feature"]["status"] == "OK"})
    boundary = dates[int(len(dates) * 0.7)] if len(dates) > 1 else None
    cutoff = timestamp(boundary + "T00:00:00+00:00") if boundary else None
    groups = defaultdict(list)
    for row in rows:
        row["partition"] = "UNAVAILABLE"
        if row["feature"]["status"] == "OK" and cutoff:
            if row["session_date"] >= boundary:
                row["partition"] = "HOLDOUT"
            elif (timestamp(row["decided_at"]) < cutoff - timedelta(days=EMBARGO_DAYS)
                  and row["closed_at"] and timestamp(row["closed_at"]) < cutoff - timedelta(days=EMBARGO_DAYS)):
                row["partition"] = "TRAIN"
            else:
                row["partition"] = "PURGED_OR_EMBARGOED"
        if row["partition"] in ("TRAIN", "HOLDOUT"):
            groups[(tuple(row["stratum"]), row["partition"])].append(row)
    insufficiency = []
    if any(r["feature"]["status"] != "OK" for r in rows):
        insufficiency.append("MISSING_OR_EXCLUDED_FEATURE_ROWS")
    if any(r["strategy_return_pct"] is None for r in rows):
        insufficiency.append("MISSING_CLOSED_STRATEGY_OUTCOMES")
    if any(r["partition"] == "PURGED_OR_EMBARGOED" for r in rows):
        insufficiency.append("PURGED_OVERLAPPING_OR_UNPROVABLE_EXIT_TIMES")
    if not rows:
        insufficiency.append("NO_CANDIDATE_ROWS")
    result = {"version": VERSION, "packet_id": packet["packet_id"],
              "registry": registry(), "registry_hash": digest(registry()),
              "input_packet_hash": digest({**packet, "analysis_rows": sorted(
                  packet.get("analysis_rows", []), key=lambda r: r["decision_ref"])}),
              "status": "INPUT_UNAVAILABLE" if not any(r["feature"]["status"] == "OK" for r in rows) else "INSUFFICIENT",
              "verdict": "PREREGISTER_REPLAY", "holdout_boundary": boundary,
              "holdout_status": "RETROSPECTIVE_DIAGNOSTIC_NOT_PROSPECTIVE",
              "insufficiency_reasons": insufficiency,
              "limitations": ["Not a full BUY/SELL agent replay", "No automatic SHADOW/LIVE promotion",
                              "Price-return diagnostics are not weighted ledger or portfolio returns",
                              "Missing exact exit time uses Packet as_of upper bound for conservative purge",
                              "Snapshot hashes prove identity, not external source authenticity or historical availability",
                              "No causal claims; no parameter tuning", "Candidate outcomes and broker fills not pooled"],
              "rows": rows,
              "strata": [{"stratum": list(key[0]), "partition": key[1],
                          "trials": {trial: _metrics(members, trial) for trial, _ in TRIALS}}
                         for key, members in sorted(groups.items())]}
    if any(s["partition"] == "HOLDOUT" and s["trials"]["baseline"]["status"] == "EXPLORATORY_ONLY" for s in result["strata"]):
        result["status"] = "EXPLORATORY_ONLY"
    else:
        result["insufficiency_reasons"].append("HOLDOUT_MINIMUM_30_CLOSED_ROWS_AND_20_SESSION_DATES_NOT_MET")
    result["study_id"] = digest(result)
    return result
