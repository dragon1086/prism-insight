"""
Analysis request management and background task processing module
"""
import logging
import traceback
import uuid
import threading
from datetime import datetime
from queue import Queue

from prism_core.report_service import generate_report

# Logger setup
logger = logging.getLogger(__name__)

# Analysis task queue
analysis_queue = Queue()


class AnalysisRequest:
    """Analysis request object"""
    def __init__(self, stock_code: str, company_name: str, chat_id: int = None,
                 avg_price: float = None, period: int = None, tone: str = None,
                 background: str = None, message_id: int = None, market_type: str = "kr",
                 user_id: int = None):
        self.id = str(uuid.uuid4())
        self.stock_code = stock_code  # KR: stock code (6 digits), US: ticker symbol (AAPL, etc.)
        self.company_name = company_name
        self.chat_id = chat_id  # Telegram chat ID
        self.user_id = user_id  # Telegram user ID (for daily limit refund on server error)
        self.avg_price = avg_price
        self.period = period
        self.tone = tone
        self.background = background
        self.status = "pending"
        self.result = None
        self.report_path = None
        self.html_path = None  # Legacy field (kept for compatibility)
        self.pdf_path = None
        self.created_at = datetime.now()
        self.message_id = message_id  # Message ID for status updates
        self.market_type = market_type  # "kr" (Korea) or "us" (USA)


def start_background_worker(bot_instance):
    """
    Start background worker
    Create thread to process analysis requests
    """
    def worker():
        logger.info("Background worker started")
        while True:
            try:
                # Get task from queue (blocking)
                request = analysis_queue.get()
                logger.info(f"Worker: Starting analysis request processing - {request.id}")

                # Update request status
                bot_instance.pending_requests[request.id] = request

                try:
                    # Evaluate requests are handled asynchronously by the
                    # telegram bot itself, so the worker must not generate for
                    # them — but a cached report is still served if present.
                    is_evaluate = bool(request.avg_price and request.period)

                    artifact = generate_report(
                        request.stock_code,
                        request.company_name,
                        market=request.market_type,
                        cache_only=is_evaluate,
                    )

                    request.status = artifact.status
                    request.result = artifact.content
                    request.report_path = artifact.markdown_path
                    request.pdf_path = artifact.pdf_path

                    if is_evaluate and artifact.status == "skipped":
                        logger.info(f"Evaluate request already processed: {request.id}")

                    # Add to queue for result processing
                    logger.info(f"Analysis complete, adding to result queue: {request.id}")
                    bot_instance.result_queue.put(request.id)

                except Exception as e:
                    logger.error(f"Worker: Error during analysis processing - {str(e)}")
                    logger.error(traceback.format_exc())
                    request.status = "failed"
                    request.result = f"Error occurred during analysis: {str(e)}"
                    # Add to result queue even on error for processing
                    bot_instance.result_queue.put(request.id)

            except Exception as e:
                logger.error(f"Worker: Error during request processing - {str(e)}")
                logger.error(traceback.format_exc())
            finally:
                # Mark task as complete
                analysis_queue.task_done()

    # Start background thread
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    logger.info("Background worker thread started.")
    return worker_thread