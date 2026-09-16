"""Single bounded bulk data read in disposable subprocess; stdout is JSON only."""
import contextlib
import json
import sys
import threading
import time
from pathlib import Path

_KR_LOCK = threading.Lock()
_KR_SOURCE = None
KR_CACHE_DIR = Path(__file__).resolve().parents[1] / "runtime/oneil_kr_daily_cache"


def previous_session(trade_date, market="US"):
    import pandas as pd
    import pandas_market_calendars as mcal

    end = pd.Timestamp(trade_date) - pd.Timedelta(days=1)
    valid = mcal.get_calendar("XKRX" if market == "KR" else "NYSE").valid_days(
        start_date=end - pd.Timedelta(days=30), end_date=end)
    if len(valid) == 0:
        raise ValueError("missing_NYSE_calendar")
    return valid[-1].date().isoformat()


def market_days(trade_date, market="US"):
    import pandas as pd
    import pandas_market_calendars as mcal
    end = pd.Timestamp(trade_date) - pd.Timedelta(days=1)
    return [d.date().isoformat() for d in mcal.get_calendar("XKRX" if market == "KR" else "NYSE").valid_days(
        start_date=end - pd.Timedelta(days=180), end_date=end)]


def collect(tickers, trade_date):
    import pandas as pd
    import yfinance as yf

    end = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    start = (pd.Timestamp(end) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    with contextlib.redirect_stdout(sys.stderr):
        data = yf.download(tickers, start=start, end=end, interval="1d", auto_adjust=True,
                           actions=False, progress=False, threads=False, timeout=8, group_by="ticker")
    result = {"__expected_completed_date": previous_session(trade_date),
              "__market_days": market_days(trade_date), "__benchmarks": {t: "SPY" for t in tickers}}
    for ticker in tickers:
        try:
            frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
            if not isinstance(data.columns, pd.MultiIndex) and len(tickers) != 1:
                continue
            if frame.columns.duplicated().any():
                continue
            result[ticker] = [{"date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                               **{key.lower(): float(row[key]) for key in ("Open", "High", "Low", "Close", "Volume")}}
                              for day, row in frame.iterrows()
                              if not row[["Open", "High", "Low", "Close", "Volume"]].isna().any()]
        except (KeyError, TypeError, ValueError):
            continue
    return result


def _rows(frame, end):
    import math

    import pandas as pd
    columns = ("Open", "High", "Low", "Close", "Volume")
    if frame.columns.duplicated().any() or frame.index.duplicated().any():
        raise ValueError("ambiguous_history")
    rows = []
    for day, row in frame.sort_index().iterrows():
        day = pd.Timestamp(day).date().isoformat()
        if day > end:
            continue
        values = {key.lower(): float(row[key]) for key in columns}
        if not all(math.isfinite(v) for v in values.values()):
            raise ValueError("nonfinite_history")
        if (min(values[k] for k in ("open", "high", "low", "close")) <= 0
                or values["volume"] < 0 or values["low"] > min(values["open"], values["close"])
                or values["high"] < max(values["open"], values["close"])):
            raise ValueError("invalid_ohlcv")
        rows.append({"date": day, **values})
    if len(rows) < 66 or rows[-1]["date"] != end:
        raise ValueError("missing_completed_history")
    return rows


def collect_kr_bounded(tickers, trade_date, *, budget=20, source=None, master=None, cache_dir=None):
    """Bound caller wait, never kill a KIS authentication/cache writer.

    One non-daemon reader at a time; completed-session cache resumes unfinished
    symbols next batch. Active watchers precede outcome-only symbols. No new read
    is started after the deadline; an in-flight provider call may finish safely.
    """
    import pandas as pd

    from observability.oneil_watchlist import _atomic

    expected = previous_session(trade_date, "KR")
    tickers = list(dict.fromkeys(str(t) for t in tickers
                                if len(str(t)) == 6 and str(t).isascii() and str(t).isdigit()))[:100]
    directory = Path(cache_dir) if cache_dir else KR_CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    cache = directory / (expected + ".json")
    attempted = []

    def read_cache():
        try:
            if cache.stat().st_size > 10_000_000:
                raise ValueError("oversized_cache")
            data = json.loads(cache.read_text())
            if data.get("__expected_completed_date") != expected:
                raise ValueError("cache_date")
            return data
        except (OSError, ValueError):
            return {"__expected_completed_date": expected, "__benchmarks": {},
                    "__market_days": market_days(trade_date, "KR")}

    def work():
        global _KR_SOURCE
        if not _KR_LOCK.acquire(blocking=False):
            return
        try:
            from datetime import datetime, timezone

            from cores.kis_market_snapshot import fetch_kis_master_data
            from cores.market_data.kis_source import KisSource
            deadline = time.monotonic() + budget
            mapping = master if master is not None else fetch_kis_master_data(timeout=min(5, budget))
            if mapping.observed_date != str(trade_date).replace("-", ""):
                return
            if source is None and _KR_SOURCE is None:
                class DeadlineKisSource(KisSource):
                    deadline = 0

                    def _fetch(self, api_url, tr_id, params):
                        if time.monotonic() >= self.deadline:
                            raise TimeoutError("shadow_collection_budget")
                        return super()._fetch(api_url, tr_id, params)

                _KR_SOURCE = DeadlineKisSource()
            provider = source if source is not None else _KR_SOURCE
            if source is None:
                provider.deadline = deadline
            data = read_cache()
            start = (pd.Timestamp(expected) - pd.Timedelta(days=180)).strftime("%Y%m%d")
            end = expected.replace("-", "")
            for ticker in tickers:
                benchmark = {"KOSPI": "1001", "KOSDAQ": "2001"}.get(mapping.markets.get(ticker))
                if benchmark is None:
                    continue
                data["__benchmarks"][ticker] = benchmark
                if ticker in data:
                    attempted.append(ticker)
                for symbol in (benchmark, ticker):
                    if symbol in data:
                        continue
                    if time.monotonic() >= deadline:
                        return
                    try:
                        if symbol == ticker:
                            attempted.append(ticker)
                        frame = (provider.index_history(symbol, start, end) if symbol == benchmark
                                 else provider.price_history(symbol, start, end, adjusted=True))
                        data[symbol] = _rows(frame, expected)
                        data.setdefault("__captured_at", {})[symbol] = datetime.now(timezone.utc).isoformat()
                        _atomic(cache, data)
                    except Exception:  # noqa: BLE001, S112 - leave explicit missing input
                        continue
        except Exception:  # noqa: BLE001 - omit raw provider/auth exception from optional worker
            return
        finally:
            _KR_LOCK.release()

    worker = threading.Thread(target=work, daemon=False, name="oneil-kis-shadow-read")
    worker.start()
    worker.join(timeout=budget)
    data = read_cache()
    data["__attempted_symbols"] = list(dict.fromkeys(attempted + [t for t in tickers if t in data]))
    keep = set(tickers) | {"1001", "2001"}
    return {key: value for key, value in data.items() if key.startswith("__") or key in keep}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    print(json.dumps(collect(request["tickers"][:101], request["trade_date"]), allow_nan=False))
