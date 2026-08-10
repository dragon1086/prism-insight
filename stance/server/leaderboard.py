"""Stance 프로토콜 — 리더보드 산출.

원장을 재생해 채점한 결과를 화면이 읽을 수 있는 형태로 내놓는다.
계산장부에 속하므로 언제든 지우고 다시 만들 수 있다.

★ 순위를 하나의 숫자로 줄이지 않는다. ★
하나로 줄이면 반드시 그 숫자를 겨냥한 조작이 생긴다.
대신 여러 지표를 나란히 두고, 모든 항목 옆에 **평균 투자비중**을 붙인다 —
"노출 얼마로 낸 점수인지" 가 보여야 해석이 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .engine import replay
from .ledger import Ledger
from .markets import MarketProfile, profile_for
from .models import Cadence
from .scoring import PROFILE_VERSION, Metrics, score

SCHEMA = "stance-leaderboard/1"


@dataclass
class Entry:
    strategy: str
    display_name: str
    handle: str
    market: str
    metrics: Metrics

    def to_dict(self) -> dict:
        m = self.metrics
        return {
            "strategy": self.strategy,
            "display_name": self.display_name,
            "handle": self.handle,
            "market": self.market,
            "qualified": m.qualified,
            "gate_failures": m.gate_failures,
            "experimental": m.experimental,
            "metrics": {
                "trading_days": m.trading_days,
                "cumulative_return": round(m.cumulative_return, 6),
                "sortino": round(m.sortino, 4),
                "max_drawdown": round(m.max_drawdown, 6),
                "calmar": round(m.calmar, 4),
                "avg_exposure": round(m.avg_exposure, 6),
                "coverage": round(m.coverage, 4),
                "cadence": m.cadence,
                "turnover": round(m.turnover, 4),
                "closed_trades": m.closed_trades_material,
                "win_rate": None if m.win_rate is None else round(m.win_rate, 4),
                "excess_return": None if m.excess_return is None else round(m.excess_return, 6),
                "paused_days": m.paused_days,
                "pending": m.pending,
            },
        }


def build(ledger: Ledger, strategies: list[tuple[str, str, str, str]]) -> dict:
    """리더보드 한 판을 만든다.

    strategies 는 (strategy_id, display_name, handle, market) 목록이다.
    """
    boards: dict[str, dict] = {}

    for strategy_id, display_name, handle, market in strategies:
        profile = profile_for(market)
        # 일별 마킹까지 포함해 재생한다. 빠지면 시간축이 없어 지표가 전부 0 이 된다.
        result = replay(ledger.full_timeline(strategy_id), costs=profile.costs)
        metrics = score(result, cadence=ledger.cadence_of(strategy_id), profile=profile)
        entry = Entry(strategy_id, display_name, handle, profile.code, metrics)

        board = boards.setdefault(profile.code, _empty_board(profile))
        board["entries"].append(entry.to_dict())

    # 시장별 보드를 섞지 않는다. 벤치마크와 변동성 스케일이 다르면 비교가 무의미하다.
    for board in boards.values():
        board["entries"].sort(key=lambda e: (not e["qualified"], -e["metrics"]["sortino"]))

    return {
        "schema": SCHEMA,
        "protocol": "stance/1",
        "score_profile": PROFILE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "live" if any(b["entries"] for b in boards.values()) else "preparing",
        "boards": boards,
    }


def _empty_board(profile: MarketProfile) -> dict:
    return {
        "market": profile.code,
        "currency": profile.currency,
        "support": profile.support.value,
        "price_authority": profile.price_authority,
        "mark_at": profile.mark_at,
        "min_track_periods": profile.min_track_periods,
        "notes": list(profile.notes),
        "entries": [],
    }


def preparing(markets: list[str] | None = None) -> dict:
    """아직 참여 전략이 없을 때의 초기 상태.

    빈 리더보드에는 아무도 오지 않는다. 그래도 **숨기지 않고** 준비 중임을 밝힌다.
    """
    codes = markets or ["KRX"]
    return {
        "schema": SCHEMA,
        "protocol": "stance/1",
        "score_profile": PROFILE_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "preparing",
        "boards": {c: _empty_board(profile_for(c)) for c in codes},
    }


def write_json(payload: dict, path: str | Path) -> Path:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":  # 대시보드용 초기 파일 생성
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "examples/dashboard/public/stance_leaderboard.json"
    print("생성:", write_json(preparing(["KRX"]), target))
