"""
US Telegram Summary Agent

Generates telegram summary messages from US stock analysis reports.
Uses EvaluatorOptimizerLLM workflow for quality-assured summaries.
"""

import asyncio
import re
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM
from mcp_agent.workflows.evaluator_optimizer.evaluator_optimizer import (
    EvaluatorOptimizerLLM,
    QualityRating,
)

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Add parent directory to path for imports
import sys
_prism_us_dir = Path(__file__).parent
sys.path.insert(0, str(_prism_us_dir))
_project_root = _prism_us_dir.parent
sys.path.insert(0, str(_project_root))

# MCPApp instance
app = MCPApp(name="us_telegram_summary")

# US-specific paths
US_REPORTS_DIR = _prism_us_dir / "reports"
US_TELEGRAM_MSGS_DIR = _prism_us_dir / "telegram_messages"
US_PDF_REPORTS_DIR = _prism_us_dir / "pdf_reports"


class USTelegramSummaryGenerator:
    """
    Generates telegram summary messages from US stock analysis reports.
    """

    def __init__(self):
        """Constructor"""
        pass

    async def read_report(self, report_path: str) -> str:
        """
        Read report file content.

        Args:
            report_path: Path to the report file

        Returns:
            Report content as string
        """
        try:
            with open(report_path, 'r', encoding='utf-8') as file:
                content = file.read()
            return content
        except Exception as e:
            logger.error(f"Failed to read report file: {e}")
            raise

    def extract_metadata_from_filename(self, filename: str) -> dict:
        """
        Extract ticker, company name, and date from filename.

        US filename format: AAPL_Apple Inc_20260118_gpt5.pdf

        Args:
            filename: Report filename

        Returns:
            Dictionary with ticker, company_name, date
        """
        # US filename pattern: TICKER_CompanyName_YYYYMMDD_*.pdf
        pattern = r'([A-Z]+)_(.+)_(\d{8})_.*\.pdf'
        match = re.match(pattern, filename)

        if match:
            ticker = match.group(1)
            company_name = match.group(2)
            date_str = match.group(3)

            # Convert YYYYMMDD to YYYY.MM.DD format
            formatted_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"

            return {
                "ticker": ticker,
                "company_name": company_name,
                "date": formatted_date
            }
        else:
            # Fallback if pattern doesn't match
            return {
                "ticker": "N/A",
                "company_name": Path(filename).stem,
                "date": datetime.now().strftime("%Y.%m.%d")
            }

    def determine_trigger_type(self, ticker: str, report_date: str = None) -> tuple:
        """
        Determine trigger type from US trigger results file.

        Logic:
        1. Check both morning and afternoon trigger result files
        2. If both exist, prefer afternoon (most recent data)
        3. If only one exists, use that mode
        4. If neither exists, return default

        Args:
            ticker: Stock ticker symbol
            report_date: Report date (YYYYMMDD)

        Returns:
            Tuple of (trigger_type, trigger_mode)
        """
        logger.info(f"Determining trigger type for {ticker}")

        # Use current date if not provided
        if report_date is None:
            report_date = datetime.now().strftime("%Y%m%d")
        elif report_date and "." in report_date:
            # Convert YYYY.MM.DD to YYYYMMDD
            report_date = report_date.replace(".", "")

        # Store found triggers by mode
        found_triggers = {}  # {mode: (trigger_type, mode)}

        # Check all modes (morning, afternoon)
        for mode in ["morning", "afternoon"]:
            # US trigger results file path
            results_file = _prism_us_dir / f"trigger_results_{mode}_{report_date}.json"

            logger.info(f"Checking trigger results file: {results_file}")

            if results_file.exists():
                try:
                    with open(results_file, 'r', encoding='utf-8') as f:
                        results = json.load(f)

                    # Check all trigger types (excluding metadata)
                    for trigger_type, stocks in results.items():
                        if trigger_type != "metadata":
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    if stock.get("ticker") == ticker:
                                        found_triggers[mode] = (trigger_type, mode)
                                        logger.info(f"Found trigger for {ticker} - type: {trigger_type}, mode: {mode}")
                                        break

                            # Already found, no need to check other trigger types
                            if mode in found_triggers:
                                break

                except Exception as e:
                    logger.error(f"Error reading trigger results file: {e}")

        # Return based on priority: afternoon > morning
        if "afternoon" in found_triggers:
            trigger_type, mode = found_triggers["afternoon"]
            logger.info(f"Final selection: afternoon mode - trigger type: {trigger_type}")
            return trigger_type, mode
        elif "morning" in found_triggers:
            trigger_type, mode = found_triggers["morning"]
            logger.info(f"Final selection: morning mode - trigger type: {trigger_type}")
            return trigger_type, mode

        # Default if not found
        logger.warning(f"Could not find trigger type for {ticker} in results file, using default")
        return "Notable Pattern", "unknown"

    def _get_trigger_display_name(self, trigger_type: str) -> str:
        """
        Get display name for trigger type.

        Args:
            trigger_type: Internal trigger type name

        Returns:
            Human-readable trigger display name
        """
        trigger_names = {
            "intraday_surge": "Intraday Surge",
            "volume_surge": "Volume Surge",
            "gap_up": "Gap Up",
            "sector_momentum": "Sector Momentum",
            "Notable Pattern": "Notable Pattern"
        }
        return trigger_names.get(trigger_type, trigger_type)

    def create_optimizer_agent(self, metadata: dict, current_date: str, language: str = "ko") -> Agent:
        """
        Create telegram summary optimizer agent.

        Args:
            metadata: Stock metadata
            current_date: Current date (YYYY.MM.DD)
            language: Target language (default: "ko")

        Returns:
            Agent instance for optimization
        """
        # Create US-specific optimizer agent
        ticker = metadata.get("ticker", "N/A")
        company_name = metadata.get("company_name", "Unknown")

        # Warning message for morning mode
        if language == "ko":
            warning_message = ""
            if metadata.get('trigger_mode') == 'morning':
                warning_message = '메시지 중간에 "⚠️ 주의: 본 정보는 장 시작 후 10분 시점 데이터 기준으로, 현재 시장 상황과 차이가 있을 수 있습니다." 문구를 반드시 포함해 주세요.'

            instruction = f"""당신은 미국 주식 정보 요약 전문가입니다.
상세한 주식 분석 보고서를 읽고, 일반 투자자를 위한 가치 있는 텔레그램 메시지로 요약해야 합니다.
메시지는 핵심 정보와 통찰력을 포함해야 하며, 아래 형식을 따라야 합니다:

## 현재 맥락
- 날짜: {current_date}
- 종목: {company_name} ({ticker})
- 시장: 미국 (NYSE/NASDAQ)

## 메시지 형식 요구사항
1. 이모지와 함께 종목 정보 표시 (📊, 📈, 💰 등 적절한 이모지)
2. 종목명(티커) 및 간략한 사업 설명 (1-2문장)
3. 핵심 거래 정보:
   - 현재가 (USD)
   - 전일 대비 등락률
   - 최근 거래량 동향
4. 주요 지지선/저항선 레벨
5. 기관 보유 현황 (의미있는 변동이 있는 경우)
6. 투자 관점 - 리스크/리워드 평가

전체 메시지는 2000자 이내로 작성하세요. 투자자가 즉시 활용할 수 있는 실질적인 정보에 집중하세요.
수치는 가능한 구체적으로 표현하고, 주관적 투자 조언이나 '추천'이라는 단어는 사용하지 마세요.

{warning_message}

메시지 끝에는 "본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다." 문구를 반드시 포함하세요.
"""

        else:  # English
            warning_message = ""
            if metadata.get('trigger_mode') == 'morning':
                warning_message = 'IMPORTANT: You must include this warning in the middle of the message: "⚠️ Note: This information is based on data from 10 minutes after market open and may differ from current market conditions."'

            instruction = f"""You are a financial analyst specializing in creating concise, engaging Telegram messages for US stock market analysis.

## Current Context
- Date: {current_date}
- Stock: {company_name} ({ticker})
- Market: US (NYSE/NASDAQ)

## Your Task
Transform the detailed stock analysis report into a compelling Telegram summary message that:
1. Captures the key investment thesis
2. Highlights critical price levels and technical signals
3. Summarizes institutional activity if notable
4. Provides clear risk/reward assessment
5. Uses appropriate emojis for visual engagement

## Message Format Requirements
- Start with an emoji that reflects the overall sentiment (📈 bullish, 📉 bearish, 📊 neutral)
- Include: Company Name (TICKER) - Analysis Summary
- Use numbered points for clarity
- Keep total length under 2000 characters
- End with: "This information is for reference only. Investment decisions and responsibilities belong to the investor."

## Key Sections to Include
1. Current Price & Trend Direction
2. Key Support/Resistance Levels
3. Volume Analysis
4. Institutional Ownership Changes (if significant)
5. Risk Factors & Target Price Range

{warning_message}

Generate a professional, informative Telegram message."""

        return Agent(
            name="us_telegram_optimizer",
            instruction=instruction,
            server_names=[]
        )

    def create_evaluator_agent(self, current_date: str, language: str = "ko") -> Agent:
        """
        Create telegram summary evaluator agent.

        Args:
            current_date: Current date (YYYY.MM.DD)
            language: Target language (default: "ko")

        Returns:
            Agent instance for evaluation
        """
        # Language-specific instructions
        if language == "ko":
            instruction = f"""당신은 미국 주식 정보 요약 메시지를 평가하는 전문가입니다.
주식 분석 보고서와 생성된 텔레그램 메시지를 비교하여 다음 기준에 따라 평가해야 합니다:

## 평가 날짜
- 날짜: {current_date}

## 평가 기준 (각 항목별 1-5점)

1. **정확성** (가중치: 30%)
   - 가격 수준과 변동률이 정확한가?
   - 기술적 지표가 올바르게 설명되어 있는가?
   - 기관 보유 현황이 정확히 보고되어 있는가?

2. **명확성** (가중치: 25%)
   - 메시지가 이해하기 쉬운가?
   - 구조가 논리적이고 잘 정리되어 있는가?
   - 복잡한 개념이 쉽게 설명되어 있는가?

3. **완전성** (가중치: 20%)
   - 주요 가격 수준이 포함되어 있는가?
   - 리스크와 기회가 언급되어 있는가?
   - 투자 논거가 명확한가?

4. **참여도** (가중치: 15%)
   - 이모지가 적절하게 사용되었는가?
   - 전문적이면서도 접근하기 쉬운 톤인가?
   - 추가 연구를 권장하고 있는가?

5. **규정 준수** (가중치: 10%)
   - 적절한 면책 조항이 포함되어 있는가?
   - 명시적인 매수/매도 권고를 피하고 있는가?
   - 텔레그램 형식에 맞는가?

## 평가 등급
- EXCELLENT (5): 게시 준비 완료, 수정 불필요
- GOOD (4): 약간의 개선 가능
- FAIR (3): 일부 수정 필요
- POOR (2): 상당한 문제 있음
- UNACCEPTABLE (1): 전체 재작성 필요

EXCELLENT 미만인 경우 구체적인 개선 피드백을 제공하세요."""

        else:  # English
            instruction = f"""You are a quality evaluator for US stock market Telegram messages.

## Evaluation Date
- Date: {current_date}

## Your Role
Evaluate the quality of Telegram summary messages for US stocks based on these criteria:

## Evaluation Criteria (Score 1-5 for each)

1. **Accuracy** (Weight: 30%)
   - Are price levels and percentages accurate?
   - Are technical indicators correctly described?
   - Are institutional holdings properly reported?

2. **Clarity** (Weight: 25%)
   - Is the message easy to understand?
   - Is the structure logical and well-organized?
   - Are complex concepts explained simply?

3. **Completeness** (Weight: 20%)
   - Does it cover key price levels?
   - Does it mention risks and opportunities?
   - Is the investment thesis clear?

4. **Engagement** (Weight: 15%)
   - Are emojis used appropriately?
   - Is the tone professional yet accessible?
   - Does it encourage further research?

5. **Compliance** (Weight: 10%)
   - Does it include proper disclaimer?
   - Does it avoid explicit buy/sell recommendations?
   - Is the format correct for Telegram?

## Rating Scale
- EXCELLENT (5): Publication-ready, no changes needed
- GOOD (4): Minor improvements possible
- FAIR (3): Requires some revisions
- POOR (2): Significant issues
- UNACCEPTABLE (1): Complete rewrite needed

Provide specific feedback for improvement if rating is below EXCELLENT."""

        return Agent(
            name="us_telegram_evaluator",
            instruction=instruction,
            server_names=[]
        )

    async def generate_telegram_message(
        self,
        report_content: str,
        metadata: dict,
        trigger_type: str,
        language: str = "ko"
    ) -> str:
        """
        Generate telegram message with evaluation and optimization.

        Args:
            report_content: Report content
            metadata: Stock metadata
            trigger_type: Trigger type
            language: Target language (default: "ko")

        Returns:
            Generated telegram message
        """
        # Current date (YYYY.MM.DD format)
        current_date = datetime.now().strftime("%Y.%m.%d")

        # Create optimizer agent
        optimizer = self.create_optimizer_agent(metadata, current_date, language)

        # Create evaluator agent
        evaluator = self.create_evaluator_agent(current_date, language)

        # Setup evaluator-optimizer workflow
        evaluator_optimizer = EvaluatorOptimizerLLM(
            optimizer=optimizer,
            evaluator=evaluator,
            llm_factory=OpenAIAugmentedLLM,
            min_rating=QualityRating.EXCELLENT
        )

        # Get display name for trigger type
        trigger_display = self._get_trigger_display_name(trigger_type)

        # Compose message prompt
        prompt_message = f"""The following is a detailed analysis report for {metadata['company_name']} ({metadata['ticker']}).
This stock was detected by the {trigger_display} trigger.

Report Content:
{report_content}
"""

        # Add warning for morning mode (data may be stale)
        if metadata.get('trigger_mode') == 'morning':
            logger.info("Adding morning data warning")
            prompt_message += "\nNote: This stock was detected 10 minutes after market open. Current conditions may differ."

        # Generate telegram message using evaluator-optimizer workflow
        response = await evaluator_optimizer.generate_str(
            message=prompt_message,
            request_params=RequestParams(
                model="gpt-5.2",
                reasoning_effort="none",
                maxTokens=6000,
                max_iterations=2
            )
        )

        # Process response
        logger.info(f"Response type: {type(response)}")

        # If response is already a string
        if isinstance(response, str):
            logger.info("Response is string format")
            # Check if already in message format
            if response.startswith(('📊', '📈', '📉', '💰', '⚠️', '🔍')):
                return response

            # Remove Python object representations
            cleaned_response = re.sub(r'[A-Za-z]+\([^)]*\)', '', response)

            # Try to extract actual message content
            emoji_start = re.search(r'(📊|📈|📉|💰|⚠️|🔍)', cleaned_response)
            # Support both Korean and English disclaimers
            message_end = re.search(
                r'(This information is for reference only\..*?investor\.|본 정보는 투자 참고용이며.*?있습니다\.)',
                cleaned_response, re.DOTALL
            )

            if emoji_start and message_end:
                return cleaned_response[emoji_start.start():message_end.end()]

        # Handle OpenAI API response object
        if hasattr(response, 'content') and response.content is not None:
            logger.info("Response has content attribute")
            return response.content

        # Handle ChatCompletionMessage with tool_calls
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.info("Response has tool_calls")

            if hasattr(response, 'function_call') and response.function_call:
                logger.info("Response has function_call result")
                return f"Function call result: {response.function_call}"

            return "Unable to extract text from tool call result. Please contact administrator."

        # Last attempt: convert to string and extract message format
        response_str = str(response)
        logger.debug(f"Response string before regex: {response_str[:100]}...")

        # Regex to extract telegram message format (support both Korean and English)
        content_match = re.search(
            r'(📊|📈|📉|💰|⚠️|🔍).*?(This information is for reference only\..*?investor\.|본 정보는 투자 참고용이며.*?있습니다\.)',
            response_str,
            re.DOTALL
        )

        if content_match:
            logger.info("Extracted message content using regex")
            return content_match.group(0)

        # Fallback: generate default message (language-aware)
        logger.warning("Unable to extract valid telegram message from response")
        logger.warning(f"Original message (first 100 chars): {response_str[:100]}...")

        # Default message based on language
        if language == "ko":
            default_message = f"""📊 {metadata['company_name']} ({metadata['ticker']}) - 분석 요약

1. 현재가: (정보 없음)
2. 최근 추세: (정보 없음)
3. 주요 체크포인트: 상세 분석 보고서를 참조하세요.

⚠️ 자동 생성 오류로 상세 정보를 표시할 수 없습니다. 전체 보고서를 확인하세요.
본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."""
        else:
            default_message = f"""📊 {metadata['company_name']} ({metadata['ticker']}) - Analysis Summary

1. Current Price: (Information unavailable)
2. Recent Trend: (Information unavailable)
3. Key Checkpoints: Please refer to the detailed analysis report.

⚠️ Unable to display detailed information due to auto-generation error. Please check the full report.
This information is for reference only. Investment decisions and responsibilities belong to the investor."""

        return default_message

    def save_telegram_message(self, message: str, output_path: str):
        """
        Save generated telegram message to file.

        Args:
            message: Telegram message content
            output_path: Output file path
        """
        try:
            # Create directory if not exists
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as file:
                file.write(message)
            logger.info(f"Telegram message saved to {output_path}")
        except Exception as e:
            logger.error(f"Failed to save telegram message: {e}")
            raise

    async def process_report(
        self,
        report_pdf_path: str,
        output_dir: str = None,
        language: str = "ko"
    ) -> str:
        """
        Process report file to generate telegram summary message.

        Args:
            report_pdf_path: Report file path
            output_dir: Output directory
            language: Target language (default: "ko")

        Returns:
            Generated telegram message
        """
        try:
            # Default output directory
            if output_dir is None:
                output_dir = str(US_TELEGRAM_MSGS_DIR)

            # Create output directory
            os.makedirs(output_dir, exist_ok=True)

            # Extract metadata from filename
            filename = os.path.basename(report_pdf_path)
            metadata = self.extract_metadata_from_filename(filename)

            logger.info(f"Processing: {filename} - {metadata['company_name']} ({metadata['ticker']})")

            # Read report content
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(report_pdf_path)

            # Determine trigger type and mode
            trigger_type, trigger_mode = self.determine_trigger_type(
                metadata['ticker'],
                metadata.get('date', '').replace('.', '')  # YYYY.MM.DD → YYYYMMDD
            )
            logger.info(f"Detected trigger type: {trigger_type}, mode: {trigger_mode}")

            # Add trigger mode to metadata
            metadata['trigger_mode'] = trigger_mode

            # Generate telegram summary message
            telegram_message = await self.generate_telegram_message(
                report_content, metadata, trigger_type, language
            )

            # Generate output file path
            output_file = os.path.join(
                output_dir,
                f"{metadata['ticker']}_{metadata['company_name']}_telegram.txt"
            )

            # Save message
            self.save_telegram_message(telegram_message, output_file)

            logger.info(f"Telegram message generated: {output_file}")

            return telegram_message

        except Exception as e:
            logger.error(f"Error processing report: {e}")
            raise


async def process_all_reports(
    reports_dir: str = None,
    output_dir: str = None,
    date_filter: str = None,
    language: str = "ko"
):
    """
    Process all report files in the specified directory.

    Args:
        reports_dir: Reports directory
        output_dir: Output directory
        date_filter: Date filter (YYYYMMDD)
        language: Target language (default: "ko")
    """
    # Default directories
    if reports_dir is None:
        reports_dir = str(US_PDF_REPORTS_DIR)
    if output_dir is None:
        output_dir = str(US_TELEGRAM_MSGS_DIR)

    # Initialize generator
    generator = USTelegramSummaryGenerator()

    # Check reports directory
    reports_path = Path(reports_dir)
    if not reports_path.exists() or not reports_path.is_dir():
        logger.error(f"Reports directory does not exist: {reports_dir}")
        return

    # Find report files
    report_files = list(reports_path.glob("*.pdf"))

    # Apply date filter
    if date_filter:
        report_files = [f for f in report_files if date_filter in f.name]

    if not report_files:
        logger.warning(f"No report files to process. Directory: {reports_dir}, Filter: {date_filter or 'None'}")
        return

    logger.info(f"Processing {len(report_files)} report files.")

    # Process each report
    for report_file in report_files:
        try:
            await generator.process_report(str(report_file), output_dir, language)
        except Exception as e:
            logger.error(f"Error processing {report_file.name}: {e}")

    logger.info("All reports processed.")


async def main():
    """
    Main function
    """
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate Telegram summary messages from US stock analysis reports."
    )
    parser.add_argument(
        "--reports-dir",
        default=str(US_PDF_REPORTS_DIR),
        help="Directory containing report files"
    )
    parser.add_argument(
        "--output-dir",
        default=str(US_TELEGRAM_MSGS_DIR),
        help="Directory to save Telegram messages"
    )
    parser.add_argument(
        "--date",
        help="Process only reports for specific date (YYYYMMDD format)"
    )
    parser.add_argument(
        "--today",
        action="store_true",
        help="Process only today's reports"
    )
    parser.add_argument(
        "--report",
        help="Process a specific report file"
    )
    parser.add_argument(
        "--language",
        default="ko",
        help="Target language code (default: ko)"
    )

    args = parser.parse_args()

    async with app.run() as parallel_app:
        app_logger = parallel_app.logger

        # Process specific report
        if args.report:
            report_pdf_path = args.report
            if not os.path.exists(report_pdf_path):
                app_logger.error(f"Specified report file does not exist: {report_pdf_path}")
                return

            generator = USTelegramSummaryGenerator()
            telegram_message = await generator.process_report(
                report_pdf_path,
                args.output_dir,
                args.language
            )

            # Print generated message
            print("\nGenerated Telegram Message:")
            print("-" * 50)
            print(telegram_message)
            print("-" * 50)

        else:
            # Apply date filter
            date_filter = None
            if args.today:
                date_filter = datetime.now().strftime("%Y%m%d")
            elif args.date:
                date_filter = args.date

            # Process all reports
            await process_all_reports(
                reports_dir=args.reports_dir,
                output_dir=args.output_dir,
                date_filter=date_filter,
                language=args.language
            )


if __name__ == "__main__":
    asyncio.run(main())
