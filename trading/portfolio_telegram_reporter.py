#!/usr/bin/env python3
"""
Portfolio Telegram Reporter
- Periodically sends account and portfolio status to Telegram
- Can be executed via crontab
"""

import asyncio
import os
import sys
import logging
import datetime
import yaml
from pathlib import Path
from typing import Dict, Any, List
from dotenv import load_dotenv

# Set paths based on current script directory
SCRIPT_DIR = Path(__file__).parent
TRADING_DIR = SCRIPT_DIR

# Add paths for importing trading module
PARENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(TRADING_DIR))

# Load configuration file
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# Import local modules
from trading.domestic_stock_trading import DomesticStockTrading
from telegram_bot_agent import TelegramBotAgent

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(SCRIPT_DIR / 'portfolio_reporter.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load .env file
SCRIPT_DIR = Path(__file__).parent.absolute()  # trading/
PROJECT_ROOT = SCRIPT_DIR.parent              # project_root/
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=str(ENV_FILE))

class PortfolioTelegramReporter:
    """Class for reporting portfolio status to Telegram"""

    # Season 2 constants
    SEASON2_START_DATE = "2025.09.29"
    SEASON2_START_AMOUNT = 9_969_801  # Starting capital in KRW

    def __init__(self, telegram_token: str = None, chat_id: str = None, trading_mode: str = None, broadcast_languages: list = None):
        """
        Initialize

        Args:
            telegram_token: Telegram bot token
            chat_id: Telegram channel ID
            trading_mode: Trading mode ('demo' or 'real', uses yaml config if None)
            broadcast_languages: List of languages to broadcast in parallel (e.g., ['en', 'ja', 'zh'])
        """
        # Telegram configuration
        self.telegram_token = telegram_token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
        self.broadcast_languages = broadcast_languages or []
        self.broadcast_channel_ids = {}

        if not self.telegram_token:
            raise ValueError("Telegram bot token is required. Please provide via environment variable TELEGRAM_BOT_TOKEN or parameter.")

        if not self.chat_id:
            raise ValueError("Telegram channel ID is required. Please provide via environment variable TELEGRAM_CHANNEL_ID or parameter.")

        # Load broadcast channel IDs
        self._load_broadcast_channels()

        # Trading configuration - use yaml default_mode as default value
        self.trading_mode = trading_mode if trading_mode is not None else _cfg["default_mode"]
        self.telegram_bot = TelegramBotAgent(token=self.telegram_token)

        logger.info(f"PortfolioTelegramReporter initialized")
        logger.info(f"Trading mode: {self.trading_mode} (yaml config: {_cfg['default_mode']})")

    def _load_broadcast_channels(self):
        """
        Load telegram channel IDs for broadcast languages
        """
        for lang in self.broadcast_languages:
            lang_upper = lang.upper()
            env_key = f"TELEGRAM_CHANNEL_ID_{lang_upper}"
            channel_id = os.getenv(env_key)

            if channel_id:
                self.broadcast_channel_ids[lang] = channel_id
                logger.info(f"Broadcast channel loaded: {lang} -> {channel_id[:10]}...")
            else:
                logger.warning(f"Broadcast channel ID not configured for language: {lang} (env var: {env_key})")

    def format_currency(self, amount: float) -> str:
        """Format amount in Korean Won"""
        return f"{amount:,.0f}원" if amount else "0원"

    def format_percentage(self, rate: float) -> str:
        """Format percentage"""
        return f"{rate:+.2f}%" if rate else "0.00%"

    def create_portfolio_message(self, portfolio: List[Dict[str, Any]], account_summary: Dict[str, Any]) -> str:
        """
        Generate telegram message based on portfolio and account summary

        Args:
            portfolio: Portfolio data
            account_summary: Account summary data

        Returns:
            Formatted telegram message
        """
        current_time = datetime.datetime.now().strftime("%m/%d %H:%M")
        mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"
        mode_text = "모의투자" if self.trading_mode == "demo" else "실전투자"

        # Header
        message = f"📊 포트폴리오 리포트 {mode_emoji}\n"
        message += f"🕐 {current_time} | {mode_text}\n\n"

        # Season 2 info
        message += f"🏆 *시즌2* (시작일: {self.SEASON2_START_DATE})\n"
        message += f"💵 시작금액: `{self.format_currency(self.SEASON2_START_AMOUNT)}`\n\n"

        # Account summary
        if account_summary:
            total_eval = account_summary.get('total_eval_amount', 0)
            total_profit = account_summary.get('total_profit_amount', 0)
            total_profit_rate = account_summary.get('total_profit_rate', 0)
            deposit = account_summary.get('deposit', 0)  # 예수금 (현금)
            available = account_summary.get('available_amount', 0)  # 주문가능금액

            # Note: total_eval (tot_evlu_amt) already includes deposit in KIS API
            # So total_assets = total_eval (not total_eval + deposit)
            total_assets = total_eval

            # Calculate season 2 profit rate (from start amount)
            season_profit = total_assets - self.SEASON2_START_AMOUNT
            season_profit_rate = (season_profit / self.SEASON2_START_AMOUNT) * 100 if self.SEASON2_START_AMOUNT > 0 else 0

            # Calculate cash ratio (using deposit as cash)
            cash_ratio = (deposit / total_assets * 100) if total_assets > 0 else 0

            # Total assets and season profit
            season_profit_emoji = "📈" if season_profit >= 0 else "📉"
            season_profit_sign = "+" if season_profit >= 0 else ""

            message += f"💰 *총 자산*: `{self.format_currency(total_assets)}`\n"
            message += f"{season_profit_emoji} 시즌 수익: `{season_profit_sign}{self.format_currency(season_profit)}` "
            message += f"({self.format_percentage(season_profit_rate)})\n\n"

            # Holdings profit (separate from season profit)
            holdings_profit_emoji = "📈" if total_profit >= 0 else "📉"
            holdings_profit_sign = "+" if total_profit >= 0 else ""

            message += f"📊 *보유종목 평가손익*: `{holdings_profit_sign}{self.format_currency(total_profit)}` "
            message += f"({self.format_percentage(total_profit_rate)})\n"

            # Cash info (deposit = 예수금, available = 주문가능금액)
            message += f"💳 현금(예수금): `{self.format_currency(deposit)}` (현금비율: {cash_ratio:.1f}%)\n"
            message += "\n"
        else:
            message += "❌ 계좌 정보를 가져올 수 없습니다\n\n"

        # 보유 종목
        if portfolio:
            message += f"📈 보유종목 ({len(portfolio)}개)\n"

            for i, stock in enumerate(portfolio, 1):
                stock_name = stock.get('stock_name', '알 수 없음')
                stock_code = stock.get('stock_code', '')
                quantity = stock.get('quantity', 0)
                current_price = stock.get('current_price', 0)
                profit_amount = stock.get('profit_amount', 0)
                profit_rate = stock.get('profit_rate', 0)
                eval_amount = stock.get('eval_amount', 0)
                avg_price = stock.get('avg_price', 0)

                # 수익률 상태
                if profit_rate > 0:
                    status_emoji = "⬆️"
                elif profit_rate < 0:
                    status_emoji = "⬇️"
                else:
                    status_emoji = "➖"

                profit_sign = "+" if profit_amount >= 0 else ""

                # Stock information
                message += f"\n*{i}. {stock_name}* ({stock_code}) {status_emoji}\n"
                message += f"  평가금액: `{self.format_currency(eval_amount)}`\n"
                message += f"  평균단가: `{self.format_currency(avg_price)}` ({quantity}주)\n"
                message += f"  손익: `{profit_sign}{self.format_currency(profit_amount)}`  |  {self.format_percentage(profit_rate)}\n"

        else:
            message += "📭 *보유종목*: 없음\n\n"

        return message


    async def get_trading_data(self) -> tuple:
        """
        Fetch trading data

        Returns:
            (portfolio, account_summary) tuple
        """
        try:
            trader = DomesticStockTrading(mode=self.trading_mode)

            logger.info("Fetching portfolio data...")
            portfolio = trader.get_portfolio()

            logger.info("Fetching account summary...")
            account_summary = trader.get_account_summary()

            logger.info(f"Data fetch complete: {len(portfolio)} holdings")
            return portfolio, account_summary

        except Exception as e:
            logger.error(f"Error fetching trading data: {str(e)}")
            return [], {}

    async def send_portfolio_report(self) -> bool:
        """
        Send portfolio report to Telegram

        Returns:
            Success status
        """
        try:
            logger.info("Starting portfolio report generation...")

            # Fetch trading data
            portfolio, account_summary = await self.get_trading_data()

            # Generate message
            message = self.create_portfolio_message(portfolio, account_summary)

            logger.info("Sending telegram message...")
            # Send to main channel
            success = await self.telegram_bot.send_message(self.chat_id, message)

            if success:
                logger.info("Portfolio report sent successfully!")
            else:
                logger.error("Failed to send portfolio report!")

            # Send to broadcast channels (non-blocking)
            if self.broadcast_languages:
                asyncio.create_task(self._send_translated_portfolio_report(message))

            return success

        except Exception as e:
            logger.error(f"Error sending portfolio report: {str(e)}")
            return False

    async def _send_translated_portfolio_report(self, original_message: str):
        """
        Send translated portfolio report to additional language channels

        Args:
            original_message: Original Korean message
        """
        try:
            import sys
            from pathlib import Path

            # Add cores directory to path for importing translator agent
            cores_path = Path(__file__).parent.parent / "cores"
            if str(cores_path) not in sys.path:
                sys.path.insert(0, str(cores_path))

            from agents.telegram_translator_agent import translate_telegram_message

            for lang in self.broadcast_languages:
                try:
                    # Get channel ID for this language
                    channel_id = self.broadcast_channel_ids.get(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID configured for language: {lang}")
                        continue

                    logger.info(f"Translating portfolio report to {lang}")

                    # Translate message
                    translated_message = await translate_telegram_message(
                        original_message,
                        model="gpt-5-nano",
                        from_lang="ko",
                        to_lang=lang
                    )

                    # Send translated message
                    success = await self.telegram_bot.send_message(channel_id, translated_message)

                    if success:
                        logger.info(f"Portfolio report sent successfully to {lang} channel")
                    else:
                        logger.error(f"Failed to send portfolio report to {lang} channel")

                except Exception as e:
                    logger.error(f"Error sending portfolio report to {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_translated_portfolio_report: {str(e)}")

    async def send_simple_status(self, status_type: str = "morning") -> bool:
        """
        Send simple status message

        Args:
            status_type: Status type ('morning', 'evening', 'market_close', etc.)

        Returns:
            Success status
        """
        try:
            current_time = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"

            # Status message settings
            status_messages = {
                "morning": "🌅 **장 시작 전 체크**",
                "evening": "🌆 **장 마감 후 정리**",
                "market_close": "🔔 **시장 마감**",
                "weekend": "🏖️ **주말 상태 체크**"
            }

            title = status_messages.get(status_type, "📊 **상태 체크**")

            # Fetch only account summary
            _, account_summary = await self.get_trading_data()

            message = f"{title} {mode_emoji}\n"
            message += f"📅 {current_time}\n\n"

            if account_summary:
                total_eval = account_summary.get('total_eval_amount', 0)
                total_profit = account_summary.get('total_profit_amount', 0)
                total_profit_rate = account_summary.get('total_profit_rate', 0)

                profit_emoji = "📈" if total_profit >= 0 else "📉"

                message += f"💼 총 평가: {self.format_currency(total_eval)}\n"
                message += f"{profit_emoji} 손익: {self.format_currency(total_profit)} ({self.format_percentage(total_profit_rate)})\n"
            else:
                message += "❌ 계좌 정보 조회 실패\n"

            message += "\n🤖 자동 상태 체크"

            success = await self.telegram_bot.send_message(self.chat_id, message)

            if success:
                logger.info(f"{status_type} status message sent successfully!")
                return True
            else:
                logger.error(f"Failed to send {status_type} status message!")
                return False

        except Exception as e:
            logger.error(f"Error sending status message: {str(e)}")
            return False


async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(description="Portfolio Telegram Reporter")
    parser.add_argument("--mode", choices=["demo", "real"],
                       help=f"Trading mode (demo: paper trading, real: live trading, default: {_cfg['default_mode']})")
    parser.add_argument("--type", choices=["full", "simple", "morning", "evening", "market_close", "weekend"],
                       default="full", help="Report type")
    parser.add_argument("--token", help="Telegram bot token")
    parser.add_argument("--chat-id", help="Telegram channel ID")
    parser.add_argument("--broadcast-languages", type=str, default="",
                       help="Additional languages for parallel telegram channel broadcasting (comma-separated, e.g., 'en,ja,zh')")

    args = parser.parse_args()

    # Parse broadcast languages
    broadcast_languages = [lang.strip() for lang in args.broadcast_languages.split(",") if lang.strip()]

    try:
        # Initialize reporter (uses yaml config if mode is None)
        reporter = PortfolioTelegramReporter(
            telegram_token=args.token,
            chat_id=args.chat_id,
            trading_mode=args.mode,  # Uses yaml's default_mode if None
            broadcast_languages=broadcast_languages
        )

        # Execute based on report type
        if args.type == "full":
            success = await reporter.send_portfolio_report()
        else:
            # Simple or specific status message
            status_type = args.type if args.type != "simple" else "morning"
            success = await reporter.send_simple_status(status_type)

        if success:
            logger.info("Program completed successfully")
            sys.exit(0)
        else:
            logger.error("Program completed with failure")
            sys.exit(1)

    except Exception as e:
        logger.error(f"Error during program execution: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
