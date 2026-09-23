"""Small, report-only peer snapshots; never an industry ranking or trading signal."""
from __future__ import annotations

import asyncio
import calendar
import math
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from prism_core.competitive_evidence import _HEADING, _mask_fences
from prism_core.market_report_context import _public_https

_BASE = "https://comp.wisereport.co.kr/company/c1010001.aspx?cmp_cd="
_UNITS = {"PER": "배", "PBR": "배", "EV/EBITDA": "배", "EPS": "원",
          "BPS": "원", "현금DPS": "원", "현금배당수익률": "%"}
_MAX_BYTES = 1_500_000
_PEER_TABLE_HEADERS = (
    ("field", "type", "entity / peer_universe", "metric·value·period", "source", "publication_date", "status"),
    ("field", "type", "entity", "peer_universe", "metric", "value", "period", "geography", "unit",
     "source", "publication_date", "status", "excerpt"),
)


def _table_cells(line):
    # Never guess shifted cells or interpret escaped pipes as column separators.
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")) or "\\|" in line:
        return None
    return [cell.strip() for cell in line[1:-1].split("|")]


def _source_url(source):
    link = re.fullmatch(r"\[[^\[\]\n]+\]\((https://[^\s<>]+)\)", source)
    if link:
        source = link[1]
    return source if re.fullmatch(r"https://[^\s<>]+", source) and _public_https(source) else ""


def select_report_peer_candidates(reports, target, code_to_name):
    """Resolve explicit evidence peer proposals, never arbitrary company mentions."""
    reverse = {}
    for code, name in code_to_name.items():
        if isinstance(code, str) and re.fullmatch(r"\d{6}", code) and isinstance(name, str):
            reverse.setdefault(_name(name), []).append(code)
    selected, seen = [], {target}
    texts = reports.values() if isinstance(reports, dict) else reports
    for text in texts:
        if not isinstance(text, str):
            continue
        text, unclosed = _mask_fences(text)
        if unclosed:
            continue
        active, table_header, table_ready = None, None, False
        for line in text.splitlines():
            heading = re.match(r"^(#{1,6})\s+(.+)$", line)
            if heading:
                table_header, table_ready = None, False
                if active and len(heading[1]) <= active:
                    active = None
                if (_HEADING.fullmatch(line) or re.fullmatch(
                        r"#{3,4}\s+(?:경쟁력 비교 근거|\*\*경쟁력 비교 근거\*\*)\s*", line)):
                    active = len(heading[1])
            if not active:
                continue
            cells = _table_cells(line)
            if cells is not None:
                if tuple(cells) in _PEER_TABLE_HEADERS:
                    table_header, table_ready = cells, False
                    continue
                if table_header and not table_ready:
                    table_ready = len(cells) == len(table_header) and all(
                        re.fullmatch(r":?-{3,}:?", cell) for cell in cells)
                    if not table_ready:
                        table_header = None
                    continue
                if not table_ready or len(cells) != len(table_header):
                    continue
                fields = dict(zip(table_header, cells))
                if "entity / peer_universe" in fields:
                    parts = fields["entity / peer_universe"].split(" / ", 1)
                    fields["peer_universe"] = parts[1] if len(parts) == 2 else ""
            else:
                table_header, table_ready = None, False
                fields = {}
                for match in re.finditer(
                    r"\*\*([a-z_]+)(?::\*\*|\*\*:)\s*(.*?)(?=\s+/\s+|$)", line
                ):
                    fields[match[1]] = match[2]
            source = _source_url(fields.get("source", "").strip())
            if not source:
                continue
            for token in re.split(r"[,·;/]|\s+및\s+", fields.get("peer_universe", "")):
                token = token.strip().strip("[]\"'")
                match = re.fullmatch(r"(.+?)\s*\((\d{6})\)", token)
                if match:
                    name, code = match.groups()
                    if _name(code_to_name.get(code, "")) != _name(name):
                        continue
                else:
                    codes = reverse.get(_name(token), [])
                    if len(codes) != 1:
                        continue
                    code = codes[0]
                if code in seen:
                    continue
                seen.add(code)
                selected.append({"ticker": code, "name": code_to_name[code], "source": source,
                                 "rationale": "기존 분석에서 제시한 비교 후보, 독립적 경쟁관계 검증 아님"})
                if len(selected) == 2:
                    return selected
    return selected


def _text(node):
    return " ".join(node.get_text(" ", strip=True).split()) if node else ""


def _name(value):
    return re.sub(r"\s+", "", str(value)).casefold()


def _day(value):
    value = str(value)
    if re.fullmatch(r"\d{8}", value):
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return date.fromisoformat(value[:10])


def _grid(table):
    """Expand spans without shifting cells; malformed/oversized grids fail closed."""
    occupied = {}
    rows = table.find_all("tr")
    if len(rows) > 40:
        raise ValueError("table_size")
    for r, row in enumerate(rows):
        c = 0
        for cell in row.find_all(["th", "td"], recursive=False):
            while (r, c) in occupied:
                c += 1
            rs, cs = int(cell.get("rowspan", 1)), int(cell.get("colspan", 1))
            if not (1 <= rs <= 10 and 1 <= cs <= 10 and c + cs <= 12):
                raise ValueError("table_span")
            for rr in range(r, r + rs):
                for cc in range(c, c + cs):
                    if (rr, cc) in occupied or rr >= len(rows):
                        raise ValueError("table_overlap")
                    occupied[rr, cc] = _text(cell)
            c += cs
    width = max((c for _, c in occupied), default=-1) + 1
    if any((r, c) not in occupied for r in range(len(rows)) for c in range(width)):
        raise ValueError("table_ragged")
    return [[occupied[r, c] for c in range(width)] for r in range(len(rows))]


def _numeric(raw, unit):
    value = raw.replace(",", "").replace(" ", "")
    if unit != "배":
        if not value.endswith(unit):
            return None
        value = value[:-len(unit)]
    else:
        value = value.removesuffix("배")
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", value):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def parse_wisereport(html, ticker, company, reference_date):
    """Extract only the explicitly labelled fundamentals table, not summary ratios."""
    if not isinstance(html, str) or len(html.encode()) > _MAX_BYTES:
        raise ValueError("html_size")
    soup = BeautifulSoup(html, "html.parser")
    if _text(soup.select_one("#comInfo .cd")) != ticker:
        raise ValueError("ticker_mismatch")
    actual_name = _text(soup.select_one("#comInfo .nm_k"))
    if _name(actual_name) != _name(company):
        raise ValueError("name_mismatch")
    quote = soup.select_one("#cTB11")
    # Only this quote table's own header, not a shareholder/comment date elsewhere.
    body = quote.parent if quote else None
    header = body.find_previous_sibling("div", class_="header") if body else None
    match = re.search(r"기준\s*:\s*(\d{4})[.\-/](\d{2})[.\-/](\d{2})", _text(header))
    quote_date = date(*map(int, match.groups())) if match else None
    if quote_date and quote_date > _day(reference_date):
        raise ValueError("future_quote")
    tables = [t for t in soup.find_all("table")
              if "펀더멘털" in _text(t.find("caption"))]
    if len(tables) != 1:
        raise ValueError("fundamentals_table")
    table = tables[0]
    rows = _grid(table)
    periods = rows[0][1:]
    if not periods or any(not re.fullmatch(r"(?:\d{4}/\d{2}\([AE]\)|Fwd\. 12M\(E\))", p)
                          for p in periods) or len(set(periods)) != len(periods):
        raise ValueError("period_headers")
    for period in periods:
        if period == "Fwd. 12M(E)":
            continue
        year, month = int(period[:4]), int(period[5:7])
        if not 1 <= month <= 12 or year < 1:
            raise ValueError("period_calendar")
        period_end = date(year, month, calendar.monthrange(year, month)[1])
        if period.endswith("(A)") and period_end > _day(reference_date):
            raise ValueError("future_actual_period")
    notes = [_text(n) for n in table.parent.select("dl.annotation li")]
    joined = " ".join(notes)
    price_basis = "전 영업일 보통주 수정주가" if "전 영업일 보통주 수정주가" in joined else ""
    controlling_basis = "지배주주" if "지배주주 기준" in joined else ""
    keyed = {}
    for row in rows[1:]:
        if row[0] in keyed:
            raise ValueError("duplicate_metric")
        keyed[row[0]] = row[1:]
    scopes = keyed.get("회계기준", [""] * len(periods))
    facts = []
    for metric, unit in _UNITS.items():
        for index, raw in enumerate(keyed.get(metric, [])):
            value = _numeric(raw, unit)
            if value is None or (metric in {"PER", "PBR", "EV/EBITDA"} and value <= 0):
                continue
            denominator = {"PER": "EPS", "PBR": "BPS"}.get(metric)
            if denominator and denominator in keyed:
                denominator_value = _numeric(keyed[denominator][index], "원")
                if denominator_value is not None and denominator_value <= 0:
                    continue
            scope = scopes[index]
            facts.append({"metric": metric, "period": periods[index], "unit": unit,
                          "value": value, "raw": raw, "scope": scope,
                          "equity_basis": controlling_basis if scope == "연결" else scope})
    return {"ticker": ticker, "name": actual_name, "source": _BASE + ticker,
            "price_date": quote_date.isoformat() if quote_date else "",
            "price_is_recent": bool(quote_date and 0 <= (_day(reference_date) - quote_date).days < 7),
            "price_basis": price_basis, "fundamentals_asof": "",
            "classification": [_text(n) for n in soup.select("#comInfo .col11 .exp")],
            "notes": notes, "facts": facts}


def render_peer_comparison(snapshots):
    if not snapshots:
        return ""
    # The same selected companies/facts must not produce different prose solely
    # because an upstream section listed the two peers in another order.
    snapshots = [snapshots[0], *sorted(snapshots[1:], key=lambda item: item['ticker'])]
    target, *peers = snapshots
    lines = ["### 주요 비교기업의 실적·예상 지표", "",
             "선정한 기업의 공개 지표를 함께 살펴봅니다. 전체 업종 평균이나 순위는 아닙니다."]
    comparable = 0
    for peer in peers:
        same_price = (target["price_date"] and target["price_date"] == peer["price_date"]
                      and target["price_basis"] and target["price_basis"] == peer["price_basis"]
                      and target.get("price_is_recent") and peer.get("price_is_recent"))
        rows = []
        for fact in target["facts"]:
            other = next((f for f in peer["facts"] if all(f[k] == fact[k] for k in
                          ("metric", "period", "scope", "unit", "equity_basis"))), None)
            if other and same_price and fact["scope"] in {"연결", "별도"} and fact["equity_basis"]:
                rows.append(f"| {fact['metric']} | {fact['period']} · {fact['scope']} | "
                            f"{_display_value(fact)} | {_display_value(other)} | {fact['unit']} |")
        if rows:
            comparable += len(rows)
            lines += ["", f"**{target['name']} · {peer['name']} 비교** · 시세 기준 {target['price_date']}",
                      f"| 지표 | 실적·예상 기간 / 회계기준 | {target['name']} | {peer['name']} | 단위 |",
                      "|---|---|---:|---:|---|", *rows]
        else:
            lines += ["", (f"{peer['name']}와는 기간·회계기준·최근 시세 기준을 함께 충족하는 항목이 없어 "
                      "직접적인 배수 비교를 하지 않았습니다.")]
    if not comparable:
        # Preserve useful target facts rather than an all-missing audit table.
        rows = [f"| {f['metric']} | {f['period']} · {f['scope'] or '회계기준 별도 확인 필요'} | "
                f"{_display_value(f)}{f['unit']} |" for f in target["facts"]]
        if rows:
            lines += ["", f"**{target['name']}에서 확인한 지표**", "| 지표 | 기간 / 회계기준 | 수치 |",
                      "|---|---|---:|", *rows]
    lines += ["", ("A는 실적, E는 증권사 예상치입니다. 예상치는 회사 가이던스나 확정 실적이 아닙니다. "
              "현재 조회한 표이며 컨센서스 자체의 별도 기준일은 표시되어 있지 않습니다. "
              "지표 차이만으로 사업 경쟁력 우위나 자동 매수 조건 충족을 판단하지 않습니다.")]
    for item in snapshots:
        basis = f"시세 기준 {item['price_date']}" if item["price_date"] else "시세 기준일 별도 확인 필요"
        lines += [f"- [{item['name']} · WiseReport]({item['source']}) · {basis}"]
    if any(s["price_date"] and not s.get("price_is_recent") for s in snapshots):
        lines += ["", "7일 이상 지난 시세에 기반한 지표는 과거 참고값이며 현재 가치평가 비교에서는 제외했습니다."]
    notes = list(dict.fromkeys(n for item in snapshots for n in item["notes"]))
    if notes:
        lines += ["", "산출 기준: " + " / ".join(notes)]
    return "\n".join(lines)


def _display_value(fact):
    """Use validated provider text, not float's six-significant-digit formatting."""
    raw, unit = fact["raw"].strip(), fact["unit"]
    return raw.removesuffix(unit).strip()


async def collect_peer_comparison(ticker, company, candidates, reference_date, *, transport=None):
    """At most three fixed-host page reads, no LLM, persistence, or order authority."""
    empty = {"public_markdown": "", "model_context": "", "private_receipt": {"status": "unavailable"}}
    today = datetime.now(ZoneInfo("Asia/Seoul")).date()
    try:
        if _day(reference_date) != today or not re.fullmatch(r"\d{6}", str(ticker)):
            return empty
    except (ValueError, TypeError):
        return empty
    selected, tickers, names = [], {ticker}, {_name(company)}
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        code, name = candidate.get("ticker", ""), candidate.get("name", "")
        if (not isinstance(code, str) or not re.fullmatch(r"\d{6}", code)
                or not isinstance(name, str) or not name.strip() or len(name) > 80
                or code in tickers or _name(name) in names
                or not candidate.get("rationale") or not candidate.get("source")):
            continue
        selected.append((code, name))
        tickers.add(code)
        names.add(_name(name))
        if len(selected) == 2:
            break
    if not selected:
        return empty
    from prism_core.report_research_prefetch import _decode, native_call
    transport = transport or native_call

    async def fetch(code, name):
        try:
            response = _decode(await asyncio.wait_for(transport("firecrawl", "firecrawl_scrape", {
                "url": _BASE + code, "formats": ["html", "markdown"],
                "onlyMainContent": False, "maxAge": 0, "waitFor": 2000}), timeout=25))
            data = response.get("data", response)
            return parse_wisereport(data.get("html", ""), code, name, reference_date)
        except Exception:  # noqa: BLE001 - optional provider failure never blocks report
            return None

    fetched = await asyncio.gather(*(fetch(code, name) for code, name in [(ticker, company), *selected]))
    if not fetched[0]:
        return empty
    snapshots = [s for s in fetched if s]
    public = render_peer_comparison(snapshots)
    missing = [name for (_, name), snapshot in zip(selected, fetched[1:]) if snapshot is None]
    if missing:
        public += "\n\n" + "·".join(missing) + "의 비교 지표는 이번 조회에서 확보하지 못해 표에서 제외했습니다."
    return {"public_markdown": public, "model_context": public,
            "private_receipt": {"status": "available" if len(snapshots) > 1 else "partial",
                                "requested": len(fetched), "received": len(snapshots),
                                "snapshots": snapshots}}
