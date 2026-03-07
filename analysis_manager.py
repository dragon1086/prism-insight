"""
Analysis request management and background task processing module

Uses ThreadPoolExecutor for efficient lifecycle management of analysis workers.
"""
import logging
import traceback
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from queue import Queue, Empty

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


class AnalysisWorker:
    """Manages analysis processing using ThreadPoolExecutor for clean lifecycle management.
    
    Replaces raw Thread + Queue pattern with ThreadPoolExecutor for:
    - Automatic thread lifecycle management
    - Graceful shutdown with pending task completion
    - Better error isolation between tasks
    """
    def __init__(self, bot_instance, max_workers: int = 2):
        self.bot_instance = bot_instance
        self.max_workers = max_workers
        self._executor = None
        self._stop_event = threading.Event()
        self._dispatcher_thread = None

    def start(self):
        """Start the analysis worker pool and dispatcher."""
        self._stop_event.clear()
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers,
            thread_name_prefix="analysis-worker"
        )
        self._dispatcher_thread = threading.Thread(
            target=self._dispatcher, daemon=True, name="analysis-dispatcher"
        )
        self._dispatcher_thread.start()
        logger.info(f"AnalysisWorker started with {self.max_workers} workers.")
        return self._dispatcher_thread

    def stop(self, wait=True):
        """Gracefully stop the worker pool."""
        self._stop_event.set()
        # Unblock queue.get() if waiting
        analysis_queue.put(None)
        if self._executor:
            self._executor.shutdown(wait=wait, cancel_futures=not wait)
        if wait and self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=10)
        logger.info("AnalysisWorker stopped.")

    def _dispatcher(self):
        """Dispatch incoming requests from the queue to the thread pool."""
        logger.info("Analysis dispatcher started")
        while not self._stop_event.is_set():
            try:
                request = analysis_queue.get(timeout=1.0)
                if request is None:  # Shutdown signal
                    analysis_queue.task_done()
                    break
                # Submit to thread pool instead of processing in-line
                future = self._executor.submit(
                    _process_single_request, self.bot_instance, request
                )
                future.add_done_callback(
                    lambda f, r=request: self._handle_completion(f, r)
                )
                analysis_queue.task_done()
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Dispatcher error: {str(e)}")
                logger.error(traceback.format_exc())

    def _handle_completion(self, future, request):
        """Handle completion of a submitted task."""
        exc = future.exception()
        if exc:
            logger.error(f"Analysis task {request.id} failed with exception: {exc}")
            request.status = "failed"
            request.result = f"Error occurred during analysis: {str(exc)}"
            self.bot_instance.result_queue.put(request.id)