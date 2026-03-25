"""
Domestic stock trading module
- Fixed amount purchase per stock
- Market price buy/sell
- Full liquidation sell
"""

import asyncio
import datetime
import logging
import math
import time
from pathlib import Path
from typing import Optional, Dict, List, Any

import yaml

# Path to directory where current file is located
TRADING_DIR = Path(__file__).parent

# kis_auth import (same directory)
import sys
sys.path.insert(0, str(TRADING_DIR))
import kis_auth as ka
from kis_auth import (
    KISAuthError,
    TokenFileError,
    CredentialMismatchError,
    TokenRequestError
)

# Logging setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Load configuration file
CONFIG_FILE = TRADING_DIR / "config" / "kis_devlp.yaml"
with open(CONFIG_FILE, encoding="UTF-8") as f:
    _cfg = yaml.load(f, Loader=yaml.FullLoader)


class DomesticStockTrading:
    """Domestic stock trading class"""

    # Default buy amount per stock
    DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
    # Auto trading enabled flag
    AUTO_TRADING = _cfg["auto_trading"]
    # Default trading environment
    DEFAULT_MODE = _cfg["default_mode"]

    def __init__(
        self,
        mode: str = DEFAULT_MODE,
        buy_amount: int = None,
        auto_trading: bool = AUTO_TRADING,
        account_name: str = None,
        account_index: int = None,
        product_code: str = "01",
    ):
        """
        Initialize

        Args:
            mode: 'demo' (simulated investment) or 'real' (real investment)
            buy_amount: Buy amount per stock (default: refer to yaml file)
            auto_trading: Whether to execute auto trading

        Raises:
            RuntimeError: Authentication failed with detailed error message
        """
        self.mode = mode
        self.env = "vps" if mode == "demo" else "prod"
        self.buy_amount = buy_amount if buy_amount else self.DEFAULT_BUY_AMOUNT
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = str(product_code)

        # Authentication with improved error handling
        try:
            ka.auth(
                svr=self.env,
                product=self.product_code,
                account_name=self.account_name,
                account_index=self.account_index,
            )
        except CredentialMismatchError as e:
            logger.error("=" * 60)
            logger.error("❌ CREDENTIAL MISMATCH DETECTED!")
            logger.error("=" * 60)
            logger.error(f"Mode: {self.mode} (env: {self.env})")
            logger.error(f"Error: {e}")
            logger.error("")
            logger.error("📋 HOW TO FIX:")
            logger.error("   1. Open trading/config/kis_devlp.yaml")
            logger.error(f"   2. For {self.mode} mode:")
            if self.mode == "real":
                logger.error("      - 'my_app' should start with 'PS' (NOT 'PSVT')")
                logger.error("      - 'accounts'에 실전투자 계좌를 올바르게 설정하세요")
            else:
                logger.error("      - 'paper_app' should start with 'PSVT'")
                logger.error("      - 'accounts'에 모의투자 계좌를 올바르게 설정하세요")
            logger.error("=" * 60)
            raise RuntimeError(f"Credential mismatch for {self.mode} mode: {e}") from e

        except TokenRequestError as e:
            logger.error("=" * 60)
            logger.error("❌ TOKEN REQUEST FAILED!")
            logger.error("=" * 60)
            logger.error(f"Mode: {self.mode} (env: {self.env})")
            logger.error(f"Status Code: {e.status_code}")
            logger.error(f"Error: {e}")
            logger.error("")
            logger.error("📋 POSSIBLE CAUSES:")
            logger.error("   - KIS API server is temporarily unavailable (try again later)")
            logger.error("   - App key/secret are incorrect in kis_devlp.yaml")
            logger.error("   - Network connectivity issue")
            logger.error("   - Rate limit exceeded (wait a few minutes)")
            logger.error("=" * 60)
            raise RuntimeError(f"Token request failed for {self.mode} mode: {e}") from e

        except TokenFileError as e:
            logger.error("=" * 60)
            logger.error("❌ TOKEN FILE ERROR!")
            logger.error("=" * 60)
            logger.error(f"Error: {e}")
            logger.error("")
            logger.error("📋 POSSIBLE CAUSES:")
            logger.error("   - trading/config/ directory permission issue")
            logger.error("   - Disk full")
            logger.error("   - Token file locked by another process")
            logger.error("=" * 60)
            raise RuntimeError(f"Token file error for {self.mode} mode: {e}") from e

        except KISAuthError as e:
            logger.error("=" * 60)
            logger.error("❌ KIS AUTHENTICATION ERROR!")
            logger.error("=" * 60)
            logger.error(f"Mode: {self.mode}, Error: {e}")
            logger.error("📋 Please check kis_devlp.yaml settings.")
            logger.error("=" * 60)
            raise RuntimeError(f"{self.mode} mode authentication failed: {e}") from e

        # Get trading environment
        try:
            self.trenv = ka.getTREnv()
        except RuntimeError as e:
            logger.error("❌ KIS API environment not initialized!")
            logger.error(f"Mode: {self.mode}, Error: {e}")
            logger.error("📋 This usually means authentication failed silently.")
            raise RuntimeError(f"{self.mode} mode authentication failed") from e

        # Additional setup for asynchronous processing
        self._global_lock = asyncio.Lock()  # Global account access control
        self._semaphore = asyncio.Semaphore(3)  # Maximum 3 concurrent requests
        self._stock_locks = {}  # Per-stock locks

        logger.info(f"✅ DomesticStockTrading initialized (Async Enabled)")
        logger.info(f"   Mode: {mode}, Buy Amount: {self.buy_amount:,} KRW")
        logger.info(f"   Account: {self.trenv.my_acct}-{self.trenv.my_prod}")

    def _activate_account(self):
        """Ensure the shared KIS environment matches this trader's account."""
        ka.changeTREnv(
            self.trenv.my_token,
            svr=self.env,
            product=self.trenv.my_prod,
            account_name=self.account_name,
            account_index=self.account_index,
        )

    def _request(self, api_url: str, tr_id: str, params: Dict[str, Any], **kwargs):
        self._activate_account()
        return ka._url_fetch(api_url, tr_id, "", params, **kwargs)

    def get_current_price(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        Get current market price (also used for connectivity test)

        Args:
            stock_code: Stock code (6 digits)

        Returns:
            {
                'stock_code': 'stock code',
                'stock_name': 'stock name',
                'current_price': current price,
                'change_rate': change rate from previous day,
                'volume': trading volume
            }
        """
        api_url = "/uapi/domestic-stock/v1/quotations/inquire-price"
        tr_id = "FHKST01010100"

        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": stock_code
        }

        try:
            res = self._request(api_url, tr_id, params)

            if res.isOK():
                data = res.getBody().output

                result = {
                    'stock_code': stock_code,
                    'stock_name': data.get('rprs_mrkt_kor_name', ''),
                    'current_price': int(data.get('stck_prpr', 0)),  # Current price
                    'change_rate': float(data.get('prdy_ctrt', 0)),  # Change rate from previous day
                    'volume': int(data.get('acml_vol', 0))  # Cumulative volume
                }

                logger.info(f"[{stock_code}] Current price: {result['current_price']:,} KRW ({result['change_rate']:+.2f}%)")
                return result
            else:
                logger.error(f"Failed to get current price: {res.getErrorCode()} - {res.getErrorMessage()}")
                return None

        except Exception as e:
            logger.error(f"Error getting current price: {str(e)}")
            return None

    def calculate_buy_quantity(self, stock_code: str, buy_amount: int = None) -> int:
        """
        Calculate buyable quantity

        Args:
            stock_code: Stock code
            buy_amount: Buy amount (default: amount set during initialization)

        Returns:
            Buyable quantity (0 if cannot buy)
        """
        amount = buy_amount if buy_amount else self.buy_amount

        # Get current price
        current_price_info = self.get_current_price(stock_code)
        if not current_price_info:
            return 0

        current_price = current_price_info['current_price']

        # Calculate buyable quantity (floor division)
        current_quantity = math.floor(amount / current_price)

        if current_quantity == 0:
            logger.warning(f"[{stock_code}] Current price {current_price:,} KRW > Buy amount {amount:,} KRW - Cannot buy")
        else:
            total_amount = current_quantity * current_price
            logger.info(f"[{stock_code}] Can buy: {current_quantity} shares x {current_price:,} KRW = {total_amount:,} KRW")

        return current_quantity

    def buy_market_price(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        Buy at market price

        Args:
            stock_code: Stock code
            buy_amount: Buy amount (default: amount set during initialization)

        Returns:
            {
                'success': Success status,
                'order_no': Order number,
                'stock_code': Stock code,
                'quantity': Order quantity,
                'message': Message
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute buy order. (AUTO_TRADING=False)'
            }


        # Calculate buyable quantity
        buy_quantity = self.calculate_buy_quantity(stock_code, buy_amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Buyable quantity is 0 (current price is higher than buy amount)'
            }

        # Execute buy order
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        # Set TR ID (real/demo distinction)
        if self.mode == "real":
            tr_id = "TTTC0012U"  # Real buy
        else:
            tr_id = "VTTC0012U"  # Demo buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "01",  # 01: Market price
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 0 for market price
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] Market buy order successful: {buy_quantity} shares, order no: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Market buy order completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'Error during buy order: {str(e)}'
            }

    def get_holding_quantity(self, stock_code: str) -> int:
        """
        Get holding quantity for a specific stock

        Args:
            stock_code: Stock code

        Returns:
            Holding quantity (0 if none)
        """
        current_portfolio = self.get_portfolio()

        for current_stock in current_portfolio:
            if current_stock['stock_code'] == stock_code:
                return current_stock['quantity']

        return 0

    def buy_limit_price(self, stock_code: str, limit_price: int, buy_amount: int = None) -> Dict[str, Any]:
        """
        Buy at limit price

        Args:
            stock_code: Stock code
            limit_price: Limit price
            buy_amount: Buy amount (default: amount set during initialization)

        Returns:
            {
                'success': Success status,
                'order_no': Order number,
                'stock_code': Stock code,
                'quantity': Order quantity,
                'limit_price': Limit price,
                'message': Message
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'limit_price': limit_price,
                'message': 'Auto trading is disabled. Cannot execute buy order. (AUTO_TRADING=False)'
            }

        amount = buy_amount if buy_amount else self.buy_amount

        # Calculate buyable quantity (based on limit price)
        buy_quantity = math.floor(amount / limit_price)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'limit_price': limit_price,
                'message': f'Buyable quantity is 0 (limit price {limit_price:,} KRW > buy amount {amount:,} KRW)'
            }

        # Execute limit price buy order
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0012U"  # Real buy
        else:
            tr_id = "VTTC0012U"  # Demo buy

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "00",  # 00: Limit price
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": str(limit_price),  # Limit price
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] Limit buy order successful: {buy_quantity} shares x {limit_price:,} KRW, order no: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Limit buy order completed ({buy_quantity} shares x {limit_price:,} KRW)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Limit buy order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'limit_price': limit_price,
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during limit buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'limit_price': limit_price,
                'message': f'Error during buy order: {str(e)}'
            }

    def smart_buy(self, stock_code: str, buy_amount: int = None, limit_price: int = None) -> Dict[str, Any]:
        """
        Automatically buy using the optimal method based on time (excluding after-hours single price trading due to high unfilled probability)

        - 09:00~15:30: Market price buy
        - 15:40~16:00: After-hours closing price trading
        - Other times: Reserved order (next day limit price if limit_price provided)

        Args:
            stock_code: Stock code
            buy_amount: Buy amount (default: amount set during initialization)
            limit_price: Limit price for reserved order (market order if None)

        Returns:
            Buy result
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute buy order. (AUTO_TRADING=False)'
            }

        now = datetime.datetime.now()
        current_time = now.time()

        # Branch by time period
        if datetime.time(9, 0) <= current_time <= datetime.time(15, 30):
            # Regular trading hours
            logger.info(f"[{stock_code}] Regular trading hours - executing market buy")
            return self.buy_market_price(stock_code, buy_amount)

        elif datetime.time(15, 40) <= current_time <= datetime.time(16, 0):
            # After-hours closing price trading
            logger.info(f"[{stock_code}] After-hours closing price time - executing closing price buy")
            return self.buy_closing_price(stock_code, buy_amount)

        else:
            # Reserved order (limit or market price)
            if limit_price:
                logger.info(f"[{stock_code}] Outside trading hours - executing reserved order (limit: {limit_price:,} KRW)")
            else:
                logger.info(f"[{stock_code}] Outside trading hours - executing reserved order (market)")
            return self.buy_reserved_order(stock_code, buy_amount, limit_price=limit_price)

    def buy_closing_price(self, stock_code: str, buy_amount: int = None) -> Dict[str, Any]:
        """
        Buy at after-hours closing price (15:40~16:00)
        Buy at closing price of the day

        Args:
            stock_code: Stock code
            buy_amount: Buy amount (default: amount set during initialization)

        Returns:
            Buy result
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute buy order. (AUTO_TRADING=False)'
            }

        # Calculate buyable quantity
        buy_quantity = self.calculate_buy_quantity(stock_code, buy_amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Buyable quantity is 0'
            }

        # After-hours closing price buy
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0012U"
        else:
            tr_id = "VTTC0012U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "06",  # 06: Post-market after-hours (장후 시간외, 15:40~16:00)
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 0 for closing price trading
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "",
            "CNDT_PRIC": ""
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] After-hours closing price buy order successful: {buy_quantity} shares, order no: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'After-hours closing price buy order completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"After-hours closing price buy failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Buy order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during after-hours closing price buy: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'Error during buy order: {str(e)}'
            }

    def buy_reserved_order(self, stock_code: str, buy_amount: int = None, end_date: str = None, limit_price: int = None) -> Dict[str, Any]:
        """
        Buy with reserved order (auto-execute on next trading day)
        Reserved order available: 15:40~next business day 07:30 (excluding 23:40~00:10)

        Args:
            stock_code: Stock code
            buy_amount: Buy amount (default: amount set during initialization)
            end_date: Period reservation end date (YYYYMMDD format, regular reservation if None)
            limit_price: Limit price (market order if None)

        Returns:
            Buy result
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute buy order. (AUTO_TRADING=False)'
            }

        amount = buy_amount if buy_amount else self.buy_amount

        # Set order type and unit price
        if limit_price and limit_price > 0:
            ord_dvsn_cd = "00"  # Limit price
            ord_unpr = str(int(limit_price))
            # Calculate quantity based on limit price (must be int for API)
            buy_quantity = int(amount // limit_price)
            logger.info(f"[{stock_code}] Reserved order limit price: {int(limit_price):,} KRW, quantity: {buy_quantity} shares")
        else:
            ord_dvsn_cd = "01"  # Market price
            ord_unpr = "0"
            # For market price, calculate quantity based on current price
            buy_quantity = self.calculate_buy_quantity(stock_code, amount)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Buyable quantity is 0'
            }

        # Call reserved order API
        api_url = "/uapi/domestic-stock/v1/trading/order-resv"
        tr_id = "CTSC0008U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_QTY": str(int(buy_quantity)),  # Must be integer string for KIS API
            "ORD_UNPR": ord_unpr,
            "SLL_BUY_DVSN_CD": "02",  # 02: Buy
            "ORD_DVSN_CD": ord_dvsn_cd,
            "ORD_OBJT_CBLC_DVSN_CD": "10",  # 10: Cash
            "LOAN_DT": "",
            "LDNG_DT": ""
        }

        # Add end date for period reservation
        if end_date:
            params["RSVN_ORD_END_DT"] = end_date
        else:
            params["RSVN_ORD_END_DT"] = ""

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('RSVN_ORD_SEQ', '')  # Reserved order receipt number

                order_type_str = {
                    "01": "Market",
                    "00": f"Limit({ord_unpr} KRW)",
                    "05": "Pre-market after-hours"
                }.get(ord_dvsn_cd, "")

                period_str = f"Period reservation(~{end_date})" if end_date else "Regular reservation"

                logger.info(f"[{stock_code}] Reserved buy order successful: {buy_quantity} shares, {order_type_str}, {period_str}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'order_type': order_type_str,
                    'period_type': period_str,
                    'message': f'Reserved buy order completed ({buy_quantity} shares, {order_type_str}, {period_str})'
                }
            else:
                # Reserved order failed - do NOT fallback to market (doesn't work outside hours)
                # Market buy will fail with APBK0918 "장운영시간이 아닙니다" outside trading hours
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved buy order failed: {error_msg}")
                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f"Reserved order failed: {error_msg}"
                }

        except Exception as e:
            logger.error(f"Error during reserved buy order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f"Error during reserved buy order: {str(e)}"
            }

    def sell_all_market_price(self, stock_code: str) -> Dict[str, Any]:
        """
        Sell all at market price (liquidate entire holding)

        Args:
            stock_code: Stock code

        Returns:
            {
                'success': Success status,
                'order_no': Order number,
                'stock_code': Stock code,
                'quantity': Sell quantity,
                'message': Message
            }
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute sell order. (AUTO_TRADING=False)'
            }

        # Check holding quantity
        buy_quantity = self.get_holding_quantity(stock_code)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'No holding quantity'
            }

        # Execute sell order
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        # Set TR ID (real/demo distinction)
        if self.mode == "real":
            tr_id = "TTTC0011U"  # Real sell
        else:
            tr_id = "VTTC0011U"  # Demo sell

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "01",  # 01: Market price
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 0 for market price
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",  # 01: Regular sell
            "CNDT_PRIC": ""
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] Market sell all order successful: {buy_quantity} shares, order no: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Market sell all order completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Sell order failed: {error_msg}")

                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Sell order failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"Error during sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'Error during sell order: {str(e)}'
            }

    def smart_sell_all(self, stock_code: str, limit_price: int = None) -> Dict[str, Any]:
        """
        Automatically sell all using the optimal method based on time (excluding after-hours single price trading due to high unfilled probability)

        - 09:00~15:30: Market price sell
        - 15:40~16:00: After-hours closing price trading
        - Other times: Reserved order (next day limit price if limit_price provided)

        Args:
            stock_code: Stock code
            limit_price: Limit price for reserved order (market order if None)

        Returns:
            Sell result
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute sell order. (AUTO_TRADING=False)'
            }

        now = datetime.datetime.now()
        current_time = now.time()

        # Branch by time period
        if datetime.time(9, 0) <= current_time <= datetime.time(15, 30):
            # Regular trading hours - market sell
            logger.info(f"[{stock_code}] Regular trading hours - executing market sell")
            return self.sell_all_market_price(stock_code)

        elif datetime.time(15, 40) <= current_time <= datetime.time(16, 0):
            # After-hours closing price trading
            logger.info(f"[{stock_code}] After-hours closing price time - executing closing price sell")
            return self.sell_all_closing_price(stock_code)

        else:
            # Reserved order (limit or market price)
            if limit_price:
                logger.info(f"[{stock_code}] Outside trading hours - executing reserved order (limit: {limit_price:,} KRW)")
            else:
                logger.info(f"[{stock_code}] Outside trading hours - executing reserved order (market)")
            return self.sell_all_reserved_order(stock_code, limit_price=limit_price)

    def sell_all_closing_price(self, stock_code: str) -> Dict[str, Any]:
        """
        Sell all at after-hours closing price (15:40~16:00)
        Sell at closing price of the day
        """
        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute sell order. (AUTO_TRADING=False)'
            }

        # Check holding quantity
        buy_quantity = self.get_holding_quantity(stock_code)

        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'No holding quantity'
            }

        # After-hours closing price sell
        api_url = "/uapi/domestic-stock/v1/trading/order-cash"

        if self.mode == "real":
            tr_id = "TTTC0011U"
        else:
            tr_id = "VTTC0011U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_DVSN": "06",  # 06: Post-market after-hours
            "ORD_QTY": str(buy_quantity),
            "ORD_UNPR": "0",  # 0 for closing price trading
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01",
            "CNDT_PRIC": ""
        }

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('odno', '')

                logger.info(f"[{stock_code}] After-hours closing price sell order successful: {buy_quantity} shares, order no: {order_no}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'After-hours closing price sell completed ({buy_quantity} shares)'
                }
            else:
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f'Sell failed: {error_msg}'
                }

        except Exception as e:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f'Error during sell: {str(e)}'
            }

    def sell_all_reserved_order(self, stock_code: str, end_date: str = None, limit_price: int = None) -> Dict[str, Any]:
        """
        Sell all with reserved order (auto-execute on next trading day)
        Reserved order available: 15:40~next business day 07:30 (excluding 23:40~00:10)

        Args:
            stock_code: Stock code
            end_date: Period reservation end date (YYYYMMDD format, regular reservation if None)
            limit_price: Limit price (market order if None)

        Returns:
            Sell result
        """

        if not self.auto_trading:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'Auto trading is disabled. Cannot execute sell order. (AUTO_TRADING=False)'
            }

        # Check holding quantity
        buy_quantity = self.get_holding_quantity(stock_code)
        if buy_quantity == 0:
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': 0,
                'message': 'No holding quantity'
            }

        # Set order type and unit price
        if limit_price and limit_price > 0:
            ord_dvsn_cd = "00"  # Limit price
            ord_unpr = str(int(limit_price))
            logger.info(f"[{stock_code}] Reserved sell order limit price: {int(limit_price):,} KRW, quantity: {buy_quantity} shares")
        else:
            ord_dvsn_cd = "01"  # Market price
            ord_unpr = "0"

        # Call reserved order API
        api_url = "/uapi/domestic-stock/v1/trading/order-resv"
        tr_id = "CTSC0008U"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "PDNO": stock_code,
            "ORD_QTY": str(int(buy_quantity)),  # Must be integer string for KIS API
            "ORD_UNPR": ord_unpr,
            "SLL_BUY_DVSN_CD": "01",  # 01: Sell
            "ORD_DVSN_CD": ord_dvsn_cd,
            "ORD_OBJT_CBLC_DVSN_CD": "10",  # 10: Cash
            "LOAN_DT": "",
            "LDNG_DT": ""
        }

        # Add end date for period reservation
        if end_date:
            params["RSVN_ORD_END_DT"] = end_date
        else:
            params["RSVN_ORD_END_DT"] = ""

        try:
            res = self._request(api_url, tr_id, params, postFlag=True)

            if res.isOK():
                output = res.getBody().output
                order_no = output.get('RSVN_ORD_SEQ', '')  # Reserved order receipt number

                order_type_str = {
                    "01": "Market",
                    "00": f"Limit({ord_unpr} KRW)",
                    "05": "Pre-market after-hours"
                }.get(ord_dvsn_cd, "")

                period_str = f"Period reservation(~{end_date})" if end_date else "Regular reservation"

                logger.info(f"[{stock_code}] Reserved sell order successful: {buy_quantity} shares, {order_type_str}, {period_str}")

                return {
                    'success': True,
                    'order_no': order_no,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'order_type': order_type_str,
                    'period_type': period_str,
                    'message': f'Reserved sell order completed ({buy_quantity} shares, {order_type_str}, {period_str})'
                }
            else:
                # Reserved order failed - do NOT fallback to market (doesn't work outside hours)
                # Market sell will fail with APBK0918 "장운영시간이 아닙니다" outside trading hours
                error_msg = f"{res.getErrorCode()} - {res.getErrorMessage()}"
                logger.error(f"Reserved sell order failed: {error_msg}")
                return {
                    'success': False,
                    'order_no': None,
                    'stock_code': stock_code,
                    'quantity': buy_quantity,
                    'message': f"Reserved order failed: {error_msg}"
                }

        except Exception as e:
            logger.error(f"Error during reserved sell order: {str(e)}")
            return {
                'success': False,
                'order_no': None,
                'stock_code': stock_code,
                'quantity': buy_quantity,
                'message': f"Error during reserved sell order: {str(e)}"
            }

    async def _get_stock_lock(self, stock_code: str) -> asyncio.Lock:
        """Return per-stock lock (prevent concurrent trading)"""
        if stock_code not in self._stock_locks:
            self._stock_locks[stock_code] = asyncio.Lock()
        return self._stock_locks[stock_code]

    async def async_buy_stock(self, stock_code: str, buy_amount: Optional[int] = None, timeout: float = 30.0, limit_price: Optional[int] = None) -> Dict[str, Any]:
        """
        Async buy API (with timeout)
        Get current price → Calculate buyable quantity → Market buy

        Args:
            stock_code: Stock code (6 digits)
            buy_amount: Buy amount (default: amount set during initialization)
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order (market order if None)

        Returns:
            {
                'success': Success status,
                'stock_code': Stock code,
                'current_price': Current price at buy time,
                'quantity': Buy quantity,
                'total_amount': Total buy amount,
                'order_no': Order number,
                'message': Result message,
                'timestamp': Execution time
            }
        """
        try:
            return await asyncio.wait_for(
                self._execute_buy_stock(stock_code, buy_amount, limit_price),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'stock_code': stock_code,
                'current_price': 0,
                'quantity': 0,
                'total_amount': 0,
                'order_no': None,
                'message': f'Buy request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_buy_stock(self, stock_code: str, buy_amount: int = None, limit_price: int = None) -> Dict[str, Any]:
        # Use class default if buy_amount is None
        amount = buy_amount if buy_amount else self.buy_amount

        result = {
            'success': False,
            'stock_code': stock_code,
            'current_price': 0,
            'quantity': 0,
            'total_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        # 3-level protection: per-stock lock + semaphore + global lock
        stock_lock = await self._get_stock_lock(stock_code)

        async with stock_lock:  # Level 1: Prevent concurrent trading per stock
            async with self._semaphore:  # Level 2: Limit total concurrent requests
                async with self._global_lock:  # Level 3: Protect account information
                    try:
                        logger.info(f"[Async Buy API] {stock_code} buy process started (amount: {amount:,} KRW)")

                        # Step 1: Get current price
                        current_price_info = await asyncio.to_thread(
                            self.get_current_price, stock_code
                        )
                        # Prevent rate limit
                        await asyncio.sleep(0.5)

                        if not current_price_info:
                            result['message'] = 'Failed to get current price'
                            logger.error(f"[Async Buy API] {stock_code} failed to get current price")
                            return result

                        result['current_price'] = current_price_info['current_price']

                        # Step 2: Calculate buyable quantity (use amount)
                        current_price = current_price_info['current_price']
                        buy_quantity = math.floor(amount / current_price)

                        if buy_quantity == 0:
                            result['message'] = f'Buyable quantity is 0 (buy amount: {amount:,} KRW)'
                            logger.warning(f"[Async Buy API] {stock_code} buyable quantity 0")
                            return result

                        result['quantity'] = buy_quantity
                        result['total_amount'] = buy_quantity * current_price_info['current_price']

                        # Step 3: Execute buy (use amount, limit price if provided)
                        # Use current_price as limit_price fallback for reserved orders (outside market hours)
                        # CRITICAL: Convert to int - KIS API requires integer strings, not float strings ("30800" not "30800.0")
                        effective_limit_price = int(limit_price) if (limit_price and limit_price > 0) else int(current_price)

                        # Prevent rate limit
                        await asyncio.sleep(0.5)
                        if limit_price:
                            logger.info(f"[Async Buy API] {stock_code} executing reserved buy order: {buy_quantity} shares x {effective_limit_price:,} KRW (limit)")
                        else:
                            logger.info(f"[Async Buy API] {stock_code} executing with effective limit price: {buy_quantity} shares x {effective_limit_price:,} KRW")
                        buy_result = await asyncio.to_thread(
                            self.smart_buy, stock_code, amount, effective_limit_price
                        )

                        if buy_result['success']:
                            result['success'] = True
                            result['order_no'] = buy_result['order_no']
                            result['message'] = f"Buy completed: {buy_quantity} shares x {current_price_info['current_price']:,} KRW = {result['total_amount']:,} KRW"
                            logger.info(f"[Async Buy API] {stock_code} buy successful")
                        else:
                            result['message'] = f"Buy failed: {buy_result['message']}"
                            logger.error(f"[Async Buy API] {stock_code} buy failed: {buy_result['message']}")

                    except Exception as e:
                        result['message'] = f'Error during async buy API execution: {str(e)}'
                        logger.error(f"[Async Buy API] {stock_code} error: {str(e)}")

                    # Delay to prevent API overload
                    await asyncio.sleep(0.1)

        return result

    async def async_sell_stock(self, stock_code: str, timeout: float = 30.0, limit_price: Optional[int] = None) -> Dict[str, Any]:
        """
        Async sell API (with timeout)
        Sell all holding quantity at market price

        Args:
            stock_code: Stock code (6 digits)
            timeout: Timeout in seconds
            limit_price: Limit price for reserved order (market order if None)

        Returns:
            {
                'success': Success status,
                'stock_code': Stock code,
                'current_price': Current price at sell time,
                'quantity': Sell quantity,
                'estimated_amount': Estimated sell amount,
                'order_no': Order number,
                'message': Result message,
                'timestamp': Execution time
            }
        """
        try:
            return await asyncio.wait_for(
                self._execute_sell_stock(stock_code, limit_price),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            return {
                'success': False,
                'stock_code': stock_code,
                'current_price': 0,
                'quantity': 0,
                'estimated_amount': 0,
                'order_no': None,
                'message': f'Sell request timeout ({timeout}s)',
                'timestamp': datetime.datetime.now().isoformat()
            }

    async def _execute_sell_stock(self, stock_code: str, limit_price: int = None) -> Dict[str, Any]:
        """Actual sell execution logic (includes portfolio verification defensive logic)"""
        result = {
            'success': False,
            'stock_code': stock_code,
            'current_price': 0,
            'quantity': 0,
            'estimated_amount': 0,
            'order_no': None,
            'message': '',
            'timestamp': datetime.datetime.now().isoformat()
        }

        # 3-level protection: per-stock lock + semaphore + global lock
        stock_lock = await self._get_stock_lock(stock_code)

        async with stock_lock:  # Level 1: Prevent concurrent trading per stock
            async with self._semaphore:  # Level 2: Limit total concurrent requests
                async with self._global_lock:  # Level 3: Protect account information
                    try:
                        logger.info(f"[Async Sell API] {stock_code} sell process started")

                        # Defensive logic 1: Verify holding in portfolio
                        logger.info(f"[Async Sell API] {stock_code} checking portfolio...")
                        current_portfolio = await asyncio.to_thread(self.get_portfolio)

                        # Check if stock exists in portfolio
                        target_stock = None
                        for current_stock in current_portfolio:
                            if current_stock['stock_code'] == stock_code:
                                target_stock = current_stock
                                break

                        if not target_stock:
                            result['message'] = f'Stock {stock_code} not found in portfolio'
                            logger.warning(f"[Async Sell API] {stock_code} not in portfolio")
                            return result

                        if target_stock['quantity'] <= 0:
                            result['message'] = f'{stock_code} holding quantity is 0'
                            logger.warning(f"[Async Sell API] {stock_code} holding quantity 0")
                            return result

                        logger.info(f"[Async Sell API] {stock_code} holding confirmed: {target_stock['quantity']} shares")

                        # Get current price (for estimated sell amount calculation)
                        current_price_info = await asyncio.to_thread(
                            self.get_current_price, stock_code
                        )

                        if current_price_info:
                            result['current_price'] = current_price_info['current_price']
                            logger.info(f"[Async Sell API] {stock_code} current price: {current_price_info['current_price']:,} KRW")

                        # Defensive logic 2: Check holding quantity once more before selling
                        holding_quantity = await asyncio.to_thread(
                            self.get_holding_quantity, stock_code
                        )

                        if holding_quantity <= 0:
                            result['message'] = f'{stock_code} holding quantity is 0 at final check'
                            logger.warning(f"[Async Sell API] {stock_code} holding quantity 0 at final check")
                            return result

                        # Execute sell all
                        # Use current_price as limit_price fallback for reserved orders (outside market hours)
                        # CRITICAL: Convert to int - KIS API requires integer strings, not float strings
                        effective_limit_price = int(limit_price) if (limit_price and limit_price > 0) else (int(result['current_price']) if result['current_price'] > 0 else None)

                        if effective_limit_price:
                            logger.info(f"[Async Sell API] {stock_code} executing sell all (holding: {holding_quantity} shares, limit: {effective_limit_price:,} KRW)")
                        else:
                            logger.info(f"[Async Sell API] {stock_code} executing sell all (holding: {holding_quantity} shares, market)")
                        all_sell_result = await asyncio.to_thread(
                            self.smart_sell_all, stock_code, effective_limit_price
                        )

                        if all_sell_result['success']:
                            result['success'] = True
                            result['quantity'] = all_sell_result['quantity']
                            result['order_no'] = all_sell_result['order_no']

                            # Calculate estimated sell amount
                            if result['current_price'] > 0:
                                result['estimated_amount'] = result['quantity'] * result['current_price']

                            # Add portfolio information
                            result['avg_price'] = target_stock['avg_price']
                            result['profit_amount'] = target_stock['profit_amount']
                            result['profit_rate'] = target_stock['profit_rate']

                            result['message'] = (f"Sell completed: {result['quantity']} shares "
                                                 f"(avg price: {result['avg_price']:,.0f} KRW, "
                                                 f"estimated amount: {result['estimated_amount']:,} KRW, "
                                                 f"return: {result['profit_rate']:+.2f}%)")

                            logger.info(f"[Async Sell API] {stock_code} sell successful")
                        else:
                            result['message'] = f"Sell failed: {all_sell_result['message']}"
                            logger.error(f"[Async Sell API] {stock_code} sell failed: {all_sell_result['message']}")

                    except Exception as e:
                        result['message'] = f'Error during async sell API execution: {str(e)}'
                        logger.error(f"[Async Sell API] {stock_code} error: {str(e)}")

                    # Delay to prevent API overload
                    await asyncio.sleep(0.1)

        return result

    def get_portfolio(self) -> List[Dict[str, Any]]:
        """
        Get current account portfolio

        Returns:
            [{
                'stock_code': 'stock code',
                'stock_name': 'stock name',
                'quantity': holding quantity,
                'avg_price': average price,
                'current_price': current price,
                'eval_amount': evaluation amount,
                'profit_amount': profit/loss amount,
                'profit_rate': return rate (%)
            }, ...]
        """
        api_url = "/uapi/domestic-stock/v1/trading/inquire-balance"

        # Set TR ID (real/demo distinction)
        if self.mode == "real":
            tr_id = "TTTC8434R"
        else:
            tr_id = "VTTC8434R"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            res = self._request(api_url, tr_id, params)

            if res.isOK():
                current_portfolio = []
                output1 = res.getBody().output1  # Holdings list
                output2 = res.getBody().output2[0]  # Account summary

                # Handle case when output1 is not a list
                if not isinstance(output1, list):
                    output1 = [output1] if output1 else []

                for item in output1:
                    # Only add stocks with quantity > 0
                    quantity = int(item.get('hldg_qty', 0))
                    if quantity > 0:
                        stock_info = {
                            'stock_code': item.get('pdno', ''),
                            'stock_name': item.get('prdt_name', ''),
                            'quantity': quantity,
                            'avg_price': float(item.get('pchs_avg_pric', 0)),
                            'current_price': float(item.get('prpr', 0)),
                            'eval_amount': float(item.get('evlu_amt', 0)),
                            'profit_amount': float(item.get('evlu_pfls_amt', 0)),
                            'profit_rate': float(item.get('evlu_pfls_rt', 0))
                        }
                        current_portfolio.append(stock_info)

                # Log account summary
                if output2:
                    total_eval = float(output2.get('tot_evlu_amt', 0))
                    total_profit = float(output2.get('evlu_pfls_smtl_amt', 0))
                    logger.info(f"Account total evaluation: {total_eval:,.0f} KRW, total profit/loss: {total_profit:+,.0f} KRW")

                logger.info(f"Portfolio: {len(current_portfolio)} holdings")
                return current_portfolio

            else:
                logger.error(f"Balance inquiry failed: {res.getErrorCode()} - {res.getErrorMessage()}")
                return []

        except Exception as e:
            logger.error(f"Error during balance inquiry: {str(e)}")
            return []

    def get_account_summary(self) -> None | dict[Any, Any] | dict[str, float]:
        """
        Get account summary information

        Returns:
            {
                'total_eval_amount': total evaluation amount,
                'total_profit_amount': total profit/loss,
                'total_profit_rate': total return rate,
                'deposit': deposit,
                'available_amount': available order amount
            }
        """
        api_url = "/uapi/domestic-stock/v1/trading/inquire-balance"

        # Set TR ID (real/demo distinction)
        if self.mode == "real":
            tr_id = "TTTC8434R"
        else:
            tr_id = "VTTC8434R"

        params = {
            "CANO": self.trenv.my_acct,
            "ACNT_PRDT_CD": self.trenv.my_prod,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            res = self._request(api_url, tr_id, params)

            if res.isOK():
                output2 = res.getBody().output2[0]  # Account summary

                if output2:
                    pchs_amt = float(output2.get('pchs_amt_smtl_amt', 0)) or 1  # Replace 0 with 1

                    # Total evaluation amount and securities evaluation amount
                    tot_evlu_amt = float(output2.get('tot_evlu_amt', 0))
                    scts_evlu_amt = float(output2.get('scts_evlu_amt', 0))
                    dnca_tot_amt = float(output2.get('dnca_tot_amt', 0))

                    # Total cash (including D+2) = Total evaluation amount - Securities evaluation amount
                    # This includes deposit (D+0) + D+1 + D+2 receivables
                    total_cash = tot_evlu_amt - scts_evlu_amt

                    account_summary = {
                        'total_eval_amount': tot_evlu_amt,
                        'total_profit_amount': float(output2.get('evlu_pfls_smtl_amt', 0)),
                        'total_profit_rate': round(float(output2.get('evlu_pfls_smtl_amt', 0)) / pchs_amt * 100, 2),
                        'deposit': dnca_tot_amt,  # Deposit (D+0, same-day withdrawal available)
                        'total_cash': total_cash,  # Total cash (including D+2)
                        'available_amount': float(output2.get('ord_psbl_cash', 0))
                    }

                    logger.info(f"Account summary: Total eval {account_summary['total_eval_amount']:,.0f} KRW, "
                                f"profit/loss {account_summary['total_profit_amount']:+,.0f} KRW "
                                f"({account_summary['total_profit_rate']:+.2f}%), "
                                f"total cash(incl D+2) {account_summary['total_cash']:,.0f} KRW")

                    return account_summary

                return {}

        except Exception as e:
            logger.error(f"Error during account summary inquiry: {str(e)}")
            return {}


# Context manager
class AsyncTradingContext:
    """Async trading context manager (safe resource management)"""
    # Default buy amount unit
    DEFAULT_BUY_AMOUNT = _cfg["default_unit_amount"]
    # Auto trading operation status
    AUTO_TRADING = _cfg["auto_trading"]
    # Default trading environment
    DEFAULT_MODE = _cfg["default_mode"]

    def __init__(
        self,
        mode: str = DEFAULT_MODE,
        buy_amount: int = None,
        auto_trading: bool = AUTO_TRADING,
        account_name: str = None,
        account_index: int = None,
        product_code: str = "01",
    ):
        self.mode = mode
        self.buy_amount = buy_amount
        self.auto_trading = auto_trading
        self.account_name = account_name
        self.account_index = account_index
        self.product_code = product_code
        self.trader = None

    async def __aenter__(self):
        self.trader = DomesticStockTrading(
            mode=self.mode,
            buy_amount=self.buy_amount,
            auto_trading=self.auto_trading,
            account_name=self.account_name,
            account_index=self.account_index,
            product_code=self.product_code,
        )
        return self.trader

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            logger.error(f"AsyncTradingContext error: {exc_type.__name__}: {exc_val}")

# ========== Test Code ==========
if __name__ == "__main__":
    """
    Usage examples and tests
    """

    # 1. Initialize
    trader = DomesticStockTrading()

    # 2. Connectivity test - get current price
    print("\n=== 1. Get Current Price (Connectivity Test) ===")
    price_info = trader.get_current_price("061040")  # RF Tech
    if price_info:
        print(f"Stock name: {price_info['stock_name']}")
        print(f"Current price: {price_info['current_price']:,} KRW")
        print(f"Change rate: {price_info['change_rate']:+.2f}%")

    # 3. Calculate buyable quantity
    print("\n=== 2. Calculate Buyable Quantity ===")
    quantity = trader.calculate_buy_quantity("061040")
    print(f"Buyable quantity: {quantity} shares")

    # 4. Market buy (CAUTION when uncommenting!)
    print("\n=== 3. Market Buy (Execute when uncommented) ===")
    # buy_result = trader.smart_buy(stock_code="061040", buy_amount=trader.buy_amount)
    # print(buy_result)

    # 5. Get portfolio
    print("\n=== 4. Get Portfolio ===")
    portfolio = trader.get_portfolio()
    for stock in portfolio:
        print(f"{stock['stock_name']}({stock['stock_code']}): "
              f"{stock['quantity']} shares, "
              f"avg price: {stock['avg_price']:,.0f} KRW, "
              f"current price: {stock['current_price']:,.0f} KRW, "
              f"return: {stock['profit_rate']:+.2f}%")

    # 6. Account summary
    print("\n=== 5. Account Summary ===")
    summary = trader.get_account_summary()
    if summary:
        print(f"Total evaluation: {summary['total_eval_amount']:,.0f} KRW")
        print(f"Total profit/loss: {summary['total_profit_amount']:+,.0f} KRW")
        print(f"Total return: {summary['total_profit_rate']:+.2f}%")
        print(f"Available order amount: {summary['available_amount']:,.0f} KRW")

    # 7. Sell all (CAUTION when uncommenting!)
    print("\n=== 6. Sell All (Execute when uncommented) ===")
    # sell_result = trader.smart_sell_all("061040")
    # print(sell_result)

# fixme : Delete below comments later
## Unit tests successful (market buy, after-hours sell need testing) -> integrated into trading functions (ok) -> call trading functions in tracking_agent (ok) -> send account summary to Telegram in orchestrator (need testing)
