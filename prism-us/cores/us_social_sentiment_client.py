"""
US Social Sentiment Client

Fetches structured US stock social sentiment snapshots from an optional
third-party API. The client is intentionally read-only and optional so the
existing PRISM-US analysis flow continues to work unchanged when no API key is
configured.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import logging
import os
from statistics import mean
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger(__name__)


class USSocialSentimentClient:
    """Small client for fetching structured social sentiment snapshots."""

    PLATFORM_SPECS = {
        "reddit": {
            "path": "/reddit/stocks/v1/compare",
            "activity_field": "mentions",
            "activity_label": "Mentions",
            "label": "Reddit",
        },
        "x": {
            "path": "/x/stocks/v1/compare",
            "activity_field": "mentions",
            "activity_label": "Mentions",
            "label": "X.com",
        },
        "news": {
            "path": "/news/stocks/v1/compare",
            "activity_field": "mentions",
            "activity_label": "Mentions",
            "label": "News",
        },
        "polymarket": {
            "path": "/polymarket/stocks/v1/compare",
            "activity_field": "trade_count",
            "activity_label": "Trades",
            "label": "Polymarket",
        },
    }

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 10,
    ) -> None:
        self.api_key = api_key or os.getenv("ADANOS_API_KEY")
        self.base_url = (base_url or os.getenv("ADANOS_API_BASE_URL") or "https://api.adanos.org").rstrip("/")
        self.timeout = timeout

    @property
    def enabled(self) -> bool:
        """Return True when the client has an API key configured."""
        return bool(self.api_key)

    def get_social_sentiment_snapshot(self, ticker: str, days: int = 7) -> Optional[Dict[str, Any]]:
        """Fetch and aggregate social sentiment snapshot for a single ticker."""
        if not self.enabled:
            return None

        ticker = (ticker or "").strip().upper()
        if not ticker:
            return None

        sources: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=len(self.PLATFORM_SPECS)) as pool:
            futures = {
                platform: pool.submit(self._fetch_platform_snapshot, platform, ticker, days)
                for platform in self.PLATFORM_SPECS
            }
            for platform in self.PLATFORM_SPECS:
                parsed = futures[platform].result()
                if parsed:
                    sources[platform] = parsed

        if not sources:
            return None

        buzz_values = [item["buzz_score"] for item in sources.values() if item["buzz_score"] is not None]
        bullish_values = [item["bullish_pct"] for item in sources.values() if item["bullish_pct"] is not None]

        return {
            "ticker": ticker,
            "days": days,
            "average_buzz": round(mean(buzz_values), 1) if buzz_values else None,
            "bullish_avg": round(mean(bullish_values), 1) if bullish_values else None,
            "source_alignment": self._calculate_alignment(bullish_values),
            "coverage": len(sources),
            "sources": sources,
        }

    def render_snapshot(self, snapshot: Optional[Dict[str, Any]]) -> str:
        """Render a compact markdown snapshot for LLM consumption."""
        if not snapshot:
            return ""

        lines = [
            f"### Structured Social Sentiment Snapshot ({snapshot['days']}d)",
            "",
            f"- Average Buzz: {self._format_buzz(snapshot.get('average_buzz'))}",
            f"- Bullish Avg: {self._format_percent(snapshot.get('bullish_avg'))}",
            f"- Source Alignment: {snapshot.get('source_alignment', 'Insufficient data')}",
            f"- Coverage: {snapshot.get('coverage', 0)}/{len(self.PLATFORM_SPECS)} sources",
            "",
        ]

        for platform in self.PLATFORM_SPECS:
            source = snapshot["sources"].get(platform)
            if not source:
                continue

            lines.extend(
                [
                    f"#### {source['label']}",
                    f"- Buzz: {self._format_buzz(source.get('buzz_score'))}",
                    f"- Bullish: {self._format_percent(source.get('bullish_pct'))}",
                    f"- {source['activity_label']}: {self._format_count(source.get('activity_value'))}",
                    f"- Trend: {source.get('trend') or 'n/a'}",
                    "",
                ]
            )

        return "\n".join(lines).strip()

    def get_social_sentiment_markdown(self, ticker: str, days: int = 7) -> str:
        """Convenience helper returning markdown directly."""
        snapshot = self.get_social_sentiment_snapshot(ticker=ticker, days=days)
        return self.render_snapshot(snapshot)

    def _fetch_platform_snapshot(self, platform: str, ticker: str, days: int) -> Optional[Dict[str, Any]]:
        """Fetch and normalize one compare endpoint."""
        spec = self.PLATFORM_SPECS[platform]
        try:
            response = requests.get(
                f"{self.base_url}{spec['path']}",
                headers={"X-API-Key": self.api_key},
                params={"tickers": ticker, "days": days},
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover - exercised through tests via mocks
            logger.warning("Adanos %s compare fetch failed for %s: %s", platform, ticker, exc)
            return None

        row = self._extract_row(payload, ticker)
        if not row:
            return None

        buzz_score = self._coerce_float(row.get("buzz_score"))
        bullish_pct = self._coerce_float(row.get("bullish_pct"))
        activity_value = self._coerce_int(row.get(spec["activity_field"]))

        if buzz_score is None and bullish_pct is None and activity_value is None:
            return None

        return {
            "label": spec["label"],
            "buzz_score": buzz_score,
            "bullish_pct": bullish_pct,
            "trend": row.get("trend"),
            "activity_label": spec["activity_label"],
            "activity_value": activity_value,
        }

    @staticmethod
    def _extract_row(payload: Any, ticker: str) -> Optional[Dict[str, Any]]:
        """Extract a ticker row from compare payloads."""
        if isinstance(payload, list):
            candidates = payload
        elif isinstance(payload, dict):
            candidates = payload.get("stocks") or payload.get("data")
            if not isinstance(candidates, list):
                candidates = [payload]
        else:
            return None

        ticker_upper = ticker.upper()
        for item in candidates:
            if isinstance(item, dict) and str(item.get("ticker", "")).upper() == ticker_upper:
                return item
        return None

    @staticmethod
    def _coerce_float(value: Any) -> Optional[float]:
        """Parse float-like values safely."""
        if value is None or value == "":
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        """Parse int-like values safely."""
        if value is None or value == "":
            return None
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _calculate_alignment(values: list[float]) -> str:
        """Convert bullish spread into a simple alignment label."""
        if len(values) < 2:
            return "Insufficient data"

        spread = max(values) - min(values)
        if spread <= 12:
            return "Strong alignment"
        if spread <= 25:
            return "Mixed"
        return "Wide divergence"

    @staticmethod
    def _format_buzz(value: Optional[float]) -> str:
        """Format buzz score consistently."""
        if value is None:
            return "n/a"
        return f"{value:.1f}/100"

    @staticmethod
    def _format_percent(value: Optional[float]) -> str:
        """Format percentage values consistently."""
        if value is None:
            return "n/a"
        return f"{value:.1f}%"

    @staticmethod
    def _format_count(value: Optional[int]) -> str:
        """Format count-like values consistently."""
        if value is None:
            return "n/a"
        return f"{value:,}"
