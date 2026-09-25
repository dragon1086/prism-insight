"""WiseFn-curated competitor comparison for KR reports.

Report-only optional data: two fixed-host WiseReport reads, no LLM, never a
trading gate. Any fetch/parse/validation failure returns a skipped packet.
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

import aiohttp
from bs4 import BeautifulSoup

_HOST = "https://comp.wisereport.co.kr/company/"
PEER_LIST_URL = (_HOST + "ajax/cF6001.aspx?cmp_cd={code}&finGubun=MAIN&sec_cd=FG000&frq=Y"
                 "&cmp_cd1=&cmp_cd2=&cmp_cd3=&cmp_cd4=")
PEER_TABLE_URL = _HOST + "cF6002.aspx?cmp_cd={code}&finGubun=MAIN&sec_cd=FG000&frq=Y"
_TIMEOUT_SECONDS = 8
_MAX_BYTES = 500_000
_CODE = re.compile(r"[0-9A-Z]{6}")

# Provider row label -> field. Absolute per-share rows are intentionally not read.
_ROWS = {
    "전일종가(원)": "price", "시가총액(억원)": "market_cap", "매출액(억원)": "revenue",
    "영업이익(억원)": "operating_income", "당기순이익(지배)(억원)": "net_income_controlling",
    "영업이익률(%)": "op_margin", "순이익률(%)": "net_margin", "ROE(%)": "roe",
    "부채비율(%)": "debt_ratio", "PER": "per", "PBR": "pbr", "재무연월": "period",
}
_REQUIRED = ("market_cap", "revenue", "op_margin", "roe", "per", "pbr", "period")
# field, public label, display digits
_TABLE_ROWS = (
    ("market_cap", "시가총액(억원)", 0), ("revenue", "매출액(억원)", 0),
    ("op_margin", "영업이익률(%)", 1), ("net_margin", "순이익률(%)", 1),
    ("roe", "ROE(%)", 1), ("debt_ratio", "부채비율(%)", 1),
    ("per", "PER(배)", 2), ("pbr", "PBR(배)", 2),
)


def _skip(reason):
    return {"ready": False, "peers": [], "period": "", "price_basis": "", "public_markdown": "",
            "model_context": "", "skip_reason": reason}


def _number(raw):
    value = str(raw or "").replace(",", "").strip()
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    return float(value)


def _basis(fin_gubun):
    text = str(fin_gubun or "")
    return "연결" if "연결" in text else "별도" if "별도" in text else "확인 필요"


def parse_peer_header(payload):
    """cF6001 JSON -> ordered companies (target first)."""
    data = json.loads(payload) if isinstance(payload, (str, bytes)) else payload
    rows = data.get("oDt_header") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not 2 <= len(rows) <= 5:
        raise ValueError("peer_list_size")
    companies = []
    for row in sorted(rows, key=lambda item: item.get("SEQ") or 0):
        code, name = str(row.get("CMP_CD") or ""), str(row.get("CMP_KOR") or "").strip()
        market_cap = row.get("MKT_VAL")
        if not _CODE.fullmatch(code) or not name or not isinstance(market_cap, (int, float)):
            raise ValueError("peer_list_row")
        yymm = str(row.get("YYMM") or "")
        companies.append({"code": code, "name": name, "list_market_cap": float(market_cap),
                          "list_period": f"{yymm[:4]}/{yymm[4:6]}" if re.fullmatch(r"\d{6}", yymm) else "",
                          "basis": _basis(row.get("FIN_GUBUN"))})
    if len({c["code"] for c in companies}) != len(companies):
        raise ValueError("peer_list_duplicate")
    return companies


def parse_peer_table(html, column_count):
    """cF6002 table#cTB611 -> {field: [raw cell per column]} in column order."""
    if not isinstance(html, str) or len(html.encode()) > _MAX_BYTES:
        raise ValueError("table_size")
    table = BeautifulSoup(html, "html.parser").select_one("table#cTB611")
    if table is None:
        raise ValueError("table_missing")
    rows = {}
    for tr in table.find_all("tr"):
        th = tr.find("th")
        label = re.sub(r"\s+", "", th.get_text(" ", strip=True)) if th else ""
        if label not in _ROWS:
            continue
        cells = [td.get_text(" ", strip=True) for td in tr.find_all("td")
                 if "display:none" not in (td.get("style") or "").replace(" ", "")]
        if len(cells) != column_count:
            raise ValueError("column_count")
        if _ROWS[label] in rows:
            raise ValueError("duplicate_row")
        rows[_ROWS[label]] = cells
    if any(field not in rows for field in _REQUIRED):
        raise ValueError("missing_rows")
    return rows


def build_peers(companies, rows):
    """Join by column order; the market-cap row must match the peer list per column."""
    peers = []
    for index, company in enumerate(companies):
        item = dict(company)
        for field, cells in rows.items():
            item[field] = cells[index].strip() if field == "period" else _number(cells[index])
        cap, expected = item["market_cap"], company["list_market_cap"]
        if cap is None or abs(cap - expected) > max(0.1, abs(expected) * 1e-4):
            raise ValueError("market_cap_mismatch")
        if not re.fullmatch(r"\d{4}/\d{2}", item["period"]):
            raise ValueError("period_format")
        if company["list_period"] and company["list_period"] != item["period"]:
            raise ValueError("period_mismatch")
        peers.append(item)
    return peers


def _fmt(value, digits):
    return "-" if value is None else f"{value:,.{digits}f}"


def _half_up(value, digits):
    # Half-up on the decimal text, not binary float rounding (22.45 -> 22.5).
    return float(Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _median(values, digits):
    """Peer median rounded like the table, so sentences cite the displayed value."""
    values = [v for v in values if v is not None]
    return _half_up(statistics.median(values), digits) if values else None


def _display_values(peers):
    """Display-rounded values; sentences are computed from these same numbers."""
    values = {}
    for field, _, digits in _TABLE_ROWS:
        row = []
        for peer in peers:
            value = peer.get(field)
            if field in {"per", "pbr"} and value is not None and value <= 0:
                value = None  # negative/zero multiples are not comparable
            row.append(None if value is None else _half_up(value, digits))
        values[field] = row
    return values


def _gap_sentence(name, subject, target, median):
    if target is None or median is None:
        return ""
    gap = _half_up(target - median, 1)
    if gap == 0:
        return f"{name}의 {subject} {target:.1f}%로 비교기업 중앙값({median:.1f}%)과 같은 수준입니다."
    direction = "높습니다" if gap > 0 else "낮습니다"
    return (f"{name}의 {subject} {target:.1f}%로 비교기업 중앙값({median:.1f}%)보다 "
            f"{abs(gap):.1f}%p {direction}.")


def _valuation_sentence(values):
    # PER first; PBR only when PER is not comparable (both sides must be positive).
    for field, label in (("per", "PER"), ("pbr", "PBR")):
        target, median = values[field][0], _median(values[field][1:], 2)
        if target is None or median is None:
            continue
        premium = int(_half_up((target / median - 1) * 100, 0))
        if premium == 0:
            return f"{label}은 {target:.2f}배로 비교기업 중앙값({median:.2f}배)과 비슷한 수준입니다."
        state = "할증" if premium > 0 else "할인"
        return (f"{label}은 {target:.2f}배로 비교기업 중앙값({median:.2f}배) 대비 "
                f"약 {abs(premium)}% {state}된 수준입니다.")
    return ""


def _comparison_sentences(peers, values):
    name = peers[0]["name"]
    sentences = [
        _gap_sentence(name, "영업이익률은", values["op_margin"][0], _median(values["op_margin"][1:], 1)),
        _gap_sentence(name, "ROE는", values["roe"][0], _median(values["roe"][1:], 1)),
        _valuation_sentence(values),
    ]
    revenue = values["revenue"]
    if revenue[0] is not None:
        ranked = [v for v in revenue if v is not None]
        rank = 1 + sum(v > revenue[0] for v in ranked)
        sentences.append(f"매출액 기준으로는 비교 대상 {len(ranked)}개사 중 {rank}위입니다.")
    return [s for s in sentences if s]


def render_peer_markdown(peers):
    """Deterministic Korean table + comparison sentences; no internal tokens."""
    values = _display_values(peers)
    lines = ["#### 경쟁사 비교 분석", "",
             "| 구분 | " + " | ".join(p["name"] for p in peers) + " | 피어 중앙값 |",
             "|---|" + "---:|" * (len(peers) + 1)]
    loss_marked = False
    for field, label, digits in _TABLE_ROWS:
        cells = []
        for peer, value in zip(peers, values[field]):
            if field == "per" and value is None and (peer.get("net_income_controlling") or 0) < 0:
                cells.append("적자")
                loss_marked = True
            else:
                cells.append(_fmt(value, digits))
        lines.append(f"| {label} | " + " | ".join(cells) + f" | {_fmt(_median(values[field][1:], digits), digits)} |")
    lines.append("| 재무기준 | " + " | ".join(p["basis"] for p in peers) + " | - |")
    periods = sorted({p["period"] for p in peers})
    if len(periods) > 1:
        lines.append("| 재무 연월 | " + " | ".join(p["period"] for p in peers) + " | - |")
    lines += ["", " ".join(_comparison_sentences(peers, values)), ""]
    period_text = periods[0] if len(periods) == 1 else "기업별 상이(표 참고)"
    lines.append(f"출처: WiseReport 경쟁사분석(WiseFn 선정 비교기업) · 재무 기준 {period_text} 연간 실적 · "
                 "가격 기준 전일종가(시가총액·PER·PBR)")
    notes = [f"피어 중앙값은 분석 대상을 제외한 비교기업 {len(peers) - 1}개사 기준입니다."]
    if len({p["basis"] for p in peers}) > 1:
        notes.append("연결과 별도 재무기준이 섞여 있어 직접 비교에는 한계가 있습니다.")
    if loss_marked:
        notes.append("PER의 '적자'는 지배주주 순이익이 적자여서 배수를 산출하지 않은 경우입니다.")
    notes.append("선정된 비교기업 기준의 참고 자료이며 전체 업종 순위나 매매 조건이 아닙니다.")
    lines += ["", " ".join(notes)]
    return "\n".join(lines)


async def _fetch_text(session, url):
    last_error = None
    for _ in range(2):  # one retry
        try:
            async with session.get(url) as response:
                if response.status != 200:
                    raise ValueError(f"http_{response.status}")
                body = await response.read()
                if len(body) > _MAX_BYTES:
                    raise ValueError("response_size")
                return body.decode("utf-8", errors="replace")
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            last_error = error
    raise last_error


def _reason(error):
    if isinstance(error, asyncio.TimeoutError):
        return "timeout"
    if isinstance(error, aiohttp.ClientError):
        return "network_" + type(error).__name__
    if isinstance(error, json.JSONDecodeError):
        return "peer_list_json"
    return str(error) if isinstance(error, ValueError) and str(error) else type(error).__name__


async def collect_wisereport_peers(company_code, company_name, reference_date, *, fetch=None):
    """Return {'ready', 'peers', 'period', 'price_basis', 'public_markdown', 'model_context', 'skip_reason'}.

    ``fetch`` is an optional async ``url -> text`` callable for tests.
    """
    code = str(company_code or "")
    if not _CODE.fullmatch(code):
        return _skip("invalid_code")
    try:
        reference = datetime.strptime(str(reference_date), "%Y%m%d").date()
    except ValueError:
        return _skip("invalid_reference_date")
    # WiseReport only serves the current snapshot; never attach it to a past date.
    if reference != datetime.now(ZoneInfo("Asia/Seoul")).date():
        return _skip("reference_date_not_today")
    list_url, table_url = PEER_LIST_URL.format(code=code), PEER_TABLE_URL.format(code=code)
    try:
        if fetch is None:
            timeout = aiohttp.ClientTimeout(total=_TIMEOUT_SECONDS)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                header_text, table_html = await asyncio.gather(
                    _fetch_text(session, list_url), _fetch_text(session, table_url))
        else:
            header_text, table_html = await asyncio.gather(fetch(list_url), fetch(table_url))
        companies = parse_peer_header(header_text)
        if companies[0]["code"] != code:
            return _skip("target_mismatch")
        peers = build_peers(companies, parse_peer_table(table_html, len(companies)))
    except Exception as error:  # noqa: BLE001 - optional data never blocks a report
        return _skip(_reason(error))
    public = render_peer_markdown(peers)
    codes = ", ".join(f"{p['name']}({p['code']})" for p in peers[1:])
    periods = sorted({p["period"] for p in peers})
    return {"ready": True, "peers": peers, "period": periods[0] if len(periods) == 1 else "mixed",
            "price_basis": "전일종가", "public_markdown": public,
            "model_context": public + f"\n\n비교기업 종목코드: {codes}", "skip_reason": None}
