#!/usr/bin/env python3
"""
PRISM-INSIGHT Trading Signal Subscriber (Auto-Trading Integration)

Running this script will receive buy/sell signals published by PRISM-INSIGHT
in real-time and execute actual auto-trading.

Usage:
    1. Install upstash-redis package
       pip install upstash-redis

    2. Configure .env file (or pass via environment variables/options)
       UPSTASH_REDIS_REST_URL=https://topical-lemur-7683.upstash.io
       UPSTASH_REDIS_REST_TOKEN=your-token-here

    3. Run script
       python examples/messaging/redis_subscriber_example.py

Options:
    --from-beginning: Receive all messages from the start (default: new messages only)
    --log-file: Specify log file path (default: logs/subscriber_YYYYMMDD.log)
    --dry-run: Run simulation only without actual trading
    --polling-interval: Polling interval in seconds (default: 5)
"""
import os
import sys
import json
import time
import logging
import argparse
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

# Project root path (parent of parent of examples/messaging folder)
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass  # If dotenv is not available, read from environment variables directly


def parse_stream_data(data: Any) -> Dict[str, Any]:
    """
    Parse stream data from upstash-redis 1.5.0+.

    upstash-redis returns Redis responses in list format:
    - Input: ['field1', 'value1', 'field2', 'value2', ...]
    - Output: {'field1': 'value1', 'field2': 'value2', ...}
    """
    if isinstance(data, dict):
        return data
    elif isinstance(data, list):
        # Convert list to dictionary (key-value pairs)
        return {data[i]: data[i+1] for i in range(0, len(data), 2)}
    return data


def setup_logging(log_file: str = None) -> logging.Logger:
    """Configure logging"""
    # Create log directory
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    # Determine log file path
    if log_file:
        log_path = Path(log_file)
    else:
        log_path = log_dir / f"subscriber_{datetime.now().strftime('%Y%m%d')}.log"

    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding='utf-8')
        ]
    )

    logger = logging.getLogger("subscriber")
    logger.info(f"Log file: {log_path}")

    return logger


async def execute_buy_trade(ticker: str, company_name: str, logger: logging.Logger,
                            position_fraction: float = 1.0) -> Dict[str, Any]:
    """
    Execute actual buy order (async)

    Args:
        ticker: Stock ticker
        company_name: Company name
        logger: Logger

    Returns:
        Trade result dictionary
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            # Micro-split / pilot entries carry position_fraction < 1.0: buy that share of one slot.
            buy_amount = int(trading.buy_amount * position_fraction) if 0 < position_fraction < 1 else None
            trade_result = await trading.async_buy_stock(stock_code=ticker, buy_amount=buy_amount)

        if trade_result['success']:
            logger.info(f"✅ Actual buy successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ Actual buy failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during buy execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


async def execute_sell_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """
    Execute actual sell order (async)

    Args:
        ticker: Stock ticker
        company_name: Company name
        logger: Logger

    Returns:
        Trade result dictionary
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_sell_stock(stock_code=ticker)

        if trade_result['success']:
            logger.info(f"✅ Actual sell successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ Actual sell failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during sell execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="PRISM-INSIGHT Trading Signal Subscriber (Auto-Trading Integration)")
    parser.add_argument(
        "--from-beginning",
        action="store_true",
        help="Receive all messages from the start (default: new messages only)"
    )
    parser.add_argument(
        "--redis-url",
        default=os.environ.get("UPSTASH_REDIS_REST_URL"),
        help="Upstash Redis REST URL"
    )
    parser.add_argument(
        "--redis-token",
        default=os.environ.get("UPSTASH_REDIS_REST_TOKEN"),
        help="Upstash Redis REST Token"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path (default: logs/subscriber_YYYYMMDD.log)"
    )
    parser.add_argument(
        "--polling-interval",
        type=int,
        default=5,
        help="Polling interval in seconds (default: 5)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run simulation only without actual trading (default: actual trading)"
    )
    args = parser.parse_args()

    # Configure logging
    logger = setup_logging(args.log_file)

    # Display mode
    if args.dry_run:
        logger.warning("🔸 DRY-RUN mode: No actual trading will be executed.")
    else:
        logger.info("🔹 LIVE mode: Actual trading will be executed!")

    # Check Redis connection info
    if not args.redis_url or not args.redis_token:
        logger.error("Redis connection information is missing.")
        logger.error("Set environment variables or use --redis-url, --redis-token options.")
        logger.error('Example: export UPSTASH_REDIS_REST_URL="https://xxx.upstash.io"')
        logger.error('         export UPSTASH_REDIS_REST_TOKEN="your-token"')
        return

    try:
        from upstash_redis import Redis
    except ImportError:
        logger.error("upstash-redis package not installed.")
        logger.error("Install with: pip install upstash-redis")
        return

    # Connect to Redis
    logger.info("Connecting to Redis...")
    redis = Redis(url=args.redis_url, token=args.redis_token)

    # Stream name
    stream_name = "prism:trading-signals"

    # Determine starting ID
    if args.from_beginning:
        last_id = "0"  # From the beginning
        logger.info("Receiving all messages from the start.")
    else:
        # Receive new messages only: Get the last ID from the current stream
        # NOTE: Upstash may not properly support the "$" special ID
        try:
            last_entries = redis.xrevrange(stream_name, count=1)
            if last_entries:
                last_id = last_entries[0][0]
                logger.info(f"Starting from last message ID: {last_id}")
            else:
                last_id = "0"
                logger.info("Stream is empty. Starting from the beginning.")
        except Exception as e:
            last_id = "0"
            logger.warning(f"Failed to query last ID, starting from beginning: {e}")

        logger.info("Receiving only new incoming messages.")

    logger.info(f"Stream subscription started: {stream_name}")
    logger.info(f"Polling interval: {args.polling_interval} seconds")
    logger.info("=" * 60)

    # Signal handler function
    def handle_signal(signal: dict):
        """Function to process received signals"""
        signal_type = signal.get("type", "UNKNOWN")
        ticker = signal.get("ticker", "")
        company_name = signal.get("company_name", "")
        price = signal.get("price", 0)
        timestamp = signal.get("timestamp", "")

        # Emoji by signal type (for logging)
        emoji = {
            "BUY": "📈",
            "SELL": "📉",
            "EVENT": "🔔"
        }.get(signal_type, "📌")

        # Log basic signal info
        logger.info(f"{emoji} [{signal_type}] {company_name}({ticker}) @ {price:,.0f} KRW")

        # If buy signal
        if signal_type == "BUY":
            target = signal.get("target_price", 0)
            stop_loss = signal.get("stop_loss", 0)
            rationale = signal.get("rationale", "")
            buy_score = signal.get("buy_score", 0)

            details = []
            if target:
                details.append(f"Target: {target:,.0f} KRW")
            if stop_loss:
                details.append(f"Stop-loss: {stop_loss:,.0f} KRW")
            if buy_score:
                details.append(f"Buy score: {buy_score}")
            if rationale:
                details.append(f"Rationale: {rationale[:100]}...")

            if details:
                logger.info(f"   -> {' | '.join(details)}")

            # Execute actual buy
            if not args.dry_run:
                logger.info(f"🚀 Executing buy order: {company_name}({ticker})")
                try:
                    fraction = float(signal.get("position_fraction", 1) or 1)
                except (TypeError, ValueError):
                    fraction = 1.0
                trade_result = asyncio.run(execute_buy_trade(ticker, company_name, logger, position_fraction=fraction))
            else:
                logger.info(f"🔸 [DRY-RUN] Buy skipped: {company_name}({ticker})")

        # If sell signal
        elif signal_type == "SELL":
            profit_rate = signal.get("profit_rate", 0)
            sell_reason = signal.get("sell_reason", "")
            buy_price = signal.get("buy_price", 0)

            details = []
            if buy_price:
                details.append(f"Buy price: {buy_price:,.0f} KRW")
            details.append(f"Profit rate: {profit_rate:+.2f}%")
            if sell_reason:
                details.append(f"Sell reason: {sell_reason}")

            logger.info(f"   -> {' | '.join(details)}")

            # Execute actual sell
            if not args.dry_run:
                logger.info(f"🚀 Executing sell order: {company_name}({ticker})")
                trade_result = asyncio.run(execute_sell_trade(ticker, company_name, logger))
            else:
                logger.info(f"🔸 [DRY-RUN] Sell skipped: {company_name}({ticker})")

        # If event signal
        elif signal_type == "EVENT":
            event_type = signal.get("event_type", "")
            event_source = signal.get("source", "")
            event_description = signal.get("event_description", "")

            details = []
            if event_type:
                details.append(f"Event: {event_type}")
            if event_source:
                details.append(f"Source: {event_source}")
            if event_description:
                details.append(f"Description: {event_description[:100]}")

            if details:
                logger.info(f"   -> {' | '.join(details)}")

        # Log original JSON at DEBUG level
        logger.debug(f"Original signal: {json.dumps(signal, ensure_ascii=False)}")

    # Main loop
    # NOTE: Upstash Redis is HTTP-based and doesn't support block parameter
    # Therefore implementing with polling approach
    polling_interval = args.polling_interval
    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}

    try:
        while True:
            try:
                # Read new messages with XREAD
                # Upstash doesn't support block, so using polling approach
                result = redis.xread({stream_name: last_id}, count=10)

                if result:
                    for stream, messages in result:
                        for msg_id, data in messages:
                            # Parse message (upstash-redis returns in list format)
                            parsed_data = parse_stream_data(data)
                            raw_data = parsed_data.get("data")
                            if raw_data:
                                if isinstance(raw_data, bytes):
                                    raw_data = raw_data.decode("utf-8")
                                signal = json.loads(raw_data)
                                handle_signal(signal)
                                message_count += 1

                                # Trade count
                                signal_type = signal.get("type", "")
                                if signal_type in trade_count:
                                    trade_count[signal_type] += 1

                            # Update ID for next message
                            last_id = msg_id
                else:
                    # Wait for polling interval if no new messages
                    time.sleep(polling_interval)

            except Exception as e:
                logger.error(f"Error occurred: {e}", exc_info=True)
                time.sleep(polling_interval)  # Wait and retry on error

    except KeyboardInterrupt:
        logger.info("=" * 60)
        logger.info(f"Subscription ended.")
        logger.info(f"Total {message_count} signals received (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
