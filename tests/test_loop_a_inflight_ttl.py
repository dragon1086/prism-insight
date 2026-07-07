"""loop_a has_open_inflight 수정 단위테스트.

버그: SHADOW inflight 레코드가 status IN ('OPEN','SHADOW')로 취급돼 3주간 손절을 영구 차단.
수정: OPEN만 차단 대상 + TTL(오래된 stale OPEN 제외).
"""
import os
import sqlite3
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.loop_a_hardstop import has_open_inflight, INFLIGHT_TTL_SEC, _iso, _now  # noqa: E402


def _conn():
    c = sqlite3.connect(":memory:")
    c.execute(
        "CREATE TABLE loop_a_inflight_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "ticker TEXT, market TEXT, side TEXT DEFAULT 'SELL', loop_run_id TEXT, "
        "order_no TEXT, qty INTEGER, status TEXT, reason TEXT, submitted_ts TEXT)"
    )
    return c


def _ins(c, ticker, status, ts):
    c.execute(
        "INSERT INTO loop_a_inflight_orders(ticker,market,side,loop_run_id,status,submitted_ts) "
        "VALUES(?,?,?,?,?,?)", (ticker, "KR", "SELL", "run", status, ts))
    c.commit()


def test_shadow_record_does_not_block():
    # 핵심: SHADOW 레코드는 실주문이 아니므로 LIVE 매도를 막으면 안 됨
    c = _conn(); _ins(c, "080220", "SHADOW", _iso(_now()))
    assert has_open_inflight(c, "080220", "KR") is False


def test_fresh_open_blocks():
    c = _conn(); _ins(c, "080220", "OPEN", _iso(_now()))
    assert has_open_inflight(c, "080220", "KR") is True


def test_stale_open_does_not_block():
    # TTL 지난 OPEN 레코드는 차단 안 함(영구 stuck 방지)
    c = _conn(); _ins(c, "080220", "OPEN", _iso(_now() - timedelta(seconds=INFLIGHT_TTL_SEC + 60)))
    assert has_open_inflight(c, "080220", "KR") is False


def test_filled_and_rejected_do_not_block():
    c = _conn()
    _ins(c, "080220", "FILLED", _iso(_now()))
    _ins(c, "080220", "REJECTED", _iso(_now()))
    assert has_open_inflight(c, "080220", "KR") is False
