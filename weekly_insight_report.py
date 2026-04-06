#!/usr/bin/env python3
"""
Weekly Insight Report — Trading Summary, Sell Evaluation, Trigger Performance, AI Intuitions
Sends weekly insight report to Telegram channel with optional broadcast.

Usage:
    python3 weekly_insight_report.py                              # Send to Telegram
    python3 weekly_insight_report.py --dry-run                     # Print only
    python3 weekly_insight_report.py --broadcast-languages en,ja   # With broadcast
"""
import argparse
import asyncio
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from trading import kis_auth as ka

load_dotenv()
logger = logging.getLogger(__name__)
DB_PATH = str(Path(__file__).parent / "stock_tracking_db.sqlite")


def _safe_query(cursor, query: str, params=(), default=(0, 0)):
    """Execute query with error handling, return default on failure."""
    try:
        cursor.execute(query, params)
        result = cursor.fetchone()
        return result if result else default
    except sqlite3.Error as e:
        logger.warning(f"Query failed: {e}")
        return default


def _safe_query_all(cursor, query: str, params=()) -> list:
    """Execute query and return all results, empty list on failure."""
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.warning(f"Query failed: {e}")
        return []


def _format_percentage(value: float) -> str:
    """Format percentage with sign."""
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def _sell_verdict(change_pct: float) -> str:
    """Determine sell evaluation verdict based on price change after selling."""
    if change_pct < -1:
        return "✅ 잘 팔았습니다"
    elif change_pct > 3:
        return "😅 더 기다릴 수 있었습니다"
    else:
        return "👌 적절한 매도"


def _get_primary_account_key(market: str) -> str | None:
    default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
    svr = "vps" if default_mode == "demo" else "prod"
    try:
        return ka.resolve_account(svr=svr, market=market)["account_key"]
    except Exception as exc:
        logger.warning(f"Primary {market} account resolution failed: {exc}")
        return None


def _get_weekly_trades(cursor, week_start_str: str) -> str:
    """Get weekly trade summary for KR and US markets."""
    kr_account_key = _get_primary_account_key("kr")
    us_account_key = _get_primary_account_key("us")
    kr_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, sell_price, profit_rate, holding_days
        FROM trading_history WHERE sell_date >= ? AND account_key = ? ORDER BY sell_date DESC
    """, (week_start_str, kr_account_key)) if kr_account_key else []
    kr_buys = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, buy_date, current_price
        FROM stock_holdings WHERE buy_date >= ? AND account_key = ?
    """, (week_start_str, kr_account_key)) if kr_account_key else []
    us_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, sell_price, profit_rate, holding_days
        FROM us_trading_history WHERE sell_date >= ? AND account_key = ? ORDER BY sell_date DESC
    """, (week_start_str, us_account_key)) if us_account_key else []
    us_buys = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, buy_date, current_price
        FROM us_stock_holdings WHERE buy_date >= ? AND account_key = ?
    """, (week_start_str, us_account_key)) if us_account_key else []

    if not (kr_sells or kr_buys or us_sells or us_buys):
        return "이번 주 매매 없음"

    lines = []

    if kr_buys or kr_sells:
        lines.append("🇰🇷 한국시장")
        for ticker, name, buy_price, _date, current_price in kr_buys:
            if current_price and buy_price:
                pnl = (current_price - buy_price) / buy_price * 100
                lines.append(f"  매수: {name}({ticker}) {buy_price:,.0f}원 → 현재 {current_price:,.0f}원 ({pnl:+.1f}%)")
            else:
                lines.append(f"  매수: {name}({ticker}) {buy_price:,.0f}원")
        for ticker, name, _buy_p, sell_p, profit, days in kr_sells:
            lines.append(f"  매도: {name}({ticker}) {sell_p:,.0f}원 → {profit:+.1f}% ({days}일 보유)")

    if us_buys or us_sells:
        if lines:
            lines.append("")
        lines.append("🇺🇸 미국시장")
        for ticker, name, buy_price, _date, current_price in us_buys:
            if current_price and buy_price:
                pnl = (current_price - buy_price) / buy_price * 100
                lines.append(f"  매수: {ticker} ${buy_price:,.2f} → 현재 ${current_price:,.2f} ({pnl:+.1f}%)")
            else:
                lines.append(f"  매수: {ticker} ${buy_price:,.2f}")
        for ticker, name, _buy_p, sell_p, profit, days in us_sells:
            lines.append(f"  매도: {ticker} ${sell_p:,.2f} → {profit:+.1f}% ({days}일 보유)")

    return "\n".join(lines)


def _get_sell_evaluation(cursor, week_start_str: str) -> str | None:
    """Evaluate sells by comparing sell price to current price.

    Returns None if no sells this week (section should be omitted).
    """
    kr_account_key = _get_primary_account_key("kr")
    us_account_key = _get_primary_account_key("us")
    kr_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, sell_price
        FROM trading_history WHERE sell_date >= ? AND account_key = ?
    """, (week_start_str, kr_account_key)) if kr_account_key else []
    us_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, sell_price
        FROM us_trading_history WHERE sell_date >= ? AND account_key = ?
    """, (week_start_str, us_account_key)) if us_account_key else []

    if not kr_sells and not us_sells:
        return None

    lines = []

    # KR: batch lookup via pykrx (single call for all tickers)
    if kr_sells:
        try:
            from krx_data_client import get_nearest_business_day_in_a_week, get_market_ohlcv_by_ticker
            today_str = datetime.now().strftime("%Y%m%d")
            trade_date = get_nearest_business_day_in_a_week(today_str, prev=True)
            df = get_market_ohlcv_by_ticker(trade_date)

            for ticker, name, sell_price in kr_sells:
                if ticker in df.index and sell_price:
                    current_price = float(df.loc[ticker, "Close"])
                    change_pct = (current_price - sell_price) / sell_price * 100
                    verdict = _sell_verdict(change_pct)
                    lines.append(
                        f"  {name}: 매도가 {sell_price:,.0f}원 → "
                        f"현재가 {current_price:,.0f}원 ({change_pct:+.1f}%) {verdict}"
                    )
        except Exception as e:
            logger.warning(f"KR price lookup failed: {e}")

    # US: batch lookup via yfinance
    if us_sells:
        try:
            import yfinance as yf
            tickers_list = [row[0] for row in us_sells]
            data = yf.download(tickers_list, period="1d", progress=False)

            for ticker, name, sell_price in us_sells:
                try:
                    if not sell_price:
                        continue
                    if len(tickers_list) == 1:
                        current_price = float(data['Close'].iloc[-1])
                    else:
                        current_price = float(data['Close'][ticker].iloc[-1])
                    change_pct = (current_price - sell_price) / sell_price * 100
                    verdict = _sell_verdict(change_pct)
                    lines.append(
                        f"  {ticker}: 매도가 ${sell_price:,.2f} → "
                        f"현재가 ${current_price:,.2f} ({change_pct:+.1f}%) {verdict}"
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"US price lookup failed: {e}")

    return "\n".join(lines) if lines else None


def _get_ai_intuitions(cursor, week_start_str: str) -> str:
    """Get AI long-term learning intuitions section."""
    new_count = _safe_query(cursor, f"""
        SELECT COUNT(*) FROM trading_intuitions
        WHERE is_active=1 AND created_at >= '{week_start_str}'
    """, default=(0,))[0] or 0

    kr_intuitions = _safe_query_all(cursor, """
        SELECT condition, insight, confidence
        FROM trading_intuitions WHERE is_active=1 AND (market='KR' OR market IS NULL)
        ORDER BY confidence DESC LIMIT 3
    """)

    us_intuitions = _safe_query_all(cursor, """
        SELECT condition, insight, confidence
        FROM trading_intuitions WHERE is_active=1 AND market='US'
        ORDER BY confidence DESC LIMIT 3
    """)

    stats = _safe_query(cursor, """
        SELECT COUNT(*), AVG(confidence), AVG(success_rate)
        FROM trading_intuitions WHERE is_active=1
    """, default=(0, 0, 0))
    total_count = stats[0] or 0
    avg_conf = stats[1] or 0

    if total_count == 0:
        return "아직 데이터 축적 중입니다. 매매 기록이 쌓이면 AI가 패턴을 학습합니다."

    new_count_str = f"{new_count}개" if new_count > 0 else "없음"
    avg_conf_str = f"{avg_conf * 100:.0f}%" if avg_conf else "집계 중"
    lines = [
        f"이번 주 신규: {new_count_str} | 누적 활성 직관: {total_count}개 | 평균 신뢰도: {avg_conf_str}"
    ]

    all_intuitions = kr_intuitions + us_intuitions
    if all_intuitions:
        lines.append("")
        lines.append("💡 주요 직관:")
        for i, (condition, insight, confidence) in enumerate(all_intuitions[:5], 1):
            conf_pct = (confidence or 0) * 100
            lines.append(f"  {i}. {condition} = {insight} (신뢰도 {conf_pct:.0f}%)")

    return "\n".join(lines)


async def generate_weekly_report(db_path: str = DB_PATH) -> str:
    """Generate weekly insight report message."""
    today = datetime.now()
    week_start = today - timedelta(days=7)
    week_start_str = week_start.strftime("%Y-%m-%d %H:%M:%S")

    start_display = week_start.strftime("%-m/%-d")
    end_display = today.strftime("%-m/%-d")

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # ========== NEW: Weekly Trades Summary ==========
    trades_summary = _get_weekly_trades(cursor, week_start_str)

    # ========== NEW: Sell Evaluation ==========
    sell_eval = _get_sell_evaluation(cursor, week_start_str)

    # ========== KOREAN MARKET (trigger performance) ==========
    kr_avoided_count, kr_avoided_avg = 0, None
    kr_missed_count, kr_missed_best = 0, None
    kr_best_trigger_name, kr_best_trigger_rate = "데이터 없음", 0
    kr_new_principles, kr_total_principles = 0, 0

    try:
        query = """
            SELECT COUNT(*), AVG(tracked_30d_return * 100)
            FROM analysis_performance_tracker
            WHERE tracking_status='completed'
              AND was_traded=0
              AND tracked_30d_return < -0.05
        """
        count, avg = _safe_query(cursor, query)
        kr_avoided_count = count or 0
        kr_avoided_avg = avg

        query = """
            SELECT COUNT(*), MAX(tracked_30d_return * 100)
            FROM analysis_performance_tracker
            WHERE tracking_status='completed'
              AND was_traded=0
              AND tracked_30d_return > 0.10
        """
        count, max_return = _safe_query(cursor, query)
        kr_missed_count = count or 0
        kr_missed_best = max_return

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

    # ========== US MARKET (trigger performance) ==========
    us_avoided_count, us_avoided_avg = 0, None
    us_missed_count, us_missed_best = 0, None
    us_best_trigger_name, us_best_trigger_rate = "데이터 없음", 0
    us_new_principles = 0

    try:
        query = """
            SELECT COUNT(*), AVG(return_30d * 100)
            FROM us_analysis_performance_tracker
            WHERE return_30d IS NOT NULL
              AND COALESCE(was_traded, 0) = 0
              AND return_30d < -0.05
        """
        count, avg = _safe_query(cursor, query)
        us_avoided_count = count or 0
        us_avoided_avg = avg

        query = """
            SELECT COUNT(*), MAX(return_30d * 100)
            FROM us_analysis_performance_tracker
            WHERE return_30d IS NOT NULL
              AND COALESCE(was_traded, 0) = 0
              AND return_30d > 0.10
        """
        count, max_return = _safe_query(cursor, query)
        us_missed_count = count or 0
        us_missed_best = max_return

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

    # ========== NEW: AI Intuitions ==========
    intuitions_section = _get_ai_intuitions(cursor, week_start_str)

    conn.close()

    # ========== GENERATE MESSAGE ==========

    # Summary line
    if kr_best_trigger_rate > 0 or us_best_trigger_rate > 0:
        best_market = "한국" if kr_best_trigger_rate >= us_best_trigger_rate else "미국"
        best_trigger = kr_best_trigger_name if kr_best_trigger_rate >= us_best_trigger_rate else us_best_trigger_name
        best_rate = max(kr_best_trigger_rate, us_best_trigger_rate)
        summary = f"{best_market} '{best_trigger}' 트리거가 승률 {best_rate:.0f}%로 가장 안정적"
    else:
        summary = "데이터 축적 중 — 30일 추적 완료 후 인사이트 제공 예정"

    # Format avoided/missed stats
    def _avoided_detail(count, avg):
        if count == 0:
            return "0건 — AI가 매수를 건너뛴 종목 중 하락한 종목 없음"
        return f"{count}건 (평균 {_format_percentage(avg)}) — 매수하지 않아 손실을 피한 종목"

    def _missed_detail(count, best):
        if count == 0:
            return "0건 — 놓친 상승 종목 없음"
        return f"{count}건 (최고 {_format_percentage(best)}) — 매수하지 않았으나 크게 오른 종목"

    kr_avoided_str = _avoided_detail(kr_avoided_count, kr_avoided_avg)
    kr_missed_str = _missed_detail(kr_missed_count, kr_missed_best)
    kr_trigger_str = f"{kr_best_trigger_name} (승률 {kr_best_trigger_rate:.0f}%)" if kr_best_trigger_rate > 0 else "데이터 축적 중"
    kr_principles_str = f"{kr_new_principles}개 추가 (총 {kr_total_principles}개)"

    us_avoided_str = _avoided_detail(us_avoided_count, us_avoided_avg)
    us_missed_str = _missed_detail(us_missed_count, us_missed_best)
    us_trigger_str = f"{us_best_trigger_name} (승률 {us_best_trigger_rate:.0f}%)" if us_best_trigger_rate > 0 else "데이터 축적 중"
    us_principles_str = f"{us_new_principles}개"

    # Actionable insights
    insights = []
    if kr_best_trigger_rate >= 60 or us_best_trigger_rate >= 60:
        insights.append("승률 60%+ 트리거가 있습니다. 해당 트리거 종목을 우선 검토하세요.")
    if kr_missed_count + us_missed_count >= 3:
        insights.append("놓친 기회가 3건 이상입니다. 매수 기준을 약간 완화하는 것을 고려해보세요.")
    if kr_avoided_count + us_avoided_count >= 5:
        insights.append("회피한 손실이 5건 이상입니다. AI의 관망 판단이 잘 작동하고 있습니다.")
    if not insights:
        insights.append("이번 주는 큰 변동 없이 안정적으로 운영되었습니다.")

    insights_str = '\n'.join(f"  → {i}" for i in insights)

    # Conditional sell evaluation section
    sell_eval_block = ""
    if sell_eval:
        sell_eval_block = f"""
🔍 매도 후 평가
━━━━━━━━━━━━━━━━━━━━
{sell_eval}
"""

    message = f"""📋 PRISM 주간 인사이트 ({start_display} ~ {end_display})
이번 주 AI 매매 판단의 성과를 돌아봅니다.

📈 이번 주 매매 요약
━━━━━━━━━━━━━━━━━━━━
{trades_summary}
{sell_eval_block}
🇰🇷 한국시장 (트리거 성과)
━━━━━━━━━━━━━━━━━━━━
🛡️ 회피한 손실: {kr_avoided_str}
❌ 놓친 기회: {kr_missed_str}
📊 가장 정확한 트리거: {kr_trigger_str}
📌 새 매매 원칙: {kr_principles_str}

🇺🇸 미국시장 (트리거 성과)
━━━━━━━━━━━━━━━━━━━━
🛡️ 회피한 손실: {us_avoided_str}
❌ 놓친 기회: {us_missed_str}
📊 가장 정확한 트리거: {us_trigger_str}
📌 새 매매 원칙: {us_principles_str}

🧠 AI 장기 학습 인사이트
━━━━━━━━━━━━━━━━━━━━
{intuitions_section}

📌 이번 주 인사이트
{insights_str}

💡 핵심: {summary}

ℹ️ 용어 안내
• 트리거 = AI가 종목을 발견한 이유 (급등, 거래량 급증 등)
• 회피한 손실 = 매수하지 않았는데 30일 뒤 -5% 이상 하락한 종목
• 놓친 기회 = 매수하지 않았는데 30일 뒤 +10% 이상 상승한 종목
• 승률 = 해당 트리거로 분석한 종목 중 30일 후 수익이 난 비율
• 매매 원칙 = AI가 과거 매매 경험에서 스스로 학습한 규칙
• 직관 = AI가 반복 패턴에서 추출한 매매 원칙"""

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


async def _send_broadcast(message: str, broadcast_languages: list):
    """Send translated report to broadcast language channels."""
    if not broadcast_languages:
        return

    try:
        import sys
        cores_path = str(Path(__file__).parent / "cores")
        if cores_path not in sys.path:
            sys.path.insert(0, cores_path)

        from agents.telegram_translator_agent import translate_telegram_message
        from telegram import Bot

        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token:
            logger.error("TELEGRAM_BOT_TOKEN not set")
            return

        bot = Bot(token=token)

        for lang in broadcast_languages:
            try:
                lang_upper = lang.upper()
                channel_id = os.getenv(f"TELEGRAM_CHANNEL_ID_{lang_upper}")
                if not channel_id:
                    logger.warning(f"No channel ID for language: {lang} (TELEGRAM_CHANNEL_ID_{lang_upper})")
                    continue

                logger.info(f"Translating weekly report to {lang}")
                translated = await translate_telegram_message(
                    message, model="gpt-5-nano", from_lang="ko", to_lang=lang
                )
                await bot.send_message(chat_id=channel_id, text=translated, parse_mode="HTML")
                logger.info(f"Weekly report sent to {lang} channel")

            except Exception as e:
                logger.error(f"Broadcast to {lang} failed: {e}")

    except Exception as e:
        logger.error(f"Broadcast error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Weekly Insight Report")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")
    parser.add_argument("--broadcast-languages", type=str, default="",
                        help="Broadcast languages (comma-separated, e.g., 'en,ja,zh')")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    async def _run():
        message = await generate_weekly_report()
        print(message)

        if not args.dry_run:
            await send_to_telegram(message)

            broadcast_languages = [l.strip() for l in args.broadcast_languages.split(",") if l.strip()]
            if broadcast_languages:
                await _send_broadcast(message, broadcast_languages)
        else:
            logger.info("Dry run mode — message not sent")

    try:
        asyncio.run(_run())
    except Exception as e:
        logger.error(f"Failed to generate report: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
