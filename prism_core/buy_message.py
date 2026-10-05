"""Telegram buy message shared by KR and US (presentation only).

Korean for both markets: the US channel is Korean too and broadcasts are translated
downstream. Nothing here changes a decision, an order or the BUY prompt inputs.
The SELL-policy template that apply_buy_scenario_contract installs as sell_triggers
is the same five sentences for every entry, so it is not repeated in the message.
"""
from __future__ import annotations

import re
from typing import Any

from prism_core.trading_scenario_contract import format_optional_number

# US trade-value text is also a BUY prompt input, so it is translated only here.
_US_TRADE_VALUE = re.compile(
    r"Trading value: \$([\d.,]+)M \(prev: \$([\d.,]+)M, change: ([▲▼=])([\d.]+)%\), "
    r"Volume ratio: ([\d.]+)x")


def _price(value: Any, market: str) -> str:
    if market == "US":
        text = format_optional_number(value, ",.2f")
        return text if text == "미확인" else f"${text}"
    text = format_optional_number(value, ",.0f")
    return text if text == "미확인" else f"{text}원"


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "").replace("$", "").replace("원", ""))
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _pct_from(price: Any, base: float) -> str:
    number = _number(price)
    if number is None or not base:
        return ""
    return f"{(number / base - 1) * 100:+.1f}%"


def _trade_value(text: str) -> str:
    match = _US_TRADE_VALUE.fullmatch(text.strip())
    if not match:
        return text.strip()
    now, prev, arrow, change, ratio = match.groups()
    return (f"${float(now):,.1f}M (전일 ${float(prev):,.1f}M, {arrow}{change}%), "
            f"거래량 {ratio}배")


def _trigger_lines(text: str) -> list[str]:
    body = text.strip().removeprefix("📡").strip()
    return [part.strip() for part in body.split(" / ") if part.strip()]


def _key_levels(levels: dict, current_price: float, market: str, parse) -> list[str]:
    lines = []
    resistance = [(name, parse(levels.get(key, 0)))
                  for name, key in (("1차", "primary_resistance"), ("2차", "secondary_resistance"))]
    support = [(name, parse(levels.get(key, 0)))
               for name, key in (("1차", "primary_support"), ("2차", "secondary_support"))]
    shown = [f"{name} {_price(value, market)}" for name, value in resistance if value]
    if shown:
        lines.append("  저항 " + " · ".join(shown))
    lines.append(f"  ━ 현재 {_price(current_price, market)} ━")
    shown = [f"{name} {_price(value, market)}" for name, value in support if value]
    if shown:
        lines.append("  지지 " + " · ".join(shown))
    return lines


def render_buy_message(*, market: str, company_name: str, ticker: str, current_price: float,
                       scenario: dict, rank_change_msg: str = "", trigger_win_rate: str = "",
                       parse_price=None, add_entry: tuple[int, float] | None = None) -> str:
    """First entry, or an add-on entry when add_entry=(entry_no, new_avg_price)."""
    from prism_core.micro_split_live import entry_message_block
    from prism_core.reentry_v3_live import entry_message_line as reentry_line

    market = str(market).upper()
    parse = parse_price or _number
    target, stop = scenario.get("target_price"), scenario.get("stop_loss")
    period = scenario.get("investment_period") or "단기"
    sector = scenario.get("sector") or "업종 미확인"
    target_pct, stop_pct = _pct_from(target, current_price), _pct_from(stop, current_price)

    if add_entry:
        entry_no, new_avg = add_entry
        lines = [f"📈 추가 진입 ({entry_no}차) | {company_name} ({ticker})",
                 f"💵 이번 진입가 {_price(current_price, market)} · 누적 평단가 {_price(new_avg, market)}",
                 "⚠️ 포트폴리오 비중이 늘었습니다 (독립 슬롯 1개 사용)"]
    else:
        lines = [f"📈 신규 매수 | {company_name} ({ticker})",
                 f"💵 매수가 {_price(current_price, market)}"]
    lines.append(f"🎯 목표가 {_price(target, market)}" + (f" ({target_pct})" if target_pct else ""))
    lines.append(f"⛔ 손절가 {_price(stop, market)} ("
                 + (f"{stop_pct}, " if stop_pct else "") + "장중 이탈 시 즉시 매도)")
    lines.append(f"🏷 {sector} · {period}")

    if (scenario.get("regime_entry_policy") or {}).get("mode") == "rebound_pilot":
        lines += ["", "🧪 주문 예산 상한: 정상 예산의 50% (상승 전환 파일럿)"]
    block = entry_message_block(scenario, market)
    if block:
        lines += [""] + block
    reentry = reentry_line(scenario, market, language="ko").strip()
    if reentry:
        lines += [""] + reentry.splitlines()

    facts = []
    trigger = _trigger_lines(trigger_win_rate) if trigger_win_rate else []
    if trigger:
        facts.append("📡 같은 신호 과거 성적")  # not "트리거": firebase_bridge.detect_type reads it as an alert
        facts += [f"  • {line}" for line in trigger]
    if rank_change_msg:
        facts.append(f"📊 거래대금 {_trade_value(rank_change_msg)}")
    if facts:
        lines += [""] + facts

    def section(title, body):
        if body:
            lines.extend(["", title, str(body).strip()])

    section("💡 매수 근거", scenario.get("rationale") or "정보 없음")
    reflection = scenario.get("journal_reflection") or {}
    if isinstance(reflection, dict):
        if reflection.get("recent_exit_caution"):
            lines.append(f"⚠️ 최근 매도 주의: {reflection['recent_exit_caution']}")
        if reflection.get("applied_lessons"):
            lines.append(f"📒 매매일지 반영: {reflection['applied_lessons']}")
    adjustment = scenario.get("score_adjustment") or {}
    if isinstance(adjustment, dict) and adjustment.get("value"):
        reasons = ", ".join(adjustment.get("reasons", []) or [])
        lines.append(f"📊 경험 기반 점수조정: {adjustment['value']:+d}점 ({reasons})")
    section("📐 밸류에이션", scenario.get("valuation_analysis"))
    section("🏭 업종 전망", scenario.get("sector_outlook"))

    plan = scenario.get("trading_scenarios")
    if isinstance(plan, dict):
        if plan.get("key_levels"):
            lines += ["", "📍 가격대"] + _key_levels(plan["key_levels"], current_price, market, parse)
        hold = [c for c in plan.get("hold_conditions") or [] if str(c).strip()]
        if hold:
            lines += ["", "✋ 보유 조건"] + [f"  • {c}" for c in hold]
        if plan.get("portfolio_context"):
            lines += ["", "💼 비중 메모", f"  {plan['portfolio_context']}"]
    return "\n".join(lines) + "\n"
