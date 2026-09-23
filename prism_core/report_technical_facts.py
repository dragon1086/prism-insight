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
        "price_basis": frame.attrs.get("price_basis", "provider_close_basis_unspecified") if frame is not None else "unknown",
        "crosses": {}, "split_boundary_date": None,
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
    # On report-only raw prices do not compare averages across known splits.
    split_columns = [c for c in frame.columns if str(c).lower().replace(" ", "_") == "stock_splits"]
    if len(split_columns) == 1 and facts["price_basis"] == "provider_unadjusted_close":
        splits = pd.to_numeric(frame[split_columns[0]].sort_index(), errors="coerce").loc[:close.index[-1]]
        events = splits[splits.notna() & splits.ne(0)]
        if not events.empty:
            facts["split_boundary_date"] = events.index[-1].isoformat()
            tail = tail.loc[events.index[-1]:]
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
    for fast, slow in ((20, 50), (50, 200)):
        difference = tail.rolling(fast).mean() - tail.rolling(slow).mean()
        previous = difference.shift(1)
        for direction, events in (
            ("golden", (difference > 0) & (previous <= 0)),
            ("dead", (difference < 0) & (previous >= 0)),
        ):
            matches = difference.index[events]
            facts["crosses"][f"SMA{fast}_{slow}_{direction}"] = matches[-1].isoformat() if len(matches) else None
    facts["status"] = "complete" if not latest_missing and all(v is not None for v in values.values()) else "partial"
    facts["reason"] = (
        "latest_close_missing_or_invalid_historical_indicators_only" if latest_missing
        else "ok" if facts["status"] == "complete"
        else "insufficient_contiguous_history_or_undefined_rsi"
    )
    return facts


def render_public_technical_facts(frame, *, unit="USD", language="ko"):
    """Publish the same deterministic facts without internal packet diagnostics."""
    facts = build_report_technical_facts(frame)
    ko = language == "ko"
    lines = ["### 기술지표 계산 기준" if ko else "### Technical indicator reference"]
    lines.append("출처: yfinance 일별 시세(이 보고서와 같은 조회 자료)." if ko else
                 "Source: yfinance daily prices (the same snapshot used in this report).")
    date = facts["last_valid_close_date"]
    value = facts["last_valid_close"]
    if value is None:
        lines.append("계산에 사용할 종가 자료가 없어 기술지표를 제시하지 않습니다." if ko else
                     "No usable closing-price observations were available for indicator calculation.")
        return "\n\n".join(lines)
    # Full ISO dates retain timezone and prevent a dated historical observation
    # from silently becoming the current session's quote.
    lines.append((f"최근 확인된 일별 종가 관측값은 {date} 기준 {value:.4f} {unit}입니다. "
                  "아래 지표도 이 날짜까지의 자료로 계산했습니다.") if ko else
                 f"The last available daily closing-price observation is {value:.4f} {unit} as of {date}. "
                 "The indicators below use observations through this date.")
    if facts["latest_observed_close"] is None:
        lines.append((f"{facts['latest_row_date']} 자료에는 종가가 제공되지 않아 이전 자료를 사용했습니다. "
                      "위 가격은 최신 거래일의 종가를 대신하지 않습니다.") if ko else
                     f"The {facts['latest_row_date']} observation has no close, so these are historical indicators, "
                     "not a substitute for the latest session's price.")
    else:
        lines.append("관측값은 최종 마감 수치와 차이가 있을 수 있습니다." if ko else
                     "The observed price may differ from the final closing value.")
    if facts["price_basis"] == "provider_unadjusted_close":
        lines.append("가격 기준: 제공기관의 비조정 종가." if ko else "Price basis: provider unadjusted close.")
    else:
        lines.append("가격 조정 기준은 제공 자료에 명시되지 않았습니다." if ko else
                     "The source did not specify the price adjustment basis.")
    if facts["split_boundary_date"]:
        lines.append((f"주식분할 이후인 {facts['split_boundary_date']}부터의 연속 자료로 계산했습니다.") if ko else
                     f"Calculations use contiguous observations since the split on {facts['split_boundary_date']}.")
    labels = {
        "SMA10": "10일 단순이동평균", "SMA20": "20일 단순이동평균",
        "SMA50": "50일 단순이동평균", "SMA200": "200일 단순이동평균",
        "RSI14": "14일 RSI", "MACD": "MACD", "MACD_SIGNAL": "MACD 신호선",
        "MACD_HISTOGRAM": "MACD 히스토그램", "BB20_MIDDLE": "볼린저밴드 중심선",
        "BB20_UPPER": "볼린저밴드 상단", "BB20_LOWER": "볼린저밴드 하단",
    }
    english_labels = {"MACD_SIGNAL": "MACD signal", "MACD_HISTOGRAM": "MACD histogram",
                      "BB20_MIDDLE": "Bollinger middle", "BB20_UPPER": "Bollinger upper",
                      "BB20_LOWER": "Bollinger lower"}
    rows = []
    for name, number in facts["indicators"].items():
        if number is not None:
            label = labels[name] if ko else english_labels.get(name, name)
            suffix = "" if name == "RSI14" else f" {unit}"
            rows.append(f"| {label} | {number:.4f}{suffix} |")
    if rows:
        lines.append(("| 지표 | 값 |\n| --- | --- |\n" if ko else
                      "| Indicator | Value |\n| --- | --- |\n") + "\n".join(rows))
    if any(v is None for v in facts["indicators"].values()):
        lines.append("연속 관측 기간이 부족하거나 계산식이 정의되지 않는 지표는 생략했습니다." if ko else
                     "Indicators with insufficient contiguous history or undefined formulas are omitted.")
    for name, crossed in facts["crosses"].items():
        if crossed:
            fast, slow, direction = name.removeprefix("SMA").split("_")
            label = "상향 돌파" if direction == "golden" else "하향 돌파"
            lines.append(f"{fast}일·{slow}일 이동평균 {label}: {crossed}." if ko else
                         f"{fast}/{slow}-day moving averages crossed {'up' if direction == 'golden' else 'down'} on {crossed}.")
    lines.append(("계산 방법: 이동평균은 단순평균입니다. RSI는 최근 14개 가격 변화의 상승·하락폭 단순평균을 "
                  "사용하며 Wilder 방식이 아닙니다. MACD는 12·26일 지수이동평균 차이, 신호선은 9일 "
                  "지수이동평균(재귀 방식)입니다. 볼린저밴드는 20일 평균 ± 표본표준편차의 2배입니다. "
                  "중간 결측값은 채우지 않으며 지수이동평균은 결측 이후 다시 계산합니다. "
                  "교차일은 연속 관측 구간에서 두 평균의 차이가 0 이하에서 양수 또는 0 이상에서 음수로 바뀐 날입니다.") if ko else
                 "Definitions: SMA is the arithmetic mean. RSI uses simple averages of 14 price changes, not Wilder smoothing. "
                 "MACD is EMA12 minus EMA26; its signal is EMA9, using recursive weighting. Bollinger bands are the "
                 "20-day mean plus/minus two sample standard deviations. Interior gaps are not filled; EMA restarts after a gap. "
                 "Cross dates mark a change in the fast-minus-slow difference from nonpositive to positive or nonnegative to negative "
                 "within the contiguous observed window.")
    return "\n\n".join(lines)


def render_report_technical_facts(frame, *, unit="USD"):
    """Human-readable authoritative facts for price, strategy and summary agents."""
    facts = build_report_technical_facts(frame)

    def number(value):
        return "N/A" if value is None else f"{value:.4f}"

    lines = [
        "### AUTHORITATIVE TECHNICAL FACTS / 사전 계산 기술 지표",
        f"Source: {facts['source']}; status={facts['status']}; reason={facts['reason']}",
        f"Price basis: {facts['price_basis']}; known split boundary: {facts['split_boundary_date'] or 'none supplied'}",
        f"Latest row date: {facts['latest_row_date'] or 'N/A'}; finality={facts['finality']}",
        f"Latest observed Close ({unit}): {number(facts['latest_observed_close'])}",
        f"Last valid Close ({unit}): {number(facts['last_valid_close'])}; date={facts['last_valid_close_date'] or 'N/A'}",
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
    lines.append("Cross dates in the contiguous observed window (not estimated): golden = previous fast-minus-slow <= 0 then > 0; dead = previous >= 0 then < 0. N/A means no confirmed crossing in this window, not proof it never occurred.")
    lines.extend(f"{name}: {value or 'N/A'}" for name, value in facts["crosses"].items())
    lines.append("Use these exact values (rounding permitted) in all report sections. "
                 "N/A means unavailable: do not invent, approximate or recalculate it from a partial table. "
                 "Indicator calculations do not certify a final session close.\n")
    return TECHNICAL_FACTS_START + "\n" + "\n".join(lines) + TECHNICAL_FACTS_END + "\n"
