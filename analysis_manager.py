"""
분석 요청 관리 및 백그라운드 작업 처리 모듈
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

# 로거 설정
logger = logging.getLogger(__name__)

# 분석 작업 큐
analysis_queue = Queue()


class AnalysisRequest:
    """분석 요청 객체"""
    def __init__(self, stock_code: str, company_name: str, chat_id: int = None,
                 avg_price: float = None, period: int = None, tone: str = None,
                 background: str = None, message_id: int = None, market_type: str = "kr"):
        self.id = str(uuid.uuid4())
        self.stock_code = stock_code  # KR: 종목코드(6자리), US: 티커심볼(AAPL 등)
        self.company_name = company_name
        self.chat_id = chat_id  # 텔레그램 채팅 ID
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
        self.message_id = message_id  # 상태 업데이트를 위한 메시지 ID
        self.market_type = market_type  # "kr" (한국) 또는 "us" (미국)


def start_background_worker(bot_instance):
    """
    백그라운드 작업자 시작
    스레드를 생성하여 분석 요청을 처리
    """
    def worker():
        logger.info("백그라운드 작업자 시작")
        while True:
            try:
                # 큐에서 작업 가져오기 (블로킹)
                request = analysis_queue.get()
                logger.info(f"작업자: 분석 요청 처리 시작 - {request.id}")

                # 요청 상태 업데이트
                bot_instance.pending_requests[request.id] = request

                try:
                    # 시장 타입에 따라 다른 캐시/분석 함수 사용
                    if request.market_type == "us":
                        # US 주식 보고서 처리
                        is_cached, cached_content, cached_file, cached_pdf = get_cached_us_report(request.stock_code)

                        if is_cached:
                            logger.info(f"캐시된 US 보고서 발견: {cached_file}")
                            request.result = cached_content
                            request.status = "completed"
                            request.report_path = cached_file
                            request.pdf_path = cached_pdf
                        else:
                            # 새로운 US 분석 수행
                            logger.info(f"새 US 분석 수행: {request.stock_code} - {request.company_name}")

                            if request.avg_price and request.period:
                                logger.info(f"US Evaluate 요청은 이미 처리됨: {request.id}")
                                request.status = "skipped"
                            else:
                                # US 보고서 생성 (동기 방식)
                                report_result = generate_us_report_response_sync(
                                    request.stock_code, request.company_name
                                )

                                if report_result:
                                    request.result = report_result
                                    request.status = "completed"

                                    # US 보고서 파일 저장
                                    md_path = save_us_report(
                                        request.stock_code, request.company_name, report_result
                                    )
                                    request.report_path = md_path

                                    # US PDF 생성
                                    pdf_path = save_us_pdf_report(
                                        request.stock_code, request.company_name, md_path
                                    )
                                    request.pdf_path = pdf_path
                                else:
                                    request.status = "failed"
                                    request.result = "US 주식 분석 중 오류가 발생했습니다."
                    else:
                        # 한국 주식 보고서 처리 (기존 로직)
                        is_cached, cached_content, cached_file, cached_pdf = get_cached_report(request.stock_code)

                        if is_cached:
                            logger.info(f"캐시된 보고서 발견: {cached_file}")
                            request.result = cached_content
                            request.status = "completed"
                            request.report_path = cached_file
                            request.pdf_path = cached_pdf
                        else:
                            # 새로운 분석 수행 (동기 실행 버전 사용)
                            logger.info(f"새 분석 수행: {request.stock_code} - {request.company_name}")

                            # 분석 실행 (evaluate vs report에 따라 다른 프롬프트 사용)
                            if request.avg_price and request.period:  # evaluate 명령의 경우
                                # evaluate 요청은 비동기로 실행되므로 백그라운드 작업에서는 처리하지 않음
                                # 이미 텔레그램 봇에서 처리됨
                                logger.info(f"Evaluate 요청은 이미 처리됨: {request.id}")
                                request.status = "skipped"
                            else:  # report 명령의 경우
                                # 동기 방식으로 실행
                                report_result = generate_report_response_sync(
                                    request.stock_code, request.company_name
                                )

                                if report_result:
                                    request.result = report_result
                                    request.status = "completed"

                                    # 파일 저장
                                    md_path = save_report(
                                        request.stock_code, request.company_name, report_result
                                    )
                                    request.report_path = md_path

                                    # PDF 생성
                                    pdf_path = save_pdf_report(
                                        request.stock_code, request.company_name, md_path
                                    )
                                    request.pdf_path = pdf_path
                                else:
                                    request.status = "failed"
                                    request.result = "분석 중 오류가 발생했습니다."
                    
                    # 결과 처리를 위한 큐에 추가
                    logger.info(f"분석 완료, 결과 큐에 추가: {request.id}")
                    bot_instance.result_queue.put(request.id)
                    
                except Exception as e:
                    logger.error(f"작업자: 분석 처리 중 오류 발생 - {str(e)}")
                    logger.error(traceback.format_exc())
                    request.status = "failed"
                    request.result = f"분석 중 오류가 발생했습니다: {str(e)}"
                    # 오류가 발생해도 결과 큐에 추가하여 처리
                    bot_instance.result_queue.put(request.id)
                
            except Exception as e:
                logger.error(f"작업자: 요청 처리 중 오류 발생 - {str(e)}")
                logger.error(traceback.format_exc())
            finally:
                # 작업 완료 표시
                analysis_queue.task_done()

    # 백그라운드 스레드 시작
    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()
    logger.info("백그라운드 작업자 스레드가 시작되었습니다.")
    return worker_thread