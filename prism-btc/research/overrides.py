# research/overrides.py — 튜너블 화이트리스트 + 챔피언 오버라이드 레이어
#
# 챔피언 config = 동결 상수(코드) + btc_overrides(status='active').
# 코드는 영원히 동결 — 검증된 개선은 이 DB 레이어를 통해서만 행동을 바꾼다.
#
# 화이트리스트가 안전장치의 1번이다: LLM 이 어떤 가설을 내든
# 여기 등록된 (param, 범위) 밖의 값은 시스템에 닿을 수 없다.
#
# 멀티 타깃 패치 이유 (tasks/btc_autoloop_design.md):
#   - engine.config 게이트(ENTRY_SCORE_MIN/TS_MIN): signal.py 가 함수-로컬 임포트
#     → config 모듈 속성 패치가 런타임에 그대로 반영된다.
#   - 트레일 상수(BE_TRAIL_ACTIVATE_R/TRAILING_TF): backtest.engine 은 모듈 글로벌
#     참조(런타임), live.shadow 는 import 시 값 복사 → 두 네임스페이스 모두 패치.
from __future__ import annotations

import importlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

import pandas as pd

MAX_ACTIVE = 2  # 복잡도 예산: 동시 활성 오버라이드 상한


@dataclass(frozen=True)
class Tunable:
    """자동 루프가 만질 수 있는 단 하나의 손잡이 정의."""
    targets: tuple[tuple[str, str], ...]   # (module_path, attr)
    kind: str                              # "float" | "enum"
    frozen: Any                            # 동결 스펙 값 (문서화용 — 적용엔 미사용)
    lo: Optional[float] = None
    hi: Optional[float] = None
    choices: tuple = field(default_factory=tuple)
    note: str = ""


TUNABLES: dict[str, Tunable] = {
    "ENTRY_SCORE_MIN": Tunable(
        targets=(("engine.config", "ENTRY_SCORE_MIN"),),
        kind="float", frozen=70.0, lo=55.0, hi=90.0,
        note="진입 정렬점수 하한 (라운드4: 85 기각·70 확정 이력 있음)",
    ),
    "TS_MIN": Tunable(
        targets=(("engine.config", "TS_MIN"),),
        kind="float", frozen=2.0, lo=1.5, hi=4.0,
        note="4h 추세강도 게이트 하한",
    ),
    "BE_TRAIL_ACTIVATE_R": Tunable(
        targets=(("backtest.engine", "BE_TRAIL_ACTIVATE_R"),
                 ("live.shadow", "BE_TRAIL_ACTIVATE_R")),
        kind="float", frozen=1.5, lo=1.0, hi=3.0,
        note="BE/트레일 활성 R 문턱 (2.0 은 기각 이력 — 재검증은 데이터 갱신시에만 의미)",
    ),
    "TRAILING_TF": Tunable(
        targets=(("backtest.engine", "TRAILING_TF"),
                 ("live.shadow", "TRAILING_TF")),
        kind="enum", frozen="12h", choices=("4h", "12h", "1d"),
        note="트레일 기준 TF (MA10)",
    ),
}


class OverrideError(ValueError):
    """화이트리스트 위반 — 자동 루프가 이 값을 시스템에 적용할 수 없음."""


def validate(param: str, value: Any) -> Any:
    """화이트리스트 검증 + 정규화. 통과 못 하면 OverrideError."""
    t = TUNABLES.get(param)
    if t is None:
        raise OverrideError(f"param {param!r} 은 화이트리스트에 없음")
    if t.kind == "float":
        try:
            v = float(value)
        except (TypeError, ValueError):
            raise OverrideError(f"{param}: float 아님: {value!r}")
        if not (t.lo <= v <= t.hi):
            raise OverrideError(f"{param}={v} 범위 밖 [{t.lo}, {t.hi}]")
        return v
    if t.kind == "enum":
        v = str(value)
        if v not in t.choices:
            raise OverrideError(f"{param}={v!r} 허용값 아님 {t.choices}")
        return v
    raise OverrideError(f"{param}: 알 수 없는 kind {t.kind}")


# ---------------------------------------------------------------------------
# 적용기 — 모듈 속성 패치 (적용/복원)
# ---------------------------------------------------------------------------

def _patch(param: str, value: Any) -> list[tuple[Any, str, Any]]:
    """타깃 모듈들에 적용. (module, attr, 이전값) 목록 반환 (복원용)."""
    saved = []
    for mod_path, attr in TUNABLES[param].targets:
        mod = importlib.import_module(mod_path)
        saved.append((mod, attr, getattr(mod, attr)))
        setattr(mod, attr, value)
    return saved


def apply_persistent(ovr: dict[str, Any]) -> dict[str, Any]:
    """복원 없이 적용 (데몬 tick 용 — 프로세스 수명 동안 유지, 재호출 멱등)."""
    applied = {}
    for param, value in ovr.items():
        v = validate(param, value)
        _patch(param, v)
        applied[param] = v
    return applied


@contextmanager
def apply(ovr: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """일시 적용 + 원상 복원 (연구공장 baseline/variant 실행용)."""
    saved: list[tuple[Any, str, Any]] = []
    try:
        applied = {}
        for param, value in ovr.items():
            v = validate(param, value)
            saved.extend(_patch(param, v))
            applied[param] = v
        yield applied
    finally:
        for mod, attr, old in reversed(saved):
            setattr(mod, attr, old)


# ---------------------------------------------------------------------------
# 영속 — btc_overrides (챔피언 상태의 단일 출처)
# ---------------------------------------------------------------------------

_SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS btc_overrides (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        mode             TEXT    NOT NULL,
        param            TEXT    NOT NULL,
        value            TEXT    NOT NULL,          -- JSON 인코딩 값
        status           TEXT    NOT NULL DEFAULT 'active',  -- active | retired
        source_lesson_id INTEGER,
        evidence         TEXT,                      -- 합격 판정 메트릭 전문 JSON
        created_at       TEXT    NOT NULL,
        retired_at       TEXT,
        retire_reason    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_btc_overrides_active ON btc_overrides(mode, status)",
]


def ensure_schema(conn: sqlite3.Connection) -> None:
    for stmt in _SCHEMA:
        conn.execute(stmt)
    conn.commit()


def load_active(conn: sqlite3.Connection, mode: str = "shadow") -> dict[str, Any]:
    """현재 챔피언 오버라이드. 검증 실패 항목은 무시 (화이트리스트 축소 등 방어)."""
    ensure_schema(conn)
    out: dict[str, Any] = {}
    cur = conn.execute(
        "SELECT param, value FROM btc_overrides WHERE mode=? AND status='active' "
        "ORDER BY id", (mode,))
    for param, value_json in cur.fetchall():
        try:
            out[param] = validate(param, json.loads(value_json))
        except (OverrideError, json.JSONDecodeError):
            continue
    return out


def apply_active(conn: sqlite3.Connection, mode: str = "shadow") -> dict[str, Any]:
    """데몬 진입점: 활성 오버라이드 로드 + 영구 적용. 적용된 dict 반환."""
    return apply_persistent(load_active(conn, mode))


def activate(conn: sqlite3.Connection, param: str, value: Any,
             source_lesson_id: Optional[int], evidence: dict,
             mode: str = "shadow") -> int:
    """검증 합격 오버라이드 활성화.

    - 같은 param 의 기존 active 는 자동 은퇴 (교체).
    - 복잡도 예산: 신규 param 인데 슬롯(MAX_ACTIVE) 초과면 OverrideError —
      활성화는 연구공장이 슬롯 정리 후에만 다시 시도한다.
    """
    v = validate(param, value)
    ensure_schema(conn)
    now = pd.Timestamp.now("UTC").isoformat()
    active = load_active(conn, mode)
    if param not in active and len(active) >= MAX_ACTIVE:
        raise OverrideError(
            f"활성 슬롯 초과 (MAX_ACTIVE={MAX_ACTIVE}): {sorted(active)} 사용 중")
    conn.execute(
        "UPDATE btc_overrides SET status='retired', retired_at=?, "
        "retire_reason='replaced' WHERE mode=? AND param=? AND status='active'",
        (now, mode, param))
    cur = conn.execute(
        "INSERT INTO btc_overrides (mode, param, value, status, source_lesson_id, "
        "evidence, created_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
        (mode, param, json.dumps(v), source_lesson_id,
         json.dumps(evidence, ensure_ascii=False), now))
    conn.commit()
    return int(cur.lastrowid)


def retire(conn: sqlite3.Connection, override_id: int, reason: str) -> None:
    conn.execute(
        "UPDATE btc_overrides SET status='retired', retired_at=?, retire_reason=? "
        "WHERE id=? AND status='active'",
        (pd.Timestamp.now("UTC").isoformat(), reason, override_id))
    conn.commit()
