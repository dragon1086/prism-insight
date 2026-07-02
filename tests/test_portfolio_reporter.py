#!/usr/bin/env python3
"""
Portfolio Telegram reporter test script
"""

import asyncio
import os
import sys
import yaml
from pathlib import Path

# Set paths based on current script directory
SCRIPT_DIR = Path(__file__).parent          # tests directory
PROJECT_ROOT = SCRIPT_DIR.parent             # project root (one level up)
TRADING_DIR = PROJECT_ROOT / "trading"

sys.path.insert(0, str(PROJECT_ROOT))       # Add project root to path

# Load config file
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.SafeLoader)

from trading.portfolio_telegram_reporter import PortfolioTelegramReporter


async def test_portfolio_reporter():
    """Portfolio reporter test"""

    print("=== Portfolio Telegram Reporter Test ===")
    print()

    # Check environment variables
    telegram_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")

    print("Environment variable check:")
    print(f"TELEGRAM_BOT_TOKEN: {'✅ Set' if telegram_token else '❌ Not set'}")
    print(f"TELEGRAM_CHANNEL_ID: {'✅ Set' if chat_id else '❌ Not set'}")
    print()

    print("YAML configuration check:")
    print(f"Default trading mode: {_cfg['default_mode']}")
    print(f"Auto trading: {_cfg['auto_trading']}")
    print(f"Config file path: {CONFIG_FILE}")
    print()

    if not telegram_token or not chat_id:
        print("❌ Required environment variables are not set.")
        print("Please set environment variables as follows:")
        print("export TELEGRAM_BOT_TOKEN='your_bot_token'")
        print("export TELEGRAM_CHANNEL_ID='your_chat_id'")
        return False


    try:
        # Initialize reporter (uses yaml's default_mode)
        print("1️⃣ Initializing reporter...")
        reporter = PortfolioTelegramReporter()  # Remove trading_mode parameter to use yaml settings
        print(f"✅ Reporter initialization complete (mode: {reporter.trading_mode})")
        print()

        # Trading data retrieval test
        print("2️⃣ Trading data retrieval test...")
        portfolio, account_summary = await reporter.get_trading_data()

        print(f"   Portfolio stocks count: {len(portfolio)}")
        print(f"   Account summary data: {'✅ Retrieved' if account_summary else '❌ Retrieval failed'}")

        if account_summary:
            total_eval = account_summary.get('total_eval_amount', 0)
            total_profit = account_summary.get('total_profit_amount', 0)
            print(f"   Total valuation: {total_eval:,.0f} KRW")
            print(f"   Total P&L: {total_profit:+,.0f} KRW")
        print()

        # Message generation test
        print("3️⃣ Message generation test...")
        message = reporter.create_portfolio_message(portfolio, account_summary)
        print("✅ Message generation complete")
        print("--- Generated message preview ---")
        print(message[:500] + "..." if len(message) > 500 else message)
        print("--- End of preview ---")
        print()

        # User confirmation
        print("4️⃣ Telegram send test")
        response = input("Do you want to actually send the Telegram message? (y/N): ").strip().lower()

        if response in ['y', 'yes']:
            print("📤 Sending Telegram message...")
            success = await reporter.send_portfolio_report()

            if success:
                print("✅ Telegram message sent successfully!")
            else:
                print("❌ Telegram message send failed!")
                return False
        else:
            print("⏭️ Skipping Telegram send.")

        print()
        print("🎉 All tests completed!")
        return True

    except Exception as e:
        print(f"❌ Error during test: {str(e)}")
        return False


async def test_simple_messages():
    """Simple messages test"""

    print("\n=== Simple Message Test ===")

    try:
        reporter = PortfolioTelegramReporter()  # Use yaml settings
        print(f"Test mode: {reporter.trading_mode}")

        # Test various message types
        message_types = ["morning", "evening", "market_close", "weekend"]

        for msg_type in message_types:
            response = input(f"Do you want to send {msg_type} message? (y/N): ").strip().lower()

            if response in ['y', 'yes']:
                print(f"📤 Sending {msg_type} message...")
                success = await reporter.send_simple_status(msg_type)

                if success:
                    print(f"✅ {msg_type} message sent successfully!")
                else:
                    print(f"❌ {msg_type} message send failed!")

                print()

    except Exception as e:
        print(f"❌ Error during simple message test: {str(e)}")


async def test_both_modes():
    """Test both modes"""

    print("\n=== Both Modes Test ===")

    modes = ["demo", "real"]

    for mode in modes:
        response = input(f"Do you want to test in {mode} mode? (y/N): ").strip().lower()

        if response in ['y', 'yes']:
            try:
                print(f"📊 Testing {mode} mode...")
                reporter = PortfolioTelegramReporter(trading_mode=mode)  # Explicitly specify mode

                portfolio, account_summary = await reporter.get_trading_data()
                print(f"   {mode} mode - Holdings: {len(portfolio)}")

                if account_summary:
                    total_eval = account_summary.get('total_eval_amount', 0)
                    print(f"   {mode} mode - Total valuation: {total_eval:,.0f} KRW")

                # Check if sending
                send_response = input(f"Do you want to send {mode} mode report? (y/N): ").strip().lower()
                if send_response in ['y', 'yes']:
                    success = await reporter.send_portfolio_report()
                    print(f"✅ {mode} mode send {'success' if success else 'failed'}!")

                print()

            except Exception as e:
                print(f"❌ Error during {mode} mode test: {str(e)}")


async def main():
    """Main function"""

    print("Starting portfolio Telegram reporter test.")
    print(f"Project root: {PROJECT_ROOT}")
    print(f"Config file: {CONFIG_FILE}")
    print()

    # Basic test (use yaml settings)
    success = await test_portfolio_reporter()

    if success:
        # Additional tests
        response = input("\nDo you want to test simple messages too? (y/N): ").strip().lower()
        if response in ['y', 'yes']:
            await test_simple_messages()

        # Both modes test
        response = input("\nDo you want to test both modes (demo/real)? (y/N): ").strip().lower()
        if response in ['y', 'yes']:
            await test_both_modes()

    print("\nTest completed.")


if __name__ == "__main__":
    asyncio.run(main())
