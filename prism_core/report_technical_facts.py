"""Deterministic report-only indicators from an already fetched daily OHLCV frame.

No provider calls, trading thresholds or final-session assumptions live here.
RSI and EMA definitions match prism-us/cores/us_stock_chart.py. Invalid closes
remain gaps, never silently becoming shorter 'trading day' windows.
"""

import math
from numbers import Real

import pandas as pd

TECHNICAL_FACTS_START = "<!-- REPORT_TECHNICAL_FACTS_START -->"
TECHNICAL_FACTS_END = "<!-- REPORT_TECHNICAL_FACTS_END -->"


def extract_report_technical_facts(text):
    """Extract only the canonical block, without the raw OHLCV table or markers."""
    if not text or TECHNICAL_FACTS_START not in text:
        return ""
    block = text.split(TECHNICAL_FACTS_START, 1)[1]
    if TECHNICAL_FACTS_END not in block:
        return ""
    return block.split(TECHNICAL_FACTS_END, 1)[0].strip()


INDICATORS = (
    "SMA10", "SMA20", "SMA50", "SMA200", "RSI14", "MACD", "MACD_SIGNAL",
    "MACD_HISTOGRAM", "BB20_MIDDLE", "BB20_UPPER", "BB20_LOWER",
)


def build_report_technical_facts(frame):
    """Return same-snapshot numeric facts; unavailable values remain None."""
    facts = {
        "source": "yfinance daily OHLCV (same fetched snapshot)",
        "status": "unavailable", "latest_row_date": None,
        "latest_observed_close": None, "last_valid_close": None,
        "last_valid_close_date": None, "indicator_asof_date": None,
        "finality": "BAR_FINALITY_UNKNOWN",
        "indicators": dict.fromkeys(INDICATORS), "reason": "no_usable_close_data",
    }
    if frame is None or frame.empty or isinstance(frame.columns, pd.MultiIndex):
        return facts
    columns = [c for c in frame.columns if str(c).lower() == "close"]
    if len(columns) != 1 or not isinstance(frame.index, pd.DatetimeIndex):
        return facts
    dates = frame.index
    if dates.hasnans or dates.normalize().has_duplicates:
        facts["reason"] = "ambiguous_dates"
        return facts
    close = frame[columns[0]].sort_index().map(
        lambda value: float(value) if isinstance(value, Real)
        and not isinstance(value, bool) and math.isfinite(value) and value > 0
        else float("nan")
    )
    facts["latest_row_date"] = close.index[-1].isoformat()
    valid = close.dropna()
    if not valid.empty:
        facts["last_valid_close"] = float(valid.iloc[-1])
        facts["last_valid_close_date"] = valid.index[-1].isoformat()
    if valid.empty:
        return facts
    latest_missing = pd.isna(close.iloc[-1])
    if not latest_missing:
        facts["latest_observed_close"] = float(close.iloc[-1])
    facts["indicator_asof_date"] = facts["last_valid_close_date"]
    # Trim only trailing invalid observations, explicitly retaining their newer
    # timestamp above. Historical calculations must not masquerade as current.
    close = close.loc[:valid.index[-1]]
    # Stateful indicators restart after a gap instead of carrying stale state.
    missing = close.isna().to_numpy().nonzero()[0]
    tail = close.iloc[missing[-1] + 1:] if len(missing) else close
    values = facts["indicators"]
    for period in (10, 20, 50, 200):
        if len(tail) >= period:
            values[f"SMA{period}"] = float(tail.iloc[-period:].mean())
    if len(tail) >= 15:
        delta = tail.diff().iloc[-14:]
        gain = float(delta.clip(lower=0).mean())
        loss = float(-delta.clip(upper=0).mean())
        # Flat prices have undefined 0/0 RSI; a real zero RSI is preserved.
        if loss > 0:
            values["RSI14"] = 100 - 100 / (1 + gain / loss)
        elif gain > 0:
            values["RSI14"] = 100.0
    if len(tail) >= 26:
        macd = tail.ewm(span=12, adjust=False).mean() - tail.ewm(span=26, adjust=False).mean()
        values["MACD"] = float(macd.iloc[-1])
        if len(tail) >= 34:
            signal = macd.ewm(span=9, adjust=False).mean()
            values["MACD_SIGNAL"] = float(signal.iloc[-1])
            values["MACD_HISTOGRAM"] = float((macd - signal).iloc[-1])
    if len(tail) >= 20:
        mean = values["SMA20"]
        sd = float(tail.iloc[-20:].std(ddof=1))
        values.update(BB20_MIDDLE=mean, BB20_UPPER=mean + 2 * sd, BB20_LOWER=mean - 2 * sd)
    facts["status"] = "complete" if not latest_missing and all(v is not None for v in values.values()) else "partial"
    facts["reason"] = (
        "latest_close_missing_or_invalid_historical_indicators_only" if latest_missing
        else "ok" if facts["status"] == "complete"
        else "insufficient_contiguous_history_or_undefined_rsi"
    )
    return facts


def render_report_technical_facts(frame):
    """Human-readable authoritative facts for price, strategy and summary agents."""
    facts = build_report_technical_facts(frame)

    def number(value):
        return "N/A" if value is None else f"{value:.4f}"

    lines = [
        "### AUTHORITATIVE TECHNICAL FACTS / 사전 계산 기술 지표",
        f"Source: {facts['source']}; status={facts['status']}; reason={facts['reason']}",
        f"Latest row date: {facts['latest_row_date'] or 'N/A'}; finality={facts['finality']}",
        f"Latest observed Close (USD): {number(facts['latest_observed_close'])}",
        f"Last valid Close (USD): {number(facts['last_valid_close'])}; date={facts['last_valid_close_date'] or 'N/A'}",
        f"계산 기준일 / Indicator as-of: {facts['indicator_asof_date'] or 'N/A'}",
        ("과거 지표 / Historical indicators only: latest Close unavailable; not current-session values."
         if facts['latest_observed_close'] is None and facts['indicator_asof_date']
         else "Indicator as-of does not attest final session close / 계산 기준일은 종가 확정 증거가 아닙니다."),
        "Last valid historical price is NOT a replacement for a missing latest price.",
        ("Definitions: SMA=arithmetic mean; RSI14=simple mean of 14 changes (not Wilder); "
         "MACD=EMA12-EMA26, signal=EMA9, adjust=False; Bollinger=SMA20 +/- 2 sample SD (ddof=1)."),
        ("Minimum contiguous closes: SMA/BB=window; RSI=15; MACD=26; signal/histogram=34. "
         "Only trailing invalid closes are trimmed for dated historical indicators; interior gaps are not filled or dropped. "
         "EMA restarts after a gap. Prices use the provider's Close basis."),
        "| Indicator | Value |", "| --- | --- |",
    ]
    lines.extend(f"| {name} | {number(value)} |" for name, value in facts["indicators"].items())
    lines.append("Use these exact values (rounding permitted) in all report sections. "
                 "N/A means unavailable: do not invent, approximate or recalculate it from a partial table. "
                 "Indicator calculations do not certify a final session close.\n")
    return TECHNICAL_FACTS_START + "\n" + "\n".join(lines) + TECHNICAL_FACTS_END + "\n"
