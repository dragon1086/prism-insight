"""Deterministic public rendering of the same descriptive batch evidence."""


def market_report_context(context, language="ko"):
    if not isinstance(context, dict) or not isinstance(context.get("market_intelligence"), dict):
        return ""
    packet = context["market_intelligence"]
    ko = language == "ko"
    from regime_display import regime_label
    lines = ["### 공통 시장 근거" if ko else "### Shared market evidence",
             ("확정된 매매 국면: " if ko else "Authoritative trading regime: ")
             + regime_label(context.get("market_regime", "UNKNOWN"), language),
             ("아래 자료는 시장 설명용입니다. 가격 상대성과는 실제 자금 순유입이나 전시장 상승 종목 비율이 아닙니다."
              if ko else "Descriptive only. Relative price returns are not cash inflows or whole-market constituent breadth.")]
    rows = packet.get("rows", [])
    if isinstance(rows, list) and rows:
        lines.append(("완료 일봉 기준: " if ko else "Completed price date: ")
                     + str(packet.get("price_asof") or "UNKNOWN") + "; "
                     + str(packet.get("source") or "UNKNOWN"))
        lines.append("5/20/60거래일 수익률(%) · SPY 대비 초과수익률(%p)" if ko
                     else "5/20/60-session returns (%) · excess over SPY (pp)")
        for row in rows[:16]:
            if not isinstance(row, dict):
                continue
            def values(key):
                data = row.get(key) or {}
                return "/".join(str(data.get(str(day), "?")) for day in (5, 20, 60))
            lines.append(f"- {row.get('symbol', '?')}: {values('returns_pct')} · {values('relative_spy_pp')}")
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
