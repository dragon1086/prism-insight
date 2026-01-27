#!/usr/bin/env python3
"""
US Stock Tracking and Trading Agent

This module performs buy/sell decisions using AI-based US stock analysis reports
and manages trading records.

Main Features:
1. Generate trading scenarios based on analysis reports
2. Manage stock purchases/sales (maximum 10 slots)
3. Track trading history and returns
4. Share results through Telegram channel

Key Differences from Korean Version:
- Uses ticker symbols (AAPL, MSFT) instead of 6-digit codes
- Uses yfinance for price data
- Uses USD currency
- US market hours (09:30-16:00 EST)
- Uses us_* database tables
"""
from dotenv import load_dotenv
load_dotenv()

import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional

# Add parent directory to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from telegram import Bot
from telegram.error import TelegramError

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"us_stock_tracking_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# MCP related imports
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Import US-specific modules
# Use explicit path to avoid conflicts with main project
_prism_us_dir = Path(__file__).parent
sys.path.insert(0, str(_prism_us_dir))

try:
    # First try direct import from prism-us directory
    from cores.agents.trading_agents import create_us_trading_scenario_agent
    from tracking.db_schema import (
        create_us_tables,
        create_us_indexes,
        add_sector_column_if_missing,
        add_market_column_to_shared_tables,
        is_us_ticker_in_holdings,
        get_us_holdings_count,
    )
    from tracking.journal import USJournalManager
    from tracking.compression import USCompressionManager
except ImportError:
    # Fallback to package import
    from prism_us.cores.agents.trading_agents import create_us_trading_scenario_agent
    from prism_us.tracking.db_schema import (
        create_us_tables,
        create_us_indexes,
        add_sector_column_if_missing,
        add_market_column_to_shared_tables,
        is_us_ticker_in_holdings,
        get_us_holdings_count,
    )
    from prism_us.tracking.journal import USJournalManager
    from prism_us.tracking.compression import USCompressionManager

# Create MCPApp instance
app = MCPApp(name="us_stock_tracking")


# =============================================================================
# US-Specific Helper Functions
# =============================================================================

def extract_ticker_info(report_path: str) -> Tuple[str, str]:
    """
    Extract ticker and company name from report file path.

    Args:
        report_path: Report file path (e.g., "AAPL_Apple Inc_20260118.pdf")

    Returns:
        Tuple[str, str]: Ticker, company name
    """
    try:
        file_name = Path(report_path).stem
        # Pattern: TICKER_CompanyName_date
        pattern = r'^([A-Z]+)_([^_]+)'
        match = re.match(pattern, file_name)

        if match:
            ticker = match.group(1)
            company_name = match.group(2)
            return ticker, company_name
        else:
            # Fallback
            parts = file_name.split('_')
            if len(parts) >= 2:
                return parts[0], parts[1]

        logger.error(f"Cannot extract ticker info from filename: {file_name}")
        return "", ""
    except Exception as e:
        logger.error(f"Error extracting ticker info: {str(e)}")
        return "", ""


async def get_current_stock_price(cursor, ticker: str) -> float:
    """
    Get current US stock price using yfinance.

    Args:
        cursor: SQLite cursor
        ticker: Stock ticker symbol (e.g., "AAPL")

    Returns:
        float: Current stock price in USD
    """
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        info = stock.info
        current_price = info.get('regularMarketPrice', 0) or info.get('previousClose', 0)

        if current_price > 0:
            logger.info(f"{ticker} current price: ${current_price:.2f}")
            return float(current_price)
        else:
            logger.warning(f"Cannot get price for {ticker}")
            return _get_last_price_from_db(cursor, ticker)

    except Exception as e:
        logger.error(f"Error querying current price for {ticker}: {str(e)}")
        return _get_last_price_from_db(cursor, ticker)


def _get_last_price_from_db(cursor, ticker: str) -> float:
    """Get last saved price from DB as fallback."""
    try:
        cursor.execute(
            "SELECT current_price FROM us_stock_holdings WHERE ticker = ?",
            (ticker,)
        )
        row = cursor.fetchone()
        if row and row[0]:
            last_price = float(row[0])
            logger.warning(f"{ticker} price query failed, using last price: ${last_price:.2f}")
            return last_price
    except:
        pass
    return 0.0


async def get_trading_value_rank_change(ticker: str) -> Tuple[float, str]:
    """
    Calculate trading value ranking change for a US stock.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Tuple[float, str]: Ranking change percentage, analysis result message
    """
    try:
        import yfinance as yf

        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")

        if hist.empty or len(hist) < 2:
            return 0, "Insufficient historical data"

        # Get recent 2 days
        recent_volume = hist['Volume'].iloc[-1]
        previous_volume = hist['Volume'].iloc[-2]
        recent_price = hist['Close'].iloc[-1]
        previous_price = hist['Close'].iloc[-2]

        # Calculate trading value
        recent_value = recent_volume * recent_price
        previous_value = previous_volume * previous_price

        if previous_value > 0:
            value_change_percentage = ((recent_value - previous_value) / previous_value) * 100
        else:
            value_change_percentage = 0

        # Get average volume for context
        avg_volume = hist['Volume'].mean()
        volume_ratio = recent_volume / avg_volume if avg_volume > 0 else 1

        result_msg = (
            f"Trading value: ${recent_value/1e6:.1f}M "
            f"(prev: ${previous_value/1e6:.1f}M, "
            f"change: {'▲' if value_change_percentage > 0 else '▼' if value_change_percentage < 0 else '='}"
            f"{abs(value_change_percentage):.1f}%), "
            f"Volume ratio: {volume_ratio:.2f}x"
        )

        logger.info(f"{ticker} {result_msg}")
        return value_change_percentage, result_msg

    except Exception as e:
        logger.error(f"Error analyzing trading value for {ticker}: {str(e)}")
        return 0, "Trading value analysis failed"


def check_sector_diversity(cursor, sector: str, max_same_sector: int, concentration_ratio: float) -> bool:
    """
    Check for over-concentration in same sector.

    Args:
        cursor: SQLite cursor
        sector: GICS sector name
        max_same_sector: Maximum holdings in same sector
        concentration_ratio: Sector concentration limit ratio

    Returns:
        bool: True if can add more, False if over-concentrated
    """
    try:
        if not sector or sector.lower() == "unknown":
            return True

        cursor.execute("SELECT scenario FROM us_stock_holdings")
        holdings_scenarios = cursor.fetchall()

        sectors = []
        for row in holdings_scenarios:
            if row[0]:
                try:
                    scenario_data = json.loads(row[0])
                    if 'sector' in scenario_data:
                        sectors.append(scenario_data['sector'])
                except:
                    pass

        same_sector_count = sum(1 for s in sectors if s and s.lower() == sector.lower())

        if same_sector_count >= max_same_sector or \
           (sectors and same_sector_count / len(sectors) >= concentration_ratio):
            logger.warning(
                f"Sector '{sector}' over-concentration risk: "
                f"Currently holding {same_sector_count} stocks "
                f"(max {max_same_sector}, limit {concentration_ratio*100:.0f}%)"
            )
            return False

        return True

    except Exception as e:
        logger.error(f"Error checking sector diversity: {str(e)}")
        return True


def parse_price_value(value: Any) -> float:
    """Parse price value and convert to number."""
    try:
        if isinstance(value, (int, float)):
            return float(value)

        if isinstance(value, str):
            value = value.replace(',', '').replace('$', '')

            range_patterns = [
                r'(\d+(?:\.\d+)?)\s*[-~]\s*(\d+(?:\.\d+)?)',
            ]

            for pattern in range_patterns:
                match = re.search(pattern, value)
                if match:
                    low = float(match.group(1))
                    high = float(match.group(2))
                    return (low + high) / 2

            number_match = re.search(r'(\d+(?:\.\d+)?)', value)
            if number_match:
                return float(number_match.group(1))

        return 0
    except Exception as e:
        logger.warning(f"Failed to parse price value: {value} - {str(e)}")
        return 0


def default_scenario() -> Dict[str, Any]:
    """Return default trading scenario for US stocks."""
    return {
        "portfolio_analysis": "Analysis failed",
        "buy_score": 0,
        "decision": "no_entry",
        "target_price": 0,
        "stop_loss": 0,
        "investment_period": "short",
        "rationale": "Analysis failed",
        "sector": "Unknown",
        "considerations": "Analysis failed"
    }


# =============================================================================
# US Stock Tracking Agent
# =============================================================================

class USStockTrackingAgent:
    """US Stock Tracking and Trading Agent"""

    # Constants
    MAX_SLOTS = 10  # Maximum number of stocks to hold
    MAX_SAME_SECTOR = 3  # Maximum holdings in same sector
    SECTOR_CONCENTRATION_RATIO = 0.3  # Sector concentration limit ratio

    # Investment period constants
    PERIOD_SHORT = "short"  # Within 1 month
    PERIOD_MEDIUM = "medium"  # 1-3 months
    PERIOD_LONG = "long"  # 3+ months

    # Buy score thresholds
    SCORE_STRONG_BUY = 8  # Strong buy
    SCORE_CONSIDER = 7  # Consider buying
    SCORE_UNSUITABLE = 6  # Unsuitable for buying

    def __init__(
        self,
        db_path: str = "stock_tracking_db.sqlite",
        telegram_token: str = None,
        enable_journal: bool = False
    ):
        """
        Initialize US Stock Tracking Agent.

        Args:
            db_path: SQLite database file path
            telegram_token: Telegram bot token
            enable_journal: Whether to enable trading journal feature
        """
        self.max_slots = self.MAX_SLOTS
        self.message_queue = []
        self.trading_agent = None
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.language = "en"  # Default to English for US
        self.enable_journal = enable_journal

        # Journal and compression managers (initialized in initialize())
        self.journal_manager = None
        self.compression_manager = None

        # Set Telegram bot token
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_bot = None
        if self.telegram_token:
            self.telegram_bot = Bot(token=self.telegram_token)

    async def initialize(self, language: str = "en"):
        """
        Create necessary tables and initialize.

        Args:
            language: Language code for agents (default: "en")
        """
        logger.info("Starting US tracking agent initialization")

        self.language = language

        # Initialize SQLite connection
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

        # Initialize trading scenario agent for US
        self.trading_agent = create_us_trading_scenario_agent(language=language)

        # Create US database tables
        await self._create_tables()

        # Initialize journal manager
        self.journal_manager = USJournalManager(
            cursor=self.cursor,
            conn=self.conn,
            language=language,
            enable_journal=self.enable_journal
        )

        # Initialize compression manager
        self.compression_manager = USCompressionManager(
            cursor=self.cursor,
            conn=self.conn
        )

        logger.info(f"US tracking agent initialization complete (journal: {self.enable_journal})")
        return True

    async def _create_tables(self):
        """Create necessary US database tables."""
        create_us_tables(self.cursor, self.conn)
        create_us_indexes(self.cursor, self.conn)
        add_sector_column_if_missing(self.cursor, self.conn)
        # Add market column to shared tables for KR/US distinction
        add_market_column_to_shared_tables(self.cursor, self.conn)

    def _normalize_decision(self, decision: str) -> str:
        """
        Normalize decision string for comparison.

        The agent prompt uses "Enter" or "No Entry" but code checks may use
        lowercase variants. This method normalizes all variants to a consistent format.

        Args:
            decision: Raw decision string from agent

        Returns:
            str: Normalized decision ("entry" or "no_entry")
        """
        if not decision:
            return "no_entry"
        d = decision.lower().strip()
        # Handle various entry formats
        if d in ("enter", "entry", "진입", "yes", "buy"):
            return "entry"
        # Handle various no-entry formats
        elif d in ("no entry", "no_entry", "no-entry", "미진입", "no", "skip", "pass"):
            return "no_entry"
        return d

    async def _extract_ticker_info(self, report_path: str) -> Tuple[str, str]:
        """Extract ticker and company name from report path."""
        return extract_ticker_info(report_path)

    async def _get_current_stock_price(self, ticker: str) -> float:
        """Get current stock price."""
        return await get_current_stock_price(self.cursor, ticker)

    async def _get_trading_value_rank_change(self, ticker: str) -> Tuple[float, str]:
        """Calculate trading value ranking change."""
        return await get_trading_value_rank_change(ticker)

    async def _is_ticker_in_holdings(self, ticker: str) -> bool:
        """Check if stock is already in holdings."""
        return is_us_ticker_in_holdings(self.cursor, ticker)

    async def _get_current_slots_count(self) -> int:
        """Get current number of holdings."""
        return get_us_holdings_count(self.cursor)

    async def _check_sector_diversity(self, sector: str) -> bool:
        """Check for over-concentration in same sector."""
        return check_sector_diversity(
            self.cursor, sector,
            self.MAX_SAME_SECTOR, self.SECTOR_CONCENTRATION_RATIO
        )

    async def _extract_trading_scenario(
        self,
        report_content: str,
        rank_change_msg: str = "",
        ticker: str = None,
        sector: str = None,
        trigger_type: str = "",
        trigger_mode: str = ""
    ) -> Dict[str, Any]:
        """
        Extract trading scenario from report.

        Args:
            report_content: Analysis report content
            rank_change_msg: Trading value ranking change info
            ticker: Stock ticker symbol
            sector: Stock sector
            trigger_type: Trigger type
            trigger_mode: Trigger mode

        Returns:
            Dict: Trading scenario information
        """
        try:
            # Get current holdings info
            current_slots = await self._get_current_slots_count()

            # Collect current portfolio information
            self.cursor.execute("""
                SELECT ticker, company_name, buy_price, current_price, scenario
                FROM us_stock_holdings
            """)
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Analyze sector distribution
            sector_distribution = {}
            investment_periods = {"short": 0, "medium": 0, "long": 0}

            for holding in holdings:
                scenario_str = holding.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_data = json.loads(scenario_str)
                        sector_name = scenario_data.get('sector', 'Unknown')
                        sector_distribution[sector_name] = sector_distribution.get(sector_name, 0) + 1
                        period = scenario_data.get('investment_period', 'medium')
                        investment_periods[period] = investment_periods.get(period, 0) + 1
                except:
                    pass

            # Portfolio info string
            portfolio_info = f"""
            Current holdings: {current_slots}/{self.max_slots}
            Sector distribution: {json.dumps(sector_distribution, ensure_ascii=False)}
            Investment period distribution: {json.dumps(investment_periods, ensure_ascii=False)}
            """

            # LLM call to generate trading scenario
            llm = await self.trading_agent.attach_llm(OpenAIAugmentedLLM)

            # Build trigger info section
            trigger_info_section = ""
            if trigger_type:
                trigger_info_section = f"""
                ### Trigger Info (Apply Trigger-Based Entry Criteria)
                - **Triggered By**: {trigger_type}
                - **Trigger Mode**: {trigger_mode or 'unknown'}
                """

            prompt_message = f"""
            This is an AI analysis report for a US stock. Please generate a trading scenario based on this report.

            ### Current Portfolio Status:
            {portfolio_info}
            {trigger_info_section}
            ### Trading Value Analysis:
            {rank_change_msg}

            ### Report Content:
            {report_content}
            """

            response = await llm.generate_str(
                message=prompt_message,
                request_params=RequestParams(
                    model="gpt-5.2",
                    maxTokens=20000
                )
            )

            # JSON parsing
            try:
                def fix_json_syntax(json_str):
                    """Fix JSON syntax errors."""
                    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)
                    json_str = re.sub(r'(\])\s*(\n\s*")', r'\1,\2', json_str)
                    json_str = re.sub(r'(})\s*(\n\s*")', r'\1,\2', json_str)
                    json_str = re.sub(r'([0-9]|")\s*(\n\s*")', r'\1,\2', json_str)
                    json_str = re.sub(r',\s*,', ',', json_str)
                    return json_str

                # Try markdown code block
                markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
                if markdown_match:
                    json_str = fix_json_syntax(markdown_match.group(1))
                    scenario_json = json.loads(json_str)
                    logger.info(f"Scenario parsed from markdown: {json.dumps(scenario_json, ensure_ascii=False)}")
                    return scenario_json

                # Try regular JSON
                json_match = re.search(r'({[\s\S]*?})(?:\s*$|\n\n)', response, re.DOTALL)
                if json_match:
                    json_str = fix_json_syntax(json_match.group(1))
                    scenario_json = json.loads(json_str)
                    return scenario_json

                # Full response as JSON
                clean_response = fix_json_syntax(response)
                scenario_json = json.loads(clean_response)
                return scenario_json

            except Exception as json_err:
                logger.error(f"Trading scenario JSON parse error: {json_err}")
                logger.error(f"Original response: {response[:500]}...")

                # Try json_repair library
                try:
                    import json_repair
                    repaired = json_repair.repair_json(response)
                    scenario_json = json.loads(repaired)
                    return scenario_json
                except (ImportError, Exception):
                    pass

                return default_scenario()

        except Exception as e:
            logger.error(f"Error extracting trading scenario: {str(e)}")
            logger.error(traceback.format_exc())
            return default_scenario()

    async def analyze_report(self, pdf_report_path: str) -> Dict[str, Any]:
        """
        Analyze US stock analysis report and make trading decision.

        Args:
            pdf_report_path: PDF analysis report file path

        Returns:
            Dict: Trading decision result
        """
        try:
            logger.info(f"Starting report analysis: {pdf_report_path}")

            # Extract ticker and company name
            ticker, company_name = await self._extract_ticker_info(pdf_report_path)

            if not ticker or not company_name:
                logger.error(f"Failed to extract ticker info: {pdf_report_path}")
                return {"success": False, "error": "Failed to extract ticker info"}

            # Check if already holding
            is_holding = await self._is_ticker_in_holdings(ticker)
            if is_holding:
                logger.info(f"{ticker} ({company_name}) already in holdings")
                holding_current_price = await self._get_current_stock_price(ticker)
                return {
                    "success": True,
                    "decision": "holding",
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": holding_current_price
                }

            # Get current stock price
            current_price = await self._get_current_stock_price(ticker)
            if current_price <= 0:
                logger.error(f"{ticker} current price query failed")
                return {"success": False, "error": "Current price query failed"}

            # Analyze trading value
            rank_change_percentage, rank_change_msg = await self._get_trading_value_rank_change(ticker)

            # Read report content
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(pdf_report_path)

            # Get trigger info
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', '')
            trigger_mode = trigger_info.get('trigger_mode', '')

            # Extract trading scenario
            scenario = await self._extract_trading_scenario(
                report_content,
                rank_change_msg,
                ticker=ticker,
                sector=None,
                trigger_type=trigger_type,
                trigger_mode=trigger_mode
            )

            # Check sector diversity
            sector = scenario.get("sector", "Unknown")
            is_sector_diverse = await self._check_sector_diversity(sector)

            # Normalize decision for consistent comparison
            raw_decision = scenario.get("decision", "no_entry")
            normalized_decision = self._normalize_decision(raw_decision)

            return {
                "success": True,
                "ticker": ticker,
                "company_name": company_name,
                "current_price": current_price,
                "scenario": scenario,
                "decision": normalized_decision,  # Normalized: "entry" or "no_entry"
                "raw_decision": raw_decision,  # Original from agent for logging
                "sector": sector,
                "sector_diverse": is_sector_diverse,
                "rank_change_percentage": rank_change_percentage,
                "rank_change_msg": rank_change_msg
            }

        except Exception as e:
            logger.error(f"Error analyzing report: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    async def buy_stock(self, ticker: str, company_name: str, current_price: float,
                        scenario: Dict[str, Any], rank_change_msg: str = "") -> bool:
        """
        Process stock purchase.

        Args:
            ticker: Stock ticker symbol
            company_name: Company name
            current_price: Current stock price in USD
            scenario: Trading scenario information
            rank_change_msg: Trading value ranking change info

        Returns:
            bool: Purchase success status
        """
        try:
            # Check if already holding
            if await self._is_ticker_in_holdings(ticker):
                logger.warning(f"{ticker} ({company_name}) already in holdings")
                return False

            # Check available slots
            current_slots = await self._get_current_slots_count()
            if current_slots >= self.max_slots:
                logger.warning(f"Holdings already at maximum ({self.max_slots})")
                return False

            # Current time
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Get trigger info
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', 'AI_Analysis')
            trigger_mode = trigger_info.get('trigger_mode', getattr(self, 'trigger_mode', 'unknown'))

            # Add to holdings table
            self.cursor.execute(
                """
                INSERT INTO us_stock_holdings
                (ticker, company_name, buy_price, buy_date, current_price, last_updated,
                 scenario, target_price, stop_loss, trigger_type, trigger_mode, sector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    current_price,
                    now,
                    json.dumps(scenario, ensure_ascii=False),
                    scenario.get('target_price', 0),
                    scenario.get('stop_loss', 0),
                    trigger_type,
                    trigger_mode,
                    scenario.get('sector', 'Unknown')
                )
            )
            self.conn.commit()

            # Build buy message
            target_price = scenario.get('target_price', 0)
            stop_loss = scenario.get('stop_loss', 0)

            message = f"NEW BUY: {company_name} ({ticker})\n" \
                      f"Buy Price: ${current_price:.2f}\n" \
                      f"Target: ${target_price:.2f}\n" \
                      f"Stop Loss: ${stop_loss:.2f}\n" \
                      f"Investment Period: {scenario.get('investment_period', 'short')}\n" \
                      f"Sector: {scenario.get('sector', 'Unknown')}\n"

            if scenario.get('valuation_analysis'):
                message += f"Valuation: {scenario.get('valuation_analysis')}\n"

            if rank_change_msg:
                message += f"Trading Value: {rank_change_msg}\n"

            message += f"Rationale: {scenario.get('rationale', 'N/A')}\n"

            self.message_queue.append(message)
            logger.info(f"{ticker} ({company_name}) purchase complete")

            return True

        except Exception as e:
            logger.error(f"{ticker} Error during purchase: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_sell_decision(self, stock_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Sell decision analysis.

        Args:
            stock_data: Stock information

        Returns:
            Tuple[bool, str]: Whether to sell, sell reason
        """
        try:
            ticker = stock_data.get('ticker', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            target_price = stock_data.get('target_price', 0)
            stop_loss = stock_data.get('stop_loss', 0)

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0

            # Days elapsed from buy date
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # Extract scenario information
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "medium"

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', 'medium')
            except:
                pass

            # Check stop-loss condition
            if stop_loss > 0 and current_price <= stop_loss:
                return True, f"Stop loss triggered (stop: ${stop_loss:.2f})"

            # Check target price reached
            if target_price > 0 and current_price >= target_price:
                return True, f"Target reached (target: ${target_price:.2f})"

            # Sell conditions by investment period
            if investment_period == "short":
                if days_passed >= 15 and profit_rate >= 5:
                    return True, f"Short-term target achieved (days: {days_passed}, return: {profit_rate:.2f}%)"
                if days_passed >= 10 and profit_rate <= -3:
                    return True, f"Short-term loss protection (days: {days_passed}, return: {profit_rate:.2f}%)"

            # General sell conditions
            if profit_rate >= 10:
                return True, f"Profit >= 10% (current: {profit_rate:.2f}%)"

            if profit_rate <= -5:
                return True, f"Loss >= 5% (current: {profit_rate:.2f}%)"

            if days_passed >= 30 and profit_rate < 0:
                return True, f"30+ days holding with loss (days: {days_passed}, return: {profit_rate:.2f}%)"

            if days_passed >= 60 and profit_rate >= 3:
                return True, f"60+ days holding with 3%+ profit (days: {days_passed}, return: {profit_rate:.2f}%)"

            if investment_period == "long" and days_passed >= 90 and profit_rate < 0:
                return True, f"Long-term loss cleanup (days: {days_passed}, return: {profit_rate:.2f}%)"

            return False, "Continue holding"

        except Exception as e:
            logger.error(f"Error analyzing sell decision: {str(e)}")
            return False, "Analysis error"

    async def sell_stock(self, stock_data: Dict[str, Any], sell_reason: str) -> bool:
        """
        Process stock sale.

        Args:
            stock_data: Stock information to sell
            sell_reason: Sell reason

        Returns:
            bool: Sell success status
        """
        try:
            ticker = stock_data.get('ticker', '')
            company_name = stock_data.get('company_name', '')
            buy_price = stock_data.get('buy_price', 0)
            buy_date = stock_data.get('buy_date', '')
            current_price = stock_data.get('current_price', 0)
            scenario_json = stock_data.get('scenario', '{}')
            trigger_type = stock_data.get('trigger_type', 'AI_Analysis')
            trigger_mode = stock_data.get('trigger_mode', 'unknown')
            sector = stock_data.get('sector', 'Unknown')

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0

            # Calculate holding period
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            holding_days = (datetime.now() - buy_datetime).days
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Add to trading history
            self.cursor.execute(
                """
                INSERT INTO us_trading_history
                (ticker, company_name, buy_price, buy_date, sell_price, sell_date,
                 profit_rate, holding_days, scenario, trigger_type, trigger_mode, sector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker, company_name, buy_price, buy_date,
                    current_price, now, profit_rate, holding_days,
                    scenario_json, trigger_type, trigger_mode, sector
                )
            )

            # Remove from holdings
            self.cursor.execute(
                "DELETE FROM us_stock_holdings WHERE ticker = ?",
                (ticker,)
            )
            self.conn.commit()

            # Build sell message
            arrow = "▲" if profit_rate > 0 else "▼" if profit_rate < 0 else "─"
            message = f"SELL: {company_name} ({ticker})\n" \
                      f"Buy: ${buy_price:.2f}\n" \
                      f"Sell: ${current_price:.2f}\n" \
                      f"Return: {arrow} {abs(profit_rate):.2f}%\n" \
                      f"Holding: {holding_days} days\n" \
                      f"Reason: {sell_reason}"

            self.message_queue.append(message)
            logger.info(f"{ticker} ({company_name}) sell complete (return: {profit_rate:.2f}%)")

            # Create trading journal entry (if enabled)
            if self.enable_journal and self.journal_manager:
                try:
                    await self.journal_manager.create_entry(
                        stock_data=stock_data,
                        sell_price=current_price,
                        profit_rate=profit_rate,
                        holding_days=holding_days,
                        sell_reason=sell_reason
                    )
                    logger.info(f"US Journal entry created for {ticker}")
                except Exception as journal_err:
                    logger.warning(f"Failed to create US journal entry: {journal_err}")

            return True

        except Exception as e:
            logger.error(f"Error during sell: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def update_holdings(self) -> List[Dict[str, Any]]:
        """
        Update holdings information and make sell decisions.

        Returns:
            List[Dict]: List of sold stock information
        """
        try:
            logger.info("Starting US holdings update")

            # Query holdings list
            self.cursor.execute(
                """SELECT ticker, company_name, buy_price, buy_date, current_price,
                   scenario, target_price, stop_loss, last_updated,
                   trigger_type, trigger_mode, sector
                   FROM us_stock_holdings"""
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            if not holdings:
                logger.info("No US holdings")
                return []

            sold_stocks = []

            for stock in holdings:
                ticker = stock.get('ticker')
                company_name = stock.get('company_name')

                # Query current stock price
                current_price = await self._get_current_stock_price(ticker)

                if current_price <= 0:
                    old_price = stock.get('current_price', 0)
                    logger.warning(f"{ticker} current price query failed, using last: ${old_price:.2f}")
                    current_price = old_price

                stock['current_price'] = current_price
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Analyze sell decision
                should_sell, sell_reason = await self._analyze_sell_decision(stock)

                if should_sell:
                    sell_success = await self.sell_stock(stock, sell_reason)

                    if sell_success:
                        # Execute actual trading
                        try:
                            from prism_us.trading.us_stock_trading import AsyncUSTradingContext
                            async with AsyncUSTradingContext() as trading:
                                trade_result = await trading.async_sell_stock(ticker=ticker)

                            if trade_result['success']:
                                logger.info(f"Actual sell successful: {trade_result['message']}")
                            else:
                                logger.error(f"Actual sell failed: {trade_result['message']}")
                        except Exception as trade_err:
                            logger.warning(f"Trading execution skipped: {trade_err}")

                        sold_stocks.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "buy_price": stock.get('buy_price', 0),
                            "sell_price": current_price,
                            "profit_rate": ((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 0) * 100) if stock.get('buy_price', 0) > 0 else 0,
                            "reason": sell_reason
                        })
                else:
                    # Update current price
                    self.cursor.execute(
                        """UPDATE us_stock_holdings
                           SET current_price = ?, last_updated = ?
                           WHERE ticker = ?""",
                        (current_price, now, ticker)
                    )
                    self.conn.commit()
                    logger.info(f"{ticker} ({company_name}) price updated: ${current_price:.2f} ({sell_reason})")

            return sold_stocks

        except Exception as e:
            logger.error(f"Error updating holdings: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def generate_report_summary(self) -> str:
        """
        Generate holdings and profit statistics summary.

        Returns:
            str: Summary message
        """
        try:
            # Query holdings
            self.cursor.execute(
                """SELECT ticker, company_name, buy_price, current_price, buy_date,
                   scenario, target_price, stop_loss, sector
                   FROM us_stock_holdings"""
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Calculate total profit from trading history
            self.cursor.execute("SELECT SUM(profit_rate) FROM us_trading_history")
            total_profit = self.cursor.fetchone()[0] or 0

            # Number of trades
            self.cursor.execute("SELECT COUNT(*) FROM us_trading_history")
            total_trades = self.cursor.fetchone()[0] or 0

            # Number of successful trades
            self.cursor.execute("SELECT COUNT(*) FROM us_trading_history WHERE profit_rate > 0")
            successful_trades = self.cursor.fetchone()[0] or 0

            # Generate message (Korean as default - same as Korean stock version)
            message = f"📊 프리즘 US 시뮬레이터 | 실시간 포트폴리오 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

            # 1. Portfolio summary
            message += f"🔸 현재 보유 종목: {len(holdings) if holdings else 0}/{self.max_slots}개\n"

            # Best profit/loss stock information (if any)
            if holdings and len(holdings) > 0:
                profit_rates = []
                for h in holdings:
                    buy_price = h.get('buy_price', 0)
                    current_price = h.get('current_price', 0)
                    if buy_price > 0:
                        profit_rate = ((current_price - buy_price) / buy_price) * 100
                        profit_rates.append((h.get('ticker'), h.get('company_name'), profit_rate))

                if profit_rates:
                    best = max(profit_rates, key=lambda x: x[2])
                    worst = min(profit_rates, key=lambda x: x[2])

                    message += f"✅ 최고 수익: {best[1]}({best[0]}) {'+' if best[2] > 0 else ''}{best[2]:.2f}%\n"
                    message += f"⚠️ 최저 수익: {worst[1]}({worst[0]}) {'+' if worst[2] > 0 else ''}{worst[2]:.2f}%\n"

            message += "\n"

            # 2. Sector distribution analysis
            sector_counts = {}

            if holdings and len(holdings) > 0:
                message += f"🔸 보유 종목 목록:\n"
                for stock in holdings:
                    ticker = stock.get('ticker', '')
                    company_name = stock.get('company_name', '')
                    buy_price = stock.get('buy_price', 0)
                    current_price = stock.get('current_price', 0)
                    buy_date = stock.get('buy_date', '')
                    target_price = stock.get('target_price', 0)
                    stop_loss = stock.get('stop_loss', 0)
                    scenario_str = stock.get('scenario', '{}')

                    # Extract sector information from scenario
                    sector = "알 수 없음"
                    try:
                        if isinstance(scenario_str, str):
                            scenario_data = json.loads(scenario_str)
                            sector = scenario_data.get('sector', '알 수 없음')
                    except:
                        sector = stock.get('sector', '알 수 없음')

                    # Update sector count
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1

                    profit_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price > 0 else 0
                    arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"

                    buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S") if buy_date else datetime.now()
                    days_passed = (datetime.now() - buy_datetime).days

                    message += f"- {company_name}({ticker}) [{sector}]\n"
                    message += f"  매수가: ${buy_price:.2f} / 현재가: ${current_price:.2f}\n"
                    message += f"  목표가: ${target_price:.2f} / 손절가: ${stop_loss:.2f}\n"
                    message += f"  수익률: {arrow} {profit_rate:.2f}% / 보유기간: {days_passed}일\n\n"

                # 산업군 분포 추가
                message += f"🔸 산업군 분포:\n"
                for sector, count in sector_counts.items():
                    percentage = (count / len(holdings)) * 100
                    message += f"- {sector}: {count}개 ({percentage:.1f}%)\n"
                message += "\n"
            else:
                message += "보유 중인 종목이 없습니다.\n\n"

            # 3. Trading history statistics
            message += f"🔸 매매 이력 통계\n"
            message += f"- 총 거래 건수: {total_trades}건\n"
            message += f"- 수익 거래: {successful_trades}건\n"
            message += f"- 손실 거래: {total_trades - successful_trades}건\n"

            if total_trades > 0:
                message += f"- 승률: {(successful_trades / total_trades * 100):.2f}%\n"
            else:
                message += f"- 승률: 0.00%\n"

            message += f"- 누적 수익률: {total_profit:.2f}%\n\n"

            # 4. 강화된 면책 조항
            message += "📝 안내사항:\n"
            message += "- 이 보고서는 AI 기반 시뮬레이션 결과이며, 실제 매매와 무관합니다.\n"
            message += "- 본 정보는 단순 참고용이며, 투자 결정과 책임은 전적으로 투자자에게 있습니다.\n"
            message += "- 이 채널은 리딩방이 아니며, 특정 종목 매수/매도를 권유하지 않습니다."

            return message

        except Exception as e:
            logger.error(f"Error generating report summary: {str(e)}")
            return f"Error generating report: {str(e)}"

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        Process analysis reports and make buy/sell decisions.

        Args:
            pdf_report_paths: List of PDF analysis report file paths

        Returns:
            Tuple[int, int]: Buy count, sell count
        """
        try:
            logger.info(f"Processing {len(pdf_report_paths)} US reports")

            buy_count = 0
            sell_count = 0

            # Update existing holdings and make sell decisions
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)} stocks sold")
            else:
                logger.info("No stocks sold")

            # Analyze new reports and make buy decisions
            for pdf_report_path in pdf_report_paths:
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(f"Report analysis failed: {pdf_report_path}")
                    continue

                if analysis_result.get("decision") == "holding":
                    logger.info(f"Skipping stock in holdings: {analysis_result.get('ticker')}")
                    continue

                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_msg = analysis_result.get("rank_change_msg", "")

                if not sector_diverse:
                    logger.info(f"Purchase deferred: {company_name} ({ticker}) - Sector over-concentration")
                    continue

                buy_score = scenario.get("buy_score", 0)
                min_score = scenario.get("min_score", 0)
                sector = analysis_result.get("sector", "Unknown")

                # Apply score adjustment from journal if enabled
                score_adjustment = 0
                adjustment_reasons = []
                if self.enable_journal and ticker:
                    score_adjustment, adjustment_reasons = self.get_score_adjustment(ticker, sector)
                    if score_adjustment != 0:
                        logger.info(
                            f"Journal score adjustment for {ticker}: {score_adjustment:+d} "
                            f"(reasons: {', '.join(adjustment_reasons)})"
                        )

                adjusted_score = buy_score + score_adjustment
                logger.info(
                    f"Buy score: {company_name} ({ticker}) - Original: {buy_score}, "
                    f"Adjusted: {adjusted_score}, Min: {min_score}"
                )

                # Log decision normalization for debugging
                raw_decision = analysis_result.get("raw_decision", "")
                normalized_decision = analysis_result.get("decision", "no_entry")
                if raw_decision and raw_decision.lower() != normalized_decision:
                    logger.debug(f"Decision normalized: '{raw_decision}' -> '{normalized_decision}'")

                if normalized_decision == "entry":
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        # Execute actual trading
                        try:
                            from prism_us.trading.us_stock_trading import AsyncUSTradingContext
                            async with AsyncUSTradingContext() as trading:
                                trade_result = await trading.async_buy_stock(ticker=ticker)

                            if trade_result['success']:
                                logger.info(f"Actual purchase successful: {trade_result['message']}")
                            else:
                                logger.error(f"Actual purchase failed: {trade_result['message']}")
                        except Exception as trade_err:
                            logger.warning(f"Trading execution skipped: {trade_err}")

                        buy_count += 1
                        logger.info(f"Purchase complete: {company_name} ({ticker}) @ ${current_price:.2f}")
                    else:
                        logger.warning(f"Purchase failed: {company_name} ({ticker})")
                else:
                    reason = ""
                    if adjusted_score < min_score:
                        reason = f"Score insufficient ({adjusted_score} < {min_score})"
                    else:
                        reason = f"No entry decision (raw: '{raw_decision}', normalized: '{normalized_decision}')"
                    logger.info(f"Purchase deferred: {company_name} ({ticker}) - {reason}")

            logger.info(f"Report processing complete - Bought: {buy_count}, Sold: {sell_count}")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"Error processing reports: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def send_telegram_message(self, chat_id: str, language: str = "ko") -> bool:
        """
        Send message via Telegram.

        Args:
            chat_id: Telegram channel ID (no sending if None)
            language: Message language ("ko" or "en")

        Returns:
            bool: Send success status
        """
        try:
            # Skip Telegram sending if chat_id is None
            if not chat_id:
                logger.info("No Telegram channel ID. Skipping message send")

                # Log message output
                for message in self.message_queue:
                    logger.info(f"[Message (not sent)] {message[:100]}...")

                # Initialize message queue
                self.message_queue = []
                return True  # Consider intentional skip as success

            # If Telegram bot not initialized, only output logs
            if not self.telegram_bot:
                logger.warning("Telegram bot not initialized. Please check token")

                # Only output messages without actual sending
                for message in self.message_queue:
                    logger.info(f"[Telegram message (bot not initialized)] {message[:100]}...")

                # Initialize message queue
                self.message_queue = []
                return False

            # Generate summary report
            summary = await self.generate_report_summary()
            self.message_queue.append(summary)

            # Translate messages if English is requested
            if language == "en":
                logger.info(f"Translating {len(self.message_queue)} US messages to English")
                try:
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    translated_queue = []
                    for idx, message in enumerate(self.message_queue, 1):
                        logger.info(f"Translating US message {idx}/{len(self.message_queue)}")
                        translated = await translate_telegram_message(message, model="gpt-5-nano")
                        translated_queue.append(translated)
                    self.message_queue = translated_queue
                    logger.info("All US messages translated successfully")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean messages.")

            # 각 메시지 전송
            success = True
            for message in self.message_queue:
                logger.info(f"Sending US Telegram message: {chat_id}")
                try:
                    # 텔레그램 메시지 길이 제한 (4096자)
                    MAX_MESSAGE_LENGTH = 4096

                    if len(message) <= MAX_MESSAGE_LENGTH:
                        # 메시지가 짧으면 한 번에 전송
                        await self.telegram_bot.send_message(
                            chat_id=chat_id,
                            text=message
                        )
                    else:
                        # 메시지가 길면 분할 전송
                        parts = []
                        current_part = ""

                        for line in message.split('\n'):
                            if len(current_part) + len(line) + 1 <= MAX_MESSAGE_LENGTH:
                                current_part += line + '\n'
                            else:
                                if current_part:
                                    parts.append(current_part.rstrip())
                                current_part = line + '\n'

                        if current_part:
                            parts.append(current_part.rstrip())

                        # 분할된 메시지 전송
                        for i, part in enumerate(parts, 1):
                            await self.telegram_bot.send_message(
                                chat_id=chat_id,
                                text=f"[{i}/{len(parts)}]\n{part}"
                            )
                            await asyncio.sleep(0.5)  # 분할 메시지 간 짧은 지연

                    logger.info(f"US Telegram message sent: {chat_id}")
                except TelegramError as e:
                    logger.error(f"US Telegram message send failed: {e}")
                    success = False

                # API 제한 방지를 위한 지연
                await asyncio.sleep(1)

            # Send to broadcast channels if configured (wait for completion)
            if hasattr(self, 'telegram_config') and self.telegram_config and self.telegram_config.broadcast_languages:
                # Create task and wait for it to complete
                translation_task = asyncio.create_task(self._send_to_translation_channels(self.message_queue.copy()))
                await translation_task
                logger.info("US broadcast channel messages sent successfully")

            # 메시지 큐 초기화
            self.message_queue = []

            return success

        except Exception as e:
            logger.error(f"Error sending US Telegram message: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _send_to_translation_channels(self, messages: List[str]):
        """
        Send messages to translation channels

        Args:
            messages: List of original Korean messages
        """
        try:
            from cores.agents.telegram_translator_agent import translate_telegram_message

            for lang in self.telegram_config.broadcast_languages:
                try:
                    # Get channel ID for this language
                    channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID configured for language: {lang}")
                        continue

                    logger.info(f"Sending US tracking messages to {lang} channel")

                    # Translate and send each message
                    for message in messages:
                        try:
                            # Translate message
                            logger.info(f"Translating US tracking message to {lang}")
                            translated_message = await translate_telegram_message(
                                message,
                                model="gpt-5-nano",
                                from_lang="ko",
                                to_lang=lang
                            )

                            # Send translated message
                            MAX_MESSAGE_LENGTH = 4096

                            if len(translated_message) <= MAX_MESSAGE_LENGTH:
                                await self.telegram_bot.send_message(
                                    chat_id=channel_id,
                                    text=translated_message
                                )
                            else:
                                # Split long messages
                                parts = []
                                current_part = ""

                                for line in translated_message.split('\n'):
                                    if len(current_part) + len(line) + 1 <= MAX_MESSAGE_LENGTH:
                                        current_part += line + '\n'
                                    else:
                                        if current_part:
                                            parts.append(current_part.rstrip())
                                        current_part = line + '\n'

                                if current_part:
                                    parts.append(current_part.rstrip())

                                for i, part in enumerate(parts, 1):
                                    await self.telegram_bot.send_message(
                                        chat_id=channel_id,
                                        text=f"[{i}/{len(parts)}]\n{part}"
                                    )
                                    await asyncio.sleep(0.5)

                            logger.info(f"US tracking message sent successfully to {lang} channel")

                            await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"Error translating/sending US message to {lang}: {str(e)}")

                except Exception as e:
                    logger.error(f"Error processing language {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_to_translation_channels: {str(e)}")

    def get_compression_stats(self) -> Dict[str, Any]:
        """
        Get current compression statistics for US market.

        Returns:
            Dict with compression layer counts and stats
        """
        if self.compression_manager:
            return self.compression_manager.get_compression_stats()
        return {"error": "Compression manager not initialized"}

    async def compress_old_journal_entries(
        self,
        layer1_age_days: int = 7,
        layer2_age_days: int = 30,
        min_entries_for_compression: int = 3
    ) -> Dict[str, Any]:
        """
        Compress old journal entries for US market.

        Args:
            layer1_age_days: Days before Layer 1 entries are compressed
            layer2_age_days: Days before Layer 2 entries are compressed
            min_entries_for_compression: Minimum entries to trigger compression

        Returns:
            Dict with compression results
        """
        if self.compression_manager:
            return await self.compression_manager.compress_old_journal_entries(
                layer1_age_days=layer1_age_days,
                layer2_age_days=layer2_age_days,
                min_entries_for_compression=min_entries_for_compression
            )
        return {"error": "Compression manager not initialized"}

    def cleanup_stale_data(
        self,
        max_principles: int = 50,
        max_intuitions: int = 50,
        stale_days: int = 90,
        archive_layer3_days: int = 365,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """
        Clean up stale data for US market.

        Args:
            max_principles: Maximum active principles to keep
            max_intuitions: Maximum active intuitions to keep
            stale_days: Days without validation before deactivation
            archive_layer3_days: Days after which to archive Layer 3 entries
            dry_run: If True, only count what would be cleaned

        Returns:
            Dict with cleanup results
        """
        if self.compression_manager:
            return self.compression_manager.cleanup_stale_data(
                max_principles=max_principles,
                max_intuitions=max_intuitions,
                stale_days=stale_days,
                archive_layer3_days=archive_layer3_days,
                dry_run=dry_run
            )
        return {"error": "Compression manager not initialized"}

    def get_journal_context(self, ticker: str, sector: str = None) -> str:
        """
        Get trading journal context for buy decisions.

        Args:
            ticker: Stock ticker symbol
            sector: Stock sector (optional)

        Returns:
            str: Context string with past trading experiences
        """
        if self.journal_manager and self.enable_journal:
            return self.journal_manager.get_context_for_ticker(ticker, sector)
        return ""

    def get_score_adjustment(self, ticker: str, sector: str = None) -> Tuple[int, List[str]]:
        """
        Calculate score adjustment based on past experiences.

        Args:
            ticker: Stock ticker symbol
            sector: Stock sector (optional)

        Returns:
            Tuple[int, List[str]]: Adjustment value (-2 to +2) and reasons
        """
        if self.journal_manager and self.enable_journal:
            return self.journal_manager.get_score_adjustment(ticker, sector)
        return 0, []

    async def run(self, pdf_report_paths: List[str], chat_id: str = None,
                  language: str = "ko", telegram_config=None, trigger_results_file: str = None) -> bool:
        """
        Main execution function for US stock tracking system.

        Args:
            pdf_report_paths: List of analysis report file paths
            chat_id: Telegram channel ID (optional)
            language: Message language (default: "ko")
            telegram_config: TelegramConfig object for multi-language support
            trigger_results_file: Path to trigger results JSON file

        Returns:
            bool: Execution success status
        """
        try:
            logger.info("Starting US tracking system batch execution")

            # Store telegram_config for use in send_telegram_message
            self.telegram_config = telegram_config

            # Load trigger type mapping
            self.trigger_info_map = {}
            if trigger_results_file:
                try:
                    if os.path.exists(trigger_results_file):
                        with open(trigger_results_file, 'r', encoding='utf-8') as f:
                            trigger_data = json.load(f)
                        for trigger_type, stocks in trigger_data.items():
                            if trigger_type == 'metadata':
                                self.trigger_mode = trigger_data.get('metadata', {}).get('trigger_mode', '')
                                continue
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    ticker = stock.get('ticker', stock.get('code', ''))
                                    if ticker:
                                        self.trigger_info_map[ticker] = {
                                            'trigger_type': trigger_type,
                                            'trigger_mode': trigger_data.get('metadata', {}).get('trigger_mode', ''),
                                            'risk_reward_ratio': stock.get('risk_reward_ratio', 0)
                                        }
                        logger.info(f"Loaded trigger info for {len(self.trigger_info_map)} stocks")
                except Exception as e:
                    logger.warning(f"Failed to load trigger results file: {e}")

            # Initialize
            await self.initialize(language)

            try:
                # Process reports
                buy_count, sell_count = await self.process_reports(pdf_report_paths)

                # Send Telegram message
                if chat_id:
                    message_sent = await self.send_telegram_message(chat_id, language)
                    if message_sent:
                        logger.info("US Telegram message sent successfully")
                    else:
                        logger.warning("US Telegram message send failed")
                else:
                    logger.info("Telegram channel ID not provided, skipping message send")
                    await self.send_telegram_message(None, language)

                logger.info("US tracking system batch execution complete")
                return True
            finally:
                if self.conn:
                    self.conn.close()
                    logger.info("Database connection closed")

        except Exception as e:
            logger.error(f"Error during US tracking system execution: {str(e)}")
            logger.error(traceback.format_exc())

            if hasattr(self, 'conn') and self.conn:
                try:
                    self.conn.close()
                except:
                    pass

            return False


async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="US Stock tracking and trading agent")
    parser.add_argument("--reports", nargs="+", help="List of analysis report file paths")
    parser.add_argument("--chat-id", help="Telegram channel ID")
    parser.add_argument("--telegram-token", help="Telegram bot token")
    parser.add_argument("--language", default="ko", help="Language (default: ko)")
    parser.add_argument(
        "--enable-journal",
        action="store_true",
        help="Enable trading journal for retrospective analysis"
    )

    args = parser.parse_args()

    if not args.reports:
        logger.error("Report path not specified")
        return False

    async with app.run():
        agent = USStockTrackingAgent(
            telegram_token=args.telegram_token,
            enable_journal=args.enable_journal
        )
        success = await agent.run(args.reports, args.chat_id, args.language)
        return success


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error during program execution: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
