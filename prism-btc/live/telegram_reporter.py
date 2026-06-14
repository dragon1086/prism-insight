# live/telegram_reporter.py — 텔레그램 정기 현황 리포터 (spec §4)
#
#   python -m live.telegram_reporter --mode demo [--channel ID] [--root-db PATH]
#
# 운영 중인 모드(기본 demo)의 현재 상태를 트레이더 친화 한국어 Markdown 으로
# 만들어 텔레그램 채널에 전송한다. 채널 미설정/패키지 없음/전송 실패는 전부
# 흡수 → stdout 출력만 (절대 크래시 금지 — Rocky 가 채널ID 안 줬을 수 있음).
#
# 인프라 재사용:
#   - 루트 tracking/telegram.py 의 TelegramSender (python-telegram-bot>=20).
#     없으면 telegram.Bot 직접 사용으로 폴백.
#   - 모든 수치는 live.tracking 조회 함수 + btc_*(해당 mode) 직접 SQL 에서.
#
# 데이터 없으면 "데이터 없음" / 포지션 없으면 "관망 중" 으로 graceful 표기.
from __future__ import annotations

import argparse
import asyncio
import logging
import os
from datetime import datetime, timezone

from live import tracking

log = logging.getLogger("live.telegram_reporter")

_SYMBOL = "BTCUSDT"


# ---------------------------------------------------------------------------
# .env 로드 — 루트(prism-insight) .env 우선, 없으면 무시 (이미 환경에 있을 수도).
# ---------------------------------------------------------------------------

def _load_env() -> None:
    """루트 .env 를 best-effort 로 로드. python-dotenv 없으면 조용히 스킵."""
    try:
        from pathlib import Path
        from dotenv import load_dotenv
        # prism-btc/live/telegram_reporter.py → prism-insight/.env
        root_env = Path(__file__).resolve().parent.parent.parent / ".env"
        if root_env.exists():
            load_dotenv(root_env)
    except Exception:  # noqa: BLE001 — dotenv 없거나 실패해도 무해
        pass


def _resolve_channel(cli_channel: str | None) -> str | None:
    """채널 ID 해석: CLI > BTC_TELEGRAM_CHANNEL_ID > TELEGRAM_CHANNEL_ID > None."""
    if cli_channel:
        return cli_channel
    return (os.environ.get("BTC_TELEGRAM_CHANNEL_ID")
            or os.environ.get("TELEGRAM_CHANNEL_ID")
            or None)


# ---------------------------------------------------------------------------
# 수치 조회 — 전부 실패 흡수. 값 없으면 None/빈값 반환.
# ---------------------------------------------------------------------------

def _first_equity(conn, mode: str) -> float | None:
    r = conn.execute(
        "SELECT equity FROM btc_equity_curve WHERE mode=? ORDER BY id ASC LIMIT 1",
        (mode,),
    ).fetchone()
    return float(r["equity"]) if r is not None else None


def _uptime_days(conn, mode: str) -> float | None:
    """첫 equity 기록 ~ 지금까지 가동일수 (없으면 None)."""
    r = conn.execute(
        "SELECT ts FROM btc_equity_curve WHERE mode=? ORDER BY id ASC LIMIT 1",
        (mode,),
    ).fetchone()
    if r is None:
        return None
    try:
        first = datetime.fromisoformat(str(r["ts"]).replace("Z", "+00:00"))
        if first.tzinfo is None:
            first = first.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - first).total_seconds() / 86400.0
    except Exception:  # noqa: BLE001
        return None


def _open_positions(conn, mode: str) -> list:
    try:
        return tracking.load_open_positions(conn, mode)
    except Exception:  # noqa: BLE001
        return []


def _recent_trades(conn, mode: str, limit: int = 3) -> list:
    try:
        rows = conn.execute(
            "SELECT side, r_multiple, exit_reason, exit_time "
            "FROM btc_trading_history WHERE mode=? ORDER BY id DESC LIMIT ?",
            (mode, limit),
        ).fetchall()
        return list(rows)
    except Exception:  # noqa: BLE001
        return []


def _cumulative_stats(conn, mode: str) -> dict:
    """누적 통계: 트레이드수/승률/PF/평균R (mode 필터)."""
    out = {"n": 0, "win_rate": None, "pf": None, "avg_r": None}
    try:
        rows = conn.execute(
            "SELECT r_multiple, net_pnl FROM btc_trading_history WHERE mode=?",
            (mode,),
        ).fetchall()
    except Exception:  # noqa: BLE001
        return out
    n = len(rows)
    out["n"] = n
    if n == 0:
        return out
    rs = [float(r["r_multiple"]) for r in rows]
    pnls = [float(r["net_pnl"]) for r in rows]
    wins = [p for p in pnls if p > 0]
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = -sum(p for p in pnls if p < 0)
    out["win_rate"] = 100.0 * len(wins) / n
    out["avg_r"] = sum(rs) / n
    out["pf"] = (gross_win / gross_loss) if gross_loss > 0 else None
    return out


def _last_signal(conn, mode: str) -> dict | None:
    try:
        r = conn.execute(
            "SELECT ts, score, ts_4h, side FROM btc_signal_log "
            "WHERE mode=? ORDER BY id DESC LIMIT 1",
            (mode,),
        ).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if r is None:
        return None
    return {"ts": r["ts"], "score": r["score"], "ts_4h": r["ts_4h"], "side": r["side"]}


def _last_price(conn) -> float | None:
    """현재가 — market.db 30m 마지막 종가에서 best-effort 조회."""
    try:
        from collector.store import get_connection as market_connection
        mc = market_connection()
        try:
            r = mc.execute(
                "SELECT close FROM klines WHERE timeframe='30m' "
                "ORDER BY open_time DESC LIMIT 1"
            ).fetchone()
            return float(r[0]) if r is not None else None
        finally:
            mc.close()
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------------------
# 메시지 빌드 — Markdown, 한국어, 트레이더 친화.
# ---------------------------------------------------------------------------

def _fmt_pct(v: float | None) -> str:
    return f"{v:+.2f}%" if v is not None else "데이터 없음"


def _fmt_num(v: float | None, suffix: str = "") -> str:
    return f"{v:,.2f}{suffix}" if v is not None else "데이터 없음"


def _unrealized_r(pos, cur_price: float | None) -> float | None:
    """현재가 기준 미실현 R (initial_risk=가격거리*qty 기준 역산)."""
    if cur_price is None or not pos.initial_risk:
        return None
    sign = 1.0 if pos.side == "long" else -1.0
    pnl = (cur_price - pos.entry_price) * pos.qty * sign
    try:
        return pnl / pos.initial_risk
    except Exception:  # noqa: BLE001
        return None


# 청산 사유 → 일반인 한국어. 내부 코드값을 사람이 읽는 말로 바꾼다.
_EXIT_REASON_KR = {
    "tp1": "1차 목표 도달", "tp2": "2차 목표 도달", "tp3": "최종 목표 도달",
    "sl": "손절(약속한 손실선)", "trail": "추세 꺾여 익절 마감",
    "signal_exit": "추세 종료 신호로 정리", "signal_reduce": "비중 축소",
    "liq_forced_reduce": "위험 관리 자동 축소", "be": "본전 부근 정리",
}


def _reason_kr(reason: str) -> str:
    return _EXIT_REASON_KR.get(str(reason), str(reason))


def _side_kr(side: str) -> str:
    # 일반인에게 롱/숏보다 직관적인 표현.
    return "📈 상승 베팅" if side == "long" else "📉 하락 베팅"


def build_message(conn, mode: str) -> str:
    """비트코인 자동매매 현황 — 한국 일반인이 바로 이해하는 표현으로.

    전문용어(롱/숏/R/PF/MDD/섀도우 등) 대신 쉬운 말로 풀어 쓴다.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    equity = tracking.latest_equity(conn, mode)
    peak = tracking.peak_equity(conn, mode)
    first_eq = _first_equity(conn, mode)
    days = _uptime_days(conn, mode)
    cur_price = _last_price(conn)

    ret_pct = None
    if equity is not None and first_eq:
        ret_pct = 100.0 * (equity - first_eq) / first_eq
    dd_pct = None
    if equity is not None and peak:
        dd_pct = 100.0 * (equity - peak) / peak

    # 모드 라벨 — 일반인용. demo = 진짜 거래소에서 가상 자금으로 검증 중.
    mode_kr = {"demo": "모의투자 (가상자금 1만 달러)",
               "live": "실전투자",
               "shadow": "시뮬레이션"}.get(mode, mode)

    lines: list[str] = []
    lines.append("🤖 *비트코인 자동매매 현황*")
    days_str = f"{days:.0f}일째" if days is not None else "시작 단계"
    lines.append(f"_{mode_kr} · {days_str} · {now}_")
    lines.append("")

    # 1) 지금 돈이 얼마인가
    lines.append("💰 *지금 자산*")
    if equity is None:
        lines.append("• 집계 준비 중")
    else:
        lines.append(f"• 평가금액: *{equity:,.0f} 달러*")
        if ret_pct is not None:
            verb = "수익" if ret_pct >= 0 else "손실"
            lines.append(f"• 시작 대비: {ret_pct:+.1f}% ({verb})")
        if dd_pct is not None and dd_pct < -0.05:
            # 고점에서 현재까지 빠진 폭 (지금 고점이면 생략).
            lines.append(f"• 고점에서 지금: {dd_pct:.1f}% (잠깐 눌린 정도)")
    lines.append("")

    # 2) 지금 사고 있나
    lines.append("📊 *지금 포지션*")
    positions = _open_positions(conn, mode)
    if not positions:
        lines.append("• 관망 중 (좋은 기회를 기다리는 중)")
    else:
        for p in positions:
            ur = _unrealized_r(p, cur_price)
            if ur is not None:
                tail = f"현재 {'수익권' if ur >= 0 else '손실권'} ({ur:+.1f}배)"
            else:
                tail = "진행 중"
            lines.append(
                f"• {_side_kr(p.side)} · 진입가 {p.entry_price:,.0f}달러 · {tail}"
            )
    lines.append("")

    # 3) 최근 거래 결과
    lines.append("📒 *최근 거래 (최대 3건)*")
    trades = _recent_trades(conn, mode, 3)
    if not trades:
        lines.append("• 아직 마감된 거래 없음")
    else:
        for t in trades:
            r = float(t["r_multiple"])
            mark = "✅ 이익" if r > 0 else "❌ 손실"
            lines.append(
                f"• {mark} {r:+.1f}배 · {_side_kr(t['side'])} · {_reason_kr(t['exit_reason'])}"
            )
    lines.append("")

    # 4) 누적 성적
    stats = _cumulative_stats(conn, mode)
    lines.append("🏆 *누적 성적*")
    if stats["n"] == 0:
        lines.append("• 아직 데이터 쌓는 중")
    else:
        wr = f"{stats['win_rate']:.0f}%" if stats["win_rate"] is not None else "—"
        avgr = f"{stats['avg_r']:+.1f}배" if stats["avg_r"] is not None else "—"
        parts = [f"총 {stats['n']}회 거래", f"승률 {wr}", f"평균 {avgr}"]
        if stats["pf"] is not None:
            parts.append(f"번 돈이 잃은 돈의 {stats['pf']:.1f}배")
        lines.append("• " + " · ".join(parts))
    lines.append("")

    # 5) 지금 시장을 보는 눈 (신호) — 점수/추세강도 숫자 대신 말로.
    sig = _last_signal(conn, mode)
    lines.append("🔭 *지금 시장 판단*")
    if sig is None or sig.get("side") is None:
        lines.append("• 분석 준비 중")
    else:
        side = sig["side"]
        if side == "none":
            lines.append("• 뚜렷한 추세 없음 → 진입 보류 (무리하지 않음)")
        else:
            lines.append(f"• {_side_kr(side)} 신호 포착 — 조건 확인 중")
    lines.append("")

    # 꼬리말 — 용어 설명 한 줄 + 면책.
    lines.append("_※ '배'는 한 번에 감수한 위험 대비 수익 비율입니다 "
                 "(예: +2배 = 건 위험의 2배를 벌었다는 뜻)._")
    if mode == "demo":
        lines.append("_※ 현재는 가상자금 모의투자 단계로, 실제 입출금은 없습니다._")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 전송 — TelegramSender 재사용, 실패 시 직접 Bot, 채널 없으면 stdout.
# ---------------------------------------------------------------------------

async def _send(token: str | None, channel: str | None, message: str) -> bool:
    """텔레그램 전송. 토큰/채널 없으면 stdout 출력 후 True (스킵=성공 취급)."""
    if not token or not channel:
        print("[telegram_reporter] 채널/토큰 미설정 — 전송 스킵, stdout 출력:")
        print(message)
        return True

    # 1) Bot 인스턴스 (python-telegram-bot).
    try:
        from telegram import Bot
        bot = Bot(token=token)
    except Exception as exc:  # noqa: BLE001 — 패키지 없음/초기화 실패
        print(f"[telegram_reporter] telegram.Bot 초기화 실패 ({exc}) — stdout 출력:")
        print(message)
        return False

    # 2) 루트 TelegramSender 재사용 (없으면 직접 Bot.send_message 폴백).
    try:
        from tracking.telegram import TelegramSender
        sender = TelegramSender(bot)
        ok = await sender.send_messages(channel, [message], language="ko")
        return bool(ok)
    except Exception as exc:  # noqa: BLE001 — 임포트/전송 실패 → 직접 폴백
        log.warning("TelegramSender 폴백 (%s) → 직접 Bot.send_message", exc)

    try:
        await bot.send_message(chat_id=channel, text=message,
                               parse_mode="Markdown")
        return True
    except Exception as exc:  # noqa: BLE001 — 직접 전송도 실패하면 stdout
        print(f"[telegram_reporter] 전송 실패 ({exc}) — stdout 출력:")
        print(message)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="prism-btc telegram 현황 리포터")
    parser.add_argument("--mode", default="demo",
                        choices=["shadow", "demo", "live"])
    parser.add_argument("--channel", default=None, help="채널 ID 오버라이드")
    parser.add_argument("--root-db", default=None, help="root tracking db 경로")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    _load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = _resolve_channel(args.channel)

    conn = tracking.get_connection(args.root_db)
    try:
        tracking.ensure_schema(conn)
        message = build_message(conn, args.mode)
    finally:
        conn.close()

    asyncio.run(_send(token, channel, message))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
