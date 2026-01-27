#!/usr/bin/env python3
"""
Portfolio Telegram Reporter
- Periodically sends account and portfolio status to Telegram
- Supports both Korean (KR) and US stock portfolios
- Can be executed via crontab
"""

import asyncio
import os
import sys
import logging
import datetime
import yaml
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dotenv import load_dotenv

# Set paths based on current script directory
SCRIPT_DIR = Path(__file__).parent
TRADING_DIR = SCRIPT_DIR

# Add paths for importing trading module
PARENT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(TRADING_DIR))
sys.path.insert(0, str(PARENT_DIR / "prism-us"))

# Load configuration file
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# Import local modules
from trading.domestic_stock_trading import DomesticStockTrading
from telegram_bot_agent import TelegramBotAgent

# Import US trading module (optional - may not be available)
try:
    from trading.us_stock_trading import USStockTrading
    US_TRADING_AVAILABLE = True
except ImportError:
    US_TRADING_AVAILABLE = False

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

    def format_currency(self, amount: float, currency: str = "KRW") -> str:
        """Format amount in specified currency"""
        if currency == "USD":
            return f"${amount:,.2f}" if amount else "$0.00"
        else:  # KRW
            return f"{amount:,.0f}원" if amount else "0원"

    def format_percentage(self, rate: float) -> str:
        """Format percentage"""
        return f"{rate:+.2f}%" if rate else "0.00%"

    def format_currency_with_sign(self, amount: float, currency: str = "KRW") -> str:
        """Format amount with +/- sign"""
        if currency == "USD":
            sign = "+" if amount >= 0 else ""
            return f"{sign}${amount:,.2f}" if amount else "$0.00"
        else:  # KRW
            sign = "+" if amount >= 0 else ""
            return f"{sign}{amount:,.0f}원" if amount else "0원"

    def create_portfolio_message(
        self,
        kr_portfolio: List[Dict[str, Any]],
        kr_account_summary: Dict[str, Any],
        us_portfolio: List[Dict[str, Any]] = None,
        us_account_summary: Dict[str, Any] = None
    ) -> str:
        """
        Generate telegram message based on KR and US portfolio data

        Args:
            kr_portfolio: Korean stock portfolio data
            kr_account_summary: Korean account summary data
            us_portfolio: US stock portfolio data (optional)
            us_account_summary: US account summary data (optional)

        Returns:
            Formatted telegram message
        """
        us_portfolio = us_portfolio or []
        us_account_summary = us_account_summary or {}

        current_time = datetime.datetime.now().strftime("%m/%d %H:%M")
        mode_emoji = "🧪" if self.trading_mode == "demo" else "💰"
        mode_text = "모의투자" if self.trading_mode == "demo" else "실전투자"

        # Header
        message = f"📊 포트폴리오 리포트 {mode_emoji}\n"
        message += f"🕐 {current_time} | {mode_text}\n\n"

        # Season 2 info
        message += f"🏆 *시즌2* (시작일: {self.SEASON2_START_DATE})\n"
        message += f"💵 시작금액: `{self.format_currency(self.SEASON2_START_AMOUNT)}`\n\n"

        # ========== KR Account Summary ==========
        if kr_account_summary:
            total_eval = kr_account_summary.get('total_eval_amount', 0)
            total_profit = kr_account_summary.get('total_profit_amount', 0)
            total_profit_rate = kr_account_summary.get('total_profit_rate', 0)
            deposit = kr_account_summary.get('deposit', 0)
            total_cash = kr_account_summary.get('total_cash', deposit)

            total_assets = total_eval

            # Calculate season 2 profit rate (from start amount)
            season_profit = total_assets - self.SEASON2_START_AMOUNT
            season_profit_rate = (season_profit / self.SEASON2_START_AMOUNT) * 100 if self.SEASON2_START_AMOUNT > 0 else 0

            # Calculate cash ratio
            cash_ratio = (total_cash / total_assets * 100) if total_assets > 0 else 0

            # Total assets and season profit
            season_profit_emoji = "📈" if season_profit >= 0 else "📉"

            message += f"🇰🇷 *한국주식 계좌*\n"
            message += f"💰 총 자산: `{self.format_currency(total_assets)}`\n"
            message += f"{season_profit_emoji} 시즌 수익: `{self.format_currency_with_sign(season_profit)}` "
            message += f"({self.format_percentage(season_profit_rate)})\n"
            message += f"📊 평가손익: `{self.format_currency_with_sign(total_profit)}` "
            message += f"({self.format_percentage(total_profit_rate)})\n"
            message += f"💳 현금: `{self.format_currency(total_cash)}` ({cash_ratio:.1f}%)\n"
        else:
            message += "🇰🇷 *한국주식 계좌*\n"
            message += "❌ 계좌 정보를 가져올 수 없습니다\n"

        message += "\n"

        # ========== US Account Summary ==========
        if us_portfolio or us_account_summary:
            message += f"🇺🇸 *미국주식 계좌*\n"

            if us_account_summary:
                us_total_eval = us_account_summary.get('total_eval_amount', 0)
                us_total_profit = us_account_summary.get('total_profit_amount', 0)
                us_total_profit_rate = us_account_summary.get('total_profit_rate', 0)
                us_cash = us_account_summary.get('usd_cash', 0)
                exchange_rate = us_account_summary.get('exchange_rate', 0)

                # Show stock evaluation if any holdings
                if us_total_eval > 0:
                    profit_emoji = "📈" if us_total_profit >= 0 else "📉"
                    message += f"📊 보유주식: `{self.format_currency(us_total_eval, 'USD')}`\n"
                    message += f"{profit_emoji} 평가손익: `{self.format_currency_with_sign(us_total_profit, 'USD')}` "
                    message += f"({self.format_percentage(us_total_profit_rate)})\n"

                # Always show USD cash
                message += f"💵 USD 현금: `{self.format_currency(us_cash, 'USD')}`"
                if exchange_rate > 0:
                    krw_value = us_cash * exchange_rate
                    message += f" (≈{krw_value:,.0f}원)\n"
                    message += f"📈 환율: `{exchange_rate:,.2f}원/USD`\n"
                else:
                    message += "\n"
            else:
                message += "❌ 계좌 정보를 가져올 수 없습니다\n"

            message += "\n"

        # ========== KR Holdings ==========
        message += "━" * 20 + "\n"

        if kr_portfolio:
            message += f"🇰🇷 *한국 보유종목* ({len(kr_portfolio)}개)\n"

            for i, stock in enumerate(kr_portfolio, 1):
                stock_name = stock.get('stock_name', '알 수 없음')
                stock_code = stock.get('stock_code', '')
                quantity = stock.get('quantity', 0)
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

                # Stock information
                message += f"\n*{i}. {stock_name}* ({stock_code}) {status_emoji}\n"
                message += f"  평가: `{self.format_currency(eval_amount)}`\n"
                message += f"  단가: `{self.format_currency(avg_price)}` ({quantity}주)\n"
                message += f"  손익: `{self.format_currency_with_sign(profit_amount)}`  |  {self.format_percentage(profit_rate)}\n"

        else:
            message += "🇰🇷 *한국 보유종목*: 없음\n"

        # ========== US Holdings ==========
        if us_portfolio:
            message += "\n" + "━" * 20 + "\n"
            message += f"🇺🇸 *미국 보유종목* ({len(us_portfolio)}개)\n"

            for i, stock in enumerate(us_portfolio, 1):
                ticker = stock.get('ticker', '???')
                stock_name = stock.get('stock_name', 'Unknown')
                quantity = stock.get('quantity', 0)
                profit_amount = stock.get('profit_amount', 0)
                profit_rate = stock.get('profit_rate', 0)
                eval_amount = stock.get('eval_amount', 0)
                avg_price = stock.get('avg_price', 0)
                exchange = stock.get('exchange', '')

                # 수익률 상태
                if profit_rate > 0:
                    status_emoji = "⬆️"
                elif profit_rate < 0:
                    status_emoji = "⬇️"
                else:
                    status_emoji = "➖"

                # Stock information
                exchange_tag = f"[{exchange}]" if exchange else ""
                message += f"\n*{i}. {ticker}* {exchange_tag} {status_emoji}\n"
                message += f"  {stock_name[:20]}{'...' if len(stock_name) > 20 else ''}\n"
                message += f"  평가: `{self.format_currency(eval_amount, 'USD')}`\n"
                message += f"  단가: `{self.format_currency(avg_price, 'USD')}` ({quantity}주)\n"
                message += f"  손익: `{self.format_currency_with_sign(profit_amount, 'USD')}`  |  {self.format_percentage(profit_rate)}\n"

        return message


    async def get_trading_data(self) -> Tuple[List, Dict, List, Dict]:
        """
        Fetch trading data for both KR and US markets

        Returns:
            (kr_portfolio, kr_account_summary, us_portfolio, us_account_summary) tuple
        """
        kr_portfolio = []
        kr_account_summary = {}
        us_portfolio = []
        us_account_summary = {}

        # Fetch KR trading data
        try:
            kr_trader = DomesticStockTrading(mode=self.trading_mode)

            logger.info("Fetching KR portfolio data...")
            kr_portfolio = kr_trader.get_portfolio()

            logger.info("Fetching KR account summary...")
            kr_account_summary = kr_trader.get_account_summary() or {}

            logger.info(f"KR data fetch complete: {len(kr_portfolio)} holdings")

        except Exception as e:
            logger.error(f"Error fetching KR trading data: {str(e)}")

        # Fetch US trading data (if available)
        if US_TRADING_AVAILABLE:
            try:
                us_trader = USStockTrading(mode=self.trading_mode)

                logger.info("Fetching US portfolio data...")
                us_portfolio = us_trader.get_portfolio()

                logger.info("Fetching US account summary...")
                us_account_summary = us_trader.get_account_summary() or {}

                logger.info(f"US data fetch complete: {len(us_portfolio)} holdings")

            except Exception as e:
                logger.error(f"Error fetching US trading data: {str(e)}")
        else:
            logger.info("US trading module not available, skipping US portfolio")

        return kr_portfolio, kr_account_summary, us_portfolio, us_account_summary

    async def send_portfolio_report(self) -> bool:
        """
        Send portfolio report to Telegram

        Returns:
            Success status
        """
        try:
            logger.info("Starting portfolio report generation...")

            # Fetch trading data (KR + US)
            kr_portfolio, kr_account_summary, us_portfolio, us_account_summary = await self.get_trading_data()

            # Generate message
            message = self.create_portfolio_message(
                kr_portfolio, kr_account_summary,
                us_portfolio, us_account_summary
            )

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

            # Fetch account summaries (KR + US)
            _, kr_account_summary, _, us_account_summary = await self.get_trading_data()

            message = f"{title} {mode_emoji}\n"
            message += f"📅 {current_time}\n\n"

            # KR account summary
            if kr_account_summary:
                total_eval = kr_account_summary.get('total_eval_amount', 0)
                total_profit = kr_account_summary.get('total_profit_amount', 0)
                total_profit_rate = kr_account_summary.get('total_profit_rate', 0)

                profit_emoji = "📈" if total_profit >= 0 else "📉"

                message += f"🇰🇷 *한국*\n"
                message += f"💼 총 평가: {self.format_currency(total_eval)}\n"
                message += f"{profit_emoji} 손익: {self.format_currency_with_sign(total_profit)} ({self.format_percentage(total_profit_rate)})\n"
            else:
                message += "🇰🇷 ❌ 계좌 정보 조회 실패\n"

            # US account summary
            if us_account_summary:
                us_total_eval = us_account_summary.get('total_eval_amount', 0)
                us_total_profit = us_account_summary.get('total_profit_amount', 0)
                us_total_profit_rate = us_account_summary.get('total_profit_rate', 0)
                us_cash = us_account_summary.get('usd_cash', 0)

                message += f"\n🇺🇸 *미국*\n"
                if us_total_eval > 0:
                    us_profit_emoji = "📈" if us_total_profit >= 0 else "📉"
                    message += f"📊 보유주식: {self.format_currency(us_total_eval, 'USD')}\n"
                    message += f"{us_profit_emoji} 손익: {self.format_currency_with_sign(us_total_profit, 'USD')} ({self.format_percentage(us_total_profit_rate)})\n"
                message += f"💵 USD 현금: {self.format_currency(us_cash, 'USD')}\n"

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
