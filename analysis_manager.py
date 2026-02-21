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
                    # Use different cache/analysis functions based on market type
                    if request.market_type == "us":
                        # Process US stock report
                        is_cached, cached_content, cached_file, cached_pdf = get_cached_us_report(request.stock_code)

                        if is_cached:
                            logger.info(f"Cached US report found: {cached_file}")
                            request.result = cached_content
                            request.status = "completed"
                            request.report_path = cached_file
                            request.pdf_path = cached_pdf
                        else:
                            # Perform new US analysis
                            logger.info(f"Performing new US analysis: {request.stock_code} - {request.company_name}")

                            if request.avg_price and request.period:
                                logger.info(f"US Evaluate request already processed: {request.id}")
                                request.status = "skipped"
                            else:
                                # Generate US report (synchronous mode)
                                report_result = generate_us_report_response_sync(
                                    request.stock_code, request.company_name
                                )

                                if report_result:
                                    request.result = report_result
                                    request.status = "completed"

                                    # Save US report file
                                    md_path = save_us_report(
                                        request.stock_code, request.company_name, report_result
                                    )
                                    request.report_path = md_path

                                    # Generate US PDF
                                    pdf_path = save_us_pdf_report(
                                        request.stock_code, request.company_name, md_path
                                    )
                                    request.pdf_path = pdf_path
                                else:
                                    request.status = "failed"
                                    request.result = "Error occurred during US stock analysis."
                    else:
                        # Process Korean stock report (existing logic)
                        is_cached, cached_content, cached_file, cached_pdf = get_cached_report(request.stock_code)

                        if is_cached:
                            logger.info(f"Cached report found: {cached_file}")
                            request.result = cached_content
                            request.status = "completed"
                            request.report_path = cached_file
                            request.pdf_path = cached_pdf
                        else:
                            # Perform new analysis (using synchronous version)
                            logger.info(f"Performing new analysis: {request.stock_code} - {request.company_name}")

                            # Execute analysis (different prompts for evaluate vs report)
                            if request.avg_price and request.period:  # For evaluate command
                                # Evaluate requests are executed asynchronously, so not processed in background
                                # Already handled by telegram bot
                                logger.info(f"Evaluate request already processed: {request.id}")
                                request.status = "skipped"
                            else:  # For report command
                                # Execute synchronously
                                report_result = generate_report_response_sync(
                                    request.stock_code, request.company_name
                                )

                                if report_result:
                                    request.result = report_result
                                    request.status = "completed"

                                    # Save file
                                    md_path = save_report(
                                        request.stock_code, request.company_name, report_result
                                    )
                                    request.report_path = md_path

                                    # Generate PDF
                                    pdf_path = save_pdf_report(
                                        request.stock_code, request.company_name, md_path
                                    )
                                    request.pdf_path = pdf_path
                                else:
                                    request.status = "failed"
                                    request.result = "Error occurred during analysis."

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