"""
data_enricher.py — Post-analysis market data collection.

KRDataEnricher : KIS API (FHKST03010100 daily chart) for KR stocks + KOSPI index
USDataEnricher : yfinance for US stocks + S&P500 index

Called at ingest time. Results stored in report_enrichment + market_timeline.
"""

import asyncio
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Market phase classifier
# ---------------------------------------------------------------------------

def _calc_market_phase(current: float, ma20: float) -> str:
    """
    Classify market phase based on current index vs 20-day moving average.

      >  +5%  -> bull
      <  -5%  -> bear
      -5% to -2% -> correction
      +2% to +5% -> recovery
      else      -> sideways
    """
    if ma20 <= 0:
        return "sideways"
    deviation = (current - ma20) / ma20
    if deviation > 0.05:
        return "bull"
    if deviation < -0.05:
        return "bear"
    if deviation < -0.02:
        return "correction"
    if deviation > 0.02:
        return "recovery"
    return "sideways"


# ---------------------------------------------------------------------------
# Return calculator
# ---------------------------------------------------------------------------

def _calc_returns(
    post_prices: Dict[str, float],
    base_date: str,
    base_price: float,
) -> Dict[str, Optional[float]]:
    """
    Calculate n-day forward returns from base_date using price dict keyed by YYYY-MM-DD.
    """
    intervals = [7, 14, 30, 60, 90]
    results: Dict[str, Optional[float]] = {f"return_{n}d": None for n in intervals}
    if base_price <= 0:
        return results

    base_dt = datetime.strptime(base_date, "%Y-%m-%d")

    for n in intervals:
        target_dt = base_dt + timedelta(days=n)
        price = None
        # Search backward up to 5 trading days (weekends/holidays)
        for offset in range(5):
            candidate = (target_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
            if candidate in post_prices and candidate > base_date:
                price = post_prices[candidate]
                break
        # If still not found, try forward
        if price is None:
            for offset in range(1, 5):
                candidate = (target_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                if candidate in post_prices:
                    price = post_prices[candidate]
                    break
        if price is not None:
            results[f"return_{n}d"] = round((price - base_price) / base_price, 6)

    return results


# ---------------------------------------------------------------------------
# Stop-loss / target tracker
# ---------------------------------------------------------------------------

def _track_stop_loss(
    post_prices: Dict[str, float],
    low_prices: Dict[str, float],
    base_date: str,
    stop_loss: float,
    target_1: float,
) -> Dict:
    """
    Walk daily prices after base_date to check stop-loss / target-1 hits.
    """
    result = {
        "stop_loss_triggered": False,
        "stop_loss_date": None,
        "post_stop_30d": None,
        "post_stop_60d": None,
        "stop_was_correct": None,
        "target_1_hit": False,
        "days_to_target_1": None,
    }

    sorted_dates = sorted(d for d in low_prices if d > base_date)
    base_dt = datetime.strptime(base_date, "%Y-%m-%d")

    for date_str in sorted_dates:
        low = low_prices[date_str]
        if not result["stop_loss_triggered"] and stop_loss > 0 and low <= stop_loss:
            result["stop_loss_triggered"] = True
            result["stop_loss_date"] = date_str
            stop_dt = datetime.strptime(date_str, "%Y-%m-%d")
            for n in [30, 60]:
                target_dt = stop_dt + timedelta(days=n)
                for offset in range(5):
                    c = (target_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                    if c in post_prices and c > date_str:
                        pct = round((post_prices[c] - stop_loss) / stop_loss, 6)
                        result[f"post_stop_{n}d"] = pct
                        break

        if not result["target_1_hit"] and target_1 > 0:
            close = post_prices.get(date_str)
            if close and close >= target_1:
                result["target_1_hit"] = True
                hit_dt = datetime.strptime(date_str, "%Y-%m-%d")
                result["days_to_target_1"] = (hit_dt - base_dt).days

    if result["stop_loss_triggered"] and result["post_stop_30d"] is not None:
        result["stop_was_correct"] = result["post_stop_30d"] < 0

    return result


# ---------------------------------------------------------------------------
# KR Enricher
# ---------------------------------------------------------------------------

@dataclass
class KREnrichmentResult:
    ticker: str
    analysis_date: str
    stop_loss_price: Optional[float] = None
    target_1_price: Optional[float] = None
    price_at_analysis: Optional[float] = None
    index_at_analysis: Optional[float] = None
    index_change_20d: Optional[float] = None
    market_phase: Optional[str] = None
    return_7d: Optional[float] = None
    return_14d: Optional[float] = None
    return_30d: Optional[float] = None
    return_60d: Optional[float] = None
    return_90d: Optional[float] = None
    stop_loss_triggered: bool = False
    stop_loss_date: Optional[str] = None
    post_stop_30d: Optional[float] = None
    post_stop_60d: Optional[float] = None
    stop_was_correct: Optional[bool] = None
    target_1_hit: bool = False
    days_to_target_1: Optional[int] = None
    data_source: str = "kis_api"
    market: str = "kr"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ticker"] = self.ticker
        d["market"] = self.market
        return d


class KRDataEnricher:
    """
    Fetches daily price history from KIS API for KR stocks and KOSPI index.
    Uses DomesticStockTrading._request wrapped with run_in_executor for async safety.
    """

    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self) -> None:
        if KRDataEnricher._semaphore is None:
            KRDataEnricher._semaphore = asyncio.Semaphore(5)
        self._trading = None

    def _get_trading(self):
        """Lazy-init DomesticStockTrading in demo mode."""
        if self._trading is None:
            try:
                from trading.domestic_stock_trading import DomesticStockTrading  # type: ignore[import]
                self._trading = DomesticStockTrading(mode="demo")
            except Exception as e:
                logger.warning(f"KIS trading init failed (enrichment unavailable): {e}")
        return self._trading

    def _sync_fetch_daily(self, ticker: str, start_date: str, end_date: str) -> Dict[str, Dict]:
        """
        Sync call to KIS daily chart API (FHKST03010100).
        Returns { 'YYYY-MM-DD': {'close': float, 'low': float, 'high': float} }
        """
        trading = self._get_trading()
        if not trading:
            return {}
        try:
            api_url = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
            tr_id = "FHKST03010100"
            params = {
                "fid_cond_mrkt_div_code": "J",
                "fid_input_iscd": ticker,
                "fid_input_date_1": start_date.replace("-", ""),
                "fid_input_date_2": end_date.replace("-", ""),
                "fid_period_div_code": "D",
                "fid_org_adj_prc": "0",
            }
            res = trading._request(api_url, tr_id, params)
            if not res.isOK():
                logger.warning(f"[KR] KIS API error for {ticker}: {res.getErrorCode()} - {res.getErrorMessage()}")
                return {}
            items = res.getBody().output2 or []
            result = {}
            for item in items:
                date_raw = item.get("stck_bsop_date", "")
                if len(date_raw) == 8:
                    date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
                    try:
                        result[date_str] = {
                            "close": float(item.get("stck_clpr", 0) or 0),
                            "low": float(item.get("stck_lwpr", 0) or 0),
                            "high": float(item.get("stck_hgpr", 0) or 0),
                        }
                    except (ValueError, TypeError):
                        pass
            return result
        except Exception as e:
            logger.error(f"[KR] KIS API fetch failed for {ticker}: {e}")
            return {}

    def _sync_fetch_kospi(self, start_date: str, end_date: str) -> Dict[str, float]:
        """Fetch KOSPI index daily close. Returns { 'YYYY-MM-DD': close }"""
        trading = self._get_trading()
        if not trading:
            return {}
        try:
            api_url = "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice"
            tr_id = "FHKST03010100"
            params = {
                "fid_cond_mrkt_div_code": "U",
                "fid_input_iscd": "0001",
                "fid_input_date_1": start_date.replace("-", ""),
                "fid_input_date_2": end_date.replace("-", ""),
                "fid_period_div_code": "D",
                "fid_org_adj_prc": "0",
            }
            res = trading._request(api_url, tr_id, params)
            if not res.isOK():
                return {}
            items = res.getBody().output2 or []
            result = {}
            for item in items:
                date_raw = item.get("stck_bsop_date", "")
                if len(date_raw) == 8:
                    date_str = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
                    try:
                        result[date_str] = float(item.get("stck_clpr", 0) or 0)
                    except (ValueError, TypeError):
                        pass
            return result
        except Exception as e:
            logger.error(f"[KR] KOSPI fetch failed: {e}")
            return {}

    async def enrich(
        self,
        ticker: str,
        analysis_date: str,
        stop_loss: Optional[float] = None,
        target_1: Optional[float] = None,
    ) -> KREnrichmentResult:
        """Fetch 100 days of data from analysis_date and compute enrichment."""
        result = KREnrichmentResult(
            ticker=ticker, analysis_date=analysis_date,
            stop_loss_price=stop_loss, target_1_price=target_1,
        )

        end_date_dt = datetime.strptime(analysis_date, "%Y-%m-%d") + timedelta(days=100)
        end_date = end_date_dt.strftime("%Y-%m-%d")
        ma_start_dt = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=120)
        ma_start = ma_start_dt.strftime("%Y-%m-%d")

        sem = KRDataEnricher._semaphore
        assert sem is not None
        async with sem:
            loop = asyncio.get_running_loop()
            stock_prices, kospi_prices = await asyncio.gather(
                loop.run_in_executor(None, self._sync_fetch_daily, ticker, ma_start, end_date),
                loop.run_in_executor(None, self._sync_fetch_kospi, ma_start, end_date),
            )

        if not stock_prices:
            logger.warning(f"[KR] No price data for {ticker} {analysis_date}")
            result.data_source = "kis_api_unavailable"
            return result

        # Price on analysis date — exact first, then backward (no look-ahead), forward only as last resort
        base_price = None
        base_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        if analysis_date in stock_prices:
            base_price = stock_prices[analysis_date]["close"]
        else:
            for offset in range(1, 5):
                candidate = (base_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                if candidate in stock_prices:
                    base_price = stock_prices[candidate]["close"]
                    break
            if base_price is None:
                for offset in range(1, 5):
                    candidate = (base_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                    if candidate in stock_prices:
                        base_price = stock_prices[candidate]["close"]
                        break

        result.price_at_analysis = base_price

        # KOSPI on analysis date — exact first, then backward, forward only as last resort
        kospi_on_date = None
        if analysis_date in kospi_prices:
            kospi_on_date = kospi_prices[analysis_date]
        else:
            for offset in range(1, 5):
                candidate = (base_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                if candidate in kospi_prices:
                    kospi_on_date = kospi_prices[candidate]
                    break
            if kospi_on_date is None:
                for offset in range(1, 5):
                    candidate = (base_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                    if candidate in kospi_prices:
                        kospi_on_date = kospi_prices[candidate]
                        break

        result.index_at_analysis = kospi_on_date

        kospi_before = sorted(d for d in kospi_prices if d <= analysis_date)
        if len(kospi_before) >= 20:
            ma20 = sum(kospi_prices[d] for d in kospi_before[-20:]) / 20
            if kospi_on_date and ma20 > 0:
                result.index_change_20d = round((kospi_on_date - ma20) / ma20, 6)
                result.market_phase = _calc_market_phase(kospi_on_date, ma20)

        post_prices = {d: stock_prices[d]["close"] for d in stock_prices if d >= analysis_date}
        if base_price:
            fwd_returns = _calc_returns(post_prices, analysis_date, base_price)
            for k, v in fwd_returns.items():
                setattr(result, k, v)

        # Stop-loss / target tracking (post-analysis dates only)
        low_prices = {d: stock_prices[d]["low"] for d in stock_prices if d >= analysis_date}
        if stop_loss and base_price:
            sl_result = _track_stop_loss(post_prices, low_prices, analysis_date, stop_loss, target_1 or 0)
            result.stop_loss_triggered = sl_result["stop_loss_triggered"]
            result.stop_loss_date = sl_result["stop_loss_date"]
            result.post_stop_30d = sl_result["post_stop_30d"]
            result.post_stop_60d = sl_result["post_stop_60d"]
            result.stop_was_correct = sl_result["stop_was_correct"]
            result.target_1_hit = sl_result["target_1_hit"]
            result.days_to_target_1 = sl_result["days_to_target_1"]

        return result


# ---------------------------------------------------------------------------
# US Enricher
# ---------------------------------------------------------------------------

@dataclass
class USEnrichmentResult:
    ticker: str
    analysis_date: str
    stop_loss_price: Optional[float] = None
    target_1_price: Optional[float] = None
    price_at_analysis: Optional[float] = None
    index_at_analysis: Optional[float] = None
    index_change_20d: Optional[float] = None
    market_phase: Optional[str] = None
    return_7d: Optional[float] = None
    return_14d: Optional[float] = None
    return_30d: Optional[float] = None
    return_60d: Optional[float] = None
    return_90d: Optional[float] = None
    stop_loss_triggered: bool = False
    stop_loss_date: Optional[str] = None
    post_stop_30d: Optional[float] = None
    post_stop_60d: Optional[float] = None
    stop_was_correct: Optional[bool] = None
    target_1_hit: bool = False
    days_to_target_1: Optional[int] = None
    data_source: str = "yfinance"
    market: str = "us"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["ticker"] = self.ticker
        d["market"] = self.market
        return d


class USDataEnricher:
    """
    Fetches daily price history via yfinance for US stocks + S&P500 index.
    """

    _semaphore: Optional[asyncio.Semaphore] = None

    def __init__(self) -> None:
        if USDataEnricher._semaphore is None:
            USDataEnricher._semaphore = asyncio.Semaphore(10)

    def _sync_fetch_stock(self, ticker: str, start: str, end: str) -> Dict[str, Dict]:
        """Fetch daily OHLCV via yfinance. Returns {date: {close, low, high}}"""
        try:
            import yfinance as yf
            hist = yf.Ticker(ticker).history(start=start, end=end, auto_adjust=True)
            if hist.empty:
                return {}
            result = {}
            for idx, row in hist.iterrows():
                date_str = str(idx)[:10]
                result[date_str] = {
                    "close": float(row["Close"]),
                    "low": float(row["Low"]),
                    "high": float(row["High"]),
                }
            return result
        except Exception as e:
            logger.error(f"[US] yfinance fetch failed for {ticker}: {e}")
            return {}

    def _sync_fetch_sp500(self, start: str, end: str) -> Dict[str, float]:
        """Fetch S&P500 (^GSPC) daily close. Returns {date: close}"""
        try:
            import yfinance as yf
            hist = yf.Ticker("^GSPC").history(start=start, end=end, auto_adjust=True)
            if hist.empty:
                return {}
            result = {}
            for idx, row in hist.iterrows():
                date_str = str(idx)[:10]
                result[date_str] = float(row["Close"])
            return result
        except Exception as e:
            logger.error(f"[US] S&P500 fetch failed: {e}")
            return {}

    async def enrich(
        self,
        ticker: str,
        analysis_date: str,
        stop_loss: Optional[float] = None,
        target_1: Optional[float] = None,
    ) -> USEnrichmentResult:
        """Fetch 100 days from analysis_date and compute enrichment."""
        result = USEnrichmentResult(ticker=ticker, analysis_date=analysis_date,
                                     stop_loss_price=stop_loss, target_1_price=target_1)

        end_date_dt = datetime.strptime(analysis_date, "%Y-%m-%d") + timedelta(days=100)
        end_date = end_date_dt.strftime("%Y-%m-%d")
        ma_start_dt = datetime.strptime(analysis_date, "%Y-%m-%d") - timedelta(days=120)
        ma_start = ma_start_dt.strftime("%Y-%m-%d")

        us_sem = USDataEnricher._semaphore
        assert us_sem is not None
        async with us_sem:
            loop = asyncio.get_running_loop()
            stock_prices, sp500_prices = await asyncio.gather(
                loop.run_in_executor(None, self._sync_fetch_stock, ticker, ma_start, end_date),
                loop.run_in_executor(None, self._sync_fetch_sp500, ma_start, end_date),
            )

        if not stock_prices:
            logger.warning(f"[US] No price data for {ticker} {analysis_date}")
            result.data_source = "yfinance_unavailable"
            return result

        # Price on analysis date — exact first, then backward (no look-ahead), forward only as last resort
        base_price = None
        base_dt = datetime.strptime(analysis_date, "%Y-%m-%d")
        if analysis_date in stock_prices:
            base_price = stock_prices[analysis_date]["close"]
        else:
            for offset in range(1, 5):
                candidate = (base_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                if candidate in stock_prices:
                    base_price = stock_prices[candidate]["close"]
                    break
            if base_price is None:
                for offset in range(1, 5):
                    candidate = (base_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                    if candidate in stock_prices:
                        base_price = stock_prices[candidate]["close"]
                        break

        result.price_at_analysis = base_price

        # S&P500 on analysis date — exact first, then backward, forward only as last resort
        sp500_on_date = None
        if analysis_date in sp500_prices:
            sp500_on_date = sp500_prices[analysis_date]
        else:
            for offset in range(1, 5):
                candidate = (base_dt - timedelta(days=offset)).strftime("%Y-%m-%d")
                if candidate in sp500_prices:
                    sp500_on_date = sp500_prices[candidate]
                    break
            if sp500_on_date is None:
                for offset in range(1, 5):
                    candidate = (base_dt + timedelta(days=offset)).strftime("%Y-%m-%d")
                    if candidate in sp500_prices:
                        sp500_on_date = sp500_prices[candidate]
                        break

        result.index_at_analysis = sp500_on_date

        sp500_before = sorted(d for d in sp500_prices if d <= analysis_date)
        if len(sp500_before) >= 20:
            ma20 = sum(sp500_prices[d] for d in sp500_before[-20:]) / 20
            if sp500_on_date and ma20 > 0:
                result.index_change_20d = round((sp500_on_date - ma20) / ma20, 6)
                result.market_phase = _calc_market_phase(sp500_on_date, ma20)

        post_prices = {d: stock_prices[d]["close"] for d in stock_prices if d >= analysis_date}
        if base_price:
            fwd_returns = _calc_returns(post_prices, analysis_date, base_price)
            for k, v in fwd_returns.items():
                setattr(result, k, v)

        # Stop-loss / target tracking (post-analysis dates only)
        low_prices = {d: stock_prices[d]["low"] for d in stock_prices if d >= analysis_date}
        if stop_loss and base_price:
            sl_result = _track_stop_loss(post_prices, low_prices, analysis_date, stop_loss, target_1 or 0)
            result.stop_loss_triggered = sl_result["stop_loss_triggered"]
            result.stop_loss_date = sl_result["stop_loss_date"]
            result.post_stop_30d = sl_result["post_stop_30d"]
            result.post_stop_60d = sl_result["post_stop_60d"]
            result.stop_was_correct = sl_result["stop_was_correct"]
            result.target_1_hit = sl_result["target_1_hit"]
            result.days_to_target_1 = sl_result["days_to_target_1"]

        return result


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_enricher(market: str):
    """Return the appropriate enricher for the given market."""
    if market == "kr":
        return KRDataEnricher()
    elif market == "us":
        return USDataEnricher()
    raise ValueError(f"Unknown market: {market!r} (must be 'kr' or 'us')")
