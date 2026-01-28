#!/usr/bin/env python3
"""
텔레그램 AI 대화형 봇

사용자 요청에 맞춤형 응답을 제공하는 봇:
- /evaluate 명령어를 통해 보유 종목에 대한 분석 및 조언 제공
- /report 명령어로 특정 종목에 대한 상세 분석 보고서 생성 및 HTML 파일 제공
- /history 명령어로 특정 종목의 분석 히스토리 확인
- 채널 구독자만 사용 가능
"""
import asyncio
import json
import logging
import os
import re
import signal
import traceback
from datetime import datetime
from pathlib import Path
from queue import Queue

from dotenv import load_dotenv
from telegram import Update, InputFile
from telegram.ext import (
    Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
)
from telegram.request import HTTPXRequest

from analysis_manager import (
    AnalysisRequest, analysis_queue, start_background_worker
)
# 내부 모듈 임포트
from report_generator import (
    generate_evaluation_response, get_cached_report, generate_follow_up_response,
    get_or_create_global_mcp_app, cleanup_global_mcp_app,
    generate_us_evaluation_response, generate_us_follow_up_response,
    get_cached_us_report, generate_journal_conversation_response
)
from tracking.user_memory import UserMemoryManager
from datetime import datetime, timedelta
from typing import Dict, Optional

# 환경 변수 로드
load_dotenv()

# 로거 설정
from logging.handlers import RotatingFileHandler
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            f"ai_bot_{datetime.now().strftime('%Y%m%d')}.log",
            maxBytes=10*1024*1024,  # 10MB
            backupCount=5
        )
    ]
)
logger = logging.getLogger(__name__)

from apscheduler.schedulers.asyncio import AsyncIOScheduler

# 상수 정의
REPORTS_DIR = Path("reports")
REPORTS_DIR.mkdir(exist_ok=True)  # 디렉토리가 없으면 생성
HTML_REPORTS_DIR = Path("html_reports")
HTML_REPORTS_DIR.mkdir(exist_ok=True)  # HTML 보고서 디렉토리

# 대화 상태 정의
CHOOSING_TICKER, ENTERING_AVGPRICE, ENTERING_PERIOD, ENTERING_TONE, ENTERING_BACKGROUND = range(5)
REPORT_CHOOSING_TICKER = 0  # /report 명령어를 위한 상태
HISTORY_CHOOSING_TICKER = 0  # /history 명령어를 위한 상태

# US 주식용 대화 상태 정의
US_CHOOSING_TICKER, US_ENTERING_AVGPRICE, US_ENTERING_PERIOD, US_ENTERING_TONE, US_ENTERING_BACKGROUND = range(5, 10)
US_REPORT_CHOOSING_TICKER = 10  # /us_report 명령어를 위한 상태

# 저널 대화 상태 정의
JOURNAL_ENTERING = 20  # /journal 명령어를 위한 상태

# 채널 ID
CHANNEL_ID = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))

class ConversationContext:
    """대화 컨텍스트 관리"""
    def __init__(self, market_type: str = "kr"):
        self.message_id = None
        self.chat_id = None
        self.user_id = None
        self.ticker = None
        self.ticker_name = None
        self.avg_price = None
        self.period = None
        self.tone = None
        self.background = None
        self.conversation_history = []
        self.created_at = datetime.now()
        self.last_updated = datetime.now()
        # 시장 타입: "kr" (한국) 또는 "us" (미국)
        self.market_type = market_type
        # 통화: KRW (한국) 또는 USD (미국)
        self.currency = "USD" if market_type == "us" else "KRW"

    def add_to_history(self, role: str, content: str):
        self.conversation_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        self.last_updated = datetime.now()

    def get_context_for_llm(self) -> str:
        # 통화 단위 설정
        if self.currency == "USD":
            price_str = f"${self.avg_price:,.2f}"
        else:
            price_str = f"{self.avg_price:,.0f}원"

        context = f"""
종목 정보: {self.ticker_name} ({self.ticker})
시장: {"미국" if self.market_type == "us" else "한국"}
평균 매수가: {price_str}
보유 기간: {self.period}개월
피드백 스타일: {self.tone}
매매 배경: {self.background if self.background else "없음"}

이전 대화 내역:"""

        for item in self.conversation_history:
            role_label = "AI 답변" if item['role'] == 'assistant' else "사용자 질문"
            context += f"\n\n{role_label}: {item['content']}"

        return context

    def is_expired(self, hours: int = 24) -> bool:
        return (datetime.now() - self.last_updated) > timedelta(hours=hours)


class TelegramAIBot:
    """텔레그램 AI 대화형 봇"""

    def __init__(self):
        """초기화"""
        self.token = os.getenv("TELEGRAM_AI_BOT_TOKEN")
        if not self.token:
            raise ValueError("텔레그램 봇 토큰이 설정되지 않았습니다.")

        # HTML 보고서 디렉토리 명시적 생성
        if not HTML_REPORTS_DIR.exists():
            HTML_REPORTS_DIR.mkdir(exist_ok=True)
            logger.info(f"HTML 보고서 디렉토리 생성: {HTML_REPORTS_DIR}")

        # 채널 ID 확인
        self.channel_id = int(os.getenv("TELEGRAM_CHANNEL_ID", "0"))
        if not self.channel_id:
            logger.warning("텔레그램 채널 ID가 설정되지 않았습니다. 채널 구독 확인을 건너뜁니다.")

        # 종목 정보 초기화
        self.stock_map = {}
        self.stock_name_map = {}
        self.load_stock_map()

        self.stop_event = asyncio.Event()

        # 진행 중인 분석 요청 관리
        self.pending_requests = {}

        # 결과 처리 큐 추가
        self.result_queue = Queue()
        
        # 대화 컨텍스트 저장소 추가
        self.conversation_contexts: Dict[int, ConversationContext] = {}

        # 저널 컨텍스트 저장소 (답장용)
        self.journal_contexts: Dict[int, Dict] = {}

        # 사용자 기억 관리자 초기화
        self.memory_manager = UserMemoryManager("user_memories.sqlite")

        # 일일 사용 제한 (user_id:command -> date)
        self.daily_report_usage: Dict[str, str] = {}

        # 봇 어플리케이션 생성 (타임아웃 설정 포함)
        request = HTTPXRequest(
            connection_pool_size=8,
            connect_timeout=30.0,
            read_timeout=120.0,   # 파일 전송 시 충분한 시간 확보
            write_timeout=120.0,
        )
        self.application = Application.builder().token(self.token).request(request).build()
        self.setup_handlers()

        # 백그라운드 작업자 시작
        start_background_worker(self)

        self.scheduler = AsyncIOScheduler()
        self.scheduler.add_job(self.load_stock_map, "interval", hours=12)
        # 만료된 컨텍스트 정리 작업 추가
        self.scheduler.add_job(self.cleanup_expired_contexts, "interval", hours=1)
        # 사용자 기억 압축 작업 추가 (매일 오전 3시)
        self.scheduler.add_job(self.compress_user_memories, "cron", hour=3, minute=0)
        self.scheduler.start()
    
    def cleanup_expired_contexts(self):
        """만료된 대화 컨텍스트 정리"""
        expired_keys = []
        for msg_id, context in self.conversation_contexts.items():
            if context.is_expired(hours=24):
                expired_keys.append(msg_id)

        for key in expired_keys:
            del self.conversation_contexts[key]
            logger.info(f"만료된 컨텍스트 삭제: 메시지 ID {key}")

        # 저널 컨텍스트도 정리 (24시간 이상 된 것)
        journal_expired = []
        now = datetime.now()
        for msg_id, ctx in self.journal_contexts.items():
            if (now - ctx.get('created_at', now)).total_seconds() > 86400:  # 24시간
                journal_expired.append(msg_id)

        for key in journal_expired:
            del self.journal_contexts[key]
            logger.info(f"만료된 저널 컨텍스트 삭제: 메시지 ID {key}")

        # 일일 사용 제한 정리 (오늘이 아닌 날짜 삭제)
        today = datetime.now().strftime("%Y-%m-%d")
        daily_limit_expired = [
            key for key, date in self.daily_report_usage.items()
            if date != today
        ]
        for key in daily_limit_expired:
            del self.daily_report_usage[key]
        if daily_limit_expired:
            logger.info(f"만료된 일일 제한 정리: {len(daily_limit_expired)}건")

    def compress_user_memories(self):
        """사용자 기억 압축 (야간 배치)"""
        if self.memory_manager:
            try:
                stats = self.memory_manager.compress_old_memories()
                logger.info(f"사용자 기억 압축 완료: {stats}")
            except Exception as e:
                logger.error(f"사용자 기억 압축 중 오류: {e}")

    def check_daily_limit(self, user_id: int, command: str) -> bool:
        """
        일일 사용 제한 확인.

        Args:
            user_id: 사용자 ID
            command: 명령어 (report, us_report)

        Returns:
            bool: True면 사용 가능, False면 이미 사용함
        """
        today = datetime.now().strftime("%Y-%m-%d")
        key = f"{user_id}:{command}"

        if self.daily_report_usage.get(key) == today:
            logger.info(f"일일 제한 초과: user={user_id}, command={command}")
            return False

        self.daily_report_usage[key] = today
        logger.info(f"일일 사용 기록: user={user_id}, command={command}")
        return True

    def load_stock_map(self):
        """
        종목 코드와 이름을 매핑하는 딕셔너리 로드
        """
        try:
            # 종목 정보 파일 경로
            stock_map_file = "stock_map.json"

            logger.info(f"종목 매핑 정보 로드 시도: {stock_map_file}")

            if os.path.exists(stock_map_file):
                with open(stock_map_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.stock_map = data.get("code_to_name", {})
                    self.stock_name_map = data.get("name_to_code", {})

                logger.info(f"{len(self.stock_map)} 개의 종목 정보 로드 완료")
            else:
                logger.warning(f"종목 정보 파일이 존재하지 않습니다: {stock_map_file}")
                # 기본 데이터를 제공 (테스트용)
                self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
                self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

        except Exception as e:
            logger.error(f"종목 정보 로드 실패: {e}")
            # 기본 데이터라도 제공
            self.stock_map = {"005930": "삼성전자", "013700": "까뮤이앤씨"}
            self.stock_name_map = {"삼성전자": "005930", "까뮤이앤씨": "013700"}

    def setup_handlers(self):
        """
        핸들러 등록
        """
        # 기본 명령어
        self.application.add_handler(CommandHandler("start", self.handle_start))
        self.application.add_handler(CommandHandler("help", self.handle_help))
        self.application.add_handler(CommandHandler("cancel", self.handle_cancel_standalone))
        self.application.add_handler(CommandHandler("memories", self.handle_memories))

        # 답장(Reply) 핸들러 - group=1로 등록하여 ConversationHandler(group=0)보다 낮은 우선순위
        # ConversationHandler가 먼저 처리하고, 매칭되지 않은 답장만 이 핸들러가 처리
        self.application.add_handler(MessageHandler(
            filters.REPLY & filters.TEXT & ~filters.COMMAND,
            self.handle_reply_to_evaluation
        ), group=1)

        # 보고서 명령어 핸들러
        report_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("report", self.handle_report_start),
                MessageHandler(filters.Regex(r'^/report(@\w+)?$'), self.handle_report_start)
            ],
            states={
                REPORT_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_report_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(report_conv_handler)

        # 히스토리 명령어 핸들러
        history_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("history", self.handle_history_start),
                MessageHandler(filters.Regex(r'^/history(@\w+)?$'), self.handle_history_start)
            ],
            states={
                HISTORY_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_history_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(history_conv_handler)

        # 평가 대화 핸들러
        conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("evaluate", self.handle_evaluate_start),
                # 그룹 채팅을 위한 패턴 추가
                MessageHandler(filters.Regex(r'^/evaluate(@\w+)?$'), self.handle_evaluate_start)
            ],
            states={
                CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_ticker_input)
                ],
                ENTERING_AVGPRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_avgprice_input)
                ],
                ENTERING_PERIOD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_period_input)
                ],
                ENTERING_TONE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_tone_input)
                ],
                ENTERING_BACKGROUND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_background_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                # 다른 명령어도 추가
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            # 그룹 채팅에서 다른 사용자의 메시지 구분
            per_chat=False,
            per_user=True,
            # 대화 시간 제한 (초)
            conversation_timeout=300,
        )
        self.application.add_handler(conv_handler)

        # ==========================================================================
        # US 주식 대화 핸들러
        # ==========================================================================

        # US 평가 대화 핸들러 (/us_evaluate)
        us_evaluate_handler = ConversationHandler(
            entry_points=[
                CommandHandler("us_evaluate", self.handle_us_evaluate_start),
                MessageHandler(filters.Regex(r'^/us_evaluate(@\w+)?$'), self.handle_us_evaluate_start)
            ],
            states={
                US_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_ticker_input)
                ],
                US_ENTERING_AVGPRICE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_avgprice_input)
                ],
                US_ENTERING_PERIOD: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_period_input)
                ],
                US_ENTERING_TONE: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_tone_input)
                ],
                US_ENTERING_BACKGROUND: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_background_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(us_evaluate_handler)

        # US 보고서 대화 핸들러 (/us_report)
        us_report_handler = ConversationHandler(
            entry_points=[
                CommandHandler("us_report", self.handle_us_report_start),
                MessageHandler(filters.Regex(r'^/us_report(@\w+)?$'), self.handle_us_report_start)
            ],
            states={
                US_REPORT_CHOOSING_TICKER: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_us_report_ticker_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(us_report_handler)

        # ==========================================================================
        # 저널(투자 일기) 대화 핸들러 (/journal)
        # ==========================================================================
        journal_conv_handler = ConversationHandler(
            entry_points=[
                CommandHandler("journal", self.handle_journal_start),
                MessageHandler(filters.Regex(r'^/journal(@\w+)?$'), self.handle_journal_start)
            ],
            states={
                JOURNAL_ENTERING: [
                    MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_journal_input)
                ]
            },
            fallbacks=[
                CommandHandler("cancel", self.handle_cancel),
                CommandHandler("start", self.handle_cancel),
                CommandHandler("help", self.handle_cancel)
            ],
            per_chat=False,
            per_user=True,
            conversation_timeout=300,
        )
        self.application.add_handler(journal_conv_handler)

        # 일반 텍스트 메시지 - /help 또는 /start 안내
        self.application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_default_message
        ))

        # 오류 핸들러
        self.application.add_error_handler(self.handle_error)
    
    async def handle_reply_to_evaluation(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평가 응답에 대한 답장 처리"""
        if not update.message or not update.message.reply_to_message:
            return
        
        # 답장 대상 메시지 ID 확인
        replied_to_msg_id = update.message.reply_to_message.message_id
        user_id = update.effective_user.id if update.effective_user else "unknown"
        text = update.message.text[:50] if update.message.text else "no text"

        logger.info(f"[REPLY] handle_reply_to_evaluation - user_id: {user_id}, replied_to: {replied_to_msg_id}, text: {text}")

        # 1. 저널 컨텍스트 확인 (저널 답장 처리)
        if replied_to_msg_id in self.journal_contexts:
            journal_ctx = self.journal_contexts[replied_to_msg_id]
            logger.info(f"[REPLY] journal_contexts에서 발견 - ticker: {journal_ctx.get('ticker')}")
            await self._handle_journal_reply(update, journal_ctx)
            return

        # 2. 평가 컨텍스트 확인
        if replied_to_msg_id not in self.conversation_contexts:
            # 컨텍스트가 없으면 일반 메시지로 처리
            logger.info(f"[REPLY] conversation_contexts에 없음, 스킵. keys: {list(self.conversation_contexts.keys())[:5]}")
            return
        
        conv_context = self.conversation_contexts[replied_to_msg_id]
        
        # 컨텍스트 만료 확인
        if conv_context.is_expired():
            # 시장 타입에 따라 다른 안내 메시지
            if conv_context.market_type == "us":
                await update.message.reply_text(
                    "이전 대화 세션이 만료되었습니다. 새로운 평가를 시작하려면 /us_evaluate 명령어를 사용해주세요."
                )
            else:
                await update.message.reply_text(
                    "이전 대화 세션이 만료되었습니다. 새로운 평가를 시작하려면 /evaluate 명령어를 사용해주세요."
                )
            del self.conversation_contexts[replied_to_msg_id]
            return

        # 사용자 메시지 가져오기
        user_question = update.message.text.strip()

        # 대기 메시지 (시장 타입에 따라)
        if conv_context.market_type == "us":
            waiting_message = await update.message.reply_text(
                "🇺🇸 추가 질문에 대해 분석 중입니다... 잠시만 기다려주세요. 💭"
            )
        else:
            waiting_message = await update.message.reply_text(
                "추가 질문에 대해 분석 중입니다... 잠시만 기다려주세요. 💭"
            )

        try:
            # 대화 히스토리에 사용자 질문 추가
            conv_context.add_to_history("user", user_question)

            # LLM에 전달할 컨텍스트 생성
            full_context = conv_context.get_context_for_llm()

            # 시장 타입에 따라 다른 응답 생성기 사용
            if conv_context.market_type == "us":
                # US 시장용 응답 생성
                response = await generate_us_follow_up_response(
                    conv_context.ticker,
                    conv_context.ticker_name,
                    full_context,
                    user_question,
                    conv_context.tone
                )
            else:
                # 한국 시장용 응답 생성 (기존)
                response = await generate_follow_up_response(
                    conv_context.ticker,
                    conv_context.ticker_name,
                    full_context,
                    user_question,
                    conv_context.tone
                )
            
            # 대기 메시지 삭제
            await waiting_message.delete()
            
            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 추가 질문이 있으시면 이 메시지에 답장(Reply)해주세요."
            )
            
            # 대화 히스토리에 AI 응답 추가
            conv_context.add_to_history("assistant", response)
            
            # 새 메시지 ID로 컨텍스트 업데이트
            conv_context.message_id = sent_message.message_id
            conv_context.user_id = update.effective_user.id
            self.conversation_contexts[sent_message.message_id] = conv_context
            
            logger.info(f"추가 질문 처리 완료: 사용자 {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"추가 질문 처리 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text(
                "죄송합니다. 추가 질문 처리 중 오류가 발생했습니다. 다시 시도해주세요."
            )

    async def send_report_result(self, request: AnalysisRequest):
        """분석 결과를 텔레그램으로 전송"""
        if not request.chat_id:
            logger.warning(f"채팅 ID가 없어 결과를 전송할 수 없습니다: {request.id}")
            return

        try:
            # PDF 파일 전송
            if request.pdf_path and os.path.exists(request.pdf_path):
                with open(request.pdf_path, 'rb') as file:
                    await self.application.bot.send_document(
                        chat_id=request.chat_id,
                        document=InputFile(file, filename=f"{request.company_name}_{request.stock_code}_분석.pdf"),
                        caption=f"✅ {request.company_name} ({request.stock_code}) 분석 보고서가 완료되었습니다."
                    )
            else:
                # PDF 파일이 없으면 텍스트로 결과 전송
                if request.result:
                    # 텍스트가 너무 길면 잘라서 전송
                    max_length = 4000  # 텔레그램 메시지 최대 길이
                    if len(request.result) > max_length:
                        summary = request.result[:max_length] + "...(이하 생략)"
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ {request.company_name} ({request.stock_code}) 분석 결과:\n\n{summary}"
                        )
                    else:
                        await self.application.bot.send_message(
                            chat_id=request.chat_id,
                            text=f"✅ {request.company_name} ({request.stock_code}) 분석 결과:\n\n{request.result}"
                        )
                else:
                    await self.application.bot.send_message(
                        chat_id=request.chat_id,
                        text=f"⚠️ {request.company_name} ({request.stock_code}) 분석 결과를 찾을 수 없습니다."
                    )
        except Exception as e:
            logger.error(f"결과 전송 중 오류: {str(e)}")
            logger.error(traceback.format_exc())
            await self.application.bot.send_message(
                chat_id=request.chat_id,
                text=f"⚠️ {request.company_name} ({request.stock_code}) 분석 결과 전송 중 오류가 발생했습니다."
            )

    @staticmethod
    async def handle_default_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """일반 메시지는 /help 또는 /start 안내"""
        # update.message이 None인지 확인
        if update.message is None:
            logger.warning(f"메시지가 없는 업데이트 수신: {update}")
            return

        # 디버그: 어떤 메시지가 여기로 오는지 확인
        user_id = update.effective_user.id if update.effective_user else "unknown"
        chat_id = update.effective_chat.id if update.effective_chat else "unknown"
        text = update.message.text[:50] if update.message.text else "no text"
        logger.debug(f"[DEFAULT] handle_default_message - user_id: {user_id}, chat_id: {chat_id}, text: {text}")

        return

    @staticmethod
    async def handle_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """시작 명령어 처리"""
        user = update.effective_user
        await update.message.reply_text(
            f"안녕하세요, {user.first_name}님! 저는 프리즘 어드바이저 봇입니다.\n\n"
            "저는 보유하신 종목에 대한 평가를 제공합니다.\n\n"
            "🇰🇷 <b>한국 주식</b>\n"
            "/evaluate - 보유 종목 평가 시작\n"
            "/report - 상세 분석 보고서 요청\n"
            "/history - 특정 종목의 분석 히스토리 확인\n\n"
            "🇺🇸 <b>미국 주식</b>\n"
            "/us_evaluate - 미국 주식 평가 시작\n"
            "/us_report - 미국 주식 보고서 요청\n\n"
            "📝 <b>투자 일기</b>\n"
            "/journal - 투자 일기 기록\n"
            "/memories - 내 기억 저장소 확인\n\n"
            "💡 평가 응답에 답장(Reply)하여 추가 질문을 할 수 있습니다!\n\n"
            "이 봇은 '프리즘 인사이트' 채널 구독자만 사용할 수 있습니다.\n"
            "채널에서는 장 시작과 마감 시 AI가 선별한 특징주 3개를 소개하고,\n"
            "각 종목에 대한 AI에이전트가 작성한 고퀄리티의 상세 분석 보고서를 제공합니다.\n\n"
            "다음 링크를 구독한 후 봇을 사용해주세요: https://t.me/stock_ai_agent",
            parse_mode="HTML"
        )

    @staticmethod
    async def handle_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """도움말 명령어 처리"""
        await update.message.reply_text(
            "📊 <b>프리즘 어드바이저 봇 도움말</b> 📊\n\n"
            "<b>기본 명령어:</b>\n"
            "/start - 봇 시작\n"
            "/help - 도움말 보기\n"
            "/cancel - 현재 진행 중인 대화 취소\n\n"
            "🇰🇷 <b>한국 주식 명령어:</b>\n"
            "/evaluate - 보유 종목 평가 시작\n"
            "/report - 상세 분석 보고서 요청\n"
            "/history - 특정 종목의 분석 히스토리 확인\n\n"
            "🇺🇸 <b>미국 주식 명령어:</b>\n"
            "/us_evaluate - 미국 주식 평가 시작\n"
            "/us_report - 미국 주식 보고서 요청\n\n"
            "📝 <b>투자 일기:</b>\n"
            "/journal - 투자 생각 기록\n"
            "/memories - 내 기억 저장소 확인\n"
            "  • 종목 코드/티커와 함께 입력 가능\n"
            "  • 과거 평가 시 기억으로 활용됨\n\n"
            "<b>보유 종목 평가 방법 (한국/미국 동일):</b>\n"
            "1. /evaluate 또는 /us_evaluate 명령어 입력\n"
            "2. 종목 코드/티커 입력 (예: 005930 또는 AAPL)\n"
            "3. 평균 매수가 입력 (원 또는 달러)\n"
            "4. 보유 기간 입력\n"
            "5. 원하는 피드백 스타일 입력\n"
            "6. 매매 배경 입력 (선택사항)\n"
            "7. 💡 AI 응답에 답장(Reply)하여 추가 질문 가능!\n\n"
            "<b>✨ 추가 질문 기능:</b>\n"
            "• AI의 평가 메시지에 답장하여 추가 질문\n"
            "• 이전 대화 컨텍스트를 유지하여 연속적인 대화 가능\n"
            "• 24시간 동안 대화 세션 유지\n\n"
            "<b>상세 분석 보고서 요청:</b>\n"
            "1. /report 명령어 입력\n"
            "2. 종목 코드 또는 이름 입력\n"
            "3. 5-10분 후 상세 보고서가 제공됩니다(요청이 많을 경우 더 길어짐)\n\n"
            "<b>주의:</b>\n"
            "이 봇은 채널 구독자만 사용할 수 있습니다.",
            parse_mode="HTML"
        )

    async def handle_memories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """내 기억 조회 명령어 처리"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        try:
            # 기억 통계 조회
            stats = self.memory_manager.get_memory_stats(user_id)

            if not stats or stats.get('total', 0) == 0:
                await update.message.reply_text(
                    f"📭 {user_name}님의 저장된 기억이 없습니다.\n\n"
                    "/journal 명령어로 투자 일기를 기록해보세요!",
                    parse_mode="HTML"
                )
                return

            # 기억 목록 조회
            memories = self.memory_manager.get_memories(user_id, limit=20)

            # 응답 메시지 생성
            msg_parts = [f"🧠 <b>{user_name}님의 기억 저장소</b>\n"]

            # 통계
            by_type = stats.get('by_type', {})
            msg_parts.append(f"\n📊 <b>저장된 기억: {stats.get('total', 0)}개</b>")
            if by_type:
                type_labels = {
                    'journal': '📝 저널',
                    'evaluation': '📈 평가',
                    'report': '📋 보고서',
                    'conversation': '💬 대화'
                }
                for mem_type, count in by_type.items():
                    label = type_labels.get(mem_type, mem_type)
                    msg_parts.append(f"  • {label}: {count}개")

            # 종목별 통계
            by_ticker = stats.get('by_ticker', {})
            if by_ticker:
                msg_parts.append(f"\n🏷️ <b>종목별 기록:</b>")
                for ticker, count in list(by_ticker.items())[:5]:
                    msg_parts.append(f"  • {ticker}: {count}개")

            # 최근 기억 상세
            msg_parts.append(f"\n\n📜 <b>최근 기억 (최대 10개):</b>\n")
            for i, mem in enumerate(memories[:10], 1):
                created = mem.get('created_at', '')[:10]
                mem_type = mem.get('memory_type', '')
                ticker = mem.get('ticker', '')
                ticker_name = mem.get('ticker_name', '')
                content = mem.get('content', {})

                # 내용 미리보기 (100자)
                text = content.get('text', content.get('response_summary', ''))[:100]
                if len(text) >= 100:
                    text = text[:97] + "..."

                # 종목 표시
                ticker_str = f" [{ticker_name or ticker}]" if ticker else ""

                # 타입 이모지
                type_emoji = {'journal': '📝', 'evaluation': '📈', 'report': '📋', 'conversation': '💬'}.get(mem_type, '💭')

                msg_parts.append(f"{i}. {type_emoji} {created}{ticker_str}")
                if text:
                    msg_parts.append(f"   <i>{text}</i>")
                msg_parts.append("")

            response = "\n".join(msg_parts)

            # 메시지 길이 제한 (4096자)
            if len(response) > 4000:
                response = response[:3997] + "..."

            await update.message.reply_text(response, parse_mode="HTML")

        except Exception as e:
            logger.error(f"Error in handle_memories: {e}", exc_info=True)
            await update.message.reply_text(
                "⚠️ 기억 조회 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )

    async def handle_report_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보고서 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 일일 사용 제한 확인
        if not self.check_daily_limit(user_id, "report"):
            await update.message.reply_text(
                "⚠️ /report 명령어는 하루에 1회만 사용할 수 있습니다.\n\n"
                "내일 다시 이용해 주세요."
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}상세 분석 보고서를 생성할 종목 코드나 이름을 입력해주세요.\n"
            "예: 005930 또는 삼성전자"
        )

        return REPORT_CHOOSING_TICKER

    async def handle_report_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보고서 요청 종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        chat_id = update.effective_chat.id

        logger.info(f"보고서 종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return REPORT_CHOOSING_TICKER

        # 대기 메시지 전송
        waiting_message = await update.message.reply_text(
            f"📊 {stock_name} ({stock_code}) 분석 보고서 생성 요청이 등록되었습니다.\n\n"
            f"요청은 도착 순서대로 처리되며, 한 건당 분석에 약 5-10분이 소요됩니다.\n\n"
            f"다른 사용자의 요청이 많을 경우 대기 시간이 길어질 수 있습니다.\n\n "
            f"완료되면 바로 알려드리겠습니다."
        )

        # 분석 요청 생성 및 큐에 추가
        request = AnalysisRequest(
            stock_code=stock_code,
            company_name=stock_name,
            chat_id=chat_id,
            message_id=waiting_message.message_id
        )

        # 캐시된 보고서가 있는지 확인
        is_cached, cached_content, cached_file, cached_pdf = get_cached_report(stock_code)

        if is_cached:
            logger.info(f"캐시된 보고서 발견: {cached_file}")
            # 캐시된 보고서가 있는 경우 바로 결과 전송
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.pdf_path = cached_pdf

            await waiting_message.edit_text(
                f"✅ {stock_name} ({stock_code}) 분석 보고서가 준비되었습니다. 잠시 후 전송됩니다."
            )

            # 결과 전송
            await self.send_report_result(request)
        else:
            # 새로운 분석 필요
            self.pending_requests[request.id] = request
            analysis_queue.put(request)

        return ConversationHandler.END

    async def handle_history_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """히스토리 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}분석 히스토리를 확인할 종목 코드나 이름을 입력해주세요.\n"
            "예: 005930 또는 삼성전자"
        )

        return HISTORY_CHOOSING_TICKER

    async def handle_history_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """히스토리 요청 종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()

        logger.info(f"히스토리 종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return HISTORY_CHOOSING_TICKER

        # 히스토리 찾기
        reports = list(REPORTS_DIR.glob(f"{stock_code}_*.md"))

        if not reports:
            await update.message.reply_text(
                f"{stock_name} ({stock_code}) 종목에 대한 분석 히스토리가 없습니다.\n"
                f"/report 명령어를 사용하여 새 분석을 요청해보세요."
            )
            return ConversationHandler.END

        # 날짜별로 정렬
        reports.sort(key=lambda x: x.stat().st_mtime, reverse=True)

        # 히스토리 메시지 구성
        history_msg = f"📋 {stock_name} ({stock_code}) 분석 히스토리:\n\n"

        for i, report in enumerate(reports[:5], 1):
            report_date = datetime.fromtimestamp(report.stat().st_mtime).strftime('%Y-%m-%d %H:%M')
            history_msg += f"{i}. {report_date}\n"

            # 파일 크기 추가
            file_size = report.stat().st_size / 1024  # KB
            history_msg += f"   크기: {file_size:.1f} KB\n"

            # 첫 줄 미리보기 추가
            try:
                with open(report, 'r', encoding='utf-8') as f:
                    first_line = next(f, "").strip()
                    if first_line:
                        preview = first_line[:50] + "..." if len(first_line) > 50 else first_line
                        history_msg += f"   미리보기: {preview}\n"
            except Exception:
                pass

            history_msg += "\n"

        if len(reports) > 5:
            history_msg += f"그 외 {len(reports) - 5}개의 분석 기록이 있습니다.\n"

        history_msg += "\n최신 분석 보고서를 확인하려면 /report 명령어를 사용하세요."

        await update.message.reply_text(history_msg)
        return ConversationHandler.END

    async def check_channel_subscription(self, user_id):
        """
        사용자가 채널을 구독하고 있는지 확인

        Args:
            user_id: 사용자 ID

        Returns:
            bool: 구독 여부
        """
        try:
            # 채널 ID가 설정되지 않았으면 항상 true 반환
            if not self.channel_id:
                return True

            # 운영자 ID 허용 리스트
            admin_ids_str = os.getenv("TELEGRAM_ADMIN_IDS", "")
            admin_ids = [int(id_str) for id_str in admin_ids_str.split(",") if id_str.strip()]

            # 운영자인 경우 항상 허용
            if user_id in admin_ids:
                logger.info(f"운영자 {user_id} 접근 허용")
                return True

            member = await self.application.bot.get_chat_member(
                self.channel_id, user_id
            )
            # 상태 확인 및 로깅 추가
            logger.info(f"사용자 {user_id}의 채널 멤버십 상태: {member.status}")

            # 채널 멤버, 관리자, 생성자/소유자 모두 허용
            # 'creator'는 초기 버전에서 사용, 일부 버전에서는 'owner'로 변경될 수 있음
            valid_statuses = ['member', 'administrator', 'creator', 'owner']

            # 채널 소유자인 경우 항상 허용
            if member.status == 'creator' or getattr(member, 'is_owner', False):
                return True

            return member.status in valid_statuses
        except Exception as e:
            logger.error(f"채널 구독 확인 중 오류: {e}")
            # 디버깅을 위해 예외 상세 정보 로깅
            logger.error(f"상세 오류: {traceback.format_exc()}")
            return False

    async def handle_evaluate_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평가 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]

        logger.info(f"평가 명령 시작 - 사용자: {user_name}, 채팅타입: {'그룹' if is_group else '개인'}")

        # 그룹 채팅에서는 사용자 이름을 언급
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}보유하신 종목의 코드나 이름을 입력해주세요. \n"
            "예: 005930 또는 삼성전자"
        )
        return CHOOSING_TICKER

    async def handle_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """종목 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        logger.info(f"종목 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 종목 코드 또는 이름을 처리
        stock_code, stock_name, error_message = await self.get_stock_code(user_input)

        if error_message:
            # 오류가 있으면 사용자에게 알리고 다시 입력 받음
            await update.message.reply_text(error_message)
            return CHOOSING_TICKER

        # 종목 정보 저장
        context.user_data['ticker'] = stock_code
        context.user_data['ticker_name'] = stock_name

        logger.info(f"종목 선택: {stock_name} ({stock_code})")

        await update.message.reply_text(
            f"{stock_name} ({stock_code}) 종목을 선택하셨습니다.\n\n"
            f"평균 매수가를 입력해주세요. (숫자만 입력)\n"
            f"예: 68500"
        )

        logger.info(f"상태 전환: ENTERING_AVGPRICE - 사용자: {user_id}")
        return ENTERING_AVGPRICE

    @staticmethod
    async def handle_avgprice_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """평균 매수가 입력 처리"""
        try:
            avg_price = float(update.message.text.strip().replace(',', ''))
            context.user_data['avg_price'] = avg_price

            await update.message.reply_text(
                f"보유 기간을 입력해주세요. (개월 수)\n"
                f"예: 6 (6개월)"
            )
            return ENTERING_PERIOD

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요. 콤마는 제외해주세요.\n"
                "예: 68500"
            )
            return ENTERING_AVGPRICE

    @staticmethod
    async def handle_period_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """보유 기간 입력 처리"""
        try:
            period = int(update.message.text.strip())
            context.user_data['period'] = period

            # 다음 단계: 원하는 피드백 스타일/톤 입력 받기
            await update.message.reply_text(
                "어떤 스타일이나 말투로 피드백을 받고 싶으신가요?\n"
                "예: 솔직하게, 전문적으로, 친구같이, 간결하게 등"
            )
            return ENTERING_TONE

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요.\n"
                "예: 6"
            )
            return ENTERING_PERIOD

    @staticmethod
    async def handle_tone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """원하는 피드백 스타일/톤 입력 처리"""
        tone = update.message.text.strip()
        context.user_data['tone'] = tone

        await update.message.reply_text(
            "종목을 매매하게 된 배경이나 주요 매매 히스토리가 있으시면 알려주세요.\n"
            "(선택사항이므로, 없으면 '없음'이라고 입력해주세요)"
        )
        return ENTERING_BACKGROUND

    async def handle_background_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """매매 배경 입력 처리 및 AI 응답 생성"""
        background = update.message.text.strip()
        context.user_data['background'] = background if background.lower() != '없음' else ""

        # 응답 대기 메시지
        waiting_message = await update.message.reply_text(
            "종목 분석 중입니다... 잠시만 기다려주세요."
        )

        # AI 에이전트로 분석 요청
        ticker = context.user_data['ticker']
        ticker_name = context.user_data.get('ticker_name', f"종목_{ticker}")
        avg_price = context.user_data['avg_price']
        period = context.user_data['period']
        tone = context.user_data['tone']
        background = context.user_data['background']
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        try:
            # 사용자 기억 컨텍스트 조회
            memory_context = ""
            if self.memory_manager:
                memory_context = self.memory_manager.build_llm_context(
                    user_id=user_id,
                    ticker=ticker
                )
                if memory_context:
                    logger.info(f"사용자 기억 컨텍스트 로드됨: {len(memory_context)} chars")

            # AI 응답 생성 (memory_context 포함)
            response = await generate_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background,
                memory_context=memory_context
            )

            # 응답이 비어있는지 확인
            if not response or not response.strip():
                response = "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."
                logger.error(f"빈 응답이 생성되었습니다: {ticker_name}({ticker})")

            # 대기 메시지 삭제
            await waiting_message.delete()

            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 추가 질문이 있으시면 이 메시지에 답장(Reply)해주세요."
            )
            
            # 대화 컨텍스트 저장
            conv_context = ConversationContext()
            conv_context.message_id = sent_message.message_id
            conv_context.chat_id = chat_id
            conv_context.user_id = update.effective_user.id
            conv_context.ticker = ticker
            conv_context.ticker_name = ticker_name
            conv_context.avg_price = avg_price
            conv_context.period = period
            conv_context.tone = tone
            conv_context.background = background
            conv_context.add_to_history("assistant", response)
            
            # 컨텍스트 저장
            self.conversation_contexts[sent_message.message_id] = conv_context
            logger.info(f"대화 컨텍스트 저장: 메시지 ID {sent_message.message_id}")

            # 평가 결과를 사용자 기억에 저장
            if self.memory_manager:
                self.memory_manager.save_memory(
                    user_id=user_id,
                    memory_type=self.memory_manager.MEMORY_EVALUATION,
                    content={
                        'ticker': ticker,
                        'ticker_name': ticker_name,
                        'avg_price': avg_price,
                        'period': period,
                        'tone': tone,
                        'background': background,
                        'response_summary': response[:500]  # 응답 요약 저장
                    },
                    ticker=ticker,
                    ticker_name=ticker_name,
                    market_type='kr',
                    command_source='/evaluate',
                    message_id=sent_message.message_id
                )
                logger.info(f"평가 결과 기억에 저장: user={user_id}, ticker={ticker}")

        except Exception as e:
            logger.error(f"응답 생성 또는 전송 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("죄송합니다. 분석 중 오류가 발생했습니다. 다시 시도해주세요.")

        # 대화 종료
        return ConversationHandler.END

    @staticmethod
    async def handle_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """대화 취소 처리 (ConversationHandler 내부에서 호출)"""
        # 사용자 데이터 초기화
        context.user_data.clear()

        await update.message.reply_text(
            "요청이 취소되었습니다.\n\n"
            "🇰🇷 한국 주식: /evaluate, /report, /history\n"
            "🇺🇸 미국 주식: /us_evaluate, /us_report"
        )
        return ConversationHandler.END

    @staticmethod
    async def handle_cancel_standalone(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """대화 취소 처리 (대화 밖에서 호출)"""
        await update.message.reply_text(
            "현재 진행 중인 대화가 없습니다.\n\n"
            "🇰🇷 한국 주식: /evaluate, /report, /history\n"
            "🇺🇸 미국 주식: /us_evaluate, /us_report"
        )

    @staticmethod
    async def handle_error(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """오류 처리"""
        error_msg = str(context.error)
        logger.error(f"오류 발생: {error_msg}")

        # 사용자에게 보여줄 오류 메시지
        user_msg = "죄송합니다. 오류가 발생했습니다. 다시 시도해주세요."

        # 타임아웃 오류 처리
        if "timed out" in error_msg.lower():
            user_msg = "요청 처리 시간이 초과되었습니다. 네트워크 상태를 확인하고 다시 시도해주세요."
        # 권한 오류 처리
        elif "permission" in error_msg.lower():
            user_msg = "봇이 메시지를 보낼 권한이 없습니다. 그룹 설정을 확인해주세요."
        # 다양한 오류 정보 로깅
        logger.error(f"오류 상세 정보: {traceback.format_exc()}")

        # 오류 응답 전송
        if update and update.effective_message:
            await update.effective_message.reply_text(user_msg)

    async def get_stock_code(self, stock_input):
        """
        종목명 또는 코드를 입력받아 종목 코드로 변환

        Args:
            stock_input (str): 종목 코드 또는 이름

        Returns:
            tuple: (종목 코드, 종목 이름, 오류 메시지)
        """
        # 입력값 방어코드
        if not stock_input:
            logger.warning("빈 입력값이 전달됨")
            return None, None, "종목명 또는 코드를 입력해주세요."

        if not isinstance(stock_input, str):
            logger.warning(f"잘못된 입력 타입: {type(stock_input)}")
            stock_input = str(stock_input)

        original_input = stock_input
        stock_input = stock_input.strip()

        logger.info(f"종목 검색 시작 - 입력: '{original_input}' -> 정리된 입력: '{stock_input}'")

        # stock_name_map 상태 확인
        if not hasattr(self, 'stock_name_map') or self.stock_name_map is None:
            logger.error("stock_name_map이 초기화되지 않음")
            return None, None, "시스템 오류: 종목 데이터가 로드되지 않았습니다."

        if not isinstance(self.stock_name_map, dict):
            logger.error(f"stock_name_map 타입 오류: {type(self.stock_name_map)}")
            return None, None, "시스템 오류: 종목 데이터 형식이 잘못되었습니다."

        logger.info(f"stock_name_map 상태 - 크기: {len(self.stock_name_map)}")

        # stock_map 상태 확인
        if not hasattr(self, 'stock_map') or self.stock_map is None:
            logger.warning("stock_map이 초기화되지 않음")
            self.stock_map = {}

        # 이미 종목 코드인 경우 (6자리 숫자)
        if re.match(r'^\d{6}$', stock_input):
            logger.info(f"6자리 숫자 코드로 인식: {stock_input}")
            stock_code = stock_input
            stock_name = self.stock_map.get(stock_code)

            if stock_name:
                logger.info(f"종목 코드 매칭 성공: {stock_code} -> {stock_name}")
                return stock_code, stock_name, None
            else:
                logger.warning(f"종목 코드 {stock_code}에 대한 이름 정보 없음")
                return stock_code, f"종목_{stock_code}", "해당 종목 코드에 대한 정보가 없습니다. 코드가 정확한지 확인해주세요."

        # 종목명으로 입력한 경우 - 정확히 일치하는 경우 확인
        logger.info(f"종목명 정확 일치 검색 시작: '{stock_input}'")

        # 디버깅을 위한 키 샘플 로깅
        sample_keys = list(self.stock_name_map.keys())[:5]
        logger.debug(f"stock_name_map 키 샘플: {sample_keys}")

        # 정확 일치 검사
        if stock_input in self.stock_name_map:
            stock_code = self.stock_name_map[stock_input]
            logger.info(f"정확 일치 성공: '{stock_input}' -> {stock_code}")
            return stock_code, stock_input, None
        else:
            logger.info(f"정확 일치 실패: '{stock_input}'")

            # 입력값의 상세 정보 로깅
            logger.debug(f"입력값 상세 - 길이: {len(stock_input)}, "
                         f"바이트: {stock_input.encode('utf-8')}, "
                         f"유니코드: {[ord(c) for c in stock_input]}")

        # 종목명 부분 일치 검색
        logger.info(f"부분 일치 검색 시작")
        possible_matches = []

        try:
            for name, code in self.stock_name_map.items():
                if not isinstance(name, str) or not isinstance(code, str):
                    logger.warning(f"잘못된 데이터 타입: name={type(name)}, code={type(code)}")
                    continue

                if stock_input.lower() in name.lower():
                    possible_matches.append((name, code))
                    logger.debug(f"부분 일치 발견: '{name}' ({code})")

        except Exception as e:
            logger.error(f"부분 일치 검색 중 오류: {e}")
            return None, None, "검색 중 오류가 발생했습니다."

        logger.info(f"부분 일치 결과: {len(possible_matches)}개 발견")

        if len(possible_matches) == 1:
            # 단일 일치 항목이 있으면 사용
            stock_name, stock_code = possible_matches[0]
            logger.info(f"단일 부분 일치 성공: '{stock_name}' ({stock_code})")
            return stock_code, stock_name, None
        elif len(possible_matches) > 1:
            # 여러 일치 항목이 있으면 오류 메시지 반환
            logger.info(f"다중 일치: {[f'{name}({code})' for name, code in possible_matches]}")
            match_info = "\n".join([f"{name} ({code})" for name, code in possible_matches[:5]])
            if len(possible_matches) > 5:
                match_info += f"\n... 외 {len(possible_matches)-5}개"

            return None, None, f"'{stock_input}'에 여러 일치하는 종목이 있습니다. 정확한 종목명이나 종목코드를 입력해주세요:\n{match_info}"
        else:
            # 일치하는 항목이 없으면 오류 메시지 반환
            logger.warning(f"일치하는 종목 없음: '{stock_input}'")
            return None, None, f"'{stock_input}'에 해당하는 종목을 찾을 수 없습니다. 정확한 종목명이나 종목코드를 입력해주세요."

    # US 티커 검증 캐시
    _us_ticker_cache: dict = {}

    async def validate_us_ticker(self, ticker_input: str) -> tuple:
        """
        US 주식 티커 심볼 검증

        Args:
            ticker_input (str): 티커 심볼 (예: AAPL, MSFT, GOOGL)

        Returns:
            tuple: (ticker, company_name, error_message)
        """
        if not ticker_input:
            return None, None, "티커 심볼을 입력해주세요. (예: AAPL, MSFT)"

        ticker = ticker_input.strip().upper()
        logger.info(f"US 티커 검증 시작: {ticker}")

        # 캐시 확인
        if ticker in self._us_ticker_cache:
            cached = self._us_ticker_cache[ticker]
            logger.info(f"캐시된 US 티커 정보 사용: {ticker} -> {cached['name']}")
            return ticker, cached['name'], None

        # 티커 형식 검증 (1-5자리 영문자)
        if not re.match(r'^[A-Z]{1,5}$', ticker):
            return None, None, (
                f"'{ticker_input}'은(는) 올바른 US 티커 형식이 아닙니다.\n"
                "US 티커는 1-5자리 영문자입니다. (예: AAPL, MSFT, GOOGL)"
            )

        # yfinance로 티커 검증
        try:
            import yfinance as yf

            stock = yf.Ticker(ticker)
            info = stock.info

            # 회사명 추출
            company_name = info.get('longName') or info.get('shortName')

            if not company_name:
                return None, None, (
                    f"'{ticker}' 티커에 대한 정보를 찾을 수 없습니다.\n"
                    "티커 심볼이 정확한지 확인해주세요."
                )

            # 캐시에 저장
            self._us_ticker_cache[ticker] = {'name': company_name}
            logger.info(f"US 티커 검증 성공: {ticker} -> {company_name}")

            return ticker, company_name, None

        except Exception as e:
            logger.error(f"US 티커 검증 중 오류: {e}")
            # yfinance가 없거나 오류 발생 시 기본 처리
            return ticker, f"{ticker} (미확인)", None

    # ==========================================================================
    # US 주식 평가 핸들러 (/us_evaluate)
    # ==========================================================================

    async def handle_us_evaluate_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 평가 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]

        logger.info(f"US 평가 명령 시작 - 사용자: {user_name}, 채팅타입: {'그룹' if is_group else '개인'}")

        # 그룹 채팅에서는 사용자 이름을 언급
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}🇺🇸 미국 주식 평가를 시작합니다.\n\n"
            "보유하신 종목의 티커 심볼을 입력해주세요.\n"
            "예: AAPL, MSFT, GOOGL, NVDA"
        )
        return US_CHOOSING_TICKER

    async def handle_us_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 티커 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        logger.info(f"US 티커 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 티커 검증
        ticker, company_name, error_message = await self.validate_us_ticker(user_input)

        if error_message:
            await update.message.reply_text(error_message)
            return US_CHOOSING_TICKER

        # 종목 정보 저장
        context.user_data['us_ticker'] = ticker
        context.user_data['us_ticker_name'] = company_name

        logger.info(f"US 종목 선택: {company_name} ({ticker})")

        await update.message.reply_text(
            f"🇺🇸 {company_name} ({ticker}) 종목을 선택하셨습니다.\n\n"
            f"평균 매수가를 USD로 입력해주세요. (숫자만 입력)\n"
            f"예: 150.50"
        )

        logger.info(f"상태 전환: US_ENTERING_AVGPRICE - 사용자: {user_id}")
        return US_ENTERING_AVGPRICE

    @staticmethod
    async def handle_us_avgprice_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 평균 매수가 입력 처리 (USD)"""
        try:
            avg_price = float(update.message.text.strip().replace(',', '').replace('$', ''))
            context.user_data['us_avg_price'] = avg_price

            await update.message.reply_text(
                f"보유 기간을 입력해주세요. (개월 수)\n"
                f"예: 6 (6개월)"
            )
            return US_ENTERING_PERIOD

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요. (예: 150.50)\n"
                "달러 기호($)와 콤마는 자동으로 제거됩니다."
            )
            return US_ENTERING_AVGPRICE

    @staticmethod
    async def handle_us_period_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 보유 기간 입력 처리"""
        try:
            period = int(update.message.text.strip())
            context.user_data['us_period'] = period

            await update.message.reply_text(
                "어떤 스타일이나 말투로 피드백을 받고 싶으신가요?\n"
                "예: 솔직하게, 전문적으로, 친구같이, 간결하게 등"
            )
            return US_ENTERING_TONE

        except ValueError:
            await update.message.reply_text(
                "숫자 형식으로 입력해주세요.\n"
                "예: 6"
            )
            return US_ENTERING_PERIOD

    @staticmethod
    async def handle_us_tone_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 피드백 스타일/톤 입력 처리"""
        tone = update.message.text.strip()
        context.user_data['us_tone'] = tone

        await update.message.reply_text(
            "종목을 매매하게 된 배경이나 주요 매매 히스토리가 있으시면 알려주세요.\n"
            "(선택사항이므로, 없으면 '없음'이라고 입력해주세요)"
        )
        return US_ENTERING_BACKGROUND

    async def handle_us_background_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 매매 배경 입력 처리 및 AI 응답 생성"""
        background = update.message.text.strip()
        context.user_data['us_background'] = background if background.lower() != '없음' else ""

        # 응답 대기 메시지
        waiting_message = await update.message.reply_text(
            "🇺🇸 미국 주식 분석 중입니다... 잠시만 기다려주세요."
        )

        # AI 에이전트로 분석 요청
        ticker = context.user_data['us_ticker']
        ticker_name = context.user_data.get('us_ticker_name', ticker)
        avg_price = context.user_data['us_avg_price']
        period = context.user_data['us_period']
        tone = context.user_data['us_tone']
        background = context.user_data['us_background']
        chat_id = update.effective_chat.id
        user_id = update.effective_user.id

        try:
            # 사용자 기억 컨텍스트 조회
            memory_context = ""
            if self.memory_manager:
                memory_context = self.memory_manager.build_llm_context(
                    user_id=user_id,
                    ticker=ticker
                )
                if memory_context:
                    logger.info(f"US 사용자 기억 컨텍스트 로드됨: {len(memory_context)} chars")

            # US AI 응답 생성 (memory_context 포함)
            response = await generate_us_evaluation_response(
                ticker, ticker_name, avg_price, period, tone, background,
                memory_context=memory_context
            )

            # 응답이 비어있는지 확인
            if not response or not response.strip():
                response = "죄송합니다. 응답 생성 중 오류가 발생했습니다. 다시 시도해주세요."
                logger.error(f"빈 응답이 생성되었습니다: {ticker_name}({ticker})")

            # 대기 메시지 삭제
            await waiting_message.delete()

            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 추가 질문이 있으시면 이 메시지에 답장(Reply)해주세요."
            )

            # 대화 컨텍스트 저장 (US 시장)
            conv_context = ConversationContext(market_type="us")
            conv_context.message_id = sent_message.message_id
            conv_context.chat_id = chat_id
            conv_context.user_id = update.effective_user.id
            conv_context.ticker = ticker
            conv_context.ticker_name = ticker_name
            conv_context.avg_price = avg_price
            conv_context.period = period
            conv_context.tone = tone
            conv_context.background = background
            conv_context.add_to_history("assistant", response)

            # 컨텍스트 저장
            self.conversation_contexts[sent_message.message_id] = conv_context
            logger.info(f"US 대화 컨텍스트 저장: 메시지 ID {sent_message.message_id}")

            # 평가 결과를 사용자 기억에 저장
            if self.memory_manager:
                self.memory_manager.save_memory(
                    user_id=user_id,
                    memory_type=self.memory_manager.MEMORY_EVALUATION,
                    content={
                        'ticker': ticker,
                        'ticker_name': ticker_name,
                        'avg_price': avg_price,
                        'period': period,
                        'tone': tone,
                        'background': background,
                        'response_summary': response[:500]  # 응답 요약 저장
                    },
                    ticker=ticker,
                    ticker_name=ticker_name,
                    market_type='us',
                    command_source='/us_evaluate',
                    message_id=sent_message.message_id
                )
                logger.info(f"US 평가 결과 기억에 저장: user={user_id}, ticker={ticker}")

        except Exception as e:
            logger.error(f"US 응답 생성 또는 전송 중 오류: {str(e)}, {traceback.format_exc()}")
            await waiting_message.delete()
            await update.message.reply_text("죄송합니다. 분석 중 오류가 발생했습니다. 다시 시도해주세요.")

        # 대화 종료
        return ConversationHandler.END

    # ==========================================================================
    # US 주식 보고서 핸들러 (/us_report)
    # ==========================================================================

    async def handle_us_report_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 보고서 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 일일 사용 제한 확인
        if not self.check_daily_limit(user_id, "us_report"):
            await update.message.reply_text(
                "⚠️ /us_report 명령어는 하루에 1회만 사용할 수 있습니다.\n\n"
                "내일 다시 이용해 주세요."
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}🇺🇸 미국 주식 보고서 요청입니다.\n\n"
            "분석할 종목의 티커 심볼을 입력해주세요.\n"
            "예: AAPL, MSFT, GOOGL, NVDA"
        )

        return US_REPORT_CHOOSING_TICKER

    async def handle_us_report_ticker_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """US 보고서 요청 티커 입력 처리"""
        user_id = update.effective_user.id
        user_input = update.message.text.strip()
        chat_id = update.effective_chat.id

        logger.info(f"US 보고서 티커 입력 받음 - 사용자: {user_id}, 입력: {user_input}")

        # 티커 검증
        ticker, company_name, error_message = await self.validate_us_ticker(user_input)

        if error_message:
            await update.message.reply_text(error_message)
            return US_REPORT_CHOOSING_TICKER

        # 대기 메시지 전송
        waiting_message = await update.message.reply_text(
            f"🇺🇸 {company_name} ({ticker}) 분석 보고서 생성 요청이 등록되었습니다.\n\n"
            f"요청은 도착 순서대로 처리되며, 한 건당 분석에 약 5-10분이 소요됩니다.\n\n"
            f"다른 사용자의 요청이 많을 경우 대기 시간이 길어질 수 있습니다.\n\n"
            f"완료되면 바로 알려드리겠습니다."
        )

        # US 분석 요청 생성 및 큐에 추가
        request = AnalysisRequest(
            stock_code=ticker,
            company_name=company_name,
            chat_id=chat_id,
            message_id=waiting_message.message_id,
            market_type="us"  # US 주식임을 명시
        )

        # 캐시된 US 보고서가 있는지 확인
        is_cached, cached_content, cached_file, cached_pdf = get_cached_us_report(ticker)

        if is_cached:
            logger.info(f"캐시된 US 보고서 발견: {cached_file}")
            # 캐시된 보고서가 있는 경우 바로 결과 전송
            request.result = cached_content
            request.status = "completed"
            request.report_path = cached_file
            request.pdf_path = cached_pdf

            await waiting_message.edit_text(
                f"✅ {company_name} ({ticker}) 분석 보고서가 준비되었습니다. 잠시 후 전송됩니다."
            )

            # 결과 전송
            await self.send_report_result(request)
        else:
            # 새로운 분석 필요 - 큐에 추가
            self.pending_requests[request.id] = request
            analysis_queue.put(request)

        return ConversationHandler.END

    # ==========================================================================
    # 저널(투자 일기) 핸들러 (/journal)
    # ==========================================================================

    async def handle_journal_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """저널 명령어 처리 - 첫 단계"""
        user_id = update.effective_user.id
        user_name = update.effective_user.first_name
        chat_id = update.effective_chat.id
        chat_type = update.effective_chat.type

        logger.info(f"[JOURNAL] handle_journal_start - user_id: {user_id}, chat_id: {chat_id}, chat_type: {chat_type}")

        # 채널 구독 여부 확인
        is_subscribed = await self.check_channel_subscription(user_id)

        if not is_subscribed:
            await update.message.reply_text(
                "이 봇은 채널 구독자만 사용할 수 있습니다.\n"
                "아래 링크를 통해 채널을 구독해주세요:\n\n"
                "https://t.me/stock_ai_agent"
            )
            return ConversationHandler.END

        # 그룹 채팅인지 개인 채팅인지 확인
        is_group = update.effective_chat.type in ["group", "supergroup"]
        greeting = f"{user_name}님, " if is_group else ""

        await update.message.reply_text(
            f"{greeting}📝 투자 일기를 작성해주세요.\n\n"
            "종목 코드/티커와 함께 입력하면 해당 종목에 연결됩니다:\n"
            "예: \"AAPL 170달러까지 홀딩 예정\"\n"
            "예: \"005930 반도체 바닥으로 판단\"\n\n"
            "또는 그냥 생각을 자유롭게 적어주세요."
        )

        logger.info(f"[JOURNAL] JOURNAL_ENTERING 상태로 전환 - user_id: {user_id}")
        return JOURNAL_ENTERING

    async def handle_journal_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """저널 입력 처리"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        text = update.message.text.strip()

        logger.info(f"[JOURNAL] handle_journal_input 호출됨 - user_id: {user_id}, chat_id: {chat_id}")
        logger.info(f"[JOURNAL] 저널 입력 받음 - 사용자: {user_id}, 입력: {text[:50]}...")

        # 티커 추출 (정규식)
        ticker, ticker_name, market_type = self._extract_ticker_from_text(text)

        # 기억 저장
        memory_id = self.memory_manager.save_journal(
            user_id=user_id,
            text=text,
            ticker=ticker,
            ticker_name=ticker_name,
            market_type=market_type,
            message_id=update.message.message_id
        )

        # 확인 메시지 구성
        # 500자 초과 시 안내 추가
        length_note = ""
        if len(text) > 500:
            length_note = f"\n⚠️ 참고: AI 대화 시 앞 500자만 참조됩니다. (현재 {len(text)}자)"

        if ticker:
            confirm_msg = (
                f"✅ 저널에 기록했습니다!\n\n"
                f"📝 종목: {ticker_name} ({ticker})\n"
                f"💭 \"{text[:100]}{'...' if len(text) > 100 else ''}\"\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f"{length_note}\n\n"
                f"💡 이 메시지에 답장하여 대화를 이어가세요!"
            )
        else:
            confirm_msg = (
                f"✅ 저널에 기록했습니다!\n\n"
                f"💭 \"{text[:100]}{'...' if len(text) > 100 else ''}\"\n"
                f"📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
                f"{length_note}\n\n"
                f"💡 이 메시지에 답장하여 대화를 이어가세요!"
            )

        sent_message = await update.message.reply_text(confirm_msg)

        # 저널 컨텍스트 저장 (답장용 - AI 대화 지원)
        self.journal_contexts[sent_message.message_id] = {
            'user_id': user_id,
            'ticker': ticker,
            'ticker_name': ticker_name,
            'market_type': market_type,
            'conversation_history': [],  # AI 대화 히스토리
            'created_at': datetime.now()
        }

        logger.info(f"저널 저장 완료: user={user_id}, ticker={ticker}, memory_id={memory_id}")

        return ConversationHandler.END

    async def _handle_journal_reply(self, update: Update, journal_ctx: Dict):
        """저널 메시지에 대한 답장 처리 - AI 대화 기능"""
        user_id = update.effective_user.id
        text = update.message.text.strip()

        logger.info(f"[JOURNAL_REPLY] 저널 대화 처리 - user_id: {user_id}, text: {text[:50]}...")

        # 컨텍스트 만료 확인 (30분으로 연장 - 대화 지속성)
        created_at = journal_ctx.get('created_at')
        if created_at and (datetime.now() - created_at).total_seconds() > 1800:
            await update.message.reply_text(
                "이전 대화 세션이 만료되었습니다.\n"
                "새 대화를 시작하려면 /journal 명령어를 사용해주세요. 💭"
            )
            return

        # 티커 정보 가져오기 (있으면)
        ticker = journal_ctx.get('ticker')
        ticker_name = journal_ctx.get('ticker_name')
        market_type = journal_ctx.get('market_type', 'kr')
        conversation_history = journal_ctx.get('conversation_history', [])

        # 대기 메시지
        waiting_message = await update.message.reply_text(
            "💭 생각 중입니다..."
        )

        try:
            # 사용자 기억 컨텍스트 빌드
            memory_context = self.memory_manager.build_llm_context(
                user_id=user_id,
                ticker=ticker,
                max_tokens=2000
            )

            # 대화 히스토리에 사용자 메시지 추가
            conversation_history.append({'role': 'user', 'content': text})

            # AI 응답 생성
            response = await generate_journal_conversation_response(
                user_id=user_id,
                user_message=text,
                memory_context=memory_context,
                ticker=ticker,
                ticker_name=ticker_name,
                conversation_history=conversation_history
            )

            # 대기 메시지 삭제
            await waiting_message.delete()

            # 응답 전송
            sent_message = await update.message.reply_text(
                response + "\n\n💡 답장으로 대화를 이어가세요!"
            )

            # 대화 히스토리에 AI 응답 추가
            conversation_history.append({'role': 'assistant', 'content': response})

            # 새 메시지 ID로 컨텍스트 업데이트
            self.journal_contexts[sent_message.message_id] = {
                'user_id': user_id,
                'ticker': ticker,
                'ticker_name': ticker_name,
                'market_type': market_type,
                'conversation_history': conversation_history,
                'created_at': datetime.now()
            }

            # 사용자 메시지를 저널로 저장 (선택적)
            self.memory_manager.save_journal(
                user_id=user_id,
                text=text,
                ticker=ticker,
                ticker_name=ticker_name,
                market_type=market_type,
                message_id=update.message.message_id
            )

            logger.info(f"[JOURNAL_REPLY] AI 대화 응답 완료: user={user_id}, response_len={len(response)}")

        except Exception as e:
            logger.error(f"[JOURNAL_REPLY] 오류: {e}")
            await waiting_message.delete()
            await update.message.reply_text(
                "죄송해요, 응답 생성 중 문제가 생겼어요. 다시 말씀해주시겠어요? 💭"
            )

    def _extract_ticker_from_text(self, text: str) -> tuple:
        """
        텍스트에서 티커/종목코드 추출

        Args:
            text: 입력 텍스트

        Returns:
            tuple: (ticker, ticker_name, market_type)

        Note:
            한국 종목을 우선 확인 (한글 텍스트에서 한국 주식이 더 일반적)
        """
        # 한국 종목 코드 패턴 (6자리 숫자)
        kr_pattern = r'\b(\d{6})\b'
        # US 티커 패턴 (1-5자리 대문자, 단어 경계)
        us_pattern = r'\b([A-Z]{1,5})\b'

        # 1. 한국 종목 코드 먼저 확인 (우선순위)
        kr_matches = re.findall(kr_pattern, text)
        for code in kr_matches:
            if code in self.stock_map:
                return code, self.stock_map[code], 'kr'

        # 2. 한국 종목명 찾기 (stock_name_map에서 검색)
        for name, code in self.stock_name_map.items():
            if name in text:
                return code, name, 'kr'

        # 3. US 티커 찾기 (한국 종목이 없을 때만)
        # 제외할 단어들: 일반 영단어 + 금융 용어
        excluded_words = {
            # 일반 영단어
            'I', 'A', 'AN', 'THE', 'IN', 'ON', 'AT', 'TO', 'FOR', 'OF',
            'AND', 'OR', 'IS', 'IT', 'AI', 'AM', 'PM', 'VS', 'OK', 'NO',
            'IF', 'AS', 'BY', 'SO', 'UP', 'BE', 'WE', 'HE', 'ME', 'MY',
            # 금융 지표/용어
            'PER', 'PBR', 'ROE', 'ROA', 'EPS', 'BPS', 'PSR', 'PCR',
            'EBITDA', 'EBIT', 'YOY', 'QOQ', 'MOM', 'YTD', 'TTM',
            'PE', 'PS', 'PB', 'EV', 'FCF', 'DCF', 'WACC', 'CAGR',
            'IPO', 'M', 'B', 'K', 'KRW', 'USD', 'EUR', 'JPY', 'CNY',
            # 기타 약어
            'CEO', 'CFO', 'CTO', 'COO', 'IR', 'PR', 'HR', 'IT', 'AI',
            'HBM', 'DRAM', 'NAND', 'SSD', 'GPU', 'CPU', 'AP', 'PC',
        }

        us_matches = re.findall(us_pattern, text)
        for ticker in us_matches:
            if ticker in excluded_words:
                continue
            # 캐시 확인
            if ticker in self._us_ticker_cache:
                return ticker, self._us_ticker_cache[ticker]['name'], 'us'
            # yfinance로 검증
            try:
                import yfinance as yf
                stock = yf.Ticker(ticker)
                info = stock.info
                company_name = info.get('longName') or info.get('shortName')
                if company_name:
                    self._us_ticker_cache[ticker] = {'name': company_name}
                    return ticker, company_name, 'us'
            except Exception:
                pass

        return None, None, 'kr'

    async def process_results(self):
        """결과 큐에서 처리할 항목 확인"""
        logger.info("결과 처리 태스크 시작")
        while not self.stop_event.is_set():
            try:
                # 큐가 비어있지 않으면 처리
                if not self.result_queue.empty():
                    # 내부 반복 없이 한 번에 하나의 요청만 처리
                    request_id = self.result_queue.get()
                    logger.info(f"결과 큐에서 항목 가져옴: {request_id}")

                    if request_id in self.pending_requests:
                        request = self.pending_requests[request_id]
                        # 결과 전송 (메인 이벤트 루프에서 실행되므로 안전)
                        await self.send_report_result(request)
                        logger.info(f"결과 전송 완료: {request.id} ({request.company_name})")
                    else:
                        logger.warning(f"요청 ID가 pending_requests에 없음: {request_id}")

                    # 큐 작업 완료 표시
                    self.result_queue.task_done()
                
                # 잠시 대기 (CPU 사용률 감소)
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"결과 처리 중 오류: {str(e)}")
                logger.error(traceback.format_exc())

            # 잠시 대기
            await asyncio.sleep(1)

    async def run(self):
        """봇 실행"""
        # 전역 MCP App 초기화
        try:
            logger.info("전역 MCPApp 초기화 중...")
            await get_or_create_global_mcp_app()
            logger.info("전역 MCPApp 초기화 완료")
        except Exception as e:
            logger.error(f"전역 MCPApp 초기화 실패: {e}")
            # 초기화 실패해도 봇은 시작 (나중에 재시도 가능)
        
        # 봇 실행
        await self.application.initialize()
        await self.application.start()
        await self.application.updater.start_polling()

        # 결과 처리를 위한 작업 추가
        asyncio.create_task(self.process_results())

        logger.info("텔레그램 AI 대화형 봇이 시작되었습니다.")

        try:
            # 봇이 중단될 때까지 실행 유지
            # 무한 대기하기 위한 간단한 방법
            await self.stop_event.wait()
        except asyncio.CancelledError:
            pass
        finally:
            # 종료 시 리소스 정리
            logger.info("봇 종료 시작 - 리소스 정리 중...")
            
            # 전역 MCP App 정리
            try:
                logger.info("전역 MCPApp 정리 중...")
                await cleanup_global_mcp_app()
                logger.info("전역 MCPApp 정리 완료")
            except Exception as e:
                logger.error(f"전역 MCPApp 정리 실패: {e}")
            
            # 봇 종료
            await self.application.stop()
            await self.application.shutdown()

            logger.info("텔레그램 AI 대화형 봇이 종료되었습니다.")

async def shutdown(sig, loop):
    """Cleanup tasks tied to the service's shutdown."""
    logger.info(f"Received signal {sig.name}, shutting down...")
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        task.cancel()

    logger.info(f"Cancelling {len(tasks)} outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()

# 메인 실행 부분
async def main():
    """
    메인 함수
    """
    # 시그널 핸들러 설정
    loop = asyncio.get_event_loop()
    signals = (signal.SIGINT, signal.SIGTERM)

    def create_signal_handler(sig):
        return lambda: asyncio.create_task(shutdown(sig, loop))

    for s in signals:
        loop.add_signal_handler(s, create_signal_handler(s))

    bot = TelegramAIBot()
    await bot.run()

if __name__ == "__main__":
    asyncio.run(main())