"""Deterministic public rendering of the same descriptive batch evidence."""
import ipaddress
import math
import re
from urllib.parse import urlsplit


def _public_https(url):
    """Syntax/public-host guard only: no fetching or claim verification."""
    try:
        parsed = urlsplit(url)
        host = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or "." not in host or host.endswith((".local", ".localhost", ".internal"))):
            return False
        try:
            return ipaddress.ip_address(host).is_global
        except ValueError:
            return bool(re.fullmatch(r"[a-z0-9.-]+", host))
    except ValueError:
        return False


def _unresolved_numeric_citations(prose):
    definitions = {
        number for number, url in re.findall(r"(?m)^\s*\[(\d+)\]:\s*(https://\S+)", prose)
        if _public_https(url)
    }
    for match in re.finditer(r"\[(\d+(?:\s*[,;-]\s*\d+)*)\]", prose):
        inline = re.match(r"\((https://[^\s)]+)\)", prose[match.end():])
        if inline and _public_https(inline.group(1)):
            continue
        if match.group(1) not in definitions:
            return True
    return False


def public_market_analysis(prose, context, language="ko"):
    """Guard market prose before strategy/summary; source data needs no invented URL."""
    if not isinstance(context, dict) or not isinstance(context.get("market_intelligence"), dict):
        return prose
    if not isinstance(prose, str) or _unresolved_numeric_citations(prose):
        return ("### 4. 시장 분석\n\n출처 번호를 확인할 수 없는 시장 서술은 제외했습니다. 아래 공통 시장 근거를 확인하십시오."
                if language == "ko" else
                "### 4. Market Analysis\n\nMarket narrative with unresolved citations was omitted. Consult the shared market evidence below.")
    return prose


def public_macro_prose(context, language="ko"):
    """Suppress visibly unlinked prose only for the opted-in evidence packet.

    URL presence is not evidence of source truth or claim-level coverage.
    Structured macro decisions are never modified here.
    """
    prose = context.get("report_prose", "")
    if not isinstance(context.get("market_intelligence"), dict):
        return prose
    prose = prose if isinstance(prose, str) else ""
    urls = re.findall(r"https://[^\s<>\]\)]+", prose)
    linked = any(_public_https(url) for url in urls)
    if linked and not _unresolved_numeric_citations(prose):
        return prose
    return ("출처 연결이 확인되지 않은 거시 서술은 제외했습니다. 아래 공통 시장 근거의 지표를 확인하십시오."
            if language == "ko" else
            "Macro narrative without connected public sources was omitted. Consult the shared market evidence below.")


def _measured_return(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def market_report_context(context, language="ko"):
    if not isinstance(context, dict) or not isinstance(context.get("market_intelligence"), dict):
        return ""
    packet = context["market_intelligence"]
    ko = language == "ko"
    from regime_display import regime_label
    lines = ["### 공통 시장 근거" if ko else "### Shared market evidence", "",
             ("확정된 매매 국면: " if ko else "Authoritative trading regime: ")
             + regime_label(context.get("market_regime", "UNKNOWN"), language),
             ("아래 자료는 시장 설명용입니다. 가격 상대성과는 실제 자금 순유입이나 전시장 상승 종목 비율이 아닙니다."
              if ko else "Descriptive only. Relative price returns are not cash inflows or whole-market constituent breadth.")]
    lines.append("분석가의 분위기 설명이나 점수는 위 확정 매매 국면을 대체하지 않습니다." if ko else
                 "Analyst sentiment descriptions or scores do not replace the authoritative trading regime above.")
    rows = packet.get("rows", [])
    if isinstance(rows, list) and rows:
        lines.append(("완료 일봉 기준: " if ko else "Completed price date: ")
                     + str(packet.get("price_asof") or "UNKNOWN") + "; "
                     + str(packet.get("source") or "UNKNOWN"))
        rendered_rows = []
        unmeasured_optional = []
        for row in rows[:16]:
            if not isinstance(row, dict):
                continue
            measurements = {key: row.get(key) if isinstance(row.get(key), dict) else {}
                            for key in ('returns_pct', 'relative_spy_pp')}
            measured = any(_measured_return(data.get(str(day)))
                           for data in measurements.values() for day in (5, 20, 60))
            # SPY defines the comparison basis. Its unknown state must remain
            # visible, unlike entirely unmeasured optional style/sector proxies.
            if not measured and row.get('symbol') != 'SPY' and row.get('label') != 'benchmark':
                unmeasured_optional.append(str(row.get('symbol') or '?'))
                continue
            def values(key):
                data = measurements[key]
                return "/".join(str(data[str(day)]) if _measured_return(data.get(str(day))) else "?"
                                for day in (5, 20, 60))
            rendered_rows.append(f"- {row.get('symbol', '?')}: {values('returns_pct')} · {values('relative_spy_pp')}")
        if rendered_rows:
            lines.append("5/20/60거래일 수익률(%) · SPY 대비 초과수익률(%p)" if ko
                         else "5/20/60-session returns (%) · excess over SPY (pp)")
            lines.extend(rendered_rows)
        if unmeasured_optional:
            symbols = ', '.join(unmeasured_optional)
            count = len(unmeasured_optional)
            lines.append(f"미측정 선택 지표 {count}개(표시 생략): {symbols}." if ko else
                         f"{count} unmeasured optional indicator{'s' if count != 1 else ''} omitted: {symbols}.")
        if packet.get("input_sha256"):
            lines.append("Evidence ID: MI-" + str(packet["input_sha256"])[:16])
    participation = packet.get("participation") or context.get("market_participation")
    if isinstance(participation, dict):
        lines.append(("배치 관측 종목군(장중 값 포함 가능): " if ko else "Batch universe (may include intraday values): ")
                     + f"advance={participation.get('advance', '?')}, decline={participation.get('decline', '?')}, "
                     + f"unchanged={participation.get('unchanged', '?')}, "
                     + f"valid={participation.get('valid_count', '?')}/{participation.get('universe_count', '?')}, "
                     + f"missing={participation.get('missing_count', '?')}; asof={participation.get('asof', 'UNKNOWN')}")
    lines.append("미수집 항목은 미확인입니다. 추가 가점이나 기존 매수 조건 완화의 근거가 아닙니다." if ko
                 else "Uncollected fields remain unknown; this does not add a score or relax existing entry gates.")
    return "\n\n" + "\n".join(lines) + "\n"
