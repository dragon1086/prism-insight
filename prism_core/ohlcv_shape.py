"""Normalize single-symbol provider frames without guessing a ticker or field."""
import pandas as pd

_PRICE_FIELDS = {"Open", "High", "Low", "Close", "Adj Close", "Volume"}


def normalize_single_ticker_ohlcv(frame: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Return flat, unique fields or an empty frame when identity is ambiguous.

    Preserve values, row order and adjustment semantics. Only column shape is
    normalized; this does not establish bar finality or validate trading prices.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    if not isinstance(frame.columns, pd.MultiIndex):
        return frame.copy() if frame.columns.is_unique else pd.DataFrame()
    if frame.columns.nlevels != 2:
        return pd.DataFrame()
    requested = str(ticker).strip().upper()
    candidates = []
    for field_level in (0, 1):
        if not _PRICE_FIELDS.intersection(frame.columns.get_level_values(field_level)):
            continue
        symbol_level = 1 - field_level
        symbols = frame.columns.get_level_values(symbol_level).unique()
        matches = [symbol for symbol in symbols if str(symbol).strip().upper() == requested]
        if len(matches) != 1:
            continue
        selected = frame.xs(matches[0], axis=1, level=symbol_level, drop_level=True)
        if selected.columns.is_unique and not isinstance(selected.columns, pd.MultiIndex):
            candidates.append(selected)
    if len(candidates) != 1:
        return pd.DataFrame()
    result = candidates[0].copy()
    result.columns.name = None
    return result
