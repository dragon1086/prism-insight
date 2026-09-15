"""Read-only notification snapshots. Renderers never contact the exchange.

USD account equity and USDT linear position values require a same-response
USDT/USD conversion; missing conversion is never replaced by an assumed peg.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timezone

from live.exchange_snapshot import read_complete


def mapping(value):
    return value if isinstance(value, dict) else {}


def persist_snapshot(conn, key, snapshot, mode):
    """Optional enrichment must not suppress a valid trade notification."""
    from live import tracking

    try:
        tracking.set_meta(conn, key, snapshot, mode)
    except Exception:  # noqa: BLE001 - optional metadata, no exception payload/credentials
        logging.getLogger(__name__).warning("optional notice snapshot persistence failed")


def number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def wallet_fields(row):
    result = {key: number(row.get(raw)) for key, raw in {
        "equity": "totalEquity", "wallet_balance": "totalWalletBalance",
        "available_balance": "totalAvailableBalance", "initial_margin": "totalInitialMargin",
        "maintenance_margin": "totalMaintenanceMargin", "margin_balance": "totalMarginBalance",
    }.items()}
    coins = row.get("coin")
    matches = [coin for coin in coins if isinstance(coin, dict) and coin.get("coin") == "USDT"] if isinstance(coins, list) else []
    if len(matches) == 1:
        equity, usd = number(matches[0].get("equity")), number(matches[0].get("usdValue"))
        if equity is not None and equity > 0 and usd is not None and usd > 0:
            result["usdt_usd_rate"] = usd / equity
    return result


def position_fields(row):
    result = {key: number(row.get(raw)) for key, raw in {
        "qty": "size", "entry_price": "avgPrice", "mark_price": "markPrice",
        "position_value": "positionValue", "leverage": "leverage",
        "position_im": "positionIM", "position_mm": "positionMM",
        "liq_price": "liqPrice", "unrealised_pnl": "unrealisedPnl",
        "stop_loss": "stopLoss", "position_idx": "positionIdx",
    }.items()}
    result.update(symbol=row.get("symbol"), side={"Buy": "long", "Sell": "short"}.get(row.get("side")))
    return result


def capture_swing_snapshot(backend, pos=None):
    """Bounded GET reads on the supplied swing session; failures are optional.

    Do not use backend._call: it mutates order rejection state and retries.
    The session already owns its bounded HTTP timeout/retry configuration.
    """
    snap = {"captured_at": datetime.now(timezone.utc).isoformat(),
            "account_scope": "swing:UNIFIED", "wallet_currency": "USD",
            "position_currency": "USDT", "account": {}, "wallet": {}, "position": {}}
    session = getattr(backend, "sess", None)
    if getattr(backend, "name", None) != "exchange" or session is None:
        return snap

    def response(method, **kwargs):
        try:
            reply = getattr(session, method)(**kwargs)
            if isinstance(reply, dict) and reply.get("retCode") == 0:
                return reply
        except Exception:  # noqa: BLE001 - optional GET, never log transport credentials
            return None
        return None

    def get(method, **kwargs):
        return mapping(mapping(response(method, **kwargs)).get("result"))

    account = get("get_account_info")
    snap["account"] = {"margin_mode": account.get("marginMode")}
    wallet = get("get_wallet_balance", accountType="UNIFIED")
    rows = wallet.get("list", [])
    if isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict) and rows[0].get("accountType") == "UNIFIED":
        snap["wallet"] = wallet_fields(rows[0])
    positions = mapping(mapping(read_complete(response, "get_positions", category="linear", symbol="BTCUSDT")).get("result"))
    rows = positions.get("list", [])
    if isinstance(rows, list) and not positions.get("nextPageCursor"):
        matches = [r for r in rows if isinstance(r, dict) and r.get("symbol") == "BTCUSDT"
                   and r.get("positionIdx") == 0
                   and (number(r.get("size")) is not None and number(r.get("size")) >= 0)
                   and (number(r.get("size")) == 0 or
                        r.get("side") == ("Buy" if getattr(pos, "side", None) == "long" else "Sell"))]
        if len(rows) == 1 and len(matches) == 1:
            candidate = position_fields(matches[0])
            if (candidate["qty"] == 0 or pos is None or
                    (candidate["entry_price"] is not None
                     and math.isclose(candidate["entry_price"], pos.entry_price, rel_tol=1e-9)
                     and math.isclose(candidate["qty"], pos.qty, abs_tol=1e-9))):
                snap["position"] = candidate
    return snap


def snapshot_lines(snapshot=None, pos=None, *, event_time=None, include_position=True,
                   operating_capital=None):
    """Shared account/position block, current capture distinct from fill time."""
    snap = mapping(snapshot)
    wallet = mapping(snap.get("wallet"))
    position = mapping(snap.get("notice_position", snap.get("position")))
    account = mapping(snap.get("account"))
    side = getattr(pos, "side", None)
    if (not include_position or position.get("symbol") != "BTCUSDT"
            or (side and position.get("side") not in (side, None))):
        position = {}
    margin_mode = str(account.get("margin_mode") or "")
    mode = {"REGULAR_MARGIN": "교차마진(Cross)", "ISOLATED_MARGIN": "격리마진(Isolated)",
            "PORTFOLIO_MARGIN": "포트폴리오마진(Portfolio)"}.get(margin_mode, "확인 불가")
    wc, pc = snap.get("wallet_currency", "USD"), snap.get("position_currency", "USDT")

    def amount(value, currency):
        n = number(value)
        return f"{n:,.2f} {currency}" if n is not None else "확인 불가"

    def ratio(value, currency):
        eq, val = number(wallet.get("equity")), number(value)
        if (snap.get("account_scope") and snap.get("captured_at")
                and eq is not None and eq > 0 and val is not None):
            if currency == wc:
                return f"계좌의 {val / eq * 100:.2f}%"
            rate = number(wallet.get("usdt_usd_rate"))
            if currency == "USDT" and wc == "USD" and rate is not None and rate > 0:
                return f"전체 계좌의 약 {val * rate / eq * 100:.2f}% (동일 조회 USDT→USD 환산)"
        return "계좌 비율 확인 불가(동일 계좌·통화·시점 분모 필요)"

    leverage = number(position.get("exchange_leverage", position.get("leverage")))
    leverage_text = f"{leverage:g}배" if leverage is not None and leverage > 0 else "확인 불가"
    idx = position.get("position_idx")
    im = position.get("position_im")
    if margin_mode == "PORTFOLIO_MARGIN":
        im = None
        leverage_text = "해당 없음/확인 불가(포트폴리오마진)"
    capital = number(operating_capital)
    rate = number(wallet.get("usdt_usd_rate"))
    margin = number(im)
    margin_usd = (margin if pc == "USD" else
                  margin * rate if pc == "USDT" and margin is not None
                  and rate is not None and rate > 0 else None)
    usage = (f"약 {margin_usd / capital * 100:.1f}%"
             if capital is not None and capital > 0 and margin_usd is not None
             and snap.get("captured_at") and snap.get("account_scope") else "확인 불가")
    lines = ["", "💼 운용자금 기준 증거금 사용 현황",
             f"• 운용 기준자금: {amount(capital if capital is not None and capital > 0 else None, 'USD')}",
             f"• 현재 포지션 증거금: {amount(im, pc)}",
             f"• 운용자금 대비 증거금 사용 비중: {usage}",
             "• 현재 증거금 ÷ 운용 기준자금 (USDT는 조회 환율로 USD 환산)",
             "• 증거금 사용 비중은 최대 손실 비중이 아닙니다.",
             "", "💰 참고: 현재 거래소 계좌·포지션 스냅샷 (진입 당시 값 아님)",
             f"• 계좌 범위: {snap.get('account_scope') or '확인 불가'}",
             f"• 조회 시각: {snap.get('captured_at') or '확인 불가'}",
             "• 계좌·포지션 API는 순차 조회하므로 비율은 조회 구간 기준입니다.",
             f"• 체결/이벤트 시각: {event_time or getattr(pos, 'entry_time', None) or '확인 불가'}",
             f"• BTCUSDT · 마진 방식: {mode} · " + ("단방향(One-way)" if idx == 0 else "포지션 모드 확인 불가"),
             f"• 거래소 레버리지: {leverage_text}"]
    if not include_position:
        lines.append("• 청산 포지션의 거래소 상세: 체결 시점에 연결된 스냅샷 없음. 현재 다른 포지션을 대입하지 않습니다.")
    for key, label in (("equity", "전체 거래계좌 평가액"), ("wallet_balance", "지갑잔고"),
                       ("available_balance", "사용 가능액"), ("initial_margin", "전체 초기증거금"),
                       ("maintenance_margin", "유지증거금")):
        value = wallet.get(key)
        lines.append(f"• {label}: {amount(value, wc)}" +
                     (f" ({ratio(value, wc)})" if "margin" in key else ""))
    qty = number(position.get("qty"))
    lines.extend([f"• 현재 총수량: {qty:.6f} BTC" if qty is not None else "• 현재 총수량: 확인 불가",
                  f"• 현재 명목 포지션: {amount(position.get('position_value'), pc)} ({ratio(position.get('position_value'), pc)})",
                  f"• 포지션 초기증거금(positionIM): {amount(im, pc)} ({ratio(im, pc)})",
                  "• 초기증거금은 평가가격 기준 요구액이며 고정 투입원금이 아닙니다.",
                  "• 고정 투입원금/계좌 비율: 확인 불가 " + {
                      "REGULAR_MARGIN": "(교차계좌는 포지션별 고정 원금 분리 불가)",
                      "ISOLATED_MARGIN": "(격리 담보 투입·추가·회수 내역 미확인)",
                      "PORTFOLIO_MARGIN": "(계좌 전체 위험 기반 증거금; 포지션 원금 아님)",
                  }.get(margin_mode, "(마진 방식 및 담보 내역 미확인)"),
                  f"• 평가가격: {amount(position.get('mark_price'), pc)} · 미실현손익: {amount(position.get('unrealised_pnl'), pc)}",
                  f"• 거래소 포지션 stopLoss: {amount(position.get('stop_loss') if number(position.get('stop_loss')) else None, pc)} (별도 조건부 보호주문 제외)",
                  f"• 거래소 청산가: {amount(position.get('liq_price') if number(position.get('liq_price')) else None, pc)}"])
    if pos is not None:
        risk = number(getattr(pos, "initial_risk", None))
        risk_label = "진입 기록 손절 위험(당시 수량, 수수료 전)"
        if risk is None:
            risk_label = "이벤트 기록 손절 위험(해당 수량, 수수료 전)"
            entry, stop, qty = (number(getattr(pos, name, None)) for name in ("entry_price", "sl_price", "qty"))
            if entry is not None and stop is not None and stop > 0 and qty is not None:
                risk = abs(entry - stop) * qty
        lines.extend([f"• 전략 손절: {amount(getattr(pos, 'sl_price', None), pc)}",
                      f"• {risk_label}: {amount(risk, pc)} (현재 계좌 대비 참고: {ratio(risk, pc)})"])
    return lines
