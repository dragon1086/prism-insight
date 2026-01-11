#!/usr/bin/env python3
"""
분석 후 성과 추적 배치 스크립트

분석한 종목(매매/관망 모두)의 7일/14일/30일 후 가격을 추적하여
어떤 트리거 유형이 실제로 좋은 성과를 내는지 통계를 수집합니다.

Usage:
    python performance_tracker_batch.py              # 모든 추적 대상 업데이트
    python performance_tracker_batch.py --dry-run    # 실제 DB 업데이트 없이 테스트
    python performance_tracker_batch.py --report     # 현재 추적 상태 리포트
"""
from dotenv import load_dotenv
load_dotenv()

import os
import sys
import sqlite3
import argparse
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Any, Optional, Tuple
from pathlib import Path

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(f"performance_tracker_{datetime.now().strftime('%Y%m%d')}.log")
    ]
)
logger = logging.getLogger(__name__)

# 프로젝트 루트 경로
PROJECT_ROOT = Path(__file__).parent
DB_PATH = PROJECT_ROOT / "stock_tracking_db.sqlite"

# krx_data_client import
try:
    from krx_data_client import (
        get_market_ohlcv_by_date,
        get_nearest_business_day_in_a_week,
    )
    KRX_AVAILABLE = True
except ImportError:
    KRX_AVAILABLE = False
    logger.warning("krx_data_client 패키지가 설치되어 있지 않습니다.")


class PerformanceTrackerBatch:
    """분석 종목 성과 추적 배치 처리기"""

    # 추적 일수 기준
    TRACK_DAYS = [7, 14, 30]

    def __init__(self, db_path: str = None, dry_run: bool = False):
        """
        Args:
            db_path: SQLite DB 경로
            dry_run: True이면 실제 DB 업데이트 없이 테스트만
        """
        self.db_path = db_path or str(DB_PATH)
        self.dry_run = dry_run
        self.today = datetime.now().strftime("%Y-%m-%d")
        self.today_yyyymmdd = datetime.now().strftime("%Y%m%d")

    def connect_db(self) -> sqlite3.Connection:
        """DB 연결"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def get_tracking_targets(self) -> List[Dict[str, Any]]:
        """추적 대상 종목 조회

        Returns:
            추적이 필요한 종목 리스트
        """
        conn = self.connect_db()
        try:
            cursor = conn.execute("""
                SELECT
                    id,
                    ticker,
                    company_name,
                    trigger_type,
                    trigger_mode,
                    analyzed_date,
                    analyzed_price,
                    decision,
                    was_traded,
                    skip_reason,
                    buy_score,
                    min_score,
                    target_price,
                    stop_loss,
                    risk_reward_ratio,
                    tracked_7d_date,
                    tracked_7d_price,
                    tracked_7d_return,
                    tracked_14d_date,
                    tracked_14d_price,
                    tracked_14d_return,
                    tracked_30d_date,
                    tracked_30d_price,
                    tracked_30d_return,
                    tracking_status
                FROM analysis_performance_tracker
                WHERE tracking_status IN ('pending', 'in_progress')
                ORDER BY analyzed_date ASC
            """)

            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()

    def get_current_price(self, ticker: str) -> Optional[float]:
        """종목 현재가 조회

        Args:
            ticker: 종목코드 (6자리)

        Returns:
            현재 종가 또는 None
        """
        if not KRX_AVAILABLE:
            logger.error("krx_data_client를 사용할 수 없습니다.")
            return None

        try:
            # 최근 7일 내 가장 가까운 영업일 가져오기
            today = datetime.now().strftime("%Y%m%d")
            week_ago = (datetime.now() - timedelta(days=7)).strftime("%Y%m%d")

            df = get_market_ohlcv_by_date(week_ago, today, ticker)

            if df is None or df.empty:
                logger.warning(f"[{ticker}] 가격 데이터 없음")
                return None

            # 가장 최근 종가 반환 (krx_data_client는 영문 컬럼명 사용)
            close_col = 'Close' if 'Close' in df.columns else '종가'
            latest_close = df[close_col].iloc[-1]
            return float(latest_close)

        except Exception as e:
            logger.error(f"[{ticker}] 가격 조회 실패: {e}")
            return None

    def calculate_days_elapsed(self, analyzed_date: str) -> int:
        """분석일로부터 경과 일수 계산

        Args:
            analyzed_date: 분석일 (YYYY-MM-DD 또는 YYYY-MM-DD HH:MM:SS)

        Returns:
            경과 일수
        """
        try:
            # 시간 부분이 포함된 경우 날짜만 추출
            date_only = analyzed_date.split(' ')[0] if ' ' in analyzed_date else analyzed_date
            analyzed = datetime.strptime(date_only, "%Y-%m-%d")
            today = datetime.now()
            return (today - analyzed).days
        except Exception as e:
            logger.error(f"날짜 계산 오류: {e}")
            return 0

    def calculate_return(self, analyzed_price: float, current_price: float) -> float:
        """수익률 계산

        Args:
            analyzed_price: 분석 시점 가격
            current_price: 현재 가격

        Returns:
            수익률 (예: 0.05 = 5%)
        """
        if analyzed_price <= 0:
            return 0.0
        return (current_price - analyzed_price) / analyzed_price

    def update_tracking_record(
        self,
        record: Dict[str, Any],
        days_elapsed: int,
        current_price: float,
        analyzed_price: float
    ) -> Dict[str, Any]:
        """추적 기록 업데이트

        Args:
            record: 기존 레코드 정보 (이미 기록된 값 확인용)
            days_elapsed: 경과 일수
            current_price: 현재 가격
            analyzed_price: 분석 시점 가격

        Returns:
            업데이트할 필드와 값
        """
        updates = {}
        return_rate = self.calculate_return(analyzed_price, current_price)

        # 7일차 업데이트 (아직 기록되지 않았고, 7일 이상 경과한 경우)
        if days_elapsed >= 7 and record.get('tracked_7d_return') is None:
            updates['tracked_7d_date'] = self.today
            updates['tracked_7d_price'] = current_price
            updates['tracked_7d_return'] = return_rate

        # 14일차 업데이트 (아직 기록되지 않았고, 14일 이상 경과한 경우)
        if days_elapsed >= 14 and record.get('tracked_14d_return') is None:
            updates['tracked_14d_date'] = self.today
            updates['tracked_14d_price'] = current_price
            updates['tracked_14d_return'] = return_rate

        # 30일차 업데이트 (아직 기록되지 않았고, 30일 이상 경과한 경우)
        if days_elapsed >= 30 and record.get('tracked_30d_return') is None:
            updates['tracked_30d_date'] = self.today
            updates['tracked_30d_price'] = current_price
            updates['tracked_30d_return'] = return_rate
            updates['tracking_status'] = 'completed'
        elif days_elapsed >= 7 and record.get('tracking_status') == 'pending':
            updates['tracking_status'] = 'in_progress'

        if updates:
            updates['updated_at'] = self.today

        return updates

    def apply_updates(self, record_id: int, updates: Dict[str, Any]) -> bool:
        """DB에 업데이트 적용

        Args:
            record_id: 레코드 ID
            updates: 업데이트할 필드와 값

        Returns:
            성공 여부
        """
        if not updates:
            return True

        if self.dry_run:
            logger.info(f"[DRY-RUN] ID {record_id}: {updates}")
            return True

        conn = self.connect_db()
        try:
            # 동적으로 UPDATE 쿼리 생성
            set_clause = ", ".join([f"{k} = ?" for k in updates.keys()])
            values = list(updates.values()) + [record_id]

            query = f"UPDATE analysis_performance_tracker SET {set_clause} WHERE id = ?"
            conn.execute(query, values)
            conn.commit()
            return True
        except Exception as e:
            logger.error(f"DB 업데이트 실패 (ID {record_id}): {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    def run(self) -> Dict[str, Any]:
        """배치 실행

        Returns:
            실행 결과 통계
        """
        logger.info("="*60)
        logger.info(f"성과 추적 배치 시작: {self.today}")
        if self.dry_run:
            logger.info("[DRY-RUN 모드] 실제 DB 업데이트 없음")
        logger.info("="*60)

        # 통계
        stats = {
            'total': 0,
            'updated': 0,
            'skipped': 0,
            'errors': 0,
            'completed': 0,
            'by_trigger_type': {},
            'by_decision': {'traded': 0, 'watched': 0}
        }

        # 추적 대상 조회
        targets = self.get_tracking_targets()
        stats['total'] = len(targets)
        logger.info(f"추적 대상: {stats['total']}개 종목")

        if not targets:
            logger.info("추적 대상 종목이 없습니다.")
            return stats

        # 각 종목 처리
        for record in targets:
            ticker = record['ticker']
            company_name = record['company_name']
            trigger_type = record['trigger_type'] or 'unknown'
            analyzed_date = record['analyzed_date']
            analyzed_price = record['analyzed_price']
            was_traded = record['was_traded']

            # 경과 일수 계산
            days_elapsed = self.calculate_days_elapsed(analyzed_date)

            # 이미 추적이 완료된 기간은 건너뛰기
            should_update = False
            if days_elapsed >= 7 and record['tracked_7d_price'] is None:
                should_update = True
            if days_elapsed >= 14 and record['tracked_14d_price'] is None:
                should_update = True
            if days_elapsed >= 30 and record['tracked_30d_price'] is None:
                should_update = True

            if not should_update:
                logger.debug(f"[{ticker}] {company_name}: 업데이트 불필요 (경과 {days_elapsed}일)")
                stats['skipped'] += 1
                continue

            logger.info(f"[{ticker}] {company_name}: 경과 {days_elapsed}일, 트리거={trigger_type}")

            # 현재가 조회
            current_price = self.get_current_price(ticker)
            if current_price is None:
                logger.warning(f"[{ticker}] 가격 조회 실패, 건너뜀")
                stats['errors'] += 1
                continue

            # 수익률 계산
            return_rate = self.calculate_return(analyzed_price, current_price)
            logger.info(f"  분석가: {analyzed_price:,.0f} → 현재가: {current_price:,.0f} ({return_rate*100:+.2f}%)")

            # 업데이트 내용 결정
            updates = self.update_tracking_record(
                record,
                days_elapsed,
                current_price,
                analyzed_price
            )

            # DB 업데이트
            if self.apply_updates(record['id'], updates):
                stats['updated'] += 1

                # 트리거 유형별 통계
                if trigger_type not in stats['by_trigger_type']:
                    stats['by_trigger_type'][trigger_type] = {'count': 0, 'returns': []}
                stats['by_trigger_type'][trigger_type]['count'] += 1
                stats['by_trigger_type'][trigger_type]['returns'].append(return_rate)

                # 매매/관망 분류
                if was_traded:
                    stats['by_decision']['traded'] += 1
                else:
                    stats['by_decision']['watched'] += 1

                # 완료된 건수
                if updates.get('tracking_status') == 'completed':
                    stats['completed'] += 1
            else:
                stats['errors'] += 1

        # 결과 요약
        logger.info("="*60)
        logger.info("배치 실행 완료")
        logger.info(f"  전체: {stats['total']}, 업데이트: {stats['updated']}, "
                   f"건너뜀: {stats['skipped']}, 오류: {stats['errors']}")
        logger.info(f"  완료: {stats['completed']}, 매매: {stats['by_decision']['traded']}, "
                   f"관망: {stats['by_decision']['watched']}")
        logger.info("="*60)

        return stats

    def generate_report(self) -> str:
        """추적 현황 리포트 생성

        Returns:
            리포트 문자열
        """
        conn = self.connect_db()
        try:
            # 전체 통계
            cursor = conn.execute("""
                SELECT
                    tracking_status,
                    COUNT(*) as count
                FROM analysis_performance_tracker
                GROUP BY tracking_status
            """)
            status_stats = {row['tracking_status']: row['count'] for row in cursor.fetchall()}

            # 트리거 유형별 통계
            cursor = conn.execute("""
                SELECT
                    trigger_type,
                    COUNT(*) as count,
                    SUM(CASE WHEN was_traded = 1 THEN 1 ELSE 0 END) as traded_count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY trigger_type
            """)
            trigger_stats = cursor.fetchall()

            # 매매 vs 관망 성과 비교
            cursor = conn.execute("""
                SELECT
                    CASE WHEN was_traded = 1 THEN '매매' ELSE '관망' END as decision,
                    COUNT(*) as count,
                    AVG(tracked_7d_return) as avg_7d_return,
                    AVG(tracked_14d_return) as avg_14d_return,
                    AVG(tracked_30d_return) as avg_30d_return
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                GROUP BY was_traded
            """)
            decision_stats = cursor.fetchall()

            # 리포트 생성
            report = []
            report.append("="*70)
            report.append(f"📊 분석 종목 성과 추적 리포트 ({self.today})")
            report.append("="*70)
            report.append("")

            # 추적 상태별 현황
            report.append("## 1. 추적 상태별 현황")
            report.append("-"*40)
            for status, count in status_stats.items():
                status_name = {
                    'pending': '대기 중',
                    'in_progress': '추적 중',
                    'completed': '완료'
                }.get(status, status)
                report.append(f"  {status_name}: {count}건")
            report.append("")

            # 트리거 유형별 성과
            report.append("## 2. 트리거 유형별 성과 (완료된 추적만)")
            report.append("-"*40)
            if trigger_stats:
                report.append(f"{'트리거 유형':<25} {'건수':>6} {'매매':>6} {'7일':>8} {'14일':>8} {'30일':>8}")
                report.append("-"*70)
                for row in trigger_stats:
                    trigger_type = row['trigger_type'] or 'unknown'
                    count = row['count']
                    traded = row['traded_count'] or 0
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    # 수익률 포맷팅
                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{trigger_type:<25} {count:>6} {traded:>6} {r7:>8} {r14:>8} {r30:>8}")
            else:
                report.append("  완료된 추적 데이터가 없습니다.")
            report.append("")

            # 매매 vs 관망 성과
            report.append("## 3. 매매 vs 관망 성과 비교")
            report.append("-"*40)
            if decision_stats:
                report.append(f"{'구분':<10} {'건수':>6} {'7일':>10} {'14일':>10} {'30일':>10}")
                report.append("-"*50)
                for row in decision_stats:
                    decision = row['decision']
                    count = row['count']
                    avg_7d = row['avg_7d_return']
                    avg_14d = row['avg_14d_return']
                    avg_30d = row['avg_30d_return']

                    r7 = f"{avg_7d*100:+.1f}%" if avg_7d else "N/A"
                    r14 = f"{avg_14d*100:+.1f}%" if avg_14d else "N/A"
                    r30 = f"{avg_30d*100:+.1f}%" if avg_30d else "N/A"

                    report.append(f"{decision:<10} {count:>6} {r7:>10} {r14:>10} {r30:>10}")
            else:
                report.append("  완료된 추적 데이터가 없습니다.")
            report.append("")

            # 최근 추적 완료 종목
            cursor = conn.execute("""
                SELECT
                    ticker,
                    company_name,
                    trigger_type,
                    analyzed_date,
                    analyzed_price,
                    tracked_30d_price,
                    tracked_30d_return,
                    was_traded,
                    decision
                FROM analysis_performance_tracker
                WHERE tracking_status = 'completed'
                ORDER BY updated_at DESC
                LIMIT 10
            """)
            recent = cursor.fetchall()

            report.append("## 4. 최근 추적 완료 종목 (최대 10건)")
            report.append("-"*40)
            if recent:
                for row in recent:
                    ticker = row['ticker']
                    name = row['company_name']
                    trigger = row['trigger_type'] or 'unknown'
                    analyzed_price = row['analyzed_price']
                    final_price = row['tracked_30d_price']
                    return_rate = row['tracked_30d_return']
                    was_traded = "매매" if row['was_traded'] else "관망"

                    ret_str = f"{return_rate*100:+.1f}%" if return_rate else "N/A"
                    final_str = f"{final_price:,.0f}" if final_price else "N/A"
                    analyzed_str = f"{analyzed_price:,.0f}" if analyzed_price else "N/A"
                    report.append(f"  [{ticker}] {name}")
                    report.append(f"    트리거: {trigger}, 결정: {was_traded}")
                    report.append(f"    분석가: {analyzed_str} → 30일 후: {final_str} ({ret_str})")
            else:
                report.append("  완료된 추적 데이터가 없습니다.")
            report.append("")

            report.append("="*70)

            return "\n".join(report)

        finally:
            conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="분석 종목 성과 추적 배치",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
    python performance_tracker_batch.py              # 전체 추적 업데이트
    python performance_tracker_batch.py --dry-run    # 테스트 모드
    python performance_tracker_batch.py --report     # 현황 리포트만 출력
        """
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="실제 DB 업데이트 없이 테스트만 수행"
    )
    parser.add_argument(
        "--report",
        action="store_true",
        help="현재 추적 상태 리포트 출력"
    )
    parser.add_argument(
        "--db",
        type=str,
        default=None,
        help="SQLite DB 경로 (기본: ./stock_tracking_db.sqlite)"
    )

    args = parser.parse_args()

    tracker = PerformanceTrackerBatch(db_path=args.db, dry_run=args.dry_run)

    if args.report:
        # 리포트만 출력
        report = tracker.generate_report()
        print(report)
    else:
        # 배치 실행
        stats = tracker.run()

        # 결과 리포트도 함께 출력
        print("\n")
        report = tracker.generate_report()
        print(report)


if __name__ == "__main__":
    main()
