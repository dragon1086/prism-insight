#!/usr/bin/env python3
"""
Full pipeline execution script for telegram summary message generation and transmission

1. Search for report files in reports directory
2. Generate telegram summary messages
3. Send messages to telegram channel
"""
import argparse
import asyncio
import logging
import os
import sys
import traceback
from datetime import datetime

from dotenv import load_dotenv

# Load .env file
load_dotenv()

# Logging configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"telegram_pipeline_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Import required functions from telegram_summary_agent.py
from telegram_summary_agent import TelegramSummaryGenerator, process_all_reports

# Import required functions from telegram_bot_agent.py
from telegram_bot_agent import TelegramBotAgent
from prism_core.log_redaction import install as _install_log_redaction  # noqa: E402
_install_log_redaction()

async def run_pipeline(args):
    """
    Execute full pipeline

    Args:
        args: Command line arguments

    Returns:
        bool: Pipeline execution success status
    """
    try:
        # 1. Configuration and initialization
        reports_dir = args.reports_dir
        output_dir = args.output_dir
        sent_dir = args.sent_dir or os.path.join(output_dir, "sent")

        # Set date filter
        date_filter = None
        if args.today:
            date_filter = datetime.now().strftime("%Y%m%d")
        elif args.date:
            date_filter = args.date

        logger.info(f"Pipeline started - Report directory: {reports_dir}, Date filter: {date_filter or 'None'}")

        # 2. Generate telegram summary messages
        if args.generate or args.all:
            logger.info("Starting telegram summary message generation")

            # Process specific PDF report only
            if args.report:
                report_path = args.report
                if not os.path.exists(report_path):
                    logger.error(f"Specified report file does not exist: {report_path}")
                    return False

                generator = TelegramSummaryGenerator()
                await generator.process_report(report_path, output_dir)
            else:
                # Process all reports
                await process_all_reports(
                    reports_dir=reports_dir,
                    output_dir=output_dir,
                    date_filter=date_filter
                )

            logger.info("Telegram summary message generation complete")

        # 3. Send telegram messages
        if args.send or args.all:
            logger.info("Starting telegram message transmission")

            # Check channel ID
            chat_id = args.chat_id or os.environ.get("TELEGRAM_CHANNEL_ID")
            if not chat_id:
                logger.error("Telegram channel ID is required. Provide via environment variable or --chat-id parameter.")
                return False

            # Initialize telegram bot agent
            try:
                bot_agent = TelegramBotAgent(token=args.token)
            except ValueError as e:
                logger.error(f"Telegram bot initialization failed: {e}")
                return False

            # Send specific file only
            if args.file:
                file_path = args.file
                if not os.path.exists(file_path):
                    logger.error(f"Specified message file does not exist: {file_path}")
                    return False

                try:
                    # Read file
                    with open(file_path, 'r', encoding='utf-8') as file:
                        message = file.read()

                    # Send message
                    logger.info(f"Sending message: {os.path.basename(file_path)}")
                    success = await bot_agent.send_message(chat_id, message)

                    if success:
                        logger.info(f"Message sent successfully: {os.path.basename(file_path)}")
                except Exception as e:
                    logger.error(f"Error occurred during message transmission: {e}")
                    return False
            else:
                # Process all messages in directory
                await bot_agent.process_messages_directory(output_dir, chat_id, sent_dir)

            logger.info("Telegram message transmission complete")

        # 4. Result summary
        logger.info("Pipeline execution complete")
        return True

    except Exception as e:
        logger.error(f"Error occurred during pipeline execution: {e}")
        return False

async def main():
    """
    Main function - command line interface
    """
    try:

        parser = argparse.ArgumentParser(description="Telegram summary message generation and transmission pipeline")

        # Common options
        parser.add_argument("--reports-dir", default="reports", help="Directory path where report files are stored")
        parser.add_argument("--output-dir", default="telegram_messages", help="Directory path to save telegram messages")
        parser.add_argument("--sent-dir", help="Directory to move sent files (default: output_dir/sent)")
        parser.add_argument("--date", help="Process only reports from specific date (YYYYMMDD format)")
        parser.add_argument("--today", action="store_true", help="Process only today's reports")

        # Step control
        parser.add_argument("--generate", action="store_true", help="Execute telegram summary message generation only")
        parser.add_argument("--send", action="store_true", help="Execute telegram message transmission only")
        parser.add_argument("--all", action="store_true", help="Execute full pipeline (generation and transmission)")

        # Specific file processing
        parser.add_argument("--report", help="Process specific report file only")
        parser.add_argument("--file", help="Send specific telegram message file only")

        # Telegram settings
        parser.add_argument("--token", help="Telegram bot token (can also be set via environment variable)")
        parser.add_argument("--chat-id", help="Telegram channel ID (can also be set via environment variable)")

        args = parser.parse_args()

        # Set default behavior (behave same as --all if no options specified)
        if not (args.generate or args.send or args.all):
            args.all = True

        # Execute pipeline
        success = await run_pipeline(args)

        # Set exit code
        return 0 if success else 1
    except Exception as e:
        print(f"Error occurred in main() function: {e}")
        traceback.print_exc()
        return 1
    finally:
        print("main() function terminated")


if __name__ == "__main__":
    try:
        print("Script started: run_telegram_pipeline.py")
        exit_code = asyncio.run(main())
        print(f"Script terminated successfully (exit code: {exit_code})")
        sys.exit(exit_code)
    except Exception as e:
        print(f"Error occurred during script execution: {e}")
        traceback.print_exc()
        sys.exit(1)