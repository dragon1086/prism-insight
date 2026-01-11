#!/usr/bin/env python3
"""
Migrate watchlist_history to analysis_performance_tracker

기존 watchlist_history 데이터를 analysis_performance_tracker 테이블로 마이그레이션합니다.
- 7/14/30일 후 가격 데이터를 pykrx에서 조회하여 수익률 계산
- trading_history와 비교하여 was_traded 필드 설정
- trigger_type이 없는 경우 기본값 사용

Usage:
    python utils/migrate_watchlist_to_performance_tracker.py --dry-run  # 미리보기
    python utils/migrate_watchlist_to_performance_tracker.py            # 실행
"""

import argparse
import sqlite3
import json
import glob
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# pykrx import (optional - for price data)
try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    logger.warning("pykrx not available. Price tracking will be skipped.")


# Global trigger map (loaded once)
_TRIGGER_MAP: Dict[tuple, str] = {}


def load_trigger_results_map(project_root: Path) -> Dict[tuple, str]:
    """
    trigger_results JSON 파일에서 (ticker, date) -> trigger_type 매핑 생성
    """
    global _TRIGGER_MAP

    if _TRIGGER_MAP:
        return _TRIGGER_MAP

    trigger_map = {}

    # Find all trigger_results files
    pattern = str(project_root / "trigger_results_*.json")
    files = glob.glob(pattern)

    for filepath in files:
        try:
            # Extract date from filename: trigger_results_afternoon_20251103.json
            filename = Path(filepath).name
            parts = filename.replace('.json', '').split('_')
            if len(parts) >= 4:
                date_str = parts[3]  # YYYYMMDD

                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                for trigger_type, stocks in data.items():
                    if not isinstance(stocks, list):
                        continue
                    # Map trigger types to simplified names
                    simplified = simplify_trigger_type(trigger_type)
                    for stock_info in stocks:
                        ticker = stock_info.get('code')
                        if ticker:
                            # Format date as YYYY-MM-DD
                            formatted_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
                            key = (ticker, formatted_date)
                            if key not in trigger_map:
                                trigger_map[key] = simplified

        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    logger.info(f"Loaded {len(trigger_map)} trigger mappings from {len(files)} files")
    _TRIGGER_MAP = trigger_map
    return trigger_map


def simplify_trigger_type(trigger_type: str) -> str:
    """
    trigger_batch.py의 트리거 이름을 간소화
    """
    mapping = {
        '거래량 급증 상위주': '거래량 급증',
        '갭 상승 모멘텀 상위주': '갭 상승',
        '시총 대비 집중 자금 유입 상위주': '자금 유입',
        '일중 상승률 상위주': '일중 상승',
        '마감 강도 상위주': '마감 강도',
        '거래량 증가 상위 횡보주': '횡보 거래량',
    }
    return mapping.get(trigger_type, trigger_type)


def get_db_path() -> Path:
    """Get database path"""
    # Try relative path first
    db_path = Path("stock_tracking_db.sqlite")
    if db_path.exists():
        return db_path

    # Try from project root
    project_root = Path(__file__).parent.parent
    db_path = project_root / "stock_tracking_db.sqlite"
    if db_path.exists():
        return db_path

    raise FileNotFoundError("Database not found")


def get_traded_tickers(conn: sqlite3.Connection) -> dict:
    """
    trading_history에서 매매한 종목 목록 조회
    Returns: {ticker: [buy_dates]}
    """
    cursor = conn.cursor()
    cursor.execute("""
        SELECT ticker, buy_date
        FROM trading_history
        ORDER BY buy_date
    """)

    traded = {}
    for ticker, buy_date in cursor.fetchall():
        if ticker not in traded:
            traded[ticker] = []
        # Extract date part only
        if buy_date:
            date_part = buy_date.split()[0] if ' ' in buy_date else buy_date[:10]
            traded[ticker].append(date_part)

    return traded


def get_price_on_date(ticker: str, target_date: str) -> float | None:
    """
    특정 날짜의 종가 조회 (pykrx 사용)
    target_date: YYYY-MM-DD format
    """
    if not PYKRX_AVAILABLE:
        return None

    try:
        # Format date for pykrx (YYYYMMDD)
        date_formatted = target_date.replace("-", "")

        # Get OHLCV data for the date range (in case of holidays)
        start_date = (datetime.strptime(target_date, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y%m%d")
        end_date = (datetime.strptime(target_date, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y%m%d")

        df = pykrx_stock.get_market_ohlcv(start_date, end_date, ticker)

        if df.empty:
            return None

        # Find closest date
        target_dt = datetime.strptime(target_date, "%Y-%m-%d")
        for idx in df.index:
            if idx.date() >= target_dt.date():
                return float(df.loc[idx, '종가'])

        # If no future date, use last available
        return float(df.iloc[-1]['종가'])

    except Exception as e:
        logger.debug(f"Failed to get price for {ticker} on {target_date}: {e}")
        return None


def calculate_tracking_data(analyzed_date: str, analyzed_price: float, ticker: str) -> dict:
    """
    7/14/30일 후 가격 및 수익률 계산
    """
    result = {
        'tracked_7d_date': None, 'tracked_7d_price': None, 'tracked_7d_return': None,
        'tracked_14d_date': None, 'tracked_14d_price': None, 'tracked_14d_return': None,
        'tracked_30d_date': None, 'tracked_30d_price': None, 'tracked_30d_return': None,
        'tracking_status': 'pending'
    }

    if not PYKRX_AVAILABLE or not analyzed_price or analyzed_price <= 0:
        return result

    try:
        # Parse analyzed date
        if ' ' in analyzed_date:
            base_date = datetime.strptime(analyzed_date.split()[0], "%Y-%m-%d")
        else:
            base_date = datetime.strptime(analyzed_date[:10], "%Y-%m-%d")

        today = datetime.now()
        days_passed = (today - base_date).days

        # 7-day tracking
        if days_passed >= 7:
            target_date = (base_date + timedelta(days=7)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_7d_date'] = target_date
                result['tracked_7d_price'] = price
                result['tracked_7d_return'] = (price - analyzed_price) / analyzed_price  # 소수점 형태 (0.078 = 7.8%)

        # 14-day tracking
        if days_passed >= 14:
            target_date = (base_date + timedelta(days=14)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_14d_date'] = target_date
                result['tracked_14d_price'] = price
                result['tracked_14d_return'] = (price - analyzed_price) / analyzed_price  # 소수점 형태

        # 30-day tracking
        if days_passed >= 30:
            target_date = (base_date + timedelta(days=30)).strftime("%Y-%m-%d")
            price = get_price_on_date(ticker, target_date)
            if price:
                result['tracked_30d_date'] = target_date
                result['tracked_30d_price'] = price
                result['tracked_30d_return'] = (price - analyzed_price) / analyzed_price  # 소수점 형태

        # Determine tracking status
        if result['tracked_30d_return'] is not None:
            result['tracking_status'] = 'completed'
        elif result['tracked_7d_return'] is not None:
            result['tracking_status'] = 'in_progress'
        else:
            result['tracking_status'] = 'pending'

    except Exception as e:
        logger.warning(f"Error calculating tracking data for {ticker}: {e}")

    return result


def determine_trigger_type(record: dict, trigger_map: Dict[tuple, str] = None) -> str:
    """
    기존 데이터에서 trigger_type 결정

    우선순위:
    1. 기존 trigger_type 필드
    2. trigger_results JSON 매핑
    3. rationale 텍스트 분석

    트리거 유형:
    - 오전: 거래량 급증, 갭 상승, 자금 유입
    - 오후: 일중 상승, 마감 강도, 횡보 거래량
    """
    # 1. Use existing trigger_type if available
    if record.get('trigger_type') and record['trigger_type'].strip():
        return record['trigger_type']

    # 2. Look up from trigger_results JSON map
    if trigger_map:
        ticker = record.get('ticker', '')
        analyzed_date = record.get('analyzed_date', '')
        if analyzed_date:
            # Extract date part only (YYYY-MM-DD)
            date_part = analyzed_date[:10] if len(analyzed_date) >= 10 else analyzed_date
            key = (ticker, date_part)
            if key in trigger_map:
                return trigger_map[key]

    # 3. Fall back to text analysis
    rationale = record.get('rationale', '') or ''
    skip_reason = record.get('skip_reason', '') or ''

    # Heuristics based on rationale/skip_reason
    combined = (rationale + ' ' + skip_reason).lower()

    # 오전 트리거
    if '급등' in combined or 'surge' in combined or ('거래량' in combined and '급증' in combined):
        return '거래량 급증'
    elif '갭' in combined or 'gap' in combined:
        return '갭 상승'
    elif '자금' in combined and '유입' in combined:
        return '자금 유입'

    # 오후 트리거
    elif '일중' in combined or ('장중' in combined and '상승' in combined):
        return '일중 상승'
    elif '마감' in combined or '강도' in combined:
        return '마감 강도'
    elif '횡보' in combined:
        return '횡보 거래량'

    # 기타
    elif '돌파' in combined or 'breakout' in combined:
        return '기술적 돌파'
    elif '뉴스' in combined or 'news' in combined:
        return '뉴스 촉발'
    else:
        return '종합 분석'  # Default


def determine_trigger_mode(analyzed_date: str) -> str:
    """
    분석 시간에서 trigger_mode 결정 (morning/afternoon)
    """
    try:
        if ' ' in analyzed_date:
            time_part = analyzed_date.split()[1]
            hour = int(time_part.split(':')[0])
            return 'morning' if hour < 12 else 'afternoon'
    except:
        pass
    return 'afternoon'  # Default


def check_was_traded(ticker: str, analyzed_date: str, traded_tickers: dict) -> int:
    """
    해당 분석 후 실제 매매 여부 확인
    """
    if ticker not in traded_tickers:
        return 0

    try:
        analyzed_dt = analyzed_date.split()[0] if ' ' in analyzed_date else analyzed_date[:10]

        for buy_date in traded_tickers[ticker]:
            # If bought within 3 days after analysis, consider it as traded
            analyzed = datetime.strptime(analyzed_dt, "%Y-%m-%d")
            bought = datetime.strptime(buy_date, "%Y-%m-%d")

            if 0 <= (bought - analyzed).days <= 3:
                return 1
    except:
        pass

    return 0


def parse_scenario(scenario_str: str) -> dict:
    """Parse scenario JSON string"""
    if not scenario_str:
        return {}
    try:
        import json
        return json.loads(scenario_str)
    except:
        return {}


def migrate_data(conn: sqlite3.Connection, dry_run: bool = True, reset: bool = False) -> dict:
    """
    watchlist_history + stock_holdings + trading_history → analysis_performance_tracker 마이그레이션

    기간 통일: watchlist_history의 최소 날짜를 기준으로 trading 데이터도 필터링
    """
    cursor = conn.cursor()

    # Create table if not exists
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS analysis_performance_tracker (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            watchlist_id INTEGER,
            ticker TEXT NOT NULL,
            company_name TEXT,
            trigger_type TEXT,
            trigger_mode TEXT,
            analyzed_date TEXT NOT NULL,
            analyzed_price REAL,
            decision TEXT,
            was_traded INTEGER DEFAULT 0,
            skip_reason TEXT,
            buy_score REAL,
            min_score REAL,
            target_price REAL,
            stop_loss REAL,
            risk_reward_ratio REAL,
            tracked_7d_date TEXT,
            tracked_7d_price REAL,
            tracked_7d_return REAL,
            tracked_14d_date TEXT,
            tracked_14d_price REAL,
            tracked_14d_return REAL,
            tracked_30d_date TEXT,
            tracked_30d_price REAL,
            tracked_30d_return REAL,
            tracking_status TEXT DEFAULT 'pending',
            created_at TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()

    # Load trigger_results map for accurate trigger type detection
    project_root = Path(__file__).parent.parent
    trigger_map = load_trigger_results_map(project_root)

    # Reset option: 기존 데이터 삭제 후 다시 마이그레이션
    if reset and not dry_run:
        cursor.execute("DELETE FROM analysis_performance_tracker")
        logger.info("Cleared existing analysis_performance_tracker data for re-migration")
        conn.commit()

    # Get existing records to avoid duplicates (by ticker + date)
    cursor.execute("SELECT ticker, analyzed_date FROM analysis_performance_tracker")
    existing_keys = {(row[0], row[1][:10] if row[1] else '') for row in cursor.fetchall()}

    stats = {
        'total': 0,
        'skipped_existing': 0,
        'skipped_date_filter': 0,
        'migrated': 0,
        'with_7d': 0,
        'with_14d': 0,
        'with_30d': 0,
        'traded': 0,
        'watched': 0,
        'date_range': {}
    }

    all_records = []

    # ===== 0. 기간 통일을 위해 watchlist_history 최소 날짜 조회 =====
    cursor.execute("SELECT MIN(DATE(analyzed_date)) FROM watchlist_history")
    min_watchlist_date = cursor.fetchone()[0]

    if min_watchlist_date:
        logger.info(f"Watchlist minimum date: {min_watchlist_date}")
        stats['date_range']['min_date'] = min_watchlist_date
    else:
        min_watchlist_date = '2000-01-01'  # Fallback if no watchlist data

    # ===== 1. watchlist_history (관망 결정) =====
    # Check which columns exist in watchlist_history
    cursor.execute("PRAGMA table_info(watchlist_history)")
    watchlist_columns = {col[1] for col in cursor.fetchall()}
    has_risk_reward = 'risk_reward_ratio' in watchlist_columns
    has_trigger_type = 'trigger_type' in watchlist_columns
    has_trigger_mode = 'trigger_mode' in watchlist_columns
    has_was_traded = 'was_traded' in watchlist_columns
    has_rationale = 'rationale' in watchlist_columns

    # Build query dynamically based on available columns
    select_cols = [
        "id", "ticker", "company_name", "current_price", "analyzed_date",
        "buy_score", "min_score", "decision", "skip_reason",
        "target_price", "stop_loss"
    ]
    if has_risk_reward:
        select_cols.append("risk_reward_ratio")
    if has_trigger_type:
        select_cols.append("trigger_type")
    if has_trigger_mode:
        select_cols.append("trigger_mode")
    if has_was_traded:
        select_cols.append("was_traded")
    if has_rationale:
        select_cols.append("rationale")

    cursor.execute(f"""
        SELECT {', '.join(select_cols)}
        FROM watchlist_history
        ORDER BY analyzed_date
    """)
    watchlist_records = cursor.fetchall()
    logger.info(f"Found {len(watchlist_records)} records in watchlist_history (관망)")

    for row in watchlist_records:
        # Build column index mapping
        col_idx = {col: i for i, col in enumerate(select_cols)}

        # Calculate risk_reward_ratio if not in table
        target_price = row[col_idx['target_price']]
        stop_loss = row[col_idx['stop_loss']]
        analyzed_price = row[col_idx['current_price']]
        if has_risk_reward:
            risk_reward_ratio = row[col_idx['risk_reward_ratio']]
        elif target_price and stop_loss and analyzed_price and stop_loss != analyzed_price:
            # Calculate: (target - current) / (current - stop_loss)
            upside = (target_price - analyzed_price) / analyzed_price if analyzed_price else 0
            downside = (analyzed_price - stop_loss) / analyzed_price if analyzed_price else 0
            risk_reward_ratio = upside / downside if downside > 0 else None
        else:
            risk_reward_ratio = None

        record = {
            'source': 'watchlist',
            'source_id': row[col_idx['id']],
            'ticker': row[col_idx['ticker']],
            'company_name': row[col_idx['company_name']],
            'analyzed_price': analyzed_price,
            'analyzed_date': row[col_idx['analyzed_date']],
            'buy_score': row[col_idx['buy_score']],
            'min_score': row[col_idx['min_score']],
            'decision': row[col_idx['decision']] or '관망',
            'skip_reason': row[col_idx['skip_reason']],
            'target_price': target_price,
            'stop_loss': stop_loss,
            'risk_reward_ratio': risk_reward_ratio,
            'trigger_type': row[col_idx['trigger_type']] if has_trigger_type else None,
            'trigger_mode': row[col_idx['trigger_mode']] if has_trigger_mode else None,
            'was_traded': 0,  # watchlist = 관망
            'rationale': row[col_idx['rationale']] if has_rationale else None
        }
        all_records.append(record)

    # ===== 2. trading_history (완료된 매매) - 기간 필터 적용 =====
    cursor.execute("""
        SELECT
            id, ticker, company_name, buy_price, buy_date, scenario
        FROM trading_history
        WHERE DATE(buy_date) >= ?
        ORDER BY buy_date
    """, (min_watchlist_date,))
    trading_records = cursor.fetchall()

    # 전체 건수 조회 (로깅용)
    cursor.execute("SELECT COUNT(*) FROM trading_history")
    total_trading = cursor.fetchone()[0]
    stats['skipped_date_filter'] += total_trading - len(trading_records)
    logger.info(f"Found {len(trading_records)} records in trading_history (완료 매매, 기간 필터: >= {min_watchlist_date}, 전체: {total_trading})")

    for row in trading_records:
        scenario = parse_scenario(row[5])
        record = {
            'source': 'trading_history',
            'source_id': row[0],
            'ticker': row[1],
            'company_name': row[2],
            'analyzed_price': row[3],
            'analyzed_date': row[4],
            'buy_score': scenario.get('buy_score'),
            'min_score': scenario.get('min_score'),
            'decision': scenario.get('decision', '진입'),
            'skip_reason': None,
            'target_price': scenario.get('target_price'),
            'stop_loss': scenario.get('stop_loss'),
            'risk_reward_ratio': scenario.get('risk_reward_ratio'),
            'trigger_type': None,
            'trigger_mode': None,
            'was_traded': 1,  # trading_history = 매매
            'rationale': scenario.get('rationale')
        }
        all_records.append(record)

    # ===== 3. stock_holdings (현재 보유) - 기간 필터 적용 =====
    cursor.execute("""
        SELECT
            ticker, company_name, buy_price, buy_date, scenario
        FROM stock_holdings
        WHERE DATE(buy_date) >= ?
        ORDER BY buy_date
    """, (min_watchlist_date,))
    holdings_records = cursor.fetchall()

    # 전체 건수 조회 (로깅용)
    cursor.execute("SELECT COUNT(*) FROM stock_holdings")
    total_holdings = cursor.fetchone()[0]
    stats['skipped_date_filter'] += total_holdings - len(holdings_records)
    logger.info(f"Found {len(holdings_records)} records in stock_holdings (현재 보유, 기간 필터: >= {min_watchlist_date}, 전체: {total_holdings})")

    for row in holdings_records:
        scenario = parse_scenario(row[4])
        record = {
            'source': 'stock_holdings',
            'source_id': None,
            'ticker': row[0],
            'company_name': row[1],
            'analyzed_price': row[2],
            'analyzed_date': row[3],
            'buy_score': scenario.get('buy_score'),
            'min_score': scenario.get('min_score'),
            'decision': scenario.get('decision', '진입'),
            'skip_reason': None,
            'target_price': scenario.get('target_price'),
            'stop_loss': scenario.get('stop_loss'),
            'risk_reward_ratio': scenario.get('risk_reward_ratio'),
            'trigger_type': None,
            'trigger_mode': None,
            'was_traded': 1,  # stock_holdings = 매매
            'rationale': scenario.get('rationale')
        }
        all_records.append(record)

    stats['total'] = len(all_records)
    logger.info(f"Total records to process: {stats['total']}")

    for record in all_records:
        # Create unique key for duplicate check
        date_key = record['analyzed_date'][:10] if record['analyzed_date'] else ''
        unique_key = (record['ticker'], date_key)

        # Skip if already migrated
        if unique_key in existing_keys:
            stats['skipped_existing'] += 1
            continue

        # Determine trigger info
        trigger_type = determine_trigger_type(record, trigger_map)
        trigger_mode = record.get('trigger_mode') or determine_trigger_mode(record['analyzed_date'] or '')

        # Use was_traded from record
        was_traded = record.get('was_traded', 0)

        if was_traded:
            stats['traded'] += 1
        else:
            stats['watched'] += 1

        # Calculate tracking data
        tracking = calculate_tracking_data(
            record['analyzed_date'] or '',
            record['analyzed_price'],
            record['ticker']
        )

        if tracking['tracked_7d_return'] is not None:
            stats['with_7d'] += 1
        if tracking['tracked_14d_return'] is not None:
            stats['with_14d'] += 1
        if tracking['tracked_30d_return'] is not None:
            stats['with_30d'] += 1

        source_label = f"[{record['source']}]"
        if dry_run:
            logger.info(f"[DRY-RUN] {source_label} {record['ticker']} ({record['analyzed_date']}) "
                       f"trigger={trigger_type}, traded={was_traded}, status={tracking['tracking_status']}")
        else:
            # Insert into analysis_performance_tracker
            cursor.execute("""
                INSERT INTO analysis_performance_tracker (
                    watchlist_id, ticker, company_name,
                    trigger_type, trigger_mode,
                    analyzed_date, analyzed_price,
                    decision, was_traded, skip_reason,
                    buy_score, min_score,
                    target_price, stop_loss, risk_reward_ratio,
                    tracked_7d_date, tracked_7d_price, tracked_7d_return,
                    tracked_14d_date, tracked_14d_price, tracked_14d_return,
                    tracked_30d_date, tracked_30d_price, tracked_30d_return,
                    tracking_status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                record['source_id'], record['ticker'], record['company_name'],
                trigger_type, trigger_mode,
                record['analyzed_date'], record['analyzed_price'],
                record['decision'], was_traded, record['skip_reason'],
                record['buy_score'], record['min_score'],
                record['target_price'], record['stop_loss'], record['risk_reward_ratio'],
                tracking['tracked_7d_date'], tracking['tracked_7d_price'], tracking['tracked_7d_return'],
                tracking['tracked_14d_date'], tracking['tracked_14d_price'], tracking['tracked_14d_return'],
                tracking['tracked_30d_date'], tracking['tracked_30d_price'], tracking['tracked_30d_return'],
                tracking['tracking_status'],
                datetime.now().isoformat(),
                datetime.now().isoformat()
            ))

        # Add to existing keys to prevent duplicates within same run
        existing_keys.add(unique_key)
        stats['migrated'] += 1

    if not dry_run:
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description='Migrate watchlist to performance tracker')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
    parser.add_argument('--reset', action='store_true', help='Clear existing data and re-migrate')
    parser.add_argument('--db', type=str, help='Database path (optional)')
    args = parser.parse_args()

    # Get database path
    try:
        db_path = Path(args.db) if args.db else get_db_path()
    except FileNotFoundError as e:
        logger.error(f"Database not found: {e}")
        return

    logger.info(f"Using database: {db_path}")

    if args.dry_run:
        logger.info("=== DRY RUN MODE (no changes will be made) ===")

    if args.reset:
        logger.info("=== RESET MODE (existing data will be cleared) ===")

    # Connect and migrate
    conn = sqlite3.connect(db_path)

    try:
        stats = migrate_data(conn, dry_run=args.dry_run, reset=args.reset)

        print("\n" + "=" * 60)
        print("Migration Summary")
        print("=" * 60)
        if stats['date_range'].get('min_date'):
            print(f"Date filter (period unification):   >= {stats['date_range']['min_date']}")
        print(f"Total records found:                {stats['total']}")
        print(f"  - Skipped (date filter):          {stats['skipped_date_filter']}")
        print(f"  - Skipped (already migrated):     {stats['skipped_existing']}")
        print(f"Records migrated:                   {stats['migrated']}")
        print(f"  - Traded (매매):                  {stats['traded']}")
        print(f"  - Watched (관망):                 {stats['watched']}")
        if stats['traded'] + stats['watched'] > 0:
            trade_rate = stats['traded'] / (stats['traded'] + stats['watched']) * 100
            print(f"  - Trade rate:                     {trade_rate:.1f}%")
        print(f"  - With 7-day data:                {stats['with_7d']}")
        print(f"  - With 14-day data:               {stats['with_14d']}")
        print(f"  - With 30-day data (completed):   {stats['with_30d']}")
        print("=" * 60)

        if args.dry_run:
            print("\nTo execute migration, run without --dry-run flag")
            print("To re-migrate (clear + migrate), use: --reset")
        else:
            print(f"\n✓ Successfully migrated {stats['migrated']} records")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
