#!/usr/bin/env python3
"""
KRX API 테스트 스크립트
서버에서 krx_data_client가 정상 작동하는지 확인합니다.
"""
import datetime
import sys
from pathlib import Path

# Load .env from project root
from dotenv import load_dotenv
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(env_path)

def main():
    print("=" * 60)
    print("KRX API Test")
    print("=" * 60)

    today = datetime.datetime.now().strftime('%Y%m%d')
    one_month_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime('%Y%m%d')

    print(f'Today: {today}')
    print(f'One month ago: {one_month_ago}')
    print(f'Current time: {datetime.datetime.now().strftime("%H:%M:%S")}')
    print()

    # Import after printing basic info
    try:
        from krx_data_client import (
            get_nearest_business_day_in_a_week,
            get_market_ohlcv_by_ticker,
            get_index_ohlcv_by_date
        )
        import pkg_resources
        version = pkg_resources.get_distribution('kospi-kosdaq-stock-server').version
        print(f'kospi-kosdaq-stock-server version: {version}')
        print()
    except Exception as e:
        print(f'Import error: {e}')
        sys.exit(1)

    errors = []

    # Test 1: get_nearest_business_day_in_a_week
    print('=== Test 1: get_nearest_business_day_in_a_week ===')
    try:
        trade_date = get_nearest_business_day_in_a_week(today, prev=True)
        print(f'Result: {trade_date}')
        print('Status: SUCCESS')
    except Exception as e:
        print(f'Error: {e}')
        print('Status: FAILED')
        errors.append(('get_nearest_business_day_in_a_week', str(e)))
    print()

    # Test 2: get_index_ohlcv_by_date (KOSPI)
    print('=== Test 2: get_index_ohlcv_by_date (KOSPI 1001) ===')
    try:
        df = get_index_ohlcv_by_date(one_month_ago, today, '1001')
        print(f'Rows: {len(df)}')
        if len(df) > 0:
            print(f'Last row date: {df.index[-1] if hasattr(df.index[-1], "strftime") else df.index[-1]}')
            print(f'Last close: {df["종가"].iloc[-1] if "종가" in df.columns else df["Close"].iloc[-1]}')
        print('Status: SUCCESS')
    except Exception as e:
        print(f'Error: {e}')
        print('Status: FAILED')
        errors.append(('get_index_ohlcv_by_date', str(e)))
    print()

    # Test 3: get_market_ohlcv_by_ticker
    print('=== Test 3: get_market_ohlcv_by_ticker ===')
    try:
        trade_date = get_nearest_business_day_in_a_week(today, prev=True)
        df = get_market_ohlcv_by_ticker(trade_date)
        print(f'Rows: {len(df)}')
        if '005930' in df.index:
            print(f'Samsung (005930) close: {df.loc["005930", "종가"]:,.0f}')
        print('Status: SUCCESS')
    except Exception as e:
        print(f'Error: {e}')
        print('Status: FAILED')
        errors.append(('get_market_ohlcv_by_ticker', str(e)))
    print()

    # Summary
    print("=" * 60)
    if errors:
        print(f'FAILED: {len(errors)} test(s) failed')
        for name, err in errors:
            print(f'  - {name}: {err}')
        sys.exit(1)
    else:
        print('ALL TESTS PASSED')
        sys.exit(0)

if __name__ == '__main__':
    main()
