"""
Redis Streams Signal Publisher

Module for publishing PRISM-INSIGHT buy/sell signals to Redis Streams.
Subscribers can receive real-time trading signals by subscribing to this stream.

Usage:
    from messaging.redis_signal_publisher import SignalPublisher

    async with SignalPublisher() as publisher:
        await publisher.publish_buy_signal(
            ticker="005930",
            company_name="Samsung Electronics",
            price=82000,
            scenario=scenario_dict
        )
"""
import os
import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager
from pathlib import Path

# Load .env file
try:
    from dotenv import load_dotenv
    # Find project root (parent of messaging folder)
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass  # If dotenv is not available, read from environment variables directly

logger = logging.getLogger(__name__)


class SignalPublisher:
    """Redis Streams-based trading signal publisher"""

    # Stream name
    STREAM_NAME = "prism:trading-signals"

    def __init__(
        self,
        redis_url: Optional[str] = None,
        redis_token: Optional[str] = None
    ):
        """
        Initialize SignalPublisher

        Args:
            redis_url: Upstash Redis REST URL (reads from environment if not provided)
            redis_token: Upstash Redis REST Token (reads from environment if not provided)
        """
        self.redis_url = redis_url or os.environ.get("UPSTASH_REDIS_REST_URL")
        self.redis_token = redis_token or os.environ.get("UPSTASH_REDIS_REST_TOKEN")
        self._redis = None

        if not self.redis_url or not self.redis_token:
            logger.warning(
                "Redis credentials not configured. "
                "Set UPSTASH_REDIS_REST_URL and UPSTASH_REDIS_REST_TOKEN environment variables."
            )

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.disconnect()

    async def connect(self):
        """Connect to Redis"""
        if not self.redis_url or not self.redis_token:
            logger.warning("Redis not configured, signals will not be published")
            return

        try:
            from upstash_redis import Redis
            self._redis = Redis(url=self.redis_url, token=self.redis_token)
            logger.info(f"Redis connected: {self.redis_url[:30]}...")
        except ImportError:
            logger.warning(
                "upstash-redis package not installed. "
                "Install with: pip install upstash-redis"
            )
        except Exception as e:
            logger.error(f"Redis connection failed: {str(e)}")
            self._redis = None

    async def disconnect(self):
        """Disconnect from Redis"""
        # upstash-redis is HTTP-based, no explicit disconnect needed
        self._redis = None
        logger.info("Redis disconnected")

    def _is_connected(self) -> bool:
        """Check connection status"""
        return self._redis is not None

    async def publish_signal(
        self,
        signal_type: str,
        ticker: str,
        company_name: str,
        price: float,
        source: str = "AI Analysis",
        scenario: Optional[Dict[str, Any]] = None,
        extra_data: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Publish trading signal

        Args:
            signal_type: Signal type ("BUY", "SELL", "EVENT", etc.)
            ticker: Stock ticker
            company_name: Company name
            price: Current/buy/sell price
            source: Signal source (default: "AI Analysis")
            scenario: Trading scenario information
            extra_data: Additional data

        Returns:
            str: Message ID (None on failure)
        """
        if not self._is_connected():
            logger.debug(f"Redis not connected, skipping signal publish: {signal_type} {ticker}")
            return None

        try:
            # Build signal data
            signal_data = {
                "type": signal_type,
                "ticker": ticker,
                "company_name": company_name,
                "price": price,
                "source": source,
                "timestamp": datetime.now().isoformat(),
            }

            # Add scenario information (key fields only)
            if scenario:
                signal_data["target_price"] = scenario.get("target_price", 0)
                signal_data["stop_loss"] = scenario.get("stop_loss", 0)
                signal_data["investment_period"] = scenario.get("investment_period", "")
                signal_data["sector"] = scenario.get("sector", "")
                signal_data["rationale"] = scenario.get("rationale", "")
                signal_data["buy_score"] = scenario.get("buy_score", 0)

            # Merge additional data
            if extra_data:
                signal_data.update(extra_data)

            # Publish to Redis Streams (XADD)
            # upstash-redis 1.5.0+ signature: xadd(key, id, data)
            # Use id="*" for auto-generated ID
            message_id = self._redis.xadd(
                self.STREAM_NAME,
                "*",  # auto-generate message ID
                {"data": json.dumps(signal_data, ensure_ascii=False)}
            )

            market = (extra_data or {}).get("market", "KR")
            currency = "USD" if market == "US" else "KRW"
            logger.info(
                f"Signal published: {signal_type} {company_name}({ticker}) "
                f"@ {price:,.2f} {currency} [ID: {message_id}]"
            )
            return message_id

        except Exception as e:
            logger.error(f"Signal publish failed: {str(e)}")
            return None

    async def publish_buy_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        scenario: Optional[Dict[str, Any]] = None,
        source: str = "AI Analysis",
        trade_result: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Publish buy signal

        Args:
            ticker: Stock ticker
            company_name: Company name
            price: Buy price
            scenario: Trading scenario
            source: Signal source
            trade_result: Actual trade result (success status, etc.)

        Returns:
            str: Message ID
        """
        extra_data = {}
        if trade_result:
            extra_data["trade_success"] = trade_result.get("success", False)
            extra_data["trade_message"] = trade_result.get("message", "")

        return await self.publish_signal(
            signal_type="BUY",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source=source,
            scenario=scenario,
            extra_data=extra_data
        )

    async def publish_sell_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        buy_price: float,
        profit_rate: float,
        sell_reason: str,
        trade_result: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Publish sell signal

        Args:
            ticker: Stock ticker
            company_name: Company name
            price: Sell price
            buy_price: Buy price
            profit_rate: Profit rate
            sell_reason: Sell reason
            trade_result: Actual trade result

        Returns:
            str: Message ID
        """
        extra_data = {
            "buy_price": buy_price,
            "profit_rate": profit_rate,
            "sell_reason": sell_reason,
        }

        if trade_result:
            extra_data["trade_success"] = trade_result.get("success", False)
            extra_data["trade_message"] = trade_result.get("message", "")

        return await self.publish_signal(
            signal_type="SELL",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source="AI Analysis",
            extra_data=extra_data
        )

    async def publish_event_signal(
        self,
        ticker: str,
        company_name: str,
        price: float,
        event_type: str,
        event_source: str,
        event_description: str
    ) -> Optional[str]:
        """
        Publish event-based signal (YouTuber video, news, etc.)

        Args:
            ticker: Stock ticker
            company_name: Company name
            price: Current price
            event_type: Event type (e.g., "YOUTUBE", "NEWS", "DISCLOSURE")
            event_source: Event source (e.g., YouTuber name, news outlet)
            event_description: Event description

        Returns:
            str: Message ID
        """
        return await self.publish_signal(
            signal_type="EVENT",
            ticker=ticker,
            company_name=company_name,
            price=price,
            source=event_source,
            extra_data={
                "event_type": event_type,
                "event_description": event_description
            }
        )


# Global instance for convenience (optional usage)
_global_publisher: Optional[SignalPublisher] = None


async def get_signal_publisher() -> SignalPublisher:
    """Return global SignalPublisher instance"""
    global _global_publisher
    if _global_publisher is None:
        _global_publisher = SignalPublisher()
        await _global_publisher.connect()
    return _global_publisher


async def publish_buy_signal(
    ticker: str,
    company_name: str,
    price: float,
    scenario: Optional[Dict[str, Any]] = None,
    source: str = "AI Analysis",
    trade_result: Optional[Dict[str, Any]] = None,
    market: str = "KR"
) -> Optional[str]:
    """Publish buy signal via global publisher (convenience function)

    Args:
        market: Market identifier ("KR" for Korea, "US" for US stocks)
    """
    publisher = await get_signal_publisher()
    # Include market in scenario for signal data
    enriched_scenario = dict(scenario) if scenario else {}
    enriched_scenario["market"] = market
    return await publisher.publish_buy_signal(
        ticker=ticker,
        company_name=company_name,
        price=price,
        scenario=enriched_scenario,
        source=source,
        trade_result=trade_result
    )


async def publish_sell_signal(
    ticker: str,
    company_name: str,
    price: float,
    buy_price: float,
    profit_rate: float,
    sell_reason: str,
    trade_result: Optional[Dict[str, Any]] = None,
    market: str = "KR"
) -> Optional[str]:
    """Publish sell signal via global publisher (convenience function)

    Args:
        market: Market identifier ("KR" for Korea, "US" for US stocks)
    """
    publisher = await get_signal_publisher()
    # Include market in extra_data by passing through the publish_signal method
    # For sell signals, we need to modify the publisher method or use publish_signal directly
    extra_data = {
        "buy_price": buy_price,
        "profit_rate": profit_rate,
        "sell_reason": sell_reason,
        "market": market,
    }

    if trade_result:
        extra_data["trade_success"] = trade_result.get("success", False)
        extra_data["trade_message"] = trade_result.get("message", "")

    return await publisher.publish_signal(
        signal_type="SELL",
        ticker=ticker,
        company_name=company_name,
        price=price,
        source="AI Analysis",
        extra_data=extra_data
    )
