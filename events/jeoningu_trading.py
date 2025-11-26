#!/usr/bin/env python3
"""
Jeon Ingu Contrarian Trading System - '전인구경제연구소' Analysis & Trading Simulator

Simplified strategy:
- Jeon says UP → Buy KODEX Inverse 2X (252670)
- Jeon says NEUTRAL → Sell all positions
- Jeon says DOWN → Buy KODEX Leverage (122630)

Always hold max 1 position at a time. Switch positions when sentiment changes.
Use full balance for each trade (all-in strategy).
"""

import os
import sys
import json
import logging
import asyncio
import yaml
import argparse
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

# Third-party imports
import feedparser
import yt_dlp
from openai import OpenAI
from mcp_agent.agents.agent import Agent
from mcp_agent.app import MCPApp
from mcp_agent.workflows.llm.augmented_llm import RequestParams
from mcp_agent.workflows.llm.augmented_llm_openai import OpenAIAugmentedLLM

# Add parent directory to path for imports
sys.path.append(str(Path(__file__).parent.parent))

from events.jeoningu_trading_db import JeoninguTradingDB
from events.jeoningu_price_fetcher import get_current_price

# Setup directories
DATA_DIR = Path(__file__).parent
SECRETS_DIR = Path(__file__).parent.parent

# Output directories - 산출물을 하위 디렉토리에 정리
LOGS_DIR = DATA_DIR / "logs"
TRANSCRIPTS_DIR = DATA_DIR / "transcripts"
AUDIO_TEMP_DIR = DATA_DIR / "audio_temp"

# Create directories if not exist
LOGS_DIR.mkdir(exist_ok=True)
TRANSCRIPTS_DIR.mkdir(exist_ok=True)
AUDIO_TEMP_DIR.mkdir(exist_ok=True)

# Configure logging
log_file = LOGS_DIR / f"jeoningu_{datetime.now().strftime('%Y%m%d')}.log"
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(log_file)
    ]
)
logger = logging.getLogger(__name__)

# Constants
CHANNEL_ID = "UCznImSIaxZR7fdLCICLdgaQ"  # 전인구경제연구소
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
VIDEO_HISTORY_FILE = DATA_DIR / "jeoningu_video_history.json"
AUDIO_FILE = AUDIO_TEMP_DIR / "temp_audio.mp3"

# Trading configuration
INITIAL_CAPITAL = 10000000  # 1천만원 초기 자본

# Stock codes
KODEX_LEVERAGE = "122630"  # KODEX 레버리지
KODEX_INVERSE_2X = "252670"  # KODEX 200선물인버스2X


class JeoninguTrading:
    """Main trading bot for contrarian strategy"""

    def __init__(self, use_telegram: bool = True):
        """Initialize bot"""
        # Load OpenAI API key
        secrets_file = SECRETS_DIR / "mcp_agent.secrets.yaml"
        if not secrets_file.exists():
            raise FileNotFoundError("mcp_agent.secrets.yaml not found")

        with open(secrets_file, 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)

        openai_api_key = secrets.get('openai', {}).get('api_key')
        if not openai_api_key or openai_api_key == "example key":
            raise ValueError("OPENAI_API_KEY not configured in mcp_agent.secrets.yaml")

        self.openai_client = OpenAI(api_key=openai_api_key)
        self.db = JeoninguTradingDB()
        self.use_telegram = use_telegram

        # Load Telegram config if enabled
        if self.use_telegram:
            self._load_telegram_config()

        logger.info("JeoninguTrading initialized")

    def _load_telegram_config(self):
        """Load Telegram credentials"""
        from dotenv import load_dotenv
        load_dotenv(SECRETS_DIR / ".env")

        self.telegram_bot_token = os.getenv("TELEGRAM_BOT_TOKEN")
        self.telegram_channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

        if not self.telegram_bot_token or not self.telegram_channel_id:
            logger.warning("Telegram not configured - disabling")
            self.use_telegram = False

    def fetch_latest_videos(self) -> List[Dict[str, str]]:
        """Fetch videos from RSS feed"""
        logger.info(f"Fetching RSS: {RSS_URL}")
        try:
            feed = feedparser.parse(RSS_URL)
            videos = []
            for entry in feed.entries:
                videos.append({
                    'id': entry.yt_videoid,
                    'title': entry.title,
                    'published': entry.published,
                    'link': entry.link,
                    'author': entry.author if hasattr(entry, 'author') else 'Unknown'
                })
            logger.info(f"Found {len(videos)} videos")
            return videos
        except Exception as e:
            logger.error(f"RSS fetch error: {e}", exc_info=True)
            return []

    def load_previous_videos(self) -> List[Dict[str, str]]:
        """Load video history"""
        if not VIDEO_HISTORY_FILE.exists():
            return []
        try:
            with open(VIDEO_HISTORY_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading history: {e}")
            return []

    def save_video_history(self, videos: List[Dict[str, str]]):
        """Save video history"""
        try:
            with open(VIDEO_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(videos)} videos")
        except Exception as e:
            logger.error(f"Error saving history: {e}")

    def find_new_videos(self, current: List[Dict], previous: List[Dict]) -> List[Dict]:
        """Find new videos"""
        previous_ids = {v['id'] for v in previous}
        new_videos = [v for v in current if v['id'] not in previous_ids]
        logger.info(f"Found {len(new_videos)} new videos")
        return new_videos

    async def ensure_youtube_cookies(self) -> Optional[Path]:
        """YouTube 쿠키 파일이 있는지 확인하고, 없거나 오래되면 Playwright로 생성"""
        cookies_file = SECRETS_DIR / "youtube_cookies.txt"
        
        try:
            from events.youtube_cookie_generator import refresh_cookies_if_needed
            return await refresh_cookies_if_needed(cookies_file, max_age_hours=12)
        except ImportError:
            logger.warning("youtube_cookie_generator not available")
            return cookies_file if cookies_file.exists() else None
        except Exception as e:
            logger.error(f"Failed to generate cookies: {e}")
            return cookies_file if cookies_file.exists() else None

    def extract_audio(self, video_url: str, cookies_file: Optional[Path] = None) -> Optional[str]:
        """Extract audio from YouTube"""
        logger.info(f"Extracting audio: {video_url}")

        # Clean up old files in audio_temp directory
        for temp_file in AUDIO_TEMP_DIR.glob('temp_audio.*'):
            try:
                temp_file.unlink()
            except Exception:
                pass

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(AUDIO_TEMP_DIR / 'temp_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
            'keepvideo': False,
            'quiet': False,  # 디버깅을 위해 출력 활성화
            'no_warnings': False,
            # 봇 탐지 우회 옵션들
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],  # Android 클라이언트 우선 시도
                    'player_skip': ['webpage', 'configs'],  # 불필요한 요청 스킵
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 12; SM-G991B) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7',
            },
            'socket_timeout': 30,
            'retries': 3,
        }
        
        # 쿠키 파일이 있으면 사용
        if cookies_file and cookies_file.exists():
            ydl_opts['cookiefile'] = str(cookies_file)
            logger.info(f"Using cookies file: {cookies_file}")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if AUDIO_FILE.exists():
                logger.info("Audio extraction successful")
                return str(AUDIO_FILE)
            return None
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Audio extraction error: {error_msg}")
            
            # Android 클라이언트도 실패하면 iOS 클라이언트로 재시도
            if "Sign in to confirm" in error_msg or "bot" in error_msg.lower():
                logger.info("Retrying with iOS client...")
                return self._extract_audio_fallback(video_url, cookies_file)
            
            return None
    
    def _extract_audio_fallback(self, video_url: str, cookies_file: Optional[Path] = None) -> Optional[str]:
        """Fallback: iOS 클라이언트로 재시도"""
        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(AUDIO_TEMP_DIR / 'temp_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
            'keepvideo': False,
            'quiet': False,
            'extractor_args': {
                'youtube': {
                    'player_client': ['ios', 'mweb'],  # iOS와 모바일 웹 클라이언트
                }
            },
            'http_headers': {
                'User-Agent': 'com.google.ios.youtube/19.29.1 (iPhone16,2; U; CPU iOS 17_5_1 like Mac OS X;)',
                'Accept-Language': 'ko-KR,ko;q=0.9',
            },
        }
        
        if cookies_file and cookies_file.exists():
            ydl_opts['cookiefile'] = str(cookies_file)
        
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            
            if AUDIO_FILE.exists():
                logger.info("Audio extraction successful (iOS fallback)")
                return str(AUDIO_FILE)
            return None
        except Exception as e:
            logger.error(f"iOS fallback also failed: {e}")
            return None

    def transcribe_audio(self, audio_file: str) -> Optional[str]:
        """Transcribe audio with Whisper"""
        logger.info(f"Transcribing: {audio_file}")

        try:
            file_size = Path(audio_file).stat().st_size
            file_size_mb = file_size / 1024 / 1024
            max_size = 20 * 1024 * 1024  # 20MB (보수적으로 설정)

            logger.info(f"File size: {file_size_mb:.2f}MB")
            
            # Try to get audio duration
            try:
                from pydub import AudioSegment
                audio = AudioSegment.from_mp3(audio_file)
                duration_sec = len(audio) / 1000
                logger.info(f"Audio duration: {duration_sec / 60:.1f} minutes ({duration_sec:.0f}s)")
            except Exception:
                logger.debug("Could not determine audio duration")

            if file_size <= max_size:
                logger.info("Sending file to OpenAI Whisper API... (this may take several minutes for long audio)")
                import time
                start_time = time.time()
                
                with open(audio_file, "rb") as f:
                    result = self.openai_client.audio.transcriptions.create(
                        model="whisper-1",
                        file=f,
                        language="ko",
                        timeout=600.0  # 10분 타임아웃 (긴 오디오 대비)
                    )
                
                elapsed = time.time() - start_time
                logger.info(f"Transcription completed in {elapsed:.1f}s ({len(result.text)} chars)")
                return result.text
            else:
                # Split large files
                logger.info(f"File size {file_size_mb:.2f}MB exceeds 20MB limit, splitting...")
                return self._transcribe_large_file(audio_file)

        except Exception as e:
            logger.error(f"Transcription error: {e}", exc_info=True)
            return None

    def _transcribe_large_file(self, audio_file: str) -> Optional[str]:
        """Split and transcribe large audio files"""
        try:
            from pydub import AudioSegment

            audio = AudioSegment.from_mp3(audio_file)
            chunk_length_ms = 5 * 60 * 1000  # 5분 (20MB 제한을 고려한 안전한 크기)
            chunks = []
            transcripts = []

            total_duration_sec = len(audio) / 1000
            num_chunks = (len(audio) + chunk_length_ms - 1) // chunk_length_ms
            logger.info(f"Audio duration: {total_duration_sec:.1f}s, splitting into {num_chunks} chunks")

            for i in range(0, len(audio), chunk_length_ms):
                chunk = audio[i:i + chunk_length_ms]
                chunk_file = AUDIO_TEMP_DIR / f"temp_audio_chunk_{i//chunk_length_ms}.mp3"
                chunk.export(chunk_file, format="mp3")
                
                # Verify chunk size doesn't exceed 20MB
                chunk_size = chunk_file.stat().st_size
                if chunk_size > 20 * 1024 * 1024:
                    logger.warning(f"Chunk {i//chunk_length_ms} size {chunk_size / 1024 / 1024:.2f}MB exceeds 20MB!")
                    # Continue anyway, but log the warning
                
                chunks.append(chunk_file)

            for idx, chunk_file in enumerate(chunks, 1):
                logger.info(f"Transcribing chunk {idx}/{len(chunks)}")
                try:
                    with open(chunk_file, "rb") as f:
                        result = self.openai_client.audio.transcriptions.create(
                            model="whisper-1",
                            file=f,
                            language="ko"
                        )
                    transcripts.append(result.text)
                except Exception as e:
                    logger.error(f"Chunk {idx} error: {e}")
                    transcripts.append(f"[Chunk {idx} failed]")

            # Cleanup
            for chunk_file in chunks:
                try:
                    chunk_file.unlink()
                except Exception:
                    pass

            logger.info(f"Large file transcription completed: {len(transcripts)} chunks processed")
            return " ".join(transcripts)

        except ImportError:
            logger.error("pydub not installed. Install: pip install pydub")
            return None
        except Exception as e:
            logger.error(f"Large file transcription error: {e}")
            return None

    def create_analysis_agent(self, video_info: Dict, transcript: str) -> Agent:
        """
        Create AI agent for analysis

        Simplified strategy:
        - Jeon UP → Inverse 2X (252670)
        - Jeon NEUTRAL → Sell all
        - Jeon DOWN → Leverage (122630)
        """
        instruction = f"""당신은 전인구경제연구소 콘텐츠를 분석하는 역발상 투자 전문가입니다.

## 영상 정보
- 제목: {video_info['title']}
- 게시일: {video_info['published']}
- URL: {video_info['link']}

## 영상 자막
{transcript}

## 분석 과제

### 1단계: 콘텐츠 유형 판별
전인구 본인이 직접 출연하여 시장 의견을 제시하는 영상인가?
- "본인의견": 전인구가 직접 시장 전망 언급
- "스킵": 단순 뉴스 요약, 게스트 인터뷰만 있는 경우

### 2단계: 시장 기조 분석
전인구가 시장에 대해 어떤 기조로 말하는지 판단:
- "상승": 낙관적 전망, 매수 추천, 긍정적 시그널 강조
- "하락": 비관적 전망, 매도/관망 추천, 부정적 시그널 강조
- "중립": 명확한 방향성 없음, 애매한 의견

### 3단계: 역발상 전략 결정

**투자 종목 (2개만 사용)**:
- KODEX 레버리지 (122630): 코스피 200 지수 2배 추종
- KODEX 200선물인버스2X (252670): 코스피 200 반대 방향 2배

**전략 규칙**:
1. 전인구 **상승** 기조 → 반대로 **하락**에 베팅 → **KODEX 200선물인버스2X(252670) 매수**
2. 전인구 **중립** 기조 → 관망 → **보유 종목 전량 매도 (현금화)**
3. 전인구 **하락** 기조 → 반대로 **상승**에 베팅 → **KODEX 레버리지(122630) 매수**

**포지션 관리**:
- 항상 1개 종목만 보유 (122630 또는 252670)
- 다른 종목으로 전환 시: 기존 보유 종목 매도 → 새 종목 매수
- 중립일 때: 보유 종목 있으면 무조건 매도
- 매수 시: **가용 잔액 전액 투자** (올인 전략)

## 출력 형식 (JSON)

반드시 아래 JSON 스키마를 따라 출력하세요 (마크다운 코드블록 없이 순수 JSON만):

```json
{{
  "video_info": {{
    "video_id": "{video_info['id']}",
    "title": "{video_info['title']}",
    "video_date": "{video_info['published']}",
    "video_url": "{video_info['link']}"
  }},
  "content_type": "본인의견" | "스킵",
  "jeon_sentiment": "상승" | "하락" | "중립",
  "jeon_reasoning": "전인구의 핵심 발언을 2-3개 문장으로 요약",
  "contrarian_action": "인버스2X매수" | "레버리지매수" | "전량매도",
  "target_stock": {{
    "code": "252670" | "122630" | null,
    "name": "KODEX 200선물인버스2X" | "KODEX 레버리지" | null
  }},
  "telegram_summary": "텔레그램 메시지 내용 (5줄 이내, 이모지 포함)"
}}
```

## 중요 사항
- **반드시 valid JSON만 출력** (마크다운 코드블록 제거)
- 자막 내용만 근거로 분석 (추측 금지)
- 종목은 122630, 252670 중 하나만 선택
- 중립일 때는 target_stock을 null로 설정
"""

        return Agent(
            name="jeoningu_analyst",
            instruction=instruction,
            server_names=[]
        )

    async def analyze_video(self, video_info: Dict, transcript: str) -> Optional[Dict]:
        """Analyze video and return structured JSON"""
        logger.info(f"Analyzing: {video_info['title']}")

        try:
            agent = self.create_analysis_agent(video_info, transcript)
            app = MCPApp(name="jeoningu_analysis")

            async with app.run() as _:
                llm = await agent.attach_llm(OpenAIAugmentedLLM)
                result = await llm.generate_str(
                    message="위 지시사항에 따라 영상을 분석하고 역발상 투자 전략을 JSON 형식으로 출력해주세요.",
                    request_params=RequestParams(
                        model="gpt-5",
                        maxTokens=8000,
                        max_iterations=3,
                        parallel_tool_calls=False,
                        use_history=True
                    )
                )

            # Clean JSON response
            result_clean = result.strip()
            if result_clean.startswith("```json"):
                result_clean = result_clean[7:]
            if result_clean.startswith("```"):
                result_clean = result_clean[3:]
            if result_clean.endswith("```"):
                result_clean = result_clean[:-3]
            result_clean = result_clean.strip()

            analysis = json.loads(result_clean)
            logger.info("Analysis completed")
            return analysis

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            logger.error(f"Response: {result[:500]}")
            return None
        except Exception as e:
            logger.error(f"Analysis error: {e}", exc_info=True)
            return None

    async def send_telegram_message(self, analysis: Dict) -> Optional[int]:
        """Send message to Telegram"""
        if not self.use_telegram:
            return None

        try:
            from telegram import Bot

            summary = analysis.get('telegram_summary', '')
            video_url = analysis['video_info']['video_url']
            video_title = analysis['video_info']['title']
            sentiment = analysis.get('jeon_sentiment', '알 수 없음')
            action = analysis.get('contrarian_action', '관망')

            message_text = f"""
🧪 <b>전인구 역발상 투자 실험</b>

<i>전인구경제연구소의 예측과 정반대로 베팅하는 시뮬레이션입니다.
커뮤니티에서 유명한 '전반꿀' 전략의 실제 효과를 검증하는 실험입니다.</i>

━━━━━━━━━━━━━━━━━━━━

📺 <b>최신 영상 분석</b>
<b>{video_title}</b>

{summary}

📊 전인구 기조: <b>{sentiment}</b>
💡 역발상 액션: <b>{action}</b>

🔗 <a href="{video_url}">영상 보기</a>

━━━━━━━━━━━━━━━━━━━━

📈 <b>실시간 실적 확인</b>
https://stocksimulation.kr/ 접속 후
<b>'실험실'</b> 탭을 클릭하세요!

⚠️ 본 정보는 투자 권유가 아닌 참고용 정보입니다.
💼 모든 투자 결정과 그 결과에 대한 책임은 투자자 본인에게 있습니다.
""".strip()

            bot = Bot(token=self.telegram_bot_token)
            message = await bot.send_message(
                chat_id=self.telegram_channel_id,
                text=message_text,
                parse_mode='HTML',
                disable_web_page_preview=True
            )

            logger.info(f"Telegram sent (message_id: {message.message_id})")
            return message.message_id

        except Exception as e:
            logger.error(f"Telegram send error: {e}")
            return None

    async def send_portfolio_status_message(self) -> Optional[int]:
        """Send portfolio status summary to Telegram"""
        if not self.use_telegram:
            return None

        try:
            from telegram import Bot

            # Get current data
            position = await self.db.get_current_position()
            balance = await self.db.get_latest_balance()
            metrics = await self.db.calculate_performance_metrics()

            # Build message
            message_parts = ["📊 **포트폴리오 현황**\n"]

            # Current position
            if position:
                current_price = get_current_price(position['stock_code'])
                current_value = position['quantity'] * current_price
                unrealized_pl = current_value - position['buy_amount']
                unrealized_pl_pct = (unrealized_pl / position['buy_amount']) * 100

                message_parts.append(f"🔹 보유 종목: {position['stock_name']}")
                message_parts.append(f"  - 수량: {position['quantity']:,}주")
                message_parts.append(f"  - 매수가: {position['buy_price']:,.0f}원")
                message_parts.append(f"  - 현재가: {current_price:,.0f}원")
                message_parts.append(f"  - 평가액: {current_value:,.0f}원")
                message_parts.append(f"  - 평가손익: {unrealized_pl:+,.0f}원 ({unrealized_pl_pct:+.2f}%)\n")
            else:
                message_parts.append(f"🔹 보유 종목: 없음 (현금 보유)\n")

            # Balance
            message_parts.append(f"💰 현재 잔액: {balance:,.0f}원")
            message_parts.append(f"💵 초기 자본: {INITIAL_CAPITAL:,.0f}원\n")

            # Performance metrics
            message_parts.append(f"📈 **누적 성과**")
            message_parts.append(f"  - 총 거래 횟수: {metrics['total_trades']}회")
            message_parts.append(f"  - 승리: {metrics['winning_trades']}회 / 패배: {metrics['losing_trades']}회")
            message_parts.append(f"  - 승률: {metrics['win_rate']:.1f}%")
            message_parts.append(f"  - 누적 수익률: {metrics['cumulative_return']:+.2f}%")
            
            if metrics['total_trades'] > 0:
                message_parts.append(f"  - 평균 거래당 수익률: {metrics['avg_return_per_trade']:+.2f}%")

            message_text = "\n".join(message_parts)

            bot = Bot(token=self.telegram_bot_token)
            message = await bot.send_message(
                chat_id=self.telegram_channel_id,
                text=message_text,
                parse_mode='Markdown',
                disable_web_page_preview=True
            )

            logger.info(f"Portfolio status sent (message_id: {message.message_id})")
            return message.message_id

        except Exception as e:
            logger.error(f"Portfolio status send error: {e}")
            return None

    async def execute_trading_strategy(self, analysis: Dict):
        """
        Execute trading strategy based on analysis

        Strategy:
        - UP → Buy Inverse 2X (252670) with full balance
        - NEUTRAL → Sell all
        - DOWN → Buy Leverage (122630) with full balance
        """
        try:
            video_info = analysis['video_info']
            
            # Check if this video was already processed
            if await self.db.video_id_exists(video_info['video_id']):
                logger.warning(f"Video {video_info['video_id']} already processed, skipping trade execution")
                return

            sentiment = analysis.get('jeon_sentiment')
            action = analysis.get('contrarian_action')
            target_stock = analysis.get('target_stock', {})

            # Get current position
            current_position = await self.db.get_current_position()
            current_balance = await self.db.get_latest_balance()

            # Initialize balance if first trade
            if current_balance == 0:
                current_balance = INITIAL_CAPITAL

            analyzed_date = datetime.now().isoformat()

            # Determine what to do
            trades_executed = []

            # Case 1: NEUTRAL → Sell all positions
            if sentiment == '중립':
                if current_position:
                    # Sell current position - get real price
                    sell_price = get_current_price(current_position['stock_code'])
                    sell_amount = current_position['quantity'] * sell_price
                    profit_loss = sell_amount - current_position['buy_amount']
                    profit_loss_pct = (profit_loss / current_position['buy_amount']) * 100

                    new_balance = current_balance + profit_loss
                    cumulative_return_pct = ((new_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

                    sell_trade = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'SELL',
                        'stock_code': current_position['stock_code'],
                        'stock_name': current_position['stock_name'],
                        'quantity': current_position['quantity'],
                        'price': sell_price,
                        'amount': sell_amount,
                        'related_buy_id': current_position['buy_id'],
                        'profit_loss': profit_loss,
                        'profit_loss_pct': profit_loss_pct,
                        'balance_before': current_balance,
                        'balance_after': new_balance,
                        'cumulative_return_pct': cumulative_return_pct,
                        'notes': f"중립 기조로 전량 매도 (손익: {profit_loss:,.0f}원, {profit_loss_pct:+.2f}%)"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    logger.info(f"✅ SELL: {current_position['stock_name']} (중립 기조)")
                else:
                    # No position to sell, just record analysis
                    record = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'HOLD',
                        'balance_before': current_balance,
                        'balance_after': current_balance,
                        'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                        'notes': '중립 기조, 보유 종목 없음'
                    }
                    await self.db.insert_trade(record)
                    logger.info("중립 기조, 보유 종목 없음")

            # Case 2: UP or DOWN → Buy target stock
            elif sentiment in ['상승', '하락']:
                target_code = target_stock.get('code')
                target_name = target_stock.get('name')

                if not target_code:
                    logger.warning(f"No target stock for sentiment: {sentiment}")
                    return

                # Step 1: Sell current position if different stock
                if current_position and current_position['stock_code'] != target_code:
                    # Sell different stock - get real price
                    sell_price = get_current_price(current_position['stock_code'])
                    sell_amount = current_position['quantity'] * sell_price
                    profit_loss = sell_amount - current_position['buy_amount']
                    profit_loss_pct = (profit_loss / current_position['buy_amount']) * 100

                    new_balance = current_balance + profit_loss
                    cumulative_return_pct = ((new_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

                    sell_trade = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'SELL',
                        'stock_code': current_position['stock_code'],
                        'stock_name': current_position['stock_name'],
                        'quantity': current_position['quantity'],
                        'price': sell_price,
                        'amount': sell_amount,
                        'related_buy_id': current_position['buy_id'],
                        'profit_loss': profit_loss,
                        'profit_loss_pct': profit_loss_pct,
                        'balance_before': current_balance,
                        'balance_after': new_balance,
                        'cumulative_return_pct': cumulative_return_pct,
                        'notes': f"종목 전환을 위한 매도 → {target_name} 매수 예정"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    current_balance = new_balance
                    logger.info(f"✅ SELL: {current_position['stock_name']} (종목 전환)")

                elif current_position and current_position['stock_code'] == target_code:
                    # Already holding target stock, no action needed
                    record = {
                        'video_id': video_info['video_id'],
                        'video_title': video_info['title'],
                        'video_date': video_info['video_date'],
                        'video_url': video_info['video_url'],
                        'analyzed_date': analyzed_date,
                        'jeon_sentiment': sentiment,
                        'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                        'contrarian_action': action,
                        'trade_type': 'HOLD',
                        'balance_before': current_balance,
                        'balance_after': current_balance,
                        'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                        'notes': f'이미 {target_name} 보유 중, 액션 없음'
                    }
                    await self.db.insert_trade(record)
                    logger.info(f"이미 {target_name} 보유 중")
                    return

                # Step 2: Buy target stock with FULL BALANCE - get real price
                buy_price = get_current_price(target_code)
                quantity = int(current_balance / buy_price)  # 전액 투자
                buy_amount = quantity * buy_price

                buy_trade = {
                    'video_id': video_info['video_id'],
                    'video_title': video_info['title'],
                    'video_date': video_info['video_date'],
                    'video_url': video_info['video_url'],
                    'analyzed_date': analyzed_date,
                    'jeon_sentiment': sentiment,
                    'jeon_reasoning': analysis.get('jeon_reasoning', ''),
                    'contrarian_action': action,
                    'trade_type': 'BUY',
                    'stock_code': target_code,
                    'stock_name': target_name,
                    'quantity': quantity,
                    'price': buy_price,
                    'amount': buy_amount,
                    'balance_before': current_balance,
                    'balance_after': current_balance,  # Balance unchanged (cash→stock)
                    'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                    'notes': f"{sentiment} 기조 → 역발상 {target_name} 전액 매수 ({buy_amount:,.0f}원)"
                }
                await self.db.insert_trade(buy_trade)
                trades_executed.append(buy_trade)
                logger.info(f"✅ BUY: {target_name} x {quantity} @ {buy_price:,} (전액 투자: {buy_amount:,.0f}원)")

            # Log performance metrics
            metrics = await self.db.calculate_performance_metrics()
            logger.info(f"📊 Performance: Win {metrics['win_rate']:.1f}%, Return {metrics['cumulative_return']:.2f}%")

        except Exception as e:
            logger.error(f"Trading execution error: {e}", exc_info=True)

    def cleanup_temp_files(self):
        """Cleanup temporary audio files"""
        for temp_file in AUDIO_TEMP_DIR.glob('temp_audio*'):
            try:
                temp_file.unlink()
                logger.debug(f"Cleaned up: {temp_file.name}")
            except Exception as e:
                logger.warning(f"Failed to clean up {temp_file.name}: {e}")

    async def process_new_video(self, video_info: Dict) -> Optional[Dict]:
        """Process new video: extract, transcribe, analyze, trade"""
        logger.info(f"Processing: {video_info['title']}")

        try:
            # 먼저 YouTube 쿠키 확보 (없거나 오래되면 Playwright로 생성)
            cookies_file = await self.ensure_youtube_cookies()
            
            # Extract audio (쿠키 파일 전달)
            audio_file = self.extract_audio(video_info['link'], cookies_file)
            if not audio_file:
                return None

            # Transcribe
            transcript = self.transcribe_audio(audio_file)
            if not transcript:
                return None

            # Save transcript to transcripts directory
            transcript_file = TRANSCRIPTS_DIR / f"transcript_{video_info['id']}.txt"
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(f"Video: {video_info['title']}\n")
                f.write(f"URL: {video_info['link']}\n")
                f.write(f"Date: {video_info['published']}\n\n")
                f.write(transcript)
            logger.info(f"Transcript saved: {transcript_file.name}")

            # Analyze
            analysis = await self.analyze_video(video_info, transcript)
            if not analysis:
                return None

            # Skip if not Jeon's own opinion
            if analysis.get('content_type') == '스킵':
                logger.info("Content type '스킵', skipping")
                return analysis

            # Send Telegram (analysis summary)
            await self.send_telegram_message(analysis)

            # Execute trading
            await self.execute_trading_strategy(analysis)

            # Send portfolio status message
            await self.send_portfolio_status_message()

            return analysis

        except Exception as e:
            logger.error(f"Video processing error: {e}", exc_info=True)
            return None
        finally:
            self.cleanup_temp_files()

    async def process_single_video_url(self, video_url: str):
        """Test mode: process single video"""
        logger.info("="*80)
        logger.info("Single Video Mode")
        logger.info("="*80)

        try:
            await self.db.initialize()

            video_info = {
                'title': 'Test Video',
                'published': datetime.now().isoformat(),
                'link': video_url,
                'id': video_url.split('=')[-1] if '=' in video_url else video_url.split('/')[-1]
            }

            analysis = await self.process_new_video(video_info)

            if analysis:
                print("\n" + "="*80)
                print("ANALYSIS RESULT")
                print("="*80)
                print(json.dumps(analysis, ensure_ascii=False, indent=2))
                print("="*80 + "\n")

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise

    async def run(self):
        """Main workflow"""
        logger.info("="*80)
        logger.info("Jeon Ingu Contrarian Trading - Starting")
        logger.info("="*80)

        try:
            await self.db.initialize()

            # Fetch videos
            current_videos = self.fetch_latest_videos()
            if not current_videos:
                logger.warning("No videos found")
                return

            # Load history
            previous_videos = self.load_previous_videos()

            # First run check
            if len(previous_videos) == 0:
                logger.info("🎬 First run - initializing history")
                self.save_video_history(current_videos)
                logger.info("✅ History initialized. Run again to process new videos.")
                return

            # Find new videos
            new_videos = self.find_new_videos(current_videos, previous_videos)
            if not new_videos:
                logger.info("No new videos")
                return

            # Process each new video
            for video in new_videos:
                analysis = await self.process_new_video(video)
                if analysis:
                    print(json.dumps(analysis, ensure_ascii=False, indent=2))

            # Save history
            self.save_video_history(current_videos)

            logger.info("="*80)
            logger.info("Completed")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Fatal error: {e}", exc_info=True)
            raise


async def main():
    """Entry point"""
    parser = argparse.ArgumentParser(
        description="Jeon Ingu Contrarian Trading Simulator"
    )
    parser.add_argument('--video-url', type=str, help='Test mode: process specific video URL')
    parser.add_argument('--no-telegram', action='store_true', help='Disable Telegram')
    args = parser.parse_args()

    try:
        bot = JeoninguTrading(use_telegram=not args.no_telegram)

        if args.video_url:
            await bot.process_single_video_url(args.video_url)
        else:
            await bot.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
