"""
Phase 4: News Agent Social Context Tests

Focused tests for optional prefetched social sentiment context in the US news
analysis agent flow.
"""

import sys
from pathlib import Path

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores.agents import get_us_agent_directory


def test_news_agent_receives_prefetched_social_sentiment(sample_reference_date):
    """News agent should embed prefetched social sentiment context when provided."""
    agents = get_us_agent_directory(
        company_name="Tesla, Inc.",
        ticker="TSLA",
        reference_date=sample_reference_date,
        base_sections=["news_analysis"],
        language="en",
        prefetched_data={"social_sentiment": "### Structured Social Sentiment Snapshot (7d)\n- Average Buzz: 74.3/100"},
    )

    agent = agents["news_analysis"]
    assert "Structured Social Sentiment Snapshot" in agent.instruction
    assert "do not make extra tool calls for social sentiment" in agent.instruction
    assert agent.server_names == ["perplexity", "firecrawl"]
