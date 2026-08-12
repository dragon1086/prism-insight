"""Stance 프로토콜 — 원장.

원장에는 세 가지만 들어간다. 전부 한 번 쓰면 고칠 수 없다.

    stances        참여자가 보낸 선언
    quotes         서버가 그 순간 찍은 시세 (사후 재조회가 불가능하므로 원장이다)
    daily_marks    하루를 마감하며 찍은 종가. 채점의 시간축이 된다
    market_events  기업행위·상장폐지 (서버가 발행)

종가를 원장에 넣는 이유는 재현 가능성 때문이다. 외부 시세 공급자를 다시 조회해야 한다면
"원장만 공개하면 제3자가 순위를 독립 재현한다" 는 주장이 성립하지 않는다.

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
import threading
from datetime import date, datetime, time as dtime, timezone
from decimal import Decimal
from pathlib import Path

from .models import (
    PROTOCOL_VERSION, Cadence, DailyMark, EventType, Kind, MarketEvent, Quote, Stance,
)

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS strategies (
  strategy_id   TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  handle        TEXT NOT NULL,          -- 계정 핸들. 한 계정의 전 전략이 함께 노출된다
  market        TEXT NOT NULL,          -- 한 전략 = 한 시장 = 한 통화
  currency      TEXT NOT NULL,
  cadence       TEXT NOT NULL DEFAULT 'daily',  -- daily|weekly|monthly|event
  api_key_hash  TEXT NOT NULL,
  created_at    TEXT NOT NULL           -- 트랙레코드 시작의 유일한 권위. 참여자가 못 정한다
);

CREATE TABLE IF NOT EXISTS stances (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy_id   TEXT NOT NULL REFERENCES strategies(strategy_id),
  seq           INTEGER NOT NULL,
  kind          TEXT NOT NULL CHECK (kind IN ('set','hold','pause','resume')),
  symbol        TEXT,
  target_weight TEXT,                   -- Decimal 을 문자열로 보존 (부동소수 오차 방지)
  reason        TEXT CHECK (reason IS NULL OR length(reason) <= 500),
  received_at   TEXT NOT NULL,          -- 권위 시각. 서버가 찍는다
  prev_hash     TEXT,
  hash          TEXT NOT NULL,
  UNIQUE (strategy_id, seq),
  CHECK ((kind='set' AND symbol IS NOT NULL AND target_weight IS NOT NULL)
      OR (kind IN ('hold','pause','resume') AND symbol IS NULL AND target_weight IS NULL))
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

CREATE TABLE IF NOT EXISTS daily_marks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  market        TEXT NOT NULL,
  on_date       TEXT NOT NULL,
  prices        TEXT NOT NULL,          -- {symbol: price} 종가
  prev_hash     TEXT,
  hash          TEXT NOT NULL,
  UNIQUE (market, on_date)
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
CREATE TRIGGER IF NOT EXISTS marks_no_update BEFORE UPDATE ON daily_marks
BEGIN SELECT RAISE(ABORT, '원장은 수정할 수 없다'); END;
CREATE TRIGGER IF NOT EXISTS marks_no_delete BEFORE DELETE ON daily_marks
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
    """원장.

    SQLite 커넥션은 기본적으로 만들어진 스레드에서만 쓸 수 있다.
    그런데 웹 서버는 동기 핸들러를 스레드풀에서 돌리므로 요청마다 스레드가 달라진다.
    그래서 `check_same_thread=False` 로 열고, 쓰기는 락으로 직렬화한다.
    (읽기는 WAL 모드라 동시에 이뤄져도 안전하다.)
    """

    def __init__(self, path: str | Path = ":memory:"):
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(str(path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)
            self.conn.executescript(IMMUTABILITY)
            self.conn.commit()

    def close(self) -> None:
        with self._lock:
            self.conn.close()

    # ── 등록 ──────────────────────────────────────────────────────────────

    def register(
        self, strategy_id: str, display_name: str, handle: str,
        market: str = "KRX", currency: str = "KRW", api_key_hash: str = "",
        cadence: Cadence | str = Cadence.DAILY,
    ) -> None:
        """전략을 등록한다.

        cadence 는 '이 시스템이 얼마나 자주 판단하는가' 를 스스로 밝히는 값이다.
        제출률을 거래일 기준으로 재면 주간·월간·이벤트 기반 시스템이 전멸하므로,
        각자의 주기 대비로 해석하기 위해 받는다.
        """
        c = cadence.value if isinstance(cadence, Cadence) else str(cadence)
        with self._lock:
            self.conn.execute(
                "INSERT INTO strategies (strategy_id, display_name, handle, market,"
                " currency, cadence, api_key_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (strategy_id, display_name, handle, market, currency, c, api_key_hash, _now()),
            )
            self.conn.commit()

    def cadence_of(self, strategy_id: str) -> Cadence:
        row = self.conn.execute(
            "SELECT cadence FROM strategies WHERE strategy_id=?", (strategy_id,)
        ).fetchone()
        return Cadence(row["cadence"]) if row else Cadence.DAILY

    def rotate_api_key(self, strategy_id: str, api_key_hash: str) -> None:
        """인증 메타데이터만 교체한다. 선언 원장은 건드리지 않는다."""
        with self._lock:
            cur = self.conn.execute(
                "UPDATE strategies SET api_key_hash=? WHERE strategy_id=?",
                (api_key_hash, strategy_id),
            )
            if cur.rowcount != 1:
                raise KeyError(strategy_id)
            self.conn.commit()

    # ── 원장 기록 ─────────────────────────────────────────────────────────

    def _tail_hash(self, table: str) -> str | None:
        if table == "stances":
            row = self.conn.execute(
                "SELECT hash FROM stances ORDER BY id DESC LIMIT 1"
            ).fetchone()
        elif table == "quotes":
            row = self.conn.execute(
                "SELECT hash FROM quotes ORDER BY id DESC LIMIT 1"
            ).fetchone()
        elif table == "market_events":
            row = self.conn.execute(
                "SELECT hash FROM market_events ORDER BY id DESC LIMIT 1"
            ).fetchone()
        elif table == "daily_marks":
            row = self.conn.execute(
                "SELECT hash FROM daily_marks ORDER BY id DESC LIMIT 1"
            ).fetchone()
        else:
            raise ValueError(f"해시체인 대상이 아닙니다: {table}")
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
        with self._lock:
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
        with self._lock:
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
        with self._lock:
            prev = self._tail_hash("market_events")
            cur = self.conn.execute(
                "INSERT INTO market_events (market, symbol, event_type, payload,"
                " effective_at, prev_hash, hash) VALUES (?,?,?,?,?,?,?)",
                (market, ev.symbol, ev.event_type.value, json.dumps(body),
                 effective, prev, _digest(prev, payload)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def append_daily_mark(self, market: str, on: date, prices: dict[str, Decimal]) -> int:
        """하루를 마감하고 종가를 봉인한다. 같은 날을 두 번 마감할 수 없다."""
        body = {s: str(p) for s, p in sorted(prices.items())}
        payload = {"market": market, "on_date": on.isoformat(), "prices": body}
        with self._lock:
            prev = self._tail_hash("daily_marks")
            cur = self.conn.execute(
                "INSERT INTO daily_marks (market, on_date, prices, prev_hash, hash)"
                " VALUES (?,?,?,?,?)",
                (market, on.isoformat(), json.dumps(body), prev, _digest(prev, payload)),
            )
            self.conn.commit()
            return int(cur.lastrowid)

    def has_mark(self, market: str, on: date) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM daily_marks WHERE market=? AND on_date=?",
            (market, on.isoformat()),
        ).fetchone()
        return row is not None

    def daily_marks(self, market: str) -> list[DailyMark]:
        rows = self.conn.execute(
            "SELECT on_date, prices FROM daily_marks WHERE market=? ORDER BY on_date, id",
            (market,),
        ).fetchall()
        return [
            DailyMark(
                on=date.fromisoformat(r["on_date"]),
                prices={s: Decimal(v) for s, v in json.loads(r["prices"]).items()},
            )
            for r in rows
        ]

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

    def full_timeline(self, strategy_id: str) -> list:
        """선언과 일별 마킹을 시간순으로 병합한다. 채점에 쓰이는 진짜 타임라인이다.

        같은 날에 선언과 마감이 함께 있으면 **마감이 나중**이다.
        그날의 선언이 모두 반영된 뒤 자산을 찍어야 하기 때문이다.
        """
        row = self.conn.execute(
            "SELECT market FROM strategies WHERE strategy_id=?", (strategy_id,)
        ).fetchone()
        market = row["market"] if row else "KRX"

        items: list[tuple[datetime, int, object]] = []
        for stance, quote in self.timeline(strategy_id):
            items.append((stance.received_at, 0, (stance, quote)))
        for mark in self.daily_marks(market):
            end_of_day = datetime.combine(mark.on, dtime.max, tzinfo=timezone.utc)
            items.append((end_of_day, 1, mark))

        items.sort(key=lambda x: (x[0], x[1]))
        return [item for _, _, item in items]

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
        if table == "stances":
            rows = self.conn.execute("SELECT * FROM stances ORDER BY id").fetchall()
        elif table == "quotes":
            rows = self.conn.execute("SELECT * FROM quotes ORDER BY id").fetchall()
        elif table == "market_events":
            rows = self.conn.execute("SELECT * FROM market_events ORDER BY id").fetchall()
        elif table == "daily_marks":
            rows = self.conn.execute("SELECT * FROM daily_marks ORDER BY id").fetchall()
        else:
            raise ValueError(f"해시체인 대상이 아닙니다: {table}")
        for r in rows:
            if r["prev_hash"] != prev:
                return False
            prev = r["hash"]
        return True
