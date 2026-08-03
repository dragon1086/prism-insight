#!/usr/bin/env python3
"""
Weekly Firecrawl Intelligence Report — AI-powered market research via Firecrawl Agent
Generates KR and US market intelligence reports and sends to Telegram channel.

Usage:
    python3 weekly_firecrawl_intelligence.py                              # Send to Telegram
    python3 weekly_firecrawl_intelligence.py --dry-run                     # Print only
    python3 weekly_firecrawl_intelligence.py --broadcast-languages en,ja   # With broadcast

# Crontab entry (add to server):
# 주간 Firecrawl 인텔리전스 (매주 일요일 11:00 KST)
# 0 11 * * 0 cd /root/prism-insight && /root/.pyenv/shims/python weekly_firecrawl_intelligence.py >> /root/prism-insight/logs/weekly_firecrawl_intelligence.log 2>&1
"""
import argparse
import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

_DISCLAIMER = "\n\n⚠️ 본 내용은 투자 참고용이며, 투자 판단의 책임은 본인에게 있습니다."


# 1차 매체만 허용. 블로그·커뮤니티가 검색 상위를 먹으면 리포트 수치가 오염된다.
# Shared with the Telegram bot commands so the two paths cannot drift apart.
from cores.search_presets import (  # noqa: E402
    KR_PRIMARY_DOMAINS as _KR_DOMAINS,
    US_PRIMARY_DOMAINS as _US_DOMAINS,
)


async def _generate_kr_report(start, end) -> str:
    """Generate KR market intelligence report via Firecrawl search + Claude."""
    from report_generator import generate_firecrawl_search_response
    from weekly_market_facts import build_kr_facts

    period = f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
    md = f"{start:%m월 %d일}~{end:%m월 %d일}"

    # 쿼리에 오늘 날짜 문자열을 박으면 검색엔진이 그 문자열을 포함한 블로그를 물어온다.
    # 날짜는 tbs 최신성 필터로 제어하고, 쿼리는 주제별로 쪼갠다.
    queries = [
        "코스피 코스닥 주간 증시 마감 시황 정리",
        "외국인 기관 순매수 순매도 수급 동향 코스피",
        "이번주 증시 주도 테마 급등 업종 특징주",
        "다음주 증시 전망 주요 경제지표 실적발표 일정",
    ]

    analysis_prompt = (
        f"위 자료를 바탕으로 {md}({period}) 한 주간의 한국 주식시장 인텔리전스 리포트를 작성해줘.\n\n"
        "포함 내용:\n"
        f"1. {md} KOSPI/KOSDAQ 주요 흐름 요약 (지수 수치는 검증 데이터 사용)\n"
        "2. 가장 주목받은 테마 3개와 대표 종목 (종목명을 반드시 명시)\n"
        "3. 외국인/기관 수급 동향 (금액은 검증 데이터의 주간 누적 순매수 사용)\n"
        "4. 다음 주 주요 일정 및 이벤트\n"
        "5. 개인투자자를 위한 전략 제안\n\n"
        "텔레그램 메시지 형태로 이모지 포함하여 작성. 4000자 이내."
    )

    result = await generate_firecrawl_search_response(
        queries,
        analysis_prompt,
        limit=8,
        tbs="qdr:w",                 # 최근 1주일 내 문서만
        sources=["news", "web"],     # 뉴스 채널은 발행일이 붙어 필터링이 가능하다
        location="KR",
        include_domains=_KR_DOMAINS,
        grounded_facts=build_kr_facts(start, end),
        period_label=period,
    )
    if not result:
        logger.error("Failed to generate KR intelligence report")
        return ""
    return result


async def _generate_us_report(start, end) -> str:
    """Generate US market intelligence report via Firecrawl search + Claude."""
    from report_generator import generate_firecrawl_search_response
    from weekly_market_facts import build_us_facts

    period = f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
    md = f"{start:%m월 %d일}~{end:%m월 %d일}"

    queries = [
        "stock market weekly recap S&P 500 Nasdaq close",
        "sector performance this week best worst performing stocks",
        "Federal Reserve rate outlook Treasury yields this week",
        "week ahead economic calendar earnings preview",
    ]

    analysis_prompt = (
        f"위 자료를 바탕으로 {md}({period}) 한 주간의 미국 주식시장 인텔리전스 리포트를 작성해줘.\n\n"
        "포함 내용:\n"
        f"1. {md} S&P500/NASDAQ 주요 흐름 요약 (지수 수치는 검증 데이터 사용)\n"
        "2. 가장 주목받은 섹터 3개와 대표 종목 (티커 명시)\n"
        "3. 연준(Fed) 관련 동향 및 금리 전망\n"
        "4. 다음 주 주요 일정 (FOMC, 실적 발표 등)\n"
        "5. 개인투자자를 위한 전략 제안\n\n"
        "한국어로, 텔레그램 메시지 형태로 이모지 포함하여 작성. 4000자 이내."
    )

    result = await generate_firecrawl_search_response(
        queries,
        analysis_prompt,
        limit=8,
        tbs="qdr:w",
        sources=["news", "web"],
        location="US",
        include_domains=_US_DOMAINS,
        grounded_facts=build_us_facts(start, end),
        period_label=period,
    )
    if not result:
        logger.error("Failed to generate US intelligence report")
        return ""
    return result


REPORT_SNAPSHOT = Path(__file__).parent / "logs" / "weekly_firecrawl_last_report.txt"


def _save_report_snapshot(message: str, start, end) -> None:
    """
    Persist the delivered report so the quality checker can grade it.

    Never let a monitoring concern break delivery — any failure here is logged
    and swallowed.
    """
    try:
        REPORT_SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        header = f"# period: {start} ~ {end}\n# generated: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        REPORT_SNAPSHOT.write_text(header + message, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - monitoring must not break the report
        logger.warning(f"Failed to save report snapshot: {e}")


async def generate_weekly_intelligence() -> str:
    """Generate combined weekly intelligence report."""
    from weekly_market_facts import resolve_week_range

    today = datetime.now()
    date_display = today.strftime("%-m/%-d")
    start, end = resolve_week_range(today.date())
    logger.info(f"Weekly intelligence period: {start} ~ {end}")

    kr_report = await _generate_kr_report(start, end)
    us_report = await _generate_us_report(start, end)

    sections = [
        f"🔥 PRISM 주간 Firecrawl 인텔리전스 ({date_display})",
        f"📅 대상 기간: {start:%Y.%m.%d} ~ {end:%m.%d}",
    ]

    if kr_report:
        sections.append(f"\n🇰🇷 한국시장 인텔리전스\n━━━━━━━━━━━━━━━━━━━━\n{kr_report}")

    if us_report:
        sections.append(f"\n🇺🇸 미국시장 인텔리전스\n━━━━━━━━━━━━━━━━━━━━\n{us_report}")

    if not kr_report and not us_report:
        sections.append("\n⚠️ Firecrawl 리포트 생성에 실패했습니다. 로그를 확인해주세요.")

    sections.append(_DISCLAIMER)

    message = "\n".join(sections)
    _save_report_snapshot(message, start, end)
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
        # Split message if it exceeds Telegram limit
        if len(message) > 4096:
            # Send in chunks
            for i in range(0, len(message), 4096):
                chunk = message[i:i + 4096]
                await bot.send_message(chat_id=channel_id, text=chunk, parse_mode=None)
        else:
            await bot.send_message(chat_id=channel_id, text=message, parse_mode=None)
        logger.info("Weekly Firecrawl intelligence sent to Telegram successfully")
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

                logger.info(f"Translating intelligence report to {lang}")
                translated = await translate_telegram_message(
                    message, model="gpt-5.6-luna", from_lang="ko", to_lang=lang
                )
                if len(translated) > 4096:
                    for i in range(0, len(translated), 4096):
                        chunk = translated[i:i + 4096]
                        await bot.send_message(chat_id=channel_id, text=chunk, parse_mode=None)
                else:
                    await bot.send_message(chat_id=channel_id, text=translated, parse_mode=None)
                logger.info(f"Intelligence report sent to {lang} channel")

            except Exception as e:
                logger.error(f"Broadcast to {lang} failed: {e}")

    except Exception as e:
        logger.error(f"Broadcast error: {e}")


def main():
    parser = argparse.ArgumentParser(description="Weekly Firecrawl Intelligence Report")
    parser.add_argument("--dry-run", action="store_true", help="Print only, don't send")
    parser.add_argument("--broadcast-languages", type=str, default="",
                        help="Broadcast languages (comma-separated, e.g., 'en,ja,zh')")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    async def _run():
        message = await generate_weekly_intelligence()
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
        logger.error(f"Failed to generate intelligence report: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
