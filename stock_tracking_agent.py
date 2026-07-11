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
load_dotenv()  # Load environment variables from .env file

import asyncio
import json
import logging
import os
import sqlite3
import sys
import traceback
from datetime import datetime
from typing import List, Dict, Any, Tuple, Optional

import cores.openai_debug  # noqa: F401 — OpenAI 400/429 request metadata logging
from telegram import Bot
from telegram.error import TelegramError, TimedOut, RetryAfter

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
from cores.llm.openai_responses_llm import OpenAIResponsesLLM as OpenAIAugmentedLLM

# Core agent imports
from cores.openai_error_logging import log_openai_error
from cores.agents.trading_agents import create_trading_scenario_agent
from cores.utils import parse_llm_json

# O'Neil 룰베이스 매도 (2026-06-04 US quota 사고 동일 룰 결함 KR에도 적용).
# 방어적 import: 실패 시 _ONEIL_FALLBACK_AVAILABLE=False 로 기존 레거시 룰 유지.
try:
    from cores.oneil_fallback import (
        evaluate_oneil_sell as _oneil_eval,
        from_stock_data as _oneil_from,
    )
    _ONEIL_FALLBACK_AVAILABLE = True
except Exception:  # pragma: no cover - defensive
    _ONEIL_FALLBACK_AVAILABLE = False

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
    get_existing_position_for_ticker,
    evaluate_pyramid_add_gate,
    pyramid_add_possible_ignoring_regime,
    compute_fractional_sell_quantity,
    JournalManager,
    CompressionManager,
    TelegramSender,
)
from trading import kis_auth as ka

# Create MCPApp instance
app = MCPApp(name="stock_tracking")

class StockTrackingAgent:
    """Stock Tracking and Trading Agent"""

    # Constants
    MAX_SLOTS = 10  # Maximum number of stocks to hold
    MAX_SAME_SECTOR = 3  # Maximum holdings in same sector
    SECTOR_CONCENTRATION_RATIO = 0.3  # Sector concentration limit ratio

    # Investment period constants
    PERIOD_SHORT = "short_term"  # Within 1 month
    PERIOD_MEDIUM = "medium_term"  # 1-3 months
    PERIOD_LONG = "long_term"  # 3+ months

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
        self._msg_types = []  # msg_type for each message in queue
        self._broadcast_task = None  # Track broadcast translation task
        self.trading_agent = None
        self.db_path = db_path
        self.conn = None
        self.cursor = None
        self.account_configs: list[dict[str, Any]] = []
        self.active_account: dict[str, Any] | None = None

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

    async def initialize(self, language: str = "ko", sector_names: list = None,
                         skip_llm_agent: bool = False):
        """
        Create necessary tables and initialize

        Args:
            language: Language code for agents (default: "ko")
            sector_names: List of valid sector names for trading agent (optional)
            skip_llm_agent: When True, skip creating the LLM trading-scenario agent.
                The sell path (sell_stock / send_telegram_message) does NOT use
                self.trading_agent, so lightweight consumers (e.g. the LLM-free
                Hardstop (구 Loop A) hard-stop loop) can reuse the sell/journal/telegram plumbing
                without pulling in the heavy LLM agent. Default False keeps the
                batch behaviour byte-for-byte unchanged.
        """
        logger.info("Starting tracking agent initialization")
        logger.info(f"Trading journal feature: {'enabled' if self.enable_journal else 'disabled'}")

        # Store language for later use
        self.language = language

        # Initialize SQLite connection
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row  # Return results as dictionary
        self.cursor = self.conn.cursor()

        # Initialize trading scenario generation agent with language and sector names.
        # Skipped for lightweight sell-only consumers (see skip_llm_agent docstring).
        self.trading_agent = None if skip_llm_agent else \
            create_trading_scenario_agent(language=language, sector_names=sector_names)

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
        self.account_configs = self._get_trading_accounts()
        if self.account_configs:
            self._set_active_account(self.account_configs[0])
        else:
            logger.warning("No trading accounts configured - skipping trade execution")

        logger.info("Tracking agent initialization complete")
        return True

    async def _create_tables(self):
        """Create necessary database tables (delegates to tracking.db_schema)"""
        create_all_tables(self.cursor, self.conn)
        add_scope_column_if_missing(self.cursor, self.conn)  # Must run before indexes
        add_trigger_columns_if_missing(self.cursor, self.conn)  # v1.16.5 migration
        add_sector_column_if_missing(self.cursor, self.conn)  # v1.17 migration for AI agent sector queries
        create_indexes(self.cursor, self.conn)

    def _get_trading_accounts(self) -> List[Dict[str, Any]]:
        default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
        svr = "vps" if default_mode == "demo" else "prod"
        return ka.get_configured_accounts(svr=svr, market="kr")

    def _set_active_account(self, account: Dict[str, Any]) -> None:
        self.active_account = account

    def _require_active_account(self) -> Dict[str, Any]:
        if not self.active_account:
            raise RuntimeError("No active KR trading account is set")
        return self.active_account

    def _account_scope(self) -> Tuple[str, str]:
        account = self._require_active_account()
        return account["account_key"], account["name"]

    @staticmethod
    def _safe_account_log_label(account: Dict[str, Any]) -> str:
        """Format account identity for logs without exposing raw account numbers."""
        account_name = account.get("name", "unknown")
        account_key = str(account.get("account_key", "") or "")
        if not account_key:
            return account_name

        parts = account_key.split(":")
        if len(parts) == 3:
            scope, account_number, product = parts
            return f"{account_name} ({scope}:{ka.mask_account_number(account_number)}:{product})"

        return f"{account_name} ({ka.mask_account_number(account_key)})"

    async def _extract_ticker_info(self, report_path: str) -> Tuple[str, str]:
        """Extract ticker code and company name (delegates to tracking.helpers)"""
        return extract_ticker_info(report_path)

    async def _get_current_stock_price(self, ticker: str) -> float:
        """Get current stock price (delegates to tracking.helpers)"""
        account_key, _ = self._account_scope()
        return await get_current_stock_price(self.cursor, ticker, account_key=account_key)

    async def _get_trading_value_rank_change(self, ticker: str) -> Tuple[float, str]:
        """Calculate trading value ranking change (delegates to tracking.helpers)"""
        return await get_trading_value_rank_change(ticker)

    async def _is_ticker_in_holdings(self, ticker: str) -> bool:
        """Check if stock is already in holdings (delegates to tracking.helpers)"""
        account_key, _ = self._account_scope()
        return is_ticker_in_holdings(self.cursor, ticker, account_key=account_key)

    async def _get_current_slots_count(self) -> int:
        """Get current number of holdings (delegates to tracking.helpers)"""
        account_key, _ = self._account_scope()
        return get_current_slots_count(self.cursor, account_key=account_key)

    async def _check_sector_diversity(self, sector: str) -> bool:
        """Check for over-concentration in same sector (delegates to tracking.helpers)"""
        account_key, _ = self._account_scope()
        return check_sector_diversity(
            self.cursor, sector,
            self.MAX_SAME_SECTOR, self.SECTOR_CONCENTRATION_RATIO, account_key=account_key
        )

    def _get_trend_facts(self, ticker: str) -> str:
        """Compute deterministic individual-stock trend facts for the buy prompt's trend gate.

        Fail-open: on ANY error returns "" and logs a warning; never raises into the buy path.
        Reuses the resilient cores.stock_chart wrappers (db-server backed on prod).
        """
        try:
            from datetime import timedelta
            from cores.stock_chart import (
                get_market_ohlcv_by_date,
                get_index_ohlcv_by_date,
                _detect_index_ticker,
            )

            end_dt = datetime.now()
            start_dt = end_dt - timedelta(days=120)
            end = end_dt.strftime("%Y%m%d")
            start = start_dt.strftime("%Y%m%d")

            df = get_market_ohlcv_by_date(start, end, ticker, adjusted=True)
            if df is None or len(df) < 25 or 'Close' not in df.columns:
                logger.warning(f"[TrendFacts] {ticker} insufficient OHLCV rows; skipping")
                return ""

            close_s = df['Close'].astype(float)
            close = float(close_s.iloc[-1])

            ma20_s = close_s.rolling(window=20).mean()
            ma50_s = close_s.rolling(window=50).mean()
            ma60_s = close_s.rolling(window=60).mean()

            def _val(s):
                try:
                    v = s.iloc[-1]
                    return float(v) if v == v else None  # NaN check
                except Exception:
                    return None

            def _slope_up(s, lookback=5):
                # rising if MA today > MA ~lookback trading days ago
                try:
                    if len(s) <= lookback:
                        return None
                    cur = s.iloc[-1]
                    prev = s.iloc[-1 - lookback]
                    if cur != cur or prev != prev:
                        return None
                    return bool(cur > prev)
                except Exception:
                    return None

            ma20 = _val(ma20_s)
            ma50 = _val(ma50_s)
            ma60 = _val(ma60_s)
            ma20_up = _slope_up(ma20_s)
            ma50_up = _slope_up(ma50_s)
            ma60_up = _slope_up(ma60_s)

            # 50일선 우선, 없으면 60일선 fallback (T1용)
            ma_mid = ma50 if ma50 is not None else ma60
            ma_mid_up = ma50_up if ma50 is not None else ma60_up
            ma_mid_label = "MA50" if ma50 is not None else "MA60"

            def _pct(a, b):
                if a is None or b is None or b == 0:
                    return None
                return (a - b) / b * 100.0

            # RS proxy: 60거래일 종목 수익률 - 지수 60거래일 수익률
            rs = None
            stock_ret = None
            idx_ret = None
            n = min(60, len(close_s) - 1)
            if n > 0:
                base = float(close_s.iloc[-1 - n])
                if base:
                    stock_ret = (close / base - 1.0) * 100.0
                try:
                    index_ticker = _detect_index_ticker(ticker)
                    idf = get_index_ohlcv_by_date(start, end, index_ticker)
                    if idf is not None and len(idf) > 1:
                        icol = "종가" if "종가" in idf.columns else (
                            "Close" if "Close" in idf.columns else None)
                        if icol:
                            iclose = idf[icol].astype(float)
                            m = min(n, len(iclose) - 1)
                            ibase = float(iclose.iloc[-1 - m])
                            if ibase:
                                idx_ret = (float(iclose.iloc[-1]) / ibase - 1.0) * 100.0
                except Exception as _ie:
                    logger.warning(f"[TrendFacts] {ticker} index return failed: {_ie}")
                if stock_ret is not None and idx_ret is not None:
                    rs = stock_ret - idx_ret

            # 게이트 판정 (deterministic)
            # T1: 종가가 50일선(오닐 10주선) 아래 = 핵심 라인 이탈. 기울기 무관 —
            #     라인 아래면 정당한 눌림이 아니라 추세 훼손으로 본다.
            t1_hit = bool(ma_mid is not None and close < ma_mid)
            # T2: 20일선 하향 + 종가가 20일선 대비 5% 이상 아래 = 급격한 초기 붕괴.
            #     (RS 60일은 직전 급등 잔상으로 stale → 게이트 조건에서 제외, 정보로만 표기)
            t2_hit = bool(ma20 is not None and ma20_up is not None
                          and ma20_up is False and close <= ma20 * 0.95)

            def _above(a, b):
                if a is None or b is None:
                    return "n/a"
                return "위" if a >= b else "아래"

            def _dir(up):
                return "n/a" if up is None else ("상승" if up else "하락")

            def _fmt(v, suffix=""):
                return f"{v:+.1f}{suffix}" if v is not None else "n/a"

            lines = [
                "### 📉 개별 추세 팩트 (추세 게이트용 · as-of 오늘)",
                f"- 종가: {close:,.0f}",
                f"- vs MA20: {_above(close, ma20)} ({_fmt(_pct(close, ma20), '%')}), MA20 기울기: {_dir(ma20_up)}",
                f"- vs MA50: {_above(close, ma50)} ({_fmt(_pct(close, ma50), '%')}), MA50 기울기: {_dir(ma50_up)}",
                f"- vs MA60: {_above(close, ma60)} ({_fmt(_pct(close, ma60), '%')}), MA60 기울기: {_dir(ma60_up)}",
                f"- RS(60일, 종목-지수): {_fmt(rs, '%p')} (종목 {_fmt(stock_ret, '%')} / 지수 {_fmt(idx_ret, '%')})",
                f"- T1_hit(종가<{ma_mid_label}, 오닐 10주선 이탈): {t1_hit} / "
                f"T2_hit(MA20 하락 and 종가 MA20 대비 -5%↓): {t2_hit}",
            ]
            # Market Pulse (O'Neil M) 상태를 프롬프트 정보로 주입 (distribution_days 동급).
            # 프로세스당 1회 계산(regime_policy 모듈 캐시); fail-open.
            try:
                from cores.regime_policy import get_market_pulse_state
                _mp_state = get_market_pulse_state("kr")
                if _mp_state:
                    lines.append(
                        f"- Market Pulse: {_mp_state} "
                        "(오닐 M 상태; CORRECTION=신중, 반등대박도 이 구간에서 나옴)"
                    )
            except Exception as _mpe:
                logger.warning(f"[TrendFacts] market pulse inject failed, fail-open: {_mpe}")
            trend_facts = "\n".join(lines)
            logger.info(f"[TrendFacts] {ticker} T1={t1_hit} T2={t2_hit}")
            return trend_facts
        except Exception as e:
            logger.warning(f"[TrendFacts] {ticker} failed, fail-open: {e}")
            return ""

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
            trigger_type: Trigger type that activated this analysis (e.g., 'Volume Surge Top Stocks')
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
                WHERE account_key = ?
            """, (self._account_scope()[0],))
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Analyze sector distribution
            sector_distribution = {}
            investment_periods = {"short_term": 0, "medium_term": 0, "long_term": 0}

            for holding in holdings:
                scenario_str = holding.get('scenario', '{}')
                try:
                    if isinstance(scenario_str, str):
                        scenario_data = json.loads(scenario_str)

                        # Collect sector info
                        sector_name = scenario_data.get('sector', 'Unknown')
                        sector_distribution[sector_name] = sector_distribution.get(sector_name, 0) + 1

                        # Collect investment period info
                        period = scenario_data.get('investment_period', 'medium_term')
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
            trend_facts = ""
            score_adjustment_info = ""
            adjustment, reasons = 0, []
            if ticker:
                # Deterministic individual-stock trend facts for the 1.5단계 trend gate (fail-open)
                trend_facts = self._get_trend_facts(ticker)
                journal_context = self._get_relevant_journal_context(
                    ticker=ticker,
                    sector=sector,
                    market_condition=None,
                    trigger_type=trigger_type
                )
                if journal_context:
                    logger.info(f"[Journal] Injected context for {ticker} ({len(journal_context)} chars)")
                    logger.debug(f"[Journal] Context preview: {journal_context[:500]}")
                elif self.enable_journal:
                    logger.warning(f"[Journal] Empty context for {ticker} despite journal being enabled")
                else:
                    logger.debug(f"[Journal] Journal disabled, no context for {ticker}")
                # Get score adjustment suggestion
                adjustment, reasons = self._get_score_adjustment_from_context(ticker, sector, trigger_type)
                if adjustment != 0 or reasons:
                    if self.language == "ko":
                        score_adjustment_info = f"""
                ### 📊 Score Adjustment Suggestion (Experience-Based)
                - Recommended Adjustment: {'+' if adjustment > 0 else ''}{adjustment} points
                - Reason: {', '.join(reasons) if reasons else 'N/A'}
                - ⚠️ This adjustment is a reference based on past experience.
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
                ### 📡 Trigger Info (Apply Trigger-Based Entry Criteria)
                - **Triggered By**: {trigger_type}
                - **Trigger Mode**: {trigger_mode or 'unknown'}
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
                This is an AI analysis report for a stock. Please generate a trading scenario based on this report.

                ### Current Portfolio Status:
                {portfolio_info}
                {trigger_info_section}
                ### Trading Value Analysis:
                {rank_change_msg}
                {score_adjustment_info}
                {trend_facts}
                {journal_context}

                ### Report Content:
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
                {trend_facts}
                {journal_context}

                ### Report Content:
                {report_content}
                """

            response = await llm.generate_str(
                message=prompt_message,
                request_params=RequestParams(
                    model="gpt-5.5",
                    maxTokens=30000
                )
            )

            # JSON parsing (consolidated in cores/utils.py)
            # TODO: Create model and call generate_structured function to improve code maintainability
            scenario_json = parse_llm_json(response, context='trading scenario')
            if scenario_json is not None:
                # Persist the experience-based score adjustment alongside the scenario.
                # It rides inside the scenario JSON, which is stored in
                # stock_holdings.scenario and copied to trading_history.scenario on sell —
                # giving the weekly influence report a journal-impact signal for free (#280).
                if adjustment != 0 or reasons:
                    scenario_json["score_adjustment"] = {"value": adjustment, "reasons": reasons}
                logger.info(f"Scenario parsed: {json.dumps(scenario_json, ensure_ascii=False)[:200]}")
                return scenario_json

            logger.error(f"Trading scenario parse failed. Full response: {response}")
            return self._default_scenario()

        except Exception as e:
            log_openai_error(logger, e, "KR trading scenario extraction")
            logger.error(f"Error extracting trading scenario: {str(e)}")
            logger.error(traceback.format_exc())
            return self._default_scenario()

    def _default_scenario(self) -> Dict[str, Any]:
        """Return default trading scenario (delegates to tracking.helpers)"""
        return default_scenario()

    async def _analyze_report_core(self, pdf_report_path: str) -> Dict[str, Any]:
        """Analyze a report once before per-account execution checks.

        Note:
            `_extract_trading_scenario()` includes the currently active account's
            portfolio state in the LLM context. In multi-account mode this means
            the primary account shapes the shared report analysis, while actual
            buy eligibility is still re-checked per account in `process_reports()`.
            This keeps LLM cost flat instead of multiplying per account.
        """
        try:
            logger.info(f"Starting report analysis: {pdf_report_path}")

            ticker, company_name = await self._extract_ticker_info(pdf_report_path)
            if not ticker or not company_name:
                logger.error(f"Failed to extract ticker info: {pdf_report_path}")
                return {"success": False, "error": "Failed to extract ticker info"}

            current_price = await self._get_current_stock_price(ticker)
            if current_price <= 0:
                logger.error(f"{ticker} current price query failed")
                return {"success": False, "error": "Current price query failed"}

            rank_change_percentage, rank_change_msg = await self._get_trading_value_rank_change(ticker)

            from pdf_converter import pdf_to_markdown_text

            report_content = pdf_to_markdown_text(pdf_report_path)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', '')
            trigger_mode = trigger_info.get('trigger_mode', '')

            scenario = await self._extract_trading_scenario(
                report_content,
                rank_change_msg,
                ticker=ticker,
                sector=None,
                trigger_type=trigger_type,
                trigger_mode=trigger_mode
            )

            raw_decision = scenario.get("decision", "No entry")
            sector = scenario.get("sector", "Unknown")

            return {
                "success": True,
                "ticker": ticker,
                "company_name": company_name,
                "current_price": current_price,
                "scenario": scenario,
                "decision": self._normalize_decision(raw_decision),
                "raw_decision": raw_decision,
                "sector": sector,
                "rank_change_percentage": rank_change_percentage,
                "rank_change_msg": rank_change_msg,
            }

        except Exception as e:
            logger.error(f"Error analyzing report: {str(e)}")
            logger.error(traceback.format_exc())
            return {"success": False, "error": str(e)}

    async def analyze_report(self, pdf_report_path: str) -> Dict[str, Any]:
        """
        Analyze stock analysis report and make trading decision

        Args:
            pdf_report_path: PDF analysis report file path

        Returns:
            Dict: Trading decision result
        """
        # Cheap pre-gate: skip the per-stock scenario LLM for a held stock that
        # cannot pyramid-add (#288) regardless of regime. The full add-gate requires
        # (row_count < max) AND (profit >= min) — both regime-independent and computed
        # from DB + price only. When they fail, the full path returns "Already holding"
        # anyway, so we return that here without paying for the ~2.5min scenario LLM.
        # Winners with room fall through to full analysis (the LLM supplies
        # market_condition for the regime check), so pyramiding is fully preserved.
        # Fail-open: any error in the cheap check falls back to full analysis.
        try:
            pre_ticker, pre_company = await self._extract_ticker_info(pdf_report_path)
        except Exception:
            pre_ticker, pre_company = None, None
        if pre_ticker and await self._is_ticker_in_holdings(pre_ticker):
            try:
                account_key, _ = self._account_scope()
                existing = get_existing_position_for_ticker(self.cursor, pre_ticker, account_key=account_key)
                pre_price = await self._get_current_stock_price(pre_ticker)
                can_add, why = pyramid_add_possible_ignoring_regime(
                    existing_avg_buy_price=existing.get("avg_buy_price", 0.0),
                    current_price=pre_price,
                    existing_row_count=existing.get("row_count", 0),
                )
            except Exception as e:
                logger.warning(
                    f"{pre_ticker} pyramid pre-gate check failed ({e}); running full analysis"
                )
                can_add = True  # fail-open: never skip analysis on uncertainty
            if not can_add:
                logger.info(
                    f"{pre_ticker}({pre_company}) already in holdings — pyramid pre-gate "
                    f"blocked ({why}); skipping scenario LLM"
                )
                return {
                    "success": True,
                    "decision": "Already holding",
                    "ticker": pre_ticker,
                    "company_name": pre_company,
                    "current_price": pre_price,
                }

        analysis_result = await self._analyze_report_core(pdf_report_path)
        if not analysis_result.get("success", False):
            return analysis_result

        ticker = analysis_result.get("ticker")
        company_name = analysis_result.get("company_name")

        is_holding = await self._is_ticker_in_holdings(ticker)
        if is_holding:
            # Pyramiding (#288): allow an additional independent entry only when the
            # strong-bull add-gate passes. Otherwise keep the legacy hard block.
            scenario = analysis_result.get("scenario", {}) or {}
            current_price = analysis_result.get("current_price", 0)
            account_key, _ = self._account_scope()
            existing = get_existing_position_for_ticker(self.cursor, ticker, account_key=account_key)
            allowed, reason = evaluate_pyramid_add_gate(
                market_condition=scenario.get("market_condition", ""),
                existing_avg_buy_price=existing.get("avg_buy_price", 0.0),
                current_price=current_price,
                existing_row_count=existing.get("row_count", 0),
            )
            if not allowed:
                logger.info(f"{ticker}({company_name}) already in holdings — add gate blocked: {reason}")
                return {
                    "success": True,
                    "decision": "Already holding",
                    "ticker": ticker,
                    "company_name": company_name,
                    "current_price": current_price,
                }

            logger.info(f"{ticker}({company_name}) pyramiding add gate passed: {reason}")
            # Tag as an add and pass through to the normal buy path (which still
            # independently gates on Enter/score/sector_diverse).
            analysis_result["is_add"] = True
            analysis_result["existing_avg_buy_price"] = existing.get("avg_buy_price", 0.0)
            analysis_result["existing_row_count"] = existing.get("row_count", 0)

        sector = analysis_result.get("sector", "Unknown")
        analysis_result["sector_diverse"] = await self._check_sector_diversity(sector)
        return analysis_result

    @staticmethod
    def _normalize_decision(decision: str) -> str:
        """Normalize AI decision string to canonical English form.

        LLM may return decision in Korean or various English forms.
        Maps all variants to a consistent set: 'Enter', 'Watch', 'Skip'.
        """
        if not decision:
            return "Skip"
        normalized = decision.strip()
        enter_variants = {"진입", "Entry", "enter", "entry", "Enter", "매수", "Buy", "buy"}
        watch_variants = {"관망", "Watch", "watch", "Hold", "hold", "보류"}
        skip_variants = {"미진입", "Skip", "skip", "No entry", "no entry", "패스", "Pass", "pass"}
        if normalized in enter_variants:
            return "Enter"
        if normalized in watch_variants:
            return "Watch"
        if normalized in skip_variants:
            return "Skip"
        return normalized

    def _parse_price_value(self, value: Any) -> float:
        """Parse price value and convert to number (delegates to tracking.helpers)"""
        return parse_price_value(value)

    def _get_trigger_win_rate(self, trigger_type: str) -> str:
        """Get trigger win rate string from analysis_performance_tracker.
        Returns a formatted string like '(이 트리거 과거 승률: 63%)' or empty string if no data."""
        if not trigger_type or not self.conn:
            return ""
        try:
            cursor = self.conn.cursor()
            row = cursor.execute("""
                SELECT COUNT(*) as completed,
                       SUM(CASE WHEN tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins
                FROM analysis_performance_tracker
                WHERE trigger_type = ? AND tracking_status = 'completed'
            """, (trigger_type,)).fetchone()
            if row and row[0] >= 3:
                win_rate = int(row[1] / row[0] * 100)
                return f"📡 이 트리거 과거 승률: {win_rate}% ({row[0]}건)"
            return ""
        except Exception:
            return ""

    async def _save_watchlist_item(
        self,
        ticker: str,
        company_name: str,
        current_price: float,
        buy_score: int,
        min_score: int,
        decision: str,
        skip_reason: str,
        scenario: Dict[str, Any],
        sector: str,
        was_traded: bool = False,
    ) -> bool:
        """Save deferred KR analyses for watchlist and performance tracking."""
        try:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            target_price = scenario.get("target_price", 0)
            stop_loss = scenario.get("stop_loss", 0)
            investment_period = scenario.get("investment_period", self.PERIOD_SHORT)
            portfolio_analysis = scenario.get("portfolio_analysis", "")
            valuation_analysis = scenario.get("valuation_analysis", "")
            sector_outlook = scenario.get("sector_outlook", "")
            market_condition = scenario.get("market_condition", "")
            rationale = scenario.get("rationale", "")

            trigger_info = getattr(self, "trigger_info_map", {}).get(ticker, {})
            trigger_type = trigger_info.get("trigger_type", "")
            trigger_mode = trigger_info.get("trigger_mode", "")
            risk_reward_ratio = trigger_info.get(
                "risk_reward_ratio",
                scenario.get("risk_reward_ratio", 0),
            )

            self.cursor.execute(
                """
                INSERT INTO watchlist_history
                (ticker, company_name, current_price, analyzed_date, buy_score, min_score,
                 decision, skip_reason, target_price, stop_loss, investment_period, sector,
                 scenario, portfolio_analysis, valuation_analysis, sector_outlook,
                 market_condition, rationale, trigger_type, trigger_mode, risk_reward_ratio, was_traded)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticker,
                    company_name,
                    current_price,
                    now,
                    buy_score,
                    min_score,
                    decision,
                    skip_reason,
                    target_price,
                    stop_loss,
                    investment_period,
                    sector,
                    json.dumps(scenario, ensure_ascii=False),
                    portfolio_analysis,
                    valuation_analysis,
                    sector_outlook,
                    market_condition,
                    rationale,
                    trigger_type,
                    trigger_mode,
                    risk_reward_ratio,
                    1 if was_traded else 0,
                ),
            )
            watchlist_id = self.cursor.lastrowid

            self.cursor.execute(
                """
                INSERT INTO analysis_performance_tracker
                (watchlist_id, ticker, company_name, trigger_type, trigger_mode,
                 analyzed_date, analyzed_price, decision, was_traded, skip_reason,
                 buy_score, min_score, target_price, stop_loss, risk_reward_ratio,
                 tracking_status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)
                """,
                (
                    watchlist_id,
                    ticker,
                    company_name,
                    trigger_type,
                    trigger_mode,
                    now,
                    current_price,
                    decision,
                    1 if was_traded else 0,
                    skip_reason,
                    buy_score,
                    min_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    now,
                    now,
                ),
            )
            self.conn.commit()
            logger.info(
                f"{ticker}({company_name}) watchlist save complete - "
                f"Score: {buy_score}/{min_score}, Reason: {skip_reason}, Trigger: {trigger_type}"
            )
            return True
        except Exception as e:
            logger.error(f"{ticker} Error saving watchlist: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def buy_stock(self, ticker: str, company_name: str, current_price: float, scenario: Dict[str, Any], rank_change_msg: str = "", is_add: bool = False) -> bool:
        """
        Process stock purchase

        Args:
            ticker: Stock code
            company_name: Company name
            current_price: Current stock price
            scenario: Trading scenario information
            rank_change_msg: Trading value ranking change info
            is_add: Pyramiding add (#288) — bypass the already-holding re-check and
                    insert an independent additional row instead of a first entry.

        Returns:
            bool: Purchase success status
        """
        try:
            # Check if already holding (skipped for a pyramiding add)
            if not is_add and await self._is_ticker_in_holdings(ticker):
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
            account_key, account_name = self._account_scope()

            # Get trigger info from trigger_info_map (loaded from trigger_results file)
            trigger_info = getattr(self, 'trigger_info_map', {}).get(ticker, {})
            trigger_type = trigger_info.get('trigger_type', 'AI Analysis')
            trigger_mode = trigger_info.get('trigger_mode', getattr(self, 'trigger_mode', 'unknown'))

            # Add to holdings table
            self.cursor.execute(
                """
                INSERT INTO stock_holdings
                (account_key, account_name, ticker, company_name, buy_price, buy_date, current_price, last_updated, scenario, target_price, stop_loss, trigger_type, trigger_mode, sector)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_key,
                    account_name,
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
                    scenario.get('sector', '알 수 없음'),
                )
            )
            self.conn.commit()

            # Add purchase message — pyramiding adds (#288) get a distinct header
            # showing the entry number and the new aggregate average price.
            if is_add:
                agg = get_existing_position_for_ticker(self.cursor, ticker, account_key=account_key)
                entry_no = agg.get("row_count", 1)  # this entry is the Nth row
                new_avg = agg.get("avg_buy_price", current_price)
                message = f"📈 추가 진입 ({entry_no}차): {company_name}({ticker})\n" \
                          f"이번 진입가: {current_price:,.0f}원\n" \
                          f"누적 평단가: {new_avg:,.0f}원\n" \
                          f"⚠️ 포트폴리오 비중이 증가했습니다 (독립 슬롯 1 소비)\n" \
                          f"목표가: {scenario.get('target_price', 0):,.0f}원\n" \
                          f"손절가: {scenario.get('stop_loss', 0):,.0f}원\n" \
                          f"투자기간: {scenario.get('investment_period', '단기')}\n" \
                          f"산업군: {scenario.get('sector', '알 수 없음')}\n"
            else:
                message = f"📈 신규 매수: {company_name}({ticker})\n" \
                          f"매수가: {current_price:,.0f}원\n" \
                          f"목표가: {scenario.get('target_price', 0):,.0f}원\n" \
                          f"손절가: {scenario.get('stop_loss', 0):,.0f}원\n" \
                          f"투자기간: {scenario.get('investment_period', '단기')}\n" \
                          f"산업군: {scenario.get('sector', '알 수 없음')}\n"

            # Add trigger win rate
            trigger_win_rate = self._get_trigger_win_rate(trigger_type)
            if trigger_win_rate:
                message += f"{trigger_win_rate}\n"

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

            # Surface journal-grounded reasoning so the feedback loop is transparent (#280).
            # All fields are optional — defends against scenarios without journal_reflection.
            _jr = scenario.get('journal_reflection') or {}
            if isinstance(_jr, dict):
                if _jr.get('recent_exit_caution'):
                    message += f"⚠️ 최근 매도 주의: {_jr.get('recent_exit_caution')}\n"
                if _jr.get('applied_lessons'):
                    message += f"📒 매매일지 반영: {_jr.get('applied_lessons')}\n"
            _sadj = scenario.get('score_adjustment') or {}
            if isinstance(_sadj, dict) and _sadj.get('value'):
                _rsn = ', '.join(_sadj.get('reasons', []) or [])
                message += f"📊 경험 기반 점수조정: {_sadj.get('value'):+d}점 ({_rsn})\n"

            # Format trading scenario
            trading_scenarios = scenario.get('trading_scenarios', {})
            if trading_scenarios and isinstance(trading_scenarios, dict):
                message += "\n" + "="*40 + "\n"
                message += "📋 매매 시나리오\n"
                message += "="*40 + "\n\n"

                # 1. Key Levels
                key_levels = trading_scenarios.get('key_levels', {})
                if key_levels:
                    message += "💰 핵심 가격대:\n"

                    # Resistance levels
                    primary_resistance = self._parse_price_value(key_levels.get('primary_resistance', 0))
                    secondary_resistance = self._parse_price_value(key_levels.get('secondary_resistance', 0))
                    if primary_resistance or secondary_resistance:
                        message += "  📈 저항선:\n"
                        if secondary_resistance:
                            message += f"    • 2차: {secondary_resistance:,.0f}원\n"
                        if primary_resistance:
                            message += f"    • 1차: {primary_resistance:,.0f}원\n"

                    # Current price
                    message += f"  ━━ 현재가: {current_price:,.0f}원 ━━\n"

                    # Support levels
                    primary_support = self._parse_price_value(key_levels.get('primary_support', 0))
                    secondary_support = self._parse_price_value(key_levels.get('secondary_support', 0))
                    if primary_support or secondary_support:
                        message += "  📉 지지선:\n"
                        if primary_support:
                            message += f"    • 1차: {primary_support:,.0f}원\n"
                        if secondary_support:
                            message += f"    • 2차: {secondary_support:,.0f}원\n"

                    # Volume baseline
                    volume_baseline = key_levels.get('volume_baseline', '')
                    if volume_baseline:
                        message += f"  📊 거래량 기준: {volume_baseline}\n"

                    message += "\n"

                # 2. Sell Signals
                sell_triggers = trading_scenarios.get('sell_triggers', [])
                if sell_triggers:
                    message += "🔔 매도 시그널:\n"
                    for i, trigger in enumerate(sell_triggers, 1):
                        # Select emoji based on condition
                        if "profit" in trigger.lower() or "target" in trigger.lower() or "resistance" in trigger.lower():
                            emoji = "✅"
                        elif "loss" in trigger.lower() or "support" in trigger.lower() or "decline" in trigger.lower():
                            emoji = "⛔"
                        elif "time" in trigger.lower() or "sideways" in trigger.lower():
                            emoji = "⏰"
                        else:
                            emoji = "•"

                        message += f"  {emoji} {trigger}\n"
                    message += "\n"

                # 3. Hold Conditions
                hold_conditions = trading_scenarios.get('hold_conditions', [])
                if hold_conditions:
                    message += "✋ 보유 지속 조건:\n"
                    for condition in hold_conditions:
                        message += f"  • {condition}\n"
                    message += "\n"

                # 4. Portfolio Context
                portfolio_context = trading_scenarios.get('portfolio_context', '')
                if portfolio_context:
                    message += f"💼 포트폴리오 관점:\n  {portfolio_context}\n"

            self._msg_types.append("analysis")
            self.message_queue.append(message)
            logger.info(f"{ticker}({company_name}) purchase complete")

            return True

        except Exception as e:
            logger.error(f"{ticker} Error during purchase processing: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    def _get_live_regime_safe(self) -> Optional[str]:
        """매도 판단용 '현재' KOSPI 레짐을 1회 계산(OpenAI 무관). 실패 시 None → stale 폴백."""
        try:
            from cores.data_prefetch import prefetch_macro_intelligence_data
            reference_date = datetime.now().strftime("%Y%m%d")
            data = prefetch_macro_intelligence_data(reference_date) or {}
            regime = (data.get("computed_regime") or {}).get("market_regime")
            if regime:
                logger.info(f"[sell] live KR market regime: {regime}")
            return regime or None
        except Exception as e:
            logger.warning(f"[sell] live regime fetch failed, using stale market_condition: {e}")
            return None

    async def _analyze_sell_decision(self, stock_data: Dict[str, Any]) -> Tuple[bool, str]:
        """
        Sell decision analysis

        1차: O'Neil 추세추종 룰(cores.oneil_fallback) — 승자 보유/손실 차단.
        2차(안전망): O'Neil 모듈 불가/예외 시에만 기존 레거시 룰.

        Args:
            stock_data: Stock information

        Returns:
            Tuple[bool, str]: Whether to sell, sell reason
        """
        # ── TIER0: 법인 이벤트 강제청산 (가격/레짐 무관, 최우선) ──────
        # KIS 종목상태코드(관리종목 51) 결정론 자동탐지. 상폐/공개매수 등 뉴스성
        # 이벤트는 매도 AI 프롬프트(핵심-0)의 perplexity 뉴스 점검이 자율 처리.
        # 여기서 True면 시뮬+KIS 양쪽이 다음 사이클에 시장가 자동 청산(정규장 기준).
        try:
            from cores.corporate_status import check_event_exit
            ev_sell, ev_reason = check_event_exit(
                stock_data.get("ticker", ""),
                kis_status_code=stock_data.get("_kis_stat_code"),
                market="KR",
            )
            if ev_sell:
                logger.warning(
                    f"{stock_data.get('ticker','')} TIER0 event force-exit: {ev_reason}"
                )
                return True, ev_reason
        except Exception as e:
            logger.warning(f"{stock_data.get('ticker','')} TIER0 event check skipped: {e}")

        # ── O'Neil 룰베이스 (live regime 주입) ───────────────────
        if _ONEIL_FALLBACK_AVAILABLE:
            try:
                live_regime = getattr(self, "_live_regime_cache", None)
                inp = _oneil_from(stock_data, live_regime=live_regime)
                should_sell, reason = _oneil_eval(inp)
                logger.info(
                    f"{stock_data.get('ticker','')} O'Neil rule-based sell: "
                    f"{'Sell' if should_sell else 'Hold'} | {reason}"
                )
                return should_sell, reason
            except Exception as e:
                logger.error(f"O'Neil sell rule error, using legacy rules: {e}")

        # ── 레거시 안전망 (O'Neil 모듈 불가/예외 시에만) ─────────
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
            investment_period = "medium_term"  # Default value

            try:
                if isinstance(scenario_str, str):
                    scenario_data = json.loads(scenario_str)
                    investment_period = scenario_data.get('investment_period', 'medium_term')
            except:
                pass

            # Check stop-loss condition
            if stop_loss > 0 and current_price <= stop_loss:
                return True, f"손절 조건 도달 (손절가: {stop_loss:,.0f}원)"

            # Check target price reached
            if target_price > 0 and current_price >= target_price:
                return True, f"목표가 달성 (목표가: {target_price:,.0f}원)"

            # Sell conditions by investment period
            if investment_period == "short_term":
                # Short-term investment: quicker sell (15+ days holding + 5%+ profit)
                if days_passed >= 15 and profit_rate >= 5:
                    return True, f"단기 투자 목표 달성 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

                # Short-term investment loss protection (10+ days + 3%+ loss)
                if days_passed >= 10 and profit_rate <= -3:
                    return True, f"단기 투자 손실 방어 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Existing sell conditions
            # Sell if profit >= 10%
            if profit_rate >= 10:
                return True, f"수익률 10% 이상 달성 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if loss >= 5%
            if profit_rate <= -5:
                return True, f"손실 -5% 이상 발생 (현재 수익률: {profit_rate:.2f}%)"

            # Sell if holding 30+ days with loss
            if days_passed >= 30 and profit_rate < 0:
                return True, f"30일 이상 보유 중 손실 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Sell if holding 60+ days with 3%+ profit
            if days_passed >= 60 and profit_rate >= 3:
                return True, f"60일 이상 보유 중 3% 이상 수익 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Long-term investment case (90+ days holding + loss)
            if investment_period == "long_term" and days_passed >= 90 and profit_rate < 0:
                return True, f"장기 투자 손실 정리 (보유: {days_passed}일, 수익률: {profit_rate:.2f}%)"

            # Continue holding by default
            return False, "보유 지속"

        except Exception as e:
            logger.error(f"{stock_data.get('ticker', '') if 'ticker' in locals() else 'Unknown stock'} Error analyzing sell: {str(e)}")
            return False, "Analysis error"

    async def sell_stock(self, stock_data: Dict[str, Any], sell_reason: str,
                         exit_kind: Optional[str] = None) -> bool:
        """
        Stock sell processing

        Args:
            stock_data: Stock information to sell
            sell_reason: Sell reason
            exit_kind: Optional explicit exit classification (stop | trend_exit |
                target | ai). Loops pass it deterministically (hardstop→'stop',
                trend_exit→'trend_exit' (구 loop_a/loop_b)); when None it is inferred from sell_reason. Stored
                in trading_history so the re-entry cooldown treats a stop-out at a
                marginal profit as churn-risk.

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
            trigger_type = stock_data.get('trigger_type', 'AI Analysis')
            trigger_mode = stock_data.get('trigger_mode', 'unknown')
            account_key = stock_data.get('account_key') or self._account_scope()[0]
            account_name = stock_data.get('account_name') or self._account_scope()[1]

            # ── Cross-cycle sell guard (single source of truth) ──────────────
            # EVERY sell path routes its real order + signal publish through
            # sell_stock and gates on this bool return: the batch update_holdings,
            # hardstop_seller, and trend_exit_seller (구 loop_a_hardstop/loop_b_trend_exit) (KR + US). A concurrent cycle
            # may have already closed this position seconds/minutes ago, so refresh
            # the connection snapshot (commit ends any stale WAL read-txn so other
            # processes' commits are visible) and abort if the row is gone — no
            # trading_history row, no delete, no journal, no queued message — so the
            # caller publishes NO duplicate/ghost SELL and P&L is not double-counted.
            # Incident 2026-07-01 (MU): hardstop (구 loop_a) stop-sold 23:50 (+published SELL),
            # the batch re-hit the same stop off a stale snapshot and re-published a
            # 2nd SELL 23:55. sell_stock is the chokepoint that closes this for all
            # paths in both markets.
            self.conn.commit()
            if get_existing_position_for_ticker(
                self.cursor, ticker, account_key=account_key
            ).get("row_count", 0) == 0:
                logger.warning(
                    f"[SELL-GUARD][KR] {ticker}({company_name}) already closed by "
                    f"another cycle — sell_stock aborting (no duplicate record/signal)"
                )
                return False
            # ─────────────────────────────────────────────────────────────────

            # Calculate profit rate
            profit_rate = ((current_price - buy_price) / buy_price) * 100

            # Calculate holding period (days)
            buy_datetime = datetime.strptime(buy_date, "%Y-%m-%d %H:%M:%S")
            now_datetime = datetime.now()
            holding_days = (now_datetime - buy_datetime).days

            # Current time
            now = now_datetime.strftime("%Y-%m-%d %H:%M:%S")

            # Classify the exit (stop/trend_exit/target/ai) for the churn guard.
            try:
                from reentry_cooldown import classify_exit_kind
                _exit_kind = classify_exit_kind(sell_reason, exit_kind)
            except Exception:
                _exit_kind = exit_kind  # fail-open: store caller hint or None

            # Add to trading history table
            self.cursor.execute(
                """
                INSERT INTO trading_history
                (account_key, account_name, ticker, company_name, buy_price, buy_date, sell_price, sell_date, profit_rate, holding_days, scenario, trigger_type, trigger_mode, sector, exit_kind)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_key,
                    account_name,
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
                    trigger_mode,
                    stock_data.get('sector'),
                    _exit_kind,
                )
            )

            # Remove from holdings.
            # Pyramiding (#288): when the row carries an id AND the ticker has more
            # than one row for this account, delete ONLY that row so the remaining
            # independent entries are preserved. Single-row tickers keep the legacy
            # ticker-scoped delete (zero behavior change).
            row_id = stock_data.get('id')
            existing = get_existing_position_for_ticker(self.cursor, ticker, account_key=account_key)
            if row_id is not None and existing.get("row_count", 0) > 1:
                self.cursor.execute(
                    "DELETE FROM stock_holdings WHERE id = ?",
                    (row_id,)
                )
            else:
                self.cursor.execute(
                    "DELETE FROM stock_holdings WHERE ticker = ? AND account_key = ?",
                    (ticker, account_key)
                )

            # Save changes
            self.conn.commit()

            # Add sell message
            arrow = "⬆️" if profit_rate > 0 else "⬇️" if profit_rate < 0 else "➖"
            message = f"📉 매도: {company_name}({ticker})\n" \
                      f"매수가: {buy_price:,.0f}원\n" \
                      f"매도가: {current_price:,.0f}원\n" \
                      f"수익률: {arrow} {abs(profit_rate):.2f}%\n" \
                      f"보유기간: {holding_days}일\n" \
                      f"매도이유: {sell_reason}"

            # Add trigger win rate
            trigger_type = stock_data.get('trigger_type', '')
            trigger_win_rate = self._get_trigger_win_rate(trigger_type)
            if trigger_win_rate:
                message += f"\n{trigger_win_rate}"

            self._msg_types.append("analysis")
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
        self, ticker: str, sector: str = None, market_condition: str = None,
        trigger_type: str = None
    ) -> str:
        """Get journal context for buy decisions (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_context_for_ticker(ticker, sector, trigger_type)

    def _get_universal_principles(self, limit: int = 10) -> List[str]:
        """Get universal principles (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_universal_principles(limit)

    def _get_score_adjustment_from_context(
        self, ticker: str, sector: str = None, trigger_type: str = None
    ) -> Tuple[int, List[str]]:
        """Calculate score adjustment (delegates to tracking.journal.JournalManager)"""
        return self.journal_manager.get_score_adjustment(ticker, sector, trigger_type)

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

            # 매도 판단에 쓸 '현재' 시장 레짐을 사이클당 1회 계산(OpenAI 무관).
            # _analyze_sell_decision 이 self._live_regime_cache 로 참조한다.
            self._live_regime_cache = self._get_live_regime_safe()

            # Query holdings list
            # id included for pyramiding (#288): enables per-row delete and
            # fractional-sell quantity computation for multi-row tickers.
            self.cursor.execute(
                """SELECT id, ticker, company_name, buy_price, buy_date, current_price,
                   scenario, target_price, stop_loss, last_updated,
                   trigger_type, trigger_mode, account_key, account_name, sector
                   FROM stock_holdings
                   WHERE account_key = ?""",
                (self._account_scope()[0],)
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            if not holdings or len(holdings) == 0:
                logger.info("No holdings")
                return []

            sold_stocks = []

            # 이벤트 강제청산 자동탐지: 사이클당 1회 KIS 종목상태코드 일괄 prefetch.
            # (관리종목/투자위험/거래정지 자동 포착 → _analyze_sell_decision의 TIER0.)
            # 실패해도 override 경로는 독립 동작하므로 빈 dict로 안전 폴백.
            kis_status_map: Dict[str, str] = {}
            try:
                from cores.corporate_status import fetch_status_codes
                kis_status_map = await fetch_status_codes(
                    [h.get("ticker") for h in holdings],
                    account_name=holdings[0].get("account_name") if holdings else None,
                )
            except Exception as e:
                logger.warning(f"KIS status prefetch skipped: {e}")

            # Pyramiding (#288) FIX 2 — in-pass over-sell guard:
            # When several rows of the SAME ticker sell within one update pass,
            # limit/reserved orders may not have filled yet, so re-reading the
            # broker quantity each iteration would see the full (un-decremented)
            # quantity and over-sell. Instead we snapshot the ticker's total
            # broker quantity ONCE (first sell of that ticker this pass) and
            # distribute from the snapshot using an in-pass accumulator —
            # independent of fill timing.
            pass_total_qty: Dict[str, int] = {}   # ticker -> snapshot total qty
            pass_sold_qty: Dict[str, int] = {}    # ticker -> cumulative ordered qty

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

                # 이벤트 자동탐지용 KIS 종목상태코드 주입(TIER0가 _analyze_sell_decision에서 사용)
                stock['_kis_stat_code'] = kis_status_map.get(ticker)

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
                    # Pyramiding (#288): compute remaining row count N for this
                    # (ticker, account) BEFORE the DB row is deleted by sell_stock.
                    # N>1 => fractional KIS sell (floor(total/N)); N==1 => sell all
                    # (unchanged). Recomputed live each sell so the last row sweeps.
                    remaining_rows = get_existing_position_for_ticker(
                        self.cursor, ticker, account_key=stock.get("account_key")
                    ).get("row_count", 1)

                    # Process sell (deletes only this row when N>1, else the ticker)
                    sell_success = await self.sell_stock(stock, sell_reason)

                    if sell_success:
                        # Call actual account trading function (async)
                        from trading.domestic_stock_trading import AsyncTradingContext
                        async with AsyncTradingContext(account_name=stock.get("account_name")) as trading:
                            # Determine fractional sell quantity for multi-row tickers.
                            # FIX 2: snapshot total qty once per ticker per pass and
                            # distribute from (snapshot - already_ordered), so fills
                            # that haven't settled yet cannot cause an over-sell.
                            sell_quantity = None
                            # Multi-row tickers sell fractionally. The FINAL row of a
                            # ticker already split THIS pass (remaining_rows==1 but
                            # ticker in pass_total_qty) must also sell from the snapshot
                            # remainder (available), NOT re-query the broker — otherwise,
                            # if the earlier limit orders are still unfilled, get_holding_quantity
                            # returns the full position and the last row over-sells (#288 FIX 2).
                            # Genuinely single-row tickers (never split) keep quantity=None → sell_all.
                            if remaining_rows > 1 or ticker in pass_total_qty:
                                if ticker not in pass_total_qty:
                                    pass_total_qty[ticker] = await asyncio.to_thread(
                                        trading.get_holding_quantity, ticker
                                    )
                                    pass_sold_qty[ticker] = 0
                                available = pass_total_qty[ticker] - pass_sold_qty[ticker]
                                sell_quantity = compute_fractional_sell_quantity(available, remaining_rows)
                                pass_sold_qty[ticker] += sell_quantity
                                logger.info(
                                    f"{ticker} pyramiding fractional sell: {sell_quantity} shares "
                                    f"(available {available} of snapshot {pass_total_qty[ticker]}, "
                                    f"remaining rows={remaining_rows})"
                                )
                            # Execute async sell with limit price for reserved orders
                            trade_result = await trading.async_sell_stock(
                                stock_code=ticker, limit_price=current_price, quantity=sell_quantity
                            )

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
                        account_label = self._safe_account_log_label(
                            {
                                "name": stock.get("account_name"),
                                "account_key": stock.get("account_key"),
                            }
                        )
                        sold_stocks.append({
                            "ticker": ticker,
                            "company_name": company_name,
                            "buy_price": stock.get('buy_price', 0),
                            "sell_price": current_price,
                            "profit_rate": ((current_price - stock.get('buy_price', 0)) / stock.get('buy_price', 0) * 100),
                            "reason": sell_reason,
                            "account_name": stock.get("account_name"),
                            "account_label": account_label,
                        })
                else:
                    # Update current price
                    self.cursor.execute(
                        """UPDATE stock_holdings
                           SET current_price = ?, last_updated = ?
                           WHERE ticker = ? AND account_key = ?""",
                        (current_price, now, ticker, stock.get("account_key"))
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
                "SELECT ticker, company_name, buy_price, current_price, buy_date, scenario, target_price, stop_loss FROM stock_holdings WHERE account_key = ?",
                (self._account_scope()[0],)
            )
            holdings = [dict(row) for row in self.cursor.fetchall()]

            # Calculate total profit from trading history
            self.cursor.execute("SELECT SUM(profit_rate) FROM trading_history WHERE account_key = ?", (self._account_scope()[0],))
            total_profit = self.cursor.fetchone()[0] or 0

            # Number of trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history WHERE account_key = ?", (self._account_scope()[0],))
            total_trades = self.cursor.fetchone()[0] or 0

            # Number of successful/failed trades
            self.cursor.execute("SELECT COUNT(*) FROM trading_history WHERE account_key = ? AND profit_rate > 0", (self._account_scope()[0],))
            successful_trades = self.cursor.fetchone()[0] or 0

            # Generate message
            message = f"📊 프리즘 시뮬레이터 | 실시간 포트폴리오 ({datetime.now().strftime('%Y-%m-%d %H:%M')})\n\n"

            # 1. Portfolio summary
            message += f"🔸 현재 보유: {len(holdings) if holdings else 0}/{self.max_slots}개\n"

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
                message += "🔸 보유 종목:\n"
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
                    message += f"  매수가: {buy_price:,.0f}원 / 현재가: {current_price:,.0f}원\n"
                    message += f"  목표가: {target_price:,.0f}원 / 손절가: {stop_loss:,.0f}원\n"
                    message += f"  수익률: {arrow} {profit_rate:.2f}% / 보유기간: {days_passed}일\n\n"

                # Add sector distribution
                message += "🔸 섹터 분포:\n"
                for sector, count in sector_counts.items():
                    percentage = (count / len(holdings)) * 100
                    message += f"- {sector}: {count}개 ({percentage:.1f}%)\n"
                message += "\n"
            else:
                message += "현재 보유 종목이 없습니다.\n\n"

            # 3. Trading history statistics
            message += "🔸 매매 이력 통계\n"
            message += f"- 총 거래: {total_trades}건\n"
            message += f"- 수익 거래: {successful_trades}건\n"
            message += f"- 손실 거래: {total_trades - successful_trades}건\n"

            if total_trades > 0:
                message += f"- 승률: {(successful_trades / total_trades * 100):.2f}%\n"
            else:
                message += "- 승률: 0.00%\n"

            message += f"- 누적 수익률: {total_profit:.2f}%\n\n"

            # 4. Enhanced disclaimer
            message += "📝 주의사항:\n"
            message += "- 본 리포트는 AI 기반 시뮬레이션 결과이며 실제 매매와 무관합니다.\n"
            message += "- 본 정보는 참고용이며, 투자 결정과 책임은 전적으로 투자자에게 있습니다.\n"
            message += "- 본 채널은 종목 추천 및 매매 방이 아닙니다."

            return message

        except Exception as e:
            logger.error(f"Error generating report summary: {str(e)}")
            error_msg = f"Error occurred while generating report: {str(e)}"
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

            if not self.account_configs:
                logger.warning("No accounts configured. Skipping buy/sell execution.")
                return 0, 0

            if not self.active_account:
                self._set_active_account(self.account_configs[0])

            buy_count = 0
            sell_count = 0
            signaled_tickers: set[str] = set()
            analysis_states: list[dict[str, Any]] = []

            for pdf_report_path in pdf_report_paths:
                analysis_result = await self._analyze_report_core(pdf_report_path)
                if not analysis_result.get("success", False):
                    logger.error(f"Report analysis failed: {pdf_report_path} - {analysis_result.get('error', 'Unknown error')}")
                    continue
                analysis_states.append(
                    {
                        "analysis": analysis_result,
                        "traded": False,
                        "should_save_watchlist": False,
                        "skip_reason": None,
                    }
                )

            for account in self.account_configs:
                self._set_active_account(account)
                label = self._safe_account_log_label(account)
                logger.info(f"Processing KR reports for account {label}")

                # 1. Update existing holdings and make sell decisions
                sold_stocks = await self.update_holdings()
                sell_count += len(sold_stocks)

                if sold_stocks:
                    logger.info(f"{len(sold_stocks)} stocks sold for {label}")
                    for stock in sold_stocks:
                        logger.info(f"Sold: {stock['company_name']}({stock['ticker']}) - Return: {stock['profit_rate']:.2f}% / Reason: {stock['reason']}")
                else:
                    logger.info(f"No stocks sold for {label}")

                for state in analysis_states:
                    analysis_result = state["analysis"]
                    ticker = analysis_result.get("ticker")
                    company_name = analysis_result.get("company_name")
                    current_price = analysis_result.get("current_price", 0)
                    scenario = analysis_result.get("scenario", {})
                    sector = analysis_result.get("sector", "Unknown")
                    rank_change_msg = analysis_result.get("rank_change_msg", "")

                    if await self._is_ticker_in_holdings(ticker):
                        logger.info(f"Skipping stock in holdings: {ticker} - {company_name}")
                        continue

                    current_slots = await self._get_current_slots_count()
                    if current_slots >= self.max_slots:
                        # User-facing reason must NOT include the account label (leaks
                        # masked account number to broadcast channels). Keep detail in logs only.
                        reason = "Max slots reached (portfolio full)"
                        logger.info(f"Purchase deferred: {company_name}({ticker}) - Max slots reached for {label}")
                        state["should_save_watchlist"] = True
                        state["skip_reason"] = state["skip_reason"] or reason
                        continue

                    if not await self._check_sector_diversity(sector):
                        reason = "Preventing sector over-investment"
                        logger.info(f"Purchase deferred: {company_name}({ticker}) - {reason}")
                        state["should_save_watchlist"] = True
                        state["skip_reason"] = state["skip_reason"] or reason
                        continue

                    buy_score = scenario.get("buy_score", 0)
                    min_score = scenario.get("min_score", 0)
                    logger.info(f"Buy score check: {company_name}({ticker}) - Score: {buy_score}")

                    # Re-entry cooldown gate (SHADOW logs only; LIVE vetoes a churn
                    # re-entry into a name just sold — longer cooldown after a loss).
                    _cd_block = False
                    if analysis_result.get("decision") == "Enter":
                        try:
                            from reentry_cooldown import reentry_block, COOLDOWN_LIVE, COOLDOWN_RISK_EXIT_LIVE
                            _cd = reentry_block("KR", ticker)
                        except Exception:
                            _cd, COOLDOWN_LIVE, COOLDOWN_RISK_EXIT_LIVE = None, False, False
                        if _cd:
                            # A stop/trend-exit block that is NOT also a loss is the new
                            # exit-kind branch -> SHADOW unless COOLDOWN_RISK_EXIT_LIVE.
                            _risk_only = bool(_cd.get("risk_exit")) and not _cd.get("after_loss")
                            _enforce = COOLDOWN_LIVE and (COOLDOWN_RISK_EXIT_LIVE or not _risk_only)
                            logger.warning(
                                "[REENTRY_COOLDOWN][%s] %s ticker=%s last_sell=%s ret=%.1f%% gap=%.1fh<%sh after_loss=%s exit_kind=%s risk_only=%s",
                                "LIVE" if _enforce else "SHADOW", _cd["action"], ticker,
                                _cd["last_sell"], _cd["last_ret"], _cd["gap_hours"],
                                _cd["window_hours"], _cd["after_loss"], _cd.get("exit_kind"), _risk_only)
                            _cd_block = _enforce

                    if analysis_result.get("decision") == "Enter" and not _cd_block:
                        buy_success = await self.buy_stock(ticker, company_name, current_price, scenario, rank_change_msg)

                        if buy_success:
                            from trading.domestic_stock_trading import AsyncTradingContext

                            async with AsyncTradingContext(account_name=account["name"]) as trading:
                                trade_result = await trading.async_buy_stock(stock_code=ticker, limit_price=current_price)

                            if trade_result['success']:
                                logger.info(f"Actual purchase successful: {trade_result['message']}")
                            else:
                                logger.error(f"Actual purchase failed: {trade_result['message']}")

                            if trade_result.get("partial_success"):
                                successful = trade_result.get("successful_accounts", [])
                                failed = trade_result.get("failed_accounts", [])
                                logger.warning(
                                    f"{ticker} partial success: {len(successful)}/{len(successful) + len(failed)} accounts"
                                )

                            if ticker not in signaled_tickers:
                                try:
                                    from messaging.redis_signal_publisher import publish_buy_signal

                                    await publish_buy_signal(
                                        ticker=ticker,
                                        company_name=company_name,
                                        price=current_price,
                                        scenario=scenario,
                                        source="AI Analysis",
                                        trade_result=trade_result
                                    )
                                except Exception as signal_err:
                                    logger.warning(f"Buy signal publish failed (non-critical): {signal_err}")

                                try:
                                    from messaging.gcp_pubsub_signal_publisher import publish_buy_signal as gcp_publish_buy_signal

                                    await gcp_publish_buy_signal(
                                        ticker=ticker,
                                        company_name=company_name,
                                        price=current_price,
                                        scenario=scenario,
                                        source="AI Analysis",
                                        trade_result=trade_result
                                    )
                                except Exception as signal_err:
                                    logger.warning(f"GCP buy signal publish failed (non-critical): {signal_err}")

                                signaled_tickers.add(ticker)

                        if buy_success:
                            buy_count += 1
                            state["traded"] = True
                            logger.info(f"Purchase complete: {company_name}({ticker}) @ {current_price:,.0f} KRW")
                        else:
                            state["should_save_watchlist"] = True
                            state["skip_reason"] = state["skip_reason"] or "Purchase failed"
                            logger.warning(f"Purchase failed: {company_name}({ticker})")
                        continue

                    reason = ""
                    if buy_score < min_score:
                        reason = f"Buy score insufficient ({buy_score} < {min_score})"
                    elif analysis_result.get("decision") != "Enter":
                        reason = f"Not an entry decision (Decision: {analysis_result.get('decision')})"

                    logger.info(f"Purchase deferred: {company_name}({ticker}) - {reason}")
                    state["should_save_watchlist"] = True
                    state["skip_reason"] = state["skip_reason"] or reason

            for state in analysis_states:
                if state["traded"] or not state["should_save_watchlist"]:
                    continue

                analysis_result = state["analysis"]
                scenario = analysis_result.get("scenario", {})
                decision = self._normalize_decision(analysis_result.get("decision", "Skip"))
                if decision == "Enter":
                    decision = "Watch"

                await self._save_watchlist_item(
                    ticker=analysis_result.get("ticker"),
                    company_name=analysis_result.get("company_name"),
                    current_price=analysis_result.get("current_price", 0),
                    buy_score=scenario.get("buy_score", 0),
                    min_score=scenario.get("min_score", 0),
                    decision=decision,
                    skip_reason=state["skip_reason"] or "Trade not executed",
                    scenario=scenario,
                    sector=analysis_result.get("sector", "Unknown"),
                    was_traded=False,
                )

            logger.info(f"Report processing complete - Purchased: {buy_count} stocks, Sold: {sell_count} stocks")
            return buy_count, sell_count

        except Exception as e:
            logger.error(f"Error processing reports: {str(e)}")
            logger.error(traceback.format_exc())
            return 0, 0

    async def _notify_firebase(self, message: str, chat_id: str, message_id: int = None, msg_type=None):
        """Send Firebase Bridge notification for Prism Mobile push (never affects Telegram delivery)."""
        try:
            from firebase_bridge import notify
            await notify(
                message=message,
                market="kr",
                telegram_message_id=message_id,
                channel_id=chat_id,
                msg_type=msg_type,
            )
        except Exception as e:
            logger.debug(f"Firebase bridge: {e}")

    def _schedule_firebase(self, message: str, chat_id: str, message_id: int = None, msg_type=None):
        """Schedule Firebase notification as non-blocking task. Returns the task."""
        return asyncio.create_task(self._notify_firebase(message, chat_id, message_id, msg_type=msg_type))

    async def _send_with_retry(self, chat_id: str, text: str, max_retries: int = 3):
        """Send a single Telegram message with retry on timeout and rate-limit."""
        for attempt in range(max_retries + 1):
            try:
                return await self.telegram_bot.send_message(chat_id=chat_id, text=text)
            except RetryAfter as e:
                if attempt < max_retries:
                    wait_time = e.retry_after + 1
                    logger.warning(f"Rate limit hit. Waiting {wait_time}s before retry... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    raise
            except TimedOut:
                if attempt < max_retries:
                    wait_time = 2 ** attempt  # 1, 2, 4 seconds
                    logger.warning(f"Timeout sending to {chat_id}. Retrying in {wait_time}s... (attempt {attempt + 1}/{max_retries})")
                    await asyncio.sleep(wait_time)
                else:
                    raise

    async def send_telegram_message(self, chat_id: str, language: str = "ko",
                                    portfolio_force: bool = False,
                                    await_broadcast: bool = False) -> bool:
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
                self._msg_types = []
                return True  # Consider intentional skip as success

            # If Telegram bot not initialized, only output logs
            if not self.telegram_bot:
                logger.warning("Telegram bot not initialized. Please check token")

                # Only output messages without actual sending
                for message in self.message_queue:
                    logger.info(f"[Telegram message (bot not initialized)] {message[:100]}...")

                # Initialize message queue
                self.message_queue = []
                self._msg_types = []
                return False

            # Generate summary report — de-duplicated so near-simultaneous run-ends
            # (KR batch + intraday loops A/B) don't emit 2-3 identical portfolio
            # summaries. Other queued messages (sell notices) are unaffected.
            try:
                from portfolio_broadcast import should_send_portfolio
                # 배치 run-end(portfolio_force=True)는 완전한 최종 요약이므로 디바운스 우회.
                _emit_portfolio = should_send_portfolio("KR", force=portfolio_force)
            except Exception:
                _emit_portfolio = True  # fail-open
            if _emit_portfolio:
                summary = await self.generate_report_summary()
                self._msg_types.append("portfolio")
                self.message_queue.append(summary)
            else:
                logger.info("[portfolio-dedup] KR portfolio summary skipped (sent within debounce window)")

            # Translate messages if English is requested
            if language == "en":
                logger.info(f"Translating {len(self.message_queue)} messages to English")
                try:
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    translated_queue = []
                    for idx, message in enumerate(self.message_queue, 1):
                        logger.info(f"Translating message {idx}/{len(self.message_queue)}")
                        translated = await translate_telegram_message(message, model="gpt-5.4-nano")
                        translated_queue.append(translated)
                    self.message_queue = translated_queue
                    logger.info("All messages translated successfully")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean messages.")

            # Send each message (Firebase notifications are non-blocking)
            success = True
            firebase_tasks = []
            for idx, message in enumerate(self.message_queue):
                msg_type = self._msg_types[idx] if idx < len(self._msg_types) else None
                logger.info(f"Sending Telegram message: {chat_id}")
                try:
                    # Telegram message length limit (4096 characters)
                    MAX_MESSAGE_LENGTH = 4096

                    if len(message) <= MAX_MESSAGE_LENGTH:
                        # Send in one message if short
                        result = await self._send_with_retry(chat_id=chat_id, text=message)
                        firebase_tasks.append(self._schedule_firebase(message, chat_id, result.message_id, msg_type=msg_type))
                    else:
                        # Split and send if long
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

                        # Send split messages
                        first_msg_id = None
                        for i, part in enumerate(parts, 1):
                            result = await self._send_with_retry(chat_id=chat_id, text=f"[{i}/{len(parts)}]\n{part}")
                            if i == 1:
                                first_msg_id = result.message_id
                            await asyncio.sleep(0.5)  # Short delay between split messages

                        # Notify with full original message, link to first part
                        firebase_tasks.append(self._schedule_firebase(message, chat_id, first_msg_id, msg_type=msg_type))

                    logger.info(f"Telegram message sent: {chat_id}")
                except TelegramError as e:
                    logger.error(f"Telegram message send failed: {e}")
                    success = False

                # Delay to prevent API rate limiting
                await asyncio.sleep(1)

            # Gather Firebase notifications (non-blocking for Telegram delivery)
            if firebase_tasks:
                await asyncio.gather(*firebase_tasks, return_exceptions=True)

            # Send to broadcast channels if configured (awaited in run() finally block,
            # or inline here when await_broadcast=True — intraday loops don't call run()
            # so the task would be cancelled on process exit unless awaited now).
            if hasattr(self, 'telegram_config') and self.telegram_config and self.telegram_config.broadcast_languages:
                self._broadcast_task = asyncio.create_task(self._send_to_translation_channels(self.message_queue.copy(), self._msg_types.copy()))
                logger.info("Broadcast channel translation dispatched")
                if await_broadcast:
                    try:
                        await self._broadcast_task
                    except Exception as e:
                        logger.warning(f"Broadcast translation await failed (non-critical): {e}")
                    finally:
                        self._broadcast_task = None

            # Clear message queue
            self.message_queue = []
            self._msg_types = []

            return success

        except Exception as e:
            logger.error(f"Error sending Telegram message: {str(e)}")
            logger.error(traceback.format_exc())
            return False

    async def _send_to_translation_channels(self, messages: List[str], msg_types: Optional[list] = None):
        """
        Send messages to translation channels

        Args:
            messages: List of original Korean messages
            msg_types: msg_type for each message in the list
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

                    # Translate and send each message (Firebase non-blocking)
                    firebase_tasks = []
                    for msg_idx, message in enumerate(messages):
                        msg_type = msg_types[msg_idx] if msg_types and msg_idx < len(msg_types) else None
                        try:
                            # Translate message
                            logger.info(f"Translating tracking message to {lang}")
                            translated_message = await translate_telegram_message(
                                message,
                                model="gpt-5.4-nano",
                                from_lang="ko",
                                to_lang=lang
                            )

                            # Send translated message
                            MAX_MESSAGE_LENGTH = 4096

                            if len(translated_message) <= MAX_MESSAGE_LENGTH:
                                result = await self._send_with_retry(chat_id=channel_id, text=translated_message)
                                firebase_tasks.append(self._schedule_firebase(translated_message, channel_id, result.message_id, msg_type=msg_type))
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
                                first_msg_id = None
                                for i, part in enumerate(parts, 1):
                                    result = await self._send_with_retry(chat_id=channel_id, text=f"[{i}/{len(parts)}]\n{part}")
                                    if i == 1:
                                        first_msg_id = result.message_id
                                    await asyncio.sleep(0.5)

                                firebase_tasks.append(self._schedule_firebase(translated_message, channel_id, first_msg_id, msg_type=msg_type))

                            logger.info(f"Tracking message sent successfully to {lang} channel")
                            await asyncio.sleep(1)

                        except Exception as e:
                            logger.error(f"Error sending tracking message to {lang}: {str(e)}")
                            from telegram_config import is_openai_quota_error, send_openai_quota_alert
                            if is_openai_quota_error(e):
                                await send_openai_quota_alert(self.telegram_config, market="KR")
                                return

                    # Gather Firebase notifications for this language
                    if firebase_tasks:
                        await asyncio.gather(*firebase_tasks, return_exceptions=True)

                except Exception as e:
                    logger.error(f"Error processing language {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_to_translation_channels: {str(e)}")

    async def run(self, pdf_report_paths: List[str], chat_id: str = None, language: str = "ko", telegram_config=None, trigger_results_file: str = None, sector_names: list = None) -> bool | None:
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

            # Initialize with language parameter and sector names
            await self.initialize(language, sector_names=sector_names)

            try:
                # Process reports
                buy_count, sell_count = await self.process_reports(pdf_report_paths)

                # Send Telegram message (only if chat_id is provided)
                if chat_id:
                    message_sent = await self.send_telegram_message(chat_id, language, portfolio_force=True)
                    if message_sent:
                        logger.info("Telegram message sent successfully")
                    else:
                        logger.warning("Telegram message send failed")
                else:
                    logger.info("Telegram channel ID not provided, skipping message send")
                    # Call even if chat_id is None to clean up message queue
                    await self.send_telegram_message(None, language, portfolio_force=True)

                logger.info("Tracking system batch execution complete")
                return True
            finally:
                # Wait for broadcast translation task before cleanup
                if self._broadcast_task:
                    try:
                        logger.info("Waiting for tracking broadcast translation to complete...")
                        await self._broadcast_task
                        logger.info("Tracking broadcast translation completed")
                    except Exception as e:
                        logger.error(f"Tracking broadcast translation failed: {e}")
                    self._broadcast_task = None

                # Ensure connection is always closed
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
