#!/usr/bin/env python3
"""
Jeon Ingu Contrarian Trading System - Analysis & Trading Simulator
(Jeon Ingu Economic Research Institute)

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

# Output directories - organize outputs in subdirectories
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
CHANNEL_ID = "UCznImSIaxZR7fdLCICLdgaQ"  # Jeon Ingu Economic Research Institute
RSS_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={CHANNEL_ID}"
VIDEO_HISTORY_FILE = DATA_DIR / "jeoningu_video_history.json"
AUDIO_FILE = AUDIO_TEMP_DIR / "temp_audio.mp3"

# Trading configuration
INITIAL_CAPITAL = 10000000  # 10 million KRW initial capital

# Stock codes
KODEX_LEVERAGE = "122630"  # KODEX Leverage
KODEX_INVERSE_2X = "252670"  # KODEX 200 Futures Inverse 2X


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

    def create_title_filter_agent(self, title: str) -> Agent:
        """Create AI agent for filtering video titles"""
        instruction = f"""You are an expert at analyzing YouTube video titles.
Review the title from Jeon Ingu Economic Research Institute channel and determine whether it's Jeon Ingu's own opinion or a guest interview.

## Video Title
{title}

## Classification Criteria

**Interview Video (guest appears for discussion):**
- Contains person's name with titles like "Professor", "Doctor", "Analyst", "Writer", "Expert"
- Contains series numbers like "Part 1", "Part 2" (Part 1/2/3 in Korean)
- Examples:
  * "Why Japan is raising interest rates (ft. Professor Kim Part 2)" → Interview
  * "You should buy this Korean industry (ft. Professor So Part 2)" → Interview
  * "AI will make us more lonely (ft. Writer Yoon Part 2)" → Interview
  * "The real reason exchange rates are rising (ft. Professor Kim Part 1)" → Interview
  * "US and North Korea changed, buy these stocks first (ft. Professor So Part 3)" → Interview

**Own Opinion Video (Jeon Ingu speaking alone):**
- No person names/titles, only numbers or topics
- No "ft." at all
- Examples:
  * "Next year I will invest here (ft. 1 open)" → Own opinion
  * "Coupang special tax audit, should we buy Coupang stock fighting the government?" → Own opinion
  * "Finally the 1480 won defense line was broken" → Own opinion
  * "100% capital gains tax exemption if selling US stocks and buying Korean stocks (ft. exchange rate drop)" → Own opinion

## Core Rules
- Title with "Professor", "Doctor", "Writer", "Analyst" + "Part 1/2/3" (or Korean Part notation) → **Always Interview**
- No person names, only numbers (e.g., "ft.1") → Own opinion

## Output
Output only one of: "Own Opinion" or "Interview"
"""
        return Agent(
            name="title_filter",
            instruction=instruction,
            server_names=[]
        )

    async def filter_jeoningu_own_videos(self, videos: List[Dict]) -> List[Dict]:
        """
        Filter videos to only include Jeon Ingu's own opinions (not interviews)
        
        Uses mcp-agent with GPT-4o-mini to classify video titles quickly and cheaply.
        
        Patterns:
        - "ft. [person name]" (Part 2, Part 3 etc.) → Interview (skip)
        - "ft. [topic]" or no "ft." → Jeon's own opinion (analyze)
        """
        if not videos:
            return []
        
        logger.info(f"Filtering {len(videos)} videos by title...")
        
        filtered_videos = []
        
        for video in videos:
            title = video['title']
            
            try:
                agent = self.create_title_filter_agent(title)
                app = MCPApp(name="title_filter")

                async with app.run() as _:
                    llm = await agent.attach_llm(OpenAIAugmentedLLM)
                    result = await llm.generate_str(
                        message="Analyze the above title and output only 'Own Opinion' or 'Interview'.",
                        request_params=RequestParams(
                            model="gpt-4.1-mini",  # Better instruction following than nano ($0.40/1M in, $1.60/1M out)
                            maxTokens=10,
                            max_iterations=1,
                            parallel_tool_calls=False,
                            use_history=False
                        )
                    )
                
                classification = result.strip()

                if classification == "Own Opinion":
                    filtered_videos.append(video)
                    logger.info(f"[{classification}] {title}")
                else:
                    logger.info(f"[{classification}] {title} - Skipping")
                    
            except Exception as e:
                logger.error(f"Title classification error for '{title}': {e}")
                # On error, include the video (safer to analyze than skip)
                filtered_videos.append(video)
                logger.warning(f"Error occurred, including video: {title}")
        
        logger.info(f"Filtered: {len(filtered_videos)}/{len(videos)} videos are Jeon's own opinions")
        return filtered_videos

    def extract_audio(self, video_url: str) -> Optional[str]:
        """Extract audio from YouTube using Docker"""
        logger.info(f"Extracting audio: {video_url}")

        # Clean up old files in audio_temp directory
        for temp_file in AUDIO_TEMP_DIR.glob('temp_audio.*'):
            try:
                temp_file.unlink()
            except Exception:
                pass

        # Cookie file path
        cookies_file = SECRETS_DIR / "youtube_cookies.txt"
        
        if not cookies_file.exists():
            logger.error(f"No cookies file found at {cookies_file}")
            logger.error("Run on local: yt-dlp --cookies-from-browser chrome --cookies youtube_cookies.txt --skip-download 'https://www.youtube.com'")
            return None

        try:
            import subprocess

            # Run yt-dlp with Docker
            output_template = "/downloads/temp_audio.%(ext)s"
            
            cmd = [
                "docker", "run", "--rm",
                "-v", f"{SECRETS_DIR}:/downloads",
                "jauderho/yt-dlp",
                "--cookies", "/downloads/youtube_cookies.txt",
                "-f", "bestaudio",
                "-x", "--audio-format", "mp3",
                "-o", output_template,
                video_url
            ]
            
            logger.info(f"Running: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                logger.error(f"Docker yt-dlp failed: {result.stderr}")
                return None

            # Find result file (created in SECRETS_DIR)
            output_file = SECRETS_DIR / "temp_audio.mp3"
            if output_file.exists():
                # Move to AUDIO_TEMP_DIR
                target_file = AUDIO_TEMP_DIR / "temp_audio.mp3"
                output_file.rename(target_file)
                logger.info(f"Audio extraction successful: {target_file}")
                return str(target_file)
            
            logger.error("Output file not found after docker run")
            return None
            
        except subprocess.TimeoutExpired:
            logger.error("Docker yt-dlp timed out")
            return None
        except Exception as e:
            logger.error(f"Audio extraction error: {e}")
            return None

    def transcribe_audio(self, audio_file: str) -> Optional[str]:
        """Transcribe audio with Whisper"""
        logger.info(f"Transcribing: {audio_file}")

        try:
            file_size = Path(audio_file).stat().st_size
            file_size_mb = file_size / 1024 / 1024
            max_size = 20 * 1024 * 1024  # 20MB (conservative limit)

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
                        timeout=600.0  # 10 minute timeout for long audio
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
            chunk_length_ms = 5 * 60 * 1000  # 5 minutes (safe size considering 20MB limit)
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
        instruction = f"""You are a contrarian investment expert analyzing Jeon Ingu Economic Research Institute content.

## Video Information
- Title: {video_info['title']}
- Published: {video_info['published']}
- URL: {video_info['link']}

## Video Transcript
{transcript}

## Analysis Tasks

### Step 1: Content Type Classification
Is this a video where Jeon Ingu personally presents market opinions?
- "Own Opinion": Jeon Ingu films solo and directly mentions market outlook
- "Skip": Interview format with Q&A, simple news summary, or guest interview only

### Step 2: Market Sentiment Analysis
Determine Jeon Ingu's market sentiment:
- "Bullish": Optimistic outlook, buy recommendations, emphasizing positive signals
- "Bearish": Pessimistic outlook, sell/hold recommendations, emphasizing negative signals
- "Neutral": No clear direction, ambiguous opinion

### Step 3: Contrarian Strategy Decision

**Investment Instruments (use only 2)**:
- KODEX Leverage (122630): 2x tracking of KOSPI 200 index
- KODEX 200 Futures Inverse 2X (252670): 2x opposite direction of KOSPI 200

**Strategy Rules**:
1. Jeon **Bullish** sentiment → Bet opposite on **decline** → **Buy KODEX 200 Futures Inverse 2X (252670)**
2. Jeon **Neutral** sentiment → Wait and see → **Sell all holdings (cash out)**
3. Jeon **Bearish** sentiment → Bet opposite on **rise** → **Buy KODEX Leverage (122630)**

**Position Management**:
- Always hold only 1 instrument (122630 or 252670)
- When switching: Sell existing holdings → Buy new instrument
- When neutral: Must sell if holding any position
- When buying: **Invest full available balance** (all-in strategy)

## Output Format (JSON)

Output must follow the JSON schema below (pure JSON only, no markdown code blocks):

```json
{{
  "video_info": {{
    "video_id": "{video_info['id']}",
    "title": "{video_info['title']}",
    "video_date": "{video_info['published']}",
    "video_url": "{video_info['link']}"
  }},
  "content_type": "Own Opinion" | "Skip",
  "jeon_sentiment": "Bullish" | "Bearish" | "Neutral",
  "jeon_reasoning": "Summarize Jeon Ingu's key statements in 2-3 sentences",
  "contrarian_action": "Buy Inverse 2X" | "Buy Leverage" | "Sell All",
  "target_stock": {{
    "code": "252670" | "122630" | null,
    "name": "KODEX 200 Futures Inverse 2X" | "KODEX Leverage" | null
  }},
  "telegram_summary": "Telegram message content (within 5 lines, include emojis)"
}}
```

## Important Notes
- **Output valid JSON only** (remove markdown code blocks)
- Analyze based only on transcript content (no speculation)
- Choose only one instrument: 122630 or 252670
- Set target_stock to null when neutral
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
                    message="Analyze the video according to the instructions above and output the contrarian investment strategy in JSON format.",
                    request_params=RequestParams(
                        model="gpt-4.1",
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
            sentiment = analysis.get('jeon_sentiment', 'Unknown')
            action = analysis.get('contrarian_action', 'Hold')

            message_text = f"""
🧪 <b>Jeon Ingu Contrarian Investment Experiment</b>

<i>This is a simulation that bets the exact opposite of Jeon Ingu Economic Research Institute's predictions.
An experiment to verify the actual effectiveness of the famous 'Jeon Inverse Honey' strategy in the community.</i>

━━━━━━━━━━━━━━━━━━━━

📺 <b>Latest Video Analysis</b>
<b>{video_title}</b>

{summary}

📊 Jeon Ingu Sentiment: <b>{sentiment}</b>
💡 Contrarian Action: <b>{action}</b>

🔗 <a href="{video_url}">Watch Video</a>

━━━━━━━━━━━━━━━━━━━━

📈 <b>Check Real-time Performance</b>
Visit https://stocksimulation.kr/ and
click the <b>'Lab'</b> tab!

⚠️ This information is for reference only, not investment advice.
💼 All investment decisions and their consequences are the responsibility of the investor.
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
            from datetime import datetime

            # Get current data
            position = await self.db.get_current_position()
            balance = await self.db.get_latest_balance()
            metrics = await self.db.calculate_performance_metrics()
            trade_history = await self.db.get_trade_history(limit=10)

            # Calculate total realized P&L from ALL completed trades (not just recent 10)
            all_sells_query = """
                SELECT COALESCE(SUM(profit_loss), 0) as total_realized_pl
                FROM jeoningu_trades
                WHERE trade_type = 'SELL'
            """
            result = await self.db.execute_read_query(all_sells_query)
            total_realized_pl = result[0]['total_realized_pl'] if result else 0

            # Build message
            message_parts = []

            if position:
                # Holding position
                current_price = get_current_price(position['stock_code'])
                current_value = position['quantity'] * current_price
                unrealized_pl = current_value - position['buy_amount']
                unrealized_pl_pct = (unrealized_pl / position['buy_amount']) * 100 if position['buy_amount'] > 0 else 0

                # Calculate holding period
                buy_date = datetime.fromisoformat(position['buy_date'].replace('Z', '+00:00')) if position.get('buy_date') else None
                holding_days = (datetime.now(buy_date.tzinfo if buy_date and buy_date.tzinfo else None) - buy_date).days if buy_date else 0

                message_parts.append("📊 **Current Position**\n")
                message_parts.append(f"🎯 {position['stock_name']}")
                message_parts.append(f"┣ Holdings: {position['quantity']:,} shares × {current_price:,.0f} KRW")
                message_parts.append(f"┣ Market Value: {current_value:,.0f} KRW")
                message_parts.append(f"┣ Avg Cost: {position['buy_price']:,.0f} KRW")

                # Unrealized P&L (emoji for color indication)
                pl_emoji = "🔴" if unrealized_pl < 0 else "🟢" if unrealized_pl > 0 else "⚪"
                message_parts.append(f"┗ Unrealized P&L: {pl_emoji} {unrealized_pl:+,.0f} KRW ({unrealized_pl_pct:+.2f}%)")

                if holding_days > 0:
                    message_parts.append(f"\n⏱ Day {holding_days} holding")
                else:
                    message_parts.append(f"\n⏱ Entered today")
            else:
                # Cash position
                unrealized_pl = 0  # No unrealized P&L when holding cash

                message_parts.append("📊 **Current Position**\n")
                message_parts.append(f"💵 Cash: {balance:,.0f} KRW")

            # Separator
            message_parts.append("\n━━━━━━━━━━━━━━━━━━━━\n")

            # Calculate cumulative performance
            # Total P&L = Total realized P&L + Current unrealized P&L
            if position:
                total_pl = total_realized_pl + unrealized_pl
            else:
                total_pl = total_realized_pl

            total_assets_actual = INITIAL_CAPITAL + total_pl
            total_return_pct_actual = (total_pl / INITIAL_CAPITAL) * 100

            message_parts.append("📈 **Cumulative Performance**")
            message_parts.append(f"┣ Start: {INITIAL_CAPITAL/10000:,.0f}M KRW")
            message_parts.append(f"┣ Current: {total_assets_actual/10000:,.0f}M KRW")

            return_emoji = "📈" if total_return_pct_actual > 0 else "📉" if total_return_pct_actual < 0 else "➖"
            message_parts.append(f"┗ Return: {return_emoji} {total_return_pct_actual:+.2f}%")

            # Show trading statistics if there are closed trades
            if metrics['total_trades'] > 0:
                message_parts.append(f"\n🎲 **Trading Record**")
                message_parts.append(f"┣ Completed: {metrics['total_trades']} trades")

                # Show draws if any
                if metrics.get('draw_trades', 0) > 0:
                    message_parts.append(f"┣ W/D/L: {metrics['winning_trades']}W {metrics['draw_trades']}D {metrics['losing_trades']}L")
                else:
                    message_parts.append(f"┣ W/L: {metrics['winning_trades']}W {metrics['losing_trades']}L")

                message_parts.append(f"┣ Win Rate: {metrics['win_rate']:.0f}%")
                message_parts.append(f"┗ Avg per Trade: {metrics['avg_return_per_trade']:+.1f}%")

            # Recent trade history (max 3 trades)
            recent_trades = [t for t in trade_history if t.get('trade_type') in ('BUY', 'SELL')][:3]
            if recent_trades:
                message_parts.append(f"\n📝 **Recent Trades**")
                for trade in recent_trades:
                    trade_date = trade.get('analyzed_date', '')[:10]
                    trade_type = trade.get('trade_type')
                    stock_name = trade.get('stock_name', '')
                    # Shorten stock name
                    short_name = stock_name.replace('KODEX ', '').replace('200 Futures', '')

                    if trade_type == 'BUY':
                        message_parts.append(f"• {trade_date} Buy {short_name}")
                    elif trade_type == 'SELL':
                        pl = trade.get('profit_loss', 0)
                        pl_pct = trade.get('profit_loss_pct', 0)

                        # Choose emoji based on P&L
                        if pl > 0:
                            pl_emoji = "✅"  # Win
                        elif pl < 0:
                            pl_emoji = "❌"  # Loss
                        else:
                            pl_emoji = "➖"  # Draw

                        message_parts.append(f"• {trade_date} Sell {short_name} {pl_emoji}{pl_pct:+.1f}%")

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

    async def execute_trading_strategy(self, analysis: Dict) -> bool:
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
                return True

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
            if sentiment == 'Neutral':
                if current_position:
                    # Sell current position - get real price
                    sell_price = get_current_price(current_position['stock_code'])
                    if not sell_price or sell_price <= 0:
                        logger.error(
                            "Price unavailable for %s; leaving video unprocessed for retry",
                            current_position['stock_code'],
                        )
                        return False
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
                        # balance_after = balance_before + profit_loss for SELL
                        # Reason: Assets change by realized P&L (stock → cash conversion + P&L reflection)
                        'balance_after': new_balance,
                        'cumulative_return_pct': cumulative_return_pct,
                        'notes': f"Sell all on neutral sentiment (P&L: {profit_loss:,.0f} KRW, {profit_loss_pct:+.2f}%)"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    logger.info(f"SELL: {current_position['stock_name']} (neutral sentiment)")
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
                        'notes': 'Neutral sentiment, no holdings'
                    }
                    await self.db.insert_trade(record)
                    logger.info("Neutral sentiment, no holdings")

            # Case 2: UP or DOWN → Buy target stock
            elif sentiment in ['Bullish', 'Bearish']:
                target_code = target_stock.get('code')
                target_name = target_stock.get('name')

                if not target_code:
                    logger.warning(f"No target stock for sentiment: {sentiment}")
                    return False

                # Resolve every price required for a switch before committing
                # its first leg. This prevents a valid SELL from being recorded
                # when the subsequent BUY price is unavailable.
                buy_price = None
                needs_buy = not current_position or current_position['stock_code'] != target_code
                if needs_buy:
                    buy_price = get_current_price(target_code)
                    if not buy_price or buy_price <= 0:
                        logger.error(
                            "Price unavailable for %s; leaving video unprocessed for retry",
                            target_code,
                        )
                        return False

                # Step 1: Sell current position if different stock
                if current_position and current_position['stock_code'] != target_code:
                    # Sell different stock - get real price
                    sell_price = get_current_price(current_position['stock_code'])
                    if not sell_price or sell_price <= 0:
                        logger.error(
                            "Price unavailable for %s; leaving video unprocessed for retry",
                            current_position['stock_code'],
                        )
                        return False
                    sell_amount = current_position['quantity'] * sell_price
                    profit_loss = sell_amount - current_position['buy_amount']
                    profit_loss_pct = (profit_loss / current_position['buy_amount']) * 100

                    new_balance = current_balance + profit_loss
                    cumulative_return_pct = ((new_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100

                    # Use SELL suffix for video_id to avoid UNIQUE constraint
                    # when both SELL and BUY happen from same video
                    sell_trade = {
                        'video_id': f"{video_info['video_id']}_SELL",
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
                        'notes': f"Sell for position switch → {target_name} buy scheduled"
                    }
                    await self.db.insert_trade(sell_trade)
                    trades_executed.append(sell_trade)
                    current_balance = new_balance
                    logger.info(f"SELL: {current_position['stock_name']} (position switch)")

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
                        'notes': f'Already holding {target_name}, no action'
                    }
                    await self.db.insert_trade(record)
                    logger.info(f"Already holding {target_name}")
                    return True

                # Step 2: Buy target stock with FULL BALANCE - get real price
                quantity = int(current_balance / buy_price)  # Full balance investment
                buy_amount = quantity * buy_price

                # Use _BUY suffix when this is part of a position switch
                # (i.e., when we just sold a different position from the same video)
                video_id_for_buy = video_info['video_id']
                if trades_executed:  # We just did a sell, so use suffix
                    video_id_for_buy = f"{video_info['video_id']}_BUY"

                buy_trade = {
                    'video_id': video_id_for_buy,
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
                    # balance_after = balance_before for BUY
                    # Reason: Cash → stock conversion, total asset valuation unchanged
                    # (Actual cash is deducted and stock increases, but equal on valuation basis)
                    'balance_after': current_balance,
                    'cumulative_return_pct': ((current_balance - INITIAL_CAPITAL) / INITIAL_CAPITAL) * 100,
                    'notes': f"{sentiment} sentiment → Contrarian {target_name} all-in buy ({buy_amount:,.0f} KRW)"
                }
                await self.db.insert_trade(buy_trade)
                trades_executed.append(buy_trade)
                logger.info(f"BUY: {target_name} x {quantity} @ {buy_price:,} (all-in: {buy_amount:,.0f} KRW)")

            # Log performance metrics
            metrics = await self.db.calculate_performance_metrics()
            logger.info(f"Performance: Win {metrics['win_rate']:.1f}%, Return {metrics['cumulative_return']:.2f}%")
            return True

        except Exception as e:
            logger.error(f"Trading execution error: {e}", exc_info=True)
            return False

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
            # Extract audio
            audio_file = self.extract_audio(video_info['link'])
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
            if analysis.get('content_type') == 'Skip':
                logger.info("Content type 'Skip', skipping")
                return analysis

            # Send Telegram (analysis summary)
            await self.send_telegram_message(analysis)

            # Execute trading
            if not await self.execute_trading_strategy(analysis):
                logger.warning("Trading deferred; video will remain eligible for retry")
                return None

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
                logger.info("First run - initializing history")
                self.save_video_history(current_videos)
                logger.info("History initialized. Run again to process new videos.")
                return

            # Find new videos
            new_videos = self.find_new_videos(current_videos, previous_videos)
            if not new_videos:
                logger.info("No new videos")
                return

            # Filter to only include Jeon's own opinion videos (skip interviews)
            filtered_videos = await self.filter_jeoningu_own_videos(new_videos)
            if not filtered_videos:
                logger.info("No videos to analyze after filtering")
                # Still save history even if all were filtered out
                self.save_video_history(current_videos)
                return

            # Process in chronological order (oldest first)
            # RSS returns newest first, so reverse for time-sequential analysis
            new_videos_chronological = list(reversed(filtered_videos))
            logger.info(f"Processing {len(new_videos_chronological)} videos in chronological order")

            # Process each new video
            deferred_ids = set()
            for video in new_videos_chronological:
                analysis = await self.process_new_video(video)
                if analysis:
                    print(json.dumps(analysis, ensure_ascii=False, indent=2))
                else:
                    deferred_ids.add(video['id'])

            # Preserve failed/deferred videos for a future retry while recording
            # every successfully processed or intentionally filtered item.
            history_videos = [v for v in current_videos if v['id'] not in deferred_ids]
            self.save_video_history(history_videos)

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
