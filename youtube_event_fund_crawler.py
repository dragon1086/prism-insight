#!/usr/bin/env python3
"""
YouTube Event Fund Crawler - '전인구경제연구소' Analysis System

This script monitors the YouTube channel '전인구경제연구소' for new videos,
transcribes them using OpenAI Whisper API, analyzes the content, and provides
contrarian investment recommendations.

Workflow:
1. Fetch latest videos from RSS feed
2. Compare with previous video list (stored in JSON)
3. Extract audio and transcribe with Whisper API
4. Analyze content and generate contrarian investment recommendations
5. Log results (future: integrate with automated trading)
"""

import os
import sys
import json
import logging
import asyncio
import yaml
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# Third-party imports
import feedparser
import yt_dlp
from openai import OpenAI
from mcp_agent.agents.agent import Agent
from mcp_agent.app import App

# Setup directories
EVENTS_DIR = Path("events")
EVENTS_DIR.mkdir(exist_ok=True)

# Configure logging
log_file = EVENTS_DIR / f"youtube_crawler_{datetime.now().strftime('%Y%m%d')}.log"
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
VIDEO_HISTORY_FILE = EVENTS_DIR / "youtube_video_history.json"
AUDIO_FILE = EVENTS_DIR / "temp_audio.mp3"


class YouTubeEventFundCrawler:
    """Main crawler class for YouTube event fund analysis"""

    def __init__(self):
        """Initialize crawler with OpenAI client"""
        # Load API key from mcp_agent.secrets.yaml
        secrets_file = Path("mcp_agent.secrets.yaml")
        if not secrets_file.exists():
            raise FileNotFoundError(
                "mcp_agent.secrets.yaml not found. "
                "Please copy mcp_agent.secrets.yaml.example and configure your API keys."
            )

        with open(secrets_file, 'r', encoding='utf-8') as f:
            secrets = yaml.safe_load(f)

        openai_api_key = secrets.get('openai', {}).get('api_key')
        if not openai_api_key or openai_api_key == "example key":
            raise ValueError(
                "OPENAI_API_KEY not found or not configured in mcp_agent.secrets.yaml. "
                "Please set openai.api_key in the secrets file."
            )

        self.openai_client = OpenAI(api_key=openai_api_key)
        logger.info("OpenAI client initialized successfully")

    def fetch_latest_videos(self) -> List[Dict[str, str]]:
        """
        Fetch latest videos from RSS feed

        Returns:
            List of video dictionaries with id, title, published, link
        """
        logger.info(f"Fetching RSS feed from: {RSS_URL}")

        try:
            feed = feedparser.parse(RSS_URL)
            videos = []

            for entry in feed.entries:
                video = {
                    'id': entry.yt_videoid,
                    'title': entry.title,
                    'published': entry.published,
                    'link': entry.link,
                    'author': entry.author if hasattr(entry, 'author') else 'Unknown'
                }
                videos.append(video)

            logger.info(f"Found {len(videos)} videos in feed")
            return videos

        except Exception as e:
            logger.error(f"Error fetching RSS feed: {e}", exc_info=True)
            return []

    def load_previous_videos(self) -> List[Dict[str, str]]:
        """
        Load previous video list from JSON file

        Returns:
            List of previous video dictionaries
        """
        if not Path(VIDEO_HISTORY_FILE).exists():
            logger.info("No previous video history found")
            return []

        try:
            with open(VIDEO_HISTORY_FILE, 'r', encoding='utf-8') as f:
                videos = json.load(f)
            logger.info(f"Loaded {len(videos)} previous videos")
            return videos
        except Exception as e:
            logger.error(f"Error loading video history: {e}", exc_info=True)
            return []

    def save_video_history(self, videos: List[Dict[str, str]]):
        """
        Save current video list to JSON file

        Args:
            videos: List of video dictionaries
        """
        try:
            with open(VIDEO_HISTORY_FILE, 'w', encoding='utf-8') as f:
                json.dump(videos, f, ensure_ascii=False, indent=2)
            logger.info(f"Saved {len(videos)} videos to history")
        except Exception as e:
            logger.error(f"Error saving video history: {e}", exc_info=True)

    def find_new_videos(self, current_videos: List[Dict], previous_videos: List[Dict]) -> List[Dict]:
        """
        Find new videos by comparing current and previous lists

        Args:
            current_videos: Current video list
            previous_videos: Previous video list

        Returns:
            List of new video dictionaries
        """
        previous_ids = {video['id'] for video in previous_videos}
        new_videos = [video for video in current_videos if video['id'] not in previous_ids]

        logger.info(f"Found {len(new_videos)} new videos")
        return new_videos

    def extract_audio(self, video_url: str) -> Optional[str]:
        """
        Extract audio from YouTube video using yt-dlp

        Args:
            video_url: YouTube video URL

        Returns:
            Path to extracted audio file, or None on failure
        """
        logger.info(f"Extracting audio from: {video_url}")

        # Remove existing audio file if present
        if AUDIO_FILE.exists():
            AUDIO_FILE.unlink()

        ydl_opts = {
            'format': 'bestaudio/best',
            'outtmpl': str(EVENTS_DIR / 'temp_audio.%(ext)s'),
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
            }],
            'quiet': True,
            'no_warnings': True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])

            if AUDIO_FILE.exists():
                logger.info("Audio extraction successful")
                return str(AUDIO_FILE)
            else:
                logger.error("Audio file not found after extraction")
                return None

        except Exception as e:
            logger.error(f"Error extracting audio: {e}", exc_info=True)
            return None

    def transcribe_audio(self, audio_file: str) -> Optional[str]:
        """
        Transcribe audio using OpenAI Whisper API

        Args:
            audio_file: Path to audio file

        Returns:
            Transcribed text, or None on failure
        """
        logger.info(f"Transcribing audio file: {audio_file}")

        try:
            with open(audio_file, "rb") as f:
                result = self.openai_client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="ko"
                )

            transcript = result.text
            logger.info(f"Transcription successful ({len(transcript)} characters)")
            return transcript

        except Exception as e:
            logger.error(f"Error transcribing audio: {e}", exc_info=True)
            return None

    def create_analysis_agent(self, video_info: Dict, transcript: str) -> Agent:
        """
        Create AI agent for content analysis and investment recommendation

        Args:
            video_info: Video metadata dictionary
            transcript: Transcribed text

        Returns:
            Configured Agent instance
        """
        instruction = f"""당신은 유튜브 채널 '전인구경제연구소'의 콘텐츠를 분석하는 역발상 투자 전문가입니다.

## 분석 대상 영상
- 제목: {video_info['title']}
- 게시일: {video_info['published']}
- URL: {video_info['link']}

## 영상 자막 전문
{transcript}

## 분석 과제

### 1단계: 콘텐츠 유형 판별
다음을 확인하세요:
- 전인구 본인이 직접 출연하여 의견을 제시하는 영상인가?
- 단순 뉴스 요약이나 게스트 인터뷰만 있는 영상은 아닌가?

**판별 결과**: "전인구 본인 의견" 또는 "스킵 대상" 중 하나로 명시

### 2단계: 시장 전망 분석 (전인구 본인 의견인 경우만)
전인구가 시장에 대해 어떤 기조로 말하고 있는지 분석:
- **상승 기조**: 낙관적 전망, 매수 추천, 긍정적 시그널 강조
- **하락 기조**: 비관적 전망, 매도/관망 추천, 부정적 시그널 강조
- **중립 기조**: 명확한 방향성 없음

**시장 기조 판단**: 상승/하락/중립 중 하나로 명시
**근거**: 자막에서 해당 판단을 내린 핵심 발언 인용 (3-5개)

### 3단계: 콘텐츠 요약
영상의 핵심 내용을 3-5개 불릿 포인트로 요약
- 주요 논점
- 언급된 경제 지표나 이슈
- 구체적으로 언급된 종목/섹터 (있는 경우)

### 4단계: 역발상 투자 전략 (Contrarian Investment)
전인구의 의견과 **반대** 방향으로 베팅하는 전략 제시:

**만약 상승 기조라면 (하락에 베팅)**:
- 인버스(Inverse) ETF/ETN 추천
  - KODEX 인버스 (114800)
  - TIGER 인버스 (252670)
  - KODEX 코스닥150 인버스 (251340)
- 방어주 추천 (헬스케어, 필수소비재 등)
- 풋옵션 전략 가능 종목

**만약 하락 기조라면 (상승에 베팅)**:
- 레버리지(Leverage) ETF/ETN 추천
  - KODEX 레버리지 (122630)
  - TIGER 레버리지 (233740)
  - KODEX 코스닥150 레버리지 (233160)
- 성장주/모멘텀주 추천
- 콜옵션 전략 가능 종목

**만약 중립 기조라면**:
- 관망 추천
- 변동성 관련 상품 검토

### 5단계: 리스크 경고
역발상 전략의 리스크 명시:
- 전인구의 의견이 맞을 경우의 손실 시나리오
- 권장 손절매 비율 (예: -5%, -10%)
- 포지션 사이징 권장 (전체 자산의 몇 %로 제한)

## 출력 형식
다음 형식으로 구조화된 분석 결과를 출력하세요:

```
# 전인구경제연구소 역발상 투자 분석

## 📺 영상 정보
- **제목**: {video_info['title']}
- **게시일**: {video_info['published']}
- **URL**: {video_info['link']}

## 1️⃣ 콘텐츠 유형 판별
[전인구 본인 의견 / 스킵 대상]

## 2️⃣ 시장 기조 분석
**판단**: [상승/하락/중립]

**근거**:
- [인용1]
- [인용2]
- [인용3]

## 3️⃣ 영상 내용 요약
- 핵심 논점 1
- 핵심 논점 2
- 핵심 논점 3

## 4️⃣ 역발상 투자 전략
### 추천 포지션: [매수/매도/관망]

### 추천 종목/상품
1. **[종목명] (종목코드)**
   - 유형: [ETF/ETN/개별주]
   - 이유: ...

2. **[종목명] (종목코드)**
   - 유형: [ETF/ETN/개별주]
   - 이유: ...

### 진입 전략
- 타이밍: ...
- 분할매수 권장: ...

## 5️⃣ 리스크 관리
- ⚠️ 손절매: -X% 도달 시 무조건 청산
- ⚠️ 포지션 크기: 전체 자산의 Y% 이하로 제한
- ⚠️ 전인구 의견이 맞을 경우 예상 손실: ...
```

## 주의사항
- 자막 내용만을 근거로 분석하세요 (추측 금지)
- 전인구가 직접 언급하지 않은 종목은 신중하게 추천하세요
- 역발상 전략의 높은 리스크를 명확히 경고하세요
- 투자 권유가 아닌 정보 제공 목적임을 명시하세요
"""

        return Agent(
            name="youtube_event_fund_analyst",
            instruction=instruction,
            server_names=[]  # No MCP servers needed for transcript analysis
        )

    async def analyze_video(self, video_info: Dict, transcript: str) -> str:
        """
        Analyze video content using AI agent

        Args:
            video_info: Video metadata
            transcript: Transcribed text

        Returns:
            Analysis result text
        """
        logger.info(f"Analyzing video: {video_info['title']}")

        try:
            agent = self.create_analysis_agent(video_info, transcript)

            async with App() as app:
                # Generate analysis using the agent
                result = await agent.generate_str(
                    "위 지시사항에 따라 영상을 분석하고 역발상 투자 전략을 제시해주세요.",
                    app=app
                )

            logger.info("Analysis completed successfully")
            return result

        except Exception as e:
            logger.error(f"Error during analysis: {e}", exc_info=True)
            return f"분석 실패: {str(e)}"

    def cleanup_temp_files(self):
        """Remove temporary audio files"""
        if AUDIO_FILE.exists():
            try:
                AUDIO_FILE.unlink()
                logger.info("Cleaned up temporary audio file")
            except Exception as e:
                logger.warning(f"Failed to clean up audio file: {e}")

    async def process_new_video(self, video_info: Dict) -> Optional[str]:
        """
        Process a new video: extract audio, transcribe, analyze

        Args:
            video_info: Video metadata dictionary

        Returns:
            Analysis result text, or None on failure
        """
        logger.info(f"Processing new video: {video_info['title']}")

        try:
            # Step 1: Extract audio
            audio_file = self.extract_audio(video_info['link'])
            if not audio_file:
                return None

            # Step 2: Transcribe audio
            transcript = self.transcribe_audio(audio_file)
            if not transcript:
                return None

            # Save transcript for debugging
            transcript_file = EVENTS_DIR / f"transcript_{video_info['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
            with open(transcript_file, 'w', encoding='utf-8') as f:
                f.write(f"Video: {video_info['title']}\n")
                f.write(f"URL: {video_info['link']}\n")
                f.write(f"Published: {video_info['published']}\n")
                f.write(f"\n{'='*80}\n\n")
                f.write(transcript)
            logger.info(f"Transcript saved to: {transcript_file}")

            # Step 3: Analyze content
            analysis = await self.analyze_video(video_info, transcript)

            # Save analysis result
            analysis_file = EVENTS_DIR / f"analysis_{video_info['id']}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
            with open(analysis_file, 'w', encoding='utf-8') as f:
                f.write(analysis)
            logger.info(f"Analysis saved to: {analysis_file}")

            return analysis

        except Exception as e:
            logger.error(f"Error processing video: {e}", exc_info=True)
            return None

        finally:
            # Always cleanup temporary files
            self.cleanup_temp_files()

    async def run(self):
        """Main execution workflow"""
        logger.info("="*80)
        logger.info("YouTube Event Fund Crawler - Starting")
        logger.info("="*80)

        try:
            # Step 1: Fetch latest videos from RSS
            current_videos = self.fetch_latest_videos()
            if not current_videos:
                logger.warning("No videos found in RSS feed")
                return

            # Step 2: Load previous video history
            previous_videos = self.load_previous_videos()

            # Step 3: Find new videos
            new_videos = self.find_new_videos(current_videos, previous_videos)

            if not new_videos:
                logger.info("No new videos found")
                return

            # Step 4: Process each new video
            for video in new_videos:
                logger.info("\n" + "="*80)
                logger.info(f"Processing: {video['title']}")
                logger.info("="*80)

                analysis = await self.process_new_video(video)

                if analysis:
                    # Print analysis to console
                    print("\n" + "="*80)
                    print("ANALYSIS RESULT")
                    print("="*80)
                    print(analysis)
                    print("="*80 + "\n")
                else:
                    logger.warning(f"Failed to analyze video: {video['title']}")

            # Step 5: Save updated video history
            self.save_video_history(current_videos)

            logger.info("="*80)
            logger.info("YouTube Event Fund Crawler - Completed")
            logger.info("="*80)

        except Exception as e:
            logger.error(f"Fatal error in main workflow: {e}", exc_info=True)
            raise


async def main():
    """Entry point"""
    try:
        crawler = YouTubeEventFundCrawler()
        await crawler.run()

    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
