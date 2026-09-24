"""Conservative completed-session cache; not a source of current quotes.

Adjusted bars are reusable only with a matching earlier observation in the fresh
response. This detects overlapping adjustment changes, not every possible vendor
correction. Fresh valid provider data must always take precedence at the caller.
"""

from datetime import datetime, timezone
import fcntl
from functools import lru_cache
import hashlib
from importlib.metadata import version
import json
import math
from numbers import Real
import os
from pathlib import Path
import re
import tempfile

import pandas as pd
import pandas_market_calendars as mcal


PRICE_BASIS = {
    "provider": "yfinance", "version": version("yfinance"),
    "auto_adjust": True, "repair": False, "interval": "1d", "prepost": False,
    "back_adjust": False, "actions": False, "market": "US", "currency": "USD",
}
_FIELDS = ("Open", "High", "Low", "Close", "Volume")
_SCHEMA = 1


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{8}", value):
        raise ValueError("Invalid session date")
    return datetime.strptime(value, "%Y%m%d").date()


def _utc(value):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError("Timezone-aware capture timestamp required")
    return stamp.tz_convert("UTC")


def _bar(row):
    try:
        if "Repaired?" in row and (pd.isna(row["Repaired?"]) or row["Repaired?"] != False):
            return None
        values = {field: row[field] for field in _FIELDS}
        if any(isinstance(v, bool) or not isinstance(v, Real) or not math.isfinite(v) for v in values.values()):
            return None
        if any(values[field] <= 0 for field in _FIELDS[:-1]) or values["Volume"] < 0:
            return None
        if not math.isfinite(float(values['Close']) * float(values['Volume'])):
            return None
        if not (values["Low"] <= min(values["Open"], values["Close"])
                <= max(values["Open"], values["Close"]) <= values["High"]):
            return None
        return {field: float(value) for field, value in values.items()}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def _history(frame):
    if (not isinstance(frame, pd.DataFrame) or frame.empty
            or not isinstance(frame.index, pd.DatetimeIndex)
            or isinstance(frame.columns, pd.MultiIndex) or not frame.columns.is_unique
            or not set(_FIELDS).issubset(frame.columns)
            or frame.index.hasnans or not frame.index.is_unique
            or not frame.index.is_monotonic_increasing
            or not frame.index.equals(frame.index.normalize())):
        return {}
    rows = {stamp.strftime("%Y%m%d"): _bar(row) for stamp, row in frame.iterrows()}
    return rows if len(rows) == len(frame) else {}


def histories_share_basis(reference_history, candidate_history, target_YYYYMMDD) -> bool:
    """Require preceding shared observations; any changed/invalid overlap fails.

    This compares observed values only. Callers must use identical provider
    settings/version and exact symbol identity for both fresh histories.
    """
    try:
        _date(target_YYYYMMDD)
        reference, candidate = _history(reference_history), _history(candidate_history)
        overlap = {day for day in set(reference).intersection(candidate) if day < target_YYYYMMDD}
        return bool(overlap) and all(reference[day] is not None and reference[day] == candidate[day] for day in overlap)
    except (TypeError, ValueError, OverflowError):
        return False


def _stored_bar(value):
    return _bar(value) if isinstance(value, dict) and set(value) == set(_FIELDS) else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate cache JSON key")
        result[key] = value
    return result


@lru_cache(maxsize=128)
def _session_closes(start, end):
    schedule = mcal.get_calendar("NYSE").schedule(start_date=_date(start), end_date=_date(end))
    return {stamp.strftime("%Y%m%d"): _utc(row["market_close"]) for stamp, row in schedule.iterrows()}


class DailyBarCache:
    """Caller-selected storage, no network, policy flags, or fallback dates.

    ``now`` optionally freezes an aware instant for deterministic tests. Public
    methods fail closed on invalid input, corruption, calendar or filesystem I/O.
    Concurrent writers serialize per symbol and never replace a newer capture.
    """

    def __init__(self, root: Path, now: datetime | None = None):
        self.root = Path(root)
        self.now = now

    def _now(self):
        return _utc(self.now if self.now is not None else datetime.now(timezone.utc))

    def _directory(self, symbol):
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?", symbol):
            raise ValueError("Invalid exact symbol")
        return self.root / hashlib.sha256(symbol.encode("utf-8")).hexdigest()

    def _closes(self, dates):
        return _session_closes(min(dates), max(dates))

    def save_history(self, symbol, normalized_daily_frame) -> int:
        saved = 0
        try:
            rows = _history(normalized_daily_frame)
            if not rows:
                return 0
            now = self._now()
            closes = self._closes(rows)
            valid = {day: bar for day, bar in rows.items()
                     if bar is not None and day in closes and closes[day] <= now}
            if not valid:
                return 0
            directory = self._directory(symbol)
            directory.mkdir(parents=True, exist_ok=True)
            anchors = {}
            with (directory / ".lock").open("a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                for day, bar in valid.items():
                    payload = {"schema": _SCHEMA, "symbol": symbol, "session_date": day,
                               "captured_at": now.isoformat(), "price_basis": dict(PRICE_BASIS),
                               "bar": bar, "anchors": dict(anchors)}
                    path = directory / f"{day}.json"
                    try:
                        existing = json.loads(path.read_text(), object_pairs_hook=_unique_object)
                        if _utc(existing["captured_at"]) >= now:
                            anchors[day] = bar
                            continue
                    except (OSError, ValueError, KeyError, TypeError):
                        pass
                    temporary = None
                    try:
                        with tempfile.NamedTemporaryFile(mode="w", dir=directory, suffix=".tmp", delete=False) as stream:
                            temporary = Path(stream.name)
                            json.dump(payload, stream, allow_nan=False, separators=(",", ":"))
                            stream.flush()
                            os.fsync(stream.fileno())
                        os.replace(temporary, path)
                        saved += 1
                    finally:
                        if temporary is not None:
                            temporary.unlink(missing_ok=True)
                    anchors[day] = bar
            return saved
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return saved

    def load_row(self, symbol, target_YYYYMMDD, current_normalized_history):
        try:
            _date(target_YYYYMMDD)
            path = self._directory(symbol) / f"{target_YYYYMMDD}.json"
            payload = json.loads(path.read_text(), object_pairs_hook=_unique_object)
            if (not isinstance(payload, dict) or type(payload.get("schema")) is not int or payload.get("schema") != _SCHEMA
                    or payload.get("symbol") != symbol or payload.get("session_date") != target_YYYYMMDD
                    or payload.get("price_basis") != PRICE_BASIS
                    or any(type(payload["price_basis"].get(key)) is not type(value) for key, value in PRICE_BASIS.items())):
                return None
            captured = _utc(payload["captured_at"])
            now = self._now()
            if captured > now:
                return None
            bar = _stored_bar(payload["bar"])
            anchors = payload["anchors"]
            if bar is None or not isinstance(anchors, dict) or not anchors:
                return None
            if any(_date(day) >= _date(target_YYYYMMDD) or _stored_bar(row) is None for day, row in anchors.items()):
                return None
            closes = self._closes([target_YYYYMMDD, *anchors])
            if any(day not in closes or closes[day] > captured for day in [target_YYYYMMDD, *anchors]):
                return None
            current = _history(current_normalized_history)
            overlap = set(anchors).intersection(current)
            if not overlap or any(current[day] != _stored_bar(anchors[day]) for day in overlap):
                return None
            return {**bar, "source": "daily_cache", "captured_at": captured.isoformat(),
                    "session_date": target_YYYYMMDD, "price_basis": dict(PRICE_BASIS)}
        except (OSError, ValueError, KeyError, TypeError, OverflowError):
            return None
