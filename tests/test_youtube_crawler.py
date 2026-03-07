#!/usr/bin/env python3
"""
Test script for YouTube Event Fund Crawler

Quick validation of individual components without full workflow execution.
"""

import sys
import asyncio
import logging
from pathlib import Path

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from youtube_event_fund_crawler import YouTubeEventFundCrawler

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def test_rss_fetch():
    """Test RSS feed fetching"""
    logger.info("="*80)
    logger.info("TEST 1: RSS Feed Fetching")
    logger.info("="*80)

    try:
        crawler = YouTubeEventFundCrawler()
        videos = crawler.fetch_latest_videos()

        if videos:
            logger.info(f"✅ Successfully fetched {len(videos)} videos")
            logger.info("\nLatest video:")
            logger.info(f"  - Title: {videos[0]['title']}")
            logger.info(f"  - ID: {videos[0]['id']}")
            logger.info(f"  - Published: {videos[0]['published']}")
            logger.info(f"  - Link: {videos[0]['link']}")
            return True
        else:
            logger.error("❌ No videos found")
            return False

    except Exception as e:
        logger.error(f"❌ RSS fetch failed: {e}", exc_info=True)
        return False


def test_video_history():
    """Test video history save/load"""
    logger.info("\n" + "="*80)
    logger.info("TEST 2: Video History Management")
    logger.info("="*80)

    try:
        crawler = YouTubeEventFundCrawler()

        # Create test data
        test_videos = [
            {
                'id': 'test123',
                'title': 'Test Video',
                'published': '2025-11-22T00:00:00Z',
                'link': 'https://youtube.com/watch?v=test123',
                'author': 'Test Author'
            }
        ]

        # Test save
        crawler.save_video_history(test_videos)
        logger.info("✅ Saved test video history")

        # Test load
        loaded_videos = crawler.load_previous_videos()
        if loaded_videos and loaded_videos[0]['id'] == 'test123':
            logger.info("✅ Loaded video history successfully")
            logger.info(f"  - Loaded {len(loaded_videos)} videos")
            return True
        else:
            logger.error("❌ Video history mismatch")
            return False

    except Exception as e:
        logger.error(f"❌ Video history test failed: {e}", exc_info=True)
        return False


def test_new_video_detection():
    """Test new video detection logic"""
    logger.info("\n" + "="*80)
    logger.info("TEST 3: New Video Detection")
    logger.info("="*80)

    try:
        crawler = YouTubeEventFundCrawler()

        # Create mock data
        previous_videos = [
            {'id': 'old1', 'title': 'Old Video 1', 'published': '2025-11-20', 'link': 'http://...'},
            {'id': 'old2', 'title': 'Old Video 2', 'published': '2025-11-19', 'link': 'http://...'}
        ]

        current_videos = [
            {'id': 'new1', 'title': 'New Video 1', 'published': '2025-11-22', 'link': 'http://...'},
            {'id': 'old1', 'title': 'Old Video 1', 'published': '2025-11-20', 'link': 'http://...'},
            {'id': 'old2', 'title': 'Old Video 2', 'published': '2025-11-19', 'link': 'http://...'}
        ]

        # Test detection
        new_videos = crawler.find_new_videos(current_videos, previous_videos)

        if len(new_videos) == 1 and new_videos[0]['id'] == 'new1':
            logger.info("✅ New video detection successful")
            logger.info(f"  - Found {len(new_videos)} new video(s)")
            logger.info(f"  - New video: {new_videos[0]['title']}")
            return True
        else:
            logger.error(f"❌ Detection failed: found {len(new_videos)} videos (expected 1)")
            return False

    except Exception as e:
        logger.error(f"❌ New video detection failed: {e}", exc_info=True)
        return False


def test_agent_creation():
    """Test AI agent creation (no execution)"""
    logger.info("\n" + "="*80)
    logger.info("TEST 4: AI Agent Creation")
    logger.info("="*80)

    try:
        crawler = YouTubeEventFundCrawler()

        video_info = {
            'title': 'Test Video',
            'published': '2025-11-22',
            'link': 'https://youtube.com/watch?v=test'
        }

        transcript = "이것은 테스트 자막입니다. 시장이 상승할 것으로 보입니다."

        agent = crawler.create_analysis_agent(video_info, transcript)

        if agent and hasattr(agent, 'instruction'):
            logger.info("✅ Agent created successfully")
            logger.info(f"  - Agent name: {agent.name}")
            logger.info(f"  - Instruction length: {len(agent.instruction)} chars")
            return True
        else:
            logger.error("❌ Agent creation failed")
            return False

    except Exception as e:
        logger.error(f"❌ Agent creation failed: {e}", exc_info=True)
        return False


async def test_analysis_mock():
    """Test analysis with mock transcript (no actual video download)"""
    logger.info("\n" + "="*80)
    logger.info("TEST 5: Analysis Execution (Mock)")
    logger.info("="*80)

    try:
        crawler = YouTubeEventFundCrawler()

        video_info = {
            'title': '📈 코스피 3,000 돌파 임박! 지금이 매수 적기',
            'published': '2025-11-22T09:00:00Z',
            'link': 'https://youtube.com/watch?v=test123'
        }

        # Mock transcript
        transcript = """
        안녕하세요, 전인구입니다. 오늘은 코스피 전망에 대해 말씀드리겠습니다.

        최근 시장 상황을 보면 상승 모멘텀이 매우 강합니다. 외국인 매수세가 이어지고 있고,
        반도체 업종의 실적이 개선되면서 코스피가 3,000선을 돌파할 가능성이 높아 보입니다.

        지금은 매수 적기라고 생각합니다. 특히 삼성전자와 SK하이닉스 같은 대형주에
        투자하시는 것을 추천드립니다.

        단기적으로 일부 조정이 있을 수 있지만, 중장기적으로 상승 추세는 계속될 것으로
        예상됩니다. 현금 비중을 낮추고 주식 비중을 높이는 것이 좋겠습니다.
        """

        logger.info("Running mock analysis (this may take 30-60 seconds)...")
        analysis = await crawler.analyze_video(video_info, transcript)

        if analysis and len(analysis) > 100:
            logger.info("✅ Analysis completed successfully")
            logger.info(f"  - Analysis length: {len(analysis)} chars")
            logger.info("\n--- ANALYSIS PREVIEW (first 500 chars) ---")
            logger.info(analysis[:500] + "...")
            logger.info("--- END PREVIEW ---")
            return True
        else:
            logger.error("❌ Analysis failed or too short")
            return False

    except Exception as e:
        logger.error(f"❌ Analysis execution failed: {e}", exc_info=True)
        return False


async def main():
    """Run all tests"""
    logger.info("\n" + "🧪 "*40)
    logger.info("YouTube Event Fund Crawler - Test Suite")
    logger.info("🧪 "*40 + "\n")

    # Check mcp_agent.secrets.yaml exists
    secrets_file = Path(__file__).parent.parent / "mcp_agent.secrets.yaml"
    if not secrets_file.exists():
        logger.error("❌ mcp_agent.secrets.yaml not found")
        logger.error("Please copy mcp_agent.secrets.yaml.example and configure your API keys")
        return

    import yaml
    try:
        with open(secrets_file, 'r') as f:
            secrets = yaml.safe_load(f)
        openai_api_key = secrets.get('openai', {}).get('api_key')
        if not openai_api_key or openai_api_key == "example key":
            logger.error("❌ OPENAI_API_KEY not configured in mcp_agent.secrets.yaml")
            logger.error("Please set openai.api_key in the secrets file")
            return
    except Exception as e:
        logger.error(f"❌ Error loading secrets file: {e}")
        return

    results = []

    # Run tests
    results.append(("RSS Fetch", test_rss_fetch()))
    results.append(("Video History", test_video_history()))
    results.append(("New Video Detection", test_new_video_detection()))
    results.append(("Agent Creation", test_agent_creation()))

    # Analysis test (async, requires API call)
    logger.info("\n⚠️  The next test will make an actual OpenAI API call")
    logger.info("⚠️  This will consume API credits")

    user_input = input("\nProceed with analysis test? (y/n): ").strip().lower()
    if user_input == 'y':
        analysis_result = await test_analysis_mock()
        results.append(("Analysis Execution", analysis_result))
    else:
        logger.info("Skipping analysis test")
        results.append(("Analysis Execution", None))

    # Summary
    logger.info("\n" + "="*80)
    logger.info("TEST SUMMARY")
    logger.info("="*80)

    for test_name, result in results:
        if result is True:
            status = "✅ PASS"
        elif result is False:
            status = "❌ FAIL"
        else:
            status = "⊘ SKIP"

        logger.info(f"{test_name:.<50} {status}")

    passed = sum(1 for _, r in results if r is True)
    failed = sum(1 for _, r in results if r is False)
    skipped = sum(1 for _, r in results if r is None)

    logger.info("="*80)
    logger.info(f"Total: {len(results)} | Passed: {passed} | Failed: {failed} | Skipped: {skipped}")
    logger.info("="*80)


if __name__ == "__main__":
    asyncio.run(main())
