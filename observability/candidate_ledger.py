"""Permanent ledger of every screening candidate and its forward price path.

Observation only: rows are built from frames the selection already scored and
never feed back into ranking, JSON, prompts or orders. Every public entry point
fails open (logs a warning, returns a neutral value) so a ledger or provider
problem can never stop a screening or tracker batch.

Units: ``*_pct`` and ``ret_*s`` columns are percent (12.5 == +12.5%). Sessions
are counted from the first trading session after ``anchor_date`` (t+1 == 1).
Entry descriptors use daily bars strictly before the anchor session plus the
captured anchor close/volume, so a morning capture never sees its own day's
final bar (no lookahead).
"""
from __future__ import annotations

import json
import logging
import math
import os
import sqlite3
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

import pandas as pd

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[1]
TABLE = "candidate_ledger"
HORIZON_SESSIONS = 30
RETURN_SESSIONS = (7, 14, 30)
TOUCH_UP_PCT = 20.0
TOUCH_DOWN_PCT = -7.0
BIG_WINNER_PCT = 50.0
# Rows that never reach 30 sessions (delisting, long suspension) stop retrying.
EXPIRE_AFTER_DAYS = 180
# Descriptors need MA200 plus slack for holidays; outcomes need only the window.
DESCRIPTOR_LOOKBACK_DAYS = 400
# A captured close this far from the provider's adjusted anchor close implies a
# corporate action; evaluate on the provider basis instead of mixing bases.
BASIS_RATIO_BOUNDS = (0.55, 1.8)
# Weekly pass; open rows wait ~6 weeks for 30 sessions, so the backlog spans many batches.
DEFAULT_MAX_TICKERS = 1500
DEFAULT_SLEEP_SEC = {"KR": 0.5, "US": 0.2}

_KEY = ("market", "trade_date", "mode", "trigger_type", "ticker")
_CAPTURE_COLUMNS = (
    ("name", "TEXT"), ("rank_in_trigger", "INTEGER"), ("candidates_in_trigger", "INTEGER"),
    ("composite_score", "REAL"), ("final_score", "REAL"), ("rs_score", "REAL"),
    ("extension_score", "REAL"), ("score_column", "TEXT"), ("close", "REAL"),
    ("volume", "REAL"), ("amount", "REAL"), ("change_rate", "REAL"),
    ("selected", "INTEGER"), ("selection_channel", "TEXT"), ("selected_trigger", "TEXT"),
    ("market_regime", "TEXT"), ("features_json", "TEXT"),
)
_DESCRIPTOR_COLUMNS = (
    ("above_ma50", "INTEGER"), ("ma50_rising", "INTEGER"), ("ma20_gt_ma50", "INTEGER"),
    ("above_ma200", "INTEGER"), ("vol_ratio_50", "REAL"), ("updown_vol_ratio_50", "REAL"),
    ("vol_dryup_10_50", "REAL"), ("pocket_pivot", "INTEGER"),
)
_OUTCOME_COLUMNS = (
    ("anchor_date", "TEXT"), ("evaluated_through", "TEXT"), ("sessions_observed", "INTEGER"),
    ("outcome_basis", "TEXT"), ("mfe_pct", "REAL"), ("mae_pct", "REAL"),
    ("peak_session", "INTEGER"), ("peak_date", "TEXT"), ("ret_7s", "REAL"), ("ret_14s", "REAL"),
    ("ret_30s", "REAL"), ("hit_20_before_stop_7", "INTEGER"), ("hit_50", "INTEGER"),
)
_STATE_COLUMNS = (
    ("outcome_status", "TEXT"), ("descriptors_at", "TEXT"), ("last_attempt_at", "TEXT"),
    ("created_at", "TEXT"), ("updated_at", "TEXT"),
)
_ALL_COLUMNS = _CAPTURE_COLUMNS + _DESCRIPTOR_COLUMNS + _OUTCOME_COLUMNS + _STATE_COLUMNS
_OPEN_STATUSES = ("PENDING", "PARTIAL")

# First present column wins; KR and US frames use different spellings.
_FIELD_ALIASES = {
    "name": ("stock_name", "CompanyName", "Company Name", "종목명"),
    "composite_score": ("composite_score", "CompositeScore"),
    "final_score": ("final_score", "FinalScore"),
    "rs_score": ("rs_score", "RSScore"),
    "extension_score": ("extension_score", "ExtensionScore"),
    "close": ("Close",),
    "volume": ("Volume",),
    "amount": ("Amount",),
    "change_rate": ("prev_day_change_rate", "DailyChange", "ChangeRate", "change_rate"),
}


def enabled() -> bool:
    return os.getenv("CANDIDATE_LEDGER_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _db_path(db_path) -> Path:
    if db_path:
        return Path(db_path)
    configured = os.getenv("CANDIDATE_LEDGER_DB", "").strip()
    # Own file: the main DB is copied to other servers several times a day.
    return Path(configured) if configured else ROOT / "runtime" / "candidate_ledger.sqlite"


def _iso_day(value) -> str:
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return pd.Timestamp(text).date().isoformat()


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _scalar(value):
    """JSON-safe scalar for features_json; anything else is dropped."""
    if hasattr(value, "item") and not isinstance(value, (str, bytes)):
        try:
            value = value.item()
        except (ValueError, AttributeError):
            return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        return value if math.isfinite(float(value)) else None
    if isinstance(value, str):
        return value[:200]
    return None


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the table if missing and add any column a newer version introduced."""
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {TABLE} (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "market TEXT NOT NULL, trade_date TEXT NOT NULL, mode TEXT NOT NULL, "
        "trigger_type TEXT NOT NULL, ticker TEXT NOT NULL, "
        "UNIQUE(market, trade_date, mode, trigger_type, ticker))")
    existing = {row[1] for row in connection.execute(f"PRAGMA table_info({TABLE})")}
    for column, kind in _ALL_COLUMNS:
        if column not in existing:
            connection.execute(f"ALTER TABLE {TABLE} ADD COLUMN {column} {kind}")
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_status ON {TABLE}(market, outcome_status, ticker)")


# --- capture -----------------------------------------------------------------

def build_rows(trigger_candidates: Mapping[str, pd.DataFrame], final_result: Mapping[str, pd.DataFrame],
               *, score_column: str | None = None) -> list[dict]:
    """Rows for every scored candidate; reads the frames, never mutates them."""
    selected = {}
    for trigger_name, frame in (final_result or {}).items():
        if frame is None or frame.empty:
            continue
        for ticker in frame.index:
            channel = None
            if "SelectionChannel" in frame.columns:
                channel = frame.loc[[ticker], "SelectionChannel"].iloc[0]
            selected.setdefault(str(ticker), (trigger_name, None if channel is None else str(channel)))
    rows = []
    for trigger_name, frame in (trigger_candidates or {}).items():
        if frame is None or frame.empty:
            continue
        if score_column and score_column in frame.columns:
            ordered = list(frame[score_column].sort_values(ascending=False, kind="mergesort").index)
        else:
            ordered = list(frame.index)
        for rank, ticker in enumerate(ordered, start=1):
            record = frame.loc[[ticker]].iloc[0]
            row = {"trigger_type": str(trigger_name), "ticker": str(ticker), "rank_in_trigger": rank,
                   "candidates_in_trigger": len(ordered), "score_column": score_column}
            for field, aliases in _FIELD_ALIASES.items():
                column = next((c for c in aliases if c in frame.columns), None)
                value = record[column] if column else None
                row[field] = (str(value) if value is not None and not pd.isna(value) else None) \
                    if field == "name" else _number(value)
            via = selected.get(str(ticker))
            row["selected"] = 1 if via else 0
            row["selected_trigger"] = via[0] if via else None
            row["selection_channel"] = via[1] if via else None
            features = {str(k): _scalar(v) for k, v in record.items()}
            row["features_json"] = json.dumps({k: v for k, v in features.items() if v is not None},
                                              ensure_ascii=False, sort_keys=True)
            rows.append(row)
    return rows


def record(rows: Iterable[Mapping[str, Any]] | None, *, market: str, trade_date, mode: str,
           market_regime: str | None = None, db_path=None, log: logging.Logger | None = None) -> int:
    """Upsert capture rows; a re-run replaces capture fields and resets outcomes. Fail-open."""
    target = log or logger
    if not rows or not enabled():
        return 0
    try:
        now = datetime.now().isoformat(timespec="seconds")
        day = _iso_day(trade_date)
        capture = [c for c, _ in _CAPTURE_COLUMNS]
        reset = [c for c, _ in _DESCRIPTOR_COLUMNS + _OUTCOME_COLUMNS] + ["descriptors_at", "last_attempt_at"]
        columns = list(_KEY) + capture + ["anchor_date", "outcome_status", "created_at", "updated_at"]
        updates = ", ".join([f"{c}=excluded.{c}" for c in capture + ["anchor_date", "updated_at"]]
                            + [f"{c}=NULL" for c in reset if c != "anchor_date"]
                            + ["outcome_status='PENDING'"])
        sql = (f"INSERT INTO {TABLE} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))}) "
               f"ON CONFLICT({', '.join(_KEY)}) DO UPDATE SET {updates}")
        values = []
        for row in rows:
            merged = {**row, "market": str(market).upper(), "trade_date": day, "mode": str(mode),
                      "market_regime": market_regime, "anchor_date": day, "outcome_status": "PENDING",
                      "created_at": now, "updated_at": now}
            values.append([merged.get(c) for c in columns])
        path = _db_path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=30)
        try:
            with connection:
                ensure_schema(connection)
                connection.executemany(sql, values)
        finally:
            connection.close()
        target.info("[CANDIDATE_LEDGER] recorded %d candidates market=%s trade_date=%s mode=%s",
                    len(values), market, day, mode)
        return len(values)
    except Exception as exc:  # noqa: BLE001 - observation must not stop the batch
        target.warning("[CANDIDATE_LEDGER] record unavailable: %s: %s", type(exc).__name__, exc)
        return 0


# --- pure math ---------------------------------------------------------------

def normalize_bars(frame) -> pd.DataFrame:
    """Flat Open/High/Low/Close/Volume frame indexed by ISO date strings, ascending."""
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    aliases = {"open": "Open", "시가": "Open", "high": "High", "고가": "High", "low": "Low",
               "저가": "Low", "close": "Close", "종가": "Close", "volume": "Volume", "거래량": "Volume"}
    renamed = {}
    for column in frame.columns:
        key = aliases.get(str(column).strip().lower()) or aliases.get(str(column).strip())
        if key and key not in renamed.values():
            renamed[column] = key
    bars = frame[list(renamed)].rename(columns=renamed)
    if not {"High", "Low", "Close"}.issubset(bars.columns):
        return pd.DataFrame()
    bars = bars.apply(pd.to_numeric, errors="coerce")
    index = pd.to_datetime(bars.index)
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    bars.index = [d.date().isoformat() for d in index]
    bars = bars[~bars.index.duplicated(keep="last")].sort_index()
    return bars.dropna(subset=["High", "Low", "Close"])


def _mean(values):
    return sum(values) / len(values) if values else None


def _flag(condition):
    return None if condition is None else int(bool(condition))


def compute_descriptors(bars: pd.DataFrame, anchor_date: str, anchor_close=None, anchor_volume=None) -> dict:
    """Trend/volume descriptors at the anchor session; insufficient history -> None."""
    result = {column: None for column, _ in _DESCRIPTOR_COLUMNS}
    if bars is None or bars.empty:
        return result
    prior = bars[bars.index < anchor_date]
    close = _number(anchor_close)
    volume = _number(anchor_volume)
    if anchor_date in bars.index:
        close = close if close is not None else _number(bars.loc[anchor_date, "Close"])
        if volume is None and "Volume" in bars.columns:
            volume = _number(bars.loc[anchor_date, "Volume"])
    if close is None:
        return result
    closes = [float(x) for x in prior["Close"]] + [close]
    volumes = None
    if "Volume" in prior.columns and volume is not None:
        prior_volume = pd.to_numeric(prior["Volume"], errors="coerce")
        if not prior_volume.isna().any():
            volumes = [float(x) for x in prior_volume] + [volume]
    n = len(closes)

    def ma(length, end=n):
        return _mean(closes[end - length:end]) if end >= length else None

    ma20, ma50, ma200 = ma(20), ma(50), ma(200)
    ma50_prev = ma(50, n - 20) if n - 20 >= 50 else None
    result["above_ma50"] = _flag(None if ma50 is None else close > ma50)
    result["ma20_gt_ma50"] = _flag(None if ma50 is None else ma20 > ma50)
    result["above_ma200"] = _flag(None if ma200 is None else close > ma200)
    result["ma50_rising"] = _flag(None if ma50_prev is None else ma50 > ma50_prev)
    if volumes is None or n < 51:
        return result
    prior50 = volumes[-51:-1]
    avg50 = _mean(prior50)
    if avg50 and avg50 > 0:
        result["vol_ratio_50"] = volume / avg50
        result["vol_dryup_10_50"] = _mean(volumes[-11:-1]) / avg50
    up = sum(volumes[i] for i in range(n - 50, n) if closes[i] > closes[i - 1])
    down = sum(volumes[i] for i in range(n - 50, n) if closes[i] < closes[i - 1])
    result["updown_vol_ratio_50"] = up / down if down > 0 else None
    down_volumes = [volumes[i] for i in range(n - 11, n - 1) if closes[i] < closes[i - 1]]
    result["pocket_pivot"] = int(closes[-1] > closes[-2] and volume > max(down_volumes, default=0.0)
                                 and close >= ma50)
    return result


def compute_outcomes(bars: pd.DataFrame, anchor_date: str, base: float) -> dict:
    """Forward MFE/MAE/peak/returns over sessions t+1..t+30 against ``base``.

    A session that touches both +20% and -7% counts as stop-first (conservative:
    daily bars cannot order the intraday path).
    """
    result = {column: None for column, _ in _OUTCOME_COLUMNS if column not in {"anchor_date", "outcome_basis"}}
    future = bars[bars.index > anchor_date].iloc[:HORIZON_SESSIONS] if bars is not None and not bars.empty \
        else pd.DataFrame()
    result["sessions_observed"] = len(future)
    if future.empty or not base or base <= 0:
        return result
    highs = [float(x) for x in future["High"]]
    lows = [float(x) for x in future["Low"]]
    closes = [float(x) for x in future["Close"]]
    peak = max(range(len(highs)), key=lambda i: (highs[i], -i))
    result.update(
        evaluated_through=future.index[-1],
        mfe_pct=(highs[peak] / base - 1) * 100,
        mae_pct=(min(lows) / base - 1) * 100,
        peak_session=peak + 1,
        peak_date=future.index[peak],
        hit_50=int((highs[peak] / base - 1) * 100 >= BIG_WINNER_PCT),
    )
    for horizon in RETURN_SESSIONS:
        if len(closes) >= horizon:
            result[f"ret_{horizon}s"] = (closes[horizon - 1] / base - 1) * 100
    for high, low in zip(highs, lows):
        if (low / base - 1) * 100 <= TOUCH_DOWN_PCT:
            result["hit_20_before_stop_7"] = 0
            break
        if (high / base - 1) * 100 >= TOUCH_UP_PCT:
            result["hit_20_before_stop_7"] = 1
            break
    return result


def _basis(bars: pd.DataFrame, anchor_date: str, captured_close, captured_volume):
    """Return (base close, descriptor volume, basis label)."""
    captured = _number(captured_close)
    historical = _number(bars.loc[anchor_date, "Close"]) if anchor_date in bars.index else None
    if captured is None:
        hist_volume = _number(bars.loc[anchor_date, "Volume"]) if historical and "Volume" in bars.columns else None
        return historical, hist_volume, "history_close" if historical else None
    if historical:
        low, high = BASIS_RATIO_BOUNDS
        if not low <= historical / captured <= high:
            hist_volume = _number(bars.loc[anchor_date, "Volume"]) if "Volume" in bars.columns else None
            return historical, hist_volume, "history_close"
    return captured, _number(captured_volume), "captured_close"


# --- outcome pass ------------------------------------------------------------

def _default_fetcher(market: str) -> Callable[[str, str, str], pd.DataFrame]:
    if market == "KR":
        from cores.market_data import get_market_ohlcv_by_date

        def fetch(ticker, start, end):
            return get_market_ohlcv_by_date(start.replace("-", ""), end.replace("-", ""), ticker)
        return fetch

    import yfinance as yf

    def fetch(ticker, start, end):
        stop = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        return yf.Ticker(ticker).history(start=start, end=stop, auto_adjust=True)
    return fetch


def update_candidate_outcomes(market: str, *, db_path=None, fetch_bars=None, today: str | None = None,
                              max_tickers: int | None = None, sleep_sec: float | None = None,
                              log: logging.Logger | None = None) -> dict:
    """Fill descriptors and forward outcomes for open rows; one fetch per ticker. Fail-open."""
    target = log or logger
    stats = {"tickers": 0, "rows": 0, "complete": 0, "fetch_errors": 0, "expired": 0}
    if not enabled():
        return stats
    try:
        market = str(market).upper()
        today = _iso_day(today) if today else date.today().isoformat()
        max_tickers = int(max_tickers if max_tickers is not None
                          else os.getenv("CANDIDATE_LEDGER_MAX_TICKERS", DEFAULT_MAX_TICKERS))
        sleep_sec = float(sleep_sec if sleep_sec is not None
                          else os.getenv("CANDIDATE_LEDGER_FETCH_SLEEP_SEC", DEFAULT_SLEEP_SEC.get(market, 0.2)))
        path = _db_path(db_path)
        if not path.exists():
            return stats                    # nothing captured yet
        connection = sqlite3.connect(path, timeout=30)
        connection.row_factory = sqlite3.Row
    except Exception as exc:  # noqa: BLE001
        target.warning("[CANDIDATE_LEDGER] outcome pass unavailable: %s: %s", type(exc).__name__, exc)
        return stats
    try:
        with connection:
            ensure_schema(connection)
        tickers = [row["ticker"] for row in connection.execute(
            f"SELECT ticker, MIN(COALESCE(last_attempt_at, '')) AS attempted, MIN(anchor_date) AS first "
            f"FROM {TABLE} WHERE market=? AND COALESCE(outcome_status, 'PENDING') IN (?, ?) "
            "AND anchor_date < ? GROUP BY ticker ORDER BY attempted, first, ticker LIMIT ?",
            (market, *_OPEN_STATUSES, today, max_tickers))]
        fetch = fetch_bars or _default_fetcher(market)
        expire_before = (date.fromisoformat(today) - timedelta(days=EXPIRE_AFTER_DAYS)).isoformat()
        for position, ticker in enumerate(tickers):
            if position and sleep_sec > 0:
                time.sleep(sleep_sec)
            rows = [dict(r) for r in connection.execute(
                f"SELECT * FROM {TABLE} WHERE market=? AND ticker=? AND anchor_date < ? "
                "AND COALESCE(outcome_status, 'PENDING') IN (?, ?)", (market, ticker, today, *_OPEN_STATUSES))]
            if not rows:
                continue
            stats["tickers"] += 1
            first = min(r["anchor_date"] for r in rows)
            lookback = DESCRIPTOR_LOOKBACK_DAYS if any(r["descriptors_at"] is None for r in rows) else 7
            start = (date.fromisoformat(first) - timedelta(days=lookback)).isoformat()
            try:
                bars = normalize_bars(fetch(ticker, start, today))
            except Exception as exc:  # noqa: BLE001 - one ticker cannot stop the pass
                target.warning("[CANDIDATE_LEDGER] %s %s bars unavailable: %s", market, ticker, type(exc).__name__)
                bars = pd.DataFrame()
            if bars.empty:
                stats["fetch_errors"] += 1
            now = datetime.now().isoformat(timespec="seconds")
            with connection:
                for row in rows:
                    update = {"last_attempt_at": now, "updated_at": now}
                    if not bars.empty:
                        anchor = row["anchor_date"]
                        base, volume, basis = _basis(bars, anchor, row["close"], row["volume"])
                        if row["descriptors_at"] is None:
                            update.update(compute_descriptors(bars, anchor, base, volume))
                            update["descriptors_at"] = now
                        if base:
                            outcome = compute_outcomes(bars, anchor, base)
                            update.update(outcome, outcome_basis=basis)
                            observed = outcome["sessions_observed"]
                            update["outcome_status"] = ("COMPLETE" if observed >= HORIZON_SESSIONS
                                                        else "PARTIAL" if observed else "PENDING")
                    if update.get("outcome_status") != "COMPLETE" and row["anchor_date"] < expire_before:
                        update["outcome_status"] = "EXPIRED"
                        stats["expired"] += 1
                    stats["complete"] += int(update.get("outcome_status") == "COMPLETE")
                    stats["rows"] += 1
                    assignments = ", ".join(f"{column}=?" for column in update)
                    connection.execute(f"UPDATE {TABLE} SET {assignments} WHERE id=?",
                                       [*update.values(), row["id"]])
        stats["open_tickers_left"] = connection.execute(
            f"SELECT COUNT(DISTINCT ticker) FROM {TABLE} WHERE market=? AND outcome_status IN (?, ?)",
            (market, *_OPEN_STATUSES)).fetchone()[0]
        target.info("[CANDIDATE_LEDGER] outcome pass market=%s %s", market, stats)
    except Exception as exc:  # noqa: BLE001 - observation must not stop the tracker
        target.warning("[CANDIDATE_LEDGER] outcome pass failed: %s: %s", type(exc).__name__, exc)
    finally:
        connection.close()
    return stats


__all__ = [
    "build_rows",
    "compute_descriptors",
    "compute_outcomes",
    "enabled",
    "ensure_schema",
    "normalize_bars",
    "record",
    "update_candidate_outcomes",
]
