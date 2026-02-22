#!/usr/bin/env python3
"""
US Stock Analysis and Telegram Transmission Orchestrator

Overall Process:
1. Execute time-based (morning/afternoon) trigger batch jobs
2. Generate detailed analysis reports for selected stocks
3. Convert reports to PDF
4. Generate and send telegram channel summary messages
5. Send generated PDF attachments
6. Execute trading simulation

Key Differences from Korean Version:
- Uses ticker symbols (AAPL, MSFT) instead of 6-digit codes
- Uses yfinance for market data
- US market hours (09:30-16:00 EST)
- Korean language default (ko)
"""
from dotenv import load_dotenv
load_dotenv()

import argparse
import asyncio
import json
import logging
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# Add paths for imports
PROJECT_ROOT = Path(__file__).parent.parent
PRISM_US_DIR = Path(__file__).parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

# Logger configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"us_orchestrator_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)


# =============================================================================
# Helper function to import modules from main project cores/ (avoid namespace collision)
# =============================================================================
def _import_from_main_cores(module_name: str, relative_path: str):
    """
    Import module directly from main project cores/ directory.

    This function avoids namespace collision where prism-us/cores/ shadows
    the main project's cores/ directory in sys.path.

    Args:
        module_name: Module name for sys.modules registration
        relative_path: Path relative to PROJECT_ROOT (e.g., "cores/agents/telegram_translator_agent.py")

    Returns:
        Loaded module object
    """
    import importlib.util
    file_path = PROJECT_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Pre-load telegram_translator_agent from main project (used in multiple methods)
_translator_module = _import_from_main_cores(
    "telegram_translator_agent",
    "cores/agents/telegram_translator_agent.py"
)
translate_telegram_message = _translator_module.translate_telegram_message

# Directory configuration
US_REPORTS_DIR = PRISM_US_DIR / "reports"
US_TELEGRAM_MSGS_DIR = PRISM_US_DIR / "telegram_messages"
US_PDF_REPORTS_DIR = PRISM_US_DIR / "pdf_reports"

# Create directories
US_REPORTS_DIR.mkdir(exist_ok=True)
US_TELEGRAM_MSGS_DIR.mkdir(exist_ok=True)
US_PDF_REPORTS_DIR.mkdir(exist_ok=True)
(US_TELEGRAM_MSGS_DIR / "sent").mkdir(exist_ok=True)


# Trigger type translation map (English -> Korean)
TRIGGER_TYPE_KO = {
    "Volume Surge Top": "거래량 급증 상위주",  # Volume surge top stocks
    "Gap Up Momentum Top": "갭 상승 모멘텀 상위주",  # Gap up momentum top stocks
    "Value-to-Cap Ratio Top": "시총 대비 집중 자금 유입 상위주",  # Concentrated capital inflow vs market cap top stocks
    "Intraday Rise Top": "일중 상승률 상위주",  # Intraday rise top stocks
    "Closing Strength Top": "장 마감 강세 상위주",  # Closing strength top stocks
    "Volume Surge Sideways": "거래량 급증 횡보주",  # Volume surge sideways stocks
}


class USStockAnalysisOrchestrator:
    """US Stock Analysis and Telegram Transmission Orchestrator"""

    def __init__(self, telegram_config=None):
        """
        Initialize orchestrator

        Args:
            telegram_config: TelegramConfig object (uses default config if None)
        """
        from telegram_config import TelegramConfig

        self.selected_tickers = {}
        self.telegram_config = telegram_config or TelegramConfig(use_telegram=True)
        self._broadcast_tasks = []  # Collect fire-and-forget broadcast tasks

    @staticmethod
    def _extract_base64_images(markdown_text: str) -> tuple:
        """
        Extract base64 images from markdown and replace with placeholders

        Args:
            markdown_text: Original markdown text with base64 images

        Returns:
            Tuple of (text_without_images, images_dict)
        """
        images = {}
        counter = 0

        def replace_image(match):
            nonlocal counter
            # Use XML-style placeholder that won't be translated
            placeholder = f"<<<__BASE64_IMAGE_{counter}__>>>"
            images[placeholder] = match.group(0)  # Store entire image markdown
            logger.info(f"Extracted image {counter}, size: {len(match.group(0))} chars")
            counter += 1
            return placeholder

        # Pattern to match base64 images in HTML img tags: <img src="data:image/...;base64,..." ... />
        # Also supports markdown format: ![alt](data:image/...;base64,...)
        patterns = [
            r'<img\s+src="data:image/[^;]+;base64,[A-Za-z0-9+/=]+"\s+[^>]*>',  # HTML img tag
            r'!\[([^\]]*)\]\(data:image/[^;]+;base64,[A-Za-z0-9+/=]+\)',  # Markdown format
        ]

        text_without_images = markdown_text
        for pattern in patterns:
            text_without_images = re.sub(pattern, replace_image, text_without_images)

        logger.info(f"Extracted {len(images)} base64 images from markdown")
        return text_without_images, images

    @staticmethod
    def _restore_base64_images(translated_text: str, images: dict) -> str:
        """
        Restore base64 images to translated text

        Args:
            translated_text: Translated text with placeholders
            images: Dictionary of placeholder -> original image markdown

        Returns:
            Text with restored images
        """
        restored_text = translated_text

        # First try exact match
        for placeholder, original_image in images.items():
            if placeholder in restored_text:
                restored_text = restored_text.replace(placeholder, original_image)
                logger.debug(f"Restored image (exact match): {placeholder}")
            else:
                # Try without special characters (LLM might have modified the placeholder)
                import re as regex
                escaped_placeholder = regex.escape(placeholder)
                # Also try variations without special chars
                simple_key = placeholder.replace("<<<", "").replace(">>>", "").replace("__", "_")
                if simple_key in restored_text:
                    restored_text = restored_text.replace(simple_key, original_image)
                    logger.debug(f"Restored image (simple key): {simple_key}")

        return restored_text

    async def run_trigger_batch(self, mode: str):
        """
        Execute US trigger batch and save results

        Args:
            mode: 'morning' or 'afternoon'

        Returns:
            list: List of selected stock info dictionaries
        """
        logger.info(f"Starting US trigger batch execution: {mode}")
        try:
            from us_trigger_batch import run_batch

            # Results file path
            results_file = f"trigger_results_us_{mode}_{datetime.now().strftime('%Y%m%d')}.json"

            # Run batch
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                lambda: run_batch(mode, "INFO", results_file)
            )

            if not results:
                logger.warning("US batch returned empty results")
                return []

            # Read results file for full data with metadata
            if os.path.exists(results_file):
                with open(results_file, 'r', encoding='utf-8') as f:
                    full_results = json.load(f)
                self.selected_tickers[mode] = full_results

            # Extract stock info from results
            tickers = []
            ticker_set = set()

            for trigger_type, stocks_df in results.items():
                if hasattr(stocks_df, 'index'):
                    for ticker in stocks_df.index:
                        if ticker not in ticker_set:
                            ticker_set.add(ticker)

                            # Get company name
                            name = ""
                            if "CompanyName" in stocks_df.columns:
                                name = stocks_df.loc[ticker, "CompanyName"]

                            # Get risk_reward_ratio if available
                            rr_ratio = 0
                            if "risk_reward_ratio" in stocks_df.columns:
                                rr_ratio = float(stocks_df.loc[ticker, "risk_reward_ratio"])

                            tickers.append({
                                'ticker': ticker,
                                'name': name or ticker,
                                'trigger_type': trigger_type,
                                'trigger_mode': mode,
                                'risk_reward_ratio': rr_ratio
                            })

            logger.info(f"Number of selected US stocks: {len(tickers)}")
            return tickers

        except Exception as e:
            logger.error(f"Error during US trigger batch execution: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    async def generate_reports(self, tickers: list, mode: str, timeout: int = None, language: str = "ko") -> list:
        """
        Generate reports serially for all US stocks.

        Args:
            tickers: List of stocks to analyze
            mode: Execution mode
            timeout: Timeout (seconds)
            language: Analysis language (default: "ko")

        Returns:
            list: List of successful report paths
        """
        logger.info(f"Starting US report generation for {len(tickers)} stocks (serial processing)")

        successful_reports = []

        for idx, ticker_info in enumerate(tickers, 1):
            if isinstance(ticker_info, dict):
                ticker = ticker_info.get('ticker')
                company_name = ticker_info.get('name', ticker)
            else:
                ticker = ticker_info
                company_name = ticker

            logger.info(f"[{idx}/{len(tickers)}] Starting US stock analysis: {company_name}({ticker})")

            reference_date = datetime.now().strftime("%Y%m%d")
            output_file = str(US_REPORTS_DIR / f"{ticker}_{company_name}_{reference_date}_{mode}_gpt5.md")

            try:
                from cores.us_analysis import analyze_us_stock

                logger.info(f"[{idx}/{len(tickers)}] Starting analyze_us_stock function call")
                report = await analyze_us_stock(
                    ticker=ticker,
                    company_name=company_name,
                    reference_date=reference_date,
                    language=language
                )

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

        logger.info(f"US report generation complete: {len(successful_reports)}/{len(tickers)} successful")
        return successful_reports

    async def convert_to_pdf(self, report_paths: list) -> list:
        """
        Convert markdown reports to PDF

        Args:
            report_paths: List of markdown report file paths

        Returns:
            list: List of generated PDF file paths
        """
        logger.info(f"Starting PDF conversion for {len(report_paths)} US reports")
        pdf_paths = []

        from pdf_converter import markdown_to_pdf

        for report_path in report_paths:
            try:
                report_file = Path(report_path)
                pdf_file = US_PDF_REPORTS_DIR / f"{report_file.stem}.pdf"

                # Convert markdown to PDF
                markdown_to_pdf(report_path, pdf_file, 'playwright', add_theme=True, enable_watermark=False)

                logger.info(f"PDF conversion complete: {pdf_file}")
                pdf_paths.append(pdf_file)

            except Exception as e:
                logger.error(f"Error during PDF conversion of {report_path}: {str(e)}")

        return pdf_paths

    async def generate_telegram_messages(self, report_pdf_paths: list, language: str = "ko") -> list:
        """
        Generate telegram messages for US stocks

        Args:
            report_pdf_paths: List of report file (pdf) paths
            language: Message language (default: "ko")

        Returns:
            list: List of generated telegram message file paths
        """
        logger.info(f"Starting US telegram message generation for {len(report_pdf_paths)} reports (language: {language})")

        from us_telegram_summary_agent import USTelegramSummaryGenerator

        generator = USTelegramSummaryGenerator()

        message_paths = []
        for report_pdf_path in report_pdf_paths:
            try:
                await generator.process_report(str(report_pdf_path), str(US_TELEGRAM_MSGS_DIR), language=language)

                report_file = Path(report_pdf_path)
                ticker = report_file.stem.split('_')[0]
                company_name = report_file.stem.split('_')[1]

                message_path = US_TELEGRAM_MSGS_DIR / f"{ticker}_{company_name}_telegram.txt"

                if message_path.exists():
                    logger.info(f"Telegram message generation complete: {message_path}")
                    message_paths.append(message_path)
                else:
                    logger.warning(f"Telegram message file not found at expected path: {message_path}")

            except Exception as e:
                logger.error(f"Error during telegram message generation for {report_pdf_path}: {str(e)}")

        return message_paths

    async def send_telegram_messages(self, message_paths: list, pdf_paths: list, report_paths: list = None):
        """
        Send telegram messages and PDF files

        Args:
            message_paths: List of telegram message file paths
            pdf_paths: List of PDF file paths
            report_paths: List of markdown report file paths (for translation)
        """
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping US message and PDF transmission")
            return

        logger.info(f"Starting US telegram message transmission for {len(message_paths)} messages")

        # Use main channel (Korean) by default - same as Korean stock version
        chat_id = self.telegram_config.channel_id
        if not chat_id:
            logger.error("Telegram channel ID is not configured for US stocks.")
            return

        from telegram_bot_agent import TelegramBotAgent

        try:
            bot_agent = TelegramBotAgent()

            # Pre-read message contents into memory for non-blocking broadcast translation
            if self.telegram_config.broadcast_languages:
                message_contents = []
                for mp in message_paths:
                    try:
                        with open(mp, 'r', encoding='utf-8') as f:
                            message_contents.append(f.read())
                    except Exception as e:
                        logger.error(f"Error reading message file {mp}: {str(e)}")
                if message_contents:
                    self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_messages(bot_agent, message_contents))
                    )

            # Send messages to main channel (this moves files to sent folder)
            await bot_agent.process_messages_directory(
                str(US_TELEGRAM_MSGS_DIR),
                chat_id,
                str(US_TELEGRAM_MSGS_DIR / "sent"),
                msg_type="analysis"
            )

            # Send PDF files to main channel
            for pdf_path in pdf_paths:
                logger.info(f"Sending US PDF file: {pdf_path}")
                success = await bot_agent.send_document(chat_id, str(pdf_path), msg_type="pdf")
                if success:
                    logger.info(f"PDF file transmission successful: {pdf_path}")
                else:
                    logger.error(f"PDF file transmission failed: {pdf_path}")
                await asyncio.sleep(1)

            # Send translated PDFs to broadcast channels asynchronously (non-blocking)
            if self.telegram_config.broadcast_languages and report_paths:
                self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_pdfs(bot_agent, report_paths))
                    )

        except Exception as e:
            logger.error(f"Error during telegram message transmission: {str(e)}")

    async def _send_translated_messages(self, bot_agent, message_contents: list):
        """
        Send translated telegram messages to broadcast channels (non-blocking, fire-and-forget)
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            message_contents: List of original message content strings (pre-read from files)
        """
        try:
            async def _translate_and_send_lang(lang, channel_id):
                for original_message in message_contents:
                    try:
                        logger.info(f"Translating US telegram message to {lang}")
                        translated_message = await translate_telegram_message(
                            original_message,
                            model="gpt-5-nano",
                            from_lang="ko",
                            to_lang=lang
                        )
                        success = await bot_agent.send_message(channel_id, translated_message, msg_type="analysis")
                        if success:
                            logger.info(f"US telegram message sent successfully to {lang} channel")
                        else:
                            logger.error(f"Failed to send US telegram message to {lang} channel")
                        await asyncio.sleep(1)
                    except Exception as e:
                        logger.error(f"Error translating/sending US message to {lang}: {str(e)}")

            lang_tasks = []
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                logger.info(f"Dispatching parallel translation for US {lang} channel")
                lang_tasks.append(_translate_and_send_lang(lang, channel_id))

            if lang_tasks:
                await asyncio.gather(*lang_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in _send_translated_messages: {str(e)}")

    async def _send_translated_pdfs(self, bot_agent, report_paths: list):
        """
        Send translated PDF reports to broadcast channels (asynchronous, runs in background)
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            report_paths: List of original markdown report file paths
        """
        try:
            async def _translate_pdfs_for_lang(lang, channel_id):
                for report_path in report_paths:
                    try:
                        logger.info(f"Translating US markdown report {report_path} to {lang}")

                        with open(report_path, 'r', encoding='utf-8') as f:
                            original_report = f.read()

                        text_for_translation, images = self._extract_base64_images(original_report)
                        logger.info(f"Prepared US report for translation: {len(text_for_translation)} chars (extracted {len(images)} images)")

                        translated_report = await translate_telegram_message(
                            text_for_translation,
                            model="gpt-5-nano",
                            from_lang="ko",
                            to_lang=lang
                        )

                        translated_report = self._restore_base64_images(translated_report, images)
                        logger.info(f"Restored images to translated US report: {len(translated_report)} chars")

                        report_file = Path(report_path)
                        translated_report_path = report_file.parent / f"{report_file.stem}_{lang}.md"

                        with open(translated_report_path, 'w', encoding='utf-8') as f:
                            f.write(translated_report)

                        logger.info(f"Translated US report saved: {translated_report_path}")

                        translated_pdf_paths = await self.convert_to_pdf([str(translated_report_path)])

                        if translated_pdf_paths and len(translated_pdf_paths) > 0:
                            translated_pdf_path = translated_pdf_paths[0]
                            logger.info(f"Sending translated US PDF {translated_pdf_path} to {lang} channel")
                            success = await bot_agent.send_document(channel_id, str(translated_pdf_path), msg_type="pdf")

                            if success:
                                logger.info(f"Translated US PDF sent successfully to {lang} channel")
                            else:
                                logger.error(f"Failed to send translated US PDF to {lang} channel")

                            await asyncio.sleep(1)
                        else:
                            logger.error(f"Failed to convert translated US report to PDF: {translated_report_path}")

                    except Exception as e:
                        logger.error(f"Error processing US report {report_path} for {lang}: {str(e)}")

            # Process languages sequentially to limit memory usage
            # (each PDF generation spawns a Playwright/Chromium instance)
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                logger.info(f"Processing PDF translation for US {lang} channel (sequential)")
                try:
                    await _translate_pdfs_for_lang(lang, channel_id)
                except Exception as lang_err:
                    logger.error(f"US PDF translation failed for {lang}: {lang_err}")

        except Exception as e:
            logger.error(f"Error in _send_translated_pdfs: {str(e)}")

    async def send_trigger_alert(self, mode: str, trigger_results_file: str, language: str = "ko"):
        """
        Send trigger execution result to telegram channel immediately

        Args:
            mode: 'morning' or 'afternoon'
            trigger_results_file: Path to trigger results JSON file
            language: Message language (default: "ko")
        """
        if not self.telegram_config.use_telegram:
            logger.info(f"Telegram disabled - skipping US Prism Signal alert (mode: {mode})")
            return False

        logger.info(f"Starting US Prism Signal alert transmission - mode: {mode}, language: {language}")

        try:
            with open(trigger_results_file, 'r', encoding='utf-8') as f:
                results = json.load(f)

            metadata = results.get("metadata", {})
            trade_date = metadata.get("trade_date", datetime.now().strftime("%Y%m%d"))

            all_results = {}
            for key, value in results.items():
                if key != "metadata" and isinstance(value, list):
                    all_results[key] = value

            if not all_results:
                logger.warning(f"No US trigger results found.")
                return False

            # Generate message based on language (no translation needed - direct templates)
            message = self._create_trigger_alert_message(mode, all_results, trade_date, language)

            # Use main channel (Korean) by default
            chat_id = self.telegram_config.channel_id
            if not chat_id:
                logger.error("Telegram channel ID is not configured for US stocks.")
                return False

            from telegram_bot_agent import TelegramBotAgent

            try:
                bot_agent = TelegramBotAgent()
                success = await bot_agent.send_message(chat_id, message, msg_type="trigger")

                if success:
                    logger.info("US Prism Signal alert transmission successful")
                else:
                    logger.error("US Prism Signal alert transmission failed")

                # Send to broadcast channels asynchronously (non-blocking)
                if self.telegram_config.broadcast_languages:
                    self._broadcast_tasks.append(
                        asyncio.create_task(self._send_translated_trigger_alert(bot_agent, message, mode))
                    )

                return success

            except Exception as e:
                logger.error(f"Error during telegram bot initialization: {str(e)}")
                return False

        except Exception as e:
            logger.error(f"Error during US Prism Signal alert generation: {str(e)}")
            return False

    async def _send_translated_trigger_alert(self, bot_agent, original_message: str, mode: str):
        """
        Send translated trigger alerts to additional language channels.
        Languages are processed in parallel for faster delivery.

        Args:
            bot_agent: TelegramBotAgent instance
            original_message: Original Korean message
            mode: 'morning' or 'afternoon'
        """
        try:
            async def _translate_and_send_lang(lang, channel_id):
                try:
                    logger.info(f"Translating US trigger alert to {lang}")
                    translated_message = await translate_telegram_message(
                        original_message,
                        model="gpt-5-nano",
                        from_lang="ko",
                        to_lang=lang
                    )
                    success = await bot_agent.send_message(channel_id, translated_message, msg_type="trigger")
                    if success:
                        logger.info(f"US trigger alert sent successfully to {lang} channel")
                    else:
                        logger.error(f"Failed to send US trigger alert to {lang} channel")
                except Exception as e:
                    logger.error(f"Error sending translated US trigger alert to {lang}: {str(e)}")

            lang_tasks = []
            for lang in self.telegram_config.broadcast_languages:
                channel_id = self.telegram_config.get_broadcast_channel_id(lang)
                if not channel_id:
                    logger.warning(f"No channel ID configured for language: {lang}")
                    continue
                lang_tasks.append(_translate_and_send_lang(lang, channel_id))

            if lang_tasks:
                await asyncio.gather(*lang_tasks, return_exceptions=True)

        except Exception as e:
            logger.error(f"Error in _send_translated_trigger_alert: {str(e)}")

    def _create_trigger_alert_message(self, mode: str, results: dict, trade_date: str, language: str = "ko") -> str:
        """
        Generate telegram alert message based on US trigger results

        Args:
            mode: 'morning' or 'afternoon'
            results: Trigger results dictionary
            trade_date: Trade date in YYYYMMDD format
            language: Message language ('ko' or 'en')
        """
        formatted_date = f"{trade_date[:4]}.{trade_date[4:6]}.{trade_date[6:8]}"

        # Language-specific templates
        if language == "ko":
            if mode == "morning":
                title = "🔔 미국주식 오전 프리즘 시그널 얼럿"
                time_desc = "장 시작 후 10분 시점"
            elif mode == "midday":
                title = "🔔 미국주식 장중 프리즘 시그널 얼럿"
                time_desc = "장중 12시 30분 시점"
            else:
                title = "🔔 미국주식 오후 프리즘 시그널 얼럿"
                time_desc = "장 마감 후"
            header = f"{title}\n📅 {formatted_date} {time_desc} 포착된 관심종목\n\n"  # Detected stocks of interest
            volume_label = "거래량 증가"  # Volume increase
            gap_label = "갭상승"  # Gap up
            footer = "📋 10~30분 후 상세 분석 리포트가 제공됩니다\n※ 본 정보는 투자 참고용이며, 투자 결정은 본인 책임입니다."  # Detailed analysis report will be available in 10-30 minutes / This is for investment reference only
        else:  # English
            if mode == "morning":
                title = "🔔 US Stock Morning Prism Signal Alert"
                time_desc = "10 minutes after market open"
            elif mode == "midday":
                title = "🔔 US Stock Midday Prism Signal Alert"
                time_desc = "at 12:30 PM market time"
            else:
                title = "🔔 US Stock Afternoon Prism Signal Alert"
                time_desc = "after market close"
            header = f"{title}\n📅 {formatted_date} Stocks detected {time_desc}\n\n"
            volume_label = "Volume Increase"
            gap_label = "Gap Up"
            footer = "📋 Detailed analysis report will be available in 10-30 minutes\n※ This is for investment reference only. Investment decisions are your responsibility."

        message = header

        for trigger_type, stocks in results.items():
            emoji = self._get_trigger_emoji(trigger_type)
            # Translate trigger type based on language
            display_trigger_type = TRIGGER_TYPE_KO.get(trigger_type, trigger_type) if language == "ko" else trigger_type
            message += f"{emoji} {display_trigger_type}\n"

            for stock in stocks:
                ticker = stock.get("ticker", stock.get("code", ""))
                name = stock.get("name", ticker)
                current_price = stock.get("current_price", 0)
                change_rate = stock.get("change_rate", 0)

                # Arrow based on change rate
                arrow = "⬆️" if change_rate > 0 else "⬇️" if change_rate < 0 else "➖"

                message += f"· {name} ({ticker})\n"
                message += f"  ${current_price:.2f} {arrow} {abs(change_rate):.2f}%\n"

                # Additional information based on trigger type
                if "volume_increase" in stock and ("Volume" in trigger_type or "거래량" in trigger_type):  # Check both English and Korean trigger names
                    volume_increase = stock.get("volume_increase", 0)
                    message += f"  {volume_label}: {volume_increase:.2f}%\n"
                elif "gap_rate" in stock and ("Gap" in trigger_type or "갭" in trigger_type):  # Check both English and Korean trigger names
                    gap_rate = stock.get("gap_rate", 0)
                    message += f"  {gap_label}: {gap_rate:.2f}%\n"

                message += "\n"

        message += footer

        return message

    def _get_trigger_emoji(self, trigger_type: str) -> str:
        """Return emoji matching trigger type"""
        # Support both Korean and English trigger type names
        if "Volume" in trigger_type or "거래량" in trigger_type:  # Volume
            return "📊"
        elif "Gap" in trigger_type or "갭" in trigger_type:  # Gap
            return "📈"
        elif "Value" in trigger_type or "Cap" in trigger_type or "시총" in trigger_type:  # Market cap
            return "💰"
        elif "Rise" in trigger_type or "Intraday" in trigger_type or "상승" in trigger_type:  # Rise
            return "🚀"
        elif "Closing" in trigger_type or "Strength" in trigger_type or "마감" in trigger_type:  # Closing
            return "🔨"
        elif "Sideways" in trigger_type or "횡보" in trigger_type:  # Sideways
            return "↔️"
        else:
            return "🔎"

    async def run_full_pipeline(self, mode: str, language: str = "ko"):
        """
        Execute full US pipeline

        Args:
            mode: 'morning' or 'afternoon'
            language: Analysis language (default: "ko" - same as Korean stock version)
        """
        logger.info(f"Starting US full pipeline - mode: {mode}")

        try:
            # 1. Execute trigger batch
            results_file = f"trigger_results_us_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
            tickers = await self.run_trigger_batch(mode)

            if not tickers:
                logger.warning("No US stocks selected. Terminating process.")
                return

            # 1-1. Send trigger results to telegram immediately
            if os.path.exists(results_file):
                logger.info(f"US trigger results file confirmed: {results_file}")
                alert_sent = await self.send_trigger_alert(mode, results_file, language)
                if alert_sent:
                    logger.info("US Prism Signal alert transmission complete")
                else:
                    logger.warning("US Prism Signal alert transmission failed")
            else:
                logger.warning(f"US trigger results file not found: {results_file}")

            # 2. Generate reports
            report_paths = await self.generate_reports(tickers, mode, timeout=600, language=language)
            if not report_paths:
                logger.warning("No US reports generated. Terminating process.")
                return

            # 3. PDF conversion
            pdf_paths = await self.convert_to_pdf(report_paths)

            # 4-5. Generate and send telegram messages
            if self.telegram_config.use_telegram:
                logger.info("Telegram enabled - proceeding with US message generation and transmission")

                message_paths = await self.generate_telegram_messages(pdf_paths, language)
                await self.send_telegram_messages(message_paths, pdf_paths, report_paths)
            else:
                logger.info("Telegram disabled - skipping US message generation and transmission")

            # 6. Tracking system batch (runs concurrently with broadcast I/O tasks via async)
            if pdf_paths:
                try:
                    logger.info("Starting US stock tracking system batch execution")

                    from us_stock_tracking_agent import USStockTrackingAgent, app as tracking_app

                    if self.telegram_config.use_telegram:
                        try:
                            self.telegram_config.validate_or_raise()
                        except ValueError as ve:
                            logger.error(f"Telegram configuration error: {str(ve)}")
                            logger.error("Skipping US tracking system batch.")
                            return

                    self.telegram_config.log_status()

                    async with tracking_app.run():
                        tracking_agent = USStockTrackingAgent(
                            telegram_token=self.telegram_config.bot_token if self.telegram_config.use_telegram else None
                        )

                        # Use main channel (Korean) by default - same as Korean stock version
                        chat_id = self.telegram_config.channel_id if self.telegram_config.use_telegram else None

                        trigger_results_file = f"trigger_results_us_{mode}_{datetime.now().strftime('%Y%m%d')}.json"
                        tracking_success = await tracking_agent.run(
                            pdf_paths, chat_id, language,
                            telegram_config=self.telegram_config,
                            trigger_results_file=trigger_results_file
                        )

                        if tracking_success:
                            logger.info("US tracking system batch execution complete")
                        else:
                            logger.error("US tracking system batch execution failed")

                except Exception as e:
                    logger.error(f"Error during US tracking system batch execution: {str(e)}")
                    import traceback
                    logger.error(traceback.format_exc())
            else:
                logger.warning("No US reports generated, not executing tracking system batch.")

            logger.info(f"US full pipeline complete - mode: {mode}")

        except Exception as e:
            logger.error(f"Error during US pipeline execution: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())

        finally:
            # Always wait for background broadcast tasks, even on error/early return
            if self._broadcast_tasks:
                logger.info(f"Waiting for {len(self._broadcast_tasks)} broadcast translation task(s) to complete...")
                results = await asyncio.gather(*self._broadcast_tasks, return_exceptions=True)
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        logger.error(f"Broadcast task {i+1} failed: {result}")
                self._broadcast_tasks.clear()
                logger.info("All broadcast translation tasks completed")


async def main():
    """Main function - command line interface"""
    parser = argparse.ArgumentParser(description="US stock analysis and telegram transmission orchestrator")
    parser.add_argument("--mode", choices=["morning", "midday", "afternoon", "both"], default="both",
                        help="Execution mode (morning, midday, afternoon, both)")
    parser.add_argument("--language", choices=["ko", "en"], default="ko",
                        help="Analysis language (ko: Korean, en: English)")
    parser.add_argument("--broadcast-languages", type=str, default="",
                        help="Additional languages for parallel telegram channel broadcasting (comma-separated, e.g., 'en,ja,zh')")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Disable telegram message transmission")
    parser.add_argument("--force", action="store_true",
                        help="Force execution even on market holidays (for testing)")

    args = parser.parse_args()

    # Parse broadcast languages
    broadcast_languages = [lang.strip() for lang in args.broadcast_languages.split(",") if lang.strip()]

    from telegram_config import TelegramConfig
    telegram_config = TelegramConfig(use_telegram=not args.no_telegram, broadcast_languages=broadcast_languages)

    if telegram_config.use_telegram:
        try:
            telegram_config.validate_or_raise()
        except ValueError as e:
            logger.error(f"Telegram configuration error: {str(e)}")
            logger.error("Terminating program.")
            sys.exit(1)

    telegram_config.log_status()

    orchestrator = USStockAnalysisOrchestrator(telegram_config=telegram_config)

    if args.mode == "morning" or args.mode == "both":
        await orchestrator.run_full_pipeline("morning", language=args.language)

    if args.mode == "midday":
        await orchestrator.run_full_pipeline("midday", language=args.language)

    if args.mode == "afternoon" or args.mode == "both":
        await orchestrator.run_full_pipeline("afternoon", language=args.language)


if __name__ == "__main__":
    # Check for --force flag before market day check
    force_execution = "--force" in sys.argv

    # Check US market holiday (skip if --force is used)
    from check_market_day import is_us_market_day

    if not force_execution and not is_us_market_day():
        current_date = datetime.now().date()
        logger.info(f"Today ({current_date}) is a US stock market holiday. Not executing batch job.")
        sys.exit(0)

    if force_execution:
        logger.warning("Force execution enabled - ignoring market holiday check")

    # Start timer thread and execute main function only on business days
    import threading

    def exit_after_timeout():
        import time
        import signal
        time.sleep(7200)  # 120 minutes
        logger.warning("120-minute timeout reached: forcefully terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    asyncio.run(main())
