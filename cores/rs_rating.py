"""
O'Neil 다개월 가중 RS Rating — 공용 순수 모듈
=================================================
데이터소스 비의존. pandas만 사용. IO/로깅 없음.

IBD 근사식 (William J. O'Neil, CANSLIM):
  raw = 2*R63 + R126 + R189 + R252
  where R_n = (P0 - P_n) / P_n  (P0 = 최신 종가, P_n = n거래일 전 종가)

용도: KR/US 스크리닝 RS Score 계산 (Phase B SHADOW-gate).
     백테스트 근거: PR #436 (KR/US 모두 현행 60d 단일수익률 대비 우위 확인).
"""

from __future__ import annotations

import pandas as pd


def oneil_weighted_return(closes: pd.Series) -> float | None:
    """O'Neil 다개월 가중 수익률 (raw RS Rating 원재료).

    IBD 근사식: raw = 2*R63 + R126 + R189 + R252
    R_n = (P0 - P_n) / P_n  (거래일 수익률, P0=최신 종가, P_n=n거래일 전 종가)

    Parameters
    ----------
    closes : pd.Series
        일별 종가 시계열. 방어적으로 index 오름차순 정렬 후 사용.

    Returns
    -------
    float | None
        히스토리 부족(len(closes) <= 252)이면 None 반환. 근사 없음.
    """
    closes = closes.dropna().sort_index()
    if len(closes) <= 252:
        return None

    p0 = float(closes.iloc[-1])

    def _r(n: int) -> float:
        p_n = float(closes.iloc[-1 - n])
        return (p0 - p_n) / p_n if p_n > 0 else 0.0

    raw = 2.0 * _r(63) + _r(126) + _r(189) + _r(252)
    return float(raw)


def percentile_ratings(raw: dict[str, float]) -> dict[str, float]:
    """raw 딕셔너리 → 1~99 백분위 변환 (높을수록 강함).

    Parameters
    ----------
    raw : dict[str, float]
        ticker → oneil_weighted_return 값.

    Returns
    -------
    dict[str, float]
        ticker → 1~99 백분위. 항목 1개면 50.0 고정.
        동점 처리: 같은 raw 값은 같은 백분위를 받는다.
    """
    if not raw:
        return {}
    if len(raw) == 1:
        return {next(iter(raw)): 50.0}

    tickers = list(raw.keys())
    values = list(raw.values())
    n = len(values)
    result: dict[str, float] = {}
    for ticker, val in zip(tickers, values):
        rank = sum(1 for v in values if v <= val)
        pct = max(1.0, min(99.0, rank / n * 99.0))
        result[ticker] = float(pct)
    return result
