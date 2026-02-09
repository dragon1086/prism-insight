#!/usr/bin/env python3
"""
Telegram AI Conversational Bot

Bot that provides customized responses to user requests:
- /evaluate command to provide analysis and advice on holdings
- /report command to generate detailed analysis reports and HTML files for specific stocks
- /history command to check analysis history for specific stocks
- Available only to channel subscribers
"""
import asyncio
import json
import logging
import os
import re
import signal
import traceback
from datetime import datetime
from pathlib import Path
from queue import Queue

from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.request import HTTPXRequest

from analysis_manager import (
    AnalysisRequest, analysis_queue, start_background_worker
)
# Internal module imports
from report_generator import (
    generate_evaluation_response, get_cached_report, generate_follow_up_response,
    get_or_create_global_mcp_app, cleanup_global_mcp_app,
    generate_us_evaluation_response, generate_us_follow_up_response,
    get_cached_us_report, generate_journal_conversation_response
)
from tracking.user_memory import UserMemoryManager
from datetime import datetime, timedelta
from typing import Dict, Optional

# Load environment variables
load_dotenv()

# Logger setup
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            f"ai_bot_{datetime.now().strftime('%Y%m%d')}.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
    ]
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Constants definition
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # Create directory if not exists
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML reports directory

# Conversation state definitions
CHOOSING_TICKER, ENTERING_AVGPRICE, ENTERING_PERIOD, ENTERING_TONE, ENTERING_BACKGROUND = range(5)
REPORT_CHOOSING_TICKER = 0  # State for /report command
HISTORY_CHOOSING_TICKER = 0  # State for /history command

# US stocks conversation state definitions
US_CHOOSING_TICKER, US_ENTERING_AVGPRICE, US_ENTERING_PERIOD, US_ENTERING_TONE, US_ENTERING_BACKGROUND = range(5, 10)
US_REPORT_CHOOSING_TICKER = 10  # State for /us_report command

# Journal conversation state definitions
JOURNAL_ENTERING = 20  # State for /journal command

# Channel ID
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))


def generate_triggers_message(db_path: str) -> str:
    """
    Generate trigger reliability report message from database.

    Args:
        db_path: Path to SQLite database

    Returns:
        Formatted message string with trigger reliability data
    """
    import sqlite3

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()

        # Query KR analysis data
        kr_analysis = {}
        try:
            cursor.execute("""
                SELECT trigger_type,
                       COUNT(*) as total,
                       SUM(CASE WHEN tracking_status = 'completed' THEN 1 ELSE 0 END) as completed,
                       AVG(CASE WHEN tracking_status = 'completed' THEN tracked_30d_return ELSE NULL END) as avg_return,
                       SUM(CASE WHEN tracking_status = 'completed' AND tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN tracking_status = 'completed' AND tracked_30d_return <= 0 THEN 1 ELSE 0 END) as losses
                FROM analysis_performance_tracker
                WHERE trigger_type IS NOT NULL
                GROUP BY trigger_type
                ORDER BY completed DESC
            """)
            for row in cursor.fetchall():
                kr_analysis[row['trigger_type']] = dict(row)
        except sqlite3.Error:
            pass

        # Query KR trading data
        kr_trading = {}
        try:
            cursor.execute("""
                SELECT COALESCE(trigger_type, 'AI분석') as trigger_type,
                       COUNT(*) as count,
                       SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(profit_rate) as avg_profit
                FROM trading_history
                GROUP BY COALESCE(trigger_type, 'AI분석')
            """)
            for row in cursor.fetchall():
                kr_trading[row['trigger_type']] = dict(row)
        except sqlite3.Error:
            pass

        # Query US analysis data
        us_analysis = {}
        try:
            # Check if table exists
            cursor.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='us_analysis_performance_tracker'
            """)
            if cursor.fetchone():
                cursor.execute("""
                    SELECT trigger_type,
                           COUNT(*) as total,
                           SUM(CASE WHEN return_30d IS NOT NULL THEN 1 ELSE 0 END) as completed,
                           AVG(CASE WHEN return_30d IS NOT NULL THEN return_30d ELSE NULL END) as avg_return,
                           SUM(CASE WHEN return_30d IS NOT NULL AND return_30d > 0 THEN 1 ELSE 0 END) as wins,
                           SUM(CASE WHEN return_30d IS NOT NULL AND return_30d <= 0 THEN 1 ELSE 0 END) as losses
                    FROM us_analysis_performance_tracker
                    WHERE trigger_type IS NOT NULL
                    GROUP BY trigger_type
                    ORDER BY completed DESC
                """)
                for row in cursor.fetchall():
                    us_analysis[row['trigger_type']] = dict(row)
        except sqlite3.Error:
            pass

        # Query US trading data
        us_trading = {}
        try:
            cursor.execute("""
                SELECT COALESCE(trigger_type, 'AI Analysis') as trigger_type,
                       COUNT(*) as count,
                       SUM(CASE WHEN profit_rate > 0 THEN 1 ELSE 0 END) as wins,
                       AVG(profit_rate) as avg_profit
                FROM us_trading_history
                GROUP BY COALESCE(trigger_type, 'AI Analysis')
            """)
            for row in cursor.fetchall():
                us_trading[row['trigger_type']] = dict(row)
        except sqlite3.Error:
            pass

        conn.close()

        # Compute grades and format message
        def compute_grade(trigger_type, analysis_data, trading_data):
            """Compute grade for a trigger"""
            completed = analysis_data.get('completed', 0) or 0

            if completed < 3:
                return 'D'

            # Analysis win rate
            wins = analysis_data.get('wins', 0) or 0
            analysis_win_rate = (wins / completed * 100) if completed > 0 else 0

            # Trading win rate
            trade_count = trading_data.get('count', 0) or 0
            trade_wins = trading_data.get('wins', 0) or 0
            trading_win_rate = (trade_wins / trade_count * 100) if trade_count > 0 else 0

            if analysis_win_rate >= 60 and trading_win_rate >= 60 and trade_count >= 5:
                return 'A'
            elif analysis_win_rate >= 50:
                return 'B'
            else:
                return 'C'

        grade_emoji = {'A': '🟢', 'B': '🔵', 'C': '🟡', 'D': '⚪'}
        grade_label = {
            'A': '높은 신뢰 — 분석+매매 모두 검증됨',
            'B': '보통 신뢰 — 분석 정확도 양호',
            'C': '낮은 신뢰 — 주의 필요',
            'D': '판단 보류 — 데이터 부족',
        }

        def _format_trigger_line(trigger_type, analysis_data, trading_data):
            """Format a single trigger line with detailed stats."""
            completed = analysis_data.get('completed', 0) or 0
            total = analysis_data.get('total', 0) or 0
            wins = analysis_data.get('wins', 0) or 0
            avg_return = analysis_data.get('avg_return')
            trade_count = trading_data.get('count', 0) or 0
            trade_wins = trading_data.get('wins', 0) or 0
            avg_profit = trading_data.get('avg_profit')

            grade = compute_grade(trigger_type, analysis_data, trading_data)
            emoji = grade_emoji[grade]

            if completed == 0:
                line = f"{emoji} {trigger_type} [{grade}]\n   추적 중 ({total}건 분석 대기)"
            elif completed < 3:
                line = f"{emoji} {trigger_type} [{grade}]\n   데이터 부족 — {completed}건 완료 (최소 3건 필요)"
            else:
                analysis_win_rate = int(wins / completed * 100)
                return_str = f", 평균수익 {avg_return * 100:+.1f}%" if avg_return is not None else ""
                parts = [f"{emoji} {trigger_type} [{grade}]"]
                parts.append(f"   분석 승률 {analysis_win_rate}% ({wins}/{completed}건{return_str})")
                if trade_count > 0:
                    trading_win_rate = int(trade_wins / trade_count * 100)
                    profit_str = f", 평균손익 {avg_profit * 100:+.1f}%" if avg_profit is not None else ""
                    parts.append(f"   매매 승률 {trading_win_rate}% ({trade_wins}/{trade_count}건{profit_str})")
                else:
                    parts.append("   매매 이력 없음")
                line = '\n'.join(parts)

            return grade, completed, line

        # Build message
        msg_parts = ["📡 트리거 신뢰도 리포트"]
        msg_parts.append("━━━━━━━━━━━━━━━━━━━━")
        msg_parts.append("AI가 종목을 발견한 '이유(트리거)'별로")
        msg_parts.append("과거 분석 정확도와 실매매 성과를 비교합니다.\n")

        # Grade legend
        msg_parts.append("등급 기준:")
        for g in ['A', 'B', 'C', 'D']:
            msg_parts.append(f"  {grade_emoji[g]} {g} — {grade_label[g]}")

        # KR section
        msg_parts.append("\n🇰🇷 한국시장")
        msg_parts.append("─────────────────")
        kr_triggers = []
        for trigger_type, analysis_data in kr_analysis.items():
            trading_data = kr_trading.get(trigger_type, {})
            kr_triggers.append(_format_trigger_line(trigger_type, analysis_data, trading_data))

        grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        kr_triggers.sort(key=lambda x: (grade_order[x[0]], -x[1]))

        if kr_triggers:
            for _, _, line in kr_triggers:
                msg_parts.append(line)
        else:
            msg_parts.append("  데이터 없음")

        # US section
        msg_parts.append("\n🇺🇸 미국시장")
        msg_parts.append("─────────────────")
        us_triggers = []
        for trigger_type, analysis_data in us_analysis.items():
            trading_data = us_trading.get(trigger_type, {})
            us_triggers.append(_format_trigger_line(trigger_type, analysis_data, trading_data))

        us_triggers.sort(key=lambda x: (grade_order[x[0]], -x[1]))

        if us_triggers:
            for _, _, line in us_triggers:
                msg_parts.append(line)
        else:
            msg_parts.append("  데이터 없음")

        # Summary & insight
        msg_parts.append("\n━━━━━━━━━━━━━━━━━━━━")

        all_triggers = kr_triggers + us_triggers
        a_grade = [t for t in all_triggers if t[0] == 'A']
        c_or_d = [t for t in all_triggers if t[0] in ('C', 'D')]

        if a_grade:
            best_name = a_grade[0][2].split('[')[0].strip().lstrip('🟢🔵🟡⚪ ')
            msg_parts.append(f"💡 가장 믿을 만한 트리거: {best_name}")
            msg_parts.append("   → 이 트리거가 발동되면 매수 적극 검토")
        elif all_triggers:
            best = all_triggers[0]
            best_name = best[2].split('[')[0].strip().lstrip('🟢🔵🟡⚪ ')
            msg_parts.append(f"💡 현재 최고 트리거: {best_name} ({best[0]}등급)")

        if c_or_d:
            weak_names = [t[2].split('[')[0].strip().lstrip('🟢🔵🟡⚪ ') for t in c_or_d[:2]]
            msg_parts.append(f"⚠️ 주의 트리거: {', '.join(weak_names)}")
            msg_parts.append("   → 이 트리거의 종목은 신중하게 판단하세요")

        msg_parts.append("\n분석 승률 = AI 예측이 맞은 비율")
        msg_parts.append("매매 승률 = 실제 매수 후 수익 비율")

        return '\n'.join(msg_parts)

    except Exception as e:
        logger.error(f"Error generating triggers message: {e}")
        return "⚠️ 트리거 신뢰도 데이터를 불러오는 중 오류가 발생했습니다."


class ConversationContext:
    """Conversation context management"""
    def __init__(self, market_type: str = "kr"):
        self.message_id = None
        self.chat_id = None
        self.user_id = None
        self.ticker = None
        self.ticker_name = None
        self.avg_price = None
        self.period = None
        self.tone = None
        self.background = None
        self.conversation_history = []
        self.created_at = datetime.now()
        self.last_updated = datetime.now()
        # Market type: "kr" (Korea) or "us" (USA)
        self.market_type = market_type
        # Currency: KRW (Korea) or USD (USA)
        self.currency = "USD" if market_type == "us" else "KRW"

    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_updated = datetime.now()

    def get_context_for_llm(self) -> str:
        # Set currency unit
        if self.currency == "USD":
            price_str = f"${self.avg_price:,.2f}"
        else:
            price_str = f"{self.avg_price:,.0f} KRW"

        context = f"""
Stock Info: {self.ticker_name} ({self.ticker})
Market: {"USA" if self.market_type == "us" else "Korea"}
Average Purchase Price: {price_str}
Holding Period: {self.period} months
Feedback Style: {self.tone}
Trading Background: {self.background if self.background else "None"}

Previous Conversation History:"""

        for item in self.conversation_history:
            role_label = "AI Response" if item['role'] == 'assistant' else "User Question"
            context += f"\n\n{role_label}: {item['content']}"

        return context

    def is_expired(self, hours: int = 24) -> bool:
        return (datetime.now() - self.last_updated) > timedelta(hours=hours)


class TelegramAIBot:
    """Telegram AI Conversational Bot"""

    def __init__(self):
        """Initialize"""
        self.token = os.getenv("TELEGRAM_AI_BOT_TOKEN")
        if not self.token:
            raise ValueError("Telegram bot token is not configured.")

        # Explicitly create HTML reports directory
        if not HTML_REPORTS_DIR.exists():
            HTML_REPORTS_DIR.mkdir(exist_ok=True)
            logger.info(f"HTML reports directory created: {HTML_REPORTS_DIR}")

        # Check Channel ID
        self.channel_id = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
        if not self.channel_id:
            logger.warning("Telegram channel ID is not configured. Skipping channel subscription verification.")

        # Initialize stock information
        self.stock_map = {}
        self.stock_name_map = {}
        self.load_stock_map()

        self.stop_event = asyncio.Event()

        # Manage pending analysis requests
        self.pending_requests = {}

        # Add result processing queue
        self.result_queue = Queue()
        
        # Add conversation context storage
        self.conversation_contexts: Dict[int, ConversationContext] = {}

        # Journal context storage (for replies)
        self.journal_contexts: Dict[int, Dict] = {}

        # Initialize user memory manager
        self.memory_manager = UserMemoryManager("user_memories.sqlite")

        # Daily usage limit (user_id:command -> date)
        self.daily_report_usage: Dict[str, str] = {}

        # Create bot application (including timeout settings)
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=120.0,   # Ensure sufficient time for file transfers
            write_timeout=120.0,
        )
        self.application = Application.builder().token(self.token).request(request).build()
        self.setup_handlers()

        # Start background worker
        start_background_worker(self)

        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self.load_stock_map, "interval", hours=12)
        # Add expired context cleanup task
        self.scheduler.add_job(self.cleanup_expired_contexts, "interval", hours=1)
        # Add user memory compression task (daily at 3 AM)
        self.scheduler.add_job(self.compress_user_memories, "cron", hour=3, minute=0)
        self.scheduler.start()
    
    def cleanup_expired_contexts(self):
        """Clean up expired conversation contexts"""
        expired_keys = []
        for msg_id, context in self.conversation_contexts.items():
            if context.is_expired(hours=24):
                expired_keys.append(msg_id)

        for key in expired_keys:
            del self.conversation_contexts[key]
            logger.info(f"Deleted expired context: Message ID {key}")

        # Also clean up journal contexts (older than 24 hours)
        journal_expired = []
        now = datetime.now()
        for msg_id, ctx in self.journal_contexts.items():
            if (now - ctx.get('created_at', now)).total_seconds() > 86400:  # 24 hours
                journal_expired.append(msg_id)

        for key in journal_expired:
            del self.journal_contexts[key]
            logger.info(f"Deleted expired journal context: Message ID {key}")

        # Clean up daily usage limits (remove non-today dates)
        today = datetime.now().strftime("%Y-%m-%d")
        daily_limit_expired = [
            key for key, date in self.daily_report_usage.items()
            if date != today
        ]
        for key in daily_limit_expired:
            del self.daily_report_usage[key]
        if daily_limit_expired:
            logger.info(f"Cleaned up expired daily limits: {len(daily_limit_expired)} entries")

    def compress_user_memories(self):
        """Compress user memories (nightly batch)"""
        if self.memory_manager:
            try:
                stats = self.memory_manager.compress_old_memories()
                logger.info(f"User memory compression complete: {stats}")
            except Exception as e:
                logger.error(f"Error during user memory compression: {e}")

    def check_daily_limit(self, user_id: int, command: str) -> bool:
        """
        Check daily usage limit.

        Args:
            user_id: User ID
            command: Command (report, us_report)

        Returns:
            bool: True if available, False if already used
        """
        today = datetime.now().strftime("%Y-%m-%d")
        key = f"{user_id}:{command}"

        if self.daily_report_usage.get(key) == today:
            logger.info(f"Daily limit exceeded: user={user_id}, command={command}")
            return False

        self.daily_report_usage[key] = today
        logger.info(f"Daily usage recorded: user={user_id}, command={command}")
        return True

    def load_stock_map(self):
        """
        Load dictionary mapping stock codes to names
        """
        try:
            # Stock information file path
            stock_map_file = "stock_map.json"

            logger.info(f"Attempting to load stock mapping info: {stock_map_file}")

            if os.path.exists(stock_map_file):
                with open(stock_map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stock_map = data.get("code_to_name", {})
                    self.stock_name_map = data.get("name_to_code", {})

                logger.info(f"Loaded {len(self.stock_map)} stock information entries")
            else:
                logger.warning(f"Stock information file does not exist: {stock_map_file}")
                # Provide default data (for testing)
                self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
                self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

        except Exception as e:
            logger.error(f"Failed to load stock information: {e}")
            # Provide default data at least
            self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
            self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

    def setup_handlers(self):
        """
        Register handlers
        """
        # Basic commands
        self.application.add_handler(CommandHandler("start", self.handle_start))
        self.application.add_handler(CommandHandler("help", self.handle_help))
        self.application.add_handler(CommandHandler("cancel", self.handle_cancel_standalone))
        self.application.add_handler(CommandHandler("memories", self.handle_memories))
        self.application.add_handler(CommandHandler("triggers", self.handle_triggers))

        # Reply handler - registered with group=1 for lower priority than ConversationHandler(group=0)
        # ConversationHandler processes first, this handler only processes unmatched replies
        self.application.add_handler(MessageHandler(
            filters.REPLY & filters.TEXT & ~filters.COMMAND,
            self.handle_reply_to_evaluation
        ), group=1)

        # Report command handler
        report_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("report", self.handle_report_start),
                MessageHandler(filters.Regex(r'^/report(@\w+)?$'), self.handle_report_start)
            ],
            states={
                REPORT_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_report_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(report_conv_handler)

        # History command handler
        history_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("history", self.handle_history_start),
                MessageHandler(filters.Regex(r'^/history(@\w+)?$'), self.handle_history_start)
            ],
            states={
                HISTORY_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_history_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(history_conv_handler)

        # Evaluation conversation handler
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("evaluate", self.handle_evaluate_start),
                # Add pattern for group chats
                MessageHandler(filters.Regex(r'^/evaluate(@\w+)?$'), self.handle_evaluate_start)
            ],
            states={
                CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_ticker_input)
                ],
                ENTERING_AVGPRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_avgprice_input)
                ],
                ENTERING_PERIOD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_period_input)
                ],
                ENTERING_TONE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_tone_input)
                ],
                ENTERING_BACKGROUND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_background_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                # Add other commands as well
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            # Distinguish messages from different users in group chats
            per_chat=False,
            per_user=True,
            # Conversation timeout (seconds)
            conversation_timeout=300,
        )
        self.application.add_handler(conv_handler)

        # ==========================================================================
        # US stocks conversation handlers
        # ==========================================================================

        # US evaluation conversation handler (/us_evaluate)
        us_evaluate_handler = ConversationHandler(
            entry_points=[
                CommandHandler("us_evaluate", self.handle_us_evaluate_start),
                MessageHandler(filters.Regex(r'^/us_evaluate(@\w+)?$'), self.handle_us_evaluate_start)
            ],
            states={
                US_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_ticker_input)
                ],
                US_ENTERING_AVGPRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_avgprice_input)
                ],
                US_ENTERING_PERIOD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_period_input)
                ],
                US_ENTERING_TONE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_tone_input)
                ],
                US_ENTERING_BACKGROUND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_background_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(us_evaluate_handler)

        # US report conversation handler (/us_report)
        us_report_handler = ConversationHandler(
            entry_points=[
                CommandHandler("us_report", self.handle_us_report_start),
                MessageHandler(filters.Regex(r'^/us_report(@\w+)?$'), self.handle_us_report_start)
            ],
            states={
                US_REPORT_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_report_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(us_report_handler)

        # ==========================================================================
        # Journal (investment diary) conversation handler (/journal)
        # ==========================================================================
        journal_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("journal", self.handle_journal_start),
                MessageHandler(filters.Regex(r'^/journal(@\w+)?$'), self.handle_journal_start)
            ],
            states={
                JOURNAL_ENTERING: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_journal_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(journal_conv_handler)

        # General text messages - /help or /start guidance
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_default_message
        ))

        # Error handler
        self.application.add_error_handler(self.handle_error)
    
    async def handle_reply_to_evaluation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle replies to evaluation responses"""
        if not update.message or not update.message.reply_to_message:
            return
        
        # Check replied-to message ID
        replied_to_msg_id = update.message.reply_to_message.message_id
        user_id = update.effective_user.id if update.effective_user else "unknown"
        text = update.message.text[:50] if update.message.text else "no text"

        logger.info(f"[REPLY] handle_reply_to_evaluation - user_id: {user_id}, replied_to: {replied_to_msg_id}, text: {text}")

        # 1. Check journal context (handle journal reply)
        if replied_to_msg_id in self.journal_contexts:
            journal_ctx = self.journal_contexts[replied_to_msg_id]
            logger.info(f"[REPLY] Found in journal_contexts - ticker: {journal_ctx.get('ticker')}")
            await self._handle_journal_reply(update, journal_ctx)
            return

        # 2. Check evaluation context
        if replied_to_msg_id not in self.conversation_contexts:
            # Treat as general message if no context exists
            logger.info(f"[REPLY] Not in conversation_contexts, skipping. keys: {list(self.conversation_contexts.keys())[:5]}")
            return
        
        conv_context = self.conversation_contexts[replied_to_msg_id]
        
        # Check context expiration
        if conv_context.is_expired():
            # Different guidance message depending on market type
            if conv_context.market_type == "us":
                await update.message.reply_text(
                    "The previous conversation session has expired. Please use the /us_evaluate command to start a new evaluation."
                )
            else:
                await update.message.reply_text(
                    "The previous conversation session has expired. Please use the /evaluate command to start a new evaluation."
                )
            del self.conversation_contexts[replied_to_msg_id]
            return

        # Get user message
        user_question = update.message.text.strip()

        # Waiting message (based on market type)
        if conv_context.market_type == "us":
            waiting_message = await update.message.reply_text(
                "🇺🇸 Analyzing your follow-up question... Please wait a moment. 💭"
            )
        else:
            waiting_message = await update.message.reply_text(
                "Analyzing your follow-up question... Please wait a moment. 💭"
            )

        try:
            # Add user question to conversation history
            conv_context.add_to_history("user", user_question)

            # Create context to pass to LLM
            full_context = conv_context.get_context_for_llm()

            # Use different response generator based on market type
            if conv_context.market_type == "us":
                # Generate response for US market
                response = await generate_us_follow_up_response(
                    conv_context.ticker,
                    conv_context.ticker_name,
                    full_context,
                    user_question,
                    conv_context.tone
                )
            else:
                # Generate response for Korean market (existing)
                response = await generate_follow_up_response(
                    conv_context.ticker,
                    conv_context.ticker_name,
                    full_context,
                    user_question,
                    conv_context.tone
                )
            
            # Delete waiting message
            await waiting_message.delete()
            
            # Send response
            sent_message = await update.message.reply_text(
                response + "\n\n💡 If you have additional questions, please reply to this message."
            )
            
            # Add AI response to conversation history
            conv_context.add_to_history("assistant", response)
            
            # Update context with new message ID
            conv_context.message_id = sent_message.message_id
            conv_context.user_id = update.effective_user.id
            self.conversation_contexts[sent_message.message_id] = conv_context
            
            logger.info(f"Follow-up question processed: User {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"Error processing follow-up question: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text(
                "Sorry, an error occurred while processing your follow-up question. Please try again."
            )

    async def send_report_result(self, request: AnalysisRequest):
        """Send analysis results to Telegram"""
        if not request.chat_id:
            logger.warning(f"Cannot send results without chat ID: {request.id}")
            return

        try:
            # Send PDF file
            if request.pdf_path and os.path.exists(request.pdf_path):
                with open(request.pdf_path, 'rb') as file:
                    await self.application.bot.send_document(
                        chat_id=request.chat_id,
                        document=InputFile(file, filename=f"{request.company_name}_{request.stock_code}_analysis.pdf"),
                        caption=f"✅ Analysis report for {request.company_name} ({request.stock_code}) is complete."
                    )
            else:
                # Send results as text if PDF file is missing
                if request.result:
                    # Truncate and send if text is too long
                    max_length = 4000  # Telegram message max length
                    if len(request.result) > max_length:
                        summary = request.result[:max_length] + "...(truncated)"
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ Analysis results for {request.company_name} ({request.stock_code}):\n\n{summary}"
                        )
                    else:
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ Analysis results for {request.company_name} ({request.stock_code}):\n\n{request.result}"
                        )
                else:
                    await self.application.bot.send_message(
                        chat_id=request.chat_id,
                        text=f"⚠️ Cannot find analysis results for {request.company_name} ({request.stock_code})."
                    )
        except Exception as e:
            logger.error(f"Error sending results: {str(e)}")
            logger.error(traceback.format_exc())
            await self.application.bot.send_message(
                chat_id=request.chat_id,
                text=f"⚠️ An error occurred while sending analysis results for {request.company_name} ({request.stock_code})."
            )

    @staticmethod
    async def handle_default_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """General messages redirect to /help or /start"""
        # Check if update.message is None
        if update.message is None:
            logger.warning(f"Received update without message: {update}")
            return

        # Debug: Check what messages are coming here
        user_id = update.effective_user.id if update.effective_user else "unknown"
        chat_id = update.effective_chat.id if update.effective_chat else "unknown"
        text = update.message.text[:50] if update.message.text else "no text"
        logger.debug(f"[DEFAULT] handle_default_message - user_id: {user_id}, chat_id: {chat_id}, text: {text}")

        return

    @staticmethod
    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle start command"""
        user = update.effective_user
        await update.message.reply_text(
            f"Hello, {user.first_name}! I am the Prism Advisor Bot.\n\n"
            "I provide evaluations of your stock holdings.\n\n"
            "🇰🇷 <b>Korean Stocks</b>\n"
            "/evaluate - Start evaluating holdings\n"
            "/report - Request detailed analysis report\n"
            "/history - Check analysis history for specific stocks\n\n"
            "🇺🇸 <b>US Stocks</b>\n"
            "/us_evaluate - Start evaluating US stocks\n"
            "/us_report - Request US stock report\n\n"
            "📝 <b>Investment Journal</b>\n"
            "/journal - Record investment journal\n"
            "/memories - Check my memory storage\n\n"
            "📡 <b>Trigger Reliability</b>\n"
            "/triggers - View trigger reliability report\n\n"
            "💡 You can ask additional questions by replying to the evaluation response!\n\n"
            "This bot is available only to subscribers of the 'Prism Insight' channel.\n"
            "The channel introduces 3 featured stocks selected by AI at market open and close,\n"
            "and provides high-quality detailed analysis reports written by AI agents for each stock.\n\n"
            "Please subscribe via the following link before using the bot: https://t.me/stock_ai_agent",
            parse_mode="HTML"
        )

    @staticmethod
    async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle help command"""
        await update.message.reply_text(
            "📊 <b>Prism Advisor Bot Help</b> 📊\n\n"
            "<b>Basic Commands:</b>\n"
            "/start - Start bot\n"
            "/help - View help\n"
            "/cancel - Cancel current conversation\n\n"
            "🇰🇷 <b>Korean Stock Commands:</b>\n"
            "/evaluate - Start evaluating holdings\n"
            "/report - Request detailed analysis report\n"
            "/history - Check analysis history for specific stocks\n\n"
            "🇺🇸 <b>US Stock Commands:</b>\n"
            "/us_evaluate - Start evaluating US stocks\n"
            "/us_report - Request US stock report\n\n"
            "📝 <b>Investment Journal:</b>\n"
            "/journal - Record investment thoughts\n"
            "/memories - Check my memory storage\n"
            "  • Can be entered with stock code/ticker\n"
            "  • Used as memory in past evaluations\n\n"
            "📡 <b>Trigger Reliability:</b>\n"
            "/triggers - View trigger reliability report for KR & US\n\n"
            "<b>How to Evaluate Holdings (Same for Korea/US):</b>\n"
            "1. Enter /evaluate or /us_evaluate command\n"
            "2. Enter stock code/ticker (e.g., 005930 or AAPL)\n"
            "3. Enter average purchase price (KRW or USD)\n"
            "4. Enter holding period\n"
            "5. Enter desired feedback style\n"
            "6. Enter trading background (optional)\n"
            "7. 💡 Can ask additional questions by replying to AI response!\n\n"
            "<b>✨ Follow-up Question Feature:</b>\n"
            "• Ask additional questions by replying to AI's evaluation message\n"
            "• Continuous conversation with previous context maintained\n"
            "• Conversation session maintained for 24 hours\n\n"
            "<b>Request Detailed Analysis Report:</b>\n"
            "1. Enter /report command\n"
            "2. Enter stock code or name\n"
            "3. Detailed report will be provided in 5-10 minutes (longer if many requests)\n\n"
            "<b>Note:</b>\n"
            "This bot is available only to channel subscribers.",
            parse_mode="HTML"
        )

    async def handle_memories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle my memories lookup command"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        try:
            # Query memory statistics
            stats = self.memory_manager.get_memory_stats(user_id)

            if not stats or stats.get('total', 0) == 0:
                await update.message.reply_text(
                    f"📭 No stored memories for {user_name}.\n\n"
                    "Try recording your investment diary with the /journal command!",
                    parse_mode="HTML"
                )
                return

            # Query memory list
            memories = self.memory_manager.get_memories(user_id, limit=20)

            # Create response message
            msg_parts = [f"🧠 <b>{user_name}'s Memory Storage</b>\n"]

            # Statistics
            by_type = stats.get('by_type', {})
            msg_parts.append(f"\n📊 <b>Stored Memories: {stats.get('total', 0)}</b>")
            if by_type:
                type_labels = {
                    'journal': '📝 Journal',
                    'evaluation': '📈 Evaluation',
                    'report': '📋 Report',
                    'conversation': '💬 Conversation'
                }
                for mem_type, count in by_type.items():
                    label = type_labels.get(mem_type, mem_type)
                    msg_parts.append(f"  • {label}: {count}")

            # Statistics by ticker
            by_ticker = stats.get('by_ticker', {})
            if by_ticker:
                msg_parts.append(f"\n🏷️ <b>Records by Ticker:</b>")
                for ticker, count in list(by_ticker.items())[:5]:
                    msg_parts.append(f"  • {ticker}: {count}")

            # Recent memory details
            msg_parts.append(f"\n\n📜 <b>Recent Memories (max 10):</b>\n")
            for i, mem in enumerate(memories[:10], 1):
                created = mem.get('created_at', '')[:10]
                mem_type = mem.get('memory_type', '')
                ticker = mem.get('ticker', '')
                ticker_name = mem.get('ticker_name', '')
                content = mem.get('content', {})

                # Content preview (100 chars)
                text = content.get('text', content.get('response_summary', ''))[:100]
                if len(text) >= 100:
                    text = text[:97] + "..."

                # Display ticker
                ticker_str = f" [{ticker_name or ticker}]" if ticker else ""

                # Type emoji
                type_emoji = {'journal': '📝', 'evaluation': '📈', 'report': '📋', 'conversation': '💬'}.get(mem_type, '💭')

                msg_parts.append(f"{i}. {type_emoji} {created}{ticker_str}")
                if text:
                    msg_parts.append(f"   <i>{text}</i>")
                msg_parts.append("")

            response = "\n".join(msg_parts)

            # Message length limit (4096 chars)
            if len(response) > 4000:
                response = response[:3997] + "..."

            await update.message.reply_text(response, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error in handle_memories: {e}", exc_info=True)
            await update.message.reply_text(
                "⚠️ An error occurred while retrieving memories. Please try again later."
            )

    async def handle_triggers(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /triggers command — show trigger reliability report"""
        try:
            db_path = str(Path(__file__).parent / "stock_tracking_db.sqlite")
            message = generate_triggers_message(db_path)
            await update.message.reply_text(message, parse_mode="HTML")
        except Exception as e:
            logger.error(f"Error in /triggers: {e}")
            await update.message.reply_text("트리거 신뢰도 데이터를 불러오는 중 오류가 발생했습니다.")

    async def handle_report_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle report command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check daily usage limit
        if not self.check_daily_limit(user_id, "report"):
            await update.message.reply_text(
                "⚠️ The /report command can only be used once per day.\n\n"
                "Please try again tomorrow."
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}Please enter the stock code or name to generate a detailed analysis report.\n"
            "Example: 005930 or Samsung Electronics"
        )

        return REPORT_CHOOSING_TICKER

    async def handle_report_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle stock input for report request"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        chat_id = update.effective_chat.id

        logger.info(f"Received report stock input - User: {user_id}, Input: {user_input}")

        # Process stock code or name
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # Notify user of error and request re-input
            await update.message.reply_text(error_message)
            return REPORT_CHOOSING_TICKER

        # Send waiting message
        waiting_message = await update.message.reply_text(
            f"📊 Analysis report generation request for {stock_name} ({stock_code}) has been registered.\n\n"
            f"Requests are processed in the order received, and each analysis takes approximately 5-10 minutes.\n\n"
            f"Wait times may be longer if there are many requests from other users.\n\n "
            f"We will notify you as soon as it's complete."
        )

        # Create analysis request and add to queue
        request = AnalysisRequest(
            stock_code=stock_code,
            company_name=stock_name,
            chat_id=chat_id,
            message_id=waiting_message.message_id
        )

        # Check if cached report exists
        is_cached, cached_content, cached_file, cached_pdf = get_cached_report(stock_code)

        if is_cached:
            logger.info(f"Found cached report: {cached_file}")
            # Send result immediately if cached report exists
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.pdf_path = cached_pdf

            await waiting_message.edit_text(
                f"✅ Analysis report for {stock_name} ({stock_code}) is ready. Will be sent shortly."
            )

            # Send result
            await self.send_report_result(request)
        else:
            # New analysis needed
            self.pending_requests[request.id] = request
            analysis_queue.put(request)

        return ConversationHandler.END

    async def handle_history_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle history command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}Please enter the stock code or name to check analysis history.\n"
            "Example: 005930 or Samsung Electronics"
        )

        return HISTORY_CHOOSING_TICKER

    async def handle_history_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle stock input for history request"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()

        logger.info(f"Received history stock input - User: {user_id}, Input: {user_input}")

        # Process stock code or name
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # Notify user of error and request re-input
            await update.message.reply_text(error_message)
            return HISTORY_CHOOSING_TICKER

        # Find history
        reports = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

        if not reports:
            await update.message.reply_text(
                f"No analysis history for {stock_name} ({stock_code}).\n"
                f"Try using the /report command to request a new analysis."
            )
            return ConversationHandler.END

        # Sort by date
        reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # Compose history message
        history_msg = f"📋 Analysis History for {stock_name} ({stock_code}):\n\n"

        for i, report in enumerate(reports[:5], 1):
            report_date = datetime.fromtimestamp(report.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            history_msg += f"{i}. {report_date}\n"

            # Add file size
            file_size = report.stat().st_size / 1024  # KB
            history_msg += f"   Size: {file_size:.1f} KB\n"

            # Add first line preview
            try:
                with open(report, 'r', encoding='utf-8') as f:
                    first_line = next(f, "").strip()
                    if first_line:
                        preview = first_line[:50] + "..." if len(first_line) > 50 else first_line
                        history_msg += f"   Preview: {preview}\n"
            except Exception:
                pass

            history_msg += "\n"

        if len(reports) > 5:
            history_msg += f"Plus {len(reports) - 5} more analysis records.\n"

        history_msg += "\nUse the /report command to check the latest analysis report."

        await update.message.reply_text(history_msg)
        return ConversationHandler.END

    async def check_channel_subscription(self, user_id):
        """
        Check if user is subscribed to the channel

        Args:
            user_id: User ID

        Returns:
            bool: Subscription status
        """
        try:
            # Always return true if Channel ID is not configured
            if not self.channel_id:
                return True

            # Admin ID allowlist
            admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
            admin_ids = [int(id_str) for id_str in admin_ids_str.split(",") if id_str.strip()]

            # Always allow if admin
            if user_id in admin_ids:
                logger.info(f"Admin {user_id} access granted")
                return True

            member = await self.application.bot.get_chat_member(
                self.channel_id, user_id
            )
            # Add status check and logging
            logger.info(f"User {user_id} channel membership status: {member.status}")

            # Allow channel members, admins, creators/owners
            # 'creator' is used in early versions, some versions may change to 'owner'
            valid_statuses = ['member', 'administrator', 'creator', 'owner']

            # Always allow if channel owner
            if member.status == 'creator' or getattr(member, 'is_owner', False):
                return True

            return member.status in valid_statuses
        except Exception as e:
            logger.error(f"Error checking channel subscription: {e}")
            # Log exception details for debugging
            logger.error(f"Detailed error: {traceback.format_exc()}")
            return False

    async def handle_evaluate_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle evaluate command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]

        logger.info(f"Evaluation command started - User: {user_name}, Chat type: {'group' if is_group else 'private'}")

        # Mention username in group chats
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}Please enter the code or name of your holdings. \n"
            "Example: 005930 or Samsung Electronics"
        )
        return CHOOSING_TICKER

    async def handle_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle ticker input"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        logger.info(f"Received ticker input - User: {user_id}, Input: {user_input}")

        # Process stock code or name
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # Notify user of error and request re-input
            await update.message.reply_text(error_message)
            return CHOOSING_TICKER

        # Save stock information
        context.user_data['ticker'] = stock_code
        context.user_data['ticker_name'] = stock_name

        logger.info(f"Stock selected: {stock_name} ({stock_code})")

        await update.message.reply_text(
            f"You have selected {stock_name} ({stock_code}).\n\n"
            f"Please enter your average purchase price. (Numbers only)\n"
            f"Example: 68500"
        )

        logger.info(f"State transition: ENTERING_AVGPRICE - User: {user_id}")
        return ENTERING_AVGPRICE

    @staticmethod
    async def handle_avgprice_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle average price input"""
        try:
            avg_price = float(update.message.text.strip().replace(',', ''))
            context.user_data['avg_price'] = avg_price

            await update.message.reply_text(
                f"Please enter the holding period. (in months)\n"
                f"Example: 6 (for 6 months)"
            )
            return ENTERING_PERIOD

        except ValueError:
            await update.message.reply_text(
                "Please enter in number format. Exclude commas.\n"
                "Example: 68500"
            )
            return ENTERING_AVGPRICE

    @staticmethod
    async def handle_period_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle holding period input"""
        try:
            period = int(update.message.text.strip())
            context.user_data['period'] = period

            # Next step: Receive desired feedback style/tone input
            await update.message.reply_text(
                "In what style or tone would you like to receive feedback?\n"
                "Example: Honest, Professional, Like a friend, Concise, etc."
            )
            return ENTERING_TONE

        except ValueError:
            await update.message.reply_text(
                "Please enter in number format.\n"
                "Example: 6"
            )
            return ENTERING_PERIOD

    @staticmethod
    async def handle_tone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle desired feedback style/tone input"""
        tone = update.message.text.strip()
        context.user_data['tone'] = tone

        await update.message.reply_text(
            "If you have any background on why you traded this stock or major trading history, please let us know.\n"
            "(Optional, enter 'None' if not applicable)"
        )
        return ENTERING_BACKGROUND

    async def handle_background_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle trading background input and generate AI response"""
        background = update.message.text.strip()
        context.user_data['background'] = background if background.lower() != 'none' else ""

        # Waiting response message
        waiting_message = await update.message.reply_text(
            "Analyzing the stock... Please wait a moment."
        )

        # Request analysis from AI agent
        ticker = context.user_data['ticker']
        ticker_name = context.user_data.get('ticker_name', f"Stock_{ticker}")
        avg_price = context.user_data['avg_price']
        period = context.user_data['period']
        tone = context.user_data['tone']
        background = context.user_data['background']
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        try:
            # Query user memory context
            memory_context = ""
            if self.memory_manager:
                memory_context = self.memory_manager.build_llm_context(
                    user_id=user_id,
                    ticker=ticker,
                    max_tokens=4000
                )
                if memory_context:
                    logger.info(f"User memory context loaded: {len(memory_context)} chars")

            # Generate AI response (including memory_context)
            response = await generate_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background,
                memory_context=memory_context
            )

            # Check if response is empty
            if not response or not response.strip():
                response = "Sorry, an error occurred while generating the response. Please try again."
                logger.error(f"Empty response generated: {ticker_name}({ticker})")

            # Delete waiting message
            await waiting_message.delete()

            # Send response
            sent_message = await update.message.reply_text(
                response + "\n\n💡 If you have additional questions, please reply to this message."
            )
            
            # Save conversation context
            conv_context = ConversationContext()
            conv_context.message_id = sent_message.message_id
            conv_context.chat_id = chat_id
            conv_context.user_id = update.effective_user.id
            conv_context.ticker = ticker
            conv_context.ticker_name = ticker_name
            conv_context.avg_price = avg_price
            conv_context.period = period
            conv_context.tone = tone
            conv_context.background = background
            conv_context.add_to_history("assistant", response)
            
            # Save context
            self.conversation_contexts[sent_message.message_id] = conv_context
            logger.info(f"Conversation context saved: Message ID {sent_message.message_id}")

            # Save evaluation result to user memory
            if self.memory_manager:
                self.memory_manager.save_memory(
                    user_id=user_id,
                    memory_type=self.memory_manager.MEMORY_EVALUATION,
                    content={
                        'ticker': ticker,
                        'ticker_name': ticker_name,
                        'avg_price': avg_price,
                        'period': period,
                        'tone': tone,
                        'background': background,
                        'response_summary': response[:500]  # Save response summary
                    },
                    ticker=ticker,
                    ticker_name=ticker_name,
                    market_type='kr',
                    command_source='/evaluate',
                    message_id=sent_message.message_id
                )
                logger.info(f"Evaluation result saved to memory: user={user_id}, ticker={ticker}")

        except Exception as e:
            logger.error(f"Error generating or sending response: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("Sorry, an error occurred during analysis. Please try again.")

        # End conversation
        return ConversationHandler.END

    @staticmethod
    async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle conversation cancellation (called from within ConversationHandler)"""
        # Initialize user data
        context.user_data.clear()

        await update.message.reply_text(
            "Request has been cancelled.\n\n"
            "🇰🇷 Korean Stocks: /evaluate, /report, /history\n"
            "🇺🇸 US Stocks: /us_evaluate, /us_report"
        )
        return ConversationHandler.END

    @staticmethod
    async def handle_cancel_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle conversation cancellation (called from outside conversation)"""
        await update.message.reply_text(
            "No conversation currently in progress.\n\n"
            "🇰🇷 Korean Stocks: /evaluate, /report, /history\n"
            "🇺🇸 US Stocks: /us_evaluate, /us_report"
        )

    @staticmethod
    async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle error"""
        error_msg = str(context.error)
        logger.error(f"Error occurred: {error_msg}")

        # Error message to show user
        user_msg = "Sorry, an error occurred. Please try again."

        # Handle timeout error
        if "timed out" in error_msg.lower():
            user_msg = "Request processing time exceeded. Please check your network status and try again."
        # Handle permission error
        elif "permission" in error_msg.lower():
            user_msg = "Bot does not have permission to send messages. Please check group settings."
        # Log various error information
        logger.error(f"Error details: {traceback.format_exc()}")

        # Send error response
        if update and update.effective_message:
            await update.effective_message.reply_text(user_msg)

    async def get_stock_code(self, stock_input):
        """
        Convert stock name or code input to stock code

        Args:
            stock_input (str): Stock code or name

        Returns:
            tuple: (stock code, stock name, error message)
        """
        # Input value defense code
        if not stock_input:
            logger.warning("Empty input value passed")
            return None, None, "Please enter a stock name or code."

        if not isinstance(stock_input, str):
            logger.warning(f"Invalid input type: {type(stock_input)}")
            stock_input = str(stock_input)

        original_input = stock_input
        stock_input = stock_input.strip()

        logger.info(f"Stock search started - Input: '{original_input}' -> Cleaned input: '{stock_input}'")

        # Check stock_name_map status
        if not hasattr(self, 'stock_name_map') or self.stock_name_map is None:
            logger.error("stock_name_map is not initialized")
            return None, None, "System error: Stock data not loaded."

        if not isinstance(self.stock_name_map, dict):
            logger.error(f"stock_name_map type error: {type(self.stock_name_map)}")
            return None, None, "System error: Stock data format is invalid."

        logger.info(f"stock_name_map status - Size: {len(self.stock_name_map)}")

        # Check stock_map status
        if not hasattr(self, 'stock_map') or self.stock_map is None:
            logger.warning("stock_map is not initialized")
            self.stock_map = {}

        # If already a stock code (6-digit number)
        if re.match(r'^\d{6}$', stock_input):
            logger.info(f"Recognized as 6-digit numeric code: {stock_input}")
            stock_code = stock_input
            stock_name = self.stock_map.get(stock_code)

            if stock_name:
                logger.info(f"Stock code match successful: {stock_code} -> {stock_name}")
                return stock_code, stock_name, None
            else:
                logger.warning(f"No name information for stock code {stock_code}")
                return stock_code, f"Stock_{stock_code}", "No information for this stock code. Please verify the code is correct."

        # If entered as stock name - check for exact match
        logger.info(f"Starting exact name match search: '{stock_input}'")

        # Log key samples for debugging
        sample_keys = list(self.stock_name_map.keys())[:5]
        logger.debug(f"stock_name_map key samples: {sample_keys}")

        # Exact match check
        if stock_input in self.stock_name_map:
            stock_code = self.stock_name_map[stock_input]
            logger.info(f"Exact match successful: '{stock_input}' -> {stock_code}")
            return stock_code, stock_input, None
        else:
            logger.info(f"Exact match failed: '{stock_input}'")

            # Log input value details
            logger.debug(f"Input details - Length: {len(stock_input)}, "
                         f"Bytes: {stock_input.encode('utf-8')}, "
                         f"Unicode: {[ord(c) for c in stock_input]}")

        # Partial stock name match search
        logger.info(f"Starting partial match search")
        possible_matches = []

        try:
            for name, code in self.stock_name_map.items():
                if not isinstance(name, str) or not isinstance(code, str):
                    logger.warning(f"Invalid data type: name={type(name)}, code={type(code)}")
                    continue

                if stock_input.lower() in name.lower():
                    possible_matches.append((name, code))
                    logger.debug(f"Partial match found: '{name}' ({code})")

        except Exception as e:
            logger.error(f"Error during partial match search: {e}")
            return None, None, "An error occurred during search."

        logger.info(f"Partial match results: {len(possible_matches)} found")

        if len(possible_matches) == 1:
            # Use if single match found
            stock_name, stock_code = possible_matches[0]
            logger.info(f"Single partial match successful: '{stock_name}' ({stock_code})")
            return stock_code, stock_name, None
        elif len(possible_matches) > 1:
            # Return error message if multiple matches
            logger.info(f"Multiple matches: {[f'{name}({code})' for name, code in possible_matches]}")
            match_info = "\n".join([f"{name} ({code})" for name, code in possible_matches[:5]])
            if len(possible_matches) > 5:
                match_info += f"\n... and {len(possible_matches)-5} more"

            return None, None, f"Multiple stocks match '{stock_input}'. Please enter the exact stock name or code:\n{match_info}"
        else:
            # Return error message if no matches
            logger.warning(f"No matching stock: '{stock_input}'")
            return None, None, f"Cannot find stock matching '{stock_input}'. Please enter the exact stock name or code."

    # US ticker validation cache
    _us_ticker_cache: dict = {}

    async def validate_us_ticker(self, ticker_input: str) -> tuple:
        """
        Validate US stock ticker symbol

        Args:
            ticker_input (str): Ticker symbol (e.g., AAPL, MSFT, GOOGL)

        Returns:
            tuple: (ticker, company_name, error_message)
        """
        if not ticker_input:
            return None, None, "Please enter a ticker symbol. (e.g., AAPL, MSFT)"

        ticker = ticker_input.strip().upper()
        logger.info(f"Starting US ticker validation: {ticker}")

        # Check cache
        if ticker in self._us_ticker_cache:
            cached = self._us_ticker_cache[ticker]
            logger.info(f"Using cached US ticker info: {ticker} -> {cached['name']}")
            return ticker, cached['name'], None

        # Validate ticker format (1-5 letter alphabets)
        if not re.match(r'^[A-Z]{1,5}$', ticker):
            return None, None, (
                f"'{ticker_input}' is not a valid US ticker format.\n"
                "US tickers are 1-5 letter alphabets. (Examples: AAPL, MSFT, GOOGL)"
            )

        # Validate ticker with yfinance
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            info = stock.info

            # Extract company name
            company_name = info.get('longName') or info.get('shortName')

            if not company_name:
                return None, None, (
                    f"Cannot find information for ticker '{ticker}'.\n"
                    "Please verify the ticker symbol is correct."
                )

            # Save to cache
            self._us_ticker_cache[ticker] = {'name': company_name}
            logger.info(f"US ticker validation successful: {ticker} -> {company_name}")

            return ticker, company_name, None

        except Exception as e:
            logger.error(f"Error validating US ticker: {e}")
            # Default handling if yfinance is missing or error occurs
            return ticker, f"{ticker} (unverified)", None

    # ==========================================================================
    # US stock evaluation handler (/us_evaluate)
    # ==========================================================================

    async def handle_us_evaluate_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US evaluate command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]

        logger.info(f"US evaluation command started - User: {user_name}, Chat type: {'group' if is_group else 'private'}")

        # Mention username in group chats
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}🇺🇸 Starting US stock evaluation.\n\n"
            "Please enter the ticker symbol of your holdings.\n"
            "Example: AAPL, MSFT, GOOGL, NVDA"
        )
        return US_CHOOSING_TICKER

    async def handle_us_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US ticker input"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        logger.info(f"Received US ticker input - User: {user_id}, Input: {user_input}")

        # Validate ticker
        ticker, company_name, error_message = await self.validate_us_ticker(user_input)

        if error_message:
            await update.message.reply_text(error_message)
            return US_CHOOSING_TICKER

        # Save stock information
        context.user_data['us_ticker'] = ticker
        context.user_data['us_ticker_name'] = company_name

        logger.info(f"US stock selected: {company_name} ({ticker})")

        await update.message.reply_text(
            f"🇺🇸 You have selected {company_name} ({ticker}).\n\n"
            f"Please enter your average purchase price in USD. (Numbers only)\n"
            f"Example: 150.50"
        )

        logger.info(f"State transition: US_ENTERING_AVGPRICE - User: {user_id}")
        return US_ENTERING_AVGPRICE

    @staticmethod
    async def handle_us_avgprice_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US average purchase price input (USD)"""
        try:
            avg_price = float(update.message.text.strip().replace(',', '').replace('$', ''))
            context.user_data['us_avg_price'] = avg_price

            await update.message.reply_text(
                f"Please enter the holding period. (in months)\n"
                f"Example: 6 (for 6 months)"
            )
            return US_ENTERING_PERIOD

        except ValueError:
            await update.message.reply_text(
                "Please enter in number format. (Example: 150.50)\n"
                "Dollar sign ($) and commas will be automatically removed."
            )
            return US_ENTERING_AVGPRICE

    @staticmethod
    async def handle_us_period_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US holding period input"""
        try:
            period = int(update.message.text.strip())
            context.user_data['us_period'] = period

            await update.message.reply_text(
                "In what style or tone would you like to receive feedback?\n"
                "Example: Honest, Professional, Like a friend, Concise, etc."
            )
            return US_ENTERING_TONE

        except ValueError:
            await update.message.reply_text(
                "Please enter in number format.\n"
                "Example: 6"
            )
            return US_ENTERING_PERIOD

    @staticmethod
    async def handle_us_tone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US feedback style/tone input"""
        tone = update.message.text.strip()
        context.user_data['us_tone'] = tone

        await update.message.reply_text(
            "If you have any background on why you traded this stock or major trading history, please let us know.\n"
            "(Optional, enter 'None' if not applicable)"
        )
        return US_ENTERING_BACKGROUND

    async def handle_us_background_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US trading background input and generate AI response"""
        background = update.message.text.strip()
        context.user_data['us_background'] = background if background.lower() != 'none' else ""

        # Waiting response message
        waiting_message = await update.message.reply_text(
            "🇺🇸 Analyzing US stock... Please wait a moment."
        )

        # Request analysis from AI agent
        ticker = context.user_data['us_ticker']
        ticker_name = context.user_data.get('us_ticker_name', ticker)
        avg_price = context.user_data['us_avg_price']
        period = context.user_data['us_period']
        tone = context.user_data['us_tone']
        background = context.user_data['us_background']
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        try:
            # Query user memory context
            memory_context = ""
            if self.memory_manager:
                memory_context = self.memory_manager.build_llm_context(
                    user_id=user_id,
                    ticker=ticker,
                    max_tokens=4000
                )
                if memory_context:
                    logger.info(f"US user memory context loaded: {len(memory_context)} chars")

            # Generate US AI response (including memory_context)
            response = await generate_us_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background,
                memory_context=memory_context
            )

            # Check if response is empty
            if not response or not response.strip():
                response = "Sorry, an error occurred while generating the response. Please try again."
                logger.error(f"Empty response generated: {ticker_name}({ticker})")

            # Delete waiting message
            await waiting_message.delete()

            # Send response
            sent_message = await update.message.reply_text(
                response + "\n\n💡 If you have additional questions, please reply to this message."
            )

            # Save conversation context (US market)
            conv_context = ConversationContext(market_type="us")
            conv_context.message_id = sent_message.message_id
            conv_context.chat_id = chat_id
            conv_context.user_id = update.effective_user.id
            conv_context.ticker = ticker
            conv_context.ticker_name = ticker_name
            conv_context.avg_price = avg_price
            conv_context.period = period
            conv_context.tone = tone
            conv_context.background = background
            conv_context.add_to_history("assistant", response)

            # Save context
            self.conversation_contexts[sent_message.message_id] = conv_context
            logger.info(f"US conversation context saved: Message ID {sent_message.message_id}")

            # Save evaluation result to user memory
            if self.memory_manager:
                self.memory_manager.save_memory(
                    user_id=user_id,
                    memory_type=self.memory_manager.MEMORY_EVALUATION,
                    content={
                        'ticker': ticker,
                        'ticker_name': ticker_name,
                        'avg_price': avg_price,
                        'period': period,
                        'tone': tone,
                        'background': background,
                        'response_summary': response[:500]  # Save response summary
                    },
                    ticker=ticker,
                    ticker_name=ticker_name,
                    market_type='us',
                    command_source='/us_evaluate',
                    message_id=sent_message.message_id
                )
                logger.info(f"US evaluation result saved to memory: user={user_id}, ticker={ticker}")

        except Exception as e:
            logger.error(f"Error generating or sending US response: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("Sorry, an error occurred during analysis. Please try again.")

        # End conversation
        return ConversationHandler.END

    # ==========================================================================
    # US stock report handler (/us_report)
    # ==========================================================================

    async def handle_us_report_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle US report command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check daily usage limit
        if not self.check_daily_limit(user_id, "us_report"):
            await update.message.reply_text(
                "⚠️ The /us_report command can only be used once per day.\n\n"
                "Please try again tomorrow."
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}🇺🇸 This is a US stock report request.\n\n"
            "Please enter the ticker symbol of the stock to analyze.\n"
            "Example: AAPL, MSFT, GOOGL, NVDA"
        )

        return US_REPORT_CHOOSING_TICKER

    async def handle_us_report_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle ticker input for US report request"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        chat_id = update.effective_chat.id

        logger.info(f"Received US report ticker input - User: {user_id}, Input: {user_input}")

        # Validate ticker
        ticker, company_name, error_message = await self.validate_us_ticker(user_input)

        if error_message:
            await update.message.reply_text(error_message)
            return US_REPORT_CHOOSING_TICKER

        # Send waiting message
        waiting_message = await update.message.reply_text(
            f"🇺🇸 Analysis report generation request for {company_name} ({ticker}) has been registered.\n\n"
            f"Requests are processed in the order received, and each analysis takes approximately 5-10 minutes.\n\n"
            f"Wait times may be longer if there are many requests from other users.\n\n"
            f"We will notify you as soon as it's complete."
        )

        # Create US analysis request and add to queue
        request = AnalysisRequest(
            stock_code=ticker,
            company_name=company_name,
            chat_id=chat_id,
            message_id=waiting_message.message_id,
            market_type="us"  # Explicitly mark as US stock
        )

        # Check if cached US report exists
        is_cached, cached_content, cached_file, cached_pdf = get_cached_us_report(ticker)

        if is_cached:
            logger.info(f"Found cached US report: {cached_file}")
            # Send result immediately if cached report exists
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.pdf_path = cached_pdf

            await waiting_message.edit_text(
                f"✅ Analysis report for {company_name} ({ticker}) is ready. Will be sent shortly."
            )

            # Send result
            await self.send_report_result(request)
        else:
            # New analysis needed - add to queue
            self.pending_requests[request.id] = request
            analysis_queue.put(request)

        return ConversationHandler.END

    # ==========================================================================
    # Journal (investment diary) handler (/journal)
    # ==========================================================================

    async def handle_journal_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle journal command - first step"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        logger.info(f"[JOURNAL] handle_journal_start - user_id: {user_id}, chat_id: {chat_id}, chat_type: {chat_type}")

        # Check channel subscription
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "This bot is available only to channel subscribers.\n"
                "Please subscribe to the channel via the link below:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # Check if group chat or private chat
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}📝 Please write your investment journal.\n\n"
            "If you enter with a stock code/ticker, it will be linked to that stock:\n"
            "Example: \"AAPL planning to hold until $170\"\n"
            "Example: \"005930 judging semiconductor bottom\"\n\n"
            "Or just freely write your thoughts."
        )

        logger.info(f"[JOURNAL] Transitioned to JOURNAL_ENTERING state - user_id: {user_id}")
        return JOURNAL_ENTERING

    async def handle_journal_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle journal input"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text.strip()

        logger.info(f"[JOURNAL] handle_journal_input called - user_id: {user_id}, chat_id: {chat_id}")
        logger.info(f"[JOURNAL] Received journal input - User: {user_id}, Input: {text[:50]}...")

        # Extract ticker (regex)
        ticker, ticker_name, market_type = self._extract_ticker_from_text(text)

        # Save memory
        memory_id = self.memory_manager.save_journal(
            user_id=user_id,
            text=text,
            ticker=ticker,
            ticker_name=ticker_name,
            market_type=market_type,
            message_id=update.message.message_id
        )

        # Compose confirmation message
        # Add notice if over 500 characters
        length_note = ""
        if len(text) > 500:
            length_note = f"\n⚠️ Note: Only the first 500 characters will be referenced in AI conversations. (Current: {len(text)} characters)"

        if ticker:
            confirm_msg = (
                f"✅ Recorded in journal!\n\n"
                f"📝 Stock: {ticker_name} ({ticker})\n"
                f"💭 \"{text[:100]}{'...' if len(text) > 100 else ''}\"\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f"{length_note}\n\n"
                f"💡 Reply to this message to continue the conversation!"
            )
        else:
            confirm_msg = (
                f"✅ Recorded in journal!\n\n"
                f"💭 \"{text[:100]}{'...' if len(text) > 100 else ''}\"\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f"{length_note}\n\n"
                f"💡 Reply to this message to continue the conversation!"
            )

        sent_message = await update.message.reply_text(confirm_msg)

        # Save journal context (for replies - AI conversation support)
        self.journal_contexts[sent_message.message_id] = {
            'user_id': user_id,
            'ticker': ticker,
            'ticker_name': ticker_name,
            'market_type': market_type,
            'conversation_history': [],  # AI conversation history
            'created_at': datetime.now()
        }

        logger.info(f"Journal saved: user={user_id}, ticker={ticker}, memory_id={memory_id}")

        return ConversationHandler.END

    async def _handle_journal_reply(self, update: Update, journal_ctx: Dict):
        """Handle replies to journal messages - AI conversation feature"""
        user_id = update.effective_user.id
        text = update.message.text.strip()

        logger.info(f"[JOURNAL_REPLY] Processing journal conversation - user_id: {user_id}, text: {text[:50]}...")

        # Check context expiration (extended to 30 minutes - conversation continuity)
        created_at = journal_ctx.get('created_at')
        if created_at and (datetime.now() - created_at).total_seconds() > 1800:
            await update.message.reply_text(
                "The previous conversation session has expired.\n"
                "Please use the /journal command to start a new conversation. 💭"
            )
            return

        # Get ticker information (if available)
        ticker = journal_ctx.get('ticker')
        ticker_name = journal_ctx.get('ticker_name')
        market_type = journal_ctx.get('market_type', 'kr')
        conversation_history = journal_ctx.get('conversation_history', [])

        # Waiting message
        waiting_message = await update.message.reply_text(
            "💭 Thinking..."
        )

        try:
            # Build user memory context (also load stocks mentioned in current message)
            memory_context = self.memory_manager.build_llm_context(
                user_id=user_id,
                ticker=ticker,
                max_tokens=4000,
                user_message=text  # For extracting ticker from current message
            )

            # Add user message to conversation history
            conversation_history.append({'role': 'user', 'content': text})

            # Generate AI response
            response = await generate_journal_conversation_response(
                user_id=user_id,
                user_message=text,
                memory_context=memory_context,
                ticker=ticker,
                ticker_name=ticker_name,
                conversation_history=conversation_history
            )

            # Delete waiting message
            await waiting_message.delete()

            # Send response
            sent_message = await update.message.reply_text(
                response + "\n\n💡 Continue the conversation with a reply!"
            )

            # Add AI response to conversation history
            conversation_history.append({'role': 'assistant', 'content': response})

            # Update context with new message ID
            self.journal_contexts[sent_message.message_id] = {
                'user_id': user_id,
                'ticker': ticker,
                'ticker_name': ticker_name,
                'market_type': market_type,
                'conversation_history': conversation_history,
                'created_at': datetime.now()
            }

            # Save user message to journal (optional)
            self.memory_manager.save_journal(
                user_id=user_id,
                text=text,
                ticker=ticker,
                ticker_name=ticker_name,
                market_type=market_type,
                message_id=update.message.message_id
            )

            logger.info(f"[JOURNAL_REPLY] AI conversation response complete: user={user_id}, response_len={len(response)}")

        except Exception as e:
            logger.error(f"[JOURNAL_REPLY] Error: {e}")
            await waiting_message.delete()
            await update.message.reply_text(
                "Sorry, there was a problem generating the response. Could you please try again? 💭"
            )

    def _extract_ticker_from_text(self, text: str) -> tuple:
        """
        Extract ticker/stock code from text

        Args:
            text: Input text

        Returns:
            tuple: (ticker, ticker_name, market_type)

        Note:
            Check Korean stocks first (Korean stocks are more common in Korean text)
        """
        # Korean stock code pattern (6-digit number)
        kr_pattern = r'\b(\d{6})\b'
        # US ticker pattern (1-5 uppercase letters, word boundary)
        us_pattern = r'\b([A-Z]{1,5})\b'

        # 1. Check Korean stock code first (priority)
        kr_matches = re.findall(kr_pattern, text)
        for code in kr_matches:
            if code in self.stock_map:
                return code, self.stock_map[code], 'kr'

        # 2. Find Korean stock name (search in stock_name_map)
        for name, code in self.stock_name_map.items():
            if name in text:
                return code, name, 'kr'

        # 3. Find US ticker (only when no Korean stock found)
        # Exclude words: common English words + financial terms
        excluded_words = {
            # Common English words
            'I', 'A', 'AN', 'THE', 'IN', 'ON', 'AT', 'TO', 'FOR', 'OF',
            'AND', 'OR', 'IS', 'IT', 'AI', 'AM', 'PM', 'VS', 'OK', 'NO',
            'IF', 'AS', 'BY', 'SO', 'UP', 'BE', 'WE', 'HE', 'ME', 'MY',
            # Financial indicators/terms
            'PER', 'PBR', 'ROE', 'ROA', 'EPS', 'BPS', 'PSR', 'PCR',
            'EBITDA', 'EBIT', 'YOY', 'QOQ', 'MOM', 'YTD', 'TTM',
            'PE', 'PS', 'PB', 'EV', 'FCF', 'DCF', 'WACC', 'CAGR',
            'IPO', 'M', 'B', 'K', 'KRW', 'USD', 'EUR', 'JPY', 'CNY',
            # Other abbreviations
            'CEO', 'CFO', 'CTO', 'COO', 'IR', 'PR', 'HR', 'IT', 'AI',
            'HBM', 'DRAM', 'NAND', 'SSD', 'GPU', 'CPU', 'AP', 'PC',
        }

        us_matches = re.findall(us_pattern, text)
        for ticker in us_matches:
            if ticker in excluded_words:
                continue
            # Check cache
            if ticker in self._us_ticker_cache:
                return ticker, self._us_ticker_cache[ticker]['name'], 'us'
            # Validate with yfinance
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                info = stock.info
                company_name = info.get('longName') or info.get('shortName')
                if company_name:
                    self._us_ticker_cache[ticker] = {'name': company_name}
                    return ticker, company_name, 'us'
            except Exception:
                pass

        return None, None, 'kr'

    async def process_results(self):
        """Check items to process from result queue"""
        logger.info("Result processing task started")
        while not self.stop_event.is_set():
            try:
                # Process if queue is not empty
                if not self.result_queue.empty():
                    # Process only one request at a time without internal loop
                    request_id = self.result_queue.get()
                    logger.info(f"Retrieved item from result queue: {request_id}")

                    if request_id in self.pending_requests:
                        request = self.pending_requests[request_id]
                        # Send result (safe because running in main event loop)
                        await self.send_report_result(request)
                        logger.info(f"Result sent successfully: {request.id} ({request.company_name})")
                    else:
                        logger.warning(f"Request ID not in pending_requests: {request_id}")

                    # Mark queue task as complete
                    self.result_queue.task_done()
                
                # Wait briefly (reduce CPU usage)
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"Error processing result: {str(e)}")
                logger.error(traceback.format_exc())

            # Wait briefly
            await asyncio.sleep(1)

    async def run(self):
        """Run bot"""
        # Initialize global MCP App
        try:
            logger.info("Initializing global MCPApp...")
            await get_or_create_global_mcp_app()
            logger.info("Global MCPApp initialization complete")
        except Exception as e:
            logger.error(f"Global MCPApp initialization failed: {e}")
            # Start bot even if initialization fails (can retry later)
        
        # Run bot
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        # Add task for result processing
        asyncio.create_task(self.process_results())

        logger.info("Telegram AI conversational bot has started.")

        try:
            # Keep running until bot is stopped
            # Simple way to wait indefinitely
            await self.stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            # Clean up resources on exit
            logger.info("Bot shutdown started - cleaning up resources...")
            
            # Clean up global MCP App
            try:
                logger.info("Cleaning up global MCPApp...")
                await cleanup_global_mcp_app()
                logger.info("Global MCPApp cleanup complete")
            except Exception as e:
                logger.error(f"Global MCPApp cleanup failed: {e}")
            
            # Stop bot
            await self.application.stop()
            await self.application.shutdown()

            logger.info("Telegram AI conversational bot has stopped.")

async def shutdown(sig, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received signal {sig.name}, shutting down...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

# Main execution section
async def main():
    """
    Main function
    """
    # Set up signal handler
    loop = asyncio.get_event_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def create_signal_handler(sig):
        return lambda: asyncio.create_task(shutdown(sig, loop))

    for s in signals:
        loop.add_signal_handler(s, create_signal_handler(s))

    bot = TelegramAIBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())