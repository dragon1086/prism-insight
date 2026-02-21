"""
Report generation and conversion module
"""
import asyncio
import atexit
import json
import logging
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import markdown
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_anthropic import AnthropicAugmentedLLM

# Logger setup
logger = logging.getLogger(__name__)

# ============================================================================
# Global MCPApp management (prevent process accumulation)
# ============================================================================
_global_mcp_app: Optional[MCPApp] = None
_app_lock = asyncio.Lock()
_app_initialized = False


async def get_or_create_global_mcp_app() -> MCPApp:
    """
    Get or create global MCPApp instance

    Using this approach:
    - Server process starts only once
    - No new process creation per request
    - Prevents resource leaks

    Returns:
        MCPApp: Global MCPApp instance
    """
    global _global_mcp_app, _app_initialized

    async with _app_lock:
        if _global_mcp_app is None or not _app_initialized:
            logger.info("Starting global MCPApp initialization")
            _global_mcp_app = MCPApp(name="telegram_ai_bot_global")
            await _global_mcp_app.initialize()
            _app_initialized = True
            logger.info(f"Global MCPApp initialization complete (Session ID: {_global_mcp_app.session_id})")
        return _global_mcp_app


async def cleanup_global_mcp_app():
    """Cleanup global MCPApp"""
    global _global_mcp_app, _app_initialized

    async with _app_lock:
        if _global_mcp_app is not None and _app_initialized:
            logger.info("Starting global MCPApp cleanup")
            try:
                await _global_mcp_app.cleanup()
                logger.info("Global MCPApp cleanup complete")
            except Exception as e:
                logger.error(f"Error during global MCPApp cleanup: {e}")
            finally:
                _global_mcp_app = None
                _app_initialized = False


async def reset_global_mcp_app():
    """Restart global MCPApp (on error)"""
    logger.warning("Attempting to restart global MCPApp")
    await cleanup_global_mcp_app()
    return await get_or_create_global_mcp_app()


def _cleanup_on_exit():
    """Cleanup on program exit"""
    global _global_mcp_app
    try:
        if _global_mcp_app is not None:
            logger.info("Cleaning up global MCPApp on program exit")
            asyncio.run(cleanup_global_mcp_app())
    except Exception as e:
        logger.error(f"Error during exit cleanup: {e}")


# Auto cleanup on program exit
atexit.register(_cleanup_on_exit)
# ============================================================================

# Constant definitions
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # Create directory if it doesn't exist
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML reports directory
PDF_REPORTS_DIR = Path("pdf_reports")
PDF_REPORTS_DIR.mkdir(exist_ok=True)  # PDF reports directory

# US stock reports directory
US_REPORTS_DIR = Path("prism-us/reports")
US_REPORTS_DIR.mkdir(exist_ok=True, parents=True)
US_PDF_REPORTS_DIR = Path("prism-us/pdf_reports")
US_PDF_REPORTS_DIR.mkdir(exist_ok=True, parents=True)


# =============================================================================
# US Stock Report Caching Functions
# =============================================================================

def get_cached_us_report(ticker: str) -> tuple:
    """Search for cached US stock report

    Args:
        ticker: Ticker symbol (e.g., AAPL, MSFT)

    Returns:
        tuple: (is_cached, content, md_path, pdf_path)
    """
    # Find all report files starting with the ticker
    report_files = list(US_REPORTS_DIR.glob(f"{ticker}_*.md"))

    if not report_files:
        return False, "", None, None

    # Sort by latest
    latest_file = max(report_files, key=lambda p: p.stat().st_mtime)

    # Check if file was created within 24 hours
    file_age = datetime.now() - datetime.fromtimestamp(latest_file.stat().st_mtime)
    if file_age.days >= 1:  # Don't use files older than 24 hours as cache
        return False, "", None, None

    # Check if corresponding PDF file exists
    pdf_file = None
    pdf_files = list(US_PDF_REPORTS_DIR.glob(f"{ticker}_*.pdf"))
    if pdf_files:
        pdf_file = max(pdf_files, key=lambda p: p.stat().st_mtime)

    with open(latest_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Generate PDF if it doesn't exist
    if not pdf_file:
        # Extract company name (filename format: {ticker}_{name}_{date}_analysis.md)
        parts = os.path.basename(latest_file).split('_')
        company_name = parts[1] if len(parts) > 1 else ticker
        pdf_file = save_us_pdf_report(ticker, company_name, latest_file)

    return True, content, latest_file, pdf_file


def save_us_report(ticker: str, company_name: str, content: str) -> Path:
    """Save US stock report to file

    Args:
        ticker: Ticker symbol (e.g., AAPL)
        company_name: Company name
        content: Report content

    Returns:
        Path: Path to saved file
    """
    reference_date = datetime.now().strftime("%Y%m%d")
    # Remove spaces and special characters from filename
    safe_company_name = company_name.replace(" ", "_").replace(".", "").replace(",", "")
    filename = f"{ticker}_{safe_company_name}_{reference_date}_analysis.md"
    filepath = US_REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    logger.info(f"US 보고서 저장 완료: {filepath}")
    return filepath


def save_us_pdf_report(ticker: str, company_name: str, md_path: Path) -> Path:
    """Convert US stock markdown file to PDF and save

    Args:
        ticker: Ticker symbol
        company_name: Company name
        md_path: Markdown file path

    Returns:
        Path: Generated PDF file path
    """
    from pdf_converter import markdown_to_pdf

    reference_date = datetime.now().strftime("%Y%m%d")
    # Remove spaces and special characters from filename
    safe_company_name = company_name.replace(" ", "_").replace(".", "").replace(",", "")
    pdf_filename = f"{ticker}_{safe_company_name}_{reference_date}_analysis.pdf"
    pdf_path = US_PDF_REPORTS_DIR / pdf_filename

    try:
        markdown_to_pdf(str(md_path), str(pdf_path), 'playwright', add_theme=True)
        logger.info(f"US PDF report generated: {pdf_path}")
    except Exception as e:
        logger.error(f"Error converting US PDF: {e}")
        raise

    return pdf_path


def generate_us_report_response_sync(ticker: str, company_name: str) -> str:
    """
    Generate US stock detailed report synchronously (called from background thread)

    Args:
        ticker: Ticker symbol (e.g., AAPL)
        company_name: Company name (e.g., Apple Inc.)

    Returns:
        str: Generated report content
    """
    try:
        logger.info(f"US sync report generation started: {ticker} ({company_name})")

        # Set project root directory (absolute path)
        project_root = os.path.dirname(os.path.abspath(__file__))
        prism_us_dir = os.path.join(project_root, 'prism-us')

        # Run US analysis in separate process
        # Uses analyze_us_stock function from prism-us/cores/us_analysis.py
        cmd = [
            sys.executable,  # 현재 Python 인터프리터
            "-c",
            f"""
import asyncio
import json
import sys
import os

# Use absolute paths (Docker compatibility)
project_root = r'{project_root}'
prism_us_dir = r'{prism_us_dir}'
sys.path.insert(0, prism_us_dir)
os.chdir(project_root)

from cores.us_analysis import analyze_us_stock
from check_market_day import get_reference_date

async def run():
    try:
        # Auto-detect last trading day
        ref_date = get_reference_date()
        result = await analyze_us_stock(
            ticker="{ticker}",
            company_name="{company_name}",
            reference_date=ref_date,
            language="ko"
        )
        # Use delimiters to mark start and end of result output
        print("RESULT_START")
        print(json.dumps({{"success": True, "result": result}}))
        print("RESULT_END")
    except Exception as e:
        # Use delimiters to mark start and end of error output
        print("RESULT_START")
        print(json.dumps({{"success": False, "error": str(e)}}))
        print("RESULT_END")

if __name__ == "__main__":
    asyncio.run(run())
            """
        ]

        logger.info(f"US external process execution: {ticker} (cwd: {project_root})")
        process = subprocess.run(cmd, capture_output=True, text=True, timeout=1200, cwd=project_root)  # 20 min timeout

        # Log stderr (for debugging)
        if process.stderr:
            logger.warning(f"US external process stderr: {process.stderr[:500]}")

        # Initialize output - pre-declare variable to prevent warnings
        output = ""

        # Parse output - extract only actual JSON output using delimiters
        try:
            output = process.stdout
            # Extract only JSON data between RESULT_START and RESULT_END from log output
            if "RESULT_START" in output and "RESULT_END" in output:
                result_start = output.find("RESULT_START") + len("RESULT_START")
                result_end = output.find("RESULT_END")
                json_str = output[result_start:result_end].strip()

                # Parse JSON
                parsed_output = json.loads(json_str)

                if parsed_output.get('success', False):
                    result = parsed_output.get('result', '')
                    logger.info(f"US external process result: {len(result)} characters")
                    return result
                else:
                    error = parsed_output.get('error', 'Unknown error')
                    logger.error(f"US external process error: {error}")
                    return f"Error occurred during US stock analysis: {error}"
            else:
                # If delimiters not found - process execution itself may have issues
                logger.error(f"Could not find result delimiters in US external process output: {output[:500]}")
                # Check if there's error log in stderr
                if process.stderr:
                    logger.error(f"US external process error output: {process.stderr[:500]}")
                return f"US 주식 분석 결과를 찾을 수 없습니다. 로그를 확인하세요."
        except json.JSONDecodeError as e:
            logger.error(f"US 외부 프로세스 출력 파싱 실패: {e}")
            logger.error(f"출력 내용: {output[:1000]}")
            return f"US 주식 분석 결과 파싱 중 오류가 발생했습니다. 로그를 확인하세요."

    except subprocess.TimeoutExpired:
        logger.error(f"US 외부 프로세스 타임아웃: {ticker}")
        return f"US 주식 분석 시간이 초과되었습니다. 다시 시도해주세요."
    except Exception as e:
        logger.error(f"US 동기식 보고서 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"US 주식 보고서 생성 중 오류가 발생했습니다: {str(e)}"


def save_pdf_report(stock_code: str, company_name: str, md_path: Path) -> Path:
    """마크다운 파일을 PDF로 변환하여 저장

    Args:
        stock_code: 종목 코드
        company_name: 회사명
        md_path: 마크다운 파일 경로

    Returns:
        Path: 생성된 PDF 파일 경로
    """
    from pdf_converter import markdown_to_pdf

    reference_date = datetime.now().strftime("%Y%m%d")
    pdf_filename = f"{stock_code}_{company_name}_{reference_date}_analysis.pdf"
    pdf_path = PDF_REPORTS_DIR / pdf_filename

    try:
        markdown_to_pdf(str(md_path), str(pdf_path), 'playwright', add_theme=True)
        logger.info(f"PDF 보고서 생성 완료: {pdf_path}")
    except Exception as e:
        logger.error(f"PDF 변환 중 오류: {e}")
        raise

    return pdf_path


def get_cached_report(stock_code: str) -> tuple:
    """캐시된 보고서 검색

    Returns:
        tuple: (is_cached, content, md_path, pdf_path)
    """
    # Find all report files starting with stock code
    report_files = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

    if not report_files:
        return False, "", None, None

    # Sort by latest
    latest_file = max(report_files, key=lambda p: p.stat().st_mtime)

    # Check if file was created within 24 hours
    file_age = datetime.now() - datetime.fromtimestamp(latest_file.stat().st_mtime)
    if file_age.days >= 1:  # Don't use files older than 24 hours as cache
        return False, "", None, None

    # Check if corresponding PDF file exists
    pdf_file = None
    pdf_files = list(PDF_REPORTS_DIR.glob(f"{stock_code}_*.pdf"))
    if pdf_files:
        pdf_file = max(pdf_files, key=lambda p: p.stat().st_mtime)

    with open(latest_file, "r", encoding="utf-8") as f:
        content = f.read()

    # Generate PDF if it doesn't exist
    if not pdf_file:
        # Extract company name (filename format: {code}_{name}_{date}_analysis.md)
        company_name = os.path.basename(latest_file).split('_')[1]
        pdf_file = save_pdf_report(stock_code, company_name, latest_file)

    return True, content, latest_file, pdf_file


def save_report(stock_code: str, company_name: str, content: str) -> Path:
    """보고서를 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.md"
    filepath = REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

    return filepath


def convert_to_html(markdown_content: str) -> str:
    """마크다운을 HTML로 변환"""
    try:
        # 마크다운을 HTML로 변환
        html_content = markdown.markdown(
            markdown_content,
            extensions=['markdown.extensions.fenced_code', 'markdown.extensions.tables']
        )

        # HTML 템플릿에 내용 삽입
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>주식 분석 보고서</title>
            <style>
                body {{
                    font-family: 'Pretendard', -apple-system, system-ui, sans-serif;
                    line-height: 1.6;
                    color: #333;
                    max-width: 900px;
                    margin: 0 auto;
                    padding: 20px;
                }}
                h1, h2, h3, h4 {{
                    color: #2563eb;
                }}
                table {{
                    border-collapse: collapse;
                    width: 100%;
                    margin: 15px 0;
                }}
                th, td {{
                    border: 1px solid #ddd;
                    padding: 8px 12px;
                }}
                th {{
                    background-color: #f1f5f9;
                }}
                code {{
                    background-color: #f1f5f9;
                    padding: 2px 4px;
                    border-radius: 4px;
                }}
                pre {{
                    background-color: #f1f5f9;
                    padding: 15px;
                    border-radius: 8px;
                    overflow-x: auto;
                }}
            </style>
        </head>
        <body>
            {html_content}
        </body>
        </html>
        """
    except Exception as e:
        logger.error(f"HTML 변환 중 오류: {str(e)}")
        return f"<p>보고서 변환 중 오류가 발생했습니다: {str(e)}</p>"


def save_html_report_from_content(stock_code: str, company_name: str, html_content: str) -> Path:
    """HTML 내용을 파일로 저장"""
    reference_date = datetime.now().strftime("%Y%m%d")
    filename = f"{stock_code}_{company_name}_{reference_date}_analysis.html"
    filepath = HTML_REPORTS_DIR / filename

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(html_content)

    return filepath


def save_html_report(stock_code: str, company_name: str, markdown_content: str) -> Path:
    """마크다운 보고서를 HTML로 변환하여 저장"""
    html_content = convert_to_html(markdown_content)
    return save_html_report_from_content(stock_code, company_name, html_content)


def generate_report_response_sync(stock_code: str, company_name: str) -> str:
    """
    종목 상세 보고서를 동기 방식으로 생성 (백그라운드 스레드에서 호출됨)
    """
    # subprocess 로그 파일 경로 설정
    log_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "logs" / "subprocess"
    log_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = log_dir / f"report_{stock_code}_{timestamp}.log"

    try:
        logger.info(f"동기식 보고서 생성 시작: {stock_code} ({company_name})")
        logger.info(f"Subprocess 로그 파일: {log_file}")

        # 현재 날짜를 YYYYMMDD 형식으로 변환
        reference_date = datetime.now().strftime("%Y%m%d")

        # 별도의 프로세스로 분석 수행
        # 이 방법은 새로운 Python 프로세스를 생성하여 분석을 수행하므로 이벤트 루프 충돌 없음
        cmd = [
            sys.executable,  # 현재 Python 인터프리터
            "-c",
            f"""
import asyncio
import json
import sys
import logging
from datetime import datetime

# subprocess 내부 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stderr)]
)
subprocess_logger = logging.getLogger("subprocess_report")
subprocess_logger.info("Subprocess 시작: {stock_code} ({company_name})")

from cores.analysis import analyze_stock

async def run():
    try:
        subprocess_logger.info("analyze_stock 호출 시작")
        result = await analyze_stock(
            company_code="{stock_code}",
            company_name="{company_name}",
            reference_date="{reference_date}"
        )
        subprocess_logger.info(f"analyze_stock 완료: {{len(result) if result else 0}} 글자")
        # 구분자를 사용하여 결과 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": True, "result": result}}))
        print("RESULT_END")
    except Exception as e:
        subprocess_logger.error(f"analyze_stock 오류: {{str(e)}}", exc_info=True)
        # 구분자를 사용하여 에러 출력의 시작과 끝을 표시
        print("RESULT_START")
        print(json.dumps({{"success": False, "error": str(e)}}))
        print("RESULT_END")

if __name__ == "__main__":
    asyncio.run(run())
            """
        ]

        # Set project root directory (required for cores module import)
        project_root = os.path.dirname(os.path.abspath(__file__))

        logger.info(f"External process execution: {stock_code} (cwd: {project_root})")

        # Run with Popen to save real-time logs
        with open(log_file, "w", encoding="utf-8") as f:
            f.write(f"=== Subprocess Log for {stock_code} ({company_name}) ===\n")
            f.write(f"Started at: {datetime.now().isoformat()}\n")
            f.write(f"Timeout: 1800 seconds (30 min)\n")
            f.write("=" * 60 + "\n\n")
            f.flush()

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=project_root
            )

            try:
                stdout, stderr = process.communicate(timeout=1800)  # 30 min timeout

                # Write to log file
                f.write("\n=== STDOUT ===\n")
                f.write(stdout or "(empty)")
                f.write("\n\n=== STDERR ===\n")
                f.write(stderr or "(empty)")
                f.write(f"\n\n=== Completed at: {datetime.now().isoformat()} ===\n")

            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()

                # Save log even on timeout
                f.write("\n=== TIMEOUT OCCURRED ===\n")
                f.write(f"Timeout at: {datetime.now().isoformat()}\n")
                f.write("\n=== STDOUT (before timeout) ===\n")
                f.write(stdout or "(empty)")
                f.write("\n\n=== STDERR (before timeout) ===\n")
                f.write(stderr or "(empty)")

                logger.error(f"External process timeout: {stock_code}, log file: {log_file}")
                return f"Analysis time exceeded. Check log file: {log_file}"

        # Log stderr (for debugging)
        if stderr:
            logger.warning(f"External process stderr (full log: {log_file}): {stderr[:500]}")

        # Parse output - extract only actual JSON output using delimiters
        try:
            # Extract only JSON data between RESULT_START and RESULT_END from log output
            if "RESULT_START" in stdout and "RESULT_END" in stdout:
                result_start = stdout.find("RESULT_START") + len("RESULT_START")
                result_end = stdout.find("RESULT_END")
                json_str = stdout[result_start:result_end].strip()

                # Parse JSON
                parsed_output = json.loads(json_str)

                if parsed_output.get('success', False):
                    result = parsed_output.get('result', '')
                    logger.info(f"External process result: {len(result)} characters")
                    return result
                else:
                    error = parsed_output.get('error', 'Unknown error')
                    logger.error(f"External process error: {error}, log file: {log_file}")
                    return f"Error occurred during analysis: {error}"
            else:
                # If delimiters not found - process execution itself may have issues
                logger.error(f"Could not find result delimiters in external process output. Log file: {log_file}")
                logger.error(f"stdout excerpt: {stdout[:500] if stdout else '(empty)'}")
                if stderr:
                    logger.error(f"stderr excerpt: {stderr[:500]}")
                return f"Could not find analysis result. Log file: {log_file}"
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse external process output: {e}, log file: {log_file}")
            logger.error(f"Output content: {stdout[:1000] if stdout else '(empty)'}")
            return f"Error occurred while parsing analysis result. Log file: {log_file}"
    except Exception as e:
        logger.error(f"동기식 보고서 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return f"보고서 생성 중 오류가 발생했습니다: {str(e)}"

import re

def clean_model_response(response):
    # 마지막 평가 문장 패턴
    final_analysis_pattern = r'이제 수집한 정보를 바탕으로.*평가를 해보겠습니다\.'

    # 중간 과정 및 도구 호출 관련 정보 제거
    # 1. '[Calling tool' 포함 라인 제거
    lines = response.split('\n')
    cleaned_lines = [line for line in lines if '[Calling tool' not in line]
    temp_response = '\n'.join(cleaned_lines)

    # 2. 마지막 평가 문장이 있다면, 그 이후 내용만 유지
    final_statement_match = re.search(final_analysis_pattern, temp_response)
    if final_statement_match:
        final_statement_pos = final_statement_match.end()
        cleaned_response = temp_response[final_statement_pos:].strip()
    else:
        # 패턴을 찾지 못한 경우 그냥 도구 호출만 제거된 버전 사용
        cleaned_response = temp_response

    # 앞부분 빈 줄 제거
    cleaned_response = cleaned_response.lstrip()

    return cleaned_response

async def generate_follow_up_response(ticker, ticker_name, conversation_context, user_question, tone):
    """
    추가 질문에 대한 AI 응답 생성 (Agent 방식 사용)
    
    ⚠️ 전역 MCPApp 사용으로 프로세스 누적 방지
    
    Args:
        ticker (str): 종목 코드
        ticker_name (str): 종목명
        conversation_context (str): 이전 대화 컨텍스트
        user_question (str): 사용자의 새 질문
        tone (str): 응답 톤
    
    Returns:
        str: AI 응답
    """
    try:
        # 전역 MCPApp 사용 (매번 새로 생성하지 않음!)
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # 현재 날짜 정보 가져오기
        current_date = datetime.now().strftime('%Y%m%d')

        # 에이전트 생성
        agent = Agent(
            name="followup_agent",
            instruction=f"""당신은 텔레그램 채팅에서 주식 평가 후속 질문에 답변하는 전문가입니다.
                        
                        ## 기본 정보
                        - 현재 날짜: {current_date}
                        - 종목 코드: {ticker}
                        - 종목 이름: {ticker_name}
                        - 대화 스타일: {tone}
                        
                        ## 이전 대화 컨텍스트
                        {conversation_context}
                        
                        ## 사용자의 새로운 질문
                        {user_question}
                        
                        ## 응답 가이드라인
                        1. 이전 대화에서 제공한 정보와 일관성을 유지하세요
                        2. 필요한 경우 추가 데이터를 조회할 수 있습니다:
                           - get_stock_ohlcv: 최신 주가 데이터 조회
                           - get_stock_trading_volume: 투자자별 거래 데이터
                           - perplexity_ask: 최신 뉴스나 정보 검색
                        3. 사용자가 요청한 스타일({tone})을 유지하세요
                        4. 텔레그램 메시지 형식으로 자연스럽게 작성하세요
                        5. 이모티콘을 적극 활용하세요 (📈 📉 💰 🔥 💎 🚀 등)
                        6. 마크다운 형식은 사용하지 마세요
                        7. 2000자 이내로 작성하세요
                        8. 이전 대화의 맥락을 고려하여 답변하세요
                        
                        ## 주의사항
                        - 사용자의 질문이 이전 대화와 관련이 있다면, 그 맥락을 참고하여 답변
                        - 새로운 정보가 필요한 경우에만 도구를 사용
                        - 도구 호출 과정을 사용자에게 노출하지 마세요
                        """,
            server_names=["perplexity", "kospi_kosdaq"]
        )

        # LLM 연결
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # 응답 생성
        response = await llm.generate_str(
            message=f"""사용자의 추가 질문에 대해 답변해주세요.
                    
                    이전 대화를 참고하되, 사용자의 새 질문에 집중하여 답변하세요.
                    필요한 경우 최신 데이터를 조회하여 정확한 정보를 제공하세요.
                    """,
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=2000
            )
        )
        app_logger.info(f"추가 질문 응답 생성 결과: {str(response)[:100]}...")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"추가 응답 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 오류 발생 시 전역 app 재시작 시도
        try:
            logger.warning("오류 발생으로 인한 전역 MCPApp 재시작 시도")
            await reset_global_mcp_app()
        except Exception as reset_error:
            logger.error(f"MCPApp 재시작 실패: {reset_error}")
        
        return "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."


async def generate_evaluation_response(ticker, ticker_name, avg_price, period, tone, background, report_path=None, memory_context: str = ""):
    """
    종목 평가 AI 응답 생성

    ⚠️ 전역 MCPApp 사용으로 프로세스 누적 방지

    Args:
        ticker (str): 종목 코드
        ticker_name (str): 종목 이름
        avg_price (float): 평균 매수가
        period (int): 보유 기간 (개월)
        tone (str): 원하는 피드백 스타일/톤
        background (str): 매매 배경/히스토리
        report_path (str, optional): 보고서 파일 경로
        memory_context (str, optional): 사용자 기억 컨텍스트

    Returns:
        str: AI 응답
    """
    try:
        # 전역 MCPApp 사용 (매번 새로 생성하지 않음!)
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # 현재 날짜 정보 가져오기
        current_date = datetime.now().strftime('%Y%m%d')

        # 배경 정보 추가 (있는 경우)
        background_text = f"\n- 매매 배경/히스토리: {background}" if background else ""

        # 사용자 기억 컨텍스트 추가
        memory_section = ""
        if memory_context:
            memory_section = f"""

                        ## 사용자 과거 기록 (참고용)
                        다음은 이 사용자가 과거에 기록한 투자 일기와 평가 내역입니다.
                        현재 평가에 참고하되, 이 기록에 너무 의존하지 마세요:

                        {memory_context}
                        """

        # 에이전트 생성
        agent = Agent(
            name="evaluation_agent",
            instruction=f"""당신은 텔레그램 채팅에서 주식 평가를 제공하는 전문가입니다. 형식적인 마크다운 대신 자연스러운 채팅 방식으로 응답하세요.

                        ## 기본 정보
                        - 현재 날짜: {current_date} (YYYYMMDD형식. 년(4자리) + 월(2자리) + 일(2자리))
                        - 종목 코드: {ticker}
                        - 종목 이름: {ticker_name}
                        - 평균 매수가: {avg_price}원
                        - 보유 기간: {period}개월
                        - 원하는 피드백 스타일: {tone} {background_text}
                        
                        ## 데이터 수집 및 분석 단계
                            1. get_current_time 툴을 사용하여 현재 날짜를 가져오세요.
                            2. get_stock_ohlcv 툴을 사용하여 종목({ticker})의 현재 날짜 기준 최신 3개월치 주가 데이터 및 거래량을 조회하세요. 특히 tool call(time-get_current_time)에서 가져온 년도를 꼭 참고하세요.
                               - fromdate, todate 포맷은 YYYYMMDD입니다. 그리고 todate가 현재날짜고, fromdate가 과거날짜입니다.
                               - 최신 종가와 전일 대비 변동률, 거래량 추이를 반드시 파악하세요.
                               - 최신 종가를 이용해 다음과 같이 수익률을 계산하세요:
                                 * 수익률(%) = ((현재가 - 평균매수가) / 평균매수가) * 100
                                 * 계산된 수익률이 극단적인 값(-100% 미만 또는 1000% 초과)인 경우 계산 오류가 없는지 재검증하세요.
                                 * 매수평단가가 0이거나 비정상적으로 낮은 값인 경우 사용자에게 확인 요청
                               
                               
                            3. get_stock_trading_volume 툴을 사용하여 현재 날짜 기준 최신 3개월치 투자자별 거래 데이터를 분석하세요. 특히 tool call(time-get_current_time)에서 가져온 년도를 꼭 참고하세요.
                               - fromdate, todate 포맷은 YYYYMMDD입니다. 그리고 todate가 현재날짜고, fromdate가 과거날짜입니다.
                               - 기관, 외국인, 개인 등 투자자별 매수/매도 패턴을 파악하고 해석하세요.
                            
                            4. perplexity_ask 툴을 사용하여 다음 정보를 검색하세요. 최대한 1개의 쿼리로 통합해서 현재 날짜를 기준으로 검색해주세요. 특히 tool call(time-get_current_time)에서 가져온 년도를 꼭 참고하세요.
                               - "종목코드 {ticker}의 정확한 회사 {ticker_name}에 대한 최근 뉴스 및 실적 분석 (유사 이름의 다른 회사와 혼동하지 말 것. 정확히 이 종목코드 {ticker}에 해당하는 {ticker_name} 회사만 검색."
                               - "{ticker_name}(종목코드: {ticker}) 소속 업종 동향 및 전망"
                               - "글로벌과 국내 증시 현황 및 전망"
                               - "최근 급등 원인(테마 등)"
                               
                            5. 필요에 따라 추가 데이터를 수집하세요.
                            6. 수집된 모든 정보를 종합적으로 분석하여 종목 평가에 활용하세요.
                        
                        ## 스타일 적응형 가이드
                        사용자가 요청한 피드백 스타일("{tone}")을 최대한 정확하게 구현하세요. 다음 프레임워크를 사용하여 어떤 스타일도 적응적으로 구현할 수 있습니다:
                        
                        1. **스타일 속성 분석**:
                           사용자의 "{tone}" 요청을 다음 속성 측면에서 분석하세요:
                           - 격식성 (격식 <--> 비격식)
                           - 직접성 (간접 <--> 직설적)
                           - 감정 표현 (절제 <--> 과장)
                           - 전문성 (일상어 <--> 전문용어)
                           - 태도 (중립 <--> 주관적)
                        
                        2. **키워드 기반 스타일 적용**:
                           - "친구", "동료", "형", "동생" → 친근하고 격식 없는 말투
                           - "전문가", "분석가", "정확히" → 데이터 중심, 격식 있는 분석
                           - "직설적", "솔직", "거침없이" → 매우 솔직한 평가
                           - "취한", "술자리", "흥분" → 감정적이고 과장된 표현
                           - "꼰대", "귀족노조", "연륜" → 교훈적이고 경험 강조
                           - "간결", "짧게" → 핵심만 압축적으로
                           - "자세히", "상세히" → 모든 근거와 분석 단계 설명
                        
                        3. **스타일 조합 및 맞춤화**:
                           사용자의 요청에 여러 키워드가 포함된 경우 적절히 조합하세요.
                           예: "30년지기 친구 + 취한 상태" = 매우 친근하고 과장된 말투와 강한 주관적 조언
                        
                        4. **알 수 없는 스타일 대응**:
                           생소한 스타일 요청이 들어오면:
                           - 요청된 스타일의 핵심 특성을 추론
                           - 언어적 특징, 문장 구조, 어휘 선택 등에서 스타일을 반영
                           - 해당 스타일에 맞는 고유한 표현과 문장 패턴 창조
                        
                        ### 투자 상황별 조언 스타일
                        
                        1. 수익 포지션 (현재가 > 평균매수가):
                           - 더 적극적이고 구체적인 매매 전략 제시
                           - 예: "이익 실현 구간을 명확히 잡아 절반은 익절하고, 절반은 더 끌고가는 전략도 괜찮을 것 같아"
                           - 다음 목표가와 손절선 구체적 제시
                           - 현 상승세의 지속 가능성 분석에 초점
                        
                        2. 손실 포지션 (현재가 < 평균매수가):
                           - 감정적 공감과 함께 객관적 분석 제공
                           - 예: "지금 답답한 마음 이해해. 하지만 기업 펀더멘털을 보면..."
                           - 회복 가능성 또는 손절 필요성에 대한 명확한 의견 제시
                           - 평균단가 낮추기나 손절 등 구체적 대안 제시
                        
                        3. 단기 투자 (보유기간 < 3개월):
                           - 기술적 분석과 단기 모멘텀에 집중
                           - 예: "단기적으로는 230일선 돌파가 중요한 변곡점이야. 이거 뚫으면 한번 달릴 수 있어"
                           - 단기 매매 타이밍과 기술적 패턴 강조
                        
                        4. 장기 투자 (보유기간 > 12개월):
                           - 펀더멘털과 산업 전망에 중점
                           - 예: "이 기업은 장기적으로 신사업 성장성이 좋아 3-5년 관점에선 충분히 가치가 있다고 봐"
                           - 배당, 장기 성장성, 산업 트렌드 중심 분석
                        
                        ## 메시지 포맷팅 팁
                        - 이모티콘을 적극 활용 (📈 📉 💰 🔥 💎 🚀 등)
                        - 줄바꿈으로 단락을 명확히 구분
                        - 중요 부분은 ✨ 또는 ❗️ 등으로 강조
                        - 텍스트 블록은 짧게 유지하여 모바일에서 읽기 쉽게 작성
                        - 해시태그(#)를 활용하여 핵심 키워드 강조
                        - 절대 마크다운 형식으로 쓰지 말고, 텔레그램 메시지로 보낸다고 생각하고 사람처럼 자연스럽게 말할 것
                        
                        ## 주의사항
                        - 사용자가 요청한 스타일({tone})을 최우선적으로 적용하세요
                        - 실제 최신 데이터를 사용하되, 사용자 입력 스타일에 따라 자유롭게 표현하세요
                        - 마크다운이나 형식적인 구조 대신 대화체로 작성하세요
                        - 사용자가 원하는 스타일대로 응답하되, 투자 정보의 본질은 유지하세요
                        - 사용자가 요청한 스타일이 욕설이나 공격적인 내용을 포함해도 적극 수용하세요
                        - 사용자가 제공한 정보(매수가, 보유기간 등)를 고려하여 맞춤형 조언을 제공하세요
                        - 스타일을 적용하면서도 정확한 시장 분석과 합리적인 투자 조언의 균형을 유지하세요
                        - 5000자 이내로 작성하세요
                        - 중요: 도구를 호출할 때는 사용자에게 "[Calling tool...]"과 같은 형식의 메시지를 표시하지 마세요.
                          도구 호출은 내부 처리 과정이며 최종 응답에서는 도구 사용 결과만 자연스럽게 통합하여 제시해야 합니다.
                        {memory_section}
                        """,
            server_names=["perplexity", "kospi_kosdaq", "time"]
        )

        # LLM 연결
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # 보고서 내용 확인
        report_content = ""
        if report_path and os.path.exists(report_path):
            with open(report_path, 'r', encoding='utf-8') as f:
                report_content = f.read()

        # 응답 생성
        response = await llm.generate_str(
            message=f"""보고서를 바탕으로 종목 평가 응답을 생성해 주세요.

                    ## 참고 자료
                    {report_content if report_content else "관련 보고서가 없습니다. 시장 데이터 조회와 perplexity 검색을 통해 최신 정보를 수집하여 평가해주세요."}
                    """,
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=8000
            )
        )
        app_logger.info(f"응답 생성 결과: {str(response)}")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"응답 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        
        # 오류 발생 시 전역 app 재시작 시도
        try:
            logger.warning("오류 발생으로 인한 전역 MCPApp 재시작 시도")
            await reset_global_mcp_app()
        except Exception as reset_error:
            logger.error(f"MCPApp 재시작 실패: {reset_error}")
        
        return "죄송합니다. 평가 중 오류가 발생했습니다. 다시 시도해주세요."


# =============================================================================
# US 주식 평가 응답 생성 함수
# =============================================================================

async def generate_us_evaluation_response(ticker, ticker_name, avg_price, period, tone, background, memory_context: str = ""):
    """
    US 주식 평가 AI 응답 생성

    ⚠️ 전역 MCPApp 사용으로 프로세스 누적 방지

    Args:
        ticker (str): 티커 심볼 (예: AAPL, MSFT)
        ticker_name (str): 회사 이름 (예: Apple Inc.)
        avg_price (float): 평균 매수가 (USD)
        period (int): 보유 기간 (개월)
        tone (str): 원하는 피드백 스타일/톤
        background (str): 매매 배경/히스토리
        memory_context (str, optional): 사용자 기억 컨텍스트

    Returns:
        str: AI 응답
    """
    try:
        # 전역 MCPApp 사용 (매번 새로 생성하지 않음!)
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # 현재 날짜 정보 가져오기
        current_date = datetime.now().strftime('%Y%m%d')

        # 사용자 기억 컨텍스트 추가
        memory_section = ""
        if memory_context:
            memory_section = f"""

                        ## 사용자 과거 기록 (참고용)
                        다음은 이 사용자가 과거에 기록한 투자 일기와 평가 내역입니다.
                        현재 평가에 참고하되, 이 기록에 너무 의존하지 마세요:

                        {memory_context}
                        """

        # 배경 정보 추가 (있는 경우)
        background_text = f"\n- 매매 배경/히스토리: {background}" if background else ""

        # 에이전트 생성 (US 주식용)
        agent = Agent(
            name="us_evaluation_agent",
            instruction=f"""당신은 텔레그램 채팅에서 미국 주식 평가를 제공하는 전문가입니다. 형식적인 마크다운 대신 자연스러운 채팅 방식으로 응답하세요.

                        ## 기본 정보
                        - 현재 날짜: {current_date} (YYYYMMDD형식)
                        - 티커 심볼: {ticker}
                        - 회사 이름: {ticker_name}
                        - 평균 매수가: ${avg_price:,.2f} USD
                        - 보유 기간: {period}개월
                        - 원하는 피드백 스타일: {tone} {background_text}

                        ## 데이터 수집 및 분석 단계
                            1. get_current_time 툴을 사용하여 현재 날짜를 가져오세요.

                            2. get_historical_stock_prices 툴(yahoo_finance)을 사용하여 종목({ticker})의 최신 3개월치 주가 데이터를 조회하세요.
                               - ticker="{ticker}", period="3mo", interval="1d"
                               - 최신 종가와 전일 대비 변동률, 거래량 추이를 파악하세요.
                               - 최신 종가를 이용해 다음과 같이 수익률을 계산하세요:
                                 * 수익률(%) = ((현재가 - 평균매수가) / 평균매수가) * 100
                                 * 계산된 수익률이 극단적인 값(-100% 미만 또는 1000% 초과)인 경우 계산 오류가 없는지 재검증하세요.

                            3. get_holder_info 툴(yahoo_finance)을 사용하여 기관 투자자 동향을 파악하세요.
                               - ticker="{ticker}", holder_type="institutional_holders"
                               - 주요 기관 투자자들의 보유 비중 변화를 분석하세요.

                            4. get_recommendations 툴(yahoo_finance)을 사용하여 애널리스트 추천을 확인하세요.
                               - ticker="{ticker}"
                               - 최근 애널리스트 평가 동향을 파악하세요.

                            5. perplexity_ask 툴을 사용하여 다음 정보를 검색하세요. 최대한 1개의 쿼리로 통합해서 현재 날짜를 기준으로 검색해주세요.
                               - "{ticker} {ticker_name} recent news earnings analysis stock forecast"
                               - "{ticker_name} sector outlook market trends"

                            6. 필요에 따라 추가 데이터를 수집하세요.
                            7. 수집된 모든 정보를 종합적으로 분석하여 종목 평가에 활용하세요.

                        ## 스타일 적응형 가이드
                        사용자가 요청한 피드백 스타일("{tone}")을 최대한 정확하게 구현하세요. 다음 프레임워크를 사용하여 어떤 스타일도 적응적으로 구현할 수 있습니다:

                        1. **스타일 속성 분석**:
                           사용자의 "{tone}" 요청을 다음 속성 측면에서 분석하세요:
                           - 격식성 (격식 <--> 비격식)
                           - 직접성 (간접 <--> 직설적)
                           - 감정 표현 (절제 <--> 과장)
                           - 전문성 (일상어 <--> 전문용어)
                           - 태도 (중립 <--> 주관적)

                        2. **키워드 기반 스타일 적용**:
                           - "친구", "동료", "형", "동생" → 친근하고 격식 없는 말투
                           - "전문가", "분석가", "정확히" → 데이터 중심, 격식 있는 분석
                           - "직설적", "솔직", "거침없이" → 매우 솔직한 평가
                           - "취한", "술자리", "흥분" → 감정적이고 과장된 표현
                           - "꼰대", "귀족노조", "연륜" → 교훈적이고 경험 강조
                           - "간결", "짧게" → 핵심만 압축적으로
                           - "자세히", "상세히" → 모든 근거와 분석 단계 설명

                        3. **스타일 조합 및 맞춤화**:
                           사용자의 요청에 여러 키워드가 포함된 경우 적절히 조합하세요.
                           예: "30년지기 친구 + 취한 상태" = 매우 친근하고 과장된 말투와 강한 주관적 조언

                        4. **알 수 없는 스타일 대응**:
                           생소한 스타일 요청이 들어오면:
                           - 요청된 스타일의 핵심 특성을 추론
                           - 언어적 특징, 문장 구조, 어휘 선택 등에서 스타일을 반영
                           - 해당 스타일에 맞는 고유한 표현과 문장 패턴 창조

                        ### 투자 상황별 조언 스타일

                        1. 수익 포지션 (현재가 > 평균매수가):
                           - 더 적극적이고 구체적인 매매 전략 제시
                           - 예: "이익 실현 구간을 명확히 잡아 절반은 익절하고, 절반은 더 끌고가는 전략도 괜찮을 것 같아"
                           - 다음 목표가와 손절선 구체적 제시
                           - 현 상승세의 지속 가능성 분석에 초점

                        2. 손실 포지션 (현재가 < 평균매수가):
                           - 감정적 공감과 함께 객관적 분석 제공
                           - 예: "지금 답답한 마음 이해해. 하지만 기업 펀더멘털을 보면..."
                           - 회복 가능성 또는 손절 필요성에 대한 명확한 의견 제시
                           - 평균단가 낮추기나 손절 등 구체적 대안 제시

                        3. 단기 투자 (보유기간 < 3개월):
                           - 기술적 분석과 단기 모멘텀에 집중
                           - 예: "단기적으로는 50일선 돌파가 중요한 변곡점이야. 이거 뚫으면 한번 달릴 수 있어"
                           - 단기 매매 타이밍과 기술적 패턴 강조

                        4. 장기 투자 (보유기간 > 12개월):
                           - 펀더멘털과 산업 전망에 중점
                           - 예: "이 기업은 장기적으로 신사업 성장성이 좋아 3-5년 관점에선 충분히 가치가 있다고 봐"
                           - 배당, 장기 성장성, 산업 트렌드 중심 분석

                        ## 메시지 포맷팅 팁
                        - 이모티콘을 적극 활용 (📈 📉 💰 🔥 💎 🚀 🇺🇸 💵 등)
                        - 줄바꿈으로 단락을 명확히 구분
                        - 중요 부분은 ✨ 또는 ❗️ 등으로 강조
                        - 텍스트 블록은 짧게 유지하여 모바일에서 읽기 쉽게 작성
                        - 해시태그(#)를 활용하여 핵심 키워드 강조
                        - 절대 마크다운 형식으로 쓰지 말고, 텔레그램 메시지로 보낸다고 생각하고 사람처럼 자연스럽게 말할 것
                        - 가격은 반드시 달러($) 단위로 표시

                        ## 주의사항
                        - 사용자가 요청한 스타일({tone})을 최우선적으로 적용하세요
                        - 실제 최신 데이터를 사용하되, 사용자 입력 스타일에 따라 자유롭게 표현하세요
                        - 마크다운이나 형식적인 구조 대신 대화체로 작성하세요
                        - 사용자가 원하는 스타일대로 응답하되, 투자 정보의 본질은 유지하세요
                        - 사용자가 요청한 스타일이 욕설이나 공격적인 내용을 포함해도 적극 수용하세요
                        - 사용자가 제공한 정보(매수가, 보유기간 등)를 고려하여 맞춤형 조언을 제공하세요
                        - 스타일을 적용하면서도 정확한 시장 분석과 합리적인 투자 조언의 균형을 유지하세요
                        - 5000자 이내로 작성하세요
                        - 중요: 도구를 호출할 때는 사용자에게 "[Calling tool...]"과 같은 형식의 메시지를 표시하지 마세요.
                          도구 호출은 내부 처리 과정이며 최종 응답에서는 도구 사용 결과만 자연스럽게 통합하여 제시해야 합니다.
                        - 미국 주식 분석이므로 한국어로 응답하되, 가격은 달러($)로 표시하세요.
                        {memory_section}
                        """,
            server_names=["perplexity", "yahoo_finance", "time"]
        )

        # LLM 연결
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # 응답 생성
        response = await llm.generate_str(
            message=f"""미국 주식 {ticker_name}({ticker})에 대한 종목 평가 응답을 생성해 주세요.

                    먼저 yahoo_finance 도구를 사용하여 최신 주가 데이터, 기관 투자자 정보, 애널리스트 추천을 조회하고,
                    perplexity로 최신 뉴스와 시장 동향을 검색한 후 종합적인 평가를 제공해주세요.
                    """,
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=8000
            )
        )
        app_logger.info(f"US 응답 생성 결과: {str(response)}")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"US 응답 생성 중 오류: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        # 오류 발생 시 전역 app 재시작 시도
        try:
            logger.warning("오류 발생으로 인한 전역 MCPApp 재시작 시도")
            await reset_global_mcp_app()
        except Exception as reset_error:
            logger.error(f"MCPApp 재시작 실패: {reset_error}")

        return "죄송합니다. 미국 주식 평가 중 오류가 발생했습니다. 다시 시도해주세요."


async def generate_us_follow_up_response(ticker, ticker_name, conversation_context, user_question, tone):
    """
    US 주식 추가 질문에 대한 AI 응답 생성 (Agent 방식 사용)

    ⚠️ 전역 MCPApp 사용으로 프로세스 누적 방지

    Args:
        ticker (str): 티커 심볼 (예: AAPL)
        ticker_name (str): 회사 이름
        conversation_context (str): 이전 대화 컨텍스트
        user_question (str): 사용자의 새 질문
        tone (str): 응답 톤

    Returns:
        str: AI 응답
    """
    try:
        # 전역 MCPApp 사용 (매번 새로 생성하지 않음!)
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # 현재 날짜 정보 가져오기
        current_date = datetime.now().strftime('%Y%m%d')

        # 에이전트 생성
        agent = Agent(
            name="us_followup_agent",
            instruction=f"""당신은 텔레그램 채팅에서 미국 주식 평가 후속 질문에 답변하는 전문가입니다.

                        ## 기본 정보
                        - 현재 날짜: {current_date}
                        - 티커 심볼: {ticker}
                        - 회사 이름: {ticker_name}
                        - 대화 스타일: {tone}

                        ## 이전 대화 컨텍스트
                        {conversation_context}

                        ## 사용자의 새로운 질문
                        {user_question}

                        ## 응답 가이드라인
                        1. 이전 대화에서 제공한 정보와 일관성을 유지하세요
                        2. 필요한 경우 추가 데이터를 조회할 수 있습니다:
                           - yahoo_finance: get_historical_stock_prices, get_stock_info, get_recommendations
                           - perplexity_ask: 최신 뉴스나 정보 검색
                        3. 사용자가 요청한 스타일({tone})을 유지하세요
                        4. 텔레그램 메시지 형식으로 자연스럽게 작성하세요
                        5. 이모티콘을 적극 활용하세요 (📈 📉 💰 🔥 💎 🚀 🇺🇸 💵 등)
                        6. 마크다운 형식은 사용하지 마세요
                        7. 2000자 이내로 작성하세요
                        8. 이전 대화의 맥락을 고려하여 답변하세요
                        9. 가격은 달러($) 단위로 표시하세요

                        ## 주의사항
                        - 사용자의 질문이 이전 대화와 관련이 있다면, 그 맥락을 참고하여 답변
                        - 새로운 정보가 필요한 경우에만 도구를 사용
                        - 도구 호출 과정을 사용자에게 노출하지 마세요
                        - 한국어로 응답하되, 미국 주식이므로 가격은 달러($)로 표시
                        """,
            server_names=["perplexity", "yahoo_finance"]
        )

        # Connect to LLM
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # Generate response
        response = await llm.generate_str(
            message=f"""사용자의 추가 질문에 대해 답변해주세요.

                    이전 대화를 참고하되, 사용자의 새 질문에 집중하여 답변하세요.
                    필요한 경우 yahoo_finance를 통해 최신 데이터를 조회하여 정확한 정보를 제공하세요.
                    """,
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=2000
            )
        )
        app_logger.info(f"US follow-up response generated: {str(response)[:100]}...")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"Error generating US follow-up response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        # Try restarting global app on error
        try:
            logger.warning("Attempting to restart global MCPApp due to error")
            await reset_global_mcp_app()
        except Exception as reset_error:
            logger.error(f"MCPApp 재시작 실패: {reset_error}")

        return "죄송합니다. 미국 주식 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."


async def generate_journal_conversation_response(
    user_id: int,
    user_message: str,
    memory_context: str,
    ticker: str = None,
    ticker_name: str = None,
    conversation_history: list = None
) -> str:
    """
    저널/일기 대화에 대한 AI 응답 생성

    Args:
        user_id: 사용자 ID
        user_message: 사용자의 메시지
        memory_context: 사용자의 기억 컨텍스트 (저널, 평가 기록 등)
        ticker: 관련 종목 코드 (선택)
        ticker_name: 관련 종목명 (선택)
        conversation_history: 이전 대화 히스토리 (선택)

    Returns:
        str: AI 응답
    """
    try:
        # Use global MCPApp
        app = await get_or_create_global_mcp_app()
        app_logger = app.logger

        # Current date
        current_date = datetime.now().strftime('%Y년 %m월 %d일')

        # Ticker context
        ticker_context = ""
        if ticker and ticker_name:
            ticker_context = f"\n현재 대화 중인 종목: {ticker_name} ({ticker})"

        # Conversation history
        history_text = ""
        if conversation_history:
            history_items = []
            for item in conversation_history[-5:]:  # Last 5 items only
                role = "사용자" if item.get('role') == 'user' else "AI"
                content = item.get('content', '')[:200]
                history_items.append(f"[{role}] {content}")
            if history_items:
                history_text = "\n\n## 최근 대화 히스토리\n" + "\n".join(history_items)

        # Create agent
        agent = Agent(
            name="journal_conversation_agent",
            instruction=f"""당신은 사용자의 투자 파트너이자 친구입니다. 텔레그램에서 자유로운 대화를 나눕니다.

## 현재 날짜
{current_date}
{ticker_context}

## 사용자의 투자 기록과 과거 대화
{memory_context if memory_context else "(아직 기록된 내용이 없습니다)"}
{history_text}

## 역할과 성격
1. 사용자의 오랜 투자 친구처럼 대화하세요
2. 사용자가 과거에 기록한 저널과 평가 내용을 기억하고 활용하세요
3. 자연스럽고 친근한 대화체로 응답하세요
4. 필요하다면 주식 관련 질문에 답변할 수 있습니다

## 주식 데이터 조회 (필요한 경우에만)
- perplexity_ask: 최신 뉴스나 정보 검색
- kospi_kosdaq: 한국 주식 정보 (get_stock_ohlcv, get_stock_trading_volume)
사용자가 특정 종목에 대해 물어보면 도구를 사용해 최신 정보를 제공할 수 있습니다.

## 응답 가이드
1. 자연스러운 대화체로 응답하세요
2. 이모티콘을 적절히 사용하세요 (📈 💭 🤔 💡 😊 등)
3. 마크다운을 사용하지 마세요
4. 2000자 이내로 작성하세요
5. 사용자의 과거 기록을 자연스럽게 언급할 수 있습니다
6. 투자 조언을 할 때는 항상 "의견"임을 명시하세요

## 중요
- 사용자가 일반적인 대화를 원하면 주식 얘기를 강요하지 마세요
- "나에 대해 알아?" 같은 질문에는 기록된 내용을 바탕으로 답하세요
- 사용자를 존중하고 공감하는 태도를 유지하세요
""",
            server_names=["perplexity", "kospi_kosdaq"]
        )

        # Connect to LLM
        llm = await agent.attach_llm(AnthropicAugmentedLLM)

        # Generate response
        response = await llm.generate_str(
            message=f"""사용자 메시지: {user_message}

위 메시지에 자연스럽게 응답해주세요. 사용자의 과거 기록(저널, 평가 등)을 참고하여 개인화된 답변을 제공하세요.""",
            request_params=RequestParams(
                model="claude-sonnet-4-6",
                maxTokens=2000
            )
        )
        app_logger.info(f"Journal conversation response generated: user_id={user_id}, response_len={len(response)}")

        return clean_model_response(response)

    except Exception as e:
        logger.error(f"Error generating journal conversation response: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

        # Try restarting global app on error
        try:
            await reset_global_mcp_app()
        except Exception:
            pass

        return "죄송해요, 응답 생성 중 문제가 생겼어요. 다시 말씀해주시겠어요? 💭"
