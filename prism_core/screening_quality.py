"""Pure, observation-only completed-bar diagnostics; never an entry decision.

Dates are provider session labels, not UTC-converted instants. No exchange calendar
is inferred: absent sessions cannot be detected without a caller-owned calendar.
Unknown price adjustment remains unknown, including around splits/dividends.
"""

from datetime import date, datetime
import hashlib
import json
import math

import pandas as pd


def _session_date(value):
    if not isinstance(value, (str, date, datetime, pd.Timestamp)):
        raise ValueError("invalid session label")
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp != stamp.normalize():
        raise ValueError("session label must identify a daily bar")
    return stamp.date()


def build_screening_quality_context(
    history,
    requested_trade_date,
    *,
    source="yfinance",
    adjustment_basis="UNCONFIRMED",
    expected_completed_session=None,
):
    """Measure signed D20 on 21 closes strictly before the requested session.

    Accept a DataFrame with ascending unique daily labels and a single Close
    column. Reject invalid completed rows rather than dropping/stitching them.
    Current/future rows are excluded even when the current session has closed.
    A supplied expected session validates the last label only, not calendar
    continuity. Gain contribution = largest positive close change / sum of all
    positive close changes (None if no gains). No thresholds or scores apply.
    """
    result = {
        "schema_version": "screening_quality_context_v1",
        "status": "MISSING",
        "reason": None,
        "directional_efficiency_20": None,
        "one_day_gain_contribution_fraction": None,
        "completed_row_count": 0,
        "last_completed_session": None,
        "requested_trade_date": None,
        "expected_completed_session": None,
        "bar_finality": "PRIOR_SESSION_LABELS_ONLY",
        "freshness_status": "UNKNOWN",
        "calendar_continuity_status": "UNKNOWN",
        "source": str(source),
        "adjustment_basis": str(adjustment_basis),
        "scoring_applied": False,
        "compatibility_status": "NEEDS_SCENARIO",
        "excluded_current_row_count": 0,
        "excluded_future_row_count": 0,
    }
    evidence = []

    def finish(reason=None):
        result["reason"] = reason
        payload = {"context": result, "completed_closes": evidence}
        result["input_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
        ).hexdigest()
        return result

    try:
        requested = _session_date(requested_trade_date)
        result["requested_trade_date"] = requested.isoformat()
        expected = (
            _session_date(expected_completed_session)
            if expected_completed_session is not None else None
        )
        if expected is not None:
            result["expected_completed_session"] = expected.isoformat()
            if expected >= requested:
                return finish("INVALID_EXPECTED_SESSION")
    except (TypeError, ValueError, OverflowError):
        return finish("INVALID_SESSION_DATE")

    if (not isinstance(history, pd.DataFrame)
            or isinstance(history.columns, pd.MultiIndex)
            or list(history.columns).count("Close") != 1):
        return finish("INVALID_HISTORY_SHAPE")
    try:
        dates = [_session_date(value) for value in history.index]
    except (TypeError, ValueError, OverflowError):
        return finish("INVALID_HISTORY_DATES")
    if len(set(dates)) != len(dates):
        return finish("DUPLICATE_SESSION")
    if dates != sorted(dates):
        return finish("UNSORTED_SESSIONS")

    completed = []
    invalid = False
    for session, raw_close in zip(dates, history["Close"]):
        if session >= requested:
            field = ("excluded_current_row_count" if session == requested
                     else "excluded_future_row_count")
            result[field] += 1
            continue
        try:
            close = float(raw_close)
            valid = not isinstance(raw_close, bool) and math.isfinite(close) and close > 0
        except (TypeError, ValueError, OverflowError):
            close, valid = None, False
        evidence.append([session.isoformat(), close if valid else None])
        invalid |= not valid
        completed.append((session, close if valid else None))

    result["completed_row_count"] = len(completed)
    if completed:
        result["last_completed_session"] = completed[-1][0].isoformat()
    if expected is not None:
        result["freshness_status"] = (
            "MATCH" if completed and completed[-1][0] == expected else "MISMATCH"
        )
    if invalid:
        return finish("INVALID_COMPLETED_CLOSE")
    if result["freshness_status"] == "MISMATCH":
        return finish("EXPECTED_SESSION_MISMATCH")
    if len(completed) < 21:
        return finish("INSUFFICIENT_COMPLETED_HISTORY")

    closes = [row[1] for row in completed[-21:]]
    # Scale first so even finite, unusually large prices cannot overflow sums.
    scale = max(closes)
    scaled = [close / scale for close in closes]
    changes = [right - left for left, right in zip(scaled, scaled[1:])]
    total = math.fsum(abs(change) for change in changes)
    gains = [change for change in changes if change > 0]
    if gains:
        result["one_day_gain_contribution_fraction"] = max(gains) / math.fsum(gains)
    if total == 0:
        result["status"] = "FLAT"
    else:
        result["status"] = "OK"
        result["directional_efficiency_20"] = (scaled[-1] - scaled[0]) / total
    return finish()


def load_quality_candidates(metadata):
    """Whitelist observations from batch JSON, never copy them into a prompt.

    Only same-session v1 scalar fields emitted by this module are eligible for
    the existing candidate decision ledger. Unknown payload fields are dropped.
    """
    if not isinstance(metadata, dict):
        return {}
    candidates = metadata.get('screening_quality_candidates')
    if not isinstance(candidates, dict) or len(candidates) > 500:
        return {}
    try:
        expected = _session_date(metadata.get('trade_date')).isoformat()
    except (ValueError, TypeError, OverflowError):
        return {}
    keys = {
        'schema_version', 'status', 'reason', 'directional_efficiency_20',
        'one_day_gain_contribution_fraction', 'completed_row_count',
        'last_completed_session', 'requested_trade_date', 'expected_completed_session',
        'bar_finality', 'freshness_status', 'calendar_continuity_status',
        'source', 'adjustment_basis', 'scoring_applied', 'compatibility_status',
        'excluded_current_row_count', 'excluded_future_row_count', 'input_hash',
    }
    cleaned = {}
    for symbol, context in candidates.items():
        if (not isinstance(symbol, str) or len(symbol) > 16
                or not isinstance(context, dict)
                or context.get('schema_version') != 'screening_quality_context_v1'
                or context.get('requested_trade_date') != expected
                or not isinstance(context.get('status'), str)
                or context.get('status') not in {'OK', 'FLAT', 'MISSING'}
                or context.get('scoring_applied') is not False):
            continue
        row = {}
        for key in keys & context.keys():
            value = context[key]
            if value is None or isinstance(value, bool):
                row[key] = value
            elif isinstance(value, str) and len(value) <= 128:
                row[key] = value
            elif isinstance(value, (int, float)):
                try:
                    if math.isfinite(value):
                        row[key] = value
                except OverflowError:
                    pass
        cleaned[symbol] = row
    return cleaned
