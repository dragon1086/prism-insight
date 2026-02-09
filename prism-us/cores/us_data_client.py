"""
US Stock Data Client

Unified interface for fetching US stock market data using yfinance.

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
from typing import Any, Dict, Optional

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
    - Institutional holders (yfinance)
    """

    def __init__(self):
        """Initialize the US data client."""
        pass

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

        Note: This is FREE via yfinance

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
def get_us_data_client() -> USDataClient:
    """
    Create and return a USDataClient instance.

    Returns:
        USDataClient instance
    """
    return USDataClient()


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
