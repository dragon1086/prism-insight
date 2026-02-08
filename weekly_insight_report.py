#!/usr/bin/env python3
"""
Weekly Insight Report — Trigger Reliability Summary
Sends weekly trigger performance report to Telegram channel.

Usage:
    python3 weekly_insight_report.py              # Send to Telegram
    python3 weekly_insight_report.py --dry-run     # Print only
"""
import argparse
import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)
DB_PATH = str(Path(__file__).parent / "stock_tracking_db.sqlite")


def _safe_query(cursor, query: str, default=(0, 0)):
    """Execute query with error handling, return default on failure."""
    try:
        cursor.execute(query)
        result = cursor.fetchone()
        return result if result else default
    except sqlite3.Error as e:
        logger.warning(f"Query failed: {e}")
        return default


def _format_percentage(value: float) -> str:
    """Format percentage with sign."""
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def generate_weekly_report(db_path: str = DB_PATH) -> str:
    """Generate weekly insight report message."""
    # Calculate week range
    today = datetime.now()
    week_start = today - timedelta(days=7)
    week_start_str = week_start.strftime("%Y-%m-%d %H:%M:%S")

    # Format dates for display
    start_display = week_start.strftime("%-m/%-d")
    end_display = today.strftime("%-m/%-d")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ========== KOREAN MARKET ==========
    kr_avoided_count, kr_avoided_avg = 0, None
    kr_missed_count, kr_missed_best = 0, None
    kr_best_trigger_name, kr_best_trigger_rate = "데이터 없음", 0
    kr_new_principles, kr_total_principles = 0, 0

    try:
        # Avoided losses
        query = f"""
            SELECT COUNT(*), AVG(tracked_30d_return * 100)
            FROM analysis_performance_tracker
            WHERE tracking_status='completed'
              AND was_traded=0
              AND tracked_30d_return < -0.05
              AND updated_at >= '{week_start_str}'
        """
        count, avg = _safe_query(cursor, query)
        kr_avoided_count = count or 0
        kr_avoided_avg = avg

        # Missed opportunities
        query = f"""
            SELECT COUNT(*), MAX(tracked_30d_return * 100)
            FROM analysis_performance_tracker
            WHERE tracking_status='completed'
              AND was_traded=0
              AND tracked_30d_return > 0.10
              AND updated_at >= '{week_start_str}'
        """
        count, max_return = _safe_query(cursor, query)
        kr_missed_count = count or 0
        kr_missed_best = max_return

        # Best trigger
        query = """
            SELECT
                trigger_type,
                SUM(CASE WHEN tracking_status='completed' THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN tracking_status='completed' AND tracked_30d_return > 0 THEN 1 ELSE 0 END) as wins
            FROM analysis_performance_tracker
            WHERE trigger_type IS NOT NULL
            GROUP BY trigger_type
            HAVING completed >= 3
            ORDER BY (wins * 1.0 / completed) DESC
            LIMIT 1
        """
        result = _safe_query(cursor, query, default=(None, 0, 0))
        if result[0]:
            kr_best_trigger_name = result[0]
            completed, wins = result[1], result[2]
            kr_best_trigger_rate = (wins / completed * 100) if completed > 0 else 0

        # New principles
        query = f"""
            SELECT COUNT(*)
            FROM trading_principles
            WHERE is_active=1 AND created_at >= '{week_start_str}'
        """
        kr_new_principles = _safe_query(cursor, query, default=(0,))[0] or 0

        query = "SELECT COUNT(*) FROM trading_principles WHERE is_active=1"
        kr_total_principles = _safe_query(cursor, query, default=(0,))[0] or 0

    except sqlite3.Error as e:
        logger.warning(f"KR market query error: {e}")

    # ========== US MARKET ==========
    us_avoided_count, us_avoided_avg = 0, None
    us_missed_count, us_missed_best = 0, None
    us_best_trigger_name, us_best_trigger_rate = "데이터 없음", 0
    us_new_principles = 0

    try:
        # Avoided losses
        query = f"""
            SELECT COUNT(*), AVG(return_30d * 100)
            FROM us_analysis_performance_tracker
            WHERE return_30d IS NOT NULL
              AND was_traded=0
              AND return_30d < -0.05
              AND last_updated >= '{week_start_str}'
        """
        count, avg = _safe_query(cursor, query)
        us_avoided_count = count or 0
        us_avoided_avg = avg

        # Missed opportunities
        query = f"""
            SELECT COUNT(*), MAX(return_30d * 100)
            FROM us_analysis_performance_tracker
            WHERE return_30d IS NOT NULL
              AND was_traded=0
              AND return_30d > 0.10
              AND last_updated >= '{week_start_str}'
        """
        count, max_return = _safe_query(cursor, query)
        us_missed_count = count or 0
        us_missed_best = max_return

        # Best trigger
        query = """
            SELECT
                trigger_type,
                SUM(CASE WHEN return_30d IS NOT NULL THEN 1 ELSE 0 END) as completed,
                SUM(CASE WHEN return_30d > 0 THEN 1 ELSE 0 END) as wins
            FROM us_analysis_performance_tracker
            WHERE trigger_type IS NOT NULL
            GROUP BY trigger_type
            HAVING completed >= 3
            ORDER BY (wins * 1.0 / completed) DESC
            LIMIT 1
        """
        result = _safe_query(cursor, query, default=(None, 0, 0))
        if result[0]:
            us_best_trigger_name = result[0]
            completed, wins = result[1], result[2]
            us_best_trigger_rate = (wins / completed * 100) if completed > 0 else 0

    except sqlite3.Error as e:
        logger.warning(f"US market query error: {e}")

    conn.close()

    # ========== GENERATE MESSAGE ==========

    # Generate summary line
    if kr_best_trigger_rate > 0 or us_best_trigger_rate > 0:
        best_market = "한국" if kr_best_trigger_rate >= us_best_trigger_rate else "미국"
        best_trigger = kr_best_trigger_name if kr_best_trigger_rate >= us_best_trigger_rate else us_best_trigger_name
        summary = f"{best_market} {best_trigger} 트리거 신뢰도 유지 중"
    else:
        summary = "데이터 축적 중, 곧 인사이트 제공 예정"

    # Format avoided/missed stats
    kr_avoided_str = f"{kr_avoided_count}건" + (f" (평균 {_format_percentage(kr_avoided_avg)})" if kr_avoided_avg else "")
    kr_missed_str = f"{kr_missed_count}건" + (f" (최고 {_format_percentage(kr_missed_best)})" if kr_missed_best else "")
    kr_trigger_str = f"{kr_best_trigger_name} (승률 {kr_best_trigger_rate:.0f}%)" if kr_best_trigger_rate > 0 else "데이터 축적 중"
    kr_principles_str = f"{kr_new_principles}개 추가 (총 {kr_total_principles}개)"

    us_avoided_str = f"{us_avoided_count}건" + (f" (평균 {_format_percentage(us_avoided_avg)})" if us_avoided_avg else "")
    us_missed_str = f"{us_missed_count}건" + (f" (최고 {_format_percentage(us_missed_best)})" if us_missed_best else "")
    us_trigger_str = f"{us_best_trigger_name} (승률 {us_best_trigger_rate:.0f}%)" if us_best_trigger_rate > 0 else "데이터 축적 중"
    us_principles_str = f"{us_new_principles}개"

    message = f"""📋 PRISM 주간 인사이트 ({start_display} ~ {end_display})

🇰🇷 한국시장
━━━━━━━━━━━━━━━━━━━━
🛡️ 회피한 손실: {kr_avoided_str}
❌ 놓친 기회: {kr_missed_str}
📊 가장 정확한 트리거: {kr_trigger_str}
📌 새 원칙: {kr_principles_str}

🇺🇸 미국시장
━━━━━━━━━━━━━━━━━━━━
🛡️ 회피한 손실: {us_avoided_str}
❌ 놓친 기회: {us_missed_str}
📊 가장 정확한 트리거: {us_trigger_str}
📌 새 원칙: {us_principles_str}

💡 이번 주 핵심: {summary}"""

    return message


async def send_to_telegram(message: str):
    """Send message to Telegram channel."""
    try:
        from telegram import Bot
    except ImportError:
        logger.error("python-telegram-bot not installed. Run: pip install python-telegram-bot")
        return

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    channel_id = os.getenv("TELEGRAM_CHANNEL_ID")

    if not token or not channel_id:
        logger.error("TELEGRAM_BOT_TOKEN or TELEGRAM_CHANNEL_ID not set in .env")
        return

    try:
        bot = Bot(token=token)
        await bot.send_message(chat_id=channel_id, text=message, parse_mode="HTML")
        logger.info("Weekly report sent to Telegram successfully")
    except Exception as e:
        logger.error(f"Failed to send Telegram message: {e}")


def main():
    parser = argparse.ArgumentParser(description="Weekly Insight Report")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    try:
        message = generate_weekly_report()
        print(message)

        if not args.dry_run:
            asyncio.run(send_to_telegram(message))
        else:
            logger.info("Dry run mode — message not sent")
    except Exception as e:
        logger.error(f"Failed to generate report: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
