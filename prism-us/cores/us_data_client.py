"""
US Stock Data Client

Unified interface for fetching US stock market data.
Primary: yfinance (free, comprehensive)
Supplementary: Finnhub (free tier, 60 calls/min)

Usage:
    from prism_us.cores.us_data_client import USDataClient

    client = USDataClient()

    # Get OHLCV data
    df = client.get_ohlcv("AAPL", period="1mo")

    # Get company info
    info = client.get_company_info("AAPL")

    # Get financials
    financials = client.get_financials("AAPL")

    # Get institutional holders
    holders = client.get_institutional_holders("AAPL")
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


class USDataClient:
    """
    Unified US stock data client.

    Provides access to:
    - OHLCV data (yfinance)
    - Company information (yfinance)
    - Financial statements (yfinance)
    - Institutional holders (yfinance - FREE!)
    - SEC filings (Finnhub)
    - News (Finnhub)
    """

    def __init__(self, finnhub_api_key: Optional[str] = None):
        """
        Initialize the US data client.

        Args:
            finnhub_api_key: Optional Finnhub API key (defaults to env var)
        """
        self.finnhub_api_key = finnhub_api_key or os.getenv("FINNHUB_API_KEY")
        self._finnhub_client = None

        if self.finnhub_api_key:
            try:
                import finnhub
                self._finnhub_client = finnhub.Client(api_key=self.finnhub_api_key)
                logger.info("Finnhub client initialized")
            except ImportError:
                logger.warning("finnhub-python not installed, Finnhub features disabled")
            except Exception as e:
                logger.warning(f"Failed to initialize Finnhub client: {e}")

    # =========================================================================
    # OHLCV Data (yfinance)
    # =========================================================================

    def get_ohlcv(
        self,
        ticker: str,
        period: str = "1mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Get OHLCV (Open, High, Low, Close, Volume) data.

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL")
            period: Data period (1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max)
            interval: Data interval (1m, 2m, 5m, 15m, 30m, 60m, 90m, 1h, 1d, 5d, 1wk, 1mo, 3mo)
            start: Start date (YYYY-MM-DD), overrides period
            end: End date (YYYY-MM-DD), overrides period

        Returns:
            DataFrame with OHLCV data
        """
        try:
            stock = yf.Ticker(ticker)

            if start and end:
                df = stock.history(start=start, end=end, interval=interval)
            else:
                df = stock.history(period=period, interval=interval)

            if df.empty:
                logger.warning(f"No OHLCV data found for {ticker}")
                return pd.DataFrame()

            # Standardize column names
            df.columns = [col.lower().replace(" ", "_") for col in df.columns]

            logger.info(f"Retrieved {len(df)} OHLCV records for {ticker}")
            return df

        except Exception as e:
            logger.error(f"Error fetching OHLCV for {ticker}: {e}")
            return pd.DataFrame()

    def get_stock_ohlcv(
        self,
        ticker: str,
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        Get OHLCV data for a specific date range.

        Args:
            ticker: Stock ticker symbol
            start_date: Start date (YYYY-MM-DD)
            end_date: End date (YYYY-MM-DD)

        Returns:
            DataFrame with OHLCV data
        """
        return self.get_ohlcv(ticker, start=start_date, end=end_date)

    # =========================================================================
    # Company Information (yfinance)
    # =========================================================================

    def get_company_info(self, ticker: str) -> Dict[str, Any]:
        """
        Get comprehensive company information.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with company info
        """
        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            if not info:
                logger.warning(f"No company info found for {ticker}")
                return {}

            # Extract key fields
            result = {
                "ticker": ticker,
                "name": info.get("longName") or info.get("shortName", ""),
                "sector": info.get("sector", ""),
                "industry": info.get("industry", ""),
                "website": info.get("website", ""),
                "description": info.get("longBusinessSummary", ""),
                "country": info.get("country", ""),
                "exchange": info.get("exchange", ""),
                "currency": info.get("currency", "USD"),

                # Market data
                "market_cap": info.get("marketCap", 0),
                "enterprise_value": info.get("enterpriseValue", 0),
                "price": info.get("currentPrice") or info.get("regularMarketPrice", 0),
                "previous_close": info.get("previousClose", 0),
                "open": info.get("open", 0),
                "day_high": info.get("dayHigh", 0),
                "day_low": info.get("dayLow", 0),
                "volume": info.get("volume", 0),
                "avg_volume": info.get("averageVolume", 0),
                "avg_volume_10d": info.get("averageVolume10days", 0),

                # 52-week data
                "fifty_two_week_high": info.get("fiftyTwoWeekHigh", 0),
                "fifty_two_week_low": info.get("fiftyTwoWeekLow", 0),
                "fifty_day_avg": info.get("fiftyDayAverage", 0),
                "two_hundred_day_avg": info.get("twoHundredDayAverage", 0),

                # Valuation
                "pe_ratio": info.get("trailingPE", 0),
                "forward_pe": info.get("forwardPE", 0),
                "peg_ratio": info.get("pegRatio", 0),
                "price_to_book": info.get("priceToBook", 0),
                "price_to_sales": info.get("priceToSalesTrailing12Months", 0),

                # Profitability
                "profit_margin": info.get("profitMargins", 0),
                "operating_margin": info.get("operatingMargins", 0),
                "return_on_assets": info.get("returnOnAssets", 0),
                "return_on_equity": info.get("returnOnEquity", 0),

                # Financials
                "revenue": info.get("totalRevenue", 0),
                "revenue_per_share": info.get("revenuePerShare", 0),
                "gross_profit": info.get("grossProfits", 0),
                "ebitda": info.get("ebitda", 0),
                "net_income": info.get("netIncomeToCommon", 0),
                "earnings_per_share": info.get("trailingEps", 0),

                # Dividend
                "dividend_rate": info.get("dividendRate", 0),
                "dividend_yield": info.get("dividendYield", 0),
                "payout_ratio": info.get("payoutRatio", 0),

                # Shares
                "shares_outstanding": info.get("sharesOutstanding", 0),
                "float_shares": info.get("floatShares", 0),
                "shares_short": info.get("sharesShort", 0),
                "short_ratio": info.get("shortRatio", 0),

                # Beta
                "beta": info.get("beta", 0),

                # Target price (analysts)
                "target_high": info.get("targetHighPrice", 0),
                "target_low": info.get("targetLowPrice", 0),
                "target_mean": info.get("targetMeanPrice", 0),
                "target_median": info.get("targetMedianPrice", 0),
                "recommendation": info.get("recommendationKey", ""),
                "num_analysts": info.get("numberOfAnalystOpinions", 0),
            }

            logger.info(f"Retrieved company info for {ticker}: {result.get('name')}")
            return result

        except Exception as e:
            logger.error(f"Error fetching company info for {ticker}: {e}")
            return {}

    # =========================================================================
    # Financial Statements (yfinance)
    # =========================================================================

    def get_financials(self, ticker: str) -> Dict[str, pd.DataFrame]:
        """
        Get financial statements.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with income statement, balance sheet, cash flow
        """
        try:
            stock = yf.Ticker(ticker)

            result = {
                "income_statement": stock.financials,
                "income_statement_quarterly": stock.quarterly_financials,
                "balance_sheet": stock.balance_sheet,
                "balance_sheet_quarterly": stock.quarterly_balance_sheet,
                "cash_flow": stock.cashflow,
                "cash_flow_quarterly": stock.quarterly_cashflow,
            }

            # Log summary
            for key, df in result.items():
                if df is not None and not df.empty:
                    logger.info(f"{ticker} {key}: {df.shape}")

            return result

        except Exception as e:
            logger.error(f"Error fetching financials for {ticker}: {e}")
            return {}

    # =========================================================================
    # Institutional Holders (yfinance - FREE!)
    # =========================================================================

    def get_institutional_holders(self, ticker: str) -> Dict[str, Any]:
        """
        Get institutional ownership data.

        Note: This is FREE via yfinance (Finnhub requires premium $49+/month)

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with institutional holders and major holders
        """
        try:
            stock = yf.Ticker(ticker)

            result = {
                "institutional_holders": stock.institutional_holders,
                "major_holders": stock.major_holders,
                "mutualfund_holders": stock.mutualfund_holders,
            }

            # Log summary
            if result["institutional_holders"] is not None:
                logger.info(f"{ticker} institutional holders: {len(result['institutional_holders'])} institutions")

            return result

        except Exception as e:
            logger.error(f"Error fetching institutional holders for {ticker}: {e}")
            return {}

    # =========================================================================
    # Market Index Data (yfinance)
    # =========================================================================

    def get_index_data(
        self,
        index: str = "^GSPC",
        period: str = "1mo"
    ) -> pd.DataFrame:
        """
        Get market index data.

        Args:
            index: Index symbol (default: S&P 500)
                - ^GSPC: S&P 500
                - ^DJI: Dow Jones Industrial Average
                - ^IXIC: NASDAQ Composite
                - ^RUT: Russell 2000
                - ^VIX: VIX Volatility Index
            period: Data period

        Returns:
            DataFrame with index OHLCV data
        """
        return self.get_ohlcv(index, period=period)

    def get_market_indices(self, period: str = "5d") -> Dict[str, pd.DataFrame]:
        """
        Get data for major US market indices.

        Args:
            period: Data period

        Returns:
            Dictionary with index data
        """
        indices = {
            "sp500": "^GSPC",
            "dow": "^DJI",
            "nasdaq": "^IXIC",
            "russell2000": "^RUT",
            "vix": "^VIX",
        }

        result = {}
        for name, symbol in indices.items():
            result[name] = self.get_ohlcv(symbol, period=period)

        return result

    # =========================================================================
    # Finnhub Data (Supplementary)
    # =========================================================================

    def get_company_profile_finnhub(self, ticker: str) -> Dict[str, Any]:
        """
        Get company profile from Finnhub.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with company profile
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return {}

        try:
            profile = self._finnhub_client.company_profile2(symbol=ticker)
            logger.info(f"Retrieved Finnhub profile for {ticker}")
            return profile or {}
        except Exception as e:
            logger.error(f"Error fetching Finnhub profile for {ticker}: {e}")
            return {}

    def get_company_news_finnhub(
        self,
        ticker: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get company news from Finnhub.

        Args:
            ticker: Stock ticker symbol
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of news articles
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return []

        try:
            if not from_date:
                from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
            if not to_date:
                to_date = datetime.now().strftime("%Y-%m-%d")

            news = self._finnhub_client.company_news(ticker, _from=from_date, to=to_date)
            logger.info(f"Retrieved {len(news)} news articles for {ticker}")
            return news or []
        except Exception as e:
            logger.error(f"Error fetching Finnhub news for {ticker}: {e}")
            return []

    def get_sec_filings_finnhub(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Get SEC filings from Finnhub.

        Args:
            ticker: Stock ticker symbol

        Returns:
            List of SEC filings
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return []

        try:
            filings = self._finnhub_client.filings(symbol=ticker)
            logger.info(f"Retrieved {len(filings)} SEC filings for {ticker}")
            return filings or []
        except Exception as e:
            logger.error(f"Error fetching SEC filings for {ticker}: {e}")
            return []

    def get_earnings_calendar_finnhub(self, ticker: str) -> List[Dict[str, Any]]:
        """
        Get earnings calendar from Finnhub.

        Args:
            ticker: Stock ticker symbol

        Returns:
            List of earnings dates
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return []

        try:
            # Get next 30 days of earnings
            from_date = datetime.now().strftime("%Y-%m-%d")
            to_date = (datetime.now() + timedelta(days=90)).strftime("%Y-%m-%d")

            earnings = self._finnhub_client.earnings_calendar(
                _from=from_date,
                to=to_date,
                symbol=ticker
            )

            result = earnings.get("earningsCalendar", []) if earnings else []
            logger.info(f"Retrieved {len(result)} earnings dates for {ticker}")
            return result
        except Exception as e:
            logger.error(f"Error fetching earnings calendar for {ticker}: {e}")
            return []

    def get_quote_finnhub(self, ticker: str) -> Dict[str, Any]:
        """
        Get real-time quote from Finnhub.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with quote data (c=current, h=high, l=low, o=open, pc=previous_close)
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return {}

        try:
            quote = self._finnhub_client.quote(ticker)
            if quote and quote.get("c", 0) > 0:
                logger.info(f"Retrieved Finnhub quote for {ticker}: ${quote.get('c', 0):.2f}")
                return {
                    "price": quote.get("c", 0),
                    "high": quote.get("h", 0),
                    "low": quote.get("l", 0),
                    "open": quote.get("o", 0),
                    "previous_close": quote.get("pc", 0),
                    "change": quote.get("d", 0),
                    "change_pct": quote.get("dp", 0),
                }
            return {}
        except Exception as e:
            logger.error(f"Error fetching Finnhub quote for {ticker}: {e}")
            return {}

    def get_basic_financials_finnhub(self, ticker: str) -> Dict[str, Any]:
        """
        Get basic financials from Finnhub (P/E, P/B, market cap, etc.).

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with financial metrics
        """
        if not self._finnhub_client:
            logger.warning("Finnhub client not initialized")
            return {}

        try:
            financials = self._finnhub_client.company_basic_financials(ticker, "all")
            if financials and financials.get("metric"):
                metrics = financials.get("metric", {})
                logger.info(f"Retrieved Finnhub basic financials for {ticker}")
                return {
                    "pe_ratio": metrics.get("peNormalizedAnnual", 0),
                    "price_to_book": metrics.get("pbAnnual", 0),
                    "market_cap": metrics.get("marketCapitalization", 0) * 1e6 if metrics.get("marketCapitalization") else 0,
                    "beta": metrics.get("beta", 0),
                    "dividend_yield": metrics.get("dividendYieldIndicatedAnnual", 0),
                    "eps": metrics.get("epsNormalizedAnnual", 0),
                    "roe": metrics.get("roeTTM", 0),
                    "roa": metrics.get("roaTTM", 0),
                    "52_week_high": metrics.get("52WeekHigh", 0),
                    "52_week_low": metrics.get("52WeekLow", 0),
                }
            return {}
        except Exception as e:
            logger.error(f"Error fetching Finnhub basic financials for {ticker}: {e}")
            return {}

    # =========================================================================
    # Fallback Methods (yfinance → Finnhub)
    # =========================================================================

    def get_company_info_with_fallback(self, ticker: str) -> Dict[str, Any]:
        """
        Get company info with Finnhub fallback if yfinance fails.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with company info (from yfinance or Finnhub)
        """
        # Try yfinance first
        info = self.get_company_info(ticker)

        # If yfinance returned valid data, return it
        if info and info.get("name") and info.get("price", 0) > 0:
            return info

        # yfinance failed, try Finnhub fallback
        if not self._finnhub_client:
            logger.warning(f"yfinance failed for {ticker} and Finnhub not configured")
            return info  # Return whatever we got from yfinance

        logger.info(f"yfinance failed for {ticker}, trying Finnhub fallback...")

        # Get data from Finnhub
        profile = self.get_company_profile_finnhub(ticker)
        quote = self.get_quote_finnhub(ticker)
        financials = self.get_basic_financials_finnhub(ticker)

        if not profile and not quote:
            logger.warning(f"Finnhub fallback also failed for {ticker}")
            return info  # Return whatever we got from yfinance

        # Build combined result
        fallback_info = {
            "ticker": ticker,
            "name": profile.get("name", info.get("name", "")),
            "sector": profile.get("finnhubIndustry", info.get("sector", "")),
            "industry": profile.get("finnhubIndustry", info.get("industry", "")),
            "website": profile.get("weburl", info.get("website", "")),
            "country": profile.get("country", info.get("country", "")),
            "exchange": profile.get("exchange", info.get("exchange", "")),
            "currency": profile.get("currency", info.get("currency", "USD")),
            "market_cap": profile.get("marketCapitalization", 0) * 1e6 if profile.get("marketCapitalization") else financials.get("market_cap", 0),
            "price": quote.get("price", info.get("price", 0)),
            "previous_close": quote.get("previous_close", info.get("previous_close", 0)),
            "open": quote.get("open", info.get("open", 0)),
            "day_high": quote.get("high", info.get("day_high", 0)),
            "day_low": quote.get("low", info.get("day_low", 0)),
            "pe_ratio": financials.get("pe_ratio", info.get("pe_ratio", 0)),
            "price_to_book": financials.get("price_to_book", info.get("price_to_book", 0)),
            "beta": financials.get("beta", info.get("beta", 0)),
            "dividend_yield": financials.get("dividend_yield", info.get("dividend_yield", 0)),
            "fifty_two_week_high": financials.get("52_week_high", info.get("fifty_two_week_high", 0)),
            "fifty_two_week_low": financials.get("52_week_low", info.get("52_week_low", 0)),
            "_source": "finnhub_fallback",  # Mark as fallback data
        }

        logger.info(f"Finnhub fallback successful for {ticker}")
        return fallback_info

    def get_current_price_with_fallback(self, ticker: str) -> float:
        """
        Get current stock price with Finnhub fallback.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Current price
        """
        # Try yfinance first
        info = self.get_company_info(ticker)
        price = info.get("price", 0.0)

        if price > 0:
            return price

        # Fallback to Finnhub
        if self._finnhub_client:
            logger.info(f"yfinance price failed for {ticker}, trying Finnhub...")
            quote = self.get_quote_finnhub(ticker)
            if quote and quote.get("price", 0) > 0:
                return quote.get("price", 0.0)

        logger.warning(f"Failed to get price for {ticker} from both sources")
        return 0.0

    def has_finnhub_fallback(self) -> bool:
        """
        Check if Finnhub fallback is available.

        Returns:
            True if Finnhub client is configured
        """
        return self._finnhub_client is not None

    # =========================================================================
    # Utility Methods
    # =========================================================================

    def get_current_price(self, ticker: str) -> float:
        """
        Get current stock price.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Current price
        """
        info = self.get_company_info(ticker)
        return info.get("price", 0.0)

    def get_market_cap(self, ticker: str) -> float:
        """
        Get market capitalization.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Market cap in USD
        """
        info = self.get_company_info(ticker)
        return info.get("market_cap", 0.0)

    def is_large_cap(self, ticker: str, threshold: float = 20e9) -> bool:
        """
        Check if stock is large cap (default: $20B+).

        Args:
            ticker: Stock ticker symbol
            threshold: Market cap threshold (default: $20B)

        Returns:
            True if large cap
        """
        market_cap = self.get_market_cap(ticker)
        return market_cap >= threshold

    def get_price_change(self, ticker: str) -> Dict[str, float]:
        """
        Get price change statistics.

        Args:
            ticker: Stock ticker symbol

        Returns:
            Dictionary with price change data
        """
        info = self.get_company_info(ticker)

        price = info.get("price", 0)
        previous_close = info.get("previous_close", 0)

        if previous_close > 0:
            change = price - previous_close
            change_pct = (change / previous_close) * 100
        else:
            change = 0
            change_pct = 0

        return {
            "price": price,
            "previous_close": previous_close,
            "change": change,
            "change_pct": change_pct,
            "day_high": info.get("day_high", 0),
            "day_low": info.get("day_low", 0),
            "volume": info.get("volume", 0),
        }


# Convenience function for quick access
def get_us_data_client(finnhub_api_key: Optional[str] = None) -> USDataClient:
    """
    Create and return a USDataClient instance.

    Args:
        finnhub_api_key: Optional Finnhub API key

    Returns:
        USDataClient instance
    """
    return USDataClient(finnhub_api_key=finnhub_api_key)


if __name__ == "__main__":
    # Test the client
    import logging
    logging.basicConfig(level=logging.INFO)

    client = USDataClient()

    print("\n=== Testing USDataClient ===\n")

    # Test OHLCV
    print("1. OHLCV Data (AAPL, 10 days):")
    df = client.get_ohlcv("AAPL", period="10d")
    print(df.tail())

    # Test company info
    print("\n2. Company Info (AAPL):")
    info = client.get_company_info("AAPL")
    print(f"  Name: {info.get('name')}")
    print(f"  Sector: {info.get('sector')}")
    print(f"  Market Cap: ${info.get('market_cap', 0):,.0f}")
    print(f"  Price: ${info.get('price', 0):.2f}")
    print(f"  P/E Ratio: {info.get('pe_ratio', 0):.2f}")

    # Test institutional holders
    print("\n3. Institutional Holders (AAPL):")
    holders = client.get_institutional_holders("AAPL")
    if holders.get("institutional_holders") is not None:
        print(holders["institutional_holders"].head())

    # Test market indices
    print("\n4. Market Indices:")
    indices = client.get_market_indices(period="5d")
    for name, df in indices.items():
        if not df.empty:
            latest = df.iloc[-1]
            print(f"  {name}: ${latest.get('close', 0):,.2f}")

    print("\n=== Test Complete ===")
