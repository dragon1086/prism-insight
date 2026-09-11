#!/usr/bin/env python3
"""
Weekly Market Facts — 검증된 수치 근거 블록 생성

주간 인텔리전스 리포트의 숫자(지수 등락, 투자자별 순매수, 등락률 상위 종목)는
웹 검색 결과에서 뽑으면 안 된다. 블로그·커뮤니티 글이 검색 상위에 올라오면
LLM이 그 숫자를 그대로 인용해 사실과 다른 리포트가 나온다.

이 모듈은 KIS / yfinance에서 직접 원천 데이터를 가져와
LLM에게 "이 숫자만 써라"라고 전달할 수 있는 텍스트 블록을 만든다.

모든 조회는 fail-soft: 실패하면 해당 항목만 빠지고 리포트 생성은 계속된다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

# Load KIS credentials for standalone read-only fact collection.
load_dotenv()

logger = logging.getLogger(__name__)

KOSPI_INDEX = "1001"
KOSDAQ_INDEX = "2001"


# ---------------------------------------------------------------------------
# 주간 구간 계산
# ---------------------------------------------------------------------------
def resolve_week_range(today: Optional[date] = None) -> tuple[date, date]:
    """
    리포트가 다루는 '이번 주' 거래 구간(직전 월~금)을 반환한다.

    일요일 11:00에 도는 배치이므로 today 기준 가장 최근 금요일과 그 주 월요일.
    금요일에 돌리면 당일이 구간 끝이 된다.
    """
    today = today or datetime.now().date()
    days_since_friday = (today.weekday() - 4) % 7  # 금=4
    friday = today - timedelta(days=days_since_friday)
    monday = friday - timedelta(days=4)
    return monday, friday


def resolve_recent_sessions(
    today: Optional[date] = None, lookback_days: int = 14
) -> Optional[tuple[date, date]]:
    """
    (직전 거래일, 최근 거래일)을 KIS 지수 관측일에서 직접 읽어 반환한다.

    주말·공휴일을 달력 계산으로 흉내내지 않는다. KOSPI 지수가 값을 가진 날이
    곧 거래일이므로, 그 인덱스의 마지막 두 행이 정답이다. 임시휴장·조기폐장도
    자동으로 따라간다.

    일간 팩트에 이 두 날짜가 모두 필요한 이유는 _kr_movers_block 이
    close(직전 거래일) → close(최근 거래일) 로 등락률을 계산하기 때문이다.
    한 날짜만 넘기면 등락률이 전부 0% 로 나온다.

    Returns:
        (baseline, latest) 또는 조회 실패/거래일 부족 시 None.
    """
    get_index_ohlcv_by_date = _market_data_fn("get_index_ohlcv_by_date")
    if get_index_ohlcv_by_date is None:
        return None

    today = today or datetime.now().date()
    try:
        df = get_index_ohlcv_by_date(
            _ymd(today - timedelta(days=lookback_days)), _ymd(today), KOSPI_INDEX
        )
        if df is None or len(df) < 2:
            logger.warning("[facts] session calendar: fewer than 2 sessions in window")
            return None
        sessions = [idx.date() if hasattr(idx, "date") else idx for idx in df.index[-2:]]
        return sessions[0], sessions[1]
    except Exception as e:  # noqa: BLE001 - fail-soft by design
        logger.warning(f"[facts] session calendar lookup failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 기간 라벨
# ---------------------------------------------------------------------------
# 같은 숫자라도 문구가 틀리면 LLM 이 그 문구를 그대로 인용한다. 일간 종가를
# "금요일 종가" 로 넘기면 리포트에 금요일이라고 적힌다. 그래서 산문을 기간
# 종류에서 분리한다.
_PERIOD_LABELS = {
    "weekly": {
        "open": "주간 시가", "close": "금요일 종가",
        "high": "주간 고가", "low": "저가",
        "daily_closes": "일별 종가",
        "flow": "주간 누적 순매수", "movers": "주간 상승률 상위",
        "span": "주간",
        "ref": "전주 종가",  # baseline 경로 전용 (주간은 사용하지 않음)
    },
    "daily": {
        "open": "시가", "close": "종가",
        "high": "고가", "low": "저가",
        "daily_closes": "종가",
        "flow": "당일 순매수", "movers": "당일 상승률 상위",
        "span": "당일",
        "ref": "전일 종가",
    },
}


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 한국 시장
# ---------------------------------------------------------------------------
def _market_data_fn(name: str):
    """Resolve only the repository KIS provider; unsupported fields stay unknown."""
    from cores import market_data

    return getattr(market_data, name, None)


# Accept the supported provider's English and Korean column variants.
# Resolve by intent instead of hardcoding either convention.
_COLUMN_ALIASES = {
    "open": ("시가", "Open"),
    "high": ("고가", "High"),
    "low": ("저가", "Low"),
    "close": ("종가", "Close"),
    "volume": ("거래량", "Volume"),
    "amount": ("거래대금", "Amount"),
}


def _col(df, key: str) -> Optional[str]:
    for candidate in _COLUMN_ALIASES[key]:
        if candidate in df.columns:
            return candidate
    return None


def _kr_index_block(
    start: date, end: date, labels: dict, *, baseline: Optional[date] = None
) -> list[str]:
    """
    지수 블록.

    baseline 이 없으면(주간) 구간 첫 시가 → 마지막 종가로 구간 수익률을 낸다.

    baseline 이 있으면(일간) **전일 종가 대비** 등락률을 낸다. 시가 대비가
    아니다 — 한국 시장에서 "등락률"은 전일 종가 기준이고, 언론·HTS 가 모두
    그 숫자를 쓴다. 시가 대비(장중 변동)를 확정값이라며 넘기면 기사 수치와
    어긋나 오히려 근거를 오염시킨다.
    실측 2026-07-31 KOSPI: 시가 대비 +16.57% vs 전일 종가 대비 +17.91%.
    """
    get_index_ohlcv_by_date = _market_data_fn("get_index_ohlcv_by_date")
    if get_index_ohlcv_by_date is None:
        return []

    fetch_from = baseline or start
    lines: list[str] = []
    for name, ticker in (("KOSPI", KOSPI_INDEX), ("KOSDAQ", KOSDAQ_INDEX)):
        try:
            df = get_index_ohlcv_by_date(_ymd(fetch_from), _ymd(end), ticker)
            if df is None or df.empty:
                continue
            c_open, c_close = _col(df, "open"), _col(df, "close")
            c_high, c_low = _col(df, "high"), _col(df, "low")
            if not (c_open and c_close):
                logger.warning(f"[facts] {name}: unexpected columns {list(df.columns)}")
                continue

            if baseline is not None:
                if len(df) < 2:
                    logger.warning(
                        f"[facts] {name}: need a prior session for the daily change, "
                        f"got {len(df)} row(s) for {fetch_from}~{end}"
                    )
                    continue
                # 마지막 행이 당일, 그 앞이 직전 거래일.
                session = df.iloc[-1:]
                ref = float(df.iloc[-2][c_close])
                ref_label = labels["ref"]
            else:
                session = df
                ref = float(df.iloc[0][c_open])
                ref_label = labels["open"]

            last_close = float(session.iloc[-1][c_close])
            week_high = float(session[c_high].max()) if c_high else last_close
            week_low = float(session[c_low].min()) if c_low else last_close
            pct = (last_close - ref) / ref * 100 if ref else 0.0
            lines.append(
                f"- {name}: {ref_label} {ref:,.2f} → "
                f"{labels['close']} {last_close:,.2f} "
                f"({pct:+.2f}%), {labels['high']} {week_high:,.2f} / "
                f"{labels['low']} {week_low:,.2f}"
            )
            daily = " / ".join(
                f"{idx:%m-%d} {float(row[c_close]):,.2f}"
                for idx, row in session.iterrows()
            )
            lines.append(f"  · {labels['daily_closes']}: {daily}")
        except Exception as e:  # noqa: BLE001 - fail-soft by design
            logger.warning(f"[facts] {name} index fetch failed: {e}")
    return lines


def _kr_investor_block(start: date, end: date, labels: dict) -> list[str]:
    """Do not replace unavailable KIS market-wide flow data with another provider."""
    return ["- 시장 전체 투자자 수급: UNKNOWN (KIS 시장별 투자자 수급 연동 미지원)"]


def _kr_movers_block(
    baseline: date, end: date, labels: dict, top_n: int = 7
) -> list[str]:
    """
    등락률 상위 종목.

    baseline/end 두 시점의 KIS 스냅샷이 확보된 경우만 직접 계산한다.
    현재 스냅샷을 과거 날짜 데이터로 대체하지 않는다.

    ⚠️ baseline 은 end 와 **다른 거래일**이어야 한다. 같은 날을 넘기면
    close(x)/close(x)-1 이라 전 종목이 +0.0% 로 나온다 — 예외가 아니라
    조용히 틀린 값이 리포트에 실린다. 일간 팩트는 resolve_recent_sessions()
    가 돌려주는 직전 거래일을 baseline 으로 쓴다.
    """
    get_ohlcv_by_ticker = _market_data_fn("get_market_ohlcv_by_ticker")
    get_ticker_list = _market_data_fn("get_market_ticker_list")
    get_ticker_name = _market_data_fn("get_market_ticker_name")
    if get_ohlcv_by_ticker is None:
        return ["- 종목 등락률: UNKNOWN (KIS 비교 시점 스냅샷 미확보)"]

    if baseline == end:
        logger.warning(
            f"[facts] movers skipped: baseline == end ({end}); "
            "every stock would print +0.0%"
        )
        return []

    try:
        first = get_ohlcv_by_ticker(_ymd(baseline))
        last = get_ohlcv_by_ticker(_ymd(end))
        if first is None or last is None or first.empty or last.empty:
            return ["- 종목 등락률: UNKNOWN (KIS 비교 시점 스냅샷 미확보)"]

        c_close_f, c_close_l = _col(first, "close"), _col(last, "close")
        c_amount = _col(last, "amount")
        if not (c_close_f and c_close_l and c_amount):
            logger.warning(f"[facts] movers: unexpected columns {list(last.columns)}")
            return []

        # 거래대금 상위 300종목으로 한정해 품절주·동전주 노이즈 제거
        universe = last.nlargest(300, c_amount)
        joined = universe.join(first[[c_close_f]], rsuffix="_start", how="inner")
        start_col = f"{c_close_f}_start" if c_close_f == c_close_l else c_close_f

        pct = (joined[c_close_l] / joined[start_col] - 1.0) * 100
        pct = pct[pct.notna()]
        if pct.empty:
            return []
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[facts] movers fetch failed: {e}")
        return ["- 종목 등락률: UNKNOWN (KIS 비교 시점 스냅샷 미확보)"]

    lines: list[str] = []
    for market in ("KOSPI", "KOSDAQ"):
        try:
            subset = pct
            if get_ticker_list is not None:
                tickers = set(get_ticker_list(_ymd(end), market=market) or [])
                subset = pct[pct.index.isin(tickers)]
            if subset.empty:
                continue
            top = subset.nlargest(top_n)

            def label(tk: str) -> str:
                if get_ticker_name is None:
                    return tk
                try:
                    return get_ticker_name(tk) or tk
                except Exception:  # noqa: BLE001
                    return tk

            names = ", ".join(f"{label(tk)} {v:+.1f}%" for tk, v in top.items())
            lines.append(
                f"- {market} {labels['movers']}(거래대금 상위 300 내): {names}"
            )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[facts] {market} movers ranking failed: {e}")
    return lines


def build_kr_facts(
    start: date,
    end: date,
    *,
    kind: str = "weekly",
    baseline: Optional[date] = None,
    include_movers: bool = True,
) -> str:
    """
    한국 시장 검증 수치 블록. 실패 시 빈 문자열.

    Args:
        start, end: 집계 구간. index(시가→종가)와 investor(순매수 합산)가 쓴다.
                    일간이면 start == end == 최근 거래일.
        kind: "weekly" | "daily". 숫자가 아니라 **문구**를 고른다. 일간 종가를
              주간 문구로 넘기면 LLM 이 "금요일 종가"라고 받아쓴다.
        baseline: movers 등락률의 기준 시점. 생략하면 start.
                  주간은 start(월요일)가 곧 기준이라 기존 동작과 동일하고,
                  일간은 직전 거래일을 넘겨야 한다(같은 날이면 전부 0%).
        include_movers: 등락률 상위 블록 포함 여부.

            대화형 호출은 include_movers=False로 전체 시장 조회를 생략한다.
            KIS의 과거 시점 스냅샷이 없으면 등락률은 UNKNOWN으로 유지한다.
    """
    if kind not in _PERIOD_LABELS:
        raise ValueError(f"unknown kind {kind!r}; expected 'weekly' or 'daily'")
    labels = _PERIOD_LABELS[kind]
    baseline = baseline or start

    # 주간은 구간 시가 기준, 일간은 전일 종가 기준. 주간 경로에 baseline 을
    # 넘기면 살아 있는 일요일 리포트의 숫자가 바뀌므로 daily 에서만 넘긴다.
    index_lines = _kr_index_block(
        start, end, labels, baseline=baseline if kind == "daily" else None
    )
    investor_lines = _kr_investor_block(start, end, labels)
    movers_lines = (
        _kr_movers_block(baseline, end, labels) if include_movers else []
    )
    lines = index_lines + investor_lines + movers_lines

    if not lines:
        logger.warning("[facts] KR facts empty — market data unavailable")
        return ""

    period_text = (
        f"{end:%Y-%m-%d}" if start == end
        else f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
    )
    header = (
        f"[검증된 시장 데이터 — KIS 조회, {period_text}]\n"
        "아래 수치는 명시된 제공자에서 조회한 관측값이며, 당일 값의 종가 확정 여부는 별도 확인해야 한다.\n"
        "지수 레벨·등락률·종목 등락률은 반드시 아래 값만 사용하고, "
        "웹 검색 결과에 다른 숫자가 있어도 무시하라.\n"
    )
    if not investor_lines or any("UNKNOWN" in line for line in investor_lines):
        # 시장 전체 수급을 확보하지 못했으면 추정 숫자를 생성하지 않는다.
        # 없는 값을 지어내는 대신 서술 방식을 제한한다.
        header += (
            "단, 외국인·기관 순매수 금액은 확정 데이터를 확보하지 못했다. "
            "이 블록의 수급을 0 또는 매수/매도 방향으로 추정하지 말고 미확인으로 유지하라.\n"
        )
    return header + "\n".join(lines)


# ---------------------------------------------------------------------------
# 미국 시장
# ---------------------------------------------------------------------------
_US_TICKERS = (
    ("S&P 500", "^GSPC"),
    ("NASDAQ 종합", "^IXIC"),
    ("다우존스", "^DJI"),
    ("러셀2000", "^RUT"),
    ("VIX", "^VIX"),
    ("미국 10년물 금리", "^TNX"),
)


def resolve_recent_us_sessions(
    today: Optional[date] = None, lookback_days: int = 14
) -> Optional[tuple[date, date]]:
    """
    (직전 거래일, 최근 거래일) — S&P 500 캘린더 기준.

    KR 과 같은 이유로 달력 계산 대신 지수 인덱스를 신뢰한다. 미국 공휴일은
    한국과 겹치지 않으므로 시장별로 따로 풀어야 한다.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[facts] yfinance not installed — US session lookup skipped")
        return None

    today = today or datetime.now().date()
    try:
        hist = yf.Ticker("^GSPC").history(
            start=(today - timedelta(days=lookback_days)).strftime("%Y-%m-%d"),
            end=(today + timedelta(days=2)).strftime("%Y-%m-%d"),
        )
        if hist is None or len(hist) < 2:
            logger.warning("[facts] US session calendar: fewer than 2 sessions")
            return None
        return hist.index[-2].date(), hist.index[-1].date()
    except Exception as e:  # noqa: BLE001 - fail-soft by design
        logger.warning(f"[facts] US session calendar lookup failed: {e}")
        return None


def build_us_facts(
    start: date, end: date, *, kind: str = "weekly", baseline: Optional[date] = None
) -> str:
    """
    미국 시장 검증 수치 블록. 실패 시 빈 문자열.

    KR 과 달리 movers 블록이 없어 종목 등락률용 baseline 은 필요 없지만, 지수
    등락률의 기준점 문제는 동일하다. baseline 을 주면 전일 종가 대비로 낸다.
    """
    if kind not in _PERIOD_LABELS:
        raise ValueError(f"unknown kind {kind!r}; expected 'weekly' or 'daily'")
    labels = _PERIOD_LABELS[kind]
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[facts] yfinance not installed — US facts skipped")
        return ""

    lines: list[str] = []
    # 미국장은 한국 대비 하루 늦게 마감되므로 여유를 둔다
    fetch_start = (baseline or start).strftime("%Y-%m-%d")
    fetch_end = (end + timedelta(days=2)).strftime("%Y-%m-%d")

    for name, ticker in _US_TICKERS:
        try:
            hist = yf.Ticker(ticker).history(start=fetch_start, end=fetch_end)
            if hist is None or hist.empty:
                continue

            if baseline is not None:
                if len(hist) < 2:
                    logger.warning(
                        f"[facts] {name}: need a prior session for the daily change, "
                        f"got {len(hist)} row(s)"
                    )
                    continue
                ref = float(hist.iloc[-2]["Close"])
                ref_label = labels["ref"]
            else:
                ref = float(hist.iloc[0]["Open"])
                ref_label = labels["open"]

            last_close = float(hist.iloc[-1]["Close"])
            pct = (last_close - ref) / ref * 100 if ref else 0.0
            last_day = hist.index[-1].strftime("%m-%d")
            if ticker == "^TNX":
                lines.append(
                    f"- {name}: {last_close:.3f}% "
                    f"({last_day} 기준, {labels['span']} {pct:+.2f}%)"
                )
            else:
                lines.append(
                    f"- {name}: {ref_label} {ref:,.2f} → {last_day} 종가 "
                    f"{last_close:,.2f} ({pct:+.2f}%)"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[facts] {name} fetch failed: {e}")

    if not lines:
        logger.warning("[facts] US facts empty — yfinance 조회 전부 실패")
        return ""

    period_text = (
        f"{end:%Y-%m-%d}" if start == end
        else f"{start:%Y-%m-%d} ~ {end:%Y-%m-%d}"
    )
    header = (
        f"[검증된 시장 데이터 — yfinance 원천 조회, {period_text}]\n"
        "아래 수치는 시장 데이터에서 직접 조회한 확정값이다.\n"
        "지수 레벨·등락률은 반드시 아래 값만 사용하고, 웹 검색 결과의 다른 숫자는 무시하라.\n"
    )
    return header + "\n".join(lines)


def diagnose() -> None:
    """Print KIS capability resolution and fail-soft fact blocks."""
    s, e = resolve_week_range()
    print(f"week range: {s} ~ {e}\n")

    print("[backend resolution]")
    for name in (
        "get_index_ohlcv_by_date",
        "get_market_trading_value_by_investor",
        "get_market_price_change_by_ticker",
    ):
        fn = _market_data_fn(name)
        origin = getattr(fn, "__module__", "?") if fn else "UNRESOLVED"
        print(f"  {name}: {origin}")
    print()

    print(build_kr_facts(s, e) or "(KR facts unavailable)")
    print()
    print(build_us_facts(s, e) or "(US facts unavailable)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    diagnose()
