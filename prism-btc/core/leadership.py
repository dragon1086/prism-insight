# core/leadership.py — Phase B L 계층: BTC/ETH 상대강도 기반 노출 배수
#
# 검증 (tasks/btc_phase_b_design.md §9, 라운드8):
#   관측: 롱 평균 R btc_leading +0.90 (n=106) vs lagging +0.28 (n=76),
#   스프레드 +0.62R, 2022-23/2024-26 부호 일관 (round8_leadership.py).
#   A/B: 두 독립 구간 Calmar 개선 3.36→3.57 / 3.12→3.67, full 창은 tie
#   (round8_l_exposure_ab.py — 결과 JSON 참조).
#
# 원칙:
#   - 신규 진입 리스크에만 곱한다 (보유 관리/청산/신호 불변, 하드 게이트 아님).
#   - point-in-time: 호출 시각 이전에 "완결된" 1d 봉만 사용.
#   - **fail-open**: 데이터 없음/부족/정체(stale)/예외 → (1.0, 1.0) = 기존 동작.
#     L 계층 장애가 트레이딩을 절대 막지 않는다.
#
# 데이터: state/btc_spot.db (Binance spot 1d — analysis/round8_spot_collector.py
# 가 백필/증분 수집; 운영은 db-server 크론이 매일 갱신).
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from engine.config import (
    L_LAYER_ENABLED,
    L_MAX_STALE_DAYS,
    L_MULT_LONG_LAGGING,
    L_MULT_LONG_LEADING,
    L_MULT_SHORT_LAGGING,
    L_MULT_SHORT_LEADING,
    L_RS_WINDOW,
)

_DAY_MS = 86_400_000
_DEFAULT_DB = Path(__file__).resolve().parents[1] / "state" / "btc_spot.db"


def spot_db_path() -> Path:
    return Path(os.environ.get("PRISM_BTC_SPOT_DB", str(_DEFAULT_DB)))


def leadership_multipliers(now_ms: int | None = None) -> tuple[float, float, str]:
    """(long_mult, short_mult, reason) — 어떤 실패에서도 (1.0, 1.0) fail-open."""
    if not L_LAYER_ENABLED:
        return 1.0, 1.0, "disabled"
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    try:
        path = spot_db_path()
        if not path.exists():
            return 1.0, 1.0, "fail-open: spot db missing"
        conn = sqlite3.connect(path)
        try:
            need = L_RS_WINDOW + 1
            q = ("SELECT close FROM spot_klines WHERE symbol=? AND timeframe='1d' "
                 "AND confirmed=1 AND open_time + ? <= ? "
                 "ORDER BY open_time DESC LIMIT ?")
            btc = [r[0] for r in conn.execute(q, ("BTCUSDT", _DAY_MS, now_ms, need))]
            eth = [r[0] for r in conn.execute(q, ("ETHUSDT", _DAY_MS, now_ms, need))]
            row = conn.execute(
                "SELECT MAX(open_time + ?) FROM spot_klines WHERE symbol='BTCUSDT' "
                "AND timeframe='1d' AND confirmed=1 AND open_time + ? <= ?",
                (_DAY_MS, _DAY_MS, now_ms)).fetchone()
        finally:
            conn.close()
        if len(btc) < need or len(eth) < need:
            return 1.0, 1.0, f"fail-open: insufficient bars ({len(btc)}/{len(eth)})"
        last_close_ms = row[0]
        if last_close_ms is None or now_ms - last_close_ms > L_MAX_STALE_DAYS * _DAY_MS:
            return 1.0, 1.0, "fail-open: spot data stale"
        # btc[0]=최신 완결봉, btc[-1]=RS_WINDOW 봉 전 → 60일 수익률 차
        rs = (btc[0] / btc[-1]) - (eth[0] / eth[-1])
        if rs > 0:
            return L_MULT_LONG_LEADING, L_MULT_SHORT_LEADING, f"btc_leading rs={rs:+.4f}"
        return L_MULT_LONG_LAGGING, L_MULT_SHORT_LAGGING, f"btc_lagging rs={rs:+.4f}"
    except Exception as exc:  # noqa: BLE001 — fail-open 계약
        return 1.0, 1.0, f"fail-open: {exc}"
