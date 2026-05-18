#!/usr/bin/env python3
"""
PRISM-INSIGHT Trading Signal Subscriber (GCP Pub/Sub Auto-Trading Integration)

Running this script will receive buy/sell signals published by PRISM-INSIGHT
in real-time via GCP Pub/Sub and execute actual auto-trading.

Supported Markets:
    - KR (Korea): 09:00-15:30 KST, using domestic_stock_trading module
    - US (USA): 09:30-16:00 EST, using us_stock_trading module

Usage:
    1. Install google-cloud-pubsub package
       pip install google-cloud-pubsub

    2. Configure .env file (or pass via environment variables/options)
       GCP_PROJECT_ID=your-project-id
       GCP_PUBSUB_SUBSCRIPTION_ID=prism-trading-signals-sub
       GCP_CREDENTIALS_PATH=/path/to/service-account-key.json

    3. Run script
       python examples/messaging/gcp_pubsub_subscriber_example.py

Options:
    --log-file: Specify log file path (default: logs/subscriber_YYYYMMDD.log)
    --dry-run: Run simulation only without actual trading

Note:
    In demo mode, if signals arrive during off-market hours,
    market orders will be automatically executed at the next trading day's market open:
    - KR: Next trading day 09:05 KST
    - US: Next trading day 09:35 EST (23:35 KST)
"""
import os
import sys
import json
import logging
import argparse
import asyncio
import threading
import importlib.util
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# Project root path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Load .env file
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass


# ============================================================
# Scheduling Utilities
# ============================================================

def get_trading_mode() -> str:
    """Check trading mode from kis_devlp.yaml (demo/real)"""
    try:
        import yaml
        config_path = PROJECT_ROOT / "trading" / "config" / "kis_devlp.yaml"
        with open(config_path, encoding="UTF-8") as f:
            cfg = yaml.load(f, Loader=yaml.FullLoader)
        return cfg.get("default_mode", "real")
    except Exception:
        return "real"


def is_market_hours(market: str = "KR") -> bool:
    """
    Check if current time is during regular market hours

    Args:
        market: "KR" (Korea) or "US" (USA)

    Returns:
        bool: Whether market is open
    """
    if market == "US":
        return is_us_market_hours()

    # Korea: 09:00~15:30 KST
    now = datetime.now().time()
    market_open = time(9, 0)
    market_close = time(15, 30)
    return market_open <= now <= market_close


def is_us_market_hours() -> bool:
    """Check if current time is during US market hours (09:30~16:00 EST, trading days only)"""
    try:
        # Use prism-us/check_market_day.py (NYSE calendar based)
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "prism-us"))
        from check_market_day import is_market_open
        return is_market_open()
    except ImportError:
        # fallback: calculate directly with pytz
        try:
            import pytz
            us_eastern = pytz.timezone('US/Eastern')
            now_est = datetime.now(us_eastern)

            # Check weekend (EST timezone)
            if now_est.weekday() >= 5:
                return False

            current_time = now_est.time()
            market_open = time(9, 30)
            market_close = time(16, 0)
            return market_open <= current_time <= market_close
        except ImportError:
            # If pytz is also unavailable, approximate calculation based on KST
            now = datetime.now()
            if now.weekday() >= 5:  # Weekend
                return False
            now_time = now.time()
            return now_time >= time(23, 30) or now_time <= time(6, 0)


def is_market_day_check() -> bool:
    """Check if it's a trading day (using check_market_day.py)"""
    try:
        from check_market_day import is_market_day
        return is_market_day()
    except ImportError:
        # fallback: only check weekends
        return datetime.now().weekday() < 5


def get_next_market_open(market: str = "KR") -> datetime:
    """
    Calculate next trading day's market open time

    Args:
        market: "KR" (Korea) or "US" (USA)

    Returns:
        datetime: Next trading day's market open time
    """
    if market == "US":
        return get_next_us_market_open()

    # Korea: Next trading day 09:05 KST
    now = datetime.now()
    next_day = now + timedelta(days=1)

    # Find next trading day (search up to 7 days)
    for _ in range(7):
        # Skip weekends
        if next_day.weekday() >= 5:
            next_day += timedelta(days=1)
            continue

        # Check holidays (using is_market_day_check)
        try:
            from check_market_day import is_market_day
            from holidays.countries import KR

            # Check if the date is a trading day
            kr_holidays = KR()
            if next_day.date() in kr_holidays:
                next_day += timedelta(days=1)
                continue
            # Check Labor Day
            if next_day.month == 5 and next_day.day == 1:
                next_day += timedelta(days=1)
                continue
        except ImportError:
            pass

        # Found trading day
        break

    # Set to 09:05 (stabilization time after market open)
    return next_day.replace(hour=9, minute=5, second=0, microsecond=0)


def get_next_us_market_open() -> datetime:
    """
    Calculate next US trading day's market open time (09:35 EST -> convert to KST)

    Returns:
        datetime: Next US trading day's market open time (KST)
    """
    try:
        # Use prism-us/check_market_day.py (NYSE calendar based)
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "prism-us"))
        from check_market_day import get_next_trading_day, EST, KST

        next_trading_day = get_next_trading_day()
        if next_trading_day:
            import pytz
            # Set to 09:35 EST (stabilization time after market open)
            market_open_est = datetime.combine(next_trading_day, time(9, 35))
            market_open_est = EST.localize(market_open_est)

            # Convert to KST
            market_open_kst = market_open_est.astimezone(KST)
            return market_open_kst.replace(tzinfo=None)  # Return naive datetime

    except ImportError:
        pass

    # fallback: calculate directly with pytz
    try:
        import pytz
        us_eastern = pytz.timezone('US/Eastern')
        kst = pytz.timezone('Asia/Seoul')

        now_est = datetime.now(us_eastern)
        next_day_est = now_est + timedelta(days=1)

        # Find next trading day (search up to 7 days)
        for _ in range(7):
            if next_day_est.weekday() >= 5:
                next_day_est += timedelta(days=1)
                continue
            break

        # Set to 09:35 EST
        market_open_est = next_day_est.replace(hour=9, minute=35, second=0, microsecond=0)
        market_open_kst = market_open_est.astimezone(kst)
        return market_open_kst.replace(tzinfo=None)

    except ImportError:
        # If pytz is also unavailable, approximate calculation (EST + 14 hours = KST)
        now = datetime.now()
        next_day = now + timedelta(days=1)

        for _ in range(7):
            if next_day.weekday() >= 5:
                next_day += timedelta(days=1)
                continue
            break

        # 23:35 KST (next day 09:35 EST)
        return next_day.replace(hour=23, minute=35, second=0, microsecond=0)


class ScheduledOrderManager:
    """
    Scheduled Order Manager

    Stores signals received during off-market hours in demo mode
    and executes them automatically at the next trading day's market open.
    """

    def __init__(self, storage_path: Path = None, logger: logging.Logger = None):
        self.storage_path = storage_path or (PROJECT_ROOT / "logs" / "scheduled_orders.json")
        self.logger = logger or logging.getLogger("scheduled_orders")
        self.orders: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # Load saved scheduled orders
        self._load_orders()

    def _load_orders(self):
        """Load scheduled orders from file"""
        try:
            if self.storage_path.exists():
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    self.orders = json.load(f)
                if self.orders:
                    self.logger.info(f"📋 Loaded {len(self.orders)} scheduled orders")
        except Exception as e:
            self.logger.error(f"Failed to load scheduled orders: {e}")
            self.orders = []

    def _save_orders(self):
        """Save scheduled orders to file"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(self.orders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Failed to save scheduled orders: {e}")

    def add_order(self, signal: Dict[str, Any], signal_type: str = "BUY", market: str = "KR") -> bool:
        """
        Add scheduled order

        Args:
            signal: Signal data dictionary
            signal_type: "BUY" or "SELL" (default: "BUY" - backward compatibility)
            market: "KR" or "US" (default: "KR" - backward compatibility)
        """
        with self._lock:
            order = {
                "signal": signal,
                "signal_type": signal_type,
                "market": market,
                "scheduled_at": datetime.now().isoformat(),
                "execute_after": get_next_market_open(market).isoformat(),
                "status": "pending"
            }
            self.orders.append(order)
            self._save_orders()

            ticker = signal.get("ticker", "")
            company_name = signal.get("company_name", "")
            execute_time = get_next_market_open(market).strftime("%Y-%m-%d %H:%M")

            action_type = "BUY" if signal_type == "BUY" else "SELL"
            market_label = "🇺🇸 US" if market == "US" else "🇰🇷 KR"
            self.logger.info(f"⏰ Scheduled order registered: [{market_label}] {company_name}({ticker}) [{action_type}] -> scheduled for {execute_time}")
            return True

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """Get pending orders"""
        with self._lock:
            now = datetime.now()
            pending = []
            for order in self.orders:
                if order["status"] == "pending":
                    execute_after = datetime.fromisoformat(order["execute_after"])
                    if now >= execute_after:
                        pending.append(order)
            return pending

    def mark_executed(self, order: Dict[str, Any], success: bool, message: str = ""):
        """Mark order as executed"""
        with self._lock:
            order["status"] = "executed" if success else "failed"
            order["executed_at"] = datetime.now().isoformat()
            order["result_message"] = message
            self._save_orders()

    def clear_old_orders(self, days: int = 7):
        """Clean up old orders"""
        with self._lock:
            cutoff = datetime.now() - timedelta(days=days)
            original_count = len(self.orders)
            self.orders = [
                o for o in self.orders
                if o["status"] == "pending" or
                   datetime.fromisoformat(o.get("executed_at", o["scheduled_at"])) > cutoff
            ]
            removed = original_count - len(self.orders)
            if removed > 0:
                self._save_orders()
                self.logger.info(f"🗑️ Cleaned up {removed} old orders")

    def start_scheduler(self, execute_callback):
        """Start background scheduler"""
        def scheduler_loop():
            self.logger.info("🕐 Scheduled order scheduler started (KR/US markets supported)")
            while not self._stop_event.is_set():
                try:
                    # Check every minute
                    if self._stop_event.wait(60):
                        break

                    # Get pending orders
                    pending_orders = self.get_pending_orders()
                    for order in pending_orders:
                        signal = order["signal"]
                        signal_type = order.get("signal_type", "BUY")
                        market = order.get("market", "KR")
                        ticker = signal.get("ticker", "")
                        company_name = signal.get("company_name", "")

                        # Check if market is open for this market
                        if not is_market_hours(market):
                            continue

                        action_type = "BUY" if signal_type == "BUY" else "SELL"
                        market_label = "🇺🇸 US" if market == "US" else "🇰🇷 KR"
                        self.logger.info(f"🚀 Executing scheduled order: [{market_label}] {company_name}({ticker}) [{action_type}]")

                        try:
                            result = execute_callback(order)
                            success = result.get("success", False)
                            message = result.get("message", "")
                            self.mark_executed(order, success, message)

                            if success:
                                self.logger.info(f"✅ Scheduled order succeeded: [{market_label}] {company_name}({ticker})")
                            else:
                                self.logger.error(f"❌ Scheduled order failed: [{market_label}] {company_name}({ticker}) - {message}")
                        except Exception as e:
                            self.mark_executed(order, False, str(e))
                            self.logger.error(f"❌ Scheduled order execution error: {e}")

                    # Clean up old orders daily at midnight
                    if datetime.now().hour == 0 and datetime.now().minute < 2:
                        self.clear_old_orders()

                except Exception as e:
                    self.logger.error(f"Scheduler error: {e}")

            self.logger.info("🕐 Scheduled order scheduler stopped")

        self._scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self):
        """Stop scheduler"""
        self._stop_event.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)


# Global scheduler manager (will be initialized in main)
scheduled_order_manager: Optional[ScheduledOrderManager] = None


def load_us_stock_trading_class():
    """Load prism-us trading module without colliding with root trading package."""
    module_path = PROJECT_ROOT / "prism-us" / "trading" / "us_stock_trading.py"
    spec = importlib.util.spec_from_file_location("prism_us_stock_trading", module_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load US trading module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.USStockTrading


def setup_logging(log_file: str = None) -> logging.Logger:
    """Configure logging"""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)

    if log_file:
        log_path = Path(log_file)
    else:
        log_path = log_dir / f"subscriber_{datetime.now().strftime('%Y%m%d')}.log"

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


async def execute_buy_trade(ticker: str, company_name: str, logger: logging.Logger, limit_price: Optional[int] = None) -> Dict[str, Any]:
    """Execute actual buy order (async)

    Args:
        ticker: Stock code
        company_name: Company name for logging
        logger: Logger instance
        limit_price: Limit price for reserved orders (required for off-hours trading)
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            # Get current price for limit_price if not provided (needed for reserved orders)
            effective_limit_price = limit_price
            if not effective_limit_price:
                price_info = trading.get_current_price(ticker)
                if price_info:
                    effective_limit_price = int(price_info['current_price'])

            trade_result = await trading.async_buy_stock(stock_code=ticker, limit_price=effective_limit_price)

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


async def execute_sell_trade(ticker: str, company_name: str, logger: logging.Logger, limit_price: Optional[int] = None) -> Dict[str, Any]:
    """Execute actual sell order (async)

    Args:
        ticker: Stock code
        company_name: Company name for logging
        logger: Logger instance
        limit_price: Limit price for reserved orders (required for off-hours trading)
    """
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            # Get current price for limit_price if not provided (needed for reserved orders)
            effective_limit_price = limit_price
            if not effective_limit_price:
                price_info = trading.get_current_price(ticker)
                if price_info:
                    effective_limit_price = int(price_info['current_price'])

            trade_result = await trading.async_sell_stock(stock_code=ticker, limit_price=effective_limit_price)

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


async def execute_us_buy_trade(ticker: str, company_name: str, logger: logging.Logger, limit_price: Optional[float] = None) -> Dict[str, Any]:
    """Execute actual US stock buy order (async)

    Args:
        ticker: Stock ticker symbol
        company_name: Company name for logging
        logger: Logger instance
        limit_price: Limit price in USD for reserved orders (required for off-hours trading)
    """
    try:
        USStockTrading = load_us_stock_trading_class()

        trading = USStockTrading()

        # Get current price for limit_price if not provided (needed for reserved orders)
        effective_limit_price = limit_price
        if not effective_limit_price:
            price_info = trading.get_current_price(ticker)
            if price_info:
                effective_limit_price = price_info['current_price']

        trade_result = await trading.async_buy_stock(ticker=ticker, limit_price=effective_limit_price)

        if trade_result['success']:
            logger.info(f"✅ 🇺🇸 US buy successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ 🇺🇸 US buy failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"US Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during US buy execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


async def execute_us_sell_trade(ticker: str, company_name: str, logger: logging.Logger, limit_price: Optional[float] = None) -> Dict[str, Any]:
    """Execute actual US stock sell order (async)

    Args:
        ticker: Stock ticker symbol
        company_name: Company name for logging
        logger: Logger instance
        limit_price: Limit price in USD for reserved orders (required for off-hours trading)
    """
    try:
        USStockTrading = load_us_stock_trading_class()

        trading = USStockTrading()

        # Get current price for limit_price if not provided (needed for reserved orders)
        effective_limit_price = limit_price
        if not effective_limit_price:
            price_info = trading.get_current_price(ticker)
            if price_info:
                effective_limit_price = price_info['current_price']

        trade_result = await trading.async_sell_stock(ticker=ticker, limit_price=effective_limit_price)

        if trade_result['success']:
            logger.info(f"✅ 🇺🇸 US sell successful: {company_name}({ticker}) - {trade_result['message']}")
        else:
            logger.error(f"❌ 🇺🇸 US sell failed: {company_name}({ticker}) - {trade_result['message']}")

        return trade_result

    except ImportError as e:
        logger.error(f"US Trading module import failed: {e}")
        return {"success": False, "message": f"Import error: {e}"}
    except Exception as e:
        logger.error(f"Error during US sell execution: {e}", exc_info=True)
        return {"success": False, "message": str(e)}


def main():
    parser = argparse.ArgumentParser(description="PRISM-INSIGHT GCP Pub/Sub Trading Signal Subscriber")
    parser.add_argument(
        "--project-id",
        default=os.environ.get("GCP_PROJECT_ID"),
        help="GCP Project ID"
    )
    parser.add_argument(
        "--subscription-id",
        default=os.environ.get("GCP_PUBSUB_SUBSCRIPTION_ID", "prism-trading-signals-sub"),
        help="GCP Pub/Sub Subscription ID"
    )
    parser.add_argument(
        "--credentials-path",
        default=os.environ.get("GCP_CREDENTIALS_PATH"),
        help="Path to GCP service account JSON key file"
    )
    parser.add_argument(
        "--log-file",
        default=None,
        help="Log file path (default: logs/subscriber_YYYYMMDD.log)"
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
    trading_mode = get_trading_mode()
    if args.dry_run:
        logger.warning("🔸 DRY-RUN mode: No actual trading will be executed.")
    else:
        logger.info("🔹 LIVE mode: Actual trading will be executed!")
        logger.info(f"🔹 Trading mode: {trading_mode.upper()}")

    # Initialize scheduled order manager (for demo mode off-market hours scheduling)
    global scheduled_order_manager
    if not args.dry_run and trading_mode == "demo":
        scheduled_order_manager = ScheduledOrderManager(logger=logger)

        # Define scheduler callback function
        def execute_scheduled_order(order: dict) -> dict:
            """Execute scheduled order (sync wrapper)"""
            signal = order.get("signal", {})
            signal_type = order.get("signal_type", "BUY")
            market = order.get("market", "KR")
            ticker = signal.get("ticker", "")
            company_name = signal.get("company_name", "")
            # Get price from signal for limit orders (signal may contain price info)
            price = signal.get("price", 0)

            # Call different trading functions based on market
            # Pass price as limit_price for reserved orders
            if market == "US":
                limit_price = float(price) if price else None
                if signal_type == "SELL":
                    return asyncio.run(execute_us_sell_trade(ticker, company_name, logger, limit_price=limit_price))
                else:  # BUY
                    return asyncio.run(execute_us_buy_trade(ticker, company_name, logger, limit_price=limit_price))
            else:  # KR (default)
                limit_price = int(price) if price else None
                if signal_type == "SELL":
                    return asyncio.run(execute_sell_trade(ticker, company_name, logger, limit_price=limit_price))
                else:  # BUY
                    return asyncio.run(execute_buy_trade(ticker, company_name, logger, limit_price=limit_price))

        # Start background scheduler
        scheduled_order_manager.start_scheduler(execute_scheduled_order)
        logger.info("📅 Demo mode off-market hours scheduler activated")

    # Check GCP connection info
    if not args.project_id or not args.subscription_id:
        logger.error("GCP connection information is missing.")
        logger.error("Set environment variables or use --project-id, --subscription-id options.")
        logger.error('Example: export GCP_PROJECT_ID="your-project-id"')
        logger.error('         export GCP_PUBSUB_SUBSCRIPTION_ID="prism-trading-signals-sub"')
        return

    try:
        from google.cloud import pubsub_v1
    except ImportError:
        logger.error("google-cloud-pubsub package not installed.")
        logger.error("Install with: pip install google-cloud-pubsub")
        return

    # Set credentials if provided
    if args.credentials_path:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = args.credentials_path

    # Connect to GCP Pub/Sub
    logger.info("Connecting to GCP Pub/Sub...")
    subscriber = pubsub_v1.SubscriberClient()
    subscription_path = subscriber.subscription_path(args.project_id, args.subscription_id)

    logger.info(f"Subscription started: {subscription_path}")
    logger.info("=" * 60)

    # Statistics
    message_count = 0
    trade_count = {"BUY": 0, "SELL": 0}

    # Signal handler function
    def handle_signal(signal: dict):
        """Function to process received signals"""
        nonlocal message_count, trade_count

        signal_type = signal.get("type", "UNKNOWN")
        ticker = signal.get("ticker", "")
        company_name = signal.get("company_name", "")
        price = signal.get("price", 0)
        market = signal.get("market", "KR")  # KR (Korea) or US (USA)

        # Emoji by signal type
        emoji = {
            "BUY": "📈",
            "SELL": "📉",
            "EVENT": "🔔"
        }.get(signal_type, "📌")

        # Market label
        market_label = "🇺🇸" if market == "US" else "🇰🇷"
        currency = "USD" if market == "US" else "KRW"

        # Log basic signal info
        logger.info(f"{emoji} {market_label} [{signal_type}] {company_name}({ticker}) @ {price:,.2f} {currency}")

        # If buy signal
        if signal_type == "BUY":
            target = signal.get("target_price", 0)
            stop_loss = signal.get("stop_loss", 0)
            rationale = signal.get("rationale", "")
            buy_score = signal.get("buy_score", 0)

            details = []
            if target:
                details.append(f"Target: {target:,.2f} {currency}")
            if stop_loss:
                details.append(f"Stop-loss: {stop_loss:,.2f} {currency}")
            if buy_score:
                details.append(f"Buy score: {buy_score}")
            if rationale:
                details.append(f"Rationale: {rationale[:100]}...")

            if details:
                logger.info(f"   -> {' | '.join(details)}")

            # Execute actual buy
            if not args.dry_run:
                trading_mode = get_trading_mode()
                in_market_hours = is_market_hours(market)

                # Demo mode + off-market hours: schedule for next trading day
                if trading_mode == "demo" and not in_market_hours:
                    logger.info(f"⏰ [DEMO mode off-hours] Scheduling for next trading day: {market_label} {company_name}({ticker}) [BUY]")
                    if scheduled_order_manager:
                        scheduled_order_manager.add_order(signal, signal_type="BUY", market=market)
                    else:
                        logger.warning("Scheduler not initialized - skipping order")
                else:
                    # Live trading or market hours: execute immediately
                    logger.info(f"🚀 Executing buy order: {market_label} {company_name}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_buy_trade(ticker, company_name, logger, limit_price=float(price) if price else None))
                    else:
                        asyncio.run(execute_buy_trade(ticker, company_name, logger, limit_price=int(price) if price else None))
            else:
                logger.info(f"🔸 [DRY-RUN] Buy skipped: {market_label} {company_name}({ticker})")

            trade_count["BUY"] += 1

        # If sell signal
        elif signal_type == "SELL":
            profit_rate = signal.get("profit_rate", 0)
            sell_reason = signal.get("sell_reason", "")
            buy_price = signal.get("buy_price", 0)

            details = []
            if buy_price:
                details.append(f"Buy price: {buy_price:,.2f} {currency}")
            details.append(f"Profit rate: {profit_rate:+.2f}%")
            if sell_reason:
                details.append(f"Sell reason: {sell_reason}")

            logger.info(f"   -> {' | '.join(details)}")

            # Execute actual sell
            if not args.dry_run:
                trading_mode = get_trading_mode()
                in_market_hours = is_market_hours(market)

                # Demo mode + off-market hours: schedule for next trading day (same logic as BUY)
                if trading_mode == "demo" and not in_market_hours:
                    logger.info(f"⏰ [DEMO mode off-hours] Scheduling for next trading day: {market_label} {company_name}({ticker}) [SELL]")
                    if scheduled_order_manager:
                        scheduled_order_manager.add_order(signal, signal_type="SELL", market=market)
                    else:
                        logger.warning("Scheduler not initialized - skipping sell order")
                else:
                    # Live trading or market hours: execute immediately
                    logger.info(f"🚀 Executing sell order: {market_label} {company_name}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_sell_trade(ticker, company_name, logger, limit_price=float(price) if price else None))
                    else:
                        asyncio.run(execute_sell_trade(ticker, company_name, logger, limit_price=int(price) if price else None))
            else:
                logger.info(f"🔸 [DRY-RUN] Sell skipped: {market_label} {company_name}({ticker})")

            trade_count["SELL"] += 1

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

        message_count += 1
        logger.debug(f"Original signal: {json.dumps(signal, ensure_ascii=False)}")

    # Callback function
    def callback(message):
        """GCP Pub/Sub message callback"""
        try:
            signal = json.loads(message.data.decode("utf-8"))
            handle_signal(signal)
            message.ack()
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
            message.nack()

    # Subscribe
    streaming_pull_future = subscriber.subscribe(subscription_path, callback=callback)
    logger.info(f"Listening for messages on {subscription_path}...")

    # Main loop
    try:
        streaming_pull_future.result()
    except KeyboardInterrupt:
        streaming_pull_future.cancel()

        # Clean up scheduler
        if scheduled_order_manager:
            scheduled_order_manager.stop_scheduler()
            pending_count = len([o for o in scheduled_order_manager.orders if o["status"] == "pending"])
            if pending_count > 0:
                logger.info(f"📋 {pending_count} scheduled orders will be processed on next run.")

        logger.info("=" * 60)
        logger.info(f"Subscription ended.")
        logger.info(f"Total {message_count} signals received (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
