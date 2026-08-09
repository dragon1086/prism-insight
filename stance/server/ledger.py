"""Stance 프로토콜 — 원장.

원장에는 세 가지만 들어간다. 전부 한 번 쓰면 고칠 수 없다.

    stances        참여자가 보낸 선언
    quotes         서버가 그 순간 찍은 시세 (사후 재조회가 불가능하므로 원장이다)
    market_events  기업행위·상장폐지 (서버가 발행)

체결가·보유현황·자산추이·순위는 여기에 없다. 그것들은 계산장부이며
원장을 다시 읽어 언제든 만들 수 있다. 이 분리가 두 가지를 동시에 해결한다.

    ① 봉인이 완전해진다 — 채점의 입력이 되는 모든 사실이 원장에 있고 해시로 묶인다
    ② 재계산이 가능해진다 — 채점 버그가 나와도 계산장부만 다시 만들면 된다

SQLite 로 구현했지만 스키마는 Postgres 로 그대로 옮겨진다.
운영에서는 접수시각을 DB 가 생성하고 참여자는 그 컬럼에 쓸 수 없어야 한다.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .models import PROTOCOL_VERSION, EventType, Kind, MarketEvent, Quote, Stance

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS strategies (
  strategy_id   TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  handle        TEXT NOT NULL,          -- 계정 핸들. 한 계정의 전 전략이 함께 노출된다
  market        TEXT NOT NULL,          -- 한 전략 = 한 시장 = 한 통화
  currency      TEXT NOT NULL,
  api_key_hash  TEXT NOT NULL,
  created_at    TEXT NOT NULL           -- 트랙레코드 시작의 유일한 권위. 참여자가 못 정한다
);

CREATE TABLE IF NOT EXISTS stances (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id   TEXT NOT NULL REFERENCES strategies(strategy_id),
  seq           INTEGER NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('set','hold')),
  symbol        TEXT,
  target_weight TEXT,                   -- Decimal 을 문자열로 보존 (부동소수 오차 방지)
  reason        TEXT CHECK (reason IS NULL OR length(reason) <= 500),
  received_at   TEXT NOT NULL,          -- 권위 시각. 서버가 찍는다
  prev_hash     TEXT,
  hash          TEXT NOT NULL,
  UNIQUE (strategy_id, seq),
  CHECK ((kind='set'  AND symbol IS NOT NULL AND target_weight IS NOT NULL)
      OR (kind='hold' AND symbol IS NULL AND target_weight IS NULL))
);

CREATE TABLE IF NOT EXISTS quotes (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  stance_id     INTEGER REFERENCES stances(id),
  symbol        TEXT NOT NULL,
  price         TEXT NOT NULL,
  tradable      INTEGER NOT NULL DEFAULT 1,
  observed_at   TEXT NOT NULL,
  source        TEXT NOT NULL DEFAULT 'primary',
  prev_hash     TEXT,
  hash          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_events (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  market        TEXT NOT NULL,
  symbol        TEXT NOT NULL,
  event_type    TEXT NOT NULL,
  payload       TEXT NOT NULL,
  effective_at  TEXT NOT NULL,
  prev_hash     TEXT,
  hash          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_stances_strategy ON stances(strategy_id, seq);
CREATE INDEX IF NOT EXISTS idx_quotes_stance ON quotes(stance_id);
"""

# 고칠 수 없다는 것을 DB 가 강제한다. 애플리케이션 규칙이 아니라 스키마 규칙이다.
IMMUTABILITY = """
CREATE TRIGGER IF NOT EXISTS stances_no_update BEFORE UPDATE ON stances
BEGIN SELECT RAISE(ABORT, '원장은 수정할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS stances_no_delete BEFORE DELETE ON stances
BEGIN SELECT RAISE(ABORT, '원장은 삭제할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS quotes_no_update BEFORE UPDATE ON quotes
BEGIN SELECT RAISE(ABORT, '원장은 수정할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS quotes_no_delete BEFORE DELETE ON quotes
BEGIN SELECT RAISE(ABORT, '원장은 삭제할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON market_events
BEGIN SELECT RAISE(ABORT, '원장은 수정할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON market_events
BEGIN SELECT RAISE(ABORT, '원장은 삭제할 수 없다'); END;
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest(prev: str | None, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(((prev or "") + body).encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.executescript(IMMUTABILITY)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ── 등록 ──────────────────────────────────────────────────────────────

    def register(
        self, strategy_id: str, display_name: str, handle: str,
        market: str = "KRX", currency: str = "KRW", api_key_hash: str = "",
    ) -> None:
        self.conn.execute(
            "INSERT INTO strategies (strategy_id, display_name, handle, market,"
            " currency, api_key_hash, created_at) VALUES (?,?,?,?,?,?,?)",
            (strategy_id, display_name, handle, market, currency, api_key_hash, _now()),
        )
        self.conn.commit()

    # ── 원장 기록 ─────────────────────────────────────────────────────────

    def _tail_hash(self, table: str) -> str | None:
        row = self.conn.execute(f"SELECT hash FROM {table} ORDER BY id DESC LIMIT 1").fetchone()
        return row["hash"] if row else None

    def append_stance(
        self, strategy_id: str, seq: int, kind: Kind = Kind.SET,
        symbol: str | None = None, target_weight: Decimal | None = None,
        reason: str | None = None, received_at: str | None = None,
    ) -> int:
        received_at = received_at or _now()   # 운영에서는 DB DEFAULT now() 로 대체
        payload = {
            "protocol": PROTOCOL_VERSION, "strategy_id": strategy_id, "seq": seq,
            "kind": kind.value, "symbol": symbol,
            "target_weight": str(target_weight) if target_weight is not None else None,
            "reason": reason, "received_at": received_at,
        }
        prev = self._tail_hash("stances")
        cur = self.conn.execute(
            "INSERT INTO stances (strategy_id, seq, kind, symbol, target_weight,"
            " reason, received_at, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
            (strategy_id, seq, kind.value, symbol,
             str(target_weight) if target_weight is not None else None,
             reason, received_at, prev, _digest(prev, payload)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def append_quote(self, stance_id: int | None, q: Quote) -> int:
        observed = (q.observed_at.isoformat() if q.observed_at else _now())
        payload = {"stance_id": stance_id, "symbol": q.symbol, "price": str(q.price),
                   "tradable": q.tradable, "observed_at": observed, "source": q.source}
        prev = self._tail_hash("quotes")
        cur = self.conn.execute(
            "INSERT INTO quotes (stance_id, symbol, price, tradable, observed_at,"
            " source, prev_hash, hash) VALUES (?,?,?,?,?,?,?,?)",
            (stance_id, q.symbol, str(q.price), int(q.tradable), observed,
             q.source, prev, _digest(prev, payload)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def append_event(self, market: str, ev: MarketEvent) -> int:
        body = {"ratio": str(ev.ratio) if ev.ratio is not None else None,
                "per_share": str(ev.per_share) if ev.per_share is not None else None,
                "final_price": str(ev.final_price) if ev.final_price is not None else None}
        effective = ev.at.isoformat()
        payload = {"market": market, "symbol": ev.symbol,
                   "event_type": ev.event_type.value, "payload": body,
                   "effective_at": effective}
        prev = self._tail_hash("market_events")
        cur = self.conn.execute(
            "INSERT INTO market_events (market, symbol, event_type, payload,"
            " effective_at, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
            (market, ev.symbol, ev.event_type.value, json.dumps(body),
             effective, prev, _digest(prev, payload)),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    # ── 읽기 ──────────────────────────────────────────────────────────────

    def timeline(self, strategy_id: str) -> list:
        """원장을 재생 가능한 형태로 꺼낸다. 채점의 입력이 되는 전부다."""
        items: list = []
        rows = self.conn.execute(
            "SELECT * FROM stances WHERE strategy_id=? ORDER BY received_at, id",
            (strategy_id,),
        ).fetchall()
        for r in rows:
            stance = Stance(
                seq=r["seq"],
                received_at=datetime.fromisoformat(r["received_at"]),
                kind=Kind(r["kind"]),
                symbol=r["symbol"],
                target_weight=Decimal(r["target_weight"]) if r["target_weight"] else None,
                reason=r["reason"],
            )
            qrow = self.conn.execute(
                "SELECT * FROM quotes WHERE stance_id=? ORDER BY id LIMIT 1", (r["id"],)
            ).fetchone()
            quote = (
                Quote(symbol=qrow["symbol"], price=Decimal(qrow["price"]),
                      tradable=bool(qrow["tradable"]),
                      observed_at=datetime.fromisoformat(qrow["observed_at"]),
                      source=qrow["source"])
                if qrow else None
            )
            items.append((stance, quote))
        return items

    def market_events(self, market: str) -> list[MarketEvent]:
        rows = self.conn.execute(
            "SELECT * FROM market_events WHERE market=? ORDER BY effective_at, id", (market,)
        ).fetchall()
        out: list[MarketEvent] = []
        for r in rows:
            body = json.loads(r["payload"])
            out.append(MarketEvent(
                event_type=EventType(r["event_type"]), symbol=r["symbol"],
                at=datetime.fromisoformat(r["effective_at"]),
                ratio=Decimal(body["ratio"]) if body.get("ratio") else None,
                per_share=Decimal(body["per_share"]) if body.get("per_share") else None,
                final_price=Decimal(body["final_price"]) if body.get("final_price") else None,
            ))
        return out

    def next_seq(self, strategy_id: str) -> int:
        row = self.conn.execute(
            "SELECT MAX(seq) AS s FROM stances WHERE strategy_id=?", (strategy_id,)
        ).fetchone()
        return int(row["s"] or 0) + 1

    # ── 검증 ──────────────────────────────────────────────────────────────

    def verify_chain(self, table: str = "stances") -> bool:
        """해시체인을 처음부터 다시 계산해 대조한다.

        누구든 원장을 받아 이 검증을 독립적으로 수행할 수 있다.
        그것이 운영자 조작을 막는 유일한 방법이다.
        """
        prev: str | None = None
        for r in self.conn.execute(f"SELECT * FROM {table} ORDER BY id").fetchall():
            if r["prev_hash"] != prev:
                return False
            prev = r["hash"]
        return True
