"""Provider-neutral temporal/scope assessment of archived price observations.

No calendars, feed-specific preference, price replacement or trading authority.
Metadata is supplied by the caller, not independently verified here. Later
retrieval of historical filings needs a separate version/availability contract.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timezone

_SCOPE = ("symbol", "interval", "session", "adjustment", "currency")
_TIMES = ("asof", "available_at", "captured_at")


def _aware(value):
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


@dataclass(frozen=True)
class PriceObservation:
    """asof is an actual snapshot instant or bar END, not a nominal future end.

    captured_at identifies the original acquisition, never the replay run time.
    finality and available_at must not be inferred from retrieval success.
    """

    source: str
    symbol: str | None
    interval: str | None
    session: str | None
    adjustment: str | None
    currency: str | None
    asof: datetime | None
    available_at: datetime | None
    captured_at: datetime | None
    final: bool | None


@dataclass(frozen=True)
class ObservationPolicy:
    """Explicit use-case expectations; no production age limit is chosen here.

    For completed daily bars, the caller supplies the last expected session end,
    which need not equal decision_at on weekends/holidays. For intraday snapshots
    expected_asof may equal decision_at. Calendar correctness is the caller's job.
    """

    decision_at: datetime
    expected_asof: datetime
    max_lag_seconds: float
    symbol: str
    interval: str
    session: str
    adjustment: str
    currency: str
    require_final: bool = True

    def __post_init__(self):
        if not _aware(self.decision_at) or not _aware(self.expected_asof):
            raise ValueError("Policy timestamps must be timezone-aware")
        if self.expected_asof.astimezone(timezone.utc) > self.decision_at.astimezone(timezone.utc):
            raise ValueError("Expected asof must not follow decision time")
        lag = self.max_lag_seconds
        try:
            valid_lag = type(lag) in (int, float) and math.isfinite(lag) and lag >= 0
        except OverflowError:
            valid_lag = False
        if not valid_lag:
            raise ValueError("Lag budget must be finite and nonnegative")
        if any(not isinstance(getattr(self, key), str) or not getattr(self, key).strip()
               for key in _SCOPE):
            raise ValueError("Expected scope fields must be nonempty strings")
        if type(self.require_final) is not bool:
            raise ValueError("require_final must be a boolean")


def assess_observation(observation: PriceObservation, policy: ObservationPolicy) -> dict:
    """Assess supplied metadata, identically for TV/KIS/yfinance or other feeds.

    Precedence: INVALID, INCOMPARABLE, UNKNOWN, STALE, FIT_FOR_COMPARISON.
    All applicable reasons survive even when a higher-priority status wins.
    FIT is not independent fact validation, completeness or permission to trade.
    """
    reasons = {"INVALID": [], "INCOMPARABLE": [], "UNKNOWN": [], "STALE": []}
    decision_at = policy.decision_at.astimezone(timezone.utc)
    expected_asof = policy.expected_asof.astimezone(timezone.utc)
    times = {}
    for key in _TIMES:
        value = getattr(observation, key)
        if value is None:
            reasons["UNKNOWN"].append(f"MISSING_{key.upper()}")
        elif not isinstance(value, datetime):
            reasons["INVALID"].append(f"INVALID_{key.upper()}")
        elif not _aware(value):
            reasons["UNKNOWN"].append(f"NAIVE_{key.upper()}")
        else:
            # Same-zone datetime arithmetic otherwise ignores a DST fold.
            times[key] = value.astimezone(timezone.utc)
            if times[key] > decision_at:
                reasons["INVALID"].append(f"FUTURE_{key.upper()}")

    lag = None
    if "asof" in times:
        lag = (expected_asof - times["asof"]).total_seconds()
        if lag < 0:
            reasons["INVALID"].append("ASOF_AFTER_EXPECTED")
        elif lag > policy.max_lag_seconds:
            reasons["STALE"].append("STALE_ASOF")
    for earlier, later in (("asof", "available_at"), ("available_at", "captured_at"),
                           ("asof", "captured_at")):
        if earlier in times and later in times and times[earlier] > times[later]:
            reasons["INVALID"].append(f"{earlier.upper()}_AFTER_{later.upper()}")

    for key in _SCOPE:
        value = getattr(observation, key)
        if value is None or (isinstance(value, str) and not value.strip()):
            reasons["UNKNOWN"].append(f"MISSING_{key.upper()}")
        elif not isinstance(value, str):
            reasons["INVALID"].append(f"INVALID_{key.upper()}")
        elif value != getattr(policy, key):
            reasons["INCOMPARABLE"].append(f"MISMATCH_{key.upper()}")

    if observation.final is not None and type(observation.final) is not bool:
        reasons["INVALID"].append("INVALID_FINALITY")
    elif policy.require_final:
        if observation.final is None:
            reasons["UNKNOWN"].append("MISSING_FINALITY")
        elif observation.final is False:
            reasons["INCOMPARABLE"].append("BAR_NOT_FINAL")
    if not isinstance(observation.source, str) or not observation.source.strip():
        reasons["UNKNOWN"].append("MISSING_SOURCE")

    return {
        "status": next((key for key, values in reasons.items() if values), "FIT_FOR_COMPARISON"),
        "source": observation.source if isinstance(observation.source, str) else None,
        "lag_seconds": lag,
        "reasons": [reason for values in reasons.values() for reason in values],
        "fact_validated": False,
        "execution_authorized": False,
    }
