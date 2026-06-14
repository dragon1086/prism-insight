# live/notifier.py — 실시간 매매 이벤트 텔레그램 알림 (진입/추가/청산 즉시 발송)
#
# telegram_reporter 는 하루 1회 "현황 스냅샷"을 보낸다. 이 모듈은 그 사이사이의
# 실제 사건(새 진입/비중 추가/포지션 정리)이 발생할 때마다 즉시 1건씩 알린다.
#
# 멱등 감지 (btc_meta 마커 기반):
#   - 진입/추가진입: btc_positions 의 autoincrement id 가 마커보다 큰 신규 행.
#     id 는 단조증가하며 삭제된 행 id 는 재등장하지 않으므로, "마지막으로 알린 id"
#     보다 큰 행만 보면 중복 없이 정확히 한 번씩 감지된다.
#   - 청산: btc_trading_history 는 불변(append-only) 이므로 같은 마커 전략을 쓴다.
#
# 콜드스타트 가드: 마커가 없으면(첫 실행) 과거 행을 폭주 전송하지 않고 현재 max(id)
# 로 마커만 세팅한다. 이후부터 실제 신규 사건만 알린다.
#
# 안전 원칙: 모든 SQL/전송 실패를 흡수한다. 어떤 예외도 밖으로 던지지 않는다
# (데몬 tick 비중단). 토큰/채널 미설정 시 stdout 폴백 (크래시 금지).
from __future__ import annotations

import asyncio
import logging
import os

from live import tracking
from live.telegram_reporter import (
    _send,
    _load_env,
    _resolve_channel,
    _side_kr,
    _reason_kr,
)

log = logging.getLogger("live.notifier")

# btc_meta 마커 키 (mode 별로 독립).
_MARK_ENTRY = "last_notified_entry_id"
_MARK_EXIT = "last_notified_exit_id"


# ---------------------------------------------------------------------------
# 메시지 빌드 — 한국어, 일반인 친화, 시범운용 명시.
# ---------------------------------------------------------------------------

def _disclaimer() -> str:
    return "_가상자금 모의투자입니다_"


def _mode_tag(mode: str) -> str:
    # demo = 시범운용(모의투자), live = 실전.
    return "시범운용" if mode == "demo" else "실전"


def _build_entry_message(row, mode: str) -> str:
    """진입/추가진입 알림 1건. tranche_index 로 신규 vs 비중 추가 구분."""
    tag = _mode_tag(mode)
    side = _side_kr(row["side"])
    entry = float(row["entry_price"])
    sl = float(row["sl_price"])
    lev = float(row["leverage"])
    tranche = int(row["tranche_index"])

    if tranche <= 0:
        # 신규 진입 (tranche 0).
        head = f"🟢 [{tag}] 새 진입 — {side}"
        body = (f"진입가 {entry:,.0f}달러 · 손절 {sl:,.0f}달러 · "
                f"{lev:.0f}배율")
    else:
        # 비중 추가 (피라미딩). 사람이 읽는 N/3 표기.
        head = f"🟢 [{tag}] 비중 추가 ({tranche + 1}/3) — {side}"
        body = f"진입가 {entry:,.0f}달러"

    return f"{head}\n{body}\n{_disclaimer()}"


def _build_exit_message(row, mode: str) -> str:
    """청산 알림 1건. r_multiple 로 이익/손실, exit_reason 한글화."""
    tag = _mode_tag(mode)
    r = float(row["r_multiple"])
    reason = _reason_kr(row["exit_reason"])
    if r > 0:
        outcome = f"✅ 이익 {r:+.1f}배"
    else:
        outcome = f"❌ 손실 {r:+.1f}배"
    head = f"🔵 [{tag}] 포지션 정리 — {outcome} ({reason})"
    return f"{head}\n{_disclaimer()}"


# ---------------------------------------------------------------------------
# 전송 — telegram_reporter._send 재사용 (asyncio.run). 실패 흡수.
# ---------------------------------------------------------------------------

def _dispatch(messages: list[str]) -> None:
    """메시지들을 순서대로 전송. 토큰/채널 없으면 _send 가 stdout 폴백한다."""
    if not messages:
        return
    try:
        _load_env()
    except Exception:  # noqa: BLE001 — env 로드 실패해도 환경에 이미 있을 수 있음
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = _resolve_channel(None)
    for msg in messages:
        try:
            asyncio.run(_send(token, channel, msg))
        except Exception as exc:  # noqa: BLE001 — 전송 실패 절대 비전파
            log.warning("notifier 전송 실패 (흡수): %s", exc)


# ---------------------------------------------------------------------------
# 핵심 진입점 — 신규 이벤트 감지 후 알림.
# ---------------------------------------------------------------------------

def _max_id(conn, table: str, mode: str):
    """해당 mode 의 max(id). 행이 없으면 None."""
    r = conn.execute(
        f"SELECT MAX(id) AS m FROM {table} WHERE mode=?", (mode,)
    ).fetchone()
    return None if r is None or r["m"] is None else int(r["m"])


def notify_new_events(conn, mode: str = "demo") -> dict:
    """신규 진입/추가/청산 이벤트를 감지해 즉시 텔레그램 알림.

    멱등: btc_meta 마커(last_notified_*_id) 보다 큰 id 만 처리하고, 전송 후 마커를
    max(id) 로 갱신한다. 콜드스타트(마커 없음)는 전송 없이 마커만 세팅한다.

    어떤 예외도 밖으로 던지지 않는다. 반환은 {"entries", "exits"} 카운트 (디버그용).
    """
    result = {"entries": 0, "exits": 0}
    try:
        # --- 진입/추가진입 (btc_positions) ---
        entry_marker = tracking.get_meta(conn, _MARK_ENTRY, mode)
        if entry_marker is None:
            # 콜드스타트: 현재 max(id) 로 마커만 세팅 (과거 폭주 전송 안 함).
            cur_max = _max_id(conn, "btc_positions", mode)
            tracking.set_meta(conn, _MARK_ENTRY, cur_max if cur_max is not None else 0, mode)
        else:
            rows = conn.execute(
                "SELECT * FROM btc_positions WHERE mode=? AND id > ? ORDER BY id ASC",
                (mode, int(entry_marker)),
            ).fetchall()
            if rows:
                msgs = []
                for row in rows:
                    try:
                        msgs.append(_build_entry_message(row, mode))
                    except Exception as exc:  # noqa: BLE001 — 1건 실패가 전체를 못 막음
                        log.warning("진입 메시지 빌드 실패 (흡수): %s", exc)
                _dispatch(msgs)
                tracking.set_meta(conn, _MARK_ENTRY, int(rows[-1]["id"]), mode)
                result["entries"] = len(rows)
    except Exception as exc:  # noqa: BLE001 — 진입 알림 실패 절대 비전파
        log.warning("notify entries 실패 (흡수): %s", exc)

    try:
        # --- 청산 (btc_trading_history, 불변 행) ---
        exit_marker = tracking.get_meta(conn, _MARK_EXIT, mode)
        if exit_marker is None:
            cur_max = _max_id(conn, "btc_trading_history", mode)
            tracking.set_meta(conn, _MARK_EXIT, cur_max if cur_max is not None else 0, mode)
        else:
            rows = conn.execute(
                "SELECT * FROM btc_trading_history WHERE mode=? AND id > ? ORDER BY id ASC",
                (mode, int(exit_marker)),
            ).fetchall()
            if rows:
                msgs = []
                for row in rows:
                    try:
                        msgs.append(_build_exit_message(row, mode))
                    except Exception as exc:  # noqa: BLE001
                        log.warning("청산 메시지 빌드 실패 (흡수): %s", exc)
                _dispatch(msgs)
                tracking.set_meta(conn, _MARK_EXIT, int(rows[-1]["id"]), mode)
                result["exits"] = len(rows)
    except Exception as exc:  # noqa: BLE001 — 청산 알림 실패 절대 비전파
        log.warning("notify exits 실패 (흡수): %s", exc)

    return result
