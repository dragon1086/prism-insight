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
from trading_memory_policy import normalize_application_context

load_dotenv()
logger = logging.getLogger(__name__)
DB_PATH = str(Path(__file__).parent / "stock_tracking_db.sqlite")


def _safe_query(cursor, query: str, params=(), default=None):
    """Execute query with error handling, return default on failure."""
    try:
        cursor.execute(query, params)
        result = cursor.fetchone()
        return result if result else default
    except sqlite3.Error as e:
        logger.warning(f"Query failed: {e}")
        return None


def _safe_query_all(cursor, query: str, params=()) -> list | None:
    """Execute query and return all results, None on failure."""
    try:
        cursor.execute(query, params)
        return cursor.fetchall()
    except sqlite3.Error as e:
        logger.warning(f"Query failed: {e}")
        return None


def _format_percentage(value: float) -> str:
    """Format percentage with sign."""
    if value is None:
        return "N/A"
    return f"{value:+.1f}%"


def _sell_verdict(change_pct: float) -> str:
    """Describe the observed price movement without judging the sell decision."""
    if not float("-inf") < change_pct < float("inf"):
        return "매도 후 가격 변화 미확인"
    if change_pct > 0:
        return "매도 후 상승"
    if change_pct < 0:
        return "매도 후 하락"
    return "매도 후 보합"


def _get_primary_account_key(market: str) -> str | None:
    try:
        default_mode = str(ka.getEnv().get("default_mode", "demo")).strip().lower()
        svr = "vps" if default_mode == "demo" else "prod"
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
    """, (week_start_str, kr_account_key)) if kr_account_key else None
    kr_buys = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, buy_date, current_price
        FROM stock_holdings WHERE buy_date >= ? AND account_key = ?
    """, (week_start_str, kr_account_key)) if kr_account_key else None
    us_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, sell_price, profit_rate, holding_days
        FROM us_trading_history WHERE sell_date >= ? AND account_key = ? ORDER BY sell_date DESC
    """, (week_start_str, us_account_key)) if us_account_key else None
    us_buys = _safe_query_all(cursor, """
        SELECT ticker, company_name, buy_price, buy_date, current_price
        FROM us_stock_holdings WHERE buy_date >= ? AND account_key = ?
    """, (week_start_str, us_account_key)) if us_account_key else None

    lines = []
    for market, kind, rows in (
        ("한국시장", "매도", kr_sells), ("한국시장", "매수", kr_buys),
        ("미국시장", "매도", us_sells), ("미국시장", "매수", us_buys),
    ):
        if rows is None:
            lines.append(f"{market} {kind} 내역 조회 실패 또는 계좌 미확인")
    kr_sells, kr_buys = kr_sells or [], kr_buys or []
    us_sells, us_buys = us_sells or [], us_buys or []

    if not (kr_sells or kr_buys or us_sells or us_buys):
        return "\n".join(lines) if lines else "이번 주 매매 없음"

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


async def _get_sell_evaluation(cursor, week_start_str: str) -> str | None:
    """Evaluate sells by comparing sell price to current price.

    Returns None if no sells this week (section should be omitted).
    """
    kr_account_key = _get_primary_account_key("kr")
    us_account_key = _get_primary_account_key("us")
    kr_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, sell_price
        FROM trading_history WHERE sell_date >= ? AND account_key = ?
    """, (week_start_str, kr_account_key)) if kr_account_key else None
    us_sells = _safe_query_all(cursor, """
        SELECT ticker, company_name, sell_price
        FROM us_trading_history WHERE sell_date >= ? AND account_key = ?
    """, (week_start_str, us_account_key)) if us_account_key else None

    lines = []
    if kr_sells is None:
        lines.append("한국시장 매도 내역 조회 실패 또는 계좌 미확인")
        kr_sells = []
    if us_sells is None:
        lines.append("미국시장 매도 내역 조회 실패 또는 계좌 미확인")
        us_sells = []
    if not kr_sells and not us_sells:
        return "\n".join(lines) if lines else None

    # DB access remains on its owning thread; only KIS reads run off-thread.
    if kr_sells:
        try:
            from tracking.helpers import get_requested_session_prices
            prices = await asyncio.to_thread(
                get_requested_session_prices, [row[0] for row in kr_sells]
            )

            for ticker, name, sell_price in kr_sells:
                current_price = prices.get(ticker)
                if all(value is not None and 0 < value < float("inf")
                       for value in (sell_price, current_price)):
                    change_pct = (current_price - sell_price) / sell_price * 100
                    verdict = _sell_verdict(change_pct)
                    lines.append(
                        f"  {name}: 매도가 {sell_price:,.0f}원 → "
                        f"현재가 {current_price:,.0f}원 ({change_pct:+.1f}%) {verdict}"
                    )
                else:
                    lines.append(f"  {name}: 매도 후 가격 변화 미확인 (유효한 가격 없음)")
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
                    if len(tickers_list) == 1:
                        current_price = float(data['Close'].iloc[-1])
                    else:
                        current_price = float(data['Close'][ticker].iloc[-1])
                    if not all(value is not None and 0 < value < float("inf")
                               for value in (sell_price, current_price)):
                        lines.append(f"  {ticker}: 매도 후 가격 변화 미확인 (유효한 가격 없음)")
                        continue
                    change_pct = (current_price - sell_price) / sell_price * 100
                    verdict = _sell_verdict(change_pct)
                    lines.append(
                        f"  {ticker}: 매도가 ${sell_price:,.2f} → "
                        f"현재가 ${current_price:,.2f} ({change_pct:+.1f}%) {verdict}"
                    )
                except Exception:
                    lines.append(f"  {ticker}: 매도 후 가격 변화 미확인 (유효한 가격 없음)")
        except Exception as e:
            logger.warning(f"US price lookup failed: {e}")

    return "\n".join(lines) if lines else "매도 후 가격 조회 실패: 가격 변화를 확인할 수 없습니다."


def _get_ai_intuitions(cursor, week_start_str: str) -> str:
    """Keep current references, development ideas and unreviewed memory distinct."""
    rows = _safe_query_all(cursor, """
        SELECT * FROM trading_intuitions WHERE is_active=1 ORDER BY confidence DESC
    """)
    if rows is None:
        return "장기 학습 데이터 조회 실패: 누적 직관과 신뢰도를 확인할 수 없습니다."
    if not rows:
        return "아직 데이터 축적 중입니다. 매매 기록이 쌓이면 AI가 패턴을 학습합니다."
    columns = [column[0] for column in cursor.description]
    records = [dict(zip(columns, row)) for row in rows]
    current, improvements = [], []
    new_count = sum(str(row.get('created_at') or '') >= week_start_str for row in records)
    for row in records:
        market = 'KR' if row.get('market') is None else row['market']
        context = normalize_application_context(row.get('application_context'), market)
        row['application_context'] = context
        if context['status'] == 'current_pipeline':
            current.append(row)
        elif context['status'] == 'improvement':
            improvements.append(row)
    lines = [
        f"이번 주 신규 활성: {new_count}개 | 누적 활성 직관: {len(records)}개",
        f"현재 적용 참고: {len(current)}개 | 개선 제안: {len(improvements)}개 | "
        f"적용성 검토 대기: {len(records) - len(current) - len(improvements)}개",
        "※ 신뢰도는 검증된 적중률이 아닌 모델 자체 평가입니다.",
    ]
    if current:
        lines.append("\n💡 현재 시스템에서 참고할 직관:")
        representatives = []
        represented = set()
        for row in sorted(current, key=lambda item: item['application_context']['stage'] != 'batch_buy'):
            context = row['application_context']
            key = (context['market'], context['stage'])
            if key not in represented:
                representatives.append(row)
                represented.add(key)
        for index, row in enumerate(representatives[:3], 1):
            confidence = row.get('confidence')
            confidence_text = f"{confidence * 100:.0f}%" if confidence is not None else "미확인"
            context = row['application_context']
            stage = '진입 판단' if context['stage'] == 'batch_buy' else '보유 관리'
            lines.append(f"  {index}. [{context['market']}·{stage}] {row['condition']} → {row['insight']} (신뢰도 {confidence_text})")
    else:
        lines.append("현재 시스템 적용성 검토를 통과한 직관이 아직 없습니다.")
    if improvements:
        row = improvements[0]
        lines.append("\n🔧 향후 시스템 개선 검토 (매매 판단에는 전달하지 않음):")
        lines.append(f"  {row['condition']} → {row['insight']}")
    return "\n".join(lines)


def _get_trigger_section(cursor, market: str, week_start_str: str) -> str:
    """Render cumulative observations; absent data and query failures stay distinct."""
    if market == "KR":
        stats_query = """
            SELECT
                SUM(CASE WHEN was_traded=0 AND tracked_30d_return < -0.05 THEN 1 ELSE 0 END),
                AVG(CASE WHEN was_traded=0 AND tracked_30d_return < -0.05 THEN tracked_30d_return * 100 END),
                SUM(CASE WHEN was_traded=0 AND tracked_30d_return > 0.10 THEN 1 ELSE 0 END),
                MAX(CASE WHEN was_traded=0 AND tracked_30d_return > 0.10 THEN tracked_30d_return * 100 END),
                MIN(analyzed_date), MAX(analyzed_date), COUNT(*),
                SUM(CASE WHEN was_traded IS NULL THEN 1 ELSE 0 END)
            FROM analysis_performance_tracker
            WHERE tracking_status='completed' AND tracked_30d_return IS NOT NULL
        """
        best_query = """
            SELECT trigger_type, COUNT(*) AS samples,
                   SUM(CASE WHEN tracked_30d_return > 0 THEN 1 ELSE 0 END) AS wins
            FROM analysis_performance_tracker
            WHERE tracking_status='completed' AND tracked_30d_return IS NOT NULL
              AND trigger_type IS NOT NULL
            GROUP BY trigger_type HAVING COUNT(*) >= 3
            ORDER BY (wins * 1.0 / samples) DESC, samples DESC, trigger_type LIMIT 1
        """
    else:
        stats_query = """
            SELECT
                SUM(CASE WHEN was_traded=0 AND return_30d < -0.05 THEN 1 ELSE 0 END),
                AVG(CASE WHEN was_traded=0 AND return_30d < -0.05 THEN return_30d * 100 END),
                SUM(CASE WHEN was_traded=0 AND return_30d > 0.10 THEN 1 ELSE 0 END),
                MAX(CASE WHEN was_traded=0 AND return_30d > 0.10 THEN return_30d * 100 END),
                MIN(analysis_date), MAX(analysis_date), COUNT(*),
                SUM(CASE WHEN was_traded IS NULL THEN 1 ELSE 0 END)
            FROM us_analysis_performance_tracker WHERE return_30d IS NOT NULL
        """
        best_query = """
            SELECT trigger_type, COUNT(*) AS samples,
                   SUM(CASE WHEN return_30d > 0 THEN 1 ELSE 0 END) AS wins
            FROM us_analysis_performance_tracker
            WHERE return_30d IS NOT NULL AND trigger_type IS NOT NULL
            GROUP BY trigger_type HAVING COUNT(*) >= 3
            ORDER BY (wins * 1.0 / samples) DESC, samples DESC, trigger_type LIMIT 1
        """

    stats = _safe_query(cursor, stats_query)
    best = _safe_query_all(cursor, best_query)
    principles = _safe_query(cursor, """
        SELECT SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), COUNT(*)
        FROM trading_principles WHERE is_active=1 AND market=?
    """, (week_start_str, market))
    lines = []
    if stats is None or best is None:
        lines.append("트리거 데이터 조회 실패: 누적 통계를 확인할 수 없습니다.")
    else:
        down, avg, up, maximum, start, end, count, unknown = stats
        if count:
            lines.append(f"📅 누적 표본의 분석일: {start} ~ {end} | 30일 수익률 확인: 분석 기록 {count}건")
        else:
            lines.append("📅 30일 수익률이 확인된 누적 표본이 없습니다.")
        lines.append(f"📉 미매수 후 하락: {down or 0}건 (평균 {_format_percentage(avg)})")
        lines.append(f"📈 미매수 후 상승: {up or 0}건 (최고 {_format_percentage(maximum)})")
        if unknown:
            lines.append(f"※ 매매 여부 미확인 {unknown}건은 미매수 등락 집계에서 제외했습니다.")
        if best:
            trigger, samples, wins = best[0]
            lines.append(f"📊 관측 상승 비율 상위: {trigger} ({wins}/{samples}건, {wins / samples * 100:.0f}%)")
        else:
            lines.append("📊 관측 상승 비율: 트리거별 표본 3건 미만")
    if principles is None:
        lines.append("📌 교훈 조회 실패")
    else:
        lines.append(f"📌 이번 주 등록 교훈: {principles[0] or 0}개 (누적 활성 {principles[1]}개, 적용성 별도 검토)")
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
    sell_eval = await _get_sell_evaluation(cursor, week_start_str)

    kr_section = _get_trigger_section(cursor, "KR", week_start_str)
    us_section = _get_trigger_section(cursor, "US", week_start_str)
    intuitions_section = _get_ai_intuitions(cursor, week_start_str)
    conn.close()

    summary = "누적 가격 관측만으로 매매 판단의 우수성이나 전략 변경 필요성을 판단할 수 없습니다."
    insights_str = (
        "  → 트리거 통계는 이번 주 실적이 아닌 누적 30일 추적 결과입니다.\n"
        "  → 표본 수와 관측 기간이 다르므로 상승 비율만으로 트리거의 안정성을 비교할 수 없습니다.\n"
        "  → 미매수 종목의 등락은 실제 회피 손익이 아니며, 매수 기준 완화의 근거가 되지 않습니다."
    )

    # Conditional sell evaluation section
    sell_eval_block = ""
    if sell_eval:
        sell_eval_block = f"""
🔍 매도 후 가격 관측
━━━━━━━━━━━━━━━━━━━━
{sell_eval}
"""

    message = f"""📋 PRISM 주간 인사이트 ({start_display} ~ {end_display})
이번 주 매매 내역과 누적 학습·가격 관측을 정리합니다.

📈 이번 주 매매 요약
━━━━━━━━━━━━━━━━━━━━
{trades_summary}
{sell_eval_block}
🇰🇷 한국시장 (누적 트리거 관측)
━━━━━━━━━━━━━━━━━━━━
{kr_section}

🇺🇸 미국시장 (누적 트리거 관측)
━━━━━━━━━━━━━━━━━━━━
{us_section}

🧠 AI 장기 학습 인사이트
━━━━━━━━━━━━━━━━━━━━
{intuitions_section}

📌 이번 주 인사이트
{insights_str}

💡 핵심: {summary}

ℹ️ 용어 안내
• 트리거 = AI가 종목을 발견한 이유 (급등, 거래량 급증 등)
• 미매수 후 하락/상승 = 미매수로 기록된 분석 건의 30일 수익률이 -5% 미만/+10% 초과인 경우
• 상승 비율 = 30일 수익률이 확인된 분석 건 중 양수인 비율(실제 매매 승률 아님)
• 분석 건은 같은 종목의 반복 분석을 포함할 수 있으며, 가격·기업행사 조정 품질은 별도 검증이 필요합니다.
• 교훈·직관 = 과거 거래와 반복 패턴에서 추출한 참고사항이며, 기존 매매 규칙을 바꾸는 권한은 아닙니다.
• 개선 제안 = 기능 구현과 별도 검증이 필요한 내용으로, 현재 매매 판단에는 전달하지 않습니다."""

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
                    message, model="gpt-6-luna", from_lang="ko", to_lang=lang
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
