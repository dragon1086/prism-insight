# live/swing.py — 라운드6 Lane B 스윙 레인 집행기 (mode='swing' 자체 원장)
#
# 결정 로직은 core/swing.py 순수함수 (백테스트 패리티 고정). 이 모듈은 집행만.
#
# 집행 백엔드 2종 (v2):
#   - ExchangeBackend: 스윙 전용 Bybit 데모 키
#     (BYBIT_SWING_DEMO_API_KEY/SECRET)로
#     실주문. ★ 메인 레인 키와 반드시 다른 계정(별도 지갑)이어야 한다 —
#     같은 계좌를 쓰면 원웨이 넷팅으로 DemoAdapter 의 reconcile 3중 오염:
#     ① _sync_state 가 임의 포지션을 메인 것으로 채택 ② _record_closed_trades
#     가 모든 reduce-only 체결을 메인 트레이드로 기록 ③ 지갑 equity 공유.
#   - VirtualBackend: 키 미설정 시 폴백 — 가상 체결 (v1 과 동일 의미론).
#
# 집행 의미론 (analysis/round6_swing_lane.py 백테스트 미러):
#   - 신호: 4h 확정봉 (30m 틱마다 _get_tf_slice 로 감지 — 메인과 동일 케이던스)
#   - 진입: 시장가 (백테스트 taker 비용 모델과 일치), 진입 직후 네이티브
#     stop-market reduce-only SL 부착. 룰 청산: SL 취소 → 시장가 reduce.
#   - 하드스탑: 가상=30m 봉내 감시 / 실집행=거래소 네이티브 스탑이 진실,
#     틱마다 포지션 소멸 감지로 사후 기록.
#   - 포지션 최대 1개, 피라미딩/부분청산/펀딩 없음. 메인 방향충돌 시 진입금지.
#
# 실행 모드: runner 는 SWING_RUN_MODES(기본 demo 전용) 틱에서만 호출한다 —
# 서버는 shadow(:01)/demo(:02) 크론이 병행이라 양쪽에서 돌리면 단일 커서를
# 선착 틱이 소비해 알림/충돌검사가 어긋난다.
#
# 안전 원칙: process() 밖으로 예외 비전파는 호출측(runner)이 보장, 여기서도
# 알림/신호로그/거래소 호출 실패는 개별 흡수한다.
from __future__ import annotations

import logging
import os
import time
from typing import Optional

import pandas as pd

from backtest.engine import SLIPPAGE_SL, TAKER_FEE, _get_tf_slice
from core.leadership import leadership_multipliers
from core.swing import (
    compute_swing_sizing,
    conflicts_with_main,
    detect_cross,
    entry_side,
    rule_exit_due,
    stop_price,
)
from engine.config import SWING_ENABLED, SWING_INITIAL_EQUITY, SWING_MAX_LEVERAGE
from live import tracking
from live.demo import _f, _order_id, _pstr, _qstr, _result_list
from live.shadow import bar_index_for

log = logging.getLogger("live.swing")

MODE = "swing"  # tracking 원장 키 — 메인 mode(shadow/demo/live)와 완전 분리

_CATEGORY = "linear"
_SYMBOL = "BTCUSDT"
_POSITION_IDX = 0
_RETRY_SLEEP_SEC = 0.5
_MAX_IDLE_REPLAY_NS = 8 * 60 * 60 * 1_000_000_000


# ---------------------------------------------------------------------------
# 스윙 전용 세션 — 메인(BYBIT_DEMO_*)과 다른 키. 없으면 None (가상 폴백).
# ---------------------------------------------------------------------------

def _make_swing_session():
    key = os.environ.get("BYBIT_SWING_DEMO_API_KEY")
    secret = os.environ.get("BYBIT_SWING_DEMO_API_SECRET")
    if not key or not secret:
        try:
            from pathlib import Path

            from dotenv import load_dotenv
            load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
            key = os.environ.get("BYBIT_SWING_DEMO_API_KEY")
            secret = os.environ.get("BYBIT_SWING_DEMO_API_SECRET")
        except Exception:  # noqa: BLE001
            pass
    if not key or not secret:
        return None, "BYBIT_SWING_DEMO_API_KEY/SECRET 미설정"
    # 안전 가드: 메인 키와 동일하면 실집행 금지 (넷팅 오염 방지).
    if key == os.environ.get("BYBIT_DEMO_API_KEY"):
        return None, "SWING 키가 메인 DEMO 키와 동일 — 별도 계정 필요, 가상 폴백"
    try:
        from pybit.unified_trading import HTTP
        return HTTP(demo=True, api_key=key, api_secret=secret), None
    except Exception as exc:  # noqa: BLE001
        return None, f"pybit HTTP(demo) init 실패: {exc}"


# ---------------------------------------------------------------------------
# 집행 백엔드
# ---------------------------------------------------------------------------

class VirtualBackend:
    """가상 체결 (v1 의미론): 진입/룰청산 = 30m 종가, 스탑 = 봉내 스탑가."""

    name = "virtual"

    def __init__(self, conn):
        self.conn = conn
        self.last_open_snapshot: dict[str, float] = {}
        self.last_close_snapshot: dict[str, float] = {}
        self.last_wallet_snapshot: dict[str, float] = {}

    def equity(self, fallback: float) -> float:
        return fallback

    def open(self, side: str, qty: float, sl: float,
             hint_price: float) -> Optional[float]:
        return hint_price

    def check_stop(self, pos: tracking.PositionRow,
                   bar: pd.Series) -> Optional[float]:
        if pos.side == "long" and float(bar["low"]) <= pos.sl_price:
            return pos.sl_price
        if pos.side == "short" and float(bar["high"]) >= pos.sl_price:
            return pos.sl_price
        return None

    def close(self, pos: tracking.PositionRow, hint_price: float) -> float:
        return hint_price


class ExchangeBackend:
    """스윙 전용 데모 계정에 실주문. 거래소 지갑이 equity 의 진실."""

    name = "exchange"

    def __init__(self, conn, sess):
        self.conn = conn
        self.sess = sess
        self.last_open_snapshot: dict[str, float] = {}
        self.last_close_snapshot: dict[str, float] = {}
        self.last_wallet_snapshot: dict[str, float] = {}

    # --- 호출 헬퍼 (demo.DemoAdapter._call 미러 — 재시도 1회, 실패 흡수) ---
    def _call(self, fn_name: str, **kwargs) -> Optional[dict]:
        fn = getattr(self.sess, fn_name, None)
        if fn is None:
            return None
        last_exc = None
        for attempt in range(2):
            try:
                resp = fn(**kwargs)
                if isinstance(resp, dict):
                    ret_code = int(resp.get("retCode", -1))
                    if ret_code == 0:
                        return resp
                    # Bybit의 이미 적용/이미 소멸 응답은 원하는 최종 상태다.
                    # 재시도·오류 이벤트로 부풀리지 않고 멱등 성공으로 취급한다.
                    if (
                        (fn_name == "set_leverage" and ret_code == 110043)
                        or (fn_name == "cancel_order" and ret_code == 110001)
                    ):
                        return resp
                last_exc = (resp.get("retMsg") if isinstance(resp, dict) else resp)
            except Exception as exc:  # noqa: BLE001
                last_exc = str(exc)
            if attempt == 0:
                time.sleep(_RETRY_SLEEP_SEC)
        tracking.log_event(self.conn, "error", f"swing {fn_name} 실패: {last_exc}",
                           level="error", mode=MODE)
        return None

    def equity(self, fallback: float) -> float:
        wb = self._call("get_wallet_balance", accountType="UNIFIED")
        rows = _result_list(wb)
        if rows:
            wallet = rows[0]
            self.last_wallet_snapshot = {
                "equity": _f(wallet.get("totalEquity")),
                "wallet_balance": _f(wallet.get("totalWalletBalance")),
                "available_balance": _f(wallet.get("totalAvailableBalance")),
                "initial_margin": _f(wallet.get("totalInitialMargin")),
                "maintenance_margin": _f(wallet.get("totalMaintenanceMargin")),
            }
            eq = self.last_wallet_snapshot["equity"]
            if eq > 0:
                return eq
        return fallback

    def _position_size(self) -> Optional[float]:
        """현재 스윙 계정 BTCUSDT 포지션 크기. 조회 실패 시 None (판단 유보)."""
        pr = self._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
        if pr is None:
            return None
        for p in _result_list(pr):
            return _f(p.get("size"))
        return 0.0

    def open(self, side: str, qty: float, sl: float,
             hint_price: float) -> Optional[float]:
        """시장가 진입 → 체결가 확인 → 네이티브 SL 부착. 실패 시 None (무진입)."""
        self._call("set_leverage", category=_CATEGORY, symbol=_SYMBOL,
                   buyLeverage=str(SWING_MAX_LEVERAGE),
                   sellLeverage=str(SWING_MAX_LEVERAGE))
        resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side="Buy" if side == "long" else "Sell",
            orderType="Market", qty=_qstr(qty),
            timeInForce="IOC", positionIdx=_POSITION_IDX,
        )
        entry_oid = _order_id(resp)
        if entry_oid is None:
            return None
        # 체결가 확인 (최대 3회 폴링, 실패 시 힌트가로 기록).
        fill = hint_price
        for _ in range(3):
            pr = self._call("get_positions", category=_CATEGORY, symbol=_SYMBOL)
            for p in _result_list(pr or {}):
                if _f(p.get("size")) > 0:
                    fill = _f(p.get("avgPrice"), hint_price)
                    self.last_open_snapshot = {
                        "qty": _f(p.get("size")),
                        "entry_price": fill,
                        "leverage": _f(p.get("leverage")),
                        "position_value": _f(p.get("positionValue")),
                        "position_im": _f(p.get("positionIM")),
                        "liq_price": _f(p.get("liqPrice")),
                        "mark_price": _f(p.get("markPrice")),
                        "unrealised_pnl": _f(p.get("unrealisedPnl")),
                    }
                    break
            else:
                time.sleep(_RETRY_SLEEP_SEC)
                continue
            break
        entry_exec = self._execution_snapshot(entry_oid, closed_only=False)
        if entry_exec:
            fill = entry_exec.get("price") or fill
            self.last_open_snapshot.update({
                "qty": entry_exec.get("qty")
                or self.last_open_snapshot.get("qty") or qty,
                "entry_price": fill,
            })
            if "fee" in entry_exec:
                self.last_open_snapshot["entry_fee"] = entry_exec["fee"]
        # 네이티브 SL (stop-market reduce-only).
        close_side = "Sell" if side == "long" else "Buy"
        trigger_dir = 2 if side == "long" else 1
        protected_qty = self.last_open_snapshot.get("qty") or qty
        sl_resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(protected_qty),
            triggerPrice=_pstr(sl), triggerDirection=trigger_dir,
            triggerBy="LastPrice", reduceOnly=True,
            timeInForce="GTC", positionIdx=_POSITION_IDX,
        )
        sl_oid = _order_id(sl_resp)
        tracking.set_meta(self.conn, "swing_sl_order_id", sl_oid or "", MODE)
        if sl_oid is None:
            tracking.log_event(self.conn, "error",
                               "swing SL 주문 실패 — 소프트 감시로만 보호됨",
                               level="error", mode=MODE)
        tracking.log_event(self.conn, "order",
                           f"swing open {side} market qty={protected_qty:.4f} "
                           f"fill≈{fill:.1f} "
                           f"sl={sl:.1f} sl_oid={sl_oid}", mode=MODE)
        return fill

    def _execution_snapshot(
        self, order_id: str | None, *, closed_only: bool
    ) -> dict[str, float]:
        """한 주문의 분할 체결을 수량가중 평균으로 합친다."""
        kwargs = {"category": _CATEGORY, "symbol": _SYMBOL, "limit": 50}
        if order_id:
            kwargs["orderId"] = order_id
        ex = self._call("get_executions", category=_CATEGORY, symbol=_SYMBOL,
                        **{k: v for k, v in kwargs.items()
                           if k not in {"category", "symbol"}})
        rows = [
            row for row in _result_list(ex or {})
            if (not order_id or str(row.get("orderId") or "") == order_id)
            and (not closed_only or _f(row.get("closedSize")) > 0)
        ]
        weighted = 0.0
        qty = 0.0
        fee = 0.0
        fee_seen = False
        for row in rows:
            row_qty = _f(row.get("execQty")) or _f(row.get("closedSize"))
            row_price = _f(row.get("execPrice"))
            if row_qty <= 0 or row_price <= 0:
                continue
            qty += row_qty
            weighted += row_qty * row_price
            if row.get("execFee") not in (None, ""):
                fee += _f(row.get("execFee"))
                fee_seen = True
        if qty <= 0:
            return {}
        snapshot = {"qty": qty, "price": weighted / qty}
        if fee_seen:
            snapshot["fee"] = fee
        return snapshot

    def _capture_close_settlement(
        self, pos: tracking.PositionRow, order_id: str | None
    ) -> None:
        """Bybit의 체결·closedPnl을 묶어 청산 정산의 단일 진실로 보관한다."""
        execution = self._execution_snapshot(order_id, closed_only=True)
        snapshot: dict[str, float] = {}
        if execution:
            snapshot = {
                "qty": execution["qty"],
                "exit_price": execution["price"],
            }
            if "fee" in execution:
                snapshot["close_fee"] = execution["fee"]

        closed_row = None
        if order_id:
            for attempt in range(3):
                pnl_resp = self._call(
                    "get_closed_pnl", category=_CATEGORY, symbol=_SYMBOL, limit=20
                )
                for row in _result_list(pnl_resp or {}):
                    if str(row.get("orderId") or "") == order_id:
                        closed_row = row
                        break
                if closed_row is not None or pnl_resp is None:
                    break
                if attempt < 2:
                    time.sleep(_RETRY_SLEEP_SEC)

        if closed_row is not None and closed_row.get("closedPnl") not in (None, ""):
            qty = _f(closed_row.get("qty")) or snapshot.get("qty") or pos.qty
            entry_price = (
                _f(closed_row.get("avgEntryPrice")) or pos.entry_price
            )
            exit_price = (
                _f(closed_row.get("avgExitPrice"))
                or snapshot.get("exit_price") or pos.sl_price
            )
            open_fee = _f(closed_row.get("openFee"))
            close_fee = (
                _f(closed_row.get("closeFee"))
                or snapshot.get("close_fee") or 0.0
            )
            closed_pnl = _f(closed_row.get("closedPnl"))
            sign = 1.0 if pos.side == "long" else -1.0
            gross = sign * qty * (exit_price - entry_price)
            snapshot.update({
                "qty": qty,
                "entry_price": entry_price,
                "exit_price": exit_price,
                "closed_pnl": closed_pnl,
                "open_fee": open_fee,
                "close_fee": close_fee,
                # Bybit closedPnl과 가격손익·매매수수료의 차이를 펀딩
                # 정산으로 복원한다. 양수는 비용, 음수는 수취다.
                "funding_paid": gross - open_fee - close_fee - closed_pnl,
            })
        exchange_leverage = tracking.get_meta(
            self.conn, "swing_exchange_leverage", MODE
        )
        if exchange_leverage is not None:
            snapshot["exchange_leverage"] = float(exchange_leverage)
        self.last_close_snapshot = snapshot

    def _last_close_exec_price(self, order_id: str | None = None) -> Optional[float]:
        snapshot = self._execution_snapshot(order_id, closed_only=True)
        return snapshot.get("price") or None

    def check_stop(self, pos: tracking.PositionRow,
                   bar: pd.Series) -> Optional[float]:
        """거래소 포지션 소멸 = 스탑(또는 외부 청산) 체결. 체결가를 반환."""
        size = self._position_size()
        if size is None or size > 0:
            return None
        sl_oid = tracking.get_meta(self.conn, "swing_sl_order_id", MODE)
        self._capture_close_settlement(pos, sl_oid)
        price = self.last_close_snapshot.get("exit_price") or pos.sl_price
        if sl_oid:
            self._call("cancel_order", category=_CATEGORY, symbol=_SYMBOL,
                       orderId=sl_oid)
        tracking.set_meta(self.conn, "swing_sl_order_id", "", MODE)
        return price

    def close(self, pos: tracking.PositionRow, hint_price: float) -> float:
        sl_oid = tracking.get_meta(self.conn, "swing_sl_order_id", MODE)
        if sl_oid:
            self._call("cancel_order", category=_CATEGORY, symbol=_SYMBOL,
                       orderId=sl_oid)
        tracking.set_meta(self.conn, "swing_sl_order_id", "", MODE)
        close_side = "Sell" if pos.side == "long" else "Buy"
        close_resp = self._call(
            "place_order", category=_CATEGORY, symbol=_SYMBOL,
            side=close_side, orderType="Market", qty=_qstr(pos.qty),
            reduceOnly=True, timeInForce="IOC", positionIdx=_POSITION_IDX,
        )
        close_oid = _order_id(close_resp)
        self._capture_close_settlement(pos, close_oid)
        return self.last_close_snapshot.get("exit_price") or hint_price


def _make_backend(conn, main_mode: str):
    """demo/live 틱에서 스윙 키가 있으면 실집행, 아니면 가상 폴백."""
    if main_mode in ("demo", "live"):
        sess, err = _make_swing_session()
        if sess is not None:
            # 연결이 회복되면 다음 장애 때 폴백 경고가 다시 발송되도록 재무장.
            if tracking.get_meta(conn, "swing_exec_fallback_notified", MODE):
                tracking.set_meta(conn, "swing_exec_fallback_notified", 0, MODE)
            return ExchangeBackend(conn, sess)
        if not tracking.get_meta(conn, "swing_exec_fallback_notified", MODE):
            tracking.log_event(conn, "info",
                               f"swing 실집행 불가({err}) — 가상 체결 폴백",
                               mode=MODE)
            tracking.set_meta(conn, "swing_exec_fallback_notified", 1, MODE)
    return VirtualBackend(conn)


# ---------------------------------------------------------------------------
# 텔레그램 알림 — notifier 와 동일한 재사용 패턴, [스윙레인] 태그로 구분.
# ---------------------------------------------------------------------------

def _notify(main_mode: str, msg: str) -> bool | None:
    """메인이 demo/live 로 돌 때만 발송 (shadow 로컬 테스트 스팸 방지). 실패 흡수."""
    if main_mode not in ("demo", "live"):
        return None
    try:
        import asyncio

        from live.telegram_reporter import _load_env, _resolve_channel, _send
        _load_env()
        token = os.environ.get("TELEGRAM_BOT_TOKEN")
        channel = _resolve_channel(None, mode=main_mode)
        if not channel:
            log.error("swing public channel missing; private fallback forbidden")
            return False
        return bool(asyncio.run(_send(token, channel, msg)))
    except Exception as exc:  # noqa: BLE001 — 알림 실패는 매매와 무관
        log.warning("swing notify 실패 (흡수): %s", exc)
        return False


def _side_kr(side: str) -> str:
    return "롱 (상승 베팅)" if side == "long" else "숏 (하락 베팅)"


def _log_signal_safe(conn, ts: str, side: str, reason: str, n_open: int) -> None:
    try:
        tracking.log_signal(conn, ts, score=None, ts_4h=None, ts_1d=None,
                            side=side, reason=reason, n_open=n_open, mode=MODE)
    except Exception as exc:  # noqa: BLE001 — 관측 실패는 매매와 무관
        log.warning("swing log_signal 실패 (흡수): %s", exc)


def _hold_duration(entry_time: str, exit_time: str) -> str | None:
    """진입부터 청산까지의 경과시간을 사람이 읽는 형식으로 반환한다."""
    try:
        elapsed_seconds = max(
            0.0,
            (pd.Timestamp(exit_time) - pd.Timestamp(entry_time)).total_seconds(),
        )
    except Exception:  # noqa: BLE001 — 알림용 보조 수치는 실패해도 매매를 막지 않음
        return None

    minutes = int(elapsed_seconds // 60)
    days, minutes = divmod(minutes, 24 * 60)
    hours, minutes = divmod(minutes, 60)
    if days:
        return f"{days}일 {hours}시간"
    if hours:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    return f"{minutes}분"


def _entry_time_kst(entry_time: str) -> str:
    try:
        ts = pd.Timestamp(entry_time)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.tz_convert("Asia/Seoul").strftime("%Y-%m-%d %H:%M KST")
    except Exception:  # noqa: BLE001 — 알림 표시용 값
        return str(entry_time)


def _pct_text(amount: float, equity: float) -> str:
    if equity <= 0:
        return "계좌 대비 계산 불가"
    return f"계좌 평가액의 {amount / equity * 100:.2f}%"


def _build_entry_message(
    pos: tracking.PositionRow,
    backend_name: str,
    equity: float,
    signal_context: dict[str, float],
    execution_context: dict[str, float] | None = None,
    wallet_context: dict[str, float] | None = None,
    native_sl_attached: bool = False,
) -> str:
    """Build a complete, auditable swing-entry notification.

    ``pos.leverage`` is the strategy's notional exposure divided by equity,
    not the leverage configured at Bybit.  Keep those concepts separate so a
    low-risk position is not reported as if the exchange leverage were lower.
    """
    execution_context = execution_context or {}
    wallet_context = wallet_context or {}
    qty = execution_context.get("qty") or pos.qty
    entry = execution_context.get("entry_price") or pos.entry_price
    notional = execution_context.get("position_value") or qty * entry
    exchange_leverage = execution_context.get("leverage") or 0.0
    position_margin = execution_context.get("position_im") or (
        notional / exchange_leverage if exchange_leverage > 0 else 0.0
    )
    exposure = notional / equity if equity > 0 else pos.leverage
    sl_move = (pos.sl_price - entry) / entry * 100.0 if entry > 0 else 0.0
    risk_amount = pos.initial_risk or abs(entry - pos.sl_price) * qty
    liq_price = execution_context.get("liq_price") or 0.0
    exec_tag = "실주문" if backend_name == "exchange" else "가상체결"
    order_type = "시장가(Taker)" if backend_name == "exchange" else "가상 시장가"
    if backend_name == "exchange":
        leverage_text = (
            f"{exchange_leverage:g}배" if exchange_leverage > 0 else "확인 불가"
        )
        stop_text = (
            "거래소 조건부 주문 부착 완료"
            if native_sl_attached else "거래소 조건부 주문 확인 필요"
        )
    else:
        leverage_text = "가상체결(거래소 설정 없음)"
        stop_text = "가상 봉내 감시"

    if pos.side == "long":
        exit_rule = "4시간봉 종가가 MA35 아래로 이탈하면 추세청산"
        cross_rule = (
            "• 4시간 MA10이 MA35를 상향 돌파 "
            f"({signal_context['prev_ma10_4h']:,.2f}≤"
            f"{signal_context['prev_ma35_4h']:,.2f} → "
            f"{signal_context['ma10_4h']:,.2f}>"
            f"{signal_context['ma35_4h']:,.2f})"
        )
        daily_rule = (
            "• 완결 일봉 MA10 > MA35 "
            f"({signal_context['ma10_1d']:,.2f}>"
            f"{signal_context['ma35_1d']:,.2f})"
        )
        price_rule = (
            "• 4시간 종가 > MA35 "
            f"({signal_context['close_4h']:,.2f}>"
            f"{signal_context['ma35_4h']:,.2f})"
        )
    else:
        exit_rule = "4시간봉 종가가 MA35 위로 이탈하면 추세청산"
        cross_rule = (
            "• 4시간 MA10이 MA35를 하향 돌파 "
            f"({signal_context['prev_ma10_4h']:,.2f}≥"
            f"{signal_context['prev_ma35_4h']:,.2f} → "
            f"{signal_context['ma10_4h']:,.2f}<"
            f"{signal_context['ma35_4h']:,.2f})"
        )
        daily_rule = (
            "• 완결 일봉 MA10 ≤ MA35 "
            f"({signal_context['ma10_1d']:,.2f}≤"
            f"{signal_context['ma35_1d']:,.2f})"
        )
        price_rule = (
            "• 4시간 종가 < MA35 "
            f"({signal_context['close_4h']:,.2f}<"
            f"{signal_context['ma35_4h']:,.2f})"
        )

    lines = [
        f"🌀 [BTC 스윙레인·{exec_tag}] 새 진입 — {_side_kr(pos.side)}",
        f"_{_entry_time_kst(pos.entry_time)} 신호 기준 · 가상자금 모의투자_",
        "",
        "📦 체결·포지션",
        f"• 주문 방식: {order_type}",
        f"• 평균 체결가: {entry:,.2f}달러",
        f"• 체결수량: {qty:.6f} BTC",
        f"• 명목 포지션: {notional:,.2f}달러 ({_pct_text(notional, equity)})",
        f"• 전략 노출배수: 계좌 평가액 대비 {exposure:.2f}배",
        f"• 거래소 레버리지: {leverage_text}",
    ]
    if position_margin > 0:
        lines.append(
            f"• 포지션 초기증거금: {position_margin:,.2f}달러 "
            f"({_pct_text(position_margin, equity)})"
        )
    lines.extend([
        "",
        "🛡️ 위험·청산 계획",
        f"• 보호 손절: {pos.sl_price:,.2f}달러 ({sl_move:+.2f}%) · {stop_text}",
        (
            f"• 손절 위험: {risk_amount:,.2f}달러 "
            f"({_pct_text(risk_amount, equity)}, 수수료 전)"
        ),
    ])
    if liq_price > 0:
        liq_move = (liq_price - entry) / entry * 100.0
        lines.append(f"• 거래소 청산가: {liq_price:,.2f}달러 ({liq_move:+.2f}%)")
    else:
        lines.append("• 거래소 청산가: 미제공 또는 가상체결")
    lines.extend([
        f"• 고정 익절가는 없음 · {exit_rule}",
        "",
        "🔎 진입 근거",
        cross_rule,
        daily_rule,
        price_rule,
        "",
        "💰 계좌 스냅샷",
        f"• 계좌 평가액: {equity:,.2f}달러",
    ])
    available = wallet_context.get("available_balance") or 0.0
    if available > 0:
        lines.append(f"• 사용 가능액: {available:,.2f}달러")
    lines.extend(["", "_가상자금 모의투자입니다_"])
    return "\n".join(lines)


def _record_notification_delivery(conn, event: str, delivered: bool | None) -> None:
    if delivered is None:
        return
    ok = delivered is True
    tracking.log_event(
        conn,
        "notification",
        f"{event} channel delivery {'ok' if ok else 'failed'}",
        level="info" if ok else "error",
        mode=MODE,
    )


def _build_exit_message(
    pos: tracking.PositionRow,
    exit_price: float,
    reason: str,
    backend_name: str,
    net: float,
    fee_paid: float,
    r_multiple: float,
    equity_before: float,
    equity_after: float,
    exit_time: str,
    gross_pnl: float | None = None,
    funding_paid: float = 0.0,
    entry_account_equity: float | None = None,
    exchange_leverage: float | None = None,
    settlement_confirmed: bool = False,
) -> str:
    """스윙 청산 알림을 정량 지표와 함께 만든다.

    ``price_return_pct`` 는 롱/숏 방향을 반영한 현물 가격 기준 수익률이고,
    실주문에서는 Bybit ``closedPnl``만 확정손익으로 표시한다. 교차계좌의
    현재 평가액은 다른 자산 변동이 섞인 스냅샷이므로 거래 손익과 빼지 않는다.
    """
    sign = 1.0 if pos.side == "long" else -1.0
    price_return_pct = (
        sign * (exit_price - pos.entry_price) / pos.entry_price * 100.0
        if pos.entry_price
        else None
    )
    duration = _hold_duration(pos.entry_time, exit_time)

    outcome = f"✅ 이익 {r_multiple:+.1f}배" if r_multiple > 0 else f"❌ 손실 {r_multiple:+.1f}배"
    why = "손절" if reason == "swing_sl" else "추세이탈 청산"
    exec_tag = "실주문" if backend_name == "exchange" else "가상체결"
    lines = [
        f"🌀 [스윙레인·{exec_tag}] 포지션 정리 — {outcome} ({why})",
        (
            f"• 진입→청산: {pos.entry_price:,.0f} → {exit_price:,.0f}달러 · "
            f"{_side_kr(pos.side)} · 가격 기준 {price_return_pct:+.2f}%"
            if price_return_pct is not None
            else f"• 진입→청산: {pos.entry_price:,.0f} → {exit_price:,.0f}달러 · {_side_kr(pos.side)}"
        ),
    ]
    if backend_name == "exchange":
        pnl_label = "Bybit 확정 실현손익" if settlement_confirmed else "추정 실현손익"
        pnl_parts = [f"• {pnl_label}: {net:+,.2f}달러"]
        if gross_pnl is not None:
            pnl_parts.append(f"가격손익 {gross_pnl:+,.2f}달러")
        pnl_parts.append(f"매매수수료 {fee_paid:,.2f}달러")
        if settlement_confirmed:
            pnl_parts.append(f"펀딩비 {funding_paid:,.2f}달러")
        lines.append(" · ".join(pnl_parts))
        if not settlement_confirmed:
            lines.append("• ⚠️ Bybit 확정 정산 조회 실패 · 원장 수치는 잠정값")
        if entry_account_equity and entry_account_equity > 0:
            contribution = net / entry_account_equity * 100.0
            lines.append(
                f"• 이번 거래 계좌 기여: {net:+,.2f}달러 "
                f"(진입 당시 평가액 대비 {contribution:+.2f}%)"
            )
        lines.append(
            f"• 현재 교차계좌 평가액: {equity_after:,.2f}달러 "
            "(현재 스냅샷이며 이번 거래 수익 아님)"
        )
        leverage_text = (
            f"{exchange_leverage:g}배" if exchange_leverage else "확인 불가"
        )
        lines.append(
            f"• 포지션: {pos.qty:.4f} BTC · 거래소 레버리지 {leverage_text} · "
            f"전략 노출 {pos.leverage:.1f}배"
            + (f" · 보유 {duration}" if duration else "")
        )
    else:
        account_delta = equity_after - equity_before
        account_return_pct = (
            account_delta / equity_before * 100.0 if equity_before else None
        )
        lines.append(
            f"• 실현손익: {net:+,.2f}달러 · 수수료 {fee_paid:,.2f}달러"
        )
        lines.append(
            f"• 가상 원장: {equity_before:,.2f} → {equity_after:,.2f}달러 · "
            f"{account_delta:+,.2f}달러"
            + (
                f" ({account_return_pct:+.2f}%)"
                if account_return_pct is not None else ""
            )
        )
        lines.append(
            f"• 포지션: {pos.qty:.4f} BTC · 전략 노출 {pos.leverage:.1f}배"
            + (f" · 보유 {duration}" if duration else "")
        )
    lines.append("_데모 계정 모의투자입니다_")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 원장 정산 — 백엔드 공통 (체결가만 백엔드가 결정).
# ---------------------------------------------------------------------------

def _close_position(conn, backend, pos: tracking.PositionRow, exit_price: float,
                    fee_rate: float, reason: str, bar_time_str: str,
                    equity: float, trade_counter: int,
                    main_mode: str) -> tuple[float, int]:
    """청산 정산: 트레이드 기록 → 포지션 제거 → equity 갱신 → 알림.

    실집행 백엔드는 지갑 equity 가 진실 — 추정 net 으로 갱신 후 지갑값으로 덮는다.
    """
    sign = 1.0 if pos.side == "long" else -1.0
    settlement = getattr(backend, "last_close_snapshot", {}) or {}
    settlement_confirmed = (
        backend.name == "exchange" and "closed_pnl" in settlement
    )
    qty = settlement.get("qty") or pos.qty
    entry_price = settlement.get("entry_price") or pos.entry_price
    exit_price = settlement.get("exit_price") or exit_price
    gross = sign * qty * (exit_price - entry_price)
    if backend.name == "exchange":
        # 실제 체결가에는 슬리피지가 이미 반영돼 있다. 백테스트용 SLIPPAGE_SL을
        # 다시 비용으로 더하지 않는다.
        open_fee = settlement.get("open_fee", pos.entry_fee)
        exit_fee = settlement.get("close_fee", qty * exit_price * TAKER_FEE)
    else:
        open_fee = pos.entry_fee
        exit_fee = qty * exit_price * fee_rate
    fee_paid = open_fee + exit_fee
    funding_paid = settlement.get("funding_paid", 0.0)
    net = (
        settlement["closed_pnl"]
        if settlement_confirmed
        else gross - fee_paid - funding_paid
    )
    risk = pos.initial_risk if pos.initial_risk > 0 else 1.0
    trade_counter += 1
    tracking.record_trade(conn, tracking.TradeRow(
        trade_id=trade_counter,
        side=pos.side,
        entry_time=pos.entry_time,
        entry_price=entry_price,
        exit_time=bar_time_str,
        exit_price=exit_price,
        qty=qty,
        leverage=pos.leverage,
        sl_price=pos.sl_price,
        exit_reason=reason,
        r_multiple=net / risk,
        fee_paid=fee_paid,
        funding_paid=funding_paid,
        tranche_index=0,
        liq_price=0.0,
        net_pnl=net,
        gross_pnl=gross,
        gross_r_multiple=gross / risk,
        num_legs=1,
        mode=MODE,
    ))
    if pos.id is not None:
        tracking.remove_position(conn, pos.id)
    equity_before = equity
    equity_after = backend.equity(fallback=equity_before + net)
    tracking.record_equity(conn, equity_after, MODE, bar_time_str)
    tracking.log_event(conn, "trade",
                       f"swing close[{backend.name}] {pos.side} @ {exit_price:.1f} "
                       f"({reason}) net={net:+.2f} "
                       f"settlement={'confirmed' if settlement_confirmed else 'estimated'} "
                       f"eq={equity_after:.2f}", mode=MODE)
    r = net / risk
    entry_account_equity = tracking.get_meta(
        conn, "swing_entry_account_equity", MODE
    )
    exchange_leverage = settlement.get("exchange_leverage")
    if exchange_leverage is None:
        exchange_leverage = tracking.get_meta(
            conn, "swing_exchange_leverage", MODE
        )
    display_pos = tracking.PositionRow(
        **{
            **pos.__dict__,
            "entry_price": entry_price,
            "qty": qty,
        }
    )
    delivered = _notify(main_mode, _build_exit_message(
        pos=display_pos,
        exit_price=exit_price,
        reason=reason,
        backend_name=backend.name,
        net=net,
        fee_paid=fee_paid,
        r_multiple=r,
        equity_before=equity_before,
        equity_after=equity_after,
        exit_time=bar_time_str,
        gross_pnl=gross,
        funding_paid=funding_paid,
        entry_account_equity=entry_account_equity,
        exchange_leverage=exchange_leverage,
        settlement_confirmed=settlement_confirmed,
    ))
    _record_notification_delivery(conn, "swing_exit", delivered)
    tracking.set_meta(conn, "swing_entry_account_equity", None, MODE)
    tracking.set_meta(conn, "swing_exchange_leverage", None, MODE)
    return equity_after, trade_counter


def _try_entry(conn, backend, bar, bar_time_str: str, s4: pd.DataFrame,
               s1: pd.DataFrame, equity: float,
               main_mode: str) -> Optional[tracking.PositionRow]:
    """4h 확정봉에서 진입 평가. 성공 시 저장된 PositionRow, 아니면 None."""
    if len(s4) < 36 or s1.empty:
        return None
    cur = s4.iloc[-1]
    prev = s4.iloc[-2]
    d1 = s1.iloc[-1]
    needed = [cur.get("ma10"), cur.get("ma35"), cur.get("atr14"),
              prev.get("ma10"), prev.get("ma35"), d1.get("ma10"), d1.get("ma35")]
    if any(v is None or pd.isna(v) for v in needed):
        return None

    cross = detect_cross(float(prev["ma10"]), float(prev["ma35"]),
                         float(cur["ma10"]), float(cur["ma35"]))
    if cross == 0:
        return None

    side = entry_side(cross, float(d1["ma10"]), float(d1["ma35"]),
                      float(cur["close"]), float(cur["ma35"]))
    if side is None:
        _log_signal_safe(conn, bar_time_str, "none",
                         f"swing cross={cross:+d} 기각: 1d 불일치 또는 MA35 역방향", 0)
        return None

    # 메인 레인과 방향 충돌 금지 (같은 runner 프로세스의 메인 mode 포지션 조회).
    try:
        main_sides = [p.side for p in tracking.load_open_positions(conn, main_mode)]
    except Exception:  # noqa: BLE001 — 조회 실패 시 보수적으로 진입 보류
        main_sides = ["__unknown__"]
    if conflicts_with_main(side, main_sides):
        _log_signal_safe(conn, bar_time_str, side,
                         f"swing 기각: 메인 레인 방향 충돌 (main={main_sides})", 1)
        return None

    hint_price = float(bar["close"])
    sizing_equity = backend.equity(fallback=equity)
    sl = stop_price(side, hint_price, float(cur["atr14"]))
    # 라운드8 L 계층: BTC/ETH 상대강도 노출 배수 (fail-open=1.0, 신규 진입만)
    l_long, l_short, _l_reason = leadership_multipliers()
    sz = compute_swing_sizing(sizing_equity, hint_price, sl,
                              risk_mult=(l_long if side == "long" else l_short))
    if sz.rejected:
        _log_signal_safe(conn, bar_time_str, side,
                         f"swing 기각: sizing ({sz.reject_reason})", 0)
        return None

    fill = backend.open(side, sz.qty, sl, hint_price)
    if fill is None:
        _log_signal_safe(conn, bar_time_str, side, "swing 기각: 주문 실패", 0)
        return None

    execution = getattr(backend, "last_open_snapshot", {}) or {}
    actual_qty = execution.get("qty") or sz.qty
    fill = execution.get("entry_price") or fill
    entry_fee = execution.get("entry_fee")
    if entry_fee is None:
        entry_fee = actual_qty * fill * TAKER_FEE
    actual_risk = abs(fill - sl) * actual_qty
    pos = tracking.PositionRow(
        side=side,
        entry_price=fill,
        qty=actual_qty,
        leverage=sz.leverage,
        sl_price=sl,
        tp1_price=0.0, tp2_price=0.0, tp3_price=0.0,
        liq_price=0.0,
        entry_time=bar_time_str,
        tranche_index=0,
        entry_bar_idx=bar_index_for(int(pd.Timestamp(bar_time_str).value) // 1_000_000),
        initial_risk=actual_risk,
        entry_fee=entry_fee,
        initial_qty=actual_qty,
        mode=MODE,
    )
    tracking.save_position(conn, pos)
    tracking.log_event(conn, "trade",
                       f"swing open[{backend.name}] {side} @ {fill:.1f} "
                       f"qty={actual_qty:.4f} sl={sl:.1f} "
                       f"exposure={sz.leverage:.2f}",
                       mode=MODE)
    if backend.name == "exchange":
        # 청산 시 현재 교차계좌 값과 오래된 원장값을 빼지 않도록 진입 시점의
        # 분모만 별도 저장한다. 거래 손익 자체는 closedPnl을 사용한다.
        tracking.record_equity(conn, sizing_equity, MODE, bar_time_str)
        tracking.set_meta(
            conn, "swing_entry_account_equity", sizing_equity, MODE
        )
        exchange_leverage = execution.get("leverage")
        tracking.set_meta(
            conn, "swing_exchange_leverage", exchange_leverage, MODE
        )
    _log_signal_safe(conn, bar_time_str, side, "swing_entry", 1)
    signal_context = {
        "prev_ma10_4h": float(prev["ma10"]),
        "prev_ma35_4h": float(prev["ma35"]),
        "ma10_4h": float(cur["ma10"]),
        "ma35_4h": float(cur["ma35"]),
        "close_4h": float(cur["close"]),
        "ma10_1d": float(d1["ma10"]),
        "ma35_1d": float(d1["ma35"]),
    }
    delivered = _notify(main_mode, _build_entry_message(
        pos=pos,
        backend_name=backend.name,
        equity=sizing_equity,
        signal_context=signal_context,
        execution_context=getattr(backend, "last_open_snapshot", None),
        wallet_context=getattr(backend, "last_wallet_snapshot", None),
        native_sl_attached=bool(
            tracking.get_meta(conn, "swing_sl_order_id", MODE)
        ),
    ))
    _record_notification_delivery(conn, "swing_entry", delivered)
    return pos


# ---------------------------------------------------------------------------
# 핵심 진입점 — runner.tick 이 SWING_RUN_MODES 틱마다 호출.
# ---------------------------------------------------------------------------

def process(root_conn, tf_data: dict, main_mode: str = "shadow",
            backend=None) -> dict:
    """새 확정 30m 봉들을 스윙 레인 관점에서 처리. {"events": n} 반환.

    자체 메타 커서(mode='swing' 의 last_processed_30m_ns / last_confirmed_4h_ns)
    를 쓰므로 메인 레인 상태와 완전 독립이며 재실행에 멱등이다.
    콜드 스타트(커서 없음)는 마지막 확정봉 1개만 처리한다 (과거 재시뮬 방지).
    backend 미지정 시 자동 선택 (스윙 키 존재+demo/live → 실집행, 아니면 가상).
    """
    result = {"events": 0}
    if not SWING_ENABLED:
        return result
    bars_30m = tf_data.get("30m")
    if bars_30m is None or bars_30m.empty:
        return result
    conn = root_conn
    if backend is None:
        backend = _make_backend(conn, main_mode)

    equity = tracking.latest_equity(conn, MODE)
    if equity is None:
        equity = backend.equity(fallback=SWING_INITIAL_EQUITY)
        tracking.record_equity(conn, equity, MODE)
        tracking.log_event(conn, "info",
                           f"swing lane ledger seeded[{backend.name}]: {equity:.0f}",
                           mode=MODE)

    positions = tracking.load_open_positions(conn, MODE)
    pos = positions[0] if positions else None
    last_ns = tracking.get_meta(conn, "last_processed_30m_ns", MODE)
    latest_ns = int(bars_30m.index[-1].value)
    if (
        last_ns is not None
        and pos is None
        and latest_ns - int(last_ns) > _MAX_IDLE_REPLAY_NS
    ):
        # A flat lane has no risk state to reconstruct. Replaying weeks of old
        # bars would manufacture historical open/close notifications (and, with
        # exchange keys, could place stale orders). Fast-forward safely.
        tracking.set_meta(conn, "last_processed_30m_ns", latest_ns, MODE)
        latest_4h = _get_tf_slice(tf_data, bars_30m.index[-1], "4h")
        if not latest_4h.empty:
            tracking.set_meta(
                conn, "last_confirmed_4h_ns", int(latest_4h.index[-1].value), MODE
            )
        tracking.log_event(
            conn,
            "cursor_resync",
            "flat swing cursor fast-forwarded; historical replay skipped",
            mode=MODE,
        )
        return result
    if last_ns is None:
        new_bars = bars_30m.iloc[[-1]]
    else:
        new_bars = bars_30m[bars_30m.index.map(
            lambda t: int(t.value) > int(last_ns))]
    if new_bars.empty:
        return result

    last_4h_ns = tracking.get_meta(conn, "last_confirmed_4h_ns", MODE)
    trade_counter = int(tracking.get_meta(conn, "trade_id_counter", MODE) or 0)

    for bar_time, bar in new_bars.iterrows():
        bar_time_str = str(bar_time)

        # --- 1. 하드스탑 감시 (가상=봉내 / 실집행=거래소 포지션 소멸 감지) ---
        if pos is not None:
            stop_fill = backend.check_stop(pos, bar)
            if stop_fill is not None:
                equity, trade_counter = _close_position(
                    conn, backend, pos, stop_fill, TAKER_FEE + SLIPPAGE_SL,
                    "swing_sl", bar_time_str, equity, trade_counter, main_mode)
                pos = None
                result["events"] += 1

        # --- 2. 새 4h 확정 감지 → 룰 청산(보유 시) / 진입 평가(무포지션 시) ---
        # 백테스트 run_lane 미러: 같은 4h 에서 청산과 진입을 겸하지 않는다.
        s4 = _get_tf_slice(tf_data, bar_time, "4h")
        if s4.empty:
            continue
        cur_4h_ns = int(s4.index[-1].value)
        if last_4h_ns is not None and cur_4h_ns == int(last_4h_ns):
            continue
        last_4h_ns = cur_4h_ns

        if pos is not None:
            row = s4.iloc[-1]
            if not pd.isna(row.get("ma35")) and rule_exit_due(
                    pos.side, float(row["close"]), float(row["ma35"])):
                # 실주문은 청산 직전 지갑 equity를 기준으로 계좌 변화율을 계산한다.
                # 가상 백엔드는 전달받은 원장 equity를 그대로 사용한다.
                equity_before_close = backend.equity(fallback=equity)
                fill = backend.close(pos, float(bar["close"]))
                equity, trade_counter = _close_position(
                    conn, backend, pos, fill, TAKER_FEE,
                    "swing_ma35_exit", bar_time_str, equity_before_close, trade_counter,
                    main_mode)
                pos = None
                result["events"] += 1
        else:
            s1 = _get_tf_slice(tf_data, bar_time, "1d")
            pos = _try_entry(conn, backend, bar, bar_time_str, s4, s1,
                             equity, main_mode)
            if pos is not None:
                result["events"] += 1

    tracking.set_meta(conn, "last_processed_30m_ns",
                      int(new_bars.index[-1].value), MODE)
    if last_4h_ns is not None:
        tracking.set_meta(conn, "last_confirmed_4h_ns", int(last_4h_ns), MODE)
    tracking.set_meta(conn, "trade_id_counter", trade_counter, MODE)
    return result
