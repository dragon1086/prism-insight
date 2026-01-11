#!/usr/bin/env python3
"""
stock_holdings와 trading_history의 기존 레코드에 trigger_type 백필

기존 데이터의 trigger_type과 trigger_mode 컬럼을 채웁니다:
1. trigger_results_*.json 파일에서 매핑 로드
2. (ticker, buy_date) 기준으로 trigger_type 결정
3. 매칭 안 되면 'AI분석' (기본값)

Usage:
    python utils/backfill_trigger_type.py --dry-run  # 미리보기
    python utils/backfill_trigger_type.py            # 실행
"""

import argparse
import sqlite3
import json
import glob
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Global trigger map (loaded once)
_TRIGGER_MAP: Dict[tuple, dict] = {}


def get_db_path() -> Path:
    """Get database path"""
    # Try relative path first (when running from project root)
    db_path = Path("stock_tracking_db.sqlite")
    if db_path.exists():
        return db_path

    # Try from utils directory
    project_root = Path(__file__).parent.parent
    db_path = project_root / "stock_tracking_db.sqlite"
    if db_path.exists():
        return db_path

    raise FileNotFoundError("Database not found")


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


def load_trigger_results_map(project_root: Path) -> Dict[tuple, dict]:
    """
    trigger_results JSON 파일에서 (ticker, date) -> {trigger_type, trigger_mode} 매핑 생성
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
            # Extract date and mode from filename: trigger_results_afternoon_20251103.json
            filename = Path(filepath).name
            parts = filename.replace('.json', '').split('_')
            if len(parts) >= 4:
                trigger_mode = parts[2]  # morning or afternoon
                date_str = parts[3]  # YYYYMMDD

                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                # Get metadata trigger_mode if available
                metadata = data.get('metadata', {})
                if metadata.get('trigger_mode'):
                    trigger_mode = metadata['trigger_mode']

                for trigger_type, stocks in data.items():
                    if trigger_type == 'metadata' or not isinstance(stocks, list):
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
                                trigger_map[key] = {
                                    'trigger_type': simplified,
                                    'trigger_mode': trigger_mode
                                }

        except Exception as e:
            logger.warning(f"Failed to load {filepath}: {e}")

    logger.info(f"Loaded {len(trigger_map)} trigger mappings from {len(files)} files")
    _TRIGGER_MAP = trigger_map
    return trigger_map


def determine_trigger_info(
    ticker: str,
    buy_date: str,
    scenario_json: Optional[str],
    trigger_map: Dict[tuple, dict]
) -> tuple:
    """
    기존 데이터에서 trigger_type과 trigger_mode 결정

    Args:
        ticker: 종목 코드
        buy_date: 매수일 (YYYY-MM-DD HH:MM:SS or YYYY-MM-DD)
        scenario_json: scenario JSON 문자열
        trigger_map: trigger_results에서 로드한 매핑

    Returns:
        (trigger_type, trigger_mode) tuple
    """
    # Extract date part
    date_part = buy_date[:10] if buy_date and len(buy_date) >= 10 else ''

    # 1. Look up from trigger_results JSON map (exact date match)
    key = (ticker, date_part)
    if key in trigger_map:
        info = trigger_map[key]
        return info['trigger_type'], info['trigger_mode']

    # 2. Try ±1 day match (for timing mismatches)
    if date_part:
        try:
            base_date = datetime.strptime(date_part, "%Y-%m-%d")
            for delta in [-1, 1]:
                check_date = (base_date + __import__('datetime').timedelta(days=delta)).strftime("%Y-%m-%d")
                check_key = (ticker, check_date)
                if check_key in trigger_map:
                    info = trigger_map[check_key]
                    logger.debug(f"[{ticker}] Found trigger match on {check_date} (original: {date_part})")
                    return info['trigger_type'], info['trigger_mode']
        except:
            pass

    # 3. Fall back to text analysis from scenario
    trigger_type = 'AI분석'
    trigger_mode = 'unknown'

    if scenario_json:
        try:
            scenario = json.loads(scenario_json) if isinstance(scenario_json, str) else scenario_json
            rationale = scenario.get('rationale', '') or ''
            combined = rationale.lower()

            # Heuristics based on rationale
            if '급등' in combined or 'surge' in combined or ('거래량' in combined and '급증' in combined):
                trigger_type = '거래량 급증'
            elif '갭' in combined or 'gap' in combined:
                trigger_type = '갭 상승'
            elif '자금' in combined and '유입' in combined:
                trigger_type = '자금 유입'
            elif '일중' in combined or ('장중' in combined and '상승' in combined):
                trigger_type = '일중 상승'
            elif '마감' in combined or '강도' in combined:
                trigger_type = '마감 강도'
            elif '횡보' in combined:
                trigger_type = '횡보 거래량'
            elif '돌파' in combined or 'breakout' in combined:
                trigger_type = '기술적 돌파'
            elif '뉴스' in combined or 'news' in combined:
                trigger_type = '뉴스 촉발'
            else:
                trigger_type = '종합 분석'
        except:
            pass

    # Determine trigger_mode from time if available
    if buy_date and len(buy_date) > 10:
        try:
            time_part = buy_date.split()[1] if ' ' in buy_date else ''
            if time_part:
                hour = int(time_part.split(':')[0])
                trigger_mode = 'morning' if hour < 12 else 'afternoon'
        except:
            pass

    return trigger_type, trigger_mode


def add_columns_if_not_exist(conn: sqlite3.Connection):
    """
    stock_holdings와 trading_history에 trigger_type, trigger_mode 컬럼 추가
    """
    cursor = conn.cursor()

    # Check and add columns to stock_holdings
    cursor.execute("PRAGMA table_info(stock_holdings)")
    columns = {row[1] for row in cursor.fetchall()}

    if 'trigger_type' not in columns:
        cursor.execute("ALTER TABLE stock_holdings ADD COLUMN trigger_type TEXT")
        logger.info("Added trigger_type column to stock_holdings")

    if 'trigger_mode' not in columns:
        cursor.execute("ALTER TABLE stock_holdings ADD COLUMN trigger_mode TEXT")
        logger.info("Added trigger_mode column to stock_holdings")

    # Check and add columns to trading_history
    cursor.execute("PRAGMA table_info(trading_history)")
    columns = {row[1] for row in cursor.fetchall()}

    if 'trigger_type' not in columns:
        cursor.execute("ALTER TABLE trading_history ADD COLUMN trigger_type TEXT")
        logger.info("Added trigger_type column to trading_history")

    if 'trigger_mode' not in columns:
        cursor.execute("ALTER TABLE trading_history ADD COLUMN trigger_mode TEXT")
        logger.info("Added trigger_mode column to trading_history")

    conn.commit()


def backfill_stock_holdings(
    conn: sqlite3.Connection,
    trigger_map: Dict[tuple, dict],
    dry_run: bool
) -> dict:
    """
    stock_holdings의 기존 레코드에 trigger_type 백필
    """
    cursor = conn.cursor()
    stats = {'total': 0, 'updated': 0, 'skipped': 0}

    # Check if trigger_type column exists
    cursor.execute("PRAGMA table_info(stock_holdings)")
    columns = {row[1] for row in cursor.fetchall()}
    has_trigger_type = 'trigger_type' in columns

    if has_trigger_type:
        cursor.execute("""
            SELECT ticker, company_name, buy_date, scenario, trigger_type
            FROM stock_holdings
        """)
    else:
        cursor.execute("""
            SELECT ticker, company_name, buy_date, scenario, NULL as trigger_type
            FROM stock_holdings
        """)
    holdings = cursor.fetchall()
    stats['total'] = len(holdings)

    for row in holdings:
        ticker, company_name, buy_date, scenario, existing_trigger = row

        # Skip if already has trigger_type
        if existing_trigger and existing_trigger.strip():
            stats['skipped'] += 1
            continue

        # Determine trigger info
        trigger_type, trigger_mode = determine_trigger_info(
            ticker, buy_date, scenario, trigger_map
        )

        if dry_run:
            logger.info(f"[DRY-RUN] stock_holdings: {ticker}({company_name}) -> {trigger_type} ({trigger_mode})")
        else:
            cursor.execute("""
                UPDATE stock_holdings
                SET trigger_type = ?, trigger_mode = ?
                WHERE ticker = ?
            """, (trigger_type, trigger_mode, ticker))

        stats['updated'] += 1

    if not dry_run:
        conn.commit()

    return stats


def backfill_trading_history(
    conn: sqlite3.Connection,
    trigger_map: Dict[tuple, dict],
    dry_run: bool
) -> dict:
    """
    trading_history의 기존 레코드에 trigger_type 백필
    """
    cursor = conn.cursor()
    stats = {'total': 0, 'updated': 0, 'skipped': 0}

    # Check if trigger_type column exists
    cursor.execute("PRAGMA table_info(trading_history)")
    columns = {row[1] for row in cursor.fetchall()}
    has_trigger_type = 'trigger_type' in columns

    if has_trigger_type:
        cursor.execute("""
            SELECT id, ticker, company_name, buy_date, scenario, trigger_type
            FROM trading_history
        """)
    else:
        cursor.execute("""
            SELECT id, ticker, company_name, buy_date, scenario, NULL as trigger_type
            FROM trading_history
        """)
    history = cursor.fetchall()
    stats['total'] = len(history)

    for row in history:
        id_, ticker, company_name, buy_date, scenario, existing_trigger = row

        # Skip if already has trigger_type
        if existing_trigger and existing_trigger.strip():
            stats['skipped'] += 1
            continue

        # Determine trigger info
        trigger_type, trigger_mode = determine_trigger_info(
            ticker, buy_date, scenario, trigger_map
        )

        if dry_run:
            logger.info(f"[DRY-RUN] trading_history[{id_}]: {ticker}({company_name}) -> {trigger_type} ({trigger_mode})")
        else:
            cursor.execute("""
                UPDATE trading_history
                SET trigger_type = ?, trigger_mode = ?
                WHERE id = ?
            """, (trigger_type, trigger_mode, id_))

        stats['updated'] += 1

    if not dry_run:
        conn.commit()

    return stats


def main():
    parser = argparse.ArgumentParser(description='Backfill trigger_type to stock_holdings and trading_history')
    parser.add_argument('--dry-run', action='store_true', help='Preview without making changes')
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

    # Load trigger_results map
    project_root = Path(__file__).parent.parent
    trigger_map = load_trigger_results_map(project_root)

    # Connect to database
    conn = sqlite3.connect(db_path)

    try:
        # Add columns if not exist
        if not args.dry_run:
            add_columns_if_not_exist(conn)

        # Backfill stock_holdings
        logger.info("\n=== Backfilling stock_holdings ===")
        holdings_stats = backfill_stock_holdings(conn, trigger_map, args.dry_run)

        # Backfill trading_history
        logger.info("\n=== Backfilling trading_history ===")
        history_stats = backfill_trading_history(conn, trigger_map, args.dry_run)

        # Print summary
        print("\n" + "=" * 60)
        print("Backfill Summary")
        print("=" * 60)
        print(f"\nstock_holdings:")
        print(f"  Total records:   {holdings_stats['total']}")
        print(f"  Updated:         {holdings_stats['updated']}")
        print(f"  Skipped:         {holdings_stats['skipped']}")

        print(f"\ntrading_history:")
        print(f"  Total records:   {history_stats['total']}")
        print(f"  Updated:         {history_stats['updated']}")
        print(f"  Skipped:         {history_stats['skipped']}")
        print("=" * 60)

        if args.dry_run:
            print("\nTo execute backfill, run without --dry-run flag")
        else:
            print(f"\nSuccessfully backfilled trigger_type data")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
