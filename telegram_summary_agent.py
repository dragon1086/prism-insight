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

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# MCPApp 인스턴스 생성
app = MCPApp(name="telegram_summary")

class TelegramSummaryGenerator:
    """
    보고서 파일을 읽어 텔레그램 메시지 요약을 생성하는 클래스
    """

    def __init__(self):
        """생성자"""
        pass

    async def read_report(self, report_path):
        """
        보고서 파일 읽기
        """
        try:
            with open(report_path, 'r', encoding='utf-8') as file:
                content = file.read()
            return content
        except Exception as e:
            logger.error(f"보고서 파일 읽기 실패: {e}")
            raise

    def extract_metadata_from_filename(self, filename):
        """
        파일 이름에서 종목코드, 종목명, 날짜 등을 추출
        """
        pattern = r'(\w+)_(.+)_(\d{8})_.*\.pdf'
        match = re.match(pattern, filename)

        if match:
            stock_code = match.group(1)
            stock_name = match.group(2)
            date_str = match.group(3)

            # YYYYMMDD 형식을 YYYY.MM.DD 형식으로 변환
            formatted_date = f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:8]}"

            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "date": formatted_date
            }
        else:
            # 파일명에서 정보를 추출할 수 없는 경우, 기본값 설정
            return {
                "stock_code": "N/A",
                "stock_name": Path(filename).stem,
                "date": datetime.now().strftime("%Y.%m.%d")
            }

    def determine_trigger_type(self, stock_code: str, report_date=None):
        """
        트리거 결과 파일에서 해당 종목의 트리거 유형을 결정
        
        로직:
        1. morning과 afternoon 두 모드의 트리거 결과 파일을 모두 확인
        2. 둘 다 존재하는 경우, 최신 데이터인 afternoon 우선 선택
        3. 하나만 존재하는 경우, 해당 모드 선택
        4. 둘 다 없는 경우, 기본값 반환
        
        이는 매일 스케줄 실행 순서(morning → afternoon)를 고려하여,
        가장 최신의 시장 데이터를 활용하기 위함입니다.

        Args:
            stock_code: 종목 코드
            report_date: 보고서 날짜 (YYYYMMDD)

        Returns:
            tuple: (트리거 유형, 트리거 모드)
        """
        logger.info(f"종목 {stock_code}의 트리거 유형 결정 시작")

        # 날짜가 주어지지 않으면 현재 날짜 사용
        if report_date is None:
            report_date = datetime.now().strftime("%Y%m%d")
        elif report_date and "." in report_date:
            # YYYY.MM.DD 형식을 YYYYMMDD로 변환
            report_date = report_date.replace(".", "")

        # 각 모드별로 발견된 트리거 정보 저장
        found_triggers = {}  # {mode: (trigger_type, stocks)}
        
        # 가능한 모든 모드 확인 (morning, afternoon)
        for mode in ["morning", "afternoon"]:
            # 트리거 결과 파일 경로
            results_file = f"trigger_results_{mode}_{report_date}.json"

            logger.info(f"트리거 결과 파일 확인: {results_file}")

            if os.path.exists(results_file):
                try:
                    with open(results_file, 'r', encoding='utf-8') as f:
                        results = json.load(f)

                    # 모든 트리거 결과 확인 (metadata 제외)
                    for trigger_type, stocks in results.items():
                        if trigger_type != "metadata":
                            # 각 트리거 유형 확인
                            if isinstance(stocks, list):
                                for stock in stocks:
                                    if stock.get("code") == stock_code:
                                        # 해당 모드에서 트리거 발견
                                        found_triggers[mode] = (trigger_type, mode)
                                        logger.info(f"종목 {stock_code}의 트리거 발견 - 유형: {trigger_type}, 모드: {mode}")
                                        break
                        
                        # 이미 찾았으면 다음 trigger_type 확인 불필요
                        if mode in found_triggers:
                            break
                            
                except Exception as e:
                    logger.error(f"트리거 결과 파일 읽기 오류: {e}")

        # 우선순위에 따라 결과 반환: afternoon > morning
        if "afternoon" in found_triggers:
            trigger_type, mode = found_triggers["afternoon"]
            logger.info(f"최종 선택: afternoon 모드 - 트리거 유형: {trigger_type}")
            return trigger_type, mode
        elif "morning" in found_triggers:
            trigger_type, mode = found_triggers["morning"]
            logger.info(f"최종 선택: morning 모드 - 트리거 유형: {trigger_type}")
            return trigger_type, mode

        # 트리거 유형을 찾지 못한 경우 기본값 반환
        logger.warning(f"종목 {stock_code}의 트리거 유형을 결과 파일에서 찾지 못함, 기본값 사용")
        return "주목할 패턴", "unknown"

    def create_optimizer_agent(self, metadata, current_date, from_lang="ko", to_lang="ko"):
        """
        텔레그램 요약 생성 에이전트 생성

        Args:
            metadata: 종목 메타데이터
            current_date: 현재 날짜 (YYYY.MM.DD)
            from_lang: 보고서 원본 언어 (default: "ko")
            to_lang: 요약 타겟 언어 (default: "ko")
        """
        from cores.agents.telegram_summary_optimizer_agent import create_telegram_summary_optimizer_agent

        return create_telegram_summary_optimizer_agent(
            metadata=metadata,
            current_date=current_date,
            from_lang=from_lang,
            to_lang=to_lang
        )

    def create_evaluator_agent(self, current_date, from_lang="ko", to_lang="ko"):
        """
        텔레그램 요약 평가 에이전트 생성

        Args:
            current_date: 현재 날짜 (YYYY.MM.DD)
            from_lang: 보고서 원본 언어 (default: "ko")
            to_lang: 요약 타겟 언어 (default: "ko")
        """
        from cores.agents.telegram_summary_evaluator_agent import create_telegram_summary_evaluator_agent

        return create_telegram_summary_evaluator_agent(
            current_date=current_date,
            from_lang=from_lang,
            to_lang=to_lang
        )

    async def generate_telegram_message(self, report_content, metadata, trigger_type, from_lang="ko", to_lang="ko"):
        """
        텔레그램 메시지 생성 (평가 및 최적화 기능 추가)

        Args:
            report_content: 보고서 내용
            metadata: 종목 메타데이터
            trigger_type: 트리거 유형
            from_lang: 보고서 원본 언어 (default: "ko")
            to_lang: 요약 타겟 언어 (default: "ko")
        """
        # 현재 날짜 설정 (YYYY.MM.DD 형식)
        current_date = datetime.now().strftime("%Y.%m.%d")

        # 최적화 에이전트 생성
        optimizer = self.create_optimizer_agent(metadata, current_date, from_lang, to_lang)

        # 평가 에이전트 생성
        evaluator = self.create_evaluator_agent(current_date, from_lang, to_lang)

        # 평가-최적화 워크플로우 설정
        evaluator_optimizer = EvaluatorOptimizerLLM(
            optimizer=optimizer,
            evaluator=evaluator,
            llm_factory=OpenAIAugmentedLLM,
            min_rating=QualityRating.EXCELLENT
        )

        # 메시지 프롬프트 구성
        prompt_message = f"""다음은 {metadata['stock_name']}({metadata['stock_code']}) 종목에 대한 상세 분석 보고서입니다. 
            이 종목은 {trigger_type} 트리거에 포착되었습니다. 
            
            보고서 내용:
            {report_content}
            """

        # 트리거 모드가 morning인 경우 경고 문구 추가
        if metadata.get('trigger_mode') == 'morning':
            logger.info("장 시작 후 10분 시점 데이터 경고 문구 추가")
            prompt_message += "\n이 종목은 장 시작 후 10분 시점에 포착되었으며, 현재 상황과 차이가 있을 수 있습니다."

        # 평가-최적화 워크플로우를 사용하여 텔레그램 메시지 생성
        response = await evaluator_optimizer.generate_str(
            message=prompt_message,
            request_params=RequestParams(
                model="gpt-5.2",
                reasoning_effort="none",
                maxTokens=6000,
                max_iterations=2
            )
        )

        # 응답 처리 - 개선된 방식
        logger.info(f"응답 유형: {type(response)}")

        # 응답이 문자열인 경우 (가장 이상적인 케이스)
        if isinstance(response, str):
            logger.info("응답이 문자열 형식입니다.")
            # 이미 메시지 형식인지 확인
            if response.startswith(('📊', '📈', '📉', '💰', '⚠️', '🔍')):
                return response

            # 파이썬 객체 표현 찾아서 제거
            cleaned_response = re.sub(r'[A-Za-z]+\([^)]*\)', '', response)

            # 실제 메시지 내용만 추출 시도
            emoji_start = re.search(r'(📊|📈|📉|💰|⚠️|🔍)', cleaned_response)
            message_end = re.search(r'본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.', cleaned_response)

            if emoji_start and message_end:
                return cleaned_response[emoji_start.start():message_end.end()]

        # OpenAI API의 응답 객체인 경우 (content 속성이 있음)
        if hasattr(response, 'content') and response.content is not None:
            logger.info("응답에 content 속성이 있습니다.")
            return response.content

        # ChatCompletionMessage 케이스 - tool_calls가 있는 경우
        if hasattr(response, 'tool_calls') and response.tool_calls:
            logger.info("응답에 tool_calls가 있습니다.")

            # tool_calls 정보는 무시하고, function_call 결과가 있으면 그것을 반환
            if hasattr(response, 'function_call') and response.function_call:
                logger.info("응답에 function_call 결과가 있습니다.")
                return f"함수 호출 결과: {response.function_call}"

            # 이 부분에서는 후속 처리를 위해 텍스트 형식의 응답만 생성
            # 실제 tool_calls 처리는 별도 로직으로 구현 필요
            return "도구 호출 결과에서 텍스트를 추출할 수 없습니다. 관리자에게 문의하세요."

        # 마지막 시도: 문자열로 변환하고 정규식으로 메시지 형식 추출
        response_str = str(response)
        logger.debug(f"정규식 적용 전 응답 문자열: {response_str[:100]}...")

        # 정규식으로 텔레그램 메시지 형식 추출 시도
        content_match = re.search(r'(📊|📈|📉|💰|⚠️|🔍).*?본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다\.', response_str, re.DOTALL)

        if content_match:
            logger.info("정규식으로 메시지 내용을 추출했습니다.")
            return content_match.group(0)

        # 정규식으로도 찾지 못한 경우, 기본 메시지 반환
        logger.warning("응답에서 유효한 텔레그램 메시지를 추출할 수 없습니다.")
        logger.warning(f"정규식으로 추출하지 못한 원본 메시지 : {response_str[:100]}...")

        # 기본 메시지 생성
        default_message = f"""📊 {metadata['stock_name']}({metadata['stock_code']}) - 분석 요약
        
    1. 현재 주가: (정보 없음)
    2. 최근 동향: (정보 없음)
    3. 주요 체크포인트: 상세 분석 보고서를 참고하세요.
    
    ⚠️ 자동 생성 메시지 오류로 인해 상세 정보를 표시할 수 없습니다. 전체 보고서를 확인해 주세요.
    본 정보는 투자 참고용이며, 투자 결정과 책임은 투자자에게 있습니다."""

        return default_message

    def save_telegram_message(self, message, output_path):
        """
        생성된 텔레그램 메시지를 파일로 저장
        """
        try:
            # 디렉토리가 없으면 생성
            os.makedirs(os.path.dirname(output_path) or '.', exist_ok=True)

            with open(output_path, 'w', encoding='utf-8') as file:
                file.write(message)
            logger.info(f"텔레그램 메시지가 {output_path}에 저장되었습니다.")
        except Exception as e:
            logger.error(f"텔레그램 메시지 저장 실패: {e}")
            raise

    async def process_report(self, report_pdf_path, output_dir="telegram_messages", from_lang="ko", to_lang="ko"):
        """
        보고서 파일을 처리하여 텔레그램 요약 메시지 생성

        Args:
            report_pdf_path: 보고서 파일 경로
            output_dir: 출력 디렉토리
            from_lang: 보고서 원본 언어 (default: "ko")
            to_lang: 요약 타겟 언어 (default: "ko")
        """
        try:
            # 출력 디렉토리 생성
            os.makedirs(output_dir, exist_ok=True)

            # 파일 이름에서 메타데이터 추출
            filename = os.path.basename(report_pdf_path)
            metadata = self.extract_metadata_from_filename(filename)

            logger.info(f"처리 중: {filename} - {metadata['stock_name']}({metadata['stock_code']})")

            # 보고서 내용 읽기
            from pdf_converter import pdf_to_markdown_text
            report_content = pdf_to_markdown_text(report_pdf_path)

            # 트리거 유형과 모드 결정
            trigger_type, trigger_mode = self.determine_trigger_type(
                metadata['stock_code'],
                metadata.get('date', '').replace('.', '')  # YYYY.MM.DD → YYYYMMDD
            )
            logger.info(f"감지된 트리거 유형: {trigger_type}, 모드: {trigger_mode}")

            # 메타데이터에 트리거 모드 추가
            metadata['trigger_mode'] = trigger_mode

            # 텔레그램 요약 메시지 생성
            telegram_message = await self.generate_telegram_message(
                report_content, metadata, trigger_type, from_lang, to_lang
            )

            # 출력 파일 경로 생성
            output_file = os.path.join(output_dir, f"{metadata['stock_code']}_{metadata['stock_name']}_telegram.txt")

            # 메시지 저장
            self.save_telegram_message(telegram_message, output_file)

            logger.info(f"텔레그램 메시지 생성 완료: {output_file}")

            return telegram_message

        except Exception as e:
            logger.error(f"보고서 처리 중 오류 발생: {e}")
            raise

async def process_all_reports(reports_dir="pdf_reports", output_dir="telegram_messages", date_filter=None, from_lang="ko", to_lang="ko"):
    """
    지정된 디렉토리 내의 모든 보고서 파일을 처리

    Args:
        reports_dir: 보고서 디렉토리
        output_dir: 출력 디렉토리
        date_filter: 날짜 필터
        from_lang: 보고서 원본 언어 (default: "ko")
        to_lang: 요약 타겟 언어 (default: "ko")
    """
    # 텔레그램 요약 생성기 초기화
    generator = TelegramSummaryGenerator()

    # PDF 보고서 디렉토리 확인
    reports_path = Path(reports_dir)
    if not reports_path.exists() or not reports_path.is_dir():
        logger.error(f"보고서 디렉토리가 존재하지 않습니다: {reports_dir}")
        return

    # 보고서 파일 찾기
    report_files = list(reports_path.glob("*.md"))

    # 날짜 필터 적용
    if date_filter:
        report_files = [f for f in report_files if date_filter in f.name]

    if not report_files:
        logger.warning(f"처리할 보고서 파일이 없습니다. 디렉토리: {reports_dir}, 필터: {date_filter or '없음'}")
        return

    logger.info(f"{len(report_files)}개의 보고서 파일을 처리합니다.")

    # 각 보고서 처리
    for report_file in report_files:
        try:
            await generator.process_report(str(report_file), output_dir, from_lang, to_lang)
        except Exception as e:
            logger.error(f"{report_file.name} 처리 중 오류 발생: {e}")

    logger.info("모든 보고서 처리가 완료되었습니다.")

async def main():
    """
    메인 함수
    """
    import argparse

    parser = argparse.ArgumentParser(description="보고서 디렉토리의 모든 파일을 텔레그램 메시지로 요약합니다.")
    parser.add_argument("--reports-dir", default="reports", help="보고서 파일이 저장된 디렉토리 경로")
    parser.add_argument("--output-dir", default="telegram_messages", help="텔레그램 메시지 저장 디렉토리 경로")
    parser.add_argument("--date", help="특정 날짜의 보고서만 처리 (YYYYMMDD 형식)")
    parser.add_argument("--today", action="store_true", help="오늘 날짜의 보고서만 처리")
    parser.add_argument("--report", help="특정 보고서 파일만 처리")
    parser.add_argument("--from-lang", default="ko", help="보고서 원본 언어 코드 (default: ko)")
    parser.add_argument("--to-lang", default="ko", help="요약 타겟 언어 코드 (default: ko)")

    args = parser.parse_args()

    async with app.run() as parallel_app:
        logger = parallel_app.logger

        # 특정 보고서만 처리
        if args.report:
            report_pdf_path = args.report
            if not os.path.exists(report_pdf_path):
                logger.error(f"지정된 보고서 파일이 존재하지 않습니다: {report_pdf_path}")
                return

            generator = TelegramSummaryGenerator()
            telegram_message = await generator.process_report(
                report_pdf_path,
                args.output_dir,
                args.from_lang,
                args.to_lang
            )

            # 생성된 메시지 출력
            print("\n생성된 텔레그램 메시지:")
            print("-" * 50)
            print(telegram_message)
            print("-" * 50)

        else:
            # 오늘 날짜 필터 적용
            date_filter = None
            if args.today:
                date_filter = datetime.now().strftime("%Y%m%d")
            elif args.date:
                date_filter = args.date

            # 모든 pdf 보고서 처리
            await process_all_reports(
                reports_dir=args.reports_dir,
                output_dir=args.output_dir,
                date_filter=date_filter,
                from_lang=args.from_lang,
                to_lang=args.to_lang
            )

if __name__ == "__main__":
    asyncio.run(main())