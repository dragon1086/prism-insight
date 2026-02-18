"""
GCP Pub/Sub Signal Publisher

Module for publishing PRISM-INSIGHT buy/sell signals to Google Cloud Pub/Sub.
Subscribers can receive real-time trading signals by subscribing to this topic.

Usage:
    from messaging.gcp_pubsub_signal_publisher import SignalPublisher

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
from pathlib import Path

# Load .env file
try:
    from dotenv import load_dotenv
    project_root = Path(__file__).parent.parent
    env_path = project_root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
except ImportError:
    pass

logger = logging.getLogger(__name__)


class SignalPublisher:
    """GCP Pub/Sub-based trading signal publisher"""

    def __init__(
        self,
        project_id: Optional[str] = None,
        topic_id: Optional[str] = None,
        credentials_path: Optional[str] = None
    ):
        """
        Initialize SignalPublisher

        Args:
            project_id: GCP Project ID
            topic_id: Pub/Sub Topic ID
            credentials_path: Path to service account JSON key file
        """
        self.project_id = project_id or os.environ.get("GCP_PROJECT_ID")
        self.topic_id = topic_id or os.environ.get("GCP_PUBSUB_TOPIC_ID", "prism-trading-signals")
        self.credentials_path = credentials_path or os.environ.get("GCP_CREDENTIALS_PATH")
        self._publisher = None
        self._topic_path = None

        if not self.project_id:
            logger.warning(
                "GCP Project ID not configured. "
                "Set GCP_PROJECT_ID environment variable."
            )

    async def __aenter__(self):
        """Async context manager entry"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.disconnect()

    async def connect(self):
        """Connect to GCP Pub/Sub"""
        if not self.project_id:
            logger.warning("GCP not configured, signals will not be published")
            return

        try:
            from google.cloud import pubsub_v1

            # Use explicit credentials object for reliability
            if self.credentials_path:
                if not os.path.exists(self.credentials_path):
                    logger.error(
                        f"GCP credentials file not found: {self.credentials_path}. "
                        "Check GCP_CREDENTIALS_PATH in .env"
                    )
                    return
                from google.oauth2 import service_account
                credentials = service_account.Credentials.from_service_account_file(
                    self.credentials_path,
                    scopes=["https://www.googleapis.com/auth/pubsub"]
                )
                self._publisher = pubsub_v1.PublisherClient(credentials=credentials)
            else:
                # Fall back to application default credentials
                self._publisher = pubsub_v1.PublisherClient()

            self._topic_path = self._publisher.topic_path(self.project_id, self.topic_id)
            logger.info(f"GCP Pub/Sub connected: {self._topic_path}")
        except ImportError:
            logger.warning(
                "google-cloud-pubsub package not installed. "
                "Install with: pip install google-cloud-pubsub"
            )
        except Exception as e:
            logger.error(f"GCP Pub/Sub connection failed: {str(e)}")
            self._publisher = None

    async def disconnect(self):
        """Disconnect from GCP Pub/Sub"""
        self._publisher = None
        self._topic_path = None
        logger.info("GCP Pub/Sub disconnected")

    def _is_connected(self) -> bool:
        """Check connection status"""
        return self._publisher is not None

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
            logger.debug(f"GCP Pub/Sub not connected, skipping signal publish: {signal_type} {ticker}")
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

            # Publish to GCP Pub/Sub
            message_json = json.dumps(signal_data, ensure_ascii=False)
            message_bytes = message_json.encode("utf-8")

            future = self._publisher.publish(self._topic_path, message_bytes)
            message_id = future.result()

            logger.info(
                f"Signal published: {signal_type} {company_name}({ticker}) "
                f"@ {price:,.0f} KRW [ID: {message_id}]"
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
