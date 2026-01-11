#!/usr/bin/env python3
"""
성과 추적 데이터 복구 스크립트

리셋된 analysis_performance_tracker 테이블의 7d/14d/30d 수익률을
과거 가격 데이터를 조회하여 복구합니다.
"""

import sqlite3
import logging
from datetime import datetime, timedelta
from pathlib import Path
import time

# pykrx import
try:
    from pykrx import stock as pykrx_stock
    PYKRX_AVAILABLE = True
except ImportError:
    PYKRX_AVAILABLE = False
    print("pykrx가 설치되어 있지 않습니다. pip install pykrx")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "stock_tracking_db.sqlite"


def get_historical_price(ticker: str, target_date: str) -> float | None:
    """
    특정 날짜의 종가를 조회합니다.
    해당 날짜가 휴장일이면 이전 영업일 가격을 반환합니다.

    Args:
        ticker: 종목코드 (6자리)
        target_date: 조회 날짜 (YYYY-MM-DD)

    Returns:
        종가 또는 None
    """
    if not PYKRX_AVAILABLE:
        return None

    try:
        # 날짜 형식 변환 (YYYY-MM-DD -> YYYYMMDD)
        date_obj = datetime.strptime(target_date, "%Y-%m-%d")
        end_date = date_obj.strftime("%Y%m%d")
        # 5일 전부터 조회 (휴장일 대비)
        start_date = (date_obj - timedelta(days=5)).strftime("%Y%m%d")

        # pykrx로 가격 조회
        df = pykrx_stock.get_market_ohlcv_by_date(start_date, end_date, ticker)

        if df.empty:
            return None

        # 가장 최근 종가 반환
        return float(df['종가'].iloc[-1])

    except Exception as e:
        logger.error(f"[{ticker}] {target_date} 가격 조회 실패: {e}")
        return None


def calculate_target_date(analyzed_date: str, days: int) -> str:
    """분석일로부터 N일 후 날짜 계산"""
    # 시간 부분 제거
    date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
    date_obj = datetime.strptime(date_only, "%Y-%m-%d")
    target = date_obj + timedelta(days=days)
    return target.strftime("%Y-%m-%d")


def backfill_record(conn, record: dict) -> dict:
    """
    단일 레코드의 7d/14d/30d 수익률을 복구합니다.

    Returns:
        업데이트된 필드들
    """
    ticker = record['ticker']
    analyzed_date = record['analyzed_date']
    analyzed_price = record['analyzed_price']

    if not analyzed_price or analyzed_price == 0:
        logger.warning(f"[{ticker}] 분석가격이 없어 건너뜀")
        return {}

    updates = {}

    # 7일차
    if record.get('tracked_7d_return') is None:
        target_date = calculate_target_date(analyzed_date, 7)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_7d_date'] = target_date
                updates['tracked_7d_price'] = price
                updates['tracked_7d_return'] = return_rate
                logger.info(f"  7일차: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    # 14일차
    if record.get('tracked_14d_return') is None:
        target_date = calculate_target_date(analyzed_date, 14)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_14d_date'] = target_date
                updates['tracked_14d_price'] = price
                updates['tracked_14d_return'] = return_rate
                logger.info(f"  14일차: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    # 30일차
    if record.get('tracked_30d_return') is None:
        target_date = calculate_target_date(analyzed_date, 30)
        if datetime.strptime(target_date, "%Y-%m-%d") <= datetime.now():
            price = get_historical_price(ticker, target_date)
            if price:
                return_rate = (price - analyzed_price) / analyzed_price
                updates['tracked_30d_date'] = target_date
                updates['tracked_30d_price'] = price
                updates['tracked_30d_return'] = return_rate
                updates['tracking_status'] = 'completed'
                logger.info(f"  30일차: {analyzed_price:,.0f} → {price:,.0f} ({return_rate*100:+.2f}%)")

    return updates


def run_backfill():
    """메인 복구 실행"""
    logger.info("=" * 60)
    logger.info("성과 추적 데이터 복구 시작")
    logger.info("=" * 60)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # 복구 대상 조회 (7d/14d/30d 값이 동일한 레코드)
    cursor.execute("""
        SELECT * FROM analysis_performance_tracker
        WHERE tracking_status IN ('in_progress', 'completed')
          AND tracked_7d_return IS NOT NULL
          AND tracked_7d_return = tracked_14d_return
          AND tracked_14d_return = tracked_30d_return
        ORDER BY analyzed_date ASC
    """)

    records = [dict(row) for row in cursor.fetchall()]
    logger.info(f"복구 대상: {len(records)}개 레코드")

    if not records:
        logger.info("복구할 레코드가 없습니다.")
        conn.close()
        return

    updated_count = 0
    error_count = 0

    for i, record in enumerate(records):
        ticker = record['ticker']
        company = record['company_name']
        analyzed_date = record['analyzed_date']

        logger.info(f"[{i+1}/{len(records)}] {company} ({ticker}) - 분석일: {analyzed_date}")

        try:
            # 기존 값 초기화 (다시 계산하기 위해)
            cursor.execute("""
                UPDATE analysis_performance_tracker
                SET tracked_7d_return = NULL, tracked_7d_price = NULL, tracked_7d_date = NULL,
                    tracked_14d_return = NULL, tracked_14d_price = NULL, tracked_14d_date = NULL,
                    tracked_30d_return = NULL, tracked_30d_price = NULL, tracked_30d_date = NULL,
                    tracking_status = 'in_progress'
                WHERE id = ?
            """, (record['id'],))

            # 과거 가격으로 복구
            record['tracked_7d_return'] = None
            record['tracked_14d_return'] = None
            record['tracked_30d_return'] = None

            updates = backfill_record(conn, record)

            if updates:
                # 업데이트 쿼리 생성
                set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
                values = list(updates.values()) + [record['id']]

                cursor.execute(f"""
                    UPDATE analysis_performance_tracker
                    SET {set_clause}, updated_at = datetime('now')
                    WHERE id = ?
                """, values)

                updated_count += 1

            # API 호출 제한 방지
            time.sleep(0.3)

        except Exception as e:
            logger.error(f"[{ticker}] 복구 실패: {e}")
            error_count += 1

    conn.commit()
    conn.close()

    logger.info("=" * 60)
    logger.info(f"복구 완료: 성공 {updated_count}, 실패 {error_count}")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_backfill()
