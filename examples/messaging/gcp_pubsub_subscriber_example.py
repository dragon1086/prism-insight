#!/usr/bin/env python3
"""
PRISM-INSIGHT Trading Signal Subscriber (GCP Pub/Sub Auto-Trading Integration)

Running this script will receive buy/sell signals published by PRISM-INSIGHT
in real-time via GCP Pub/Sub and execute actual auto-trading.

Supported Markets:
    - KR (한국): 09:00-15:30 KST, domestic_stock_trading 모듈 사용
    - US (미국): 09:30-16:00 EST, us_stock_trading 모듈 사용

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
    모의투자(demo) 모드에서 장외 시간에 시그널이 들어오면,
    해당 시장의 다음 영업일 장 시작 시점에 자동으로 시장가 매수/매도를 실행합니다.
    - KR: 다음 영업일 09:05 KST
    - US: 다음 영업일 09:35 EST (KST 기준 23:35)
"""
import os
import sys
import json
import logging
import argparse
import asyncio
import threading
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
# 스케줄링 관련 유틸리티
# ============================================================

def get_trading_mode() -> str:
    """kis_devlp.yaml에서 거래 모드 확인 (demo/real)"""
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
    현재 시간이 정규 장 시간인지 확인

    Args:
        market: "KR" (한국) or "US" (미국)

    Returns:
        bool: 장 시간 여부
    """
    if market == "US":
        return is_us_market_hours()

    # 한국: 09:00~15:30 KST
    now = datetime.now().time()
    market_open = time(9, 0)
    market_close = time(15, 30)
    return market_open <= now <= market_close


def is_us_market_hours() -> bool:
    """현재 시간이 미국 정규 장 시간(09:30~16:00 EST)인지 확인"""
    try:
        import pytz
        us_eastern = pytz.timezone('US/Eastern')
        now_est = datetime.now(us_eastern).time()
        market_open = time(9, 30)
        market_close = time(16, 0)
        return market_open <= now_est <= market_close
    except ImportError:
        # pytz 없으면 KST 기준으로 대략 계산 (EST = KST - 14시간)
        now = datetime.now()
        # KST 23:30 ~ 06:00 (다음날) 이 US 장 시간
        now_time = now.time()
        return now_time >= time(23, 30) or now_time <= time(6, 0)


def is_market_day_check() -> bool:
    """영업일인지 확인 (check_market_day.py 활용)"""
    try:
        from check_market_day import is_market_day
        return is_market_day()
    except ImportError:
        # fallback: 주말만 체크
        return datetime.now().weekday() < 5


def get_next_market_open(market: str = "KR") -> datetime:
    """
    다음 영업일 장 시작 시간 계산

    Args:
        market: "KR" (한국) or "US" (미국)

    Returns:
        datetime: 다음 영업일 장 시작 시간
    """
    if market == "US":
        return get_next_us_market_open()

    # 한국: 다음 영업일 09:05 KST
    now = datetime.now()
    next_day = now + timedelta(days=1)

    # 다음 영업일 찾기 (최대 7일까지 탐색)
    for _ in range(7):
        # 주말 스킵
        if next_day.weekday() >= 5:
            next_day += timedelta(days=1)
            continue

        # 공휴일 체크 (is_market_day_check 활용)
        try:
            from check_market_day import is_market_day
            from holidays.countries import KR

            # 임시로 해당 날짜가 영업일인지 확인
            kr_holidays = KR()
            if next_day.date() in kr_holidays:
                next_day += timedelta(days=1)
                continue
            # 노동절 체크
            if next_day.month == 5 and next_day.day == 1:
                next_day += timedelta(days=1)
                continue
        except ImportError:
            pass

        # 영업일 발견
        break

    # 09:05 설정 (장 시작 후 안정화 시간)
    return next_day.replace(hour=9, minute=5, second=0, microsecond=0)


def get_next_us_market_open() -> datetime:
    """
    다음 미국 영업일 장 시작 시간 계산 (09:35 EST -> KST로 변환)

    Returns:
        datetime: 다음 미국 영업일 장 시작 시간 (KST)
    """
    try:
        import pytz
        us_eastern = pytz.timezone('US/Eastern')
        kst = pytz.timezone('Asia/Seoul')

        now_est = datetime.now(us_eastern)
        next_day_est = now_est + timedelta(days=1)

        # 다음 미국 영업일 찾기 (최대 7일까지 탐색)
        for _ in range(7):
            # 주말 스킵
            if next_day_est.weekday() >= 5:
                next_day_est += timedelta(days=1)
                continue

            # 미국 공휴일 체크
            try:
                from holidays.countries import US
                us_holidays = US()
                if next_day_est.date() in us_holidays:
                    next_day_est += timedelta(days=1)
                    continue
            except ImportError:
                pass

            # 영업일 발견
            break

        # 09:35 EST 설정 (장 시작 후 안정화 시간)
        market_open_est = next_day_est.replace(hour=9, minute=35, second=0, microsecond=0)

        # KST로 변환
        market_open_kst = market_open_est.astimezone(kst)
        return market_open_kst.replace(tzinfo=None)  # naive datetime 반환

    except ImportError:
        # pytz 없으면 대략 계산 (EST + 14시간 = KST)
        now = datetime.now()
        next_day = now + timedelta(days=1)

        for _ in range(7):
            if next_day.weekday() >= 5:
                next_day += timedelta(days=1)
                continue
            break

        # KST 기준 23:35 (다음날 EST 09:35)
        return next_day.replace(hour=23, minute=35, second=0, microsecond=0)


class ScheduledOrderManager:
    """
    예약 주문 관리자

    모의투자 장외 시간에 들어온 시그널을 저장하고,
    다음 영업일 장 시작 시 자동 실행합니다.
    """

    def __init__(self, storage_path: Path = None, logger: logging.Logger = None):
        self.storage_path = storage_path or (PROJECT_ROOT / "logs" / "scheduled_orders.json")
        self.logger = logger or logging.getLogger("scheduled_orders")
        self.orders: List[Dict[str, Any]] = []
        self._lock = threading.Lock()
        self._scheduler_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

        # 저장된 예약 주문 로드
        self._load_orders()

    def _load_orders(self):
        """파일에서 예약 주문 로드"""
        try:
            if self.storage_path.exists():
                with open(self.storage_path, 'r', encoding='utf-8') as f:
                    self.orders = json.load(f)
                if self.orders:
                    self.logger.info(f"📋 {len(self.orders)}개의 예약 주문 로드됨")
        except Exception as e:
            self.logger.error(f"예약 주문 로드 실패: {e}")
            self.orders = []

    def _save_orders(self):
        """예약 주문을 파일에 저장"""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, 'w', encoding='utf-8') as f:
                json.dump(self.orders, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"예약 주문 저장 실패: {e}")

    def add_order(self, signal: Dict[str, Any], signal_type: str = "BUY", market: str = "KR") -> bool:
        """
        예약 주문 추가

        Args:
            signal: Signal data dictionary
            signal_type: "BUY" or "SELL" (기본값: "BUY" - 하위 호환성)
            market: "KR" or "US" (기본값: "KR" - 하위 호환성)
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

            action_type = "매수" if signal_type == "BUY" else "매도"
            market_label = "🇺🇸 US" if market == "US" else "🇰🇷 KR"
            self.logger.info(f"⏰ 예약 주문 등록: [{market_label}] {company_name}({ticker}) [{action_type}] -> {execute_time} 실행 예정")
            return True

    def get_pending_orders(self) -> List[Dict[str, Any]]:
        """실행 대기 중인 주문 조회"""
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
        """주문 실행 완료 처리"""
        with self._lock:
            order["status"] = "executed" if success else "failed"
            order["executed_at"] = datetime.now().isoformat()
            order["result_message"] = message
            self._save_orders()

    def clear_old_orders(self, days: int = 7):
        """오래된 주문 정리"""
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
                self.logger.info(f"🗑️ {removed}개의 오래된 주문 정리됨")

    def start_scheduler(self, execute_callback):
        """백그라운드 스케줄러 시작"""
        def scheduler_loop():
            self.logger.info("🕐 예약 주문 스케줄러 시작됨 (KR/US 시장 지원)")
            while not self._stop_event.is_set():
                try:
                    # 1분마다 체크
                    if self._stop_event.wait(60):
                        break

                    # 대기 중인 주문 조회
                    pending_orders = self.get_pending_orders()
                    for order in pending_orders:
                        signal = order["signal"]
                        signal_type = order.get("signal_type", "BUY")
                        market = order.get("market", "KR")
                        ticker = signal.get("ticker", "")
                        company_name = signal.get("company_name", "")

                        # 해당 시장의 장 시간인지 체크
                        if not is_market_hours(market):
                            continue

                        action_type = "매수" if signal_type == "BUY" else "매도"
                        market_label = "🇺🇸 US" if market == "US" else "🇰🇷 KR"
                        self.logger.info(f"🚀 예약 주문 실행: [{market_label}] {company_name}({ticker}) [{action_type}]")

                        try:
                            result = execute_callback(order)
                            success = result.get("success", False)
                            message = result.get("message", "")
                            self.mark_executed(order, success, message)

                            if success:
                                self.logger.info(f"✅ 예약 주문 성공: [{market_label}] {company_name}({ticker})")
                            else:
                                self.logger.error(f"❌ 예약 주문 실패: [{market_label}] {company_name}({ticker}) - {message}")
                        except Exception as e:
                            self.mark_executed(order, False, str(e))
                            self.logger.error(f"❌ 예약 주문 실행 오류: {e}")

                    # 매일 자정에 오래된 주문 정리
                    if datetime.now().hour == 0 and datetime.now().minute < 2:
                        self.clear_old_orders()

                except Exception as e:
                    self.logger.error(f"스케줄러 오류: {e}")

            self.logger.info("🕐 예약 주문 스케줄러 종료됨")

        self._scheduler_thread = threading.Thread(target=scheduler_loop, daemon=True)
        self._scheduler_thread.start()

    def stop_scheduler(self):
        """스케줄러 중지"""
        self._stop_event.set()
        if self._scheduler_thread:
            self._scheduler_thread.join(timeout=5)


# Global scheduler manager (will be initialized in main)
scheduled_order_manager: Optional[ScheduledOrderManager] = None


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


async def execute_buy_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """Execute actual buy order (async)"""
    try:
        from trading.domestic_stock_trading import AsyncTradingContext

        async with AsyncTradingContext() as trading:
            trade_result = await trading.async_buy_stock(stock_code=ticker)

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
    """Execute actual sell order (async)"""
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


async def execute_us_buy_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """Execute actual US stock buy order (async)"""
    try:
        # Import from prism-us module
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "prism-us"))
        from trading.us_stock_trading import USStockTrading

        trading = USStockTrading()
        trade_result = await trading.async_buy_stock(ticker=ticker)

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


async def execute_us_sell_trade(ticker: str, company_name: str, logger: logging.Logger) -> Dict[str, Any]:
    """Execute actual US stock sell order (async)"""
    try:
        # Import from prism-us module
        import sys
        sys.path.insert(0, str(PROJECT_ROOT / "prism-us"))
        from trading.us_stock_trading import USStockTrading

        trading = USStockTrading()
        trade_result = await trading.async_sell_stock(ticker=ticker)

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

    # Initialize scheduled order manager (모의투자 장외 시간 스케줄링용)
    global scheduled_order_manager
    if not args.dry_run and trading_mode == "demo":
        scheduled_order_manager = ScheduledOrderManager(logger=logger)

        # 스케줄러 콜백 함수 정의
        def execute_scheduled_order(order: dict) -> dict:
            """스케줄된 주문 실행 (동기 래퍼)"""
            signal = order.get("signal", {})
            signal_type = order.get("signal_type", "BUY")
            market = order.get("market", "KR")
            ticker = signal.get("ticker", "")
            company_name = signal.get("company_name", "")

            # Market에 따라 다른 트레이딩 함수 호출
            if market == "US":
                if signal_type == "SELL":
                    return asyncio.run(execute_us_sell_trade(ticker, company_name, logger))
                else:  # BUY
                    return asyncio.run(execute_us_buy_trade(ticker, company_name, logger))
            else:  # KR (기본값)
                if signal_type == "SELL":
                    return asyncio.run(execute_sell_trade(ticker, company_name, logger))
                else:  # BUY
                    return asyncio.run(execute_buy_trade(ticker, company_name, logger))

        # 백그라운드 스케줄러 시작
        scheduled_order_manager.start_scheduler(execute_scheduled_order)
        logger.info("📅 모의투자 장외 시간 스케줄러 활성화됨")

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
        market = signal.get("market", "KR")  # KR (한국) or US (미국)

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

                # 모의투자 + 장외시간: 다음 영업일로 스케줄링
                if trading_mode == "demo" and not in_market_hours:
                    logger.info(f"⏰ [DEMO 모드 장외시간] 다음 영업일 예약 등록: {market_label} {company_name}({ticker}) [매수]")
                    if scheduled_order_manager:
                        scheduled_order_manager.add_order(signal, signal_type="BUY", market=market)
                    else:
                        logger.warning("스케줄러 미초기화 - 주문 스킵")
                else:
                    # 실전투자 또는 장중: 즉시 실행
                    logger.info(f"🚀 Executing buy order: {market_label} {company_name}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_buy_trade(ticker, company_name, logger))
                    else:
                        asyncio.run(execute_buy_trade(ticker, company_name, logger))
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

                # 모의투자 + 장외시간: 다음 영업일로 스케줄링 (BUY와 동일한 로직)
                if trading_mode == "demo" and not in_market_hours:
                    logger.info(f"⏰ [DEMO 모드 장외시간] 다음 영업일 예약 등록: {market_label} {company_name}({ticker}) [매도]")
                    if scheduled_order_manager:
                        scheduled_order_manager.add_order(signal, signal_type="SELL", market=market)
                    else:
                        logger.warning("스케줄러 미초기화 - 매도 주문 스킵")
                else:
                    # 실전투자 또는 장중: 즉시 실행
                    logger.info(f"🚀 Executing sell order: {market_label} {company_name}({ticker})")
                    if market == "US":
                        asyncio.run(execute_us_sell_trade(ticker, company_name, logger))
                    else:
                        asyncio.run(execute_sell_trade(ticker, company_name, logger))
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

        # 스케줄러 정리
        if scheduled_order_manager:
            scheduled_order_manager.stop_scheduler()
            pending_count = len([o for o in scheduled_order_manager.orders if o["status"] == "pending"])
            if pending_count > 0:
                logger.info(f"📋 {pending_count}개의 예약 주문이 다음 실행 시 처리됩니다.")

        logger.info("=" * 60)
        logger.info(f"Subscription ended.")
        logger.info(f"Total {message_count} signals received (Buy: {trade_count['BUY']}, Sell: {trade_count['SELL']})")


if __name__ == "__main__":
    main()
