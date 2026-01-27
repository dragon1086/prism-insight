"""
US Stock Trading Module (KIS Overseas Stock API)

Provides:
- Fixed amount purchase per stock
- Market price buy/sell
- Full liquidation sell
- Portfolio management

Key differences from Korean domestic trading:
- API endpoints: /uapi/overseas-stock/ instead of /uapi/domestic-stock/
- Exchange codes: NASD (NASDAQ), NYSE (NYSE), AMEX (AMEX)
- TR IDs are different for overseas trading
- Currency: USD
- Market hours: 09:30-16:00 EST (23:30-06:00 KST next day)
"""

import asyncio
import datetime
import logging
import math
import time
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml
import pytz

# Path to directory where current file is located
TRADING_DIR = Path(__file__).parent
PROJECT_ROOT = TRADING_DIR.parent.parent

# Import KIS auth from parent trading directory
import sys
sys.path.insert(0, str(PROJECT_ROOT / "trading"))
import kis_auth as ka

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load configuration file (use same config as domestic)
CONFIG_FILE = PROJECT_ROOT / "trading" / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)

# US timezone
US_EASTERN = pytz.timezone('US/Eastern')

# Exchange code mapping
EXCHANGE_CODES = {
    "NASDAQ": "NASD",
    "NYSE": "NYSE",
    "AMEX": "AMEX",
    "NASD": "NASD",  # Allow direct use
}

# Common NASDAQ stocks for exchange detection
NASDAQ_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "GOOG", "AMZN", "META", "NVDA", "TSLA",
    "AVGO", "COST", "ADBE", "CSCO", "PEP", "NFLX", "INTC", "AMD",
    "QCOM", "TXN", "HON", "CMCSA", "SBUX", "GILD", "MDLZ", "ISRG",
    "VRTX", "REGN", "ATVI", "ADP", "BKNG", "CHTR", "LRCX", "MU",
    "KLAC", "SNPS", "CDNS", "MRVL", "PANW", "CRWD", "ZS", "DDOG"
}


def get_exchange_code(ticker: str) -> str:
    """
    Determine the exchange code for a given ticker.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Exchange code (NASD, NYSE, AMEX)
    """
    # Simple heuristic - most tech stocks are on NASDAQ
    # In production, you'd want to look this up from a database or API
    ticker_upper = ticker.upper()

    if ticker_upper in NASDAQ_TICKERS:
        return "NASD"

    # Default to NYSE for unknown tickers (can be overridden)
    return "NYSE"


class USStockTrading:
    """US Stock Trading class using KIS Overseas Stock API"""

    # Default buy amount per stock (in USD)
    DEFAULT_BUY_AMOUNT = _cfg.get("default_unit_amount_usd", 100)  # $100 default
    # Auto trading enabled flag
    AUTO_TRADING = _cfg.get("auto_trading", True)
    # Default trading environment
    DEFAULT_MODE = _cfg.get("default_mode", "demo")

    def __init__(self, mode: str = None, buy_amount: float = None, auto_trading: bool = None):
        """
        Initialize US Stock Trading

        Args:
            mode: 'demo' (simulated) or 'real' (real trading)
            buy_amount: Buy amount per stock in USD (default: from config)
            auto_trading: Whether to execute auto trading
        """
        self.mode = mode if mode else self.DEFAULT_MODE
        self.env = "vps" if self.mode == "demo" else "prod"
        self.buy_amount = buy_amount if buy_amount else self.DEFAULT_BUY_AMOUNT
        self.auto_trading = auto_trading if auto_trading is not None else self.AUTO_TRADING

        # Authentication
        ka.auth(svr=self.env, product="01")

        try:
            self.trenv = ka.getTREnv()
        except RuntimeError as e:
            print("❌ KIS API authentication failed!")
            print(f"Mode: {self.mode}, Error: {e}")
            print("📋 Please check kis_devlp.yaml settings.")
            raise RuntimeError(f"{self.mode} mode authentication failed") from e

        # Async setup
        self._global_lock = asyncio.Lock()
        self._semaphore = asyncio.Semaphore(3)
        self._stock_locks = {}

        logger.info(f"USStockTrading initialized (Async Enabled)")
        logger.info(f"Mode: {mode}, Buy Amount: ${self.buy_amount:,.2f} USD")
        logger.info(f"Account: {self.trenv.my_acct}-{self.trenv.my_prod}")

    def get_current_price(self, ticker: str, exchange: str = None) -> Optional[Dict[str, Any]]:
        """
        Get current market price for US stock

        Args:
            ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
            exchange: Exchange code (NASD, NYSE, AMEX) - auto-detected if not provided

        Returns:
            {
                'ticker': 'AAPL',
                'stock_name': 'APPLE INC',
                'current_price': 185.50,
                'change_rate': 1.25,
                'volume': 45000000,
                'exchange': 'NASD'
            }
        """
        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        api_url = "/uapi/overseas-price/v1/quotations/price"
        tr_id = "HHDFS00000300"

        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": ticker.upper()
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params)

            if res.isOK():
                data = res.getBody().output

                result = {
                    'ticker': ticker.upper(),
                    'stock_name': data.get('name', ''),
                    'current_price': float(data.get('last', 0)),
                    'change_rate': float(data.get('rate', 0)),
                    'volume': int(data.get('tvol', 0)),
                    'exchange': exchange
                }

                logger.info(f"[{ticker}] Current price: ${result['current_price']:.2f} ({result['change_rate']:+.2f}%)")
                return result
            else:
                logger.error(f"Price query failed: {res.getErrorCode()} - {res.getErrorMessage()}")
                return None

        except Exception as e:
            logger.error(f"Error getting price: {str(e)}")
            return None

    def calculate_buy_quantity(self, ticker: str, buy_amount: float = None,
                               exchange: str = None) -> int:
        """
        Calculate buyable quantity

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD (default: class setting)
            exchange: Exchange code

        Returns:
            Buyable quantity (0 if cannot buy)
        """
        amount = buy_amount if buy_amount else self.buy_amount

        # Get current price
        price_info = self.get_current_price(ticker, exchange)
        if not price_info:
            return 0

        current_price = price_info['current_price']

        # Calculate quantity (floor)
        quantity = math.floor(amount / current_price)

        if quantity == 0:
            logger.warning(f"[{ticker}] Price ${current_price:.2f} > Amount ${amount:.2f} - Cannot buy")
        else:
            total = quantity * current_price
            logger.info(f"[{ticker}] Buyable: {quantity} shares x ${current_price:.2f} = ${total:.2f}")

        return quantity

    def buy_market_price(self, ticker: str, buy_amount: float = None,
                         exchange: str = None) -> Dict[str, Any]:
        """
        Market price buy for US stock

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            {
                'success': bool,
                'order_no': str,
                'ticker': str,
                'quantity': int,
                'message': str
            }
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        # Calculate buy quantity
        buy_quantity = self.calculate_buy_quantity(ticker, buy_amount, exchange)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Buy quantity is 0 (price higher than buy amount)'
            }

        # Execute buy order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        # TR ID for overseas stock buy
        if self.mode == "real":
            tr_id = "TTTT1002U"  # Real overseas buy
        else:
            tr_id = "VTTT1002U"  # Demo overseas buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(buy_quantity),
            "OVRS_ORD_UNPR": "0",  # Market price = 0
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"  # 00: Market price
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Market buy order success: {buy_quantity} shares, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'message': f'Market buy order completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'message': f'Buy order error: {str(e)}'
            }

    def buy_limit_price(self, ticker: str, limit_price: float, buy_amount: float = None,
                        exchange: str = None) -> Dict[str, Any]:
        """
        Limit price buy for US stock

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        amount = buy_amount if buy_amount else self.buy_amount

        # Calculate quantity based on limit price
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'Buy quantity is 0 (limit ${limit_price:.2f} > amount ${amount:.2f})'
            }

        # Execute limit buy order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        if self.mode == "real":
            tr_id = "TTTT1002U"
        else:
            tr_id = "VTTT1002U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(buy_quantity),
            "OVRS_ORD_UNPR": str(limit_price),
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"  # Limit order
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Limit buy order success: {buy_quantity} shares x ${limit_price:.2f}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Limit buy order completed ({buy_quantity} shares x ${limit_price:.2f})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Limit buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during limit buy: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'Buy order error: {str(e)}'
            }

    def get_holding_quantity(self, ticker: str) -> int:
        """
        Get holding quantity for a specific ticker

        Args:
            ticker: Stock ticker symbol

        Returns:
            Holding quantity (0 if not held)
        """
        portfolio = self.get_portfolio()

        for stock in portfolio:
            if stock['ticker'].upper() == ticker.upper():
                return stock['quantity']

        return 0

    def sell_all_market_price(self, ticker: str, exchange: str = None) -> Dict[str, Any]:
        """
        Market price sell all holdings

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        # Check holding quantity
        quantity = self.get_holding_quantity(ticker)

        if quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'No holdings to sell'
            }

        # Execute sell order
        api_url = "/uapi/overseas-stock/v1/trading/order"

        if self.mode == "real":
            tr_id = "TTTT1006U"  # Real overseas sell
        else:
            tr_id = "VTTT1001U"  # Demo overseas sell

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "ORD_QTY": str(quantity),
            "OVRS_ORD_UNPR": "0",  # Market price = 0
            "ORD_SVR_DVSN_CD": "0",
            "SLL_TYPE": "00"  # Sell type
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '')

                logger.info(f"[{ticker}] Market sell order success: {quantity} shares, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Market sell order completed ({quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Sell order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Sell order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': quantity,
                'message': f'Sell order error: {str(e)}'
            }

    def is_market_open(self) -> bool:
        """
        Check if US market is currently open

        Returns:
            True if market is open, False otherwise
        """
        now_et = datetime.datetime.now(US_EASTERN)
        current_time = now_et.time()

        # US market hours: 09:30 - 16:00 ET
        market_open = datetime.time(9, 30)
        market_close = datetime.time(16, 0)

        # Check if it's a weekday
        if now_et.weekday() >= 5:  # Saturday = 5, Sunday = 6
            return False

        return market_open <= current_time <= market_close

    def is_reserved_order_available(self) -> bool:
        """
        Check if reserved order is available (Korean time window)

        Reserved order available: 10:00 ~ 23:20 KST (winter) / 10:00 ~ 22:20 KST (summer)
        System maintenance: 16:30 ~ 16:45 KST (not available)

        Returns:
            True if reserved order can be placed, False otherwise
        """
        import pytz
        KST = pytz.timezone('Asia/Seoul')
        now_kst = datetime.datetime.now(KST)
        current_time = now_kst.time()

        # System maintenance window: 16:30 ~ 16:45
        if datetime.time(16, 30) <= current_time <= datetime.time(16, 45):
            return False

        # Reserved order window: 10:00 ~ 23:20 (using conservative winter time)
        resv_start = datetime.time(10, 0)
        resv_end = datetime.time(23, 20)

        return resv_start <= current_time <= resv_end

    def buy_reserved_order(self, ticker: str, limit_price: float, buy_amount: float = None,
                           exchange: str = None) -> Dict[str, Any]:
        """
        Reserved order buy for US stock (executed at next market open)
        예약주문 매수 - 다음 장 시작시 자동 실행

        Note: US reserved orders only support LIMIT orders (지정가주문만 가능)

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD (REQUIRED - market order not supported)
            buy_amount: Buy amount in USD
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if not limit_price or limit_price <= 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': 0,
                'message': 'Limit price is required for US reserved orders (market order not supported)'
            }

        if not self.is_reserved_order_available():
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Reserved order not available at this time (available 10:00-23:20 KST, except 16:30-16:45)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        amount = buy_amount if buy_amount else self.buy_amount

        # Calculate quantity based on limit price
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'Buy quantity is 0 (limit ${limit_price:.2f} > amount ${amount:.2f})'
            }

        # Reserved order API
        api_url = "/uapi/overseas-stock/v1/trading/order-resv"

        # TR ID for US reserved order buy
        if self.mode == "real":
            tr_id = "TTTT3014U"  # Real US reserved buy
        else:
            tr_id = "VTTT3014U"  # Demo US reserved buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(buy_quantity),
            "FT_ORD_UNPR3": str(limit_price),
            "ORD_SVR_DVSN_CD": "0"
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '') or output.get('RSVN_ORD_SEQ', '')

                logger.info(f"[{ticker}] Reserved buy order success: {buy_quantity} shares x ${limit_price:.2f}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'order_type': 'reserved_limit',
                    'message': f'Reserved buy order completed ({buy_quantity} shares x ${limit_price:.2f})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Reserved buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during reserved buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'Reserved buy order error: {str(e)}'
            }

    def sell_reserved_order(self, ticker: str, limit_price: float = None,
                            use_moo: bool = False, exchange: str = None) -> Dict[str, Any]:
        """
        Reserved order sell for US stock (executed at next market open)
        예약주문 매도 - 다음 장 시작시 자동 실행

        Note: US reserved sell orders support LIMIT or MOO (Market On Open)

        Args:
            ticker: Stock ticker symbol
            limit_price: Limit price in USD (required if use_moo is False)
            use_moo: Use Market On Open order (default: False)
            exchange: Exchange code

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if not use_moo and (not limit_price or limit_price <= 0):
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Limit price is required for reserved sell (or use use_moo=True for Market On Open)'
            }

        if not self.is_reserved_order_available():
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Reserved order not available at this time (available 10:00-23:20 KST, except 16:30-16:45)'
            }

        if exchange is None:
            exchange = get_exchange_code(ticker)
        else:
            exchange = EXCHANGE_CODES.get(exchange.upper(), exchange)

        # Check holding quantity
        quantity = self.get_holding_quantity(ticker)

        if quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'No holdings to sell'
            }

        # Reserved order API
        api_url = "/uapi/overseas-stock/v1/trading/order-resv"

        # TR ID for US reserved order sell
        if self.mode == "real":
            tr_id = "TTTT3016U"  # Real US reserved sell
        else:
            tr_id = "VTTT3016U"  # Demo US reserved sell

        # Set price based on order type
        if use_moo:
            order_price = "0"
            order_type_str = "MOO (Market On Open)"
        else:
            order_price = str(limit_price)
            order_type_str = f"Limit ${limit_price:.2f}"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": exchange,
            "PDNO": ticker.upper(),
            "FT_ORD_QTY": str(quantity),
            "FT_ORD_UNPR3": order_price,
            "ORD_SVR_DVSN_CD": "0"
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('ODNO', '') or output.get('RSVN_ORD_SEQ', '')

                logger.info(f"[{ticker}] Reserved sell order success: {quantity} shares, {order_type_str}, Order#: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'ticker': ticker,
                    'quantity': quantity,
                    'limit_price': limit_price if not use_moo else None,
                    'order_type': 'reserved_moo' if use_moo else 'reserved_limit',
                    'message': f'Reserved sell order completed ({quantity} shares, {order_type_str})'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved sell order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': quantity,
                    'message': f'Reserved sell order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during reserved sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': quantity,
                'message': f'Reserved sell order error: {str(e)}'
            }

    def smart_buy(self, ticker: str, buy_amount: float = None,
                  exchange: str = None, limit_price: float = None) -> Dict[str, Any]:
        """
        Smart buy - automatically choose best method based on market hours

        - Market open: Execute market price buy immediately
        - Market closed + limit_price provided: Place reserved order (지정가 예약주문)
        - Market closed + no limit_price: Return error (reserved order requires limit price)

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code
            limit_price: Limit price for reserved order when market is closed

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if self.is_market_open():
            logger.info(f"[{ticker}] Market is open - executing market buy")
            return self.buy_market_price(ticker, buy_amount, exchange)
        else:
            # Market is closed - use reserved order if limit_price provided
            if limit_price and limit_price > 0:
                logger.info(f"[{ticker}] Market is closed - placing reserved order (limit: ${limit_price:.2f})")
                return self.buy_reserved_order(ticker, limit_price, buy_amount, exchange)
            else:
                logger.warning(f"[{ticker}] Market is closed and no limit_price provided - cannot place reserved order")
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'US market is closed. Provide limit_price for reserved order.'
                }

    def smart_sell_all(self, ticker: str, exchange: str = None,
                       limit_price: float = None, use_moo: bool = False) -> Dict[str, Any]:
        """
        Smart sell - automatically choose best method based on market hours

        - Market open: Execute market price sell immediately
        - Market closed + limit_price provided: Place reserved order (지정가 예약주문)
        - Market closed + use_moo=True: Place reserved MOO order (시장가 예약주문)
        - Market closed + no limit_price + no use_moo: Return error

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code
            limit_price: Limit price for reserved order when market is closed
            use_moo: Use Market On Open for reserved order (default: False)

        Returns:
            Order result dict
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'ticker': ticker,
                'quantity': 0,
                'message': 'Auto trading is disabled (AUTO_TRADING=False)'
            }

        if self.is_market_open():
            logger.info(f"[{ticker}] Market is open - executing market sell")
            return self.sell_all_market_price(ticker, exchange)
        else:
            # Market is closed - use reserved order
            if limit_price and limit_price > 0:
                logger.info(f"[{ticker}] Market is closed - placing reserved sell order (limit: ${limit_price:.2f})")
                return self.sell_reserved_order(ticker, limit_price, use_moo=False, exchange=exchange)
            elif use_moo:
                logger.info(f"[{ticker}] Market is closed - placing reserved MOO sell order")
                return self.sell_reserved_order(ticker, limit_price=None, use_moo=True, exchange=exchange)
            else:
                logger.warning(f"[{ticker}] Market is closed and no limit_price/use_moo provided")
                return {
                    'success': False,
                    'order_no': None,
                    'ticker': ticker,
                    'quantity': 0,
                    'message': 'US market is closed. Provide limit_price or use_moo=True for reserved order.'
                }

    async def _get_stock_lock(self, ticker: str) -> asyncio.Lock:
        """Get per-stock lock (prevent concurrent trades on same stock)"""
        if ticker not in self._stock_locks:
            self._stock_locks[ticker] = asyncio.Lock()
        return self._stock_locks[ticker]

    async def async_buy_stock(self, ticker: str, buy_amount: float = None,
                              exchange: str = None, timeout: float = 30.0,
                              limit_price: float = None) -> Dict[str, Any]:
        """
        Async buy API with timeout

        Args:
            ticker: Stock ticker symbol
            buy_amount: Buy amount in USD
            exchange: Exchange code
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order when market is closed

        Returns:
            Order result dict
        """
        try:
            return await asyncio.wait_for(
                self._execute_buy_stock(ticker, buy_amount, exchange, limit_price),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'ticker': ticker,
                'current_price': 0,
                'quantity': 0,
                'total_amount': 0,
                'order_no': None,
                'message': f'Buy request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_buy_stock(self, ticker: str, buy_amount: float = None,
                                 exchange: str = None, limit_price: float = None) -> Dict[str, Any]:
        """Execute buy stock logic"""
        amount = buy_amount if buy_amount else self.buy_amount

        result = {
            'success': False,
            'ticker': ticker,
            'current_price': 0,
            'quantity': 0,
            'total_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        stock_lock = await self._get_stock_lock(ticker)

        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        logger.info(f"[Async Buy] {ticker} starting (amount: ${amount:.2f})")

                        # Get current price
                        price_info = await asyncio.to_thread(
                            self.get_current_price, ticker, exchange
                        )
                        await asyncio.sleep(0.5)

                        if not price_info:
                            result['message'] = 'Failed to get current price'
                            return result

                        result['current_price'] = price_info['current_price']

                        # Calculate buy quantity
                        current_price = price_info['current_price']
                        buy_quantity = math.floor(amount / current_price)

                        if buy_quantity == 0:
                            result['message'] = f'Buy quantity is 0 (amount: ${amount:.2f})'
                            return result

                        result['quantity'] = buy_quantity
                        result['total_amount'] = buy_quantity * current_price

                        # Execute buy
                        await asyncio.sleep(0.5)
                        buy_result = await asyncio.to_thread(
                            self.smart_buy, ticker, amount, exchange, limit_price
                        )

                        if buy_result['success']:
                            result['success'] = True
                            result['order_no'] = buy_result['order_no']
                            result['message'] = f"Buy completed: {buy_quantity} shares x ${current_price:.2f} = ${result['total_amount']:.2f}"
                        else:
                            result['message'] = f"Buy failed: {buy_result['message']}"

                    except Exception as e:
                        result['message'] = f'Async buy error: {str(e)}'
                        logger.error(f"[Async Buy] {ticker} error: {str(e)}")

                    await asyncio.sleep(0.1)

        return result

    async def async_sell_stock(self, ticker: str, exchange: str = None,
                               timeout: float = 30.0, limit_price: float = None,
                               use_moo: bool = False) -> Dict[str, Any]:
        """
        Async sell API with timeout

        Args:
            ticker: Stock ticker symbol
            exchange: Exchange code
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order when market is closed
            use_moo: Use Market On Open for reserved order

        Returns:
            Order result dict
        """
        try:
            return await asyncio.wait_for(
                self._execute_sell_stock(ticker, exchange, limit_price, use_moo),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'ticker': ticker,
                'current_price': 0,
                'quantity': 0,
                'estimated_amount': 0,
                'order_no': None,
                'message': f'Sell request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_sell_stock(self, ticker: str, exchange: str = None,
                                  limit_price: float = None, use_moo: bool = False) -> Dict[str, Any]:
        """Execute sell stock logic with portfolio verification"""
        result = {
            'success': False,
            'ticker': ticker,
            'current_price': 0,
            'quantity': 0,
            'estimated_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        stock_lock = await self._get_stock_lock(ticker)

        async with stock_lock:
            async with self._semaphore:
                async with self._global_lock:
                    try:
                        logger.info(f"[Async Sell] {ticker} starting")

                        # Verify portfolio holdings
                        portfolio = await asyncio.to_thread(self.get_portfolio)

                        target_stock = None
                        for stock in portfolio:
                            if stock['ticker'].upper() == ticker.upper():
                                target_stock = stock
                                break

                        if not target_stock:
                            result['message'] = f'{ticker} not found in portfolio'
                            return result

                        if target_stock['quantity'] <= 0:
                            result['message'] = f'{ticker} quantity is 0'
                            return result

                        logger.info(f"[Async Sell] {ticker} holdings verified: {target_stock['quantity']} shares")

                        # Get current price for estimate
                        price_info = await asyncio.to_thread(
                            self.get_current_price, ticker, exchange
                        )

                        if price_info:
                            result['current_price'] = price_info['current_price']

                        # Execute sell
                        sell_result = await asyncio.to_thread(
                            self.smart_sell_all, ticker, exchange, limit_price, use_moo
                        )

                        if sell_result['success']:
                            result['success'] = True
                            result['quantity'] = sell_result['quantity']
                            result['order_no'] = sell_result['order_no']

                            if result['current_price'] > 0:
                                result['estimated_amount'] = result['quantity'] * result['current_price']

                            result['avg_price'] = target_stock.get('avg_price', 0)
                            result['profit_amount'] = target_stock.get('profit_amount', 0)
                            result['profit_rate'] = target_stock.get('profit_rate', 0)

                            result['message'] = (f"Sell completed: {result['quantity']} shares "
                                               f"(avg: ${result['avg_price']:.2f}, "
                                               f"est: ${result['estimated_amount']:.2f}, "
                                               f"P/L: {result['profit_rate']:+.2f}%)")
                        else:
                            result['message'] = f"Sell failed: {sell_result['message']}"

                    except Exception as e:
                        result['message'] = f'Async sell error: {str(e)}'
                        logger.error(f"[Async Sell] {ticker} error: {str(e)}")

                    await asyncio.sleep(0.1)

        return result

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """
        Get current US stock portfolio

        Returns:
            [{
                'ticker': 'AAPL',
                'stock_name': 'APPLE INC',
                'quantity': 10,
                'avg_price': 150.00,
                'current_price': 185.50,
                'eval_amount': 1855.00,
                'profit_amount': 355.00,
                'profit_rate': 23.67,
                'exchange': 'NASD'
            }, ...]
        """
        api_url = "/uapi/overseas-stock/v1/trading/inquire-balance"

        if self.mode == "real":
            tr_id = "TTTS3012R"  # Real overseas balance
        else:
            tr_id = "VTTS3012R"  # Demo overseas balance

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "OVRS_EXCG_CD": "NASD",  # Default to NASDAQ, loop through others
            "TR_CRCY_CD": "USD",
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }

        portfolio = []

        # Query each exchange
        for exchange in ["NASD", "NYSE", "AMEX"]:
            params["OVRS_EXCG_CD"] = exchange

            try:
                res = ka._url_fetch(api_url, tr_id, "", params)

                if res.isOK():
                    output1 = res.getBody().output1

                    if not isinstance(output1, list):
                        output1 = [output1] if output1 else []

                    for item in output1:
                        quantity = int(item.get('ovrs_cblc_qty', 0) or 0)
                        if quantity > 0:
                            stock_info = {
                                'ticker': item.get('ovrs_pdno', ''),
                                'stock_name': item.get('ovrs_item_name', ''),
                                'quantity': quantity,
                                'avg_price': float(item.get('pchs_avg_pric', 0) or 0),
                                'current_price': float(item.get('now_pric2', 0) or 0),
                                'eval_amount': float(item.get('ovrs_stck_evlu_amt', 0) or 0),
                                'profit_amount': float(item.get('frcr_evlu_pfls_amt', 0) or 0),
                                'profit_rate': float(item.get('evlu_pfls_rt', 0) or 0),
                                'exchange': exchange
                            }
                            portfolio.append(stock_info)

                time.sleep(0.1)  # Rate limit

            except Exception as e:
                logger.error(f"Error getting portfolio for {exchange}: {str(e)}")
                continue

        logger.info(f"Portfolio: {len(portfolio)} US stocks held")
        return portfolio

    def get_account_summary(self) -> Optional[Dict[str, Any]]:
        """
        Get account summary for US stocks including USD cash balance

        Returns:
            {
                'total_eval_amount': Total stock evaluation in USD,
                'total_profit_amount': Total P/L in USD,
                'total_profit_rate': Total P/L rate (%),
                'available_amount': Available USD for trading,
                'usd_cash': USD cash balance,
                'exchange_rate': USD/KRW exchange rate
            }
        """
        # Use inquire-present-balance API for accurate USD cash info
        api_url = "/uapi/overseas-stock/v1/trading/inquire-present-balance"
        tr_id = "CTRP6504R"  # 해외주식 체결기준 현재잔고

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "WCRC_FRCR_DVSN_CD": "02",  # 02: 외화
            "NATN_CD": "840",  # 미국
            "TR_MKET_CD": "00",  # 전체
            "INQR_DVSN_CD": "00"  # 전체
        }

        try:
            res = ka._url_fetch(api_url, tr_id, "", params)

            if res.isOK():
                body = res.getBody()
                output2 = body.output2 if hasattr(body, 'output2') else []
                output3 = body.output3 if hasattr(body, 'output3') else {}

                # Extract USD info from output2
                usd_cash = 0.0
                exchange_rate = 0.0

                if output2 and isinstance(output2, list):
                    for item in output2:
                        if item.get('crcy_cd') == 'USD':
                            usd_cash = float(item.get('frcr_dncl_amt_2', 0) or 0)
                            exchange_rate = float(item.get('frst_bltn_exrt', 0) or 0)
                            break

                # Calculate from portfolio for stock totals
                portfolio = self.get_portfolio()
                total_eval = sum(s['eval_amount'] for s in portfolio)
                total_profit = sum(s['profit_amount'] for s in portfolio)
                total_cost = sum(s['avg_price'] * s['quantity'] for s in portfolio)

                summary = {
                    'total_eval_amount': total_eval,
                    'total_profit_amount': total_profit,
                    'total_profit_rate': (total_profit / total_cost * 100) if total_cost > 0 else 0,
                    'available_amount': usd_cash,  # USD cash available for trading
                    'usd_cash': usd_cash,
                    'exchange_rate': exchange_rate,
                }

                logger.info(f"Account Summary: Stock Eval ${summary['total_eval_amount']:.2f}, "
                           f"P/L ${summary['total_profit_amount']:+.2f} "
                           f"({summary['total_profit_rate']:+.2f}%), "
                           f"USD Cash ${summary['usd_cash']:.2f}")

                return summary

            logger.error(f"Account summary API failed: {res.getErrorCode()} - {res.getErrorMessage()}")
            return None

        except Exception as e:
            logger.error(f"Error getting account summary: {str(e)}")
            return None


# Context Manager
class AsyncUSTradingContext:
    """Async trading context manager for safe resource management"""

    DEFAULT_BUY_AMOUNT = _cfg.get("default_unit_amount_usd", 100)
    AUTO_TRADING = _cfg.get("auto_trading", True)
    DEFAULT_MODE = _cfg.get("default_mode", "demo")

    def __init__(self, mode: str = None, buy_amount: float = None, auto_trading: bool = None):
        self.mode = mode if mode else self.DEFAULT_MODE
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading if auto_trading is not None else self.AUTO_TRADING
        self.trader = None

    async def __aenter__(self):
        self.trader = USStockTrading(
            mode=self.mode,
            buy_amount=self.buy_amount,
            auto_trading=self.auto_trading
        )
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"AsyncUSTradingContext error: {exc_type.__name__}: {exc_val}")


# ========== Test Code ==========
if __name__ == "__main__":
    """
    Usage example and test
    """

    # 1. Initialize
    print("\n=== 1. Initialize USStockTrading ===")
    try:
        trader = USStockTrading(mode="demo", buy_amount=100)
    except Exception as e:
        print(f"Failed to initialize: {e}")
        exit(1)

    # 2. Market hours check
    print("\n=== 2. Market Hours Check ===")
    is_open = trader.is_market_open()
    print(f"US Market is {'OPEN' if is_open else 'CLOSED'}")

    # 3. Get current price
    print("\n=== 3. Get Current Price (AAPL) ===")
    price_info = trader.get_current_price("AAPL")
    if price_info:
        print(f"Ticker: {price_info['ticker']}")
        print(f"Name: {price_info['stock_name']}")
        print(f"Price: ${price_info['current_price']:.2f}")
        print(f"Change: {price_info['change_rate']:+.2f}%")

    # 4. Calculate buy quantity
    print("\n=== 4. Calculate Buy Quantity ===")
    quantity = trader.calculate_buy_quantity("AAPL", 100)
    print(f"Buyable quantity with $100: {quantity} shares")

    # 5. Get portfolio
    print("\n=== 5. Get Portfolio ===")
    portfolio = trader.get_portfolio()
    if portfolio:
        for stock in portfolio:
            print(f"{stock['ticker']} ({stock['exchange']}): "
                  f"{stock['quantity']} shares, "
                  f"Avg: ${stock['avg_price']:.2f}, "
                  f"Current: ${stock['current_price']:.2f}, "
                  f"P/L: {stock['profit_rate']:+.2f}%")
    else:
        print("No US stock holdings")

    # 6. Get account summary
    print("\n=== 6. Account Summary ===")
    summary = trader.get_account_summary()
    if summary:
        print(f"Total Eval: ${summary['total_eval_amount']:.2f}")
        print(f"Total P/L: ${summary['total_profit_amount']:+.2f}")
        print(f"P/L Rate: {summary['total_profit_rate']:+.2f}%")
        print(f"Available: ${summary['available_amount']:.2f}")
