"""Plain-language lines for the KR/US sell notice (display only).

The stored sell_reason keeps its codes (exit_kind, journals and reviews parse
them); the channel notice says what happened in words. 삼표시멘트 (2026-10-08)
read "TIER1_STOPLOSS: price<=stop_loss(7400.5000)", "보유기간: 0일" and a
separate "슬롯 기준 손익" line, which hid that the stop has a 0.5% intraday
buffer, that the fill came after a fast drop, and what the loss meant for the
account.
"""
from __future__ import annotations

import re
from datetime import timedelta

from prism_core.slot_weight import slot_fraction

STOP_WICK_BUFFER = 0.005  # cores/oneil_fallback.py: stop_loss x 0.995 triggers the intraday stop

_STOP = re.compile(r"^TIER1_STOPLOSS: price<=stop_loss\(([0-9.]+)\)")
_ABS7 = re.compile(r"^TIER1_ABS7: loss (-?[0-9.]+)% <= (-?[0-9.]+)%")
_MA50 = re.compile(r"^TIER1\.5_MA50: below 50MA\(([0-9.]+)\) while losing \((-?[0-9.]+)%\)")
_TRAIL = re.compile(r"^TIER2_TRAIL: regime=\S+ peak=([0-9.]+) trail\(-([0-9]+)%\)=([0-9.]+) >= price")
_KIS = re.compile(r"^TIER0_EVENT:KIS_STATUS:\S+?\((.+)\)")


def _money(value, market):
    value = float(value)
    return f"${value:,.2f}" if market == "US" else f"{value:,.0f}원"


def plain_reason(reason, *, sell_price, market="KR"):
    """Deterministic exit codes in words; any other reason is returned unchanged."""
    text = str(reason or "")
    if m := _STOP.match(text):
        stop = float(m[1])
        return (f"손절선 이탈 — 손절선 {_money(stop, market)}(장중 0.5% 여유 {_money(stop * (1 - STOP_WICK_BUFFER), market)}) "
                f"아래로 내려가 {_money(sell_price, market)}에 매도했습니다.")
    if m := _ABS7.match(text):
        return f"최대 손실 한도 도달 — 매수가 대비 {float(m[1]):.2f}%로 손실 한도({float(m[2]):.0f}%)에 닿아 매도했습니다."
    if m := _MA50.match(text):
        return (f"손실 중 50일 이동평균선({_money(m[1], market)}) 아래로 내려가 매도했습니다 "
                f"(매수가 대비 {float(m[2]):.2f}%).")
    if m := _TRAIL.match(text):
        return (f"고점 대비 하락 — 보유 중 최고가 {_money(m[1], market)}에서 {m[2]}% 넘게 내려가"
                f"(기준 {_money(m[3], market)}) 매도했습니다.")
    if m := _KIS.match(text):
        return f"법인 이벤트 — 거래소 종목 상태가 '{m[1]}'(으)로 바뀌어 매도했습니다."
    return text


def return_line(profit_rate, scenario, *, label="수익률"):
    """'수익률: -5.43% (실제 투입 비중 68% 반영 시 -3.69%)'; no bracket for a full slot."""
    rate = float(profit_rate)
    arrow = "⬆️" if rate > 0 else "⬇️" if rate < 0 else "➖"
    line = f"{label}: {arrow} {rate:+.2f}%"
    fraction = slot_fraction(scenario)
    if fraction < 1:
        line += f" (실제 투입 비중 {round(fraction * 100)}% 반영 시 {rate * fraction:+.2f}%)"
    return line


def allocation_after_adds(scenario, *, profit_rate, market):
    """The allocation line only when adds changed the entry (average price); '' otherwise."""
    import json

    from prism_core.micro_split_live import allocation_line, record

    if isinstance(scenario, str):
        try:
            scenario = json.loads(scenario or "{}")
        except ValueError:
            return ""
    block = record(scenario) if isinstance(scenario, dict) else None
    if block is None or len(block.get("legs") or []) < 2:
        return ""
    return allocation_line(scenario, profit_rate=profit_rate, market=market, language="ko").rstrip("\n")


def holding_text(elapsed: timedelta):
    """'3일', or hours and minutes under one day (a stored 0 days read as 'not held')."""
    if elapsed.days >= 1:
        return f"{elapsed.days}일"
    minutes = max(0, int(elapsed.total_seconds() // 60))
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes}분" if (hours or minutes) else "1분 미만"
