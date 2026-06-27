# live/tracking.py — 루트 DB(stock_tracking_db.sqlite) 기록 계층
#
# 매매 기록은 저장소 루트의 기존 주식 트래킹 DB(stock_tracking_db.sqlite)에
# btc_ 프리픽스 테이블로 추가한다. 기존 KR/US 주식 테이블은 절대 건드리지 않는다
# (CREATE TABLE IF NOT EXISTS 만 사용, btc_* 만 생성/수정).
#
# 테이블:
#   btc_positions       — 현재 열린 가상 포지션 (backtest Position 필드 미러 + mode)
#   btc_trading_history — 종결 트레이드 (backtest TradeLog 필드 미러 + mode)
#   btc_equity_curve    — (ts, equity, mode)
#   btc_events          — (ts, level, kind, message) 진입신호/주문/에러/하트비트 로그
#   btc_meta            — (mode, key, value) 크로스-바 트래커 영속 (pending_order,
#                         last_close_bar, last_new_entry_eval_4h_ns 등)
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

Mode = Literal["shadow", "demo", "live"]


def root_db_path() -> Path:
    """저장소 루트의 stock_tracking_db.sqlite 경로 (prism-btc/ 기준 ../).

    Path 기반으로 견고하게 해결한다: 이 파일은 prism-btc/live/tracking.py 이므로
    parents[2] == 저장소 루트(prism-insight/).
    """
    return Path(__file__).resolve().parents[2] / "stock_tracking_db.sqlite"


def get_connection(db_path: str | Path | None = None) -> sqlite3.Connection:
    """루트 DB 연결을 연다 (WAL). 스키마는 호출자가 ensure_schema 로 보장."""
    path = Path(db_path) if db_path is not None else root_db_path()
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


# ---------------------------------------------------------------------------
# Row dataclasses — backtest engine 의 Position / TradeLog 필드 미러
# ---------------------------------------------------------------------------

@dataclass
class PositionRow:
    """열린 가상 포지션. backtest.engine.Position 의 영속 필드 미러 + mode.

    backtest 의 Position 과 1:1 대응(누적기 acc_* / legs_closed 포함)이라
    재시작 후 동일한 집행 의미론으로 복원된다.
    """
    side: Literal["long", "short"]
    entry_price: float
    qty: float
    leverage: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    tp3_price: float
    liq_price: float
    entry_time: str
    tranche_index: int
    entry_bar_idx: int
    initial_risk: float
    trailing_active: bool = False
    be_stop_set: bool = False
    tp1_hit: bool = False
    tp2_hit: bool = False
    entry_fee: float = 0.0
    liq_breach_flagged: bool = False
    had_forced_reduce: bool = False
    initial_qty: float = 0.0
    acc_gross_pnl: float = 0.0
    acc_exit_fee: float = 0.0
    acc_funding: float = 0.0
    legs_closed: int = 0
    last_leg_exit_price: float = 0.0
    last_leg_reason: str = ""
    mode: Mode = "shadow"
    id: Optional[int] = None  # DB rowid (열린 포지션 식별)


@dataclass
class TradeRow:
    """종결 트레이드. backtest.engine.TradeLog 필드 미러 + mode."""
    trade_id: int
    side: Literal["long", "short"]
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    qty: float
    leverage: float
    sl_price: float
    exit_reason: str
    r_multiple: float
    fee_paid: float
    funding_paid: float
    tranche_index: int
    liq_price: float
    net_pnl: float = 0.0
    gross_pnl: float = 0.0
    gross_r_multiple: float = 0.0
    num_legs: int = 1
    mode: Mode = "shadow"


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS btc_positions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        mode            TEXT    NOT NULL,
        side            TEXT    NOT NULL,
        entry_price     REAL    NOT NULL,
        qty             REAL    NOT NULL,
        leverage        REAL    NOT NULL,
        sl_price        REAL    NOT NULL,
        tp1_price       REAL    NOT NULL,
        tp2_price       REAL    NOT NULL,
        tp3_price       REAL    NOT NULL,
        liq_price       REAL    NOT NULL,
        entry_time      TEXT    NOT NULL,
        tranche_index   INTEGER NOT NULL,
        entry_bar_idx   INTEGER NOT NULL,
        initial_risk    REAL    NOT NULL,
        trailing_active INTEGER NOT NULL DEFAULT 0,
        be_stop_set     INTEGER NOT NULL DEFAULT 0,
        tp1_hit         INTEGER NOT NULL DEFAULT 0,
        tp2_hit         INTEGER NOT NULL DEFAULT 0,
        entry_fee       REAL    NOT NULL DEFAULT 0,
        liq_breach_flagged INTEGER NOT NULL DEFAULT 0,
        had_forced_reduce  INTEGER NOT NULL DEFAULT 0,
        initial_qty     REAL    NOT NULL DEFAULT 0,
        acc_gross_pnl   REAL    NOT NULL DEFAULT 0,
        acc_exit_fee    REAL    NOT NULL DEFAULT 0,
        acc_funding     REAL    NOT NULL DEFAULT 0,
        legs_closed     INTEGER NOT NULL DEFAULT 0,
        last_leg_exit_price REAL NOT NULL DEFAULT 0,
        last_leg_reason TEXT    NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS btc_trading_history (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        mode            TEXT    NOT NULL,
        trade_id        INTEGER NOT NULL,
        side            TEXT    NOT NULL,
        entry_time      TEXT    NOT NULL,
        entry_price     REAL    NOT NULL,
        exit_time       TEXT    NOT NULL,
        exit_price      REAL    NOT NULL,
        qty             REAL    NOT NULL,
        leverage        REAL    NOT NULL,
        sl_price        REAL    NOT NULL,
        exit_reason     TEXT    NOT NULL,
        r_multiple      REAL    NOT NULL,
        fee_paid        REAL    NOT NULL,
        funding_paid    REAL    NOT NULL,
        tranche_index   INTEGER NOT NULL,
        liq_price       REAL    NOT NULL,
        net_pnl         REAL    NOT NULL DEFAULT 0,
        gross_pnl       REAL    NOT NULL DEFAULT 0,
        gross_r_multiple REAL   NOT NULL DEFAULT 0,
        num_legs        INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS btc_equity_curve (
        id     INTEGER PRIMARY KEY AUTOINCREMENT,
        mode   TEXT NOT NULL,
        ts     TEXT NOT NULL,
        equity REAL NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS btc_events (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        ts      TEXT NOT NULL,
        level   TEXT NOT NULL,
        kind    TEXT NOT NULL,
        message TEXT NOT NULL,
        mode    TEXT NOT NULL DEFAULT 'shadow'
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS btc_meta (
        mode  TEXT NOT NULL,
        key   TEXT NOT NULL,
        value TEXT NOT NULL,
        PRIMARY KEY (mode, key)
    )
    """,
    # 신호 평가 로그 — "진입하지 않은 순간"까지 전부 기록 (연구/감사용).
    # 4h 확정봉마다 1행 (~6행/일). 없으면 기각된 신호 분석에 매번 재시뮬이 필요하다.
    """
    CREATE TABLE IF NOT EXISTS btc_signal_log (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        ts       TEXT NOT NULL,                -- 평가 대상 30m 봉 시각
        mode     TEXT NOT NULL,
        score    REAL,                         -- alignment_score (-100~+100)
        ts_4h    REAL,                         -- 4h trend_strength
        ts_1d    REAL,                         -- 1d trend_strength
        side     TEXT NOT NULL,                -- long | short | none
        reason   TEXT NOT NULL,                -- 신호/기각 사유
        n_open   INTEGER NOT NULL DEFAULT 0,   -- 평가 시점 보유 포지션 수
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_btc_signal_ts ON btc_signal_log(mode, ts)",
]

# btc_positions 컬럼 순서 (PositionRow 영속 필드, id/mode 제외 — INSERT 용)
_POS_FIELDS = [
    "side", "entry_price", "qty", "leverage", "sl_price", "tp1_price",
    "tp2_price", "tp3_price", "liq_price", "entry_time", "tranche_index",
    "entry_bar_idx", "initial_risk", "trailing_active", "be_stop_set",
    "tp1_hit", "tp2_hit", "entry_fee", "liq_breach_flagged", "had_forced_reduce",
    "initial_qty", "acc_gross_pnl", "acc_exit_fee", "acc_funding", "legs_closed",
    "last_leg_exit_price", "last_leg_reason",
]
_BOOL_POS_FIELDS = {
    "trailing_active", "be_stop_set", "tp1_hit", "tp2_hit",
    "liq_breach_flagged", "had_forced_reduce",
}

_TRADE_FIELDS = [
    "trade_id", "side", "entry_time", "entry_price", "exit_time", "exit_price",
    "qty", "leverage", "sl_price", "exit_reason", "r_multiple", "fee_paid",
    "funding_paid", "tranche_index", "liq_price", "net_pnl", "gross_pnl",
    "gross_r_multiple", "num_legs",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    """btc_* 테이블을 생성한다 (없을 때만). 기존 테이블은 건드리지 않는다."""
    for stmt in _SCHEMA:
        conn.execute(stmt)
    conn.commit()


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def log_signal(conn: sqlite3.Connection, ts: str, *, score, ts_4h, ts_1d,
               side: str, reason: str, n_open: int = 0,
               mode: Mode = "shadow") -> None:
    """4h 신호 평가 1건 기록 — 진입하지 않은 평가도 전부 (연구/감사 데이터).

    관측 전용: 어떤 매매 결정에도 영향을 주지 않는다. 호출측은 실패를 흡수할 것.
    """
    conn.execute(
        "INSERT INTO btc_signal_log (ts, mode, score, ts_4h, ts_1d, side, reason, "
        "n_open, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ts, mode, score, ts_4h, ts_1d, side, reason[:200], n_open, _utcnow()))
    conn.commit()


# ---------------------------------------------------------------------------
# Position persistence
# ---------------------------------------------------------------------------

def save_position(conn: sqlite3.Connection, pos: PositionRow) -> int:
    """포지션을 저장(INSERT)하거나 갱신(UPDATE, pos.id 있을 때). rowid 반환."""
    d = asdict(pos)
    vals = [int(d[f]) if f in _BOOL_POS_FIELDS else d[f] for f in _POS_FIELDS]
    if pos.id is not None:
        sets = ", ".join(f"{f}=?" for f in _POS_FIELDS)
        conn.execute(
            f"UPDATE btc_positions SET {sets} WHERE id=?",
            (*vals, pos.id),
        )
        conn.commit()
        return pos.id
    cols = ", ".join(["mode", *_POS_FIELDS])
    qs = ", ".join(["?"] * (1 + len(_POS_FIELDS)))
    cur = conn.execute(
        f"INSERT INTO btc_positions ({cols}) VALUES ({qs})",
        (pos.mode, *vals),
    )
    conn.commit()
    pos.id = int(cur.lastrowid)
    return pos.id


def remove_position(conn: sqlite3.Connection, pos_id: int) -> None:
    conn.execute("DELETE FROM btc_positions WHERE id=?", (pos_id,))
    conn.commit()


def load_open_positions(conn: sqlite3.Connection, mode: Mode = "shadow") -> list[PositionRow]:
    rows = conn.execute(
        "SELECT * FROM btc_positions WHERE mode=? ORDER BY id ASC", (mode,)
    ).fetchall()
    out: list[PositionRow] = []
    for r in rows:
        kwargs = {f: r[f] for f in _POS_FIELDS}
        for f in _BOOL_POS_FIELDS:
            kwargs[f] = bool(kwargs[f])
        out.append(PositionRow(mode=r["mode"], id=r["id"], **kwargs))
    return out


# ---------------------------------------------------------------------------
# Trade / equity / event recording
# ---------------------------------------------------------------------------

def record_trade(conn: sqlite3.Connection, trade: TradeRow) -> None:
    d = asdict(trade)
    cols = ", ".join(["mode", *_TRADE_FIELDS])
    qs = ", ".join(["?"] * (1 + len(_TRADE_FIELDS)))
    conn.execute(
        f"INSERT INTO btc_trading_history ({cols}) VALUES ({qs})",
        (trade.mode, *[d[f] for f in _TRADE_FIELDS]),
    )
    conn.commit()


def record_equity(
    conn: sqlite3.Connection, equity: float, mode: Mode = "shadow", ts: str | None = None
) -> None:
    conn.execute(
        "INSERT INTO btc_equity_curve (mode, ts, equity) VALUES (?, ?, ?)",
        (mode, ts or _utcnow(), round(float(equity), 4)),
    )
    conn.commit()


def latest_equity(conn: sqlite3.Connection, mode: Mode = "shadow") -> float | None:
    """가장 최근 기록된 equity (없으면 None) — 가상 계좌 복원용."""
    r = conn.execute(
        "SELECT equity FROM btc_equity_curve WHERE mode=? ORDER BY id DESC LIMIT 1",
        (mode,),
    ).fetchone()
    return float(r["equity"]) if r is not None else None


def peak_equity(conn: sqlite3.Connection, mode: Mode = "shadow") -> float | None:
    """기록된 equity 곡선의 high-water-mark (E4 오버레이용). 없으면 None."""
    r = conn.execute(
        "SELECT MAX(equity) AS p FROM btc_equity_curve WHERE mode=?", (mode,)
    ).fetchone()
    return float(r["p"]) if r is not None and r["p"] is not None else None


def log_event(
    conn: sqlite3.Connection,
    kind: str,
    message: str,
    level: str = "info",
    mode: Mode = "shadow",
    ts: str | None = None,
) -> None:
    conn.execute(
        "INSERT INTO btc_events (ts, level, kind, message, mode) VALUES (?, ?, ?, ?, ?)",
        (ts or _utcnow(), level, kind, message, mode),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Meta (cross-bar trackers) — pending order, cooldown, 4h hardcap 등 영속
# ---------------------------------------------------------------------------

def get_meta(conn: sqlite3.Connection, key: str, mode: Mode = "shadow"):
    r = conn.execute(
        "SELECT value FROM btc_meta WHERE mode=? AND key=?", (mode, key)
    ).fetchone()
    if r is None:
        return None
    return json.loads(r["value"])


def set_meta(conn: sqlite3.Connection, key: str, value, mode: Mode = "shadow") -> None:
    conn.execute(
        "INSERT INTO btc_meta (mode, key, value) VALUES (?, ?, ?) "
        "ON CONFLICT(mode, key) DO UPDATE SET value=excluded.value",
        (mode, key, json.dumps(value)),
    )
    conn.commit()
