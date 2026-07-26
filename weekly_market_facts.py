#!/usr/bin/env python3
"""
Weekly Market Facts — 검증된 수치 근거 블록 생성

주간 인텔리전스 리포트의 숫자(지수 등락, 투자자별 순매수, 등락률 상위 종목)는
웹 검색 결과에서 뽑으면 안 된다. 블로그·커뮤니티 글이 검색 상위에 올라오면
LLM이 그 숫자를 그대로 인용해 사실과 다른 리포트가 나온다.

이 모듈은 KRX(pykrx) / yfinance에서 직접 원천 데이터를 가져와
LLM에게 "이 숫자만 써라"라고 전달할 수 있는 텍스트 블록을 만든다.

모든 조회는 fail-soft: 실패하면 해당 항목만 빠지고 리포트 생성은 계속된다.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from dotenv import load_dotenv

# KRX 조회는 KRX_ID/KRX_PW 자격증명을 요구한다. 이 모듈이 단독 실행되거나
# load_dotenv를 부르지 않은 경로에서 임포트돼도 동작하도록 여기서 로드한다.
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


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# 한국 시장
# ---------------------------------------------------------------------------
def _krx_fn(name: str):
    """
    Resolve a KRX data function.

    Production runs the `krx_data_client` wrapper (kospi-kosdaq-stock-server),
    which exposes pykrx-compatible signatures and Korean column names. Bare
    pykrx is the fallback for functions the wrapper doesn't re-export.
    Returns None if neither is usable — callers must treat that as "skip".
    """
    try:
        import krx_data_client

        fn = getattr(krx_data_client, name, None)
        if fn is not None:
            return fn
    except Exception:  # noqa: BLE001 - wrapper is server-only, absence is normal
        pass

    try:
        from pykrx import stock

        return getattr(stock, name, None)
    except Exception as e:  # noqa: BLE001 - pykrx import can fail (setuptools/pkg_resources)
        logger.warning(f"[facts] no KRX backend for {name}: {e}")
        return None


# krx_data_client returns English column names, bare pykrx returns Korean ones.
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


def _kr_index_block(start: date, end: date) -> list[str]:
    get_index_ohlcv_by_date = _krx_fn("get_index_ohlcv_by_date")
    if get_index_ohlcv_by_date is None:
        return []

    lines: list[str] = []
    for name, ticker in (("KOSPI", KOSPI_INDEX), ("KOSDAQ", KOSDAQ_INDEX)):
        try:
            df = get_index_ohlcv_by_date(_ymd(start), _ymd(end), ticker)
            if df is None or df.empty:
                continue
            c_open, c_close = _col(df, "open"), _col(df, "close")
            c_high, c_low = _col(df, "high"), _col(df, "low")
            if not (c_open and c_close):
                logger.warning(f"[facts] {name}: unexpected columns {list(df.columns)}")
                continue
            first_open = float(df.iloc[0][c_open])
            last_close = float(df.iloc[-1][c_close])
            week_high = float(df[c_high].max()) if c_high else last_close
            week_low = float(df[c_low].min()) if c_low else last_close
            pct = (last_close - first_open) / first_open * 100 if first_open else 0.0
            lines.append(
                f"- {name}: 주간 시가 {first_open:,.2f} → 금요일 종가 {last_close:,.2f} "
                f"({pct:+.2f}%), 주간 고가 {week_high:,.2f} / 저가 {week_low:,.2f}"
            )
            daily = " / ".join(
                f"{idx:%m-%d} {float(row[c_close]):,.2f}"
                for idx, row in df.iterrows()
            )
            lines.append(f"  · 일별 종가: {daily}")
        except Exception as e:  # noqa: BLE001 - fail-soft by design
            logger.warning(f"[facts] {name} index fetch failed: {e}")
    return lines


_NAVER_INVESTOR_URL = (
    "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate={bizdate}&sosok={sosok}"
)
_INVESTOR_LABELS = ("개인", "외국인", "기관계", "금융투자", "연기금등")


def _flatten_columns(df) -> list[str]:
    """read_html gives a 2-level header here; take the most specific label."""
    out: list[str] = []
    for col in df.columns:
        if isinstance(col, tuple):
            parts = [str(c) for c in col if str(c) and not str(c).startswith("Unnamed")]
            out.append(parts[-1] if parts else "")
        else:
            out.append(str(col))
    return out


def _naver_investor_daily(sosok: str, bizdate: date) -> dict[date, dict[str, float]]:
    """
    네이버 금융 '투자자별 매매동향' 일별 순매수 (단위: 억원).

    시장 전체 수급은 krx_data_client(개별종목 전용)로도, pykrx(KRX 응답 포맷
    변경으로 파싱 실패)로도 받을 수 없어 네이버를 1차 소스로 쓴다.
    페이지는 bizdate 기준 최근 약 15거래일을 담고 있다.
    """
    import io

    import pandas as pd
    import requests

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://finance.naver.com/",
    })
    url = _NAVER_INVESTOR_URL.format(bizdate=_ymd(bizdate), sosok=sosok)
    resp = session.get(url, timeout=15)
    resp.raise_for_status()

    tables = pd.read_html(io.StringIO(resp.text))
    table = next(
        (t for t in tables if t.shape[0] > 2 and t.shape[1] >= 5),
        None,
    )
    if table is None:
        raise ValueError("investor table not found")

    names = _flatten_columns(table)
    idx = {label: names.index(label) for label in _INVESTOR_LABELS if label in names}
    if "날짜" not in names or not idx:
        raise ValueError(f"unexpected investor columns: {names}")
    date_pos = names.index("날짜")

    result: dict[date, dict[str, float]] = {}
    for _, row in table.iterrows():
        raw_date = str(row.iloc[date_pos]).strip()
        # "26.07.24" 형식
        parts = raw_date.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            continue
        try:
            day = date(2000 + int(parts[0]), int(parts[1]), int(parts[2]))
        except ValueError:
            continue

        values: dict[str, float] = {}
        for label, pos in idx.items():
            val = row.iloc[pos]
            if pd.notna(val):
                values[label] = float(val)
        if values:
            result[day] = values
    return result


def _kr_investor_block(start: date, end: date) -> list[str]:
    lines: list[str] = []
    for market, sosok in (("KOSPI", "01"), ("KOSDAQ", "02")):
        try:
            daily = _naver_investor_daily(sosok, end)
            in_week = {d: v for d, v in daily.items() if start <= d <= end}
            if not in_week:
                logger.warning(f"[facts] {market} investor: no rows in {start}~{end}")
                continue

            totals: dict[str, float] = {}
            for row in in_week.values():
                for label, value in row.items():
                    totals[label] = totals.get(label, 0.0) + value

            order = ("외국인", "기관계", "개인", "금융투자", "연기금등")
            parts = [
                f"{label} {totals[label]:+,.0f}억원"
                for label in order
                if label in totals
            ]
            if parts:
                lines.append(
                    f"- {market} 투자자별 주간 누적 순매수({len(in_week)}거래일): "
                    + ", ".join(parts)
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[facts] {market} investor fetch failed: {e}")
    return lines


def _kr_movers_block(start: date, end: date, top_n: int = 7) -> list[str]:
    """
    주간 등락률 상위 종목.

    krx_data_client에는 get_market_price_change_by_ticker가 없으므로
    주 시작/종료 시점의 전종목 종가를 받아 직접 계산한다.
    """
    get_ohlcv_by_ticker = _krx_fn("get_market_ohlcv_by_ticker")
    get_ticker_list = _krx_fn("get_market_ticker_list")
    get_ticker_name = _krx_fn("get_market_ticker_name")
    if get_ohlcv_by_ticker is None:
        return []

    try:
        first = get_ohlcv_by_ticker(_ymd(start))
        last = get_ohlcv_by_ticker(_ymd(end))
        if first is None or last is None or first.empty or last.empty:
            return []

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
        return []

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
            lines.append(f"- {market} 주간 상승률 상위(거래대금 상위 300 내): {names}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[facts] {market} movers ranking failed: {e}")
    return lines


def build_kr_facts(start: date, end: date) -> str:
    """한국 시장 검증 수치 블록. 실패 시 빈 문자열."""
    index_lines = _kr_index_block(start, end)
    investor_lines = _kr_investor_block(start, end)
    movers_lines = _kr_movers_block(start, end)
    lines = index_lines + investor_lines + movers_lines

    if not lines:
        logger.warning("[facts] KR facts empty — KRX 조회 전부 실패")
        return ""

    header = (
        f"[검증된 시장 데이터 — KRX 원천 조회, {start:%Y-%m-%d} ~ {end:%Y-%m-%d}]\n"
        "아래 수치는 한국거래소 데이터에서 직접 조회한 확정값이다.\n"
        "지수 레벨·등락률·종목 등락률은 반드시 아래 값만 사용하고, "
        "웹 검색 결과에 다른 숫자가 있어도 무시하라.\n"
    )
    if not investor_lines:
        # 시장 전체 수급은 krx_data_client(개별종목 전용)로는 못 받는다.
        # 없는 값을 지어내는 대신 서술 방식을 제한한다.
        header += (
            "단, 외국인·기관 순매수 금액은 확정 데이터를 확보하지 못했다. "
            "수급은 1차 매체(연합뉴스·인포맥스 등) 기사에 명시된 수치만 인용하고, "
            "그런 수치가 없으면 금액 없이 매수/매도 방향성만 서술하라.\n"
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


def build_us_facts(start: date, end: date) -> str:
    """미국 시장 검증 수치 블록. 실패 시 빈 문자열."""
    try:
        import yfinance as yf
    except ImportError:
        logger.warning("[facts] yfinance not installed — US facts skipped")
        return ""

    lines: list[str] = []
    # 미국장은 한국 대비 하루 늦게 마감되므로 여유를 둔다
    fetch_start = start.strftime("%Y-%m-%d")
    fetch_end = (end + timedelta(days=2)).strftime("%Y-%m-%d")

    for name, ticker in _US_TICKERS:
        try:
            hist = yf.Ticker(ticker).history(start=fetch_start, end=fetch_end)
            if hist is None or hist.empty:
                continue
            first_open = float(hist.iloc[0]["Open"])
            last_close = float(hist.iloc[-1]["Close"])
            pct = (last_close - first_open) / first_open * 100 if first_open else 0.0
            last_day = hist.index[-1].strftime("%m-%d")
            if ticker == "^TNX":
                lines.append(f"- {name}: {last_close:.3f}% ({last_day} 기준, 주간 {pct:+.2f}%)")
            else:
                lines.append(
                    f"- {name}: 주간 시가 {first_open:,.2f} → {last_day} 종가 "
                    f"{last_close:,.2f} ({pct:+.2f}%)"
                )
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[facts] {name} fetch failed: {e}")

    if not lines:
        logger.warning("[facts] US facts empty — yfinance 조회 전부 실패")
        return ""

    header = (
        f"[검증된 시장 데이터 — yfinance 원천 조회, {start:%Y-%m-%d} ~ {end:%Y-%m-%d}]\n"
        "아래 수치는 시장 데이터에서 직접 조회한 확정값이다.\n"
        "지수 레벨·등락률은 반드시 아래 값만 사용하고, 웹 검색 결과의 다른 숫자는 무시하라.\n"
    )
    return header + "\n".join(lines)


def diagnose() -> None:
    """
    Print which KRX backend resolves for each function and what the fact blocks
    look like. Run this on the server after deploy:

        python weekly_market_facts.py

    If the investor line is missing, `krx_data_client` doesn't re-export
    get_market_trading_value_by_investor and bare pykrx couldn't reach KRX —
    the flow numbers will be absent from the report (by design, rather than
    hallucinated from a blog post).
    """
    s, e = resolve_week_range()
    print(f"week range: {s} ~ {e}\n")

    print("[backend resolution]")
    for name in (
        "get_index_ohlcv_by_date",
        "get_market_trading_value_by_investor",
        "get_market_price_change_by_ticker",
    ):
        fn = _krx_fn(name)
        origin = getattr(fn, "__module__", "?") if fn else "UNRESOLVED"
        print(f"  {name}: {origin}")
    print()

    print(build_kr_facts(s, e) or "(KR facts unavailable)")
    print()
    print(build_us_facts(s, e) or "(US facts unavailable)")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    diagnose()
