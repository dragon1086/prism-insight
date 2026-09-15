"""Single bounded bulk data read in disposable subprocess; stdout is JSON only."""
import contextlib
import json
import sys


def previous_session(trade_date):
    import pandas as pd
    import pandas_market_calendars as mcal

    end = pd.Timestamp(trade_date) - pd.Timedelta(days=1)
    valid = mcal.get_calendar("NYSE").valid_days(start_date=end - pd.Timedelta(days=10), end_date=end)
    if len(valid) == 0:
        raise ValueError("missing_NYSE_calendar")
    return valid[-1].date().isoformat()


def collect(tickers, trade_date):
    import pandas as pd
    import yfinance as yf

    end = pd.Timestamp(trade_date).strftime("%Y-%m-%d")
    start = (pd.Timestamp(end) - pd.Timedelta(days=180)).strftime("%Y-%m-%d")
    with contextlib.redirect_stdout(sys.stderr):
        data = yf.download(tickers, start=start, end=end, interval="1d", auto_adjust=True,
                           actions=False, progress=False, threads=False, timeout=8, group_by="ticker")
    result = {"__expected_completed_date": previous_session(trade_date)}
    for ticker in tickers:
        try:
            frame = data[ticker] if isinstance(data.columns, pd.MultiIndex) else data
            if not isinstance(data.columns, pd.MultiIndex) and len(tickers) != 1:
                continue
            if frame.columns.duplicated().any():
                continue
            result[ticker] = [{"date": pd.Timestamp(day).strftime("%Y-%m-%d"),
                               "close": float(row["Close"]), "high": float(row["High"])}
                              for day, row in frame.iterrows() if not row[["Close", "High"]].isna().any()]
        except (KeyError, TypeError, ValueError):
            continue
    return result


if __name__ == "__main__":
    request = json.load(sys.stdin)
    print(json.dumps(collect(request["tickers"][:21], request["trade_date"]), allow_nan=False))
