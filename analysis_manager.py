"""
Analysis request management and background task processing module
"""
import logging
import traceback
import uuid
import threading
from datetime import datetime
from queue import Queue

from report_generator import (
    get_cached_report, save_report, save_pdf_report,
    generate_report_response_sync,
    get_cached_us_report, save_us_report, save_us_pdf_report,
    generate_us_report_response_sync
)

# Logger setup
logger = logging.getLogger(__name__)

# Analysis task queue
analysis_queue = Queue()


from dataclasses import dataclass, field

@dataclass
class AnalysisRequest:
    """Analysis request object"""
    stock_code: str  # KR: stock code (6 digits), US: ticker symbol (AAPL, etc.)
    company_name: str
    chat_id: int = None  # Telegram chat ID
    user_id: int = None  # Telegram user ID (for daily limit refund on server error)
    avg_price: float = None
    period: int = None
    tone: str = None
    background: str = None
    message_id: int = None  # Message ID for status updates
    market_type: str = "kr"  # "kr" (Korea) or "us" (USA)
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: str = "pending"
    result: str = None
    report_path: str = None
    html_path: str = None  # Legacy field (kept for compatibility)
    pdf_path: str = None
    created_at: datetime = field(default_factory=datetime.now)


def _process_report_generic(request: AnalysisRequest, prefix: str, get_cache_fn, generate_fn, save_md_fn, save_pdf_fn):
    """Generic report processing logic for both US and KR markets"""
    is_cached, cached_content, cached_file, cached_pdf = get_cache_fn(request.stock_code)

    if is_cached:
        logger.info(f"Cached {prefix.upper() if prefix else 'KR'} report found: {cached_file}")
        request.result = cached_content
        request.status = "completed"
        request.report_path = cached_file
        request.pdf_path = cached_pdf
    else:
        logger.info(f"Performing new {prefix.upper() if prefix else 'KR'} analysis: {request.stock_code} - {request.company_name}")

        if request.avg_price and request.period:
            logger.info(f"{prefix.upper() + ' ' if prefix else ''}Evaluate request already processed: {request.id}")
            request.status = "skipped"
        else:
            report_result = generate_fn(request.stock_code, request.company_name)

            if report_result:
                request.result = report_result
                request.status = "completed"
                md_path = save_md_fn(request.stock_code, request.company_name, report_result)
                request.report_path = md_path
                pdf_path = save_pdf_fn(request.stock_code, request.company_name, md_path)
                request.pdf_path = pdf_path
            else:
                request.status = "failed"
                request.result = f"Error occurred during {prefix.upper() if prefix else 'KR'} stock analysis."

def _process_us_report(request: AnalysisRequest):
    _process_report_generic(
        request, "US", 
        get_cached_us_report, 
        generate_us_report_response_sync, 
        save_us_report, 
        save_us_pdf_report
    )

def _process_kr_report(request: AnalysisRequest):
    _process_report_generic(
        request, "", 
        get_cached_report, 
        generate_report_response_sync, 
        save_report, 
        save_pdf_report
    )

def _process_single_request(bot_instance, request: AnalysisRequest):
    logger.info(f"Worker: Starting analysis request processing - {request.id}")
    bot_instance.pending_requests[request.id] = request

    try:
        if request.market_type == "us":
            _process_us_report(request)
        else:
            _process_kr_report(request)

        logger.info(f"Analysis complete, adding to result queue: {request.id}")
        bot_instance.result_queue.put(request.id)

    except Exception as e:
        logger.error(f"Worker: Error during analysis processing - {str(e)}")
        logger.error(traceback.format_exc())
        request.status = "failed"
        request.result = f"Error occurred during analysis: {str(e)}"
        bot_instance.result_queue.put(request.id)

def start_background_worker(bot_instance):
    """
    Start background worker
    Create thread to process analysis requests
    """
    def worker():
        logger.info("Background worker started")
        while True:
            try:
                request = analysis_queue.get()
                _process_single_request(bot_instance, request)
            except Exception as e:
                logger.error(f"Worker: Error during request processing - {str(e)}")
                logger.error(traceback.format_exc())
            finally:
                analysis_queue.task_done()

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    logger.info("Background worker thread started.")
    return worker_thread