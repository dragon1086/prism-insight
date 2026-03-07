"""
Quick test script - Simple test of core functions only

Usage:
python quick_test.py [buy|sell|portfolio] [--mode demo|real]
python quick_test.py [buy|sell|portfolio] [demo|real]  # Simple form
"""

import asyncio
import sys
import os
import logging
import argparse

# Import trading module from parent directory
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.domestic_stock_trading import AsyncTradingContext

# Simple logging setup
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)


def parse_arguments():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description='Quick stock trading test',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Usage examples:
  python quick_test.py portfolio              # Check portfolio with demo trading
  python quick_test.py portfolio --mode demo  # Check portfolio with demo trading
  python quick_test.py buy --mode real        # Buy with real trading (caution!)
  python quick_test.py sell real              # Sell with real trading (caution!)
        """
    )

    parser.add_argument(
        'command',
        choices=['buy', 'sell', 'portfolio'],
        help='Command to execute (buy: buy, sell: sell, portfolio: check portfolio)'
    )

    parser.add_argument(
        '--mode',
        choices=['demo', 'real'],
        default='demo',
        help='Trading mode (demo: demo trading, real: real trading, default: demo)'
    )
    
    # Also allow mode as positional argument (backward compatibility)
    parser.add_argument(
        'mode_pos',
        nargs='?',
        choices=['demo', 'real'],
        help='Trading mode (positional argument, same as --mode)'
    )

    args = parser.parse_args()

    # Prioritize mode if given as positional argument
    if args.mode_pos:
        args.mode = args.mode_pos
    
    return args


async def quick_portfolio_check(mode="demo"):
    """Quick portfolio check"""
    print(f"📊 Checking portfolio... (mode: {mode})")

    async with AsyncTradingContext(mode=mode) as trader:
        portfolio = await asyncio.to_thread(trader.get_portfolio)
        summary = await asyncio.to_thread(trader.get_account_summary)

        print(f"\n💼 Holdings: {len(portfolio)}")

        if summary:
            print(f"💰 Total value: {summary.get('total_eval_amount', 0):,.0f} KRW")
            print(f"📈 Total P&L: {summary.get('total_profit_amount', 0):+,.0f} KRW")
            print(f"📊 P&L rate: {summary.get('total_profit_rate', 0):+.2f}%")

        for i, stock in enumerate(portfolio[:3]):
            print(f"  {i+1}. {stock['stock_name']}: {stock['quantity']} shares ({stock['profit_rate']:+.2f}%)")

        if len(portfolio) > 3:
            print(f"  ... and {len(portfolio)-3} more stocks")


async def quick_buy_test(stock_code="061040", amount=10000, mode="demo"):
    """Quick buy test"""
    print(f"💳 Testing buy for {stock_code}... (amount: {amount:,} KRW, mode: {mode})")

    if mode == "real":
        print("⚠️ Real trading mode! Actual trading will occur!")
        confirmation = input("Do you really want to buy in real trading mode? (yes/no): ")
        if confirmation.lower() != "yes":
            print("Buy cancelled.")
            return {'success': False, 'message': 'User cancelled'}

    async with AsyncTradingContext(mode=mode, buy_amount=amount) as trader:
        result = await trader.async_buy_stock(stock_code, timeout=20.0)

        if result['success']:
            print("✅ Buy successful!")
            print(f"   Stock: {result['stock_code']}")
            print(f"   Quantity: {result['quantity']} shares")
            print(f"   Current price: {result['current_price']:,} KRW")
            print(f"   Total amount: {result['total_amount']:,} KRW")
        else:
            print(f"❌ Buy failed: {result['message']}")

        return result


async def quick_sell_test(stock_code="061040", mode="demo"):
    """Quick sell test"""
    print(f"💸 Testing sell for {stock_code}... (mode: {mode})")

    if mode == "real":
        print("⚠️ Real trading mode! Actual trading will occur!")
        confirmation = input("Do you really want to sell in real trading mode? (yes/no): ")
        if confirmation.lower() != "yes":
            print("Sell cancelled.")
            return {'success': False, 'message': 'User cancelled'}

    async with AsyncTradingContext(mode=mode) as trader:
        result = await trader.async_sell_stock(stock_code, timeout=20.0)

        if result['success']:
            print("✅ Sell successful!")
            print(f"   Stock: {result['stock_code']}")
            print(f"   Quantity: {result['quantity']} shares")
            print(f"   Estimated amount: {result['estimated_amount']:,} KRW")
            if 'profit_rate' in result:
                print(f"   P&L rate: {result['profit_rate']:+.2f}%")
        else:
            print(f"❌ Sell failed: {result['message']}")

        return result


async def main():
    """Main function"""
    try:
        args = parse_arguments()
    except SystemExit:
        return

    mode = args.mode
    command = args.command

    # Mode display
    mode_emoji = "🟢" if mode == "demo" else "🔴"
    mode_text = "Demo trading" if mode == "demo" else "Real trading"

    print(f"🚀 Quick test started ({mode_emoji} {mode_text})")
    print("="*40)

    if mode == "real":
        print("⚠️ Warning: Real trading mode!")
        print("⚠️ Actual trading may occur!")
        print("="*40)

    try:
        if command == "portfolio":
            await quick_portfolio_check(mode)

        elif command == "buy":
            await quick_buy_test("061040", 10000, mode)  # RF Tech 10,000 KRW

        elif command == "sell":
            await quick_sell_test("061040", mode)  # RF Tech all shares

    except Exception as e:
        logger.error(f"Error during test: {e}")

    print(f"\n✅ Test completed ({mode_text})")


def show_usage():
    """Show usage"""
    print("🚀 Quick Test Script")
    print("="*40)
    print("Usage:")
    print("  python quick_test.py [command] [mode]")
    print()
    print("Commands:")
    print("  portfolio - Check portfolio")
    print("  buy       - Buy RF Tech 10,000 KRW")
    print("  sell      - Sell all RF Tech shares")
    print()
    print("Modes:")
    print("  demo - Demo trading (default, safe)")
    print("  real - Real trading (⚠️ Actual trading occurs!)")
    print()
    print("Examples:")
    print("  python quick_test.py portfolio")
    print("  python quick_test.py portfolio demo")
    print("  python quick_test.py buy --mode demo")
    print("  python quick_test.py sell --mode real")


if __name__ == "__main__":
    # Show usage if run without arguments
    if len(sys.argv) == 1:
        show_usage()
    else:
        asyncio.run(main())
