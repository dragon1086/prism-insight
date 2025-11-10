#!/usr/bin/env python3
"""
Stock Analysis and Telegram Transmission Orchestrator

Overall Process:
1. Execute time-based (morning/afternoon) trigger batch jobs
2. Generate detailed analysis reports for selected stocks
3. Convert reports to PDF
4. Generate and send telegram channel summary messages
5. Send generated PDF attachments
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# Environment configuration
REPORTS_DIR = Path("reports")
TELEGRAM_MSGS_DIR = Path("telegram_messages")
PDF_REPORTS_DIR = Path("pdf_reports")

# Create directories
REPORTS_DIR.mkdir(exist_ok=True)
TELEGRAM_MSGS_DIR.mkdir(exist_ok=True)
PDF_REPORTS_DIR.mkdir(exist_ok=True)
(TELEGRAM_MSGS_DIR / "sent").mkdir(exist_ok=True)

class StockAnalysisOrchestrator:
    """Stock Analysis and Telegram Transmission Orchestrator"""

    def __init__(self, telegram_config=None):
        """
        Initialize orchestrator

        Args:
            telegram_config: TelegramConfig object (uses default config if None)
        """
        from telegram_config import TelegramConfig

        self.selected_tickers = {}  # Store selected stock information
        self.telegram_config = telegram_config or TelegramConfig(use_telegram=True)

    async def run_trigger_batch(self, mode):
        """
        Execute trigger batch and save results (async version)

        Args:
            mode (str): 'morning' or 'afternoon'

        Returns:
            list: List of selected stock codes
        """
        logger.info(f"Starting trigger batch execution: {mode}")
        try:
            # Execute batch process
            import subprocess

            # Save results to temporary file
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"

            # Execute command - run asynchronously using asyncio.create_subprocess_exec
            import asyncio
            process = await asyncio.create_subprocess_exec(
                sys.executable, "trigger_batch.py", mode, "INFO", "--output", results_file,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # Log output - resolve encoding issues
            if stdout:
                try:
                    stdout_text = stdout.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        stdout_text = stdout.decode('cp949')  # Windows Korean encoding
                    except UnicodeDecodeError:
                        stdout_text = stdout.decode('utf-8', errors='ignore')
                logger.info(f"Batch output:\n{stdout_text}")

            if stderr:
                try:
                    stderr_text = stderr.decode('utf-8')
                except UnicodeDecodeError:
                    try:
                        stderr_text = stderr.decode('cp949')  # Windows Korean encoding
                    except UnicodeDecodeError:
                        stderr_text = stderr.decode('utf-8', errors='ignore')
                logger.warning(f"Batch error:\n{stderr_text}")

            if process.returncode != 0:
                logger.error(f"Batch process failed: exit code {process.returncode}")
                return []

            # Read results file
            if os.path.exists(results_file):
                with open(results_file, 'r', encoding='utf-8') as f:
                    results = json.load(f)

                # Save results
                self.selected_tickers[mode] = results

                # Extract stock codes - modified to match JSON structure
                tickers = []
                ticker_codes = set()  # For duplicate checking

                # Extract stocks by trigger type (excluding metadata)
                for trigger_type, stocks in results.items():
                    if trigger_type != "metadata" and isinstance(stocks, list):
                        for stock in stocks:
                            if isinstance(stock, dict) and 'code' in stock:
                                code = stock['code']
                                if code not in ticker_codes:  # Remove duplicates
                                    ticker_codes.add(code)
                                    tickers.append({
                                        'code': code,
                                        'name': stock.get('name', '')
                                    })

                logger.info(f"Number of selected stocks: {len(tickers)}")
                return tickers
            else:
                logger.error(f"Results file was not created: {results_file}")
                return []

        except Exception as e:
            logger.error(f"Error during trigger batch execution: {str(e)}")
            return []

    async def convert_to_pdf(self, report_paths):
        """
        Convert markdown reports to PDF

        Args:
            report_paths (list): List of markdown report file paths

        Returns:
            list: List of generated PDF file paths
        """
        logger.info(f"Starting PDF conversion for {len(report_paths)} reports")
        pdf_paths = []

        # Import PDF converter module
        from pdf_converter import markdown_to_pdf

        for report_path in report_paths:
            try:
                report_file = Path(report_path)
                pdf_file = PDF_REPORTS_DIR / f"{report_file.stem}.pdf"

                # Convert markdown to PDF
                markdown_to_pdf(report_path, pdf_file, 'pdfkit', add_theme=True, enable_watermark=False)

                logger.info(f"PDF conversion complete: {pdf_file}")
                pdf_paths.append(pdf_file)

            except Exception as e:
                logger.error(f"Error during PDF conversion of {report_path}: {str(e)}")

        return pdf_paths

    async def generate_telegram_messages(self, report_pdf_paths):
        """
        Generate telegram messages

        Args:
            report_pdf_paths (list): List of report file (pdf) paths

        Returns:
            list: List of generated telegram message file paths
        """
        logger.info(f"Starting telegram message generation for {len(report_pdf_paths)} reports")

        # Import telegram summary generator module
        from telegram_summary_agent import TelegramSummaryGenerator

        # Initialize summary generator
        generator = TelegramSummaryGenerator()

        message_paths = []
        for report_pdf_path in report_pdf_paths:
            try:
                # Generate telegram message
                await generator.process_report(str(report_pdf_path), str(TELEGRAM_MSGS_DIR))

                # Estimate generated message file path
                report_file = Path(report_pdf_path)
                ticker = report_file.stem.split('_')[0]
                company_name = report_file.stem.split('_')[1]

                message_path = TELEGRAM_MSGS_DIR / f"{ticker}_{company_name}_telegram.txt"

                if message_path.exists():
                    logger.info(f"Telegram message generation complete: {message_path}")
                    message_paths.append(message_path)
                else:
                    logger.warning(f"Telegram message file not found at expected path: {message_path}")

            except Exception as e:
                logger.error(f"Error during telegram message generation for {report_pdf_path}: {str(e)}")

        return message_paths

    async def send_telegram_messages(self, message_paths, pdf_paths):
        """
        Send telegram messages and PDF files

        Args:
            message_paths (list): List of telegram message file paths
            pdf_paths (list): List of PDF file paths
        """
        # Skip if telegram is disabled
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping message and PDF transmission")
            return

        logger.info(f"Starting telegram message transmission for {len(message_paths)} messages")

        # Use telegram configuration
        chat_id = self.telegram_config.channel_id
        if not chat_id:
            logger.error("Telegram channel ID is not configured.")
            return

        # Initialize telegram bot agent
        from telegram_bot_agent import TelegramBotAgent

        try:
            bot_agent = TelegramBotAgent()

            # Send messages
            await bot_agent.process_messages_directory(
                str(TELEGRAM_MSGS_DIR),
                chat_id,
                str(TELEGRAM_MSGS_DIR / "sent")
            )

            # Send PDF files
            for pdf_path in pdf_paths:
                logger.info(f"Sending PDF file: {pdf_path}")
                success = await bot_agent.send_document(chat_id, str(pdf_path))
                if success:
                    logger.info(f"PDF file transmission successful: {pdf_path}")
                else:
                    logger.error(f"PDF file transmission failed: {pdf_path}")

                # Transmission interval
                await asyncio.sleep(1)

        except Exception as e:
            logger.error(f"Error during telegram message transmission: {str(e)}")

    async def send_trigger_alert(self, mode, trigger_results_file, language: str = "ko"):
        """
        Send trigger execution result information to telegram channel immediately

        Args:
            mode: 'morning' or 'afternoon'
            trigger_results_file: Path to trigger results JSON file
            language: Message language ("ko" or "en")
        """
        # Log and return if telegram is disabled
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping Prism Signal alert transmission (mode: {mode})")
            return False

        logger.info(f"Starting Prism Signal alert transmission - mode: {mode}, language: {language}")

        try:
            # Read JSON file
            with open(trigger_results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)

            # Extract metadata
            metadata = results.get("metadata", {})
            trade_date = metadata.get("trade_date", datetime.now().strftime("%Y%m%d"))

            # Extract trigger stock information - handle direct list case
            all_results = {}
            for key, value in results.items():
                if key != "metadata" and isinstance(value, list):
                    # When value is directly a stock list
                    all_results[key] = value

            if not all_results:
                logger.warning(f"No trigger results found.")
                return False

            # Generate telegram message
            message = self._create_trigger_alert_message(mode, all_results, trade_date)

            # Translate message if English is requested
            if language == "en":
                try:
                    logger.info("Translating trigger alert message to English")
                    from cores.agents.telegram_translator_agent import translate_telegram_message
                    message = await translate_telegram_message(message, model="gpt-5-nano")
                    logger.info("Translation complete")
                except Exception as e:
                    logger.error(f"Translation failed: {str(e)}. Using original Korean message.")

            # Use telegram configuration
            chat_id = self.telegram_config.channel_id
            if not chat_id:
                logger.error("Telegram channel ID is not configured.")
                return False

            # Initialize telegram bot agent
            from telegram_bot_agent import TelegramBotAgent

            try:
                bot_agent = TelegramBotAgent()

                # Send message
                success = await bot_agent.send_message(chat_id, message)

                if success:
                    logger.info("Prism Signal alert transmission successful")
                    return True
                else:
                    logger.error("Prism Signal alert transmission failed")
                    return False

            except Exception as e:
                logger.error(f"Error during telegram bot initialization or message transmission: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error during Prism Signal alert generation: {str(e)}")
            return False

    def _create_trigger_alert_message(self, mode, results, trade_date):
        """
        Generate telegram alert message based on trigger results
        """
        # Convert date format
        formatted_date = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:8]}"

        # Set title based on mode
        if mode == "morning":
            title = "🔔 오전 프리즘 시그널 얼럿"
            time_desc = "장 시작 후 10분 시점"
        else:
            title = "🔔 오후 프리즘 시그널 얼럿"
            time_desc = "장 마감 후"

        # Message header
        message = f"{title}\n"
        message += f"📅 {formatted_date} {time_desc} 포착된 관심종목\n\n"

        # Add stock information by trigger
        for trigger_type, stocks in results.items():
            # Set emoji based on trigger type
            emoji = self._get_trigger_emoji(trigger_type)

            message += f"{emoji} *{trigger_type}*\n"

            # Add each stock information
            for stock in stocks:
                code = stock.get("code", "")
                name = stock.get("name", "")
                current_price = stock.get("current_price", 0)
                change_rate = stock.get("change_rate", 0)

                # Arrow based on change rate
                arrow = "🔺" if change_rate > 0 else "🔻" if change_rate < 0 else "➖"

                # Basic information
                message += f"· *{name}* ({code})\n"
                message += f"  {current_price:,.0f}원 {arrow} {abs(change_rate):.2f}%\n"

                # Additional information based on trigger type
                if "volume_increase" in stock and trigger_type.startswith("거래량"):
                    volume_increase = stock.get("volume_increase", 0)
                    message += f"  거래량 증가율: {volume_increase:.2f}%\n"

                elif "gap_rate" in stock and trigger_type.startswith("갭 상승"):
                    gap_rate = stock.get("gap_rate", 0)
                    message += f"  갭 상승률: {gap_rate:.2f}%\n"

                elif "trade_value_ratio" in stock and "시총 대비" in trigger_type:
                    trade_value_ratio = stock.get("trade_value_ratio", 0)
                    market_cap = stock.get("market_cap", 0) / 100000000  # Convert to hundred million won units
                    message += f"  거래대금/시총 비율: {trade_value_ratio:.2f}%\n"
                    message += f"  시가총액: {market_cap:.2f}억원\n"

                elif "closing_strength" in stock and "마감 강도" in trigger_type:
                    closing_strength = stock.get("closing_strength", 0) * 100
                    message += f"  마감 강도: {closing_strength:.2f}%\n"

                message += "\n"

        # Footer message
        message += "💡 상세 분석 보고서는 약 10-30분 내 제공 예정\n"
        message += "⚠️ 본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."

        return message

    def _get_trigger_emoji(self, trigger_type):
        """
        Return emoji matching trigger type
        """
        if "거래량" in trigger_type:
            return "📊"
        elif "갭 상승" in trigger_type:
            return "📈"
        elif "시총 대비" in trigger_type:
            return "💰"
        elif "상승률" in trigger_type:
            return "🚀"
        elif "마감 강도" in trigger_type:
            return "🔨"
        elif "횡보" in trigger_type:
            return "↔️"
        else:
            return "🔎"

    async def run_full_pipeline(self, mode, language: str = "ko"):
        """
        Execute full pipeline

        Args:
            mode (str): 'morning' or 'afternoon'
            language (str): Analysis language ("ko" or "en")
        """
        logger.info(f"Starting full pipeline - mode: {mode}")

        try:
            # 1. Execute trigger batch - changed to async method (improved asyncio resource management)
            results_file = f"trigger_results_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
            tickers = await self.run_trigger_batch(mode)

            if not tickers:
                logger.warning("No stocks selected. Terminating process.")
                return

            # 1-1. Send trigger results to telegram immediately
            if os.path.exists(results_file):
                logger.info(f"Trigger results file confirmed: {results_file}")
                alert_sent = await self.send_trigger_alert(mode, results_file, language)
                if alert_sent:
                    logger.info("Prism Signal alert transmission complete")
                else:
                    logger.warning("Prism Signal alert transmission failed")
            else:
                logger.warning(f"Trigger results file not found: {results_file}")

            # 2. Generate reports - important: await added here!
            report_paths = await self.generate_reports(tickers, mode, timeout=600, language=language)
            if not report_paths:
                logger.warning("No reports generated. Terminating process.")
                return

            # 3. PDF conversion
            pdf_paths = await self.convert_to_pdf(report_paths)

            # 4-5. Generate and send telegram messages (only when telegram is enabled)
            if self.telegram_config.use_telegram:
                logger.info("Telegram enabled - proceeding with message generation and transmission steps")

                # 4. Generate telegram messages
                message_paths = await self.generate_telegram_messages(pdf_paths)

                # 5. Send telegram messages and PDFs
                await self.send_telegram_messages(message_paths, pdf_paths)
            else:
                logger.info("Telegram disabled - skipping message generation and transmission steps")

            # 6. Tracking system batch
            if pdf_paths:
                try:
                    logger.info("Starting stock tracking system batch execution")

                    # Import tracking agent
                    from stock_tracking_enhanced_agent import EnhancedStockTrackingAgent as StockTrackingAgent
                    from stock_tracking_agent import app as tracking_app

                    # Validate telegram configuration
                    if self.telegram_config.use_telegram:
                        # Validate required settings when telegram is enabled
                        try:
                            self.telegram_config.validate_or_raise()
                        except ValueError as ve:
                            logger.error(f"Telegram configuration error: {str(ve)}")
                            logger.error("Skipping tracking system batch.")
                            return

                    # Log telegram configuration status
                    self.telegram_config.log_status()

                    # Use MCPApp context manager
                    async with tracking_app.run():
                        # Pass telegram configuration to agent
                        tracking_agent = StockTrackingAgent(
                            telegram_token=self.telegram_config.bot_token if self.telegram_config.use_telegram else None
                        )

                        # Pass report paths, telegram configuration, and language
                        chat_id = self.telegram_config.channel_id if self.telegram_config.use_telegram else None
                        tracking_success = await tracking_agent.run(pdf_paths, chat_id, language)

                        if tracking_success:
                            logger.info("Tracking system batch execution complete")
                        else:
                            logger.error("Tracking system batch execution failed")

                except Exception as e:
                    logger.error(f"Error during tracking system batch execution: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("No reports generated, not executing tracking system batch.")

            logger.info(f"Full pipeline complete - mode: {mode}")

        except Exception as e:
            logger.error(f"Error during pipeline execution: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

    async def generate_reports(self, tickers, mode, timeout: int = None, language: str = "ko") -> list:
        """
        Generate reports serially for all stocks.
        Process one stock at a time to prevent OpenAI rate limit issues.

        Args:
            tickers: List of stocks to analyze
            mode: Execution mode
            timeout: Timeout (seconds)
            language: Analysis language ("ko" or "en")

        Returns:
            list: List of successful report paths
        """

        logger.info(f"Starting report generation for {len(tickers)} stocks (serial processing)")

        successful_reports = []

        # Process each stock sequentially
        for idx, ticker_info in enumerate(tickers, 1):
            # If ticker_info is a dict
            if isinstance(ticker_info, dict):
                ticker = ticker_info.get('code')
                company_name = ticker_info.get('name', f"Stock_{ticker}")
            else:
                ticker = ticker_info
                company_name = f"Stock_{ticker}"

            logger.info(f"[{idx}/{len(tickers)}] Starting stock analysis: {company_name}({ticker})")

            # Set output file path
            reference_date = datetime.now().strftime("%Y%m%d")
            output_file = str(REPORTS_DIR / f"{ticker}_{company_name}_{reference_date}_{mode}_gpt4.1.md")

            try:
                # Import function directly from main.py
                from cores.main import analyze_stock

                # Use await directly since already in async environment
                logger.info(f"[{idx}/{len(tickers)}] Starting analyze_stock function call")
                report = await analyze_stock(
                    company_code=ticker,
                    company_name=company_name,
                    reference_date=reference_date,
                    language=language
                )

                # Save result
                if report and len(report.strip()) > 0:
                    with open(output_file, "w", encoding="utf-8") as f:
                        f.write(report)
                    logger.info(f"[{idx}/{len(tickers)}] Report generation complete: {company_name}({ticker}) - {len(report)} characters")
                    successful_reports.append(output_file)
                else:
                    logger.error(f"[{idx}/{len(tickers)}] Report generation failed: {company_name}({ticker}) - empty content")

            except Exception as e:
                logger.error(f"[{idx}/{len(tickers)}] Error during analysis: {company_name}({ticker}) - {str(e)}")
                import traceback
                logger.error(traceback.format_exc())


        logger.info(f"Report generation complete: {len(successful_reports)}/{len(tickers)} successful")

        return successful_reports

async def main():
    """
    Main function - command line interface
    """
    parser = argparse.ArgumentParser(description="Stock analysis and telegram transmission orchestrator")
    parser.add_argument("--mode", choices=["morning", "afternoon", "both"], default="both",
                        help="Execution mode (morning, afternoon, both)")
    parser.add_argument("--language", choices=["ko", "en"], default="ko",
                        help="Analysis language (ko: Korean, en: English)")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable telegram message transmission. "
                             "Use when testing without telegram configuration or running locally.")

    args = parser.parse_args()

    # Create telegram configuration
    from telegram_config import TelegramConfig
    telegram_config = TelegramConfig(use_telegram=not args.no_telegram)

    # Validate telegram configuration (only when enabled)
    if telegram_config.use_telegram:
        try:
            telegram_config.validate_or_raise()
        except ValueError as e:
            logger.error(f"Telegram configuration error: {str(e)}")
            logger.error("Terminating program.")
            sys.exit(1)

    # Log telegram configuration status
    telegram_config.log_status()

    orchestrator = StockAnalysisOrchestrator(telegram_config=telegram_config)

    if args.mode == "morning" or args.mode == "both":
        await orchestrator.run_full_pipeline("morning", language=args.language)

    if args.mode == "afternoon" or args.mode == "both":
        await orchestrator.run_full_pipeline("afternoon", language=args.language)

if __name__ == "__main__":
    # Check market holiday
    from check_market_day import is_market_day

    if not is_market_day():
        current_date = datetime.now().date()  # Use datetime.now()
        logger.info(f"Today ({current_date}) is a stock market holiday. Not executing batch job.")
        sys.exit(0)

    # Start timer thread and execute main function only on business days
    import threading

    # Timer function to terminate process after 120 minutes
    def exit_after_timeout():
        import time
        import os
        import signal
        time.sleep(7200)  # Wait 120 minutes
        logger.warning("120-minute timeout reached: forcefully terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    # Start timer as background thread
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    asyncio.run(main())