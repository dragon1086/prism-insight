"""Today's strong US industries for the US signal alert (quick version before roadmap U1~U4).

KR shows "오늘 돈이 몰린 테마" from its own news store and theme map (prism_core/kr_theme_brief.py). US has
neither yet, so this block uses industry ETFs: their change versus the previous close at alert time, from one
yfinance download. Deterministic, no model call; any failure returns "" and the alert goes out without it.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

TOP = 3
MIN_RISE = 0.1   # % — a flat ETF is not a strong industry
ETFS = {
    "SMH": ("반도체", "Semiconductors"),
    "IGV": ("소프트웨어", "Software"),
    "SKYY": ("클라우드", "Cloud"),
    "CIBR": ("사이버보안", "Cybersecurity"),
    "BOTZ": ("로봇·AI", "Robotics & AI"),
    "XBI": ("바이오", "Biotech"),
    "IHI": ("의료기기", "Medical devices"),
    "ITA": ("항공·방산", "Aerospace & defense"),
    "URA": ("우라늄·원전", "Uranium & nuclear"),
    "TAN": ("태양광", "Solar"),
    "LIT": ("리튬·배터리", "Lithium & batteries"),
    "XLE": ("에너지", "Energy"),
    "XOP": ("석유·가스 개발", "Oil & gas E&P"),
    "GDX": ("금광", "Gold miners"),
    "COPX": ("구리", "Copper miners"),
    "XME": ("금속·광업", "Metals & mining"),
    "KRE": ("지역은행", "Regional banks"),
    "KBE": ("은행", "Banks"),
    "IAI": ("증권·거래소", "Brokers & exchanges"),
    "XHB": ("주택건설", "Homebuilders"),
    "XRT": ("소매", "Retail"),
    "IBUY": ("온라인 커머스", "E-commerce"),
    "IYT": ("운송", "Transportation"),
    "JETS": ("항공사", "Airlines"),
    "XLU": ("유틸리티", "Utilities"),
    "XLRE": ("리츠", "REITs"),
    "XLP": ("필수소비재", "Consumer staples"),
    "XLC": ("통신·미디어", "Communication services"),
}


def _download(tickers):
    import yfinance as yf
    return yf.download(tickers, period="5d", interval="1d", auto_adjust=False, actions=False,
                       progress=False, threads=False, timeout=15, group_by="ticker")


def etf_changes(trade_date: str, download=_download) -> list[tuple[str, float]]:
    """[(etf, % change vs previous close)] for ETFs whose latest bar is trade_date (YYYYMMDD, New York)."""
    day = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
    data = download(list(ETFS))
    out = []
    for etf in ETFS:
        try:
            closes = data[etf]["Close"].dropna()
        except (KeyError, TypeError):
            continue
        if len(closes) < 2 or str(closes.index[-1])[:10] != day:
            continue
        prev, now = float(closes.iloc[-2]), float(closes.iloc[-1])
        if prev > 0:
            out.append((etf, (now / prev - 1) * 100))
    return sorted(out, key=lambda row: -row[1])


def render(changes: list[tuple[str, float]], language: str = "ko") -> str:
    rising = [(etf, pct) for etf, pct in changes if pct >= MIN_RISE][:TOP]
    if not rising:
        return ""
    ko = language == "ko"
    parts = [f"{i}) {ETFS[etf][0 if ko else 1]}({etf}) {pct:+.1f}%" for i, (etf, pct) in enumerate(rising, 1)]
    if ko:
        return ("🧭 오늘 강한 업종 (업종 ETF 등락, 알림 시점·전일 종가 대비)\n" + " · ".join(parts) +
                "\n※ 업종 ETF 기준이라 아래 개별 종목의 테마와 다를 수 있습니다.\n\n")
    return ("🧭 Strong industries today (industry ETFs vs previous close, at alert time)\n" + " · ".join(parts) +
            "\n※ Based on industry ETFs; individual picks below may belong to other themes.\n\n")


def sector_brief(trade_date: str, language: str = "ko", download=_download) -> str:
    """Rendered block, or "" when data is unavailable."""
    try:
        changes = etf_changes(trade_date, download)
        logger.info("[US_SECTOR_BRIEF] date=%s etfs=%d top=%s", trade_date, len(changes),
                    ",".join(f"{etf}{pct:+.1f}" for etf, pct in changes[:TOP]))
        return render(changes, language)
    except Exception as error:  # noqa: BLE001 - optional alert context, never blocks the alert
        logger.warning("[US_SECTOR_BRIEF] skipped (%s)", type(error).__name__)
        return ""
