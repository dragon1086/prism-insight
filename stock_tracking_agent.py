#!/usr/bin/env python3
"""
Stock Tracking and Trading Agent

This module performs buy/sell decisions using AI-based stock analysis reports
and manages trading records.

Main Features:
1. Generate trading scenarios based on analysis reports
2. Manage stock purchases/sales (maximum 10 slots)
3. Track trading history and returns
4. Share results through Telegram channel
"""
from dotenv import load_dotenv
load_dotenv()  # .env 파일에서 환경변수 로드

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

from telegram import Bot
from telegram.error import TelegramError

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"stock_tracking_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# MCP related imports
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Core agent imports
from cores.agents.trading_agents import create_trading_scenario_agent

# Tracking package imports (refactored helpers)
from tracking import (
    create_all_tables,
    create_indexes,
    add_scope_column_if_missing,
    add_trigger_columns_if_missing,
    add_sector_column_if_missing,
    extract_ticker_info,
    get_current_stock_price,
    get_trading_value_rank_change,
    is_ticker_in_holdings,
    get_current_slots_count,
    check_sector_diversity,
    parse_price_value,
    default_scenario,
    analyze_sell_decision,
    format_buy_message,
    format_sell_message,
    calculate_profit_rate,
    calculate_holding_days,
    JournalManager,
    CompressionManager,
    TelegramSender,
)

# Create MCPApp instance
app = MCPApp(name="stock_tracking")

class StockTrackingAgent:
    """Stock Tracking and Trading Agent"""

    # Constants
    MAX_SLOTS = 10  # Maximum number of stocks to hold
    MAX_SAME_SECTOR = 3  # Maximum holdings in same sector
    SECTOR_CONCENTRATION_RATIO = 0.3  # Sector concentration limit ratio

    # Investment period constants
    PERIOD_SHORT = "단기"  # Within 1 month
    PERIOD_MEDIUM = "중기"  # 1-3 months
    PERIOD_LONG = "장기"  # 3+ months

    # Buy score thresholds
    SCORE_STRONG_BUY = 8  # Strong buy
    SCORE_CONSIDER = 7  # Consider buying
    SCORE_UNSUITABLE = 6  # Unsuitable for buying

    def __init__(self, db_path: str = "stock_tracking_db.sqlite", telegram_token: str = None, enable_journal: bool = None):
        """
        Initialize agent

        Args:
            db_path: SQLite database file path
            telegram_token: Telegram bot token
            enable_journal: Enable trading journal feature (default: False, reads from ENABLE_TRADING_JOURNAL env)
        """
        self.max_slots = self.MAX_SLOTS
        self.message_queue = []  # For storing Telegram messages
        self.trading_agent = None
        self.db_path = db_path
        self.conn = None
        self.cursor = None

        # Set trading journal feature flag
        # Priority: parameter > environment variable > default (False)
        if enable_journal is not None:
            self.enable_journal = enable_journal
        else:
            env_value = os.environ.get("ENABLE_TRADING_JOURNAL", "false").lower()
            self.enable_journal = env_value in ("true", "1", "yes")

        # Set Telegram bot token
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.telegram_bot = None
        if self.telegram_token:
            self.telegram_bot = Bot(token=self.telegram_token)

    async def initialize(self, language: str = "ko"):
        """
        Create necessary tables and initialize

        Args:
            language: Language code for agents (default: "ko")
        """
        logger.info("Starting tracking agent initialization")
        logger.info(f"Trading journal feature: {'enabled' if self.enable_journal else 'disabled'}")

        # Store language for later use
        self.language = language

        # Initialize SQLite connection
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Return results as dictionary
        self.cursor = self.conn.cursor()

        # Initialize trading scenario generation agent with language
        self.trading_agent = create_trading_scenario_agent(language=language)

        # Create database tables
        await self._create_tables()

        # Initialize helper managers (delegates to tracking/ package)
        self.journal_manager = JournalManager(
            self.cursor, self.conn, language, self.enable_journal
        )
        self.compression_manager = CompressionManager(
            self.cursor, self.conn, language, self.enable_journal
        )
        self.telegram_sender = TelegramSender(self.telegram_bot)

        logger.info("Tracking agent initialization complete")
        return True

    async def _create_tables(self):
        """Create necessary database tables (delegates to tracking.db_schema)"""
        create_all_tables(self.cursor, self.conn)
        add_scope_column_if_missing(self.cursor, self.conn)  # Must run before indexes
        add_trigger_columns_if_missing(self.cursor, self.conn)  # v1.16.5 migration
        add_sector_column_if_missing(self.cursor, self.conn)  # v1.17 migration for AI agent sector queries
        create_indexes(self.cursor, self.conn)

    async def _extract_ticker_info(self, report_path: str) -> Tuple[str, str]:
        """Extract ticker code and company name (delegates to tracking.helpers)"""
        return extract_ticker_info(report_path)

    async def _get_current_stock_price(self, ticker: str) -> float:
        """Get current stock price (delegates to tracking.helpers)"""
        return await get_current_stock_price(self.cursor, ticker)

    async def _get_trading_value_rank_change(self, ticker: str) -> Tuple[float, str]:
        """Calculate trading value ranking change (delegates to tracking.helpers)"""
        return await get_trading_value_rank_change(ticker)

    async def _is_ticker_in_holdings(self, ticker: str) -> bool:
        """Check if stock is already in holdings (delegates to tracking.helpers)"""
        return is_ticker_in_holdings(self.cursor, ticker)

    async def _get_current_slots_count(self) -> int:
        """Get current number of holdings (delegates to tracking.helpers)"""
        return get_current_slots_count(self.cursor)

    async def _check_sector_diversity(self, sector: str) -> bool:
        """Check for over-concentration in same sector (delegates to tracking.helpers)"""
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
        Extract trading scenario from report

        Args:
            report_content: Analysis report content
            rank_change_msg: Trading value ranking change info
            ticker: Stock ticker code (for journal context lookup)
            sector: Stock sector (for journal context lookup)
            trigger_type: Trigger type that activated this analysis (e.g., '거래량 급증 상위주')
            trigger_mode: Trigger mode ('morning' or 'afternoon')

        Returns:
            Dict: Trading scenario information
        """
        try:
            # Get current holdings info and sector distribution
            current_slots = await self._get_current_slots_count()

            # Collect current portfolio information
            self.cursor.execute("""
                SELECT ticker, company_name, buy_price, current_price, scenario
                FROM stock_holdings
            """)
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Analyze sector distribution
            sector_distribution = {}
            investment_periods = {"단기": 0, "중기": 0, "장기": 0}

            for holding in holdings:
                scenario_str = holding.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_data = json.loads(scenario_str)

                        # Collect sector info
                        sector_name = scenario_data.get('sector', '알 수 없음')
                        sector_distribution[sector_name] = sector_distribution.get(sector_name, 0) + 1

                        # Collect investment period info
                        period = scenario_data.get('investment_period', '중기')
                        investment_periods[period] = investment_periods.get(period, 0) + 1
                except:
                    pass

            # Portfolio info string
            portfolio_info = f"""
            Current holdings: {current_slots}/{self.max_slots}
            Sector distribution: {json.dumps(sector_distribution, ensure_ascii=False)}
            Investment period distribution: {json.dumps(investment_periods, ensure_ascii=False)}
            """

            # Get trading journal context for informed decisions
            journal_context = ""
            score_adjustment_info = ""
            if ticker:
                journal_context = self._get_relevant_journal_context(
                    ticker=ticker,
                    sector=sector,
                    market_condition=None
                )
                # Get score adjustment suggestion
                adjustment, reasons = self._get_score_adjustment_from_context(ticker, sector)
                if adjustment != 0 or reasons:
                    if self.language == "ko":
                        score_adjustment_info = f"""
                ### 📊 과거 경험 기반 점수 보정 제안
                - 권장 점수 조정: {'+' if adjustment > 0 else ''}{adjustment}점
                - 조정 이유: {', '.join(reasons) if reasons else '해당 없음'}
                - ⚠️ 이 보정값은 과거 경험에 기반한 참고 사항입니다.
                """
                    else:
                        score_adjustment_info = f"""
                ### 📊 Score Adjustment Suggestion (Experience-Based)
                - Recommended Adjustment: {'+' if adjustment > 0 else ''}{adjustment} points
                - Reason: {', '.join(reasons) if reasons else 'N/A'}
                - ⚠️ This adjustment is a reference based on past experience.
                """

            # LLM call to generate trading scenario
            llm = await self.trading_agent.attach_llm(OpenAIAugmentedLLM)

            # Build trigger info section if available
            trigger_info_section = ""
            if trigger_type:
                if self.language == "ko":
                    trigger_info_section = f"""
                ### 📡 트리거 정보 (진입 기준 차별화 필수 참고)
                - **발동 트리거**: {trigger_type}
                - **트리거 모드**: {trigger_mode or '알 수 없음'}
                """
                else:
                    trigger_info_section = f"""
                ### 📡 Trigger Info (Apply Trigger-Based Entry Criteria)
                - **Triggered By**: {trigger_type}
                - **Trigger Mode**: {trigger_mode or 'unknown'}
                """

            # Prepare prompt based on language
            if self.language == "ko":
                prompt_message = f"""
                다음은 주식 종목에 대한 AI 분석 보고서입니다. 이 보고서를 기반으로 매매 시나리오를 생성해주세요.

                ### 현재 포트폴리오 상황:
                {portfolio_info}
                {trigger_info_section}
                ### 거래대금 분석:
                {rank_change_msg}
                {score_adjustment_info}
                {journal_context}

                ### 보고서 내용:
                {report_content}
                """
            else:  # English
                prompt_message = f"""
                This is an AI analysis report for a stock. Please generate a trading scenario based on this report.

                ### Current Portfolio Status:
                {portfolio_info}
                {trigger_info_section}
                ### Trading Value Analysis:
                {rank_change_msg}
                {score_adjustment_info}
                {journal_context}

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
            # TODO: Create model and call generate_structured function to improve code maintainability
            # TODO: Move JSON conversion function to utils for better maintainability
            try:
                # JSON string extraction function
                def fix_json_syntax(json_str):
                    """Fix JSON syntax errors"""
                    # 1. Remove trailing commas
                    json_str = re.sub(r',(\s*[}\]])', r'\1', json_str)

                    # 2. Add comma after array before object property
                    # Add comma if " follows ] (array ends and new property starts)
                    json_str = re.sub(r'(\])\s*(\n\s*")', r'\1,\2', json_str)

                    # 3. Add comma after object before object property
                    # Add comma if " follows } (object ends and new property starts)
                    json_str = re.sub(r'(})\s*(\n\s*")', r'\1,\2', json_str)

                    # 4. Add comma after number or string before property
                    # Add comma if new line and " follows number or string ending with "
                    json_str = re.sub(r'([0-9]|")\s*(\n\s*")', r'\1,\2', json_str)

                    # 5. Remove duplicate commas
                    json_str = re.sub(r',\s*,', ',', json_str)

                    return json_str

                # Try extracting JSON from markdown code block (```json ... ```)
                markdown_match = re.search(r'```(?:json)?\s*({[\s\S]*?})\s*```', response, re.DOTALL)
                if markdown_match:
                    json_str = markdown_match.group(1)
                    json_str = fix_json_syntax(json_str)
                    scenario_json = json.loads(json_str)
                    logger.info(f"Scenario parsed from markdown code block: {json.dumps(scenario_json, ensure_ascii=False)}")
                    return scenario_json

                # Try extracting regular JSON object
                json_match = re.search(r'({[\s\S]*?})(?:\s*$|\n\n)', response, re.DOTALL)
                if json_match:
                    json_str = json_match.group(1)
                    json_str = fix_json_syntax(json_str)
                    scenario_json = json.loads(json_str)
                    logger.info(f"Scenario parsed from regular JSON format: {json.dumps(scenario_json, ensure_ascii=False)}")
                    return scenario_json

                # If full response is JSON
                clean_response = fix_json_syntax(response)
                scenario_json = json.loads(clean_response)
                logger.info(f"Full response scenario: {json.dumps(scenario_json, ensure_ascii=False)}")
                return scenario_json

            except Exception as json_err:
                logger.error(f"Trading scenario JSON parse error: {json_err}")
                logger.error(f"Original response: {response}")

                # Additional recovery attempt: More robust JSON fixing
                try:
                    clean_response = re.sub(r'```(?:json)?|```', '', response).strip()

                    # Fix all possible JSON syntax errors
                    # 1. Add comma after array/object end before property
                    clean_response = re.sub(r'(\]|\})\s*(\n\s*"[^"]+"\s*:)', r'\1,\2', clean_response)

                    # 2. Add comma after value before property
                    clean_response = re.sub(r'(["\d\]\}])\s*\n\s*("[^"]+"\s*:)', r'\1,\n    \2', clean_response)

                    # 3. Remove trailing commas
                    clean_response = re.sub(r',(\s*[}\]])', r'\1', clean_response)

                    # 4. Remove duplicate commas
                    clean_response = re.sub(r',\s*,+', ',', clean_response)

                    scenario_json = json.loads(clean_response)
                    logger.info(f"Scenario parsed with additional recovery: {json.dumps(scenario_json, ensure_ascii=False)}")
                    return scenario_json
                except Exception as e:
                    logger.error(f"Additional recovery attempt failed: {str(e)}")

                    # Last resort: Use json_repair library if available
                    try:
                        import json_repair
                        repaired = json_repair.repair_json(response)
                        scenario_json = json.loads(repaired)
                        logger.info("Successfully recovered with json_repair")
                        return scenario_json
                    except (ImportError, Exception):
                        pass

                # Return default scenario if all parsing attempts fail
                return self._default_scenario()

        except Exception as e:
            logger.error(f"Error extracting trading scenario: {str(e)}")
            logger.error(traceback.format_exc())
            return self._default_scenario()

    def _default_scenario(self) -> Dict[str, Any]:
        """Return default trading scenario (delegates to tracking.helpers)"""
        return default_scenario()

    async def analyze_report(self, pdf_report_path: str) -> Dict[str, Any]:
        """
        Analyze stock analysis report and make trading decision

        Args:
            pdf_report_path: PDF analysis report file path

        Returns:
            Dict: Trading decision result
        """
        try:
            logger.info(f"Starting report analysis: {pdf_report_path}")

            # Extract ticker code and company name from file path
            ticker, company_name = await self._extract_ticker_info(pdf_report_path)

            if not ticker or not company_name:
                logger.error(f"Failed to extract ticker info: {pdf_report_path}")
                return {"success": False, "error": "Failed to extract ticker info"}

            # Check if already holding this stock
            is_holding = await self._is_ticker_in_holdings(ticker)
            if is_holding:
                logger.info(f"{ticker}({company_name}) already in holdings")
                # Get current price for the stock even when already holding
                holding_current_price = await self._get_current_stock_price(ticker)
                return {
                    "success": True,
                    "decision": "보유 중",
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": holding_current_price
                }

            # Get current stock price
            current_price = await self._get_current_stock_price(ticker)
            if current_price <= 0:
                logger.error(f"{ticker} current price query failed")
                return {"success": False, "error": "Current price query failed"}

            # Analyze trading value ranking change
            rank_change_percentage, rank_change_msg = await self._get_trading_value_rank_change(ticker)

            # Read report content
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(pdf_report_path)

            # Get trigger info for this ticker (from trigger_results file loaded at run() time)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', '')
            trigger_mode = trigger_info.get('trigger_mode', '')

            # Extract trading scenario (pass trading value ranking info, ticker, and trigger info)
            scenario = await self._extract_trading_scenario(
                report_content,
                rank_change_msg,
                ticker=ticker,
                sector=None,  # sector will be determined by the scenario agent
                trigger_type=trigger_type,
                trigger_mode=trigger_mode
            )

            # Check sector diversity
            sector = scenario.get("sector", "알 수 없음")
            is_sector_diverse = await self._check_sector_diversity(sector)

            # Return result
            return {
                "success": True,
                "ticker": ticker,
                "company_name": company_name,
                "current_price": current_price,
                "scenario": scenario,
                "decision": scenario.get("decision", "미진입"),
                "sector": sector,
                "sector_diverse": is_sector_diverse,
                "rank_change_percentage": rank_change_percentage,
                "rank_change_msg": rank_change_msg
            }

        except Exception as e:
            logger.error(f"Error analyzing report: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    def _parse_price_value(self, value: Any) -> float:
        """Parse price value and convert to number (delegates to tracking.helpers)"""
        return parse_price_value(value)

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "") -> bool:
        """
        Process stock purchase

        Args:
            ticker: Stock code
            company_name: Company name
            current_price: Current stock price
            scenario: Trading scenario information
            rank_change_msg: Trading value ranking change info

        Returns:
            bool: Purchase success status
        """
        try:
            # Check if already holding
            if await self._is_ticker_in_holdings(ticker):
                logger.warning(f"{ticker}({company_name}) already in holdings")
                return False

            # Check available slots
            current_slots = await self._get_current_slots_count()
            if current_slots >= self.max_slots:
                logger.warning(f"Holdings already at maximum ({self.max_slots})")
                return False

            # Check market-based maximum portfolio size
            max_portfolio_size = scenario.get('max_portfolio_size', self.max_slots)
            # Convert to int if stored as string
            if isinstance(max_portfolio_size, str):
                try:
                    max_portfolio_size = int(max_portfolio_size)
                except (ValueError, TypeError):
                    max_portfolio_size = self.max_slots
            if current_slots >= max_portfolio_size:
                logger.warning(f"Reached market-based max portfolio size ({max_portfolio_size}). Current holdings: {current_slots}")
                return False

            # Current time
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            # Get trigger info from trigger_info_map (loaded from trigger_results file)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', 'AI분석')
            trigger_mode = trigger_info.get('trigger_mode', getattr(self, 'trigger_mode', 'unknown'))

            # Add to holdings table
            self.cursor.execute(
                """
                INSERT INTO stock_holdings
                (ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, trigger_type, trigger_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    trigger_mode
                )
            )
            self.conn.commit()

            # Add purchase message
            message = f"📈 신규 매수: {company_name}({ticker})\n" \
                      f"매수가: {current_price:,.0f} \n" \
                      f"목표가: {scenario.get('target_price', 0):,.0f}원\n" \
                      f"손절가: {scenario.get('stop_loss', 0):,.0f}원\n" \
                      f"투자기간: {scenario.get('investment_period', '단기')}\n" \
                      f"산업군: {scenario.get('sector', '알 수 없음')}\n"

            # Add valuation analysis if available
            if scenario.get('valuation_analysis'):
                message += f"밸류에이션: {scenario.get('valuation_analysis')}\n"

            # Add sector outlook if available
            if scenario.get('sector_outlook'):
                message += f"업종 전망: {scenario.get('sector_outlook')}\n"

            # Add trading value ranking info if available
            if rank_change_msg:
                message += f"거래대금 분석: {rank_change_msg}\n"

            message += f"투자근거: {scenario.get('rationale', '정보 없음')}\n"
            
            # Format trading scenario
            trading_scenarios = scenario.get('trading_scenarios', {})
            if trading_scenarios and isinstance(trading_scenarios, dict):
                message += "\n" + "="*40 + "\n"
                message += "📋 매매 시나리오\n"
                message += "="*40 + "\n\n"
                
                # 1. 핵심 가격대 (Key Levels)
                key_levels = trading_scenarios.get('key_levels', {})
                if key_levels:
                    message += "💰 핵심 가격대:\n"
                    
                    # 저항선
                    primary_resistance = self._parse_price_value(key_levels.get('primary_resistance', 0))
                    secondary_resistance = self._parse_price_value(key_levels.get('secondary_resistance', 0))
                    if primary_resistance or secondary_resistance:
                        message += f"  📈 저항선:\n"
                        if secondary_resistance:
                            message += f"    • 2차: {secondary_resistance:,.0f}원\n"
                        if primary_resistance:
                            message += f"    • 1차: {primary_resistance:,.0f}원\n"
                    
                    # 현재가 표시
                    message += f"  ━━ 현재가: {current_price:,.0f} 원 ━━\n"
                    
                    # 지지선
                    primary_support = self._parse_price_value(key_levels.get('primary_support', 0))
                    secondary_support = self._parse_price_value(key_levels.get('secondary_support', 0))
                    if primary_support or secondary_support:
                        message += f"  📉 지지선:\n"
                        if primary_support:
                            message += f"    • 1차: {primary_support:,.0f}원\n"
                        if secondary_support:
                            message += f"    • 2차: {secondary_support:,.0f}원\n"
                    
                    # 거래량 기준
                    volume_baseline = key_levels.get('volume_baseline', '')
                    if volume_baseline:
                        message += f"  📊 거래량 기준: {volume_baseline}\n"
                    
                    message += "\n"
                
                # 2. 매도 시그널
                sell_triggers = trading_scenarios.get('sell_triggers', [])
                if sell_triggers:
                    message += "🔔 매도 시그널:\n"
                    for i, trigger in enumerate(sell_triggers, 1):
                        # 조건별로 이모지 선택
                        if "익절" in trigger or "목표" in trigger or "저항" in trigger:
                            emoji = "✅"
                        elif "손절" in trigger or "지지" in trigger or "하락" in trigger:
                            emoji = "⛔"
                        elif "시간" in trigger or "횡보" in trigger:
                            emoji = "⏰"
                        else:
                            emoji = "•"
                        
                        message += f"  {emoji} {trigger}\n"
                    message += "\n"
                
                # 3. 보유 조건
                hold_conditions = trading_scenarios.get('hold_conditions', [])
                if hold_conditions:
                    message += "✋ 보유 지속 조건:\n"
                    for condition in hold_conditions:
                        message += f"  • {condition}\n"
                    message += "\n"
                
                # 4. 포트폴리오 맥락
                portfolio_context = trading_scenarios.get('portfolio_context', '')
                if portfolio_context:
                    message += f"💼 포트폴리오 관점:\n  {portfolio_context}\n"

            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) purchase complete")

            return True

        except Exception as e:
            logger.error(f"{ticker} Error during purchase processing: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _analyze_sell_decision(self, stock_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Sell decision analysis

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
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Days elapsed from buy date
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            days_passed = (datetime.now() - buy_datetime).days

            # Extract scenario information
            scenario_str = stock_data.get('scenario', '{}')
            investment_period = "중기"  # Default value

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', '중기')
            except:
                pass

            # Check stop-loss condition
            if stop_loss > 0 and current_price <= stop_loss:
                return True, f"손절매 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # Check target price reached
            if target_price > 0 and current_price >= target_price:
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # Sell conditions by investment period
            if investment_period == "단기":
                # Short-term investment: quicker sell (15+ days holding + 5%+ profit)
                if days_passed >= 15 and profit_rate >= 5:
                    return True, f"단기 투자 목표 달성 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # Short-term investment loss protection (10+ days + 3%+ loss)
                if days_passed >= 10 and profit_rate <= -3:
                    return True, f"단기 투자 손실 방어 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Existing sell conditions
            # Sell if profit >= 10%
            if profit_rate >= 10:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if loss >= 5%
            if profit_rate <= -5:
                return True, f"손실 -5% 이상 발생 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if holding 30+ days with loss
            if days_passed >= 30 and profit_rate < 0:
                return True, f"30일 이상 보유 중이며 손실 상태 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Sell if holding 60+ days with 3%+ profit
            if days_passed >= 60 and profit_rate >= 3:
                return True, f"60일 이상 보유 중이며 3% 이상 수익 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Long-term investment case (90+ days holding + loss)
            if investment_period == "장기" and days_passed >= 90 and profit_rate < 0:
                return True, f"장기 투자 손실 정리 (보유일: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Continue holding by default
            return False, "계속 보유"

        except Exception as e:
            logger.error(f"{stock_data.get('ticker', '') if 'ticker' in locals() else 'Unknown stock'} Error analyzing sell: {str(e)}")
            return False, "분석 오류"

    async def sell_stock(self, stock_data: Dict[str, Any], sell_reason: str) -> bool:
        """
        Stock sell processing

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
            trigger_type = stock_data.get('trigger_type', 'AI분석')
            trigger_mode = stock_data.get('trigger_mode', 'unknown')

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Calculate holding period (days)
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            now_datetime = datetime.now()
            holding_days = (now_datetime - buy_datetime).days

            # Current time
            now = now_datetime.strftime("%Y-%m-%d %H:%M:%S")

            # Add to trading history table
            self.cursor.execute(
                """
                INSERT INTO trading_history
                (ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, scenario, trigger_type, trigger_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    buy_price,
                    buy_date,
                    current_price,
                    now,
                    profit_rate,
                    holding_days,
                    scenario_json,
                    trigger_type,
                    trigger_mode
                )
            )

            # Remove from holdings
            self.cursor.execute(
                "DELETE FROM stock_holdings WHERE ticker = ?",
                (ticker,)
            )

            # Save changes
            self.conn.commit()

            # Add sell message
            arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"
            message = f"📉 매도: {company_name}({ticker})\n" \
                      f"매수가: {buy_price:,.0f}원\n" \
                      f"매도가: {current_price:,.0f} \n" \
                      f"수익률: {arrow} {abs(profit_rate):.2f}%\n" \
                      f"보유기간: {holding_days}일\n" \
                      f"매도이유: {sell_reason}"

            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) sell complete (return: {profit_rate:.2f}%)")

            # Create trading journal entry for retrospective analysis
            try:
                await self._create_journal_entry(
                    stock_data=stock_data,
                    sell_price=current_price,
                    profit_rate=profit_rate,
                    holding_days=holding_days,
                    sell_reason=sell_reason
                )
            except Exception as journal_err:
                # Journal creation failure should not block the sell process
                logger.warning(f"Journal entry creation failed (non-critical): {journal_err}")

            return True

        except Exception as e:
            logger.error(f"Error during sell: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _create_journal_entry(
        self,
        stock_data: Dict[str, Any],
        sell_price: float,
        profit_rate: float,
        holding_days: int,
        sell_reason: str
    ) -> bool:
        """Create trading journal entry (delegates to tracking.journal.JournalManager)"""
        return await self.journal_manager.create_entry(
            stock_data, sell_price, profit_rate, holding_days, sell_reason
        )

    def _extract_principles_from_lessons(
        self, lessons: List[Dict[str, Any]], source_journal_id: int
    ) -> int:
        """Extract principles from lessons (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.extract_principles(lessons, source_journal_id)

    def _parse_journal_response(self, response: str) -> Dict[str, Any]:
        """Parse journal response (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager._parse_response(response)

    def _get_relevant_journal_context(
        self, ticker: str, sector: str = None, market_condition: str = None
    ) -> str:
        """Get journal context for buy decisions (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_context_for_ticker(ticker, sector)

    def _get_universal_principles(self, limit: int = 10) -> List[str]:
        """Get universal principles (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_universal_principles(limit)

    def _get_score_adjustment_from_context(
        self, ticker: str, sector: str = None
    ) -> Tuple[int, List[str]]:
        """Calculate score adjustment (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_score_adjustment(ticker, sector)

    async def compress_old_journal_entries(
        self,
        layer1_age_days: int = 7,
        layer2_age_days: int = 30,
        min_entries_for_compression: int = 3
    ) -> Dict[str, Any]:
        """Compress old journal entries (delegates to tracking.compression.CompressionManager)"""
        return await self.compression_manager.compress_old_entries(
            layer1_age_days, layer2_age_days, min_entries_for_compression
        )

    def get_compression_stats(self) -> Dict[str, Any]:
        """Get compression statistics (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager.get_stats()

    def cleanup_stale_data(
        self,
        max_principles: int = 50,
        max_intuitions: int = 50,
        min_confidence_threshold: float = 0.3,
        stale_days: int = 90,
        archive_layer3_days: int = 365,
        dry_run: bool = False
    ) -> Dict[str, Any]:
        """Clean up stale data (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager.cleanup_stale_data(
            max_principles, max_intuitions, min_confidence_threshold,
            stale_days, archive_layer3_days, dry_run
        )

    # === Backward compatibility wrappers for tests ===
    def _save_intuition(self, intuition: Dict[str, Any], source_ids: List[int]) -> bool:
        """Save intuition (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._save_intuition(intuition, source_ids)

    def _generate_simple_summary(self, entry: Dict[str, Any]) -> str:
        """Generate simple summary (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._generate_simple_summary(entry)

    def _format_entries_for_compression(self, entries: List[Dict[str, Any]]) -> str:
        """Format entries for compression (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._format_entries_for_compression(entries)

    def _parse_compression_response(self, response: str) -> Dict[str, Any]:
        """Parse compression response (delegates to tracking.compression.CompressionManager)"""
        return self.compression_manager._parse_response(response)

    def _save_principle(
        self, scope: str, scope_context: Optional[str], condition: str,
        action: str, reason: str, priority: str, source_journal_id: int
    ) -> bool:
        """Save principle (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager._save_principle(
            scope, scope_context, condition, action, reason, priority, source_journal_id
        )

    async def update_holdings(self) -> List[Dict[str, Any]]:
        """
        Update holdings information and make sell decisions

        Returns:
            List[Dict]: List of sold stock information
        """
        try:
            logger.info("Starting holdings info update")

            # Query holdings list
            self.cursor.execute(
                """SELECT ticker, company_name, buy_price, buy_date, current_price,
                   scenario, target_price, stop_loss, last_updated,
                   trigger_type, trigger_mode
                   FROM stock_holdings"""
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            if not holdings or len(holdings) == 0:
                logger.info("No holdings")
                return []

            sold_stocks = []

            for stock in holdings:
                ticker = stock.get('ticker')
                company_name = stock.get('company_name')

                # Query current stock price
                current_price = await self._get_current_stock_price(ticker)

                if current_price <= 0:
                    old_price = stock.get('current_price', 0)
                    logger.warning(f"{ticker} Current price query failed, keeping previous price: {old_price}")
                    current_price = old_price

                # Update stock price information
                stock['current_price'] = current_price

                # Check scenario JSON string
                scenario_str = stock.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_json = json.loads(scenario_str)

                        # Check and update target price/stop-loss
                        if 'target_price' in scenario_json and stock.get('target_price', 0) == 0:
                            stock['target_price'] = scenario_json['target_price']

                        if 'stop_loss' in scenario_json and stock.get('stop_loss', 0) == 0:
                            stock['stop_loss'] = scenario_json['stop_loss']
                except:
                    logger.warning(f"{ticker} Scenario JSON parse failed")

                # Current time
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # Analyze sell decision
                should_sell, sell_reason = await self._analyze_sell_decision(stock)

                if should_sell:
                    # Process sell
                    sell_success = await self.sell_stock(stock, sell_reason)

                    if sell_success:
                        # Call actual account trading function (async)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext() as trading:
                            # Execute async sell
                            trade_result = await trading.async_sell_stock(stock_code=ticker)

                        if trade_result['success']:
                            logger.info(f"Actual sell successful: {trade_result['message']}")
                        else:
                            logger.error(f"Actual sell failed: {trade_result['message']}")

                        # [Optional] Publish sell signal via Redis Streams
                        # Auto-skipped if Redis not configured (requires UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)
                        try:
                            from messaging.redis_signal_publisher import publish_sell_signal
                            await publish_sell_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                buy_price=stock.get('buy_price', 0),
                                profit_rate=((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 0) * 100),
                                sell_reason=sell_reason,
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"Sell signal publish failed (non-critical): {signal_err}")

                        # [Optional] Publish sell signal via GCP Pub/Sub
                        # Auto-skipped if GCP not configured (requires GCP_PROJECT_ID, GCP_PUBSUB_TOPIC_ID)
                        try:
                            from messaging.gcp_pubsub_signal_publisher import publish_sell_signal as gcp_publish_sell_signal
                            await gcp_publish_sell_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                buy_price=stock.get('buy_price', 0),
                                profit_rate=((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 0) * 100),
                                sell_reason=sell_reason,
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"GCP sell signal publish failed (non-critical): {signal_err}")

                    if sell_success:
                        sold_stocks.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "buy_price": stock.get('buy_price', 0),
                            "sell_price": current_price,
                            "profit_rate": ((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 0) * 100),
                            "reason": sell_reason
                        })
                else:
                    # Update current price
                    self.cursor.execute(
                        """UPDATE stock_holdings
                           SET current_price = ?, last_updated = ?
                           WHERE ticker = ?""",
                        (current_price, now, ticker)
                    )
                    self.conn.commit()
                    logger.info(f"{ticker}({company_name}) current price updated: {current_price:,.0f} KRW ({sell_reason})")

            return sold_stocks

        except Exception as e:
            logger.error(f"Error updating holdings: {str(e)}")
            logger.error(traceback.format_exc())
            return []

    async def generate_report_summary(self) -> str:
        """
        Generate holdings and profit statistics summary

        Returns:
            str: Summary message
        """
        try:
            # Query holdings
            self.cursor.execute(
                "SELECT ticker, company_name, buy_price, current_price, buy_date, scenario, target_price, stop_loss FROM stock_holdings"
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Calculate total profit from trading history
            self.cursor.execute("SELECT SUM(profit_rate) FROM trading_history")
            total_profit = self.cursor.fetchone()[0] or 0

            # Number of trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history")
            total_trades = self.cursor.fetchone()[0] or 0

            # Number of successful/failed trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history WHERE profit_rate > 0")
            successful_trades = self.cursor.fetchone()[0] or 0

            # Generate message
            message = f"📊 프리즘 시뮬레이터 | 실시간 포트폴리오 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

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
                    scenario_str = stock.get('scenario', '{}')
                    target_price = stock.get('target_price', 0)
                    stop_loss = stock.get('stop_loss', 0)

                    # Extract sector information from scenario
                    sector = "알 수 없음"
                    try:
                        if isinstance(scenario_str, str):
                            scenario_data = json.loads(scenario_str)
                            sector = scenario_data.get('sector', '알 수 없음')
                    except:
                        pass

                    # Update sector count
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1

                    profit_rate = ((current_price - buy_price) / buy_price) * 100 if buy_price else 0
                    arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"

                    buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S") if buy_date else datetime.now()
                    days_passed = (datetime.now() - buy_datetime).days

                    message += f"- {company_name}({ticker}) [{sector}]\n"
                    message += f"  매수가: {buy_price:,.0f}원 / 현재가: {current_price:,.0f} 원\n"
                    message += f"  목표가: {target_price:,.0f}원 / 손절가: {stop_loss:,.0f}원\n"
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
            error_msg = f"보고서 생성 중 오류가 발생했습니다: {str(e)}"
            return error_msg

    async def process_reports(self, pdf_report_paths: List[str]) -> Tuple[int, int]:
        """
        Process analysis reports and make buy/sell decisions

        Args:
            pdf_report_paths: List of pdf analysis report file paths

        Returns:
            Tuple[int, int]: Buy count, sell count
        """
        try:
            logger.info(f"Starting processing of {len(pdf_report_paths)} reports")

            # Buy/sell counters
            buy_count = 0
            sell_count = 0

            # 1. Update existing holdings and make sell decisions
            sold_stocks = await self.update_holdings()
            sell_count = len(sold_stocks)

            if sold_stocks:
                logger.info(f"{len(sold_stocks)} stocks sold")
                for stock in sold_stocks:
                    logger.info(f"Sold: {stock['company_name']}({stock['ticker']}) - Return: {stock['profit_rate']:.2f}% / Reason: {stock['reason']}")
            else:
                logger.info("No stocks sold")

            # 2. Analyze new reports and make buy decisions
            for pdf_report_path in pdf_report_paths:
                # Analyze report
                analysis_result = await self.analyze_report(pdf_report_path)

                if not analysis_result.get("success", False):
                    logger.error(f"Report analysis failed: {pdf_report_path} - {analysis_result.get('error', 'Unknown error')}")
                    continue

                # Skip if already holding this stock
                if analysis_result.get("decision") == "보유 중":
                    logger.info(f"Skipping stock in holdings: {analysis_result.get('ticker')} - {analysis_result.get('company_name')}")
                    continue

                # Stock information and scenario
                ticker = analysis_result.get("ticker")
                company_name = analysis_result.get("company_name")
                current_price = analysis_result.get("current_price", 0)
                scenario = analysis_result.get("scenario", {})
                sector = analysis_result.get("sector", "알 수 없음")
                sector_diverse = analysis_result.get("sector_diverse", True)
                rank_change_msg = analysis_result.get("rank_change_msg", "")
                rank_change_percentage = analysis_result.get("rank_change_percentage", 0)

                # Skip if sector diversity check fails
                if not sector_diverse:
                    logger.info(f"Purchase deferred: {company_name}({ticker}) - Preventing sector over-investment '.*'")
                    continue

                # Process buy if entry decision
                buy_score = scenario.get("buy_score", 0)
                min_score = scenario.get("min_score", 0)
                logger.info(f"Buy score check: {company_name}({ticker}) - Score: {buy_score}")
                if analysis_result.get("decision") == "진입":
                    # Process buy
                    buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                    if buy_success:
                        # Call actual account trading function (async)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext() as trading:
                            # Execute async buy
                            trade_result = await trading.async_buy_stock(stock_code=ticker)

                        if trade_result['success']:
                            logger.info(f"Actual purchase successful: {trade_result['message']}")
                        else:
                            logger.error(f"Actual purchase failed: {trade_result['message']}")

                        # [Optional] Publish buy signal via Redis Streams
                        # Auto-skipped if Redis not configured (requires UPSTASH_REDIS_REST_URL, UPSTASH_REDIS_REST_TOKEN)
                        try:
                            from messaging.redis_signal_publisher import publish_buy_signal
                            await publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI분석",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"Buy signal publish failed (non-critical): {signal_err}")

                        # [Optional] Publish buy signal via GCP Pub/Sub
                        # Auto-skipped if GCP not configured (requires GCP_PROJECT_ID, GCP_PUBSUB_TOPIC_ID)
                        try:
                            from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal
                            await gcp_publish_buy_signal(
                                ticker=ticker,
                                company_name=company_name,
                                price=current_price,
                                scenario=scenario,
                                source="AI분석",
                                trade_result=trade_result
                            )
                        except Exception as signal_err:
                            logger.warning(f"GCP buy signal publish failed (non-critical): {signal_err}")

                    if buy_success:
                        buy_count += 1
                        logger.info(f"Purchase complete: {company_name}({ticker}) @ {current_price:,.0f} KRW")
                    else:
                        logger.warning(f"Purchase failed: {company_name}({ticker})")
                else:
                    reason = ""
                    if buy_score < min_score:
                        reason = f"매수 점수 부족 ({buy_score} < {min_score})"
                    elif analysis_result.get("decision") != "진입":
                        reason = f"진입 결정 아님 (결정: {analysis_result.get('decision')})"

                    logger.info(f"Purchase deferred: {company_name}({ticker}) - {reason}")

            logger.info(f"Report processing complete - Purchased: {buy_count}items, Sold: {sell_count} items")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"Error processing reports: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def send_telegram_message(self, chat_id: str, language: str = "ko") -> bool:
        """
        Send message via Telegram

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
                logger.info(f"Translating {len(self.message_queue)} messages to English")
                try:
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    translated_queue = []
                    for idx, message in enumerate(self.message_queue, 1):
                        logger.info(f"Translating message {idx}/{len(self.message_queue)}")
                        translated = await translate_telegram_message(message, model="gpt-5-nano")
                        translated_queue.append(translated)
                    self.message_queue = translated_queue
                    logger.info("All messages translated successfully")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean messages.")

            # 각 메시지 전송
            success = True
            for message in self.message_queue:
                logger.info(f"Sending Telegram message: {chat_id}")
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

                    logger.info(f"Telegram message sent: {chat_id}")
                except TelegramError as e:
                    logger.error(f"Telegram message send failed: {e}")
                    success = False

                # API 제한 방지를 위한 지연
                await asyncio.sleep(1)

            # Send to broadcast channels if configured (wait for completion)
            if hasattr(self, 'telegram_config') and self.telegram_config and self.telegram_config.broadcast_languages:
                # Create task and wait for it to complete
                translation_task = asyncio.create_task(self._send_to_translation_channels(self.message_queue.copy()))
                await translation_task
                logger.info("Broadcast channel messages sent successfully")

            # 메시지 큐 초기화
            self.message_queue = []

            return success

        except Exception as e:
            logger.error(f"Error sending Telegram message: {str(e)}")
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

                    logger.info(f"Sending tracking messages to {lang} channel")

                    # Translate and send each message
                    for message in messages:
                        try:
                            # Translate message
                            logger.info(f"Translating tracking message to {lang}")
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

                                # Send split messages
                                for i, part in enumerate(parts, 1):
                                    await self.telegram_bot.send_message(
                                        chat_id=channel_id,
                                        text=f"[{i}/{len(parts)}]\n{part}"
                                    )
                                    await asyncio.sleep(0.5)

                            logger.info(f"Tracking message sent successfully to {lang} channel")
                            await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"Error sending tracking message to {lang}: {str(e)}")

                except Exception as e:
                    logger.error(f"Error processing language {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_to_translation_channels: {str(e)}")

    async def run(self, pdf_report_paths: List[str], chat_id: str = None, language: str = "ko", telegram_config=None, trigger_results_file: str = None) -> bool | None:
        """
        Main execution function for stock tracking system

        Args:
            pdf_report_paths: List of analysis report file paths
            chat_id: Telegram channel ID (no messages sent if None)
            language: Message language ("ko" or "en")
            telegram_config: TelegramConfig object for multi-language support
            trigger_results_file: Path to trigger results JSON file for tracking trigger types

        Returns:
            bool: Execution success status
        """
        try:
            logger.info("Starting tracking system batch execution")

            # Store telegram_config for use in send_telegram_message
            self.telegram_config = telegram_config

            # Load trigger type mapping from trigger_results file
            self.trigger_info_map = {}
            if trigger_results_file:
                try:
                    import os
                    if os.path.exists(trigger_results_file):
                        with open(trigger_results_file, 'r', encoding='utf-8') as f:
                            trigger_data = json.load(f)
                        # Build ticker -> trigger info mapping
                        for trigger_type, stocks in trigger_data.items():
                            if trigger_type == 'metadata':
                                self.trigger_mode = trigger_data.get('metadata', {}).get('trigger_mode', '')
                                continue
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    ticker = stock.get('code', '')
                                    if ticker:
                                        self.trigger_info_map[ticker] = {
                                            'trigger_type': trigger_type,
                                            'trigger_mode': trigger_data.get('metadata', {}).get('trigger_mode', ''),
                                            'risk_reward_ratio': stock.get('risk_reward_ratio', 0)
                                        }
                        logger.info(f"Loaded trigger info for {len(self.trigger_info_map)} stocks")
                except Exception as e:
                    logger.warning(f"Failed to load trigger results file: {e}")

            # Initialize with language parameter
            await self.initialize(language)

            try:
                # Process reports
                buy_count, sell_count = await self.process_reports(pdf_report_paths)

                # Send Telegram message (only if chat_id is provided)
                if chat_id:
                    message_sent = await self.send_telegram_message(chat_id, language)
                    if message_sent:
                        logger.info("Telegram message sent successfully")
                    else:
                        logger.warning("Telegram message send failed")
                else:
                    logger.info("Telegram channel ID not provided, skipping message send")
                    # Call even if chat_id is None to clean up message queue
                    await self.send_telegram_message(None, language)

                logger.info("Tracking system batch execution complete")
                return True
            finally:
                # Move to finally block to ensure connection is always closed
                if self.conn:
                    self.conn.close()
                    logger.info("Database connection closed")

        except Exception as e:
            logger.error(f"Error during tracking system execution: {str(e)}")
            logger.error(traceback.format_exc())

            # Check and close database connection
            if hasattr(self, 'conn') and self.conn:
                try:
                    self.conn.close()
                    logger.info("Database connection closed after error")
                except:
                    pass

            return False

async def main():
    """Main function"""
    import argparse
    import logging

    # Get logger
    local_logger = logging.getLogger(__name__)

    parser = argparse.ArgumentParser(description="Stock tracking and trading agent")
    parser.add_argument("--reports", nargs="+", help="List of analysis report file paths")
    parser.add_argument("--chat-id", help="Telegram channel ID")
    parser.add_argument("--telegram-token", help="Telegram bot token")

    args = parser.parse_args()

    if not args.reports:
        local_logger.error("Report path not specified")
        return False

    async with app.run():
        agent = StockTrackingAgent(telegram_token=args.telegram_token)
        success = await agent.run(args.reports, args.chat_id)

        return success

if __name__ == "__main__":
    try:
        # Execute asyncio
        asyncio.run(main())
    except Exception as e:
        logger.error(f"Error during program execution: {str(e)}")
        logger.error(traceback.format_exc())
        sys.exit(1)
