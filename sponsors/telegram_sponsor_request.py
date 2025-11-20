#!/usr/bin/env python3
"""
GitHub Sponsors Telegram Request Script

Periodically sends sponsor request messages to Telegram channel.
Follows "Coffee Cup" strategy - transparent, non-pressuring approach.

⚠️ IMPORTANT: Best practice is to send sponsor requests 1-2 times per MONTH, not daily.
   Daily messages may feel like pressure. Consider running this weekly or monthly.

Usage:
    # Full sponsor request with operating cost details
    python sponsors/telegram_sponsor_request.py --type full

    # Simple reminder message
    python sponsors/telegram_sponsor_request.py --type simple

    # Monthly report with achievements
    python sponsors/telegram_sponsor_request.py --type monthly

    # Specify channel and language
    python sponsors/telegram_sponsor_request.py --type full --broadcast-languages en,ja,zh

Crontab examples:
    # Weekly on Sunday at 10:00 AM (RECOMMENDED)
    0 10 * * 0 cd /path/to/prism-insight && python3 sponsors/telegram_sponsor_request.py --type simple

    # Monthly on 1st at 9:00 AM (for detailed report)
    0 9 1 * * cd /path/to/prism-insight && python3 sponsors/telegram_sponsor_request.py --type monthly
"""

import asyncio
import os
import sys
import logging
import datetime
import random
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv

# Set paths based on current script directory
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent

# Add paths for importing modules
sys.path.insert(0, str(PROJECT_ROOT))

# Import local modules
from telegram_bot_agent import TelegramBotAgent
from telegram_config import TelegramConfig

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(SCRIPT_DIR / 'sponsor_request.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Load .env file
ENV_FILE = PROJECT_ROOT / ".env"
load_dotenv(dotenv_path=str(ENV_FILE))

# Operating cost details (monthly, in KRW)
MONTHLY_COSTS = {
    "openai_api": 125000,      # OpenAI GPT-4.1, GPT-5
    "anthropic_api": 42000,    # Anthropic Claude Sonnet 4
    "server_infra": 20000,     # Server and infrastructure
    "misc": 13000,             # Domain, monitoring, etc.
}
TOTAL_MONTHLY_COST = sum(MONTHLY_COSTS.values())

# GitHub Sponsors URL
GITHUB_SPONSORS_URL = "https://github.com/sponsors/dragon1086"
TELEGRAM_CHANNEL_URL = "https://t.me/stock_ai_agent"
GITHUB_REPO_URL = "https://github.com/dragon1086/prism-insight"
DASHBOARD_URL = "https://analysis.stocksimulation.kr/"


class SponsorTelegramMessenger:
    """Class for sending sponsor request messages to Telegram"""

    def __init__(
        self,
        telegram_token: str = None,
        chat_id: str = None,
        broadcast_languages: list = None
    ):
        """
        Initialize

        Args:
            telegram_token: Telegram bot token
            chat_id: Telegram channel ID
            broadcast_languages: List of languages to broadcast in parallel
        """
        # Telegram configuration
        self.telegram_config = TelegramConfig(
            use_telegram=True,
            channel_id=chat_id,
            bot_token=telegram_token,
            broadcast_languages=broadcast_languages or []
        )

        # Validate configuration
        self.telegram_config.validate_or_raise()

        # Initialize bot
        self.telegram_bot = TelegramBotAgent(token=self.telegram_config.bot_token)

        logger.info("SponsorTelegramMessenger initialized")
        self.telegram_config.log_status()

    def format_currency(self, amount: int) -> str:
        """Format amount in Korean Won"""
        return f"₩{amount:,}"

    def create_full_sponsor_message(self) -> str:
        """
        Create full sponsor request message with detailed operating costs

        Returns:
            Formatted telegram message
        """
        current_date = datetime.datetime.now().strftime("%Y년 %m월 %d일")

        message = "💙 프리즘 인사이트를 후원해주세요\n\n"

        message += "안녕하세요! 프리즘 인사이트를 운영하는 dragon1086입니다.\n\n"

        # Transparent operating costs
        message += "📊 *투명한 운영 비용 공개*\n"
        message += f"매월 약 `{self.format_currency(TOTAL_MONTHLY_COST)}`의 비용이 발생합니다:\n\n"

        for key, value in MONTHLY_COSTS.items():
            if key == "openai_api":
                label = "OpenAI API (GPT-4.1, GPT-5)"
            elif key == "anthropic_api":
                label = "Anthropic API (Claude Sonnet 4)"
            elif key == "server_infra":
                label = "서버 및 인프라"
            elif key == "misc":
                label = "기타 (도메인, 모니터링 등)"
            else:
                label = key

            message += f"• {label}: `{self.format_currency(value)}`\n"

        message += "\n"

        # Community status
        message += "👥 *현재 커뮤니티*\n"
        message += "• 텔레그램 구독자: 약 450명\n"
        message += "• 활성 사용자: 약 100명\n"
        message += "• 현재 후원자: 8명\n\n"

        # Core message: all features remain free
        message += "✨ *중요한 약속*\n"
        message += "*모든 기능은 앞으로도 계속 무료입니다.*\n\n"

        message += "후원은 서비스를 지속 가능하게 만들어줄 뿐,\n"
        message += "기능 차이를 만들지 않습니다.\n\n"

        # What sponsorship means
        message += "🎯 *후원의 의미*\n"
        message += "• 450명이 무료로 AI 주식 분석을 받을 수 있습니다\n"
        message += "• AI 기술을 더 많은 사람과 공유할 수 있습니다\n"
        message += "• 오픈소스 생태계에 기여합니다\n\n"

        # Sponsor tiers
        message += "☕ *후원 티어*\n"
        message += "• 커피 한 잔: $5/월 (약 ₩7,000)\n"
        message += "• 커피 두 잔: $10/월 (약 ₩14,000)\n"
        message += "• 넉넉한 후원: $20/월 (약 ₩28,000)\n"
        message += "• 일회성 후원도 환영합니다!\n\n"

        # Call to action (non-pressuring)
        message += f"🔗 후원하기: [GitHub Sponsors]({GITHUB_SPONSORS_URL})\n\n"

        message += "커피 한 잔 값으로 응원해주시면\n"
        message += "정말 큰 힘이 됩니다. 감사합니다! 💙\n\n"

        message += f"📅 {current_date}"

        return message

    def create_simple_sponsor_message(self) -> str:
        """
        Create simple sponsor reminder message

        Returns:
            Formatted telegram message
        """
        # Multiple message templates for rotation (to avoid repetition)
        templates = [
            # Template 1: Cost-focused
            (
                "☕ 프리즘 인사이트 운영 안내\n\n"
                f"매월 약 `{self.format_currency(TOTAL_MONTHLY_COST)}`의 API 비용이 발생합니다.\n"
                "현재 8명의 후원자분들이 함께 응원해주고 계십니다.\n\n"
                "모든 기능은 계속 무료로 제공됩니다.\n"
                "후원은 서비스 지속을 위한 응원일 뿐입니다.\n\n"
                f"🔗 후원하기: {GITHUB_SPONSORS_URL}\n\n"
                "감사합니다! 💙"
            ),
            # Template 2: Community-focused
            (
                "💙 함께 만드는 프리즘 인사이트\n\n"
                "450명이 무료로 AI 주식 분석을 받고 있습니다.\n"
                "8분이 커피값으로 서비스를 응원해주고 계십니다.\n\n"
                "작은 응원이 큰 힘이 됩니다.\n"
                "커피 한 잔($5)부터 시작할 수 있습니다.\n\n"
                f"🔗 {GITHUB_SPONSORS_URL}\n\n"
                "모든 기능은 계속 무료입니다!"
            ),
            # Template 3: Achievement-focused
            (
                "📊 프리즘 인사이트 현황\n\n"
                "✅ 누적 수익률: +251%\n"
                "✅ 매일 아침/저녁 신규 리포트\n"
                "✅ 완전 투명한 매매 이력 공개\n\n"
                f"월 운영비 {self.format_currency(TOTAL_MONTHLY_COST)}로 운영 중이며,\n"
                "8분의 후원자분들이 함께해주고 계십니다.\n\n"
                f"🔗 응원하기: {GITHUB_SPONSORS_URL}\n\n"
                "모든 기능은 후원 여부와 관계없이 무료입니다! 💙"
            ),
            # Template 4: Personal story
            (
                "👋 안녕하세요, dragon1086입니다\n\n"
                "18개월 딸을 키우며 틈틈이 운영하고 있습니다.\n"
                f"매월 {self.format_currency(TOTAL_MONTHLY_COST)}의 API 비용이 발생하지만,\n"
                "모든 기능을 계속 무료로 제공하고 있습니다.\n\n"
                "여러분의 커피 한 잔이 큰 힘이 됩니다.\n\n"
                f"🔗 {GITHUB_SPONSORS_URL}\n\n"
                "감사합니다! 💙"
            ),
        ]

        # Rotate message randomly to avoid repetition
        return random.choice(templates)

    def create_monthly_report_message(self) -> str:
        """
        Create monthly report with sponsor request

        Returns:
            Formatted telegram message
        """
        current_month = datetime.datetime.now().strftime("%Y년 %m월")

        message = f"📊 {current_month} 프리즘 인사이트 운영 리포트\n\n"

        message += "안녕하세요! 간단히 현황을 공유드립니다.\n\n"

        # Achievements (placeholder - update with actual data)
        message += "🎯 *이번 달 성과*\n"
        message += "• 시뮬레이터 누적 수익률: +251%\n"
        message += "• 텔레그램 구독자: 450명 (계속 증가 중!)\n"
        message += "• 매일 아침/저녁 신규 분석 리포트 제공\n"
        message += "• 완전 투명한 매매 이력 공개\n\n"

        # Operating status
        message += "💰 *운영 현황*\n"
        message += f"• 이번 달 비용: `{self.format_currency(TOTAL_MONTHLY_COST)}`\n"
        message += "• 현재 후원자: 8명\n"
        message += "• 모든 기능 무료 제공 유지\n\n"

        # Sponsor request
        message += "💙 *후원 안내*\n"
        message += "여러분의 커피 한 잔($5)이 서비스를 지속 가능하게 만듭니다.\n"
        message += "기능 차이는 없으며, 모든 것은 계속 무료입니다!\n\n"

        message += f"🔗 {GITHUB_SPONSORS_URL}\n\n"

        message += "앞으로도 더 나은 서비스로 보답하겠습니다.\n"
        message += "감사합니다! 💙"

        return message

    async def send_sponsor_request(self, message_type: str = "full") -> bool:
        """
        Send sponsor request message to Telegram

        Args:
            message_type: Message type ('full', 'simple', 'monthly')

        Returns:
            Success status
        """
        try:
            logger.info(f"Creating sponsor request message (type: {message_type})...")

            # Create message based on type
            if message_type == "full":
                message = self.create_full_sponsor_message()
            elif message_type == "simple":
                message = self.create_simple_sponsor_message()
            elif message_type == "monthly":
                message = self.create_monthly_report_message()
            else:
                logger.error(f"Unknown message type: {message_type}")
                return False

            logger.info("Sending telegram message to main channel...")
            # Send to main channel
            success = await self.telegram_bot.send_message(
                self.telegram_config.channel_id,
                message
            )

            if success:
                logger.info("Sponsor request message sent successfully!")
            else:
                logger.error("Failed to send sponsor request message!")

            # Send to broadcast channels if configured
            if self.telegram_config.broadcast_languages:
                await self._send_translated_sponsor_request(message)

            return success

        except Exception as e:
            logger.error(f"Error sending sponsor request: {str(e)}")
            return False

    async def _send_translated_sponsor_request(self, original_message: str):
        """
        Send translated sponsor request to additional language channels

        Args:
            original_message: Original Korean message
        """
        try:
            # Add cores directory to path for importing translator agent
            cores_path = PROJECT_ROOT / "cores"
            if str(cores_path) not in sys.path:
                sys.path.insert(0, str(cores_path))

            from agents.telegram_translator_agent import translate_telegram_message

            for lang in self.telegram_config.broadcast_languages:
                try:
                    # Get channel ID for this language
                    channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                    if not channel_id:
                        logger.warning(f"No channel ID configured for language: {lang}")
                        continue

                    logger.info(f"Translating sponsor request to {lang}")

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
                        logger.info(f"Sponsor request sent successfully to {lang} channel")
                    else:
                        logger.error(f"Failed to send sponsor request to {lang} channel")

                except Exception as e:
                    logger.error(f"Error sending sponsor request to {lang}: {str(e)}")

        except Exception as e:
            logger.error(f"Error in _send_translated_sponsor_request: {str(e)}")


async def main():
    """Main function"""
    import argparse

    parser = argparse.ArgumentParser(
        description="GitHub Sponsors Telegram Request Script",
        epilog="""
Examples:
  # Full sponsor request (recommended: monthly)
  python sponsors/telegram_sponsor_request.py --type full

  # Simple reminder (recommended: weekly)
  python sponsors/telegram_sponsor_request.py --type simple

  # Monthly report with achievements
  python sponsors/telegram_sponsor_request.py --type monthly

  # Multi-language broadcast
  python sponsors/telegram_sponsor_request.py --type simple --broadcast-languages en,ja,zh

⚠️ Best practice: Send sponsor requests 1-2 times per MONTH, not daily!
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    parser.add_argument(
        "--type",
        choices=["full", "simple", "monthly"],
        default="simple",
        help="Message type (full: detailed with costs, simple: brief reminder, monthly: monthly report)"
    )
    parser.add_argument(
        "--token",
        help="Telegram bot token (defaults to TELEGRAM_BOT_TOKEN from .env)"
    )
    parser.add_argument(
        "--chat-id",
        help="Telegram channel ID (defaults to TELEGRAM_CHANNEL_ID from .env)"
    )
    parser.add_argument(
        "--broadcast-languages",
        type=str,
        default="",
        help="Additional languages for parallel telegram channel broadcasting (comma-separated, e.g., 'en,ja,zh')"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print message without sending (for testing)"
    )

    args = parser.parse_args()

    # Parse broadcast languages
    broadcast_languages = [lang.strip() for lang in args.broadcast_languages.split(",") if lang.strip()]

    try:
        # Initialize messenger
        messenger = SponsorTelegramMessenger(
            telegram_token=args.token,
            chat_id=args.chat_id,
            broadcast_languages=broadcast_languages
        )

        # Dry run mode: just print the message
        if args.dry_run:
            logger.info("DRY RUN MODE: Message will not be sent")
            if args.type == "full":
                message = messenger.create_full_sponsor_message()
            elif args.type == "simple":
                message = messenger.create_simple_sponsor_message()
            elif args.type == "monthly":
                message = messenger.create_monthly_report_message()

            print("\n" + "="*60)
            print("MESSAGE PREVIEW:")
            print("="*60)
            print(message)
            print("="*60 + "\n")

            logger.info("Dry run completed")
            sys.exit(0)

        # Send sponsor request
        success = await messenger.send_sponsor_request(args.type)

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
