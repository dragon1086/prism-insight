"""Stance 프로토콜 — 채점 프로파일 `stance-score/1`.

★ 이 파일은 코어가 아니다. ★

어떤 지표가 좋은 전략을 가려내는지에는 정답이 없다.
수익을 위험으로 나누는 방식은 현금을 많이 든 전략을 구조적으로 우대하고,
시장 대비 초과수익으로 재면 국면에 따라 노출 방향에 베팅하게 된다.
노출 5% 짜리와 85% 짜리를 하나의 숫자로 줄 세우려면
낮은 노출이 좋은지 높은 노출이 좋은지를 미리 정해야 하는데, 그 결정 자체가 편향이다.

그래서 정답을 하나 고르는 대신 갈아끼울 수 있게 만든다.
이 모듈을 통째로 바꿔도 원장과 엔진은 그대로다.

설계 메모
    · 하락편차에 하한을 둔다. 조작을 벌하려는 것이 아니라 0 으로 나누는 것을 막는 장치다.
      하한을 크게 잡으면 정상적인 방어 전략이 벌을 받는다. 실측으로 0.05%/일 이 적정했다.
    · 참가 요건에 투자비중을 넣지 않는다. 현금을 드는 것도 하나의 판단이며,
      판단 리더보드가 특정 판단을 금지하면 자기모순이다.
    · 하나의 순위로 줄이지 않는다. 하나로 줄이면 반드시 그 숫자를 겨냥한 조작이 생긴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .engine import ReplayResult

PROFILE_VERSION = "stance-score/1"

TRADING_DAYS = 252
DOWNSIDE_FLOOR_DAILY = 0.0005   # 0 으로 나누는 것을 막는 최소한의 하한
GATE_MIN_DAYS = 60
GATE_MIN_TRADES = 20
GATE_MIN_COVERAGE = 0.70


@dataclass
class Metrics:
    profile: str = PROFILE_VERSION

    trading_days: int = 0
    cumulative_return: float = 0.0
    sortino: float = 0.0
    max_drawdown: float = 0.0
    calmar: float = 0.0

    avg_exposure: float = 0.0          # 순위와 함께 항상 보여준다
    coverage: float = 0.0              # 성실하게 보고했는가
    turnover: float = 0.0
    closed_trades: int = 0
    closed_trades_material: int = 0
    losing_days: int = 0
    win_rate: float | None = None

    benchmark_return: float | None = None
    excess_return: float | None = None

    qualified: bool = False
    gate_failures: list[str] = field(default_factory=list)


def _returns(series: list[tuple[date, Decimal]]) -> list[float]:
    out: list[float] = []
    for i in range(1, len(series)):
        prev = float(series[i - 1][1])
        if prev <= 0:
            out.append(0.0)
            continue
        out.append(float(series[i][1]) / prev - 1.0)
    return out


def _max_drawdown(series: list[tuple[date, Decimal]]) -> float:
    peak = float("-inf")
    worst = 0.0
    for _, v in series:
        x = float(v)
        peak = max(peak, x)
        if peak > 0:
            worst = min(worst, x / peak - 1.0)
    return abs(worst)


def _sortino(rets: list[float], floor: float = DOWNSIDE_FLOOR_DAILY) -> float:
    """하락위험 대비 수익.

    분모가 0 에 수렴하면 값이 무한대로 발산한다. 하한이 그것을 막는다.
    하한을 넘는 실제 하락편차를 가진 전략에는 아무 영향이 없다.
    """
    if not rets:
        return 0.0
    mean = sum(rets) / len(rets)
    downs = [r for r in rets if r < 0]
    dd = math.sqrt(sum(r * r for r in downs) / len(rets)) if downs else 0.0
    dd = max(dd, floor)
    return (mean * TRADING_DAYS) / (dd * math.sqrt(TRADING_DAYS))


def score(
    result: ReplayResult,
    benchmark: list[tuple[date, Decimal]] | None = None,
) -> Metrics:
    m = Metrics()
    series = result.daily_assets
    m.trading_days = len(series)

    if m.trading_days >= 2:
        start, end = float(series[0][1]), float(series[-1][1])
        m.cumulative_return = (end / start - 1.0) if start > 0 else 0.0

        rets = _returns(series)
        m.sortino = _sortino(rets)
        m.losing_days = sum(1 for r in rets if r < 0)
        m.max_drawdown = _max_drawdown(series)

        years = m.trading_days / TRADING_DAYS
        if years > 0 and m.max_drawdown > 0:
            annual = (1.0 + m.cumulative_return) ** (1.0 / years) - 1.0
            m.calmar = annual / m.max_drawdown

    if result.daily_exposure:
        m.avg_exposure = sum(float(e) for _, e in result.daily_exposure) / len(result.daily_exposure)

    if m.trading_days:
        marked = {d for d, _ in series}
        m.coverage = len(result.declared_days & marked) / len(marked)

    start_assets = float(series[0][1]) if series else 1.0
    if start_assets > 0:
        m.turnover = float(result.turnover) / start_assets

    m.closed_trades = result.closed_trades
    m.closed_trades_material = result.closed_trades_material

    wins = [f for f in result.fills if f.realized_pnl != 0]
    if wins:
        m.win_rate = sum(1 for f in wins if f.realized_pnl > 0) / len(wins)

    if benchmark and len(benchmark) >= 2:
        b0, b1 = float(benchmark[0][1]), float(benchmark[-1][1])
        if b0 > 0:
            m.benchmark_return = b1 / b0 - 1.0
            m.excess_return = m.cumulative_return - m.benchmark_return

    _apply_gate(m)
    return m


def _apply_gate(m: Metrics) -> None:
    """참가 요건은 셋뿐이다. 투자비중은 요건이 아니라 표시 항목이다."""
    fails: list[str] = []
    if m.trading_days < GATE_MIN_DAYS:
        fails.append(f"운영 {m.trading_days}일 (필요 {GATE_MIN_DAYS}일)")
    if m.closed_trades_material < GATE_MIN_TRADES:
        fails.append(f"청산 거래 {m.closed_trades_material}건 (필요 {GATE_MIN_TRADES}건)")
    if m.coverage < GATE_MIN_COVERAGE:
        fails.append(f"제출률 {m.coverage:.0%} (필요 {GATE_MIN_COVERAGE:.0%})")
    m.gate_failures = fails
    m.qualified = not fails


def summary_lines(m: Metrics) -> list[str]:
    """사람이 읽는 요약. 하나의 숫자로 줄이지 않는다."""
    pct = lambda x: f"{x:+.2%}" if x is not None else "—"
    lines = [
        f"프로파일        {m.profile}",
        f"운영 거래일     {m.trading_days}일",
        f"누적 수익       {pct(m.cumulative_return)}",
        f"하락위험 대비   {m.sortino:.2f}",
        f"최대 낙폭       {m.max_drawdown:.2%}",
        f"낙폭 대비 수익  {m.calmar:.2f}",
        f"평균 투자비중   {m.avg_exposure:.1%}   ← 위 지표는 이 값과 함께 읽어야 한다",
        f"제출률          {m.coverage:.0%}",
        f"회전율          {m.turnover:.2f}회",
        f"청산 거래       {m.closed_trades_material}건 (전체 {m.closed_trades}건)",
    ]
    if m.win_rate is not None:
        lines.append(f"승률            {m.win_rate:.0%}")
    if m.excess_return is not None:
        lines.append(f"시장 대비       {pct(m.excess_return)}")
    lines.append(
        "자격            " + ("본선" if m.qualified else "예선 — " + ", ".join(m.gate_failures))
    )
    return lines
