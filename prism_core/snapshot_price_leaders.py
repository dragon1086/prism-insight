"""Descriptive KR observed-group returns, never screening or trading inputs.

Reuses an already loaded snapshot and KIS classification; does not fetch data.
These are intraday price performers among observed issues, not business leaders,
direct competitors, market-cap leaders, or an exchange-wide ranking.
"""
import datetime
import hashlib
import json
import math
import os
import re
from collections import Counter
from numbers import Real


def enabled() -> bool:
    return os.getenv("PRISM_REPORT_INSIGHT_PREFETCH", "0").strip().lower() in {"1", "true"}


def _date(value):
    if not isinstance(value, str):
        return None
    if len(value) == 8 and value.isdigit():
        value = f"{value[:4]}-{value[4:6]}-{value[6:]}"
    try:
        return datetime.date.fromisoformat(value).isoformat() if len(value) == 10 else None
    except ValueError:
        return None


def build_snapshot_price_leaders(snapshot, previous, sector_map, *, prior_date,
                                 source, trigger_mode) -> dict:
    """Top three per observed classification, with missingness and ties explicit."""
    observed = _date(getattr(snapshot, "attrs", {}).get("observed_date"))
    prior = _date(prior_date)
    requested_count = getattr(snapshot, "attrs", {}).get("requested_universe")
    if type(requested_count) is not int or requested_count < len(snapshot):
        requested_count = None
    packet = {
        "contract": "snapshot_price_leaders_v1", "contract_version": 1, "status": "UNKNOWN", "market": "KR",
        "metric": "snapshot_vs_previous_close_return_pct",
        "classification_kind": "kis_sector", "source": None,
        "observed_date": observed, "prior_date": prior,
        "trigger_mode": trigger_mode, "intraday": True,
        "is_business_leadership": False, "is_market_cap_rank": False,
        "universe_scope": "observed_snapshot_only", "groups": {},
        "universe_sha256": hashlib.sha256(json.dumps(sorted(str(ticker) for ticker in snapshot.index),
                                                    separators=(",", ":")).encode()).hexdigest(),
        "coverage": {"requested_count": requested_count, "observed_count": len(snapshot), "eligible_count": 0,
                     "missing_classification_count": 0, "missing_previous_count": 0,
                     "invalid_price_count": 0},
        "limitations": ["KIS classifications are coarse and do not establish direct competitors.",
                        "Intraday returns are not completed-session returns or recommendations."],
    }
    if (not isinstance(source, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", source)
            or source.lower() == "unknown"):
        packet["reason"] = "missing_or_invalid_source"
        return packet
    packet["source"] = source
    if not observed or not prior or prior >= observed:
        packet["reason"] = "missing_or_invalid_observed_dates"
        return packet
    if not isinstance(sector_map, dict) or not sector_map:
        packet["coverage"]["missing_classification_count"] = len(snapshot)
        packet["reason"] = "missing_classification"
        return packet
    if ("Close" not in snapshot.columns or "Close" not in previous.columns
            or not snapshot.index.is_unique or not previous.index.is_unique
            or not snapshot.columns.is_unique or not previous.columns.is_unique):
        packet["reason"] = "invalid_snapshot_shape"
        return packet

    groups = {}
    coverage = packet["coverage"]
    for ticker in snapshot.index:
        group = sector_map.get(str(ticker))
        if (not isinstance(group, str) or not group.strip()
                or group.strip().lower() in {"unknown", "n/a", "미분류", "미확인"}):
            coverage["missing_classification_count"] += 1
            continue
        if ticker not in previous.index:
            coverage["missing_previous_count"] += 1
            continue
        try:
            prices = (snapshot.at[ticker, "Close"], previous.at[ticker, "Close"])
            # bool and numpy.bool_ are not prices, even though float accepts them.
            if any(isinstance(value, bool) or not isinstance(value, (Real, str)) for value in prices):
                raise ValueError("invalid price type")
            current, prior_close = (float(value) for value in prices)
            if not all(math.isfinite(v) and v > 0 for v in (current, prior_close)):
                raise ValueError("invalid price")
            change = round((current / prior_close - 1) * 100, 8)
            if not math.isfinite(change):
                raise ValueError("invalid return")
        except (TypeError, ValueError, OverflowError):
            coverage["invalid_price_count"] += 1
            continue
        groups.setdefault(group.strip(), []).append({"ticker": str(ticker), "return_pct": change})
        coverage["eligible_count"] += 1

    for group, rows in sorted(groups.items()):
        rows.sort(key=lambda row: (-row["return_pct"], row["ticker"]))
        ties = Counter(row["return_pct"] for row in rows)
        leaders = rows[:3]
        for row in leaders:
            row["rank"] = 1 + sum(other["return_pct"] > row["return_pct"] for other in rows)
            row["tie_count"] = ties[row["return_pct"]]
        packet["groups"][group] = {
            "leaders": leaders, "eligible_count": len(rows),
            "cutoff_ties_omitted": sum(row["return_pct"] == leaders[-1]["return_pct"] for row in rows[3:]),
            "tie_break": "ticker_ascending", "return_rounding_decimals": 8,
        }
    if not groups:
        packet["reason"] = "no_comparable_classified_prices"
    else:
        packet["status"] = "OK" if coverage["eligible_count"] == coverage["observed_count"] else "PARTIAL"
    return packet


def optional_snapshot_price_leaders(snapshot, previous, sector_map, *, prior_date,
                                    source, trigger_mode) -> dict | None:
    if not enabled():
        return None
    try:
        return build_snapshot_price_leaders(snapshot, previous, sector_map,
                                            prior_date=prior_date, source=source, trigger_mode=trigger_mode)
    except Exception:  # noqa: BLE001 - optional report metadata cannot abort selected candidates
        return {
            "contract": "snapshot_price_leaders_v1", "contract_version": 1, "status": "UNKNOWN", "market": "KR",
            "reason": "invalid_optional_snapshot_input", "source": None,
            "metric": "snapshot_vs_previous_close_return_pct", "classification_kind": "kis_sector",
            "observed_date": None, "prior_date": None, "intraday": True,
            "is_business_leadership": False, "is_market_cap_rank": False,
            "universe_scope": "observed_snapshot_only", "universe_sha256": None,
            "groups": {}, "coverage": {"requested_count": None, "observed_count": None, "eligible_count": 0},
        }
