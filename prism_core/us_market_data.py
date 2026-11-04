"""KIS overseas quotations as the US price provider (replaces yfinance prices).

yfinance scrapes Yahoo without a licence, so it cannot back a commercial
service. KIS is the contracted provider we already trade through. US runtime
code reads prices from it here:

- ``daily_history``: daily OHLCV, same shape and basis as yfinance
  ``history(auto_adjust=False)``: split-adjusted, not dividend-adjusted
  (2026-10-23: XOM 2026-08-10 close 159.79 on both), today's bar included while
  the session is open.
  Stocks/ETFs use ``dailyprice`` (HHDFS76240000, needs the exchange, serves
  class shares as ``BRK/B``). Indices use ``inquire-daily-chartprice``
  (FHKST03030100: SPX, COMP, VIX, NDX, .DJI).
- ``current_quote``: the last trade (HHDFS00000300) through the trading client.

Missing stays missing: a blank field is NaN, an index volume is NaN when KIS
sends ``0`` (it publishes none for SPX/VIX, and none for any index while the
session is open), and a failed request raises ``UsMarketDataUnavailable``
instead of returning an empty or zero frame.

The trading client is borrowed, not re-authenticated, for the same reason as
``cores/market_data/kis_source.py``: one token per app key, and ``kis_auth``
keeps global state. Nothing here places orders.

Providers are adapters behind the Korean market-data contract
(``cores/market_data/source.py``: ``SourceChain``, ``Unsupported``, ``Unavailable``).
``PRISM_US_MARKET_DATA_SOURCES`` picks them in order (default ``kis``; e.g.
``kis,yfinance``). A new vendor is one class with ``daily_history`` and
``current_quote`` plus one entry in ``_SOURCES``; callers do not change.
"""

from __future__ import annotations

import logging
import math
import os
import re
import sys
import threading
import time
from datetime import date, datetime

import pandas as pd

logger = logging.getLogger(__name__)

STOCK_URL = "/uapi/overseas-price/v1/quotations/dailyprice"
STOCK_TR_ID = "HHDFS76240000"
INDEX_URL = "/uapi/overseas-price/v1/quotations/inquire-daily-chartprice"
INDEX_TR_ID = "FHKST03030100"
_STOCK_FIELDS = ("xymd", {"open": "Open", "high": "High", "low": "Low", "clos": "Close", "tvol": "Volume"})
_INDEX_FIELDS = ("stck_bsop_date", {"ovrs_nmix_oprc": "Open", "ovrs_nmix_hgpr": "High",
                                    "ovrs_nmix_lwpr": "Low", "ovrs_nmix_prpr": "Close", "acml_vol": "Volume"})
# yfinance index symbols -> (KIS market division, code), verified 2026-10-23. N = overseas index,
# I = government bond yield. KIS has no Russell 2000 (^RUT): it raises unavailable, never a substitute.
INDEX_CODES = {"^GSPC": ("N", "SPX"), "^IXIC": ("N", "COMP"), "^VIX": ("N", "VIX"), "^NDX": ("N", "NDX"),
               "^DJI": ("N", ".DJI"), "^TNX": ("I", "Y0202")}
_NEW_YORK = "America/New_York"
_INDEX_TZ = {"^VIX": "America/Chicago"}  # yfinance stamps CBOE VIX bars in Chicago time
_PRICE_EXCD = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}
_MAX_WINDOWS = 40
_RATE_LIMIT_CODE = "EGW00201"
_KIS_EXPIRED_AUTH_CODE = "EGW00123"
_RATE_LIMIT_WAITS = (0.5, 1.0)

_client = None
_client_lock = threading.Lock()
_chain = None
_DEFAULT_ORDER = "kis"


def _load_source_contract():
    """The Korean provider contract, without importing ``cores`` (prism-us/cores shadows it in US runs)."""
    loaded = sys.modules.get("cores.market_data.source")
    if loaded is not None:
        return loaded
    module = sys.modules.get("prism_market_data_source")
    if module is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "cores", "market_data", "source.py")
        spec = importlib.util.spec_from_file_location("prism_market_data_source", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["prism_market_data_source"] = module
    return module


_contract = _load_source_contract()
SourceChain, Unavailable, Unsupported = _contract.SourceChain, _contract.Unavailable, _contract.Unsupported


class UsMarketDataUnavailable(Unavailable, RuntimeError):
    """No configured US provider could answer; the caller treats the value as unknown."""


def _us_trading_class():
    try:
        from trading.us_stock_trading import USStockTrading
        return USStockTrading
    except ModuleNotFoundError as exc:
        if exc.name != "trading.us_stock_trading":
            raise
    # The root ``trading`` package has no US module. Load prism-us/trading/us_stock_trading.py by
    # path (as examples/generate_us_dashboard_json.py does) so prism-us/trading never shadows it.
    module = sys.modules.get("prism_us_stock_trading")
    if module is None:
        import importlib.util
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "prism-us", "trading", "us_stock_trading.py")
        spec = importlib.util.spec_from_file_location("prism_us_stock_trading", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sys.modules["prism_us_stock_trading"] = module
    return module.USStockTrading


def _trading():
    """The shared quotation client for the primary US account (no orders)."""
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:
                try:
                    _client = _us_trading_class()(auto_trading=False)
                except Exception as exc:  # missing config, auth failure, import
                    raise UsMarketDataUnavailable(f"KIS US client unavailable: {type(exc).__name__}") from exc
    return _client


def _fetch(url: str, tr_id: str, params: dict):
    global _client
    try:
        return _fetch_once(url, tr_id, params)
    except UsMarketDataUnavailable as exc:
        if _KIS_EXPIRED_AUTH_CODE not in str(exc):
            raise
    # The shared client keeps the token it was built with; rebuild it once so ka.auth() gets a live token.
    with _client_lock:
        _client = None
    return _fetch_once(url, tr_id, params)


def _fetch_once(url: str, tr_id: str, params: dict):
    for wait in (*_RATE_LIMIT_WAITS, None):
        try:
            response = _trading()._request(url, tr_id, params)
        except UsMarketDataUnavailable:
            raise
        except Exception as exc:  # transport, auth
            raise UsMarketDataUnavailable(f"KIS {tr_id} failed: {type(exc).__name__}") from exc
        if response is not None and response.isOK():
            return response.getBody()
        try:
            detail = str(response.getErrorMessage() or "") if response is not None else "no response"
        except Exception:  # noqa: BLE001 - keep the original failure
            detail = "unreadable error"
        if wait is None or _RATE_LIMIT_CODE not in detail:
            raise UsMarketDataUnavailable(f"KIS {tr_id} rejected: {detail}")
        time.sleep(wait)


def _yyyymmdd(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y%m%d")
    return pd.Timestamp(str(value)).strftime("%Y%m%d")


def _walk(first: str, last: str, date_field: str, page) -> list[dict]:
    """Collect rows dated within [first, last]; each page ends at a cursor and runs backwards."""
    collected: dict[str, dict] = {}
    cursor = last
    for _ in range(_MAX_WINDOWS):
        fresh = {}
        for row in page(cursor):
            day = str(row.get(date_field) or "")
            if day and first <= day <= last and day not in collected:
                fresh.setdefault(day, row)  # a repeated date keeps its first row
        if not fresh:
            break
        collected.update(fresh)
        earliest = min(fresh)
        cursor = (pd.Timestamp(earliest) - pd.Timedelta(days=1)).strftime("%Y%m%d")
        if earliest <= first or cursor < first:
            break
    return [collected[key] for key in sorted(collected)]


def daily_history(symbol: str, start, end, *, adjusted: bool = False) -> pd.DataFrame:
    """Daily OHLCV for [start, end] (inclusive), oldest first, tz-naive ``Date`` index.

    ``symbol`` uses the yfinance spelling (``AAPL``, ``BRK-B``, ``^GSPC``).
    Raises ``UsMarketDataUnavailable`` when no configured provider can answer.
    """
    symbol = str(symbol or "").strip().upper()
    if not symbol:
        raise ValueError("symbol is required")
    return _ask("daily_history", symbol, start, end, adjusted=adjusted)


def _kis_daily_history(symbol: str, start, end, *, adjusted: bool = False) -> pd.DataFrame:
    """KIS adapter body of ``daily_history`` (symbol already normalised)."""
    first, last = _yyyymmdd(start), _yyyymmdd(end)
    index_code = INDEX_CODES.get(symbol)
    if symbol.startswith("^") and not index_code:
        raise UsMarketDataUnavailable(f"no KIS code for index {symbol}")
    if index_code:
        def page(cursor):
            body = _fetch(INDEX_URL, INDEX_TR_ID, {
                "FID_COND_MRKT_DIV_CODE": index_code[0], "FID_INPUT_ISCD": index_code[1], "FID_INPUT_DATE_1": first,
                "FID_INPUT_DATE_2": cursor, "FID_PERIOD_DIV_CODE": "D"})
            return getattr(body, "output2", None) or []
    else:
        exchange = _PRICE_EXCD.get(_trading()._resolve_exchange(symbol))
        if exchange is None:
            raise UsMarketDataUnavailable(f"no KIS exchange for {symbol}")
        kis_symbol = symbol.replace("-", "/")  # class shares: BRK-B -> BRK/B

        def frame(modp):
            def page(cursor):
                body = _fetch(STOCK_URL, STOCK_TR_ID, {
                    "AUTH": "", "EXCD": exchange, "SYMB": kis_symbol, "GUBN": "0", "BYMD": cursor, "MODP": modp})
                return getattr(body, "output2", None) or []
            return frame_from_rows(_walk(first, last, _STOCK_FIELDS[0], page), _STOCK_FIELDS, symbol=symbol)
        return yfinance_basis(frame("0"), frame("1"), adjusted=adjusted)
    return frame_from_rows(_walk(first, last, _INDEX_FIELDS[0], page), _INDEX_FIELDS, is_index=True, symbol=symbol)


# A close-to-close step in raw/adjusted price this large is a split, not a dividend.
# shortcut: a special dividend above ~17% of the price would read as a split; use KIS
# period rights (기간별권리조회) if that ever matters.
_SPLIT_STEP = 1.2


def yfinance_basis(raw: pd.DataFrame, adj: pd.DataFrame, *, adjusted: bool) -> pd.DataFrame:
    """KIS raw (MODP=0) + adjusted (MODP=1) bars -> the yfinance basis.

    Measured 2026-10-23 against yfinance 1.4.1: MODP=1 prices equal
    ``auto_adjust=True`` (split + dividend), MODP=0 is fully raw, and KIS never
    split-adjusts volume while yfinance always does. ``adjusted=False`` returns
    yfinance ``auto_adjust=False``: split-adjusted, not dividend-adjusted.
    """
    raw = raw.copy()
    ratio = (raw["Close"] / adj["Close"].reindex(raw.index)).ffill()
    step = ratio.shift(1) / ratio
    split_step = step.where((step >= _SPLIT_STEP) | (step <= 1 / _SPLIT_STEP), 1.0).fillna(1.0)
    # Factor for each bar = product of split steps on later dates.
    later = split_step[::-1].cumprod()[::-1].shift(-1, fill_value=1.0)
    base = adj.reindex(raw.index) if adjusted else raw.div(later, axis=0)
    out = base[["Open", "High", "Low", "Close"]].copy()
    out["Volume"] = raw["Volume"] * later
    out["Stock Splits"] = split_step.where(split_step != 1.0, 0.0)  # yfinance convention: 0 = no split
    return out


_PERIOD_UNITS = {"d": "days", "wk": "weeks", "mo": "months", "y": "years"}


def history(symbol: str, period: str = "1mo", start=None, end=None, *, auto_adjust: bool = True) -> pd.DataFrame:
    """Daily drop-in for yfinance ``Ticker(symbol).history(...)``.

    Same columns (Open/High/Low/Close/Volume, plus Stock Splits for stocks),
    same basis for ``auto_adjust``, America/New_York midnight index, ``end``
    exclusive, ``period`` counted back from today (``Nd`` = last N sessions).
    No Dividends column: KIS does not report them, and unknown is not zero.
    """
    today = pd.Timestamp.now(tz=_NEW_YORK).normalize().tz_localize(None)
    if start is not None:
        first = pd.Timestamp(start).tz_localize(None) if pd.Timestamp(start).tzinfo else pd.Timestamp(start)
        last = (pd.Timestamp(end).tz_localize(None) if pd.Timestamp(end).tzinfo else pd.Timestamp(end)) \
            - pd.Timedelta(days=1) if end is not None else today
        frame = daily_history(symbol, first, last, adjusted=auto_adjust)
    else:
        text = str(period or "").strip().lower()
        if text == "ytd":
            frame = daily_history(symbol, today.replace(month=1, day=1), today, adjusted=auto_adjust)
        else:
            match = re.fullmatch(r"(\d+)(d|wk|mo|y)", text)
            if not match:
                raise ValueError(f"unsupported period {period!r}")
            count, unit = int(match.group(1)), match.group(2)
            if unit == "d":  # sessions, not calendar days
                frame = daily_history(symbol, today - pd.Timedelta(days=2 * count + 10), today,
                                      adjusted=auto_adjust).tail(count)
            else:
                first = today - pd.DateOffset(**{_PERIOD_UNITS[unit]: count})
                frame = daily_history(symbol, first, today, adjusted=auto_adjust)
    out = frame.copy()
    out.index = out.index.tz_localize(_INDEX_TZ.get(str(symbol).strip().upper(), _NEW_YORK))
    return out


def frame_from_rows(rows: list[dict], fields=_STOCK_FIELDS, *, is_index: bool = False,
                    symbol: str = "") -> pd.DataFrame:
    """KIS daily rows -> OHLCV frame. Blank or non-positive prices are NaN, never zero."""
    date_field, columns = fields
    if not rows:
        raise UsMarketDataUnavailable(f"KIS returned no daily rows for {symbol}")
    frame = pd.DataFrame(rows)
    if date_field not in frame.columns or not set(columns) <= set(frame.columns):
        raise UsMarketDataUnavailable(f"KIS daily rows for {symbol} miss OHLCV fields")
    out = frame[list(columns)].rename(columns=columns).apply(
        lambda column: pd.to_numeric(column.astype(str).str.replace(",", "").str.strip(), errors="coerce"))
    out.index = pd.DatetimeIndex(pd.to_datetime(frame[date_field].astype(str), format="%Y%m%d"), name="Date")
    out = out[~out.index.duplicated(keep="first")].sort_index()
    prices = ["Open", "High", "Low", "Close"]
    out[prices] = out[prices].where(out[prices] > 0)
    if is_index:
        out["Volume"] = out["Volume"].where(out["Volume"] > 0)  # an index never trades zero shares
    return out


def current_quote(symbol: str, *, allow_previous_close: bool = False):
    """Last trade price; with ``allow_previous_close`` the previous close when there is no trade
    (yfinance ``regularMarketPrice or previousClose``). None when the provider has neither."""
    return _ask("current_quote", str(symbol).strip().upper(), allow_previous_close=allow_previous_close)


def _kis_current_quote(symbol: str, *, allow_previous_close: bool = False):
    quote = _trading().get_current_price(symbol, strict=not allow_previous_close)
    price = (quote or {}).get("current_price")
    return float(price) if isinstance(price, (int, float)) and math.isfinite(price) and price > 0 else None


def download(symbols, **history_kwargs) -> pd.DataFrame:
    """Several symbols as one frame shaped like yfinance ``download(..., group_by="ticker")``.

    Sequential (KIS's per-second quota is shared with the trading loops). A symbol
    KIS cannot price is left out, so ``frame[symbol]`` raises KeyError as before.
    """
    frames = {}
    for symbol in dict.fromkeys(symbols):
        try:
            frames[symbol] = history(symbol, **history_kwargs)
        except UsMarketDataUnavailable as error:
            logger.warning("KIS daily prices unavailable for %s: %s", symbol, error)
    return pd.concat(frames, axis=1) if frames else pd.DataFrame()


class KisUsSource:
    """KIS overseas quotations (the production provider)."""

    name = "kis"

    def daily_history(self, symbol, start, end, *, adjusted=False):
        return _kis_daily_history(symbol, start, end, adjusted=adjusted)

    def current_quote(self, symbol, *, allow_previous_close=False):
        return _kis_current_quote(symbol, allow_previous_close=allow_previous_close)


class YFinanceUsSource:
    """yfinance, limited to what US runtime code used before KIS (opt-in only, unlicensed)."""

    name = "yfinance"

    def daily_history(self, symbol, start, end, *, adjusted=False):
        import yfinance as yf
        stop = pd.Timestamp(_yyyymmdd(end)) + pd.Timedelta(days=1)
        frame = yf.Ticker(symbol).history(start=pd.Timestamp(_yyyymmdd(start)).strftime("%Y-%m-%d"),
                                          end=stop.strftime("%Y-%m-%d"), auto_adjust=adjusted)
        if frame is None or frame.empty:
            raise Unavailable(f"yfinance returned no daily rows for {symbol}")
        out = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
        if not symbol.startswith("^"):
            out["Stock Splits"] = frame["Stock Splits"] if "Stock Splits" in frame else 0.0
        out.index = pd.DatetimeIndex(frame.index.tz_localize(None).normalize(), name="Date")
        return out

    def current_quote(self, symbol, *, allow_previous_close=False):
        import yfinance as yf
        info = yf.Ticker(symbol).info or {}
        price = info.get("regularMarketPrice") or (info.get("previousClose") if allow_previous_close else None)
        return float(price) if isinstance(price, (int, float)) and math.isfinite(price) and price > 0 else None


_SOURCES = {"kis": KisUsSource, "yfinance": YFinanceUsSource}


def default_chain():
    """Providers from ``PRISM_US_MARKET_DATA_SOURCES`` (default ``kis``), built once per process."""
    global _chain
    if _chain is None:
        names = [n.strip().lower() for n in os.getenv("PRISM_US_MARKET_DATA_SOURCES", _DEFAULT_ORDER).split(",")]
        unknown = [n for n in names if n not in _SOURCES]
        if unknown or not any(names):
            raise ValueError(f"unknown US market data source(s): {unknown or names}")
        _chain = SourceChain([_SOURCES[n]() for n in names])
        logger.info("US market data sources: %s", " -> ".join(_chain.names))
    return _chain


def set_default_chain(chain) -> None:
    """Replace the process chain. For tests and deliberate reconfiguration."""
    global _chain
    _chain = chain


def _ask(capability, *args, **kwargs):
    try:
        return default_chain().fetch(capability, *args, **kwargs)
    except UsMarketDataUnavailable:
        raise
    except Unavailable as exc:
        raise UsMarketDataUnavailable(str(exc)) from exc
