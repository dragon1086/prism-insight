"""Deterministic competitor comparison for US reports.

Report-only optional data: one structured Perplexity call proposes candidate
tickers, yfinance validates them and supplies every number. No other LLM call,
never a trading gate. Any failure returns a skipped packet.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import re
import statistics
import time
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

import aiohttp

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
PERPLEXITY_MODEL = "sonar"
_PERPLEXITY_TIMEOUT = 20
_TICKER_TIMEOUT = 12
_BUDGET_SECONDS = 40
_CONCURRENCY = 3
_MAX_PEERS = 4
_MAX_CANDIDATES = 6
_MIN_PEER_CAP_RATIO = 0.1   # same size rule as KR: peers below 10% of the target are not comparable
_MAX_FALLBACK_CAP_RATIO = 10.0
_MISALIGNED_DAYS = 60
# yfinance exchange codes for US venues (Nasdaq tiers, NYSE, NYSE American/Arca, Cboe BZX).
US_EXCHANGES = frozenset({"NMS", "NGM", "NCM", "NYQ", "ASE", "PCX", "BTS"})
_TICKER = re.compile(r"[A-Z]{1,5}(?:[.-][A-Z]{1,2})?")

# field, public label, display digits, scale applied before display
_TABLE_ROWS = (
    ("market_cap", "시가총액($B)", 1, 1e-9),
    ("revenue_ttm", "TTM 매출($B)", 2, 1e-9),
    ("growth", None, 1, 100),  # label depends on the growth basis
    ("gross_margin", "매출총이익률(%)", 1, 100),
    ("op_margin", "영업이익률(%)", 1, 100),
    ("fcf_margin", "FCF 마진(%)", 1, 100),
    ("ev_sales", "EV/Sales(배)", 1, 1),
    ("forward_pe", "Forward PER(배)", 1, 1),
    ("ret_3m", "3개월 수익률(%)", 1, 100),
    ("ret_12m", "12개월 수익률(%)", 1, 100),
)
_GROWTH_LABELS = {"ttm_yoy": "TTM YoY", "quarter_yoy": "최근 분기 YoY"}


def _skip(reason, **extra):
    return {"ready": False, "peers": [], "public_markdown": "", "model_context": "",
            "source": "", "skip_reason": reason, **extra}


def _finite(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _half_up(value, digits):
    # Half-up on the decimal text, not binary float rounding (22.45 -> 22.5).
    return float(Decimal(repr(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _display_name(info, ticker):
    name = str(info.get("shortName") or info.get("longName") or ticker).strip()
    name = re.sub(r",?\s+(?:Inc\.?|Corporation|Corp\.?|Incorporated|Holdings,? Inc\.?|Ltd\.?|plc|N\.V\.|Co\.?)$",
                  "", name, flags=re.IGNORECASE).strip(" ,")
    return (name or ticker).replace("|", "/")[:40]


# --------------------------------------------------------------------------- selection

def parse_candidates(content, target):
    """Perplexity JSON text -> [{'ticker','name','overlap'}]; malformed rows are dropped."""
    if not isinstance(content, str):
        return []
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
        if not match:
            return []
        try:
            data = json.loads(match.group(1))
        except ValueError:
            return []
    if isinstance(data, dict):
        data = data.get("peers") or data.get("competitors") or []
    if not isinstance(data, list):
        return []
    result, seen = [], {str(target).upper()}
    for row in data:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper().replace("/", ".")
        if not _TICKER.fullmatch(ticker) or ticker in seen:
            continue
        seen.add(ticker)
        result.append({"ticker": ticker, "name": str(row.get("name") or "").strip()[:80],
                       "overlap": " ".join(str(row.get("overlap") or "").split())[:200]})
    return result[:_MAX_CANDIDATES]


def listing_issue(info):
    """None when the quote is a USD-reporting common stock on a US venue."""
    if not isinstance(info, dict) or not info:
        return "no_quote"
    if info.get("quoteType") != "EQUITY":
        return "not_equity"
    if info.get("exchange") not in US_EXCHANGES:
        return "not_us_listed"
    if (_finite(info.get("marketCap")) or 0) <= 0:
        return "no_market_cap"
    # Statement amounts are shown in USD; a foreign-currency filer would be mislabeled.
    if (info.get("financialCurrency") or "USD") != "USD":
        return "non_usd_financials"
    return None


def select_peers(target_info, candidates, infos, *, cap_ceiling=None):
    """Validate, size-filter and order candidates. Returns (kept, excluded).

    ``candidates`` keeps the proposer's order; ``infos`` maps ticker -> yfinance info.
    Peers below 10% of the target's market cap are dropped, always keeping the two
    largest valid peers. Same-sector peers are preferred, then capped at 4.
    """
    target_cap = _finite(target_info.get("marketCap")) or 0
    valid, excluded = [], []
    for candidate in candidates:
        info = infos.get(candidate["ticker"]) or {}
        issue = listing_issue(info)
        cap = _finite(info.get("marketCap")) or 0
        if issue is None and cap_ceiling and target_cap and cap > target_cap * cap_ceiling:
            issue = "too_large"
        if issue:
            excluded.append({**candidate, "reason": issue})
            continue
        valid.append({**candidate, "market_cap": cap, "sector": info.get("sector")})
    floor = target_cap * _MIN_PEER_CAP_RATIO
    kept = [p for p in valid if p["market_cap"] >= floor]
    if len(kept) < 2:
        largest = sorted(valid, key=lambda p: p["market_cap"], reverse=True)[:2]
        kept = [p for p in valid if p in largest]
    excluded += [{**p, "reason": "too_small"} for p in valid if p not in kept]
    sector = target_info.get("sector")
    kept.sort(key=lambda p: bool(sector) and p.get("sector") != sector)  # stable: proposer order within
    excluded += [{**p, "reason": "cap_limit"} for p in kept[_MAX_PEERS:]]
    return kept[:_MAX_PEERS], excluded


# --------------------------------------------------------------------------- metrics

def _quarter_columns(frame):
    """Quarter-end dates newest first, only while consecutive (~one quarter apart)."""
    try:
        columns = sorted((c for c in frame.columns), reverse=True)
        dates = [c.date() if hasattr(c, "date") else date.fromisoformat(str(c)[:10]) for c in columns]
    except (AttributeError, TypeError, ValueError):
        return [], []
    keep = dates[:1]
    for prev, current in zip(dates, dates[1:]):
        if not 75 <= (prev - current).days <= 105:
            break
        keep.append(current)
    return columns[:len(keep)], keep


def _row(frame, labels, columns, need=1):
    """Newest-first values up to the first gap; None when fewer than ``need``."""
    index = getattr(frame, "index", None)
    if index is None or not index.is_unique:
        return None
    for label in labels:
        if label in frame.index:
            values = []
            for column in columns:
                value = _finite(frame.loc[label, column])
                if value is None:
                    break
                values.append(value)
            if len(values) >= need:
                return values
    return None


def compute_fundamentals(info, q_income=None, q_cashflow=None):
    """TTM metrics from quarterly statements only (never mixed with fiscal-year rows).

    Provider TTM fields in ``info`` are fallbacks when quarterly rows are incomplete.
    """
    info = info or {}
    out = {"market_cap": _finite(info.get("marketCap")),
           "ev_sales": _finite(info.get("enterpriseToRevenue")),
           "forward_pe": _finite(info.get("forwardPE")),
           "revenue_ttm": None, "growth": None, "growth_basis": None,
           "gross_margin": None, "op_margin": None, "fcf_margin": None,
           "quarter_end": None, "revenue_basis": None}
    if out["ev_sales"] is not None and out["ev_sales"] <= 0:
        out["ev_sales"] = None  # negative EV is not a comparable multiple
    columns, dates = ([], [])
    if q_income is not None and not getattr(q_income, "empty", True):
        columns, dates = _quarter_columns(q_income)
    revenue = _row(q_income, ("Total Revenue", "Operating Revenue"), columns) if columns else None
    if dates:
        out["quarter_end"] = dates[0]
    if revenue and len(revenue) >= 4 and sum(revenue[:4]) > 0:
        ttm = sum(revenue[:4])
        out["revenue_ttm"], out["revenue_basis"] = ttm, "quarterly_sum"
        if len(revenue) >= 8 and sum(revenue[4:8]) > 0:
            out["growth"], out["growth_basis"] = ttm / sum(revenue[4:8]) - 1, "ttm_yoy"
        gross = _row(q_income, ("Gross Profit",), columns[:4], need=4)
        operating = _row(q_income, ("Operating Income", "Total Operating Income As Reported"), columns[:4], need=4)
        if gross:
            out["gross_margin"] = sum(gross) / ttm
        if operating:
            out["op_margin"] = sum(operating) / ttm
        if q_cashflow is not None and not getattr(q_cashflow, "empty", True):
            cf_columns, _ = _quarter_columns(q_cashflow)
            if list(cf_columns[:4]) == list(columns[:4]):  # same four quarters as revenue
                fcf = _row(q_cashflow, ("Free Cash Flow",), cf_columns[:4], need=4)
                if fcf:
                    out["fcf_margin"] = sum(fcf) / ttm
    if out["growth"] is None and revenue and len(revenue) >= 5 and revenue[4] > 0 \
            and 350 <= (dates[0] - dates[4]).days <= 380:
        out["growth"], out["growth_basis"] = revenue[0] / revenue[4] - 1, "quarter_yoy"
    if out["growth"] is None and _finite(info.get("revenueGrowth")) is not None:
        # Yahoo's revenueGrowth is the latest quarter's YoY change.
        out["growth"], out["growth_basis"] = _finite(info["revenueGrowth"]), "quarter_yoy"
    if out["revenue_ttm"] is None and (_finite(info.get("totalRevenue")) or 0) > 0:
        out["revenue_ttm"], out["revenue_basis"] = _finite(info["totalRevenue"]), "provider_ttm"
    if out["gross_margin"] is None:
        out["gross_margin"] = _finite(info.get("grossMargins"))
    if out["op_margin"] is None:
        out["op_margin"] = _finite(info.get("operatingMargins"))
    if out["quarter_end"] is None and _finite(info.get("mostRecentQuarter")):
        out["quarter_end"] = datetime.fromtimestamp(float(info["mostRecentQuarter"]), timezone.utc).date()
    return out


def compute_returns(closes, asof):
    """closes: {ticker: [(date, close), ...]} -> {ticker: {'ret_3m','ret_12m','price_date'}}."""
    result = {}
    for ticker, series in closes.items():
        points = sorted((d, c) for d, c in series if d <= asof and _finite(c) and c > 0)
        if not points:
            continue
        last_date, last_close = points[-1]
        row = {"price_date": last_date, "ret_3m": None, "ret_12m": None}
        for key, days in (("ret_3m", 91), ("ret_12m", 365)):
            target = last_date - timedelta(days=days)
            base = [p for p in points if p[0] <= target]
            if base and (target - base[-1][0]).days <= 7:
                row[key] = last_close / base[-1][1] - 1
        result[ticker] = row
    return result


# --------------------------------------------------------------------------- rendering

def _median(values, digits):
    values = [v for v in values if v is not None]
    return _half_up(statistics.median(values), digits) if values else None


def _fmt(value, digits):
    return "-" if value is None else f"{value:,.{digits}f}"


def _display_values(rows):
    values = {}
    for field, _, digits, scale in _TABLE_ROWS:
        line = []
        for row in rows:
            value = row.get(field)
            if field == "forward_pe" and value is not None and value <= 0:
                value = None  # rendered as 적자 and excluded from the median
            line.append(None if value is None else _half_up(value * scale, digits))
        values[field] = line
    return values


_EN_LABELS = {
    "market_cap": "Market cap ($B)", "revenue_ttm": "TTM revenue ($B)", "gross_margin": "Gross margin (%)",
    "op_margin": "Operating margin (%)", "fcf_margin": "FCF margin (%)", "ev_sales": "EV/Sales (x)",
    "forward_pe": "Forward P/E (x)", "ret_3m": "3M return (%)", "ret_12m": "12M return (%)",
}
_EN_GROWTH_LABELS = {"ttm_yoy": "TTM YoY", "quarter_yoy": "latest quarter YoY"}


def _gap_sentence(subject, target, median, language="ko", unit="%"):
    if target is None or median is None:
        return ""
    gap = _half_up(target - median, 1)
    if language != "ko":
        if gap == 0:
            return f"{subject} is {target:,.1f}{unit}, in line with the peer median ({median:,.1f}{unit})."
        return (f"{subject} is {target:,.1f}{unit}, {abs(gap):,.1f}pp {'above' if gap > 0 else 'below'} "
                f"the peer median ({median:,.1f}{unit}).")
    if gap == 0:
        return f"{subject} {target:,.1f}{unit}로 피어 중앙값({median:,.1f}{unit})과 같은 수준입니다."
    direction = "높습니다" if gap > 0 else "낮습니다"
    return f"{subject} {target:,.1f}{unit}로 피어 중앙값({median:,.1f}{unit})보다 {abs(gap):,.1f}%p {direction}."


def _comparison_sentences(rows, values, growth_label, language="ko"):
    name, ko = rows[0]["name"], language == "ko"
    subjects = ((f"{name}의 매출 성장률({growth_label})은", f"{name}의 영업이익률은", "FCF 마진은", "매출총이익률은")
                if ko else (f"{name}'s revenue growth ({growth_label})", f"{name}'s operating margin",
                            "FCF margin", "Gross margin"))
    sentences = [
        _gap_sentence(subjects[0], values["growth"][0], _median(values["growth"][1:], 1), language),
        _gap_sentence(subjects[1], values["op_margin"][0], _median(values["op_margin"][1:], 1), language),
    ]
    fcf, fcf_median = values["fcf_margin"][0], _median(values["fcf_margin"][1:], 1)
    if fcf is not None and fcf_median is not None:
        sentences.append(_gap_sentence(subjects[2], fcf, fcf_median, language))
    else:
        sentences.append(_gap_sentence(subjects[3], values["gross_margin"][0],
                                       _median(values["gross_margin"][1:], 1), language))
    target, median = values["ev_sales"][0], _median(values["ev_sales"][1:], 1)
    if target is not None and median:
        premium = int(_half_up((target / median - 1) * 100, 0))
        if premium == 0:
            sentences.append(f"EV/Sales는 {target:,.1f}배로 피어 중앙값({median:,.1f}배)과 비슷한 수준입니다." if ko else
                             f"EV/Sales of {target:,.1f}x is in line with the peer median ({median:,.1f}x).")
        elif ko:
            state = "할증" if premium > 0 else "할인"
            sentences.append(f"EV/Sales는 {target:,.1f}배로 피어 중앙값({median:,.1f}배) 대비 "
                             f"약 {abs(premium)}% {state}된 수준입니다.")
        else:
            sentences.append(f"EV/Sales of {target:,.1f}x is a {abs(premium)}% "
                             f"{'premium' if premium > 0 else 'discount'} to the peer median ({median:,.1f}x).")
    target, median = values["ret_12m"][0], _median(values["ret_12m"][1:], 1)
    if target is not None and median is not None:
        gap = _half_up(target - median, 1)
        if not ko:
            sentences.append(f"The 12-month share-price return of {target:,.1f}% is "
                             + (f"in line with the peer median ({median:,.1f}%)." if gap == 0 else
                                f"{abs(gap):,.1f}pp {'above' if gap > 0 else 'below'} the peer median ({median:,.1f}%)."))
        elif gap == 0:
            sentences.append(f"최근 12개월 주가 수익률은 {target:,.1f}%로 피어 중앙값과 같습니다.")
        else:
            state = "상회했습니다" if gap > 0 else "하회했습니다"
            sentences.append(f"최근 12개월 주가 수익률은 {target:,.1f}%로 피어 중앙값({median:,.1f}%)을 "
                             f"{abs(gap):,.1f}%p {state}.")
    return [s for s in sentences if s]


def _growth_basis(rows):
    bases = {row.get("growth_basis") for row in rows if row.get("growth") is not None}
    return next(iter(bases)) if len(bases) == 1 else None


def render_peer_markdown(rows, *, source, asof, excluded=(), language="ko"):
    """Deterministic table + code-computed sentences (Korean, or English for en). rows[0] is the target."""
    ko = language == "ko"
    values = _display_values(rows)
    basis = _growth_basis(rows)
    growth_label = (_GROWTH_LABELS if ko else _EN_GROWTH_LABELS).get(basis, "YoY")
    mixed_growth = basis is None and len({r.get("growth_basis") for r in rows if r.get("growth") is not None}) > 1
    lines = ["#### 경쟁사 비교 분석" if ko else "#### Peer Comparison Analysis", "",
             f"| {'구분' if ko else 'Metric'} | " + " | ".join(r["ticker"] for r in rows)
             + f" | {'피어 중앙값' if ko else 'Peer median'} |",
             "|---|" + "---:|" * (len(rows) + 1)]
    loss_marked = False
    for field, label, digits, _ in _TABLE_ROWS:
        if field == "growth":
            label = f"매출 성장률({growth_label},%)" if ko else f"Revenue growth ({growth_label}, %)"
        elif not ko:
            label = _EN_LABELS[field]
        cells = []
        for row, value in zip(rows, values[field]):
            if field == "forward_pe" and value is None and (row.get("forward_pe") or 0) < 0:
                cells.append("적자" if ko else "Loss")
                loss_marked = True
            elif field == "growth" and mixed_growth and value is not None and row.get("growth_basis") == "quarter_yoy":
                cells.append(_fmt(value, digits) + "*")
            else:
                cells.append(_fmt(value, digits))
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {_fmt(_median(values[field][1:], digits), digits)} |")
    lines.append(f"| {'최근 분기 말' if ko else 'Latest quarter end'} | " + " | ".join(
        r["quarter_end"].isoformat() if r.get("quarter_end") else "-" for r in rows) + " | - |")
    lines += ["", " ".join(_comparison_sentences(rows, values, growth_label, language)), ""]
    target_end = rows[0].get("quarter_end")
    misaligned = [r["ticker"] for r in rows[1:] if target_end and r.get("quarter_end")
                  and abs((r["quarter_end"] - target_end).days) > _MISALIGNED_DAYS]
    small = [p["ticker"] for p in excluded if p.get("reason") == "too_small"]
    names = " · ".join(f"{r['name']}({r['ticker']})" for r in rows[1:])
    if ko:
        selection = ("Perplexity 후보 + yfinance 검증(미국 상장 보통주·시가총액 기준)" if source == "perplexity"
                     else "yfinance 업종 상위 기업(시가총액 0.1~10배)")
        lines.append(f"출처: 비교기업 선정 {selection} · 재무·밸류에이션·주가 yfinance · 기준일 {asof.isoformat()}")
        notes = [f"비교 기업: {names}.",
                 f"피어 중앙값은 분석 대상을 제외한 비교기업 {len(rows) - 1}개사 기준입니다.",
                 "TTM 매출·이익률·FCF 마진은 최근 4개 분기 합산 기준이며 분기 자료가 부족하면 Yahoo 제공 TTM 값을 씁니다."]
        if mixed_growth:
            notes.append("매출 성장률의 '*'는 최근 분기 YoY, 표시가 없는 값은 TTM YoY입니다.")
        if misaligned:
            notes.append(f"{', '.join(misaligned)}의 최근 분기 말은 분석 대상과 {_MISALIGNED_DAYS}일 넘게 차이 나므로 "
                         "같은 기간 비교가 아닙니다.")
        if small:
            notes.append(f"시가총액이 분석 대상의 10% 미만인 {', '.join(small)}의 경우 규모 차이가 커서 제외했습니다.")
        if loss_marked:
            notes.append("Forward PER의 '적자'는 예상 이익이 음수여서 배수를 산출하지 않은 경우입니다.")
        notes.append("선정된 비교기업 기준의 참고 자료이며 전체 업종 순위나 매매 조건이 아닙니다.")
    else:
        selection = ("Perplexity candidates validated with yfinance (US-listed common stock, market-cap rule)"
                     if source == "perplexity" else "yfinance industry leaders (0.1-10x market cap)")
        lines.append(f"Source: peer selection by {selection} · financials, valuation and prices from yfinance · "
                     f"as of {asof.isoformat()}")
        notes = [f"Peers: {names}.",
                 f"Peer median excludes the target and covers {len(rows) - 1} peers.",
                 "TTM revenue, margins and FCF margin sum the latest four quarters; Yahoo TTM values are used "
                 "when quarterly data is incomplete."]
        if mixed_growth:
            notes.append("'*' marks latest-quarter YoY growth; unmarked values are TTM YoY.")
        if misaligned:
            notes.append(f"Latest quarter end of {', '.join(misaligned)} differs from the target by more than "
                         f"{_MISALIGNED_DAYS} days, so periods are not aligned.")
        if small:
            notes.append(f"{', '.join(small)} excluded: market cap below 10% of the target.")
        if loss_marked:
            notes.append("'Loss' means forward earnings are negative, so no P/E multiple is shown.")
        notes.append("Reference comparison against selected peers only; not an industry ranking or a trading condition.")
    lines += ["", " ".join(notes)]
    return "\n".join(lines), misaligned


# --------------------------------------------------------------------------- collection

def _perplexity_key():
    key = os.environ.get("PERPLEXITY_API_KEY")
    if key:
        return key
    try:
        from cores.llm.config_loader import load_report_mcp_registry
        return load_report_mcp_registry().get("perplexity").env.get("PERPLEXITY_API_KEY") or None
    except Exception:  # noqa: BLE001 - optional provider configuration
        return None


async def perplexity_candidates(ticker, company, asof, *, key=None, timeout=_PERPLEXITY_TIMEOUT):
    """One structured Perplexity call. Returns [] when unavailable."""
    key = key or _perplexity_key()
    if not key:
        return []
    prompt = (f"List 4 to 6 US-listed public companies that are DIRECT competitors of {company} "
              f"(ticker {ticker}) by product or business-segment overlap, as of {asof.isoformat()}. "
              "Exclude customers, suppliers, partners, parents, subsidiaries, ETFs, private companies and "
              "companies not listed on a US exchange. Use the US ticker symbol. "
              'Return JSON only: {"peers": [{"ticker": "...", "name": "...", '
              '"overlap": "<= 15 words on the overlapping product or segment"}]}')
    schema = {"type": "object", "properties": {"peers": {"type": "array", "items": {
        "type": "object", "properties": {"ticker": {"type": "string"}, "name": {"type": "string"},
                                         "overlap": {"type": "string"}},
        "required": ["ticker", "name", "overlap"]}}}, "required": ["peers"]}
    body = {"model": PERPLEXITY_MODEL, "temperature": 0,
            "messages": [{"role": "system", "content": "You are a precise equity research assistant. Reply with JSON only."},
                         {"role": "user", "content": prompt}],
            "response_format": {"type": "json_schema", "json_schema": {"schema": schema}}}
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout)) as session:
        async with session.post(PERPLEXITY_URL, json=body, headers=headers) as response:
            if response.status != 200:
                raise ValueError(f"perplexity_http_{response.status}")
            data = await response.json(content_type=None)
    content = (((data or {}).get("choices") or [{}])[0].get("message") or {}).get("content")
    return parse_candidates(content, ticker)


def _yf_info(ticker):
    import yfinance as yf
    return yf.Ticker(ticker).info or {}


def _yf_statements(ticker):
    import yfinance as yf
    stock = yf.Ticker(ticker)
    return stock.quarterly_income_stmt, stock.quarterly_cashflow


def _yf_industry(industry_key):
    import yfinance as yf
    frame = yf.Industry(industry_key).top_companies
    return [] if frame is None else [str(symbol).upper() for symbol in frame.index]


def _yf_closes(tickers, asof):
    import yfinance as yf
    frame = yf.download(list(tickers), start=(asof - timedelta(days=400)).isoformat(),
                        end=(asof + timedelta(days=1)).isoformat(), auto_adjust=True,
                        progress=False, threads=False, group_by="column")
    closes = frame["Close"] if "Close" in frame else frame
    result = {}
    for ticker in tickers:
        if ticker in getattr(closes, "columns", ()):
            series = closes[ticker].dropna()
            result[ticker] = [(index.date(), float(value)) for index, value in series.items()]
    return result


async def _bounded(semaphore, func, *args, timeout=_TICKER_TIMEOUT):
    async with semaphore:
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout)


async def _gather_map(semaphore, func, keys):
    results = await asyncio.gather(*(_bounded(semaphore, func, key) for key in keys), return_exceptions=True)
    return {key: value for key, value in zip(keys, results) if not isinstance(value, BaseException)}


async def _collect(ticker, company, asof, language, *, candidates_fn, info_fn, statements_fn, industry_fn, closes_fn):
    semaphore = asyncio.Semaphore(_CONCURRENCY)
    target_task = asyncio.create_task(_bounded(semaphore, info_fn, ticker))
    try:
        candidates = await candidates_fn(ticker, company, asof)
        proposer_error = None
    except Exception as error:  # noqa: BLE001 - fall back to the industry list
        candidates, proposer_error = [], type(error).__name__
    target_info = await target_task
    if listing_issue(target_info):
        return _skip("target_" + listing_issue(target_info))
    infos = await _gather_map(semaphore, info_fn, [c["ticker"] for c in candidates])
    kept, excluded = select_peers(target_info, candidates, infos)
    source = "perplexity"
    if len(kept) < 2 and target_info.get("industryKey"):
        source = "industry"
        symbols = await _bounded(semaphore, industry_fn, target_info["industryKey"])
        known = {ticker, *(p["ticker"] for p in kept)}
        extra = [{"ticker": s, "name": "", "overlap": ""} for s in symbols if s not in known][:8]
        infos.update(await _gather_map(semaphore, info_fn, [c["ticker"] for c in extra]))
        more, more_excluded = select_peers(target_info, extra, infos, cap_ceiling=_MAX_FALLBACK_CAP_RATIO)
        more = [p for p in more if p["market_cap"] >= (_finite(target_info.get("marketCap")) or 0) * _MIN_PEER_CAP_RATIO]
        kept = (kept + more)[:_MAX_PEERS]
        excluded += [p for p in more_excluded if p.get("reason") == "too_small"]
    if len(kept) < 2:
        return _skip("insufficient_peers", proposer_error=proposer_error)
    tickers = [ticker] + [p["ticker"] for p in kept]
    statements = await _gather_map(semaphore, statements_fn, tickers)
    try:
        closes = await asyncio.wait_for(asyncio.to_thread(closes_fn, tickers, asof), _TICKER_TIMEOUT + 3)
    except Exception:  # noqa: BLE001 - returns are optional rows
        closes = {}
    returns = compute_returns(closes, asof)
    rows = []
    for symbol in tickers:
        info = target_info if symbol == ticker else infos.get(symbol, {})
        income, cashflow = statements.get(symbol, (None, None))
        row = compute_fundamentals(info, income, cashflow)
        row.update({"ticker": symbol, "name": _display_name(info, symbol)})
        row.update({k: v for k, v in returns.get(symbol, {}).items() if k in ("ret_3m", "ret_12m")})
        rows.append(row)
    markdown, misaligned = render_peer_markdown(rows, source=source, asof=asof, excluded=excluded,
                                                language=language)
    overlaps = "\n".join(f"- {p['ticker']}: {p['overlap']}" for p in kept if p.get("overlap"))
    context = markdown + (f"\n\nPeer selection rationale (candidate proposer summary, unverified):\n{overlaps}"
                          if overlaps else "")
    return {"ready": True, "peers": rows, "excluded_peers": excluded, "source": source,
            "misaligned": misaligned, "public_markdown": markdown, "model_context": context,
            "skip_reason": None, "proposer_error": proposer_error}


async def collect_us_peer_comparison(ticker, company_name, reference_date, language="ko", *, candidates_fn=None,
                                     info_fn=None, statements_fn=None, industry_fn=None, closes_fn=None,
                                     budget=_BUDGET_SECONDS, today=None):
    """Return {'ready', 'peers', 'source', 'public_markdown', 'model_context', 'skip_reason', ...}.

    Injected callables replace network access in tests. Never raises.
    """
    started = time.monotonic()
    symbol = str(ticker or "").upper()
    if not _TICKER.fullmatch(symbol):
        return _skip("invalid_ticker", elapsed=0.0)
    try:
        asof = datetime.strptime(str(reference_date), "%Y%m%d").date()
    except ValueError:
        return _skip("invalid_reference_date", elapsed=0.0)
    # yfinance serves the current snapshot only; do not attach it to an older report date.
    current = today or datetime.now(timezone.utc).date()
    if not -1 <= (current - asof).days <= 4:
        return _skip("reference_date_not_current", elapsed=0.0)
    try:
        packet = await asyncio.wait_for(_collect(
            symbol, company_name or symbol, asof, language,
            candidates_fn=candidates_fn or perplexity_candidates, info_fn=info_fn or _yf_info,
            statements_fn=statements_fn or _yf_statements, industry_fn=industry_fn or _yf_industry,
            closes_fn=closes_fn or _yf_closes), budget)
    except asyncio.TimeoutError:
        packet = _skip("timeout")
    except Exception as error:  # noqa: BLE001 - optional data never blocks a report
        packet = _skip(f"error_{type(error).__name__}")
    packet["elapsed"] = round(time.monotonic() - started, 1)
    return packet
