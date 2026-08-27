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
from typing import Any

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


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def _row_value(row, key: str, default: Any = None) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _pct(amount: float, equity: float | None) -> str:
    if equity is None or equity <= 0:
        return "비율 계산 불가"
    return f"계좌의 {amount / equity * 100:.2f}%"


def _margin_mode_kr(raw: str | None) -> str:
    return {
        "REGULAR_MARGIN": "교차마진(Cross)",
        "ISOLATED_MARGIN": "격리마진(Isolated)",
        "PORTFOLIO_MARGIN": "포트폴리오마진(Portfolio)",
    }.get(str(raw or ""), "마진 방식 확인 불가")


def _position_mode_kr(position_idx: Any) -> str:
    try:
        idx = int(position_idx)
    except (TypeError, ValueError):
        return "포지션 모드 확인 불가"
    return "단방향(One-way)" if idx == 0 else "헤지모드(Hedge)"


def _account_context(conn, mode: str, row=None) -> dict[str, Any]:
    """Combine the last exchange snapshot with local ledger fallbacks."""
    snapshot = tracking.get_meta(conn, "account_snapshot", mode)
    if not isinstance(snapshot, dict):
        snapshot = {}
    account = snapshot.get("account") if isinstance(snapshot.get("account"), dict) else {}
    wallet = snapshot.get("wallet") if isinstance(snapshot.get("wallet"), dict) else {}
    exchange_position = (
        snapshot.get("position")
        if isinstance(snapshot.get("position"), dict)
        else {}
    )

    equity = _f(wallet.get("equity")) or tracking.latest_equity(conn, mode)
    equity = float(equity) if equity and equity > 0 else None
    local_positions = tracking.load_open_positions(conn, mode)
    side = str(_row_value(row, "side", "")) if row is not None else ""
    same_side = [position for position in local_positions if not side or position.side == side]
    local_qty = sum(float(position.qty) for position in same_side)
    total_qty = _f(exchange_position.get("qty")) or local_qty
    total_entry = _f(exchange_position.get("entry_price"))
    if total_entry <= 0 and row is not None:
        total_entry = _f(_row_value(row, "entry_price"))
    leverage = _f(exchange_position.get("leverage"))
    if leverage <= 0 and row is not None:
        leverage = _f(_row_value(row, "leverage"), 1.0)
    total_notional = total_qty * total_entry
    position_margin = _f(exchange_position.get("position_im"))
    if position_margin <= 0 and leverage > 0:
        position_margin = total_notional / leverage

    return {
        "captured_at": snapshot.get("captured_at"),
        "margin_mode": _margin_mode_kr(account.get("margin_mode")),
        "position_mode": _position_mode_kr(exchange_position.get("position_idx", 0)),
        "equity": equity,
        "wallet_balance": _f(wallet.get("wallet_balance")),
        "available_balance": _f(wallet.get("available_balance")),
        "initial_margin": _f(wallet.get("initial_margin")),
        "maintenance_margin": _f(wallet.get("maintenance_margin")),
        "total_qty": total_qty,
        "total_entry": total_entry,
        "total_notional": total_notional,
        "position_margin": position_margin,
        "exchange_liq_price": _f(exchange_position.get("liq_price")),
        "mark_price": _f(exchange_position.get("mark_price")),
        "unrealised_pnl": _f(exchange_position.get("unrealised_pnl")),
    }


def _account_lines(context: dict[str, Any], *, exit_message: bool = False) -> list[str]:
    equity = context.get("equity")
    title = "청산 후 계좌 평가액" if exit_message else "계좌 평가액"
    lines = ["", "💰 계좌 스냅샷"]
    lines.append(
        f"• {title}: {equity:,.2f}달러"
        if equity is not None
        else f"• {title}: 확인 불가"
    )
    wallet = context.get("wallet_balance", 0.0)
    available = context.get("available_balance", 0.0)
    initial_margin = context.get("initial_margin", 0.0)
    maintenance_margin = context.get("maintenance_margin", 0.0)
    if wallet > 0:
        lines.append(f"• 지갑잔고: {wallet:,.2f}달러")
    if available > 0:
        lines.append(f"• 사용 가능액: {available:,.2f}달러")
    if initial_margin > 0:
        lines.append(
            f"• 전체 초기증거금: {initial_margin:,.2f}달러 "
            f"({_pct(initial_margin, equity)})"
        )
    if maintenance_margin > 0:
        lines.append(
            f"• 유지증거금: {maintenance_margin:,.2f}달러 "
            f"({_pct(maintenance_margin, equity)})"
        )
    unrealised = context.get("unrealised_pnl", 0.0)
    if not exit_message and (context.get("mark_price", 0.0) > 0 or unrealised != 0):
        lines.append(f"• 미실현손익: {unrealised:+,.2f}달러")
    return lines


def _build_entry_message(row, mode: str, context: dict[str, Any] | None = None) -> str:
    """진입/추가진입 알림 1건. tranche_index 로 신규 vs 비중 추가 구분."""
    context = context or {}
    tag = _mode_tag(mode)
    side = _side_kr(row["side"])
    entry = float(row["entry_price"])
    sl = float(row["sl_price"])
    lev = float(row["leverage"])
    tranche = int(row["tranche_index"])
    qty = float(row["qty"])
    equity = context.get("equity")
    total_qty = float(context.get("total_qty") or qty)
    total_entry = float(context.get("total_entry") or entry)
    notional = total_qty * total_entry
    margin = float(context.get("position_margin") or (notional / lev if lev > 0 else 0))
    risk_amount = float(_row_value(row, "initial_risk", 0.0))
    if risk_amount <= 0:
        risk_amount = abs(entry - sl) * qty
    sl_move = (sl - entry) / entry * 100 if entry > 0 else 0.0
    tranche_fraction = (0.40, 0.30, 0.30)[min(max(tranche, 0), 2)]

    if tranche <= 0:
        head = f"🟢 [{tag}] 새 진입 — {side}"
    else:
        head = f"🟢 [{tag}] 비중 추가 ({tranche + 1}/3) — {side}"

    lines = [
        head,
        f"BTCUSDT · {context.get('margin_mode', '마진 방식 확인 불가')} · "
        f"{context.get('position_mode', '포지션 모드 확인 불가')}",
        "",
        "📦 체결·포지션",
        f"• 진입가: {entry:,.2f}달러",
        f"• 이번 체결: {qty:.6f} BTC",
        f"• 현재 총수량: {total_qty:.6f} BTC",
        f"• 명목 포지션: {notional:,.2f}달러 ({_pct(notional, equity)})",
        f"• 포지션 증거금: {margin:,.2f}달러 ({_pct(margin, equity)}) · "
        f"레버리지 {lev:g}배",
        f"• 위험예산 단계: {tranche + 1}/3 · 전체 위험예산의 "
        f"{tranche_fraction * 100:.0f}%",
        "",
        "🛡️ 위험·청산 계획",
        f"• 손절: {sl:,.2f}달러 ({sl_move:+.2f}%)",
        f"• 손절 위험: {risk_amount:,.2f}달러 ({_pct(risk_amount, equity)}, 수수료 전)",
        f"• 목표가: 1차 {float(row['tp1_price']):,.2f}달러 · "
        f"2차 {float(row['tp2_price']):,.2f}달러 · "
        f"3차 {float(row['tp3_price']):,.2f}달러",
    ]
    exchange_liq = float(context.get("exchange_liq_price") or 0.0)
    if exchange_liq > 0:
        lines.append(f"• 거래소 청산가: {exchange_liq:,.2f}달러")
    else:
        lines.append(
            "• 거래소 청산가: 미제공 "
            "(교차마진 계좌 위험률·잔고에 따라 변동)"
        )
    strategy_liq = float(row["liq_price"])
    liq_move = (strategy_liq - entry) / entry * 100 if entry > 0 else 0.0
    lines.append(
        f"• 전략 추정 청산가: {strategy_liq:,.2f}달러 ({liq_move:+.2f}%)"
    )
    lines.extend(_account_lines(context))
    lines.extend(["", _disclaimer()])
    return "\n".join(lines)


def _build_exit_message(row, mode: str, context: dict[str, Any] | None = None) -> str:
    """청산 알림 1건. r_multiple 로 이익/손실, exit_reason 한글화."""
    context = context or {}
    tag = _mode_tag(mode)
    r = float(row["r_multiple"])
    reason = _reason_kr(row["exit_reason"])
    if r > 0:
        outcome = f"✅ 이익 {r:+.1f}배"
    else:
        outcome = f"❌ 손실 {r:+.1f}배"
    head = f"🔵 [{tag}] 포지션 정리 — {outcome} ({reason})"
    lines = [
        head,
        f"BTCUSDT · {context.get('margin_mode', '마진 방식 확인 불가')}",
        "",
        "📤 청산 정산",
        f"• 진입 {float(row['entry_price']):,.2f} → "
        f"청산 {float(row['exit_price']):,.2f}달러",
        f"• 정리 수량: {float(row['qty']):.6f} BTC · "
        f"레버리지 {float(row['leverage']):g}배",
        f"• 순손익: {float(_row_value(row, 'net_pnl', 0.0)):+,.2f}달러 · "
        f"결과 {r:+.2f}R",
        f"• 수수료: {float(row['fee_paid']):,.2f}달러 · "
        f"펀딩비: {float(row['funding_paid']):,.2f}달러",
    ]
    lines.extend(_account_lines(context, exit_message=True))
    lines.extend(["", _disclaimer()])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 전송 — telegram_reporter._send 재사용 (asyncio.run). 실패 흡수.
# ---------------------------------------------------------------------------

def _dispatch(messages: list[str], mode: str) -> None:
    """메시지들을 순서대로 전송. 토큰/채널 없으면 _send 가 stdout 폴백한다."""
    if not messages:
        return
    try:
        _load_env()
    except Exception:  # noqa: BLE001 — env 로드 실패해도 환경에 이미 있을 수 있음
        pass
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    channel = _resolve_channel(None, mode=mode)
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
                        msgs.append(
                            _build_entry_message(
                                row,
                                mode,
                                _account_context(conn, mode, row),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001 — 1건 실패가 전체를 못 막음
                        log.warning("진입 메시지 빌드 실패 (흡수): %s", exc)
                _dispatch(msgs, mode)
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
                        msgs.append(
                            _build_exit_message(
                                row,
                                mode,
                                _account_context(conn, mode),
                            )
                        )
                    except Exception as exc:  # noqa: BLE001
                        log.warning("청산 메시지 빌드 실패 (흡수): %s", exc)
                _dispatch(msgs, mode)
                tracking.set_meta(conn, _MARK_EXIT, int(rows[-1]["id"]), mode)
                result["exits"] = len(rows)
    except Exception as exc:  # noqa: BLE001 — 청산 알림 실패 절대 비전파
        log.warning("notify exits 실패 (흡수): %s", exc)

    return result
