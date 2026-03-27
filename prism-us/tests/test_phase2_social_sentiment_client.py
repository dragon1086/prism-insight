"""
Phase 2: Social Sentiment Client Tests

Tests for the optional social sentiment client used by PRISM-US.
"""

import sys
from pathlib import Path

import pytest

# Add paths for imports
PRISM_US_DIR = Path(__file__).parent.parent
PROJECT_ROOT = PRISM_US_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PRISM_US_DIR))

from cores.us_social_sentiment_client import USSocialSentimentClient


class _MockResponse:
    """Minimal requests-like response helper."""

    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_social_sentiment_snapshot_aggregates_sources(monkeypatch):
    """Client should aggregate reddit/x/news/polymarket into one snapshot."""
    payloads = {
        "reddit": [{"ticker": "TSLA", "buzz_score": 80.0, "bullish_pct": 45, "trend": "rising", "mentions": 640}],
        "x": [{"ticker": "TSLA", "buzz_score": 70.0, "bullish_pct": 60, "trend": "stable", "mentions": 1200}],
        "news": [{"ticker": "TSLA", "buzz_score": 60.0, "bullish_pct": 55, "trend": "stable", "mentions": 90}],
        "polymarket": [{"ticker": "TSLA", "buzz_score": 90.0, "bullish_pct": 70, "trend": "falling", "trade_count": 333}],
    }

    def fake_get(url, headers, params, timeout):
        assert headers["X-API-Key"] == "sk_test"
        assert params == {"tickers": "TSLA", "days": 7}
        for platform in payloads:
            if f"/{platform}/stocks/v1/compare" in url:
                return _MockResponse(payloads[platform])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("cores.us_social_sentiment_client.requests.get", fake_get)

    client = USSocialSentimentClient(api_key="sk_test", base_url="https://api.example.com")
    snapshot = client.get_social_sentiment_snapshot("TSLA", days=7)

    assert snapshot["ticker"] == "TSLA"
    assert snapshot["coverage"] == 4
    assert snapshot["average_buzz"] == 75.0
    assert snapshot["bullish_avg"] == 57.5
    assert snapshot["source_alignment"] == "Mixed"
    assert snapshot["sources"]["reddit"]["activity_value"] == 640
    assert snapshot["sources"]["polymarket"]["activity_label"] == "Trades"


def test_social_sentiment_snapshot_tolerates_partial_source_failures(monkeypatch):
    """Malformed or failing sources should be skipped without aborting the snapshot."""

    def fake_get(url, headers, params, timeout):
        if "/reddit/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "AAPL", "buzz_score": "82.5", "bullish_pct": "61", "trend": "rising", "mentions": "512"}])
        if "/x/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "AAPL", "buzz_score": "n/a", "bullish_pct": "", "trend": "stable", "mentions": None}])
        if "/news/stocks/v1/compare" in url:
            return _MockResponse([], status_code=500)
        if "/polymarket/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "AAPL", "buzz_score": 76, "bullish_pct": 58, "trend": "stable", "trade_count": 44}])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("cores.us_social_sentiment_client.requests.get", fake_get)

    client = USSocialSentimentClient(api_key="sk_test", base_url="https://api.example.com")
    snapshot = client.get_social_sentiment_snapshot("AAPL", days=7)

    assert snapshot["coverage"] == 2
    assert snapshot["average_buzz"] == 79.2
    assert snapshot["bullish_avg"] == 59.5
    assert "x" not in snapshot["sources"]
    assert "news" not in snapshot["sources"]


def test_social_sentiment_markdown_renders_expected_fields(monkeypatch):
    """Rendered markdown should expose the summary and per-source metrics."""

    def fake_get(url, headers, params, timeout):
        if "/reddit/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "MSFT", "buzz_score": 74.3, "bullish_pct": 52, "trend": "rising", "mentions": 200}])
        if "/x/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "MSFT", "buzz_score": 68.8, "bullish_pct": 49, "trend": "stable", "mentions": 180}])
        if "/news/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "MSFT", "buzz_score": 63.0, "bullish_pct": 51, "trend": "stable", "mentions": 42}])
        if "/polymarket/stocks/v1/compare" in url:
            return _MockResponse([{"ticker": "MSFT", "buzz_score": 71.0, "bullish_pct": 50, "trend": "falling", "trade_count": 18}])
        raise AssertionError(f"Unexpected URL: {url}")

    monkeypatch.setattr("cores.us_social_sentiment_client.requests.get", fake_get)

    client = USSocialSentimentClient(api_key="sk_test", base_url="https://api.example.com")
    markdown = client.get_social_sentiment_markdown("MSFT", days=7)

    assert "### Structured Social Sentiment Snapshot (7d)" in markdown
    assert "- Average Buzz: 69.3/100" in markdown
    assert "- Bullish Avg: 50.5%" in markdown
    assert "#### Reddit" in markdown
    assert "- Mentions: 200" in markdown
    assert "#### Polymarket" in markdown
    assert "- Trades: 18" in markdown
    assert markdown.index("#### Reddit") < markdown.index("#### X.com") < markdown.index("#### News") < markdown.index("#### Polymarket")
