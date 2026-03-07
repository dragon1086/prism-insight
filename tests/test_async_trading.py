"""
Async trading API test script

Cautions:
- Run this test only in demo trading environment
- Actual trading may occur, so set stock code and amount carefully
- Check config/kis_devlp.yaml file settings before testing
"""

import asyncio
import sys
from pathlib import Path
import logging
from typing import List, Dict, Any

# Path setup for importing trading module from parent directory
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from trading.domestic_stock_trading import AsyncTradingContext

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AsyncTradingTester:
    """Async trading API tester"""

    def __init__(self, mode: str = "demo", buy_amount: int = 100000):
        """
        Initialize

        Args:
            mode: "demo" (demo trading) or "real" (real trading) - Test must use demo!
            buy_amount: Buy amount (set to small amount for testing)
        """
        if mode != "demo" and mode != "real":
            raise ValueError("mode must be 'demo' or 'real'!")

        self.mode = mode
        self.buy_amount = buy_amount
        logger.info(f"Tester initialized: mode={mode}, buy_amount={buy_amount:,} KRW")

    async def test_single_buy(self, stock_code: str = "061040") -> Dict[str, Any]:
        """Single buy test"""
        logger.info(f"=== Single buy test started: {stock_code} ===")

        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                result = await trader.async_buy_stock(stock_code, timeout=30.0)

                if result['success']:
                    logger.info(f"✅ Buy success: {result['message']}")
                else:
                    logger.warning(f"❌ Buy failed: {result['message']}")

                return result

            except Exception as e:
                logger.error(f"Error during single buy test: {e}")
                return {'success': False, 'error': str(e)}

    async def test_single_sell(self, stock_code: str = "061040") -> Dict[str, Any]:
        """Single sell test"""
        logger.info(f"=== Single sell test started: {stock_code} ===")

        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                result = await trader.async_sell_stock(stock_code, timeout=30.0)

                if result['success']:
                    logger.info(f"✅ Sell success: {result['message']}")
                else:
                    logger.warning(f"❌ Sell failed: {result['message']}")
                
                return result
                
            except Exception as e:
                logger.error(f"Error during single sell test: {e}")
                return {'success': False, 'error': str(e)}

    async def test_portfolio_check(self) -> Dict[str, Any]:
        """Portfolio check test"""
        logger.info("=== Portfolio check test started ===")

        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                # Check portfolio
                portfolio = await asyncio.to_thread(trader.get_portfolio)
                summary = await asyncio.to_thread(trader.get_account_summary)

                logger.info(f"📊 Holdings count: {len(portfolio)}")

                if summary:
                    logger.info(f"💰 Total valuation: {summary.get('total_eval_amount', 0):,.0f} KRW")
                    logger.info(f"📈 Total P&L: {summary.get('total_profit_amount', 0):+,.0f} KRW")

                # Display holdings details
                for i, stock in enumerate(portfolio[:5]):  # Show max 5
                    logger.info(f"  {i+1}. {stock['stock_name']}({stock['stock_code']}): "
                              f"{stock['quantity']} shares, P&L: {stock['profit_rate']:+.2f}%")
                
                return {
                    'success': True,
                    'portfolio_count': len(portfolio),
                    'portfolio': portfolio,
                    'summary': summary
                }


            except Exception as e:
                logger.error(f"Error during portfolio check test: {e}")
                return {'success': False, 'error': str(e)}

    async def test_batch_operations(self, stock_codes: List[str] = None) -> Dict[str, Any]:
        """Batch trading test"""
        if stock_codes is None:
            stock_codes = ["061040", "100130"]  # RF Tech, Dongguk S&C (small amount test)

        logger.info(f"=== Batch trading test started: {stock_codes} ===")

        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            try:
                # Step 1: Batch buy
                logger.info("🔄 Executing batch buy...")
                buy_tasks = [
                    trader.async_buy_stock(code, timeout=45.0)
                    for code in stock_codes
                ]

                buy_results = await asyncio.gather(*buy_tasks, return_exceptions=True)

                # Analyze buy results
                successful_buys = []
                for i, result in enumerate(buy_results):
                    if isinstance(result, Exception):
                        logger.error(f"[{stock_codes[i]}] Exception during buy: {result}")
                    elif result.get('success'):
                        successful_buys.append(result)
                        logger.info(f"[{result['stock_code']}] Buy successful")
                    else:
                        logger.warning(f"[{stock_codes[i]}] Buy failed: {result.get('message', 'Unknown error')}")

                logger.info(f"✅ Batch buy completed: {len(successful_buys)} successful")

                # Step 2: Wait briefly
                if successful_buys:
                    logger.info("⏰ Waiting 3 seconds...")
                    await asyncio.sleep(3)

                    # Step 3: Batch sell
                    logger.info("🔄 Executing batch sell...")
                    successful_codes = [r['stock_code'] for r in successful_buys]

                    sell_tasks = [
                        trader.async_sell_stock(code, timeout=45.0)
                        for code in successful_codes
                    ]

                    sell_results = await asyncio.gather(*sell_tasks, return_exceptions=True)

                    # Analyze sell results
                    successful_sells = []
                    for i, result in enumerate(sell_results):
                        if isinstance(result, Exception):
                            logger.error(f"[{successful_codes[i]}] Exception during sell: {result}")
                        elif result.get('success'):
                            successful_sells.append(result)
                            logger.info(f"[{result['stock_code']}] Sell successful")
                        else:
                            logger.warning(f"[{successful_codes[i]}] Sell failed: {result.get('message', 'Unknown error')}")

                    logger.info(f"✅ Batch sell completed: {len(successful_sells)} successful")
                
                return {
                    'success': True,
                    'buy_results': buy_results,
                    'sell_results': sell_results if successful_buys else [],
                    'summary': {
                        'total_requested': len(stock_codes),
                        'buy_success': len(successful_buys),
                        'sell_success': len(successful_sells) if successful_buys else 0
                    }
                }


            except Exception as e:
                logger.error(f"Error during batch trading test: {e}")
                return {'success': False, 'error': str(e)}

    async def test_error_handling(self) -> Dict[str, Any]:
        """Error handling test"""
        logger.info("=== Error handling test started ===")

        async with AsyncTradingContext(self.mode, self.buy_amount) as trader:
            results = {}

            # 1. Invalid stock code buy test
            logger.info("🧪 Invalid stock code buy test...")
            try:
                invalid_result = await trader.async_buy_stock("999999", timeout=10.0)
                results['invalid_buy'] = invalid_result
                logger.info(f"Invalid stock code result: {invalid_result['message']}")
            except Exception as e:
                results['invalid_buy'] = {'error': str(e)}
                logger.error(f"Invalid stock code test error: {e}")

            # 2. Sell non-held stock test
            logger.info("🧪 Sell non-held stock test...")
            try:
                no_holding_result = await trader.async_sell_stock("005490", timeout=10.0)  # POSCO Holdings
                results['no_holding_sell'] = no_holding_result
                logger.info(f"Non-held stock sell result: {no_holding_result['message']}")
            except Exception as e:
                results['no_holding_sell'] = {'error': str(e)}
                logger.error(f"Non-held stock sell test error: {e}")

            # 3. Timeout test (very short timeout)
            logger.info("🧪 Timeout test...")
            try:
                timeout_result = await trader.async_buy_stock("061040", timeout=0.001)  # 1ms timeout
                results['timeout_test'] = timeout_result
                logger.info(f"Timeout test result: {timeout_result['message']}")
            except Exception as e:
                results['timeout_test'] = {'error': str(e)}
                logger.error(f"Timeout test error: {e}")
            
            return {'success': True, 'tests': results}

    async def run_basic_tests(self, mode: str = None) -> Dict[str, Any]:
        """
        Run basic tests (class method)

        Args:
            mode: Test mode (uses initialized mode if None)
        """
        # Use mode parameter if given, otherwise use instance mode
        test_mode = mode if mode is not None else self.mode

        if test_mode == "real":
            logger.warning("⚠️ Running test in real trading mode!")
            confirmation = input("Do you really want to test in real trading mode? (yes/no): ")
            if confirmation.lower() != "yes":
                return {"success": False, "message": "User cancelled real trading test."}

        logger.info(f"🚀 Starting async trading API basic tests (mode: {test_mode})")

        results = {}

        try:
            # Create test tester (use mode parameter)
            test_tester = AsyncTradingTester(mode=test_mode, buy_amount=self.buy_amount)

            # 1. Portfolio check test
            portfolio_result = await test_tester.test_portfolio_check()
            results['portfolio'] = portfolio_result
            print(f"\n1️⃣ Portfolio check: {'Success' if portfolio_result['success'] else 'Failed'}")

            # 2. Single buy test
            buy_result = await test_tester.test_single_buy("061040")
            results['buy'] = buy_result
            print(f"\n2️⃣ Single buy: {'Success' if buy_result['success'] else 'Failed'}")

            if buy_result['success']:
                # 3. Single sell test
                await asyncio.sleep(2)
                sell_result = await test_tester.test_single_sell("061040")
                results['sell'] = sell_result
                print(f"\n3️⃣ Single sell: {'Success' if sell_result['success'] else 'Failed'}")

            # 4. Error handling test
            error_result = await test_tester.test_error_handling()
            results['error_handling'] = error_result
            print(f"\n4️⃣ Error handling test: {'Success' if error_result['success'] else 'Failed'}")

            results['success'] = True
            results['test_mode'] = test_mode

        except Exception as e:
            logger.error(f"Error during basic test execution: {e}")
            results['success'] = False
            results['error'] = str(e)

        logger.info("✅ Basic tests completed")
        return results

    async def run_batch_tests(self, mode: str = None) -> Dict[str, Any]:
        """
        Run batch tests (class method)

        Args:
            mode: Test mode (uses initialized mode if None)
        """
        test_mode = mode if mode is not None else self.mode

        if test_mode == "real":
            logger.warning("⚠️ Running batch test in real trading mode!")
            confirmation = input("Do you really want to run batch test in real trading mode? (yes/no): ")
            if confirmation.lower() != "yes":
                return {"success": False, "message": "User cancelled real trading batch test."}

        logger.info(f"🚀 Starting async trading API batch tests (mode: {test_mode})")

        try:
            # Create test tester
            test_tester = AsyncTradingTester(mode=test_mode, buy_amount=30000)  # Smaller amount for batch

            # Batch trading test
            batch_result = await test_tester.test_batch_operations(["061040", "100130"])
            print(f"\n🔄 Batch trading test: {'Success' if batch_result['success'] else 'Failed'}")

            if batch_result['success']:
                summary = batch_result['summary']
                print(f"   - Requested: {summary['total_requested']}")
                print(f"   - Buy success: {summary['buy_success']}")
                print(f"   - Sell success: {summary['sell_success']}")

            batch_result['test_mode'] = test_mode
            return batch_result

        except Exception as e:
            logger.error(f"Error during batch test execution: {e}")
            return {"success": False, "error": str(e), "test_mode": test_mode}


async def main():
    """Main test function"""
    print("="*60)
    print("🧪 Async Trading API Test Script")
    print("="*60)
    print("⚠️  Warning: Actual trading will occur in real trading mode!")
    print("="*60)

    try:
        # Mode selection
        print("\nSelect trading mode:")
        print("1. Demo trading (demo) - Safe test")
        print("2. Real trading (real) - ⚠️ Actual trading occurs!")

        mode_choice = input("Select mode (1-2): ").strip()

        if mode_choice == "1":
            mode = "demo"
            print("✅ Demo trading mode selected")
        elif mode_choice == "2":
            mode = "real"
            print("⚠️ Real trading mode selected - Proceed carefully!")
        else:
            print("Please select a valid mode.")
            return

        # Create tester
        tester = AsyncTradingTester(mode=mode, buy_amount=10000)

        # Test option selection
        print("\nSelect test option:")
        print("1. Basic tests (portfolio check, single buy/sell, error handling)")
        print("2. Batch tests (simultaneous buy/sell of multiple stocks)")
        print("3. All tests")
        print("4. Exit")

        choice = input("\nSelection (1-4): ").strip()

        if choice == "1":
            await tester.run_basic_tests()
        elif choice == "2":
            await tester.run_batch_tests()
        elif choice == "3":
            await tester.run_basic_tests()
            print("\n" + "="*40)
            await tester.run_batch_tests()
        elif choice == "4":
            print("Exiting test.")
            return
        else:
            print("Please enter a valid selection.")
            return

    except KeyboardInterrupt:
        print("\n\n🛑 Test interrupted by user.")
    except Exception as e:
        logger.error(f"Error during main test execution: {e}")

    print("\n" + "="*60)
    print("✅ Test script completed")
    print("="*60)


if __name__ == "__main__":
    # Run event loop
    asyncio.run(main())
