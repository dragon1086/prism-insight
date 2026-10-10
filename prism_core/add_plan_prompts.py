"""Per-report prompt appendices for micro-split add plans (KR/US, ko/en).

Appended to the per-report BUY message and the per-holding sell-review message
only while micro-split LIVE is on for the market, never to the shared agent
instructions, so every other prompt stays byte-identical (same pattern as
``prism_core.sector_trading_criteria.buy_sector_block``).
"""
from __future__ import annotations

from decimal import Decimal

from prism_core import add_plan as P

_RULES = {
    "ko": (
        "- 시나리오는 2~4개입니다. 각 시나리오에 근거 관점 lens를 하나 이상 붙이십시오(점수와 무관한 설명용): "
        "oneil(수익 중 피라미드형, 포켓 피벗·50일선 첫 눌림 후 반등·베이스 위 베이스 돌파, 피벗에서 5% 넘게 벌어지면 "
        "추격 금지, 분배일 증가 시 보수적), minervini(변동성 수축·거래량 감소 뒤 저위험 피벗, 10·21일선 과확장·"
        "클라이맥스 구간 증액 금지), druckenmiller(계획보다 강하게 작동하면 앞당기는 acceleration, 시장이 약한데 종목이 "
        "강하면 상대강도 증거를 더 요구하고 폭을 줄임), buffett(사업·실적 논리 유지 확인), quant_risk(무효 조건), "
        "systems(닫힌 조건 어휘).\n"
        "- 가격은 모두 이 종목의 실제 가격으로 쓰십시오. 고정 퍼센트 사다리는 쓰지 마십시오.\n"
        "- type: breakout, pullback_reclaim, new_closing_high, acceleration 중 하나.\n"
        "- trigger 조건 어휘(이 목록 밖 키가 있으면 그 시나리오는 실행되지 않습니다): price_above, "
        "zone_low·zone_high·reclaim_above(셋을 함께, zone_low < zone_high ≤ reclaim_above), "
        "new_closing_high_lookback(3~20세션 종가 고점), gap_up_min_pct(전일 종가 대비 시가 갭 %), "
        "hold_above_open_minutes(시가 위 유지 분, 5분 단위), volume_pace_min(같은 경과 시각 20일 평균 대비 누적 거래량 "
        "배수, 종가 확인이면 일 거래량 배수), volume_dry_up_max(최근 5세션 최저 거래량 ÷ 20일 평균), "
        "confirm(5m_close 또는 daily_close, 기본 5m_close), bars(5m_close일 때 연속 확인 5분봉 수, 기본 2), "
        "earliest_session(진입 세션=1), hold_sessions(가격 위 연속 종가 세션 수). 가격 기준(price_above, "
        "reclaim_above, new_closing_high_lookback, gap_up_min_pct) 중 하나는 반드시 있어야 합니다.\n"
        "- target_allocation: 1슬롯 대비 누적 목표 비중, 0.05 단위, 1.00 이하. 현재 비중보다 커야 하고, 한 번 늘리는 "
        "폭은 0.25 이하이며 직전 매수분보다 크지 않아야 합니다(피라미드형).\n"
        "- max_chase_pct: 트리거 가격 대비 추격 한도(%), 기본이자 최대 2.0. 그보다 위로 달아나면 사지 않습니다.\n"
        "- invalidation: close_below(이 가격 아래 종가면 모든 증액 취소), stall_sessions(진입 후 종가 고점이 이 세션 수 "
        "동안 갱신되지 않으면 취소, 2~20), note.\n"
        "- 코드 안전장치(시나리오가 넘을 수 없음): 평균 매수가보다 위일 때만 증액, 현재 유효 손절선(최초 손절가와 보유 "
        "손절가 중 높은 값)까지 밀려도 총손실이 최초 1슬롯 손절 위험 이내가 되도록 증액분을 자름, 매도·손절 신호가 "
        "나온 날은 증액 없음, 계획은 지정 세션 하루만 유효, 누적 비중은 1.00 이하.\n"
        "- 세션당 증액 횟수: 기본 1회입니다. 다만 가격이 최초 진입가보다 8% 이상 높고 거래량 속도(같은 경과 시각 20일 "
        "평균 대비 누적 거래량, 종가 확인이면 일 거래량)가 1.5배 이상이면 코드가 가속 구간으로 판정해 같은 세션에 "
        "2회까지 증액합니다. 두 번째 증액은 다른 시나리오가 충족되면 그 시나리오로, 없으면 그날 집행된 시나리오를 같은 "
        "트리거 가격·추격 한도로 한 단계 더(0.25 이하, 직전 매수분 이하) 집행합니다. 가속 판정은 시나리오 type"
        "(acceleration 포함)과 관계없이 가격과 거래량으로만 정하므로, 두 번째 증액만을 위한 시나리오는 쓰지 않아도 됩니다.\n"),
    "en": (
        "- Write 2 to 4 scenarios. Tag each with at least one review lens (explanation only, never a score): "
        "oneil (pyramid only while profitable; pocket pivot, first pullback to the 50-day line, base-on-base "
        "breakout; never chase more than 5% past a pivot; be conservative as distribution days rise), minervini "
        "(low-risk pivot after volatility contraction and volume dry-up; no adds when extended from the 10/21-day "
        "lines or in a climax run), druckenmiller (an acceleration scenario when the stock works better than "
        "planned; in a weak market require more relative-strength evidence and smaller adds), buffett (the business "
        "and earnings thesis still holds), quant_risk (invalidation), systems (closed condition vocabulary).\n"
        "- Use this stock's real price levels. Never use a fixed percentage ladder.\n"
        "- type: one of breakout, pullback_reclaim, new_closing_high, acceleration.\n"
        "- trigger vocabulary (any other key disables that scenario): price_above, zone_low/zone_high/reclaim_above "
        "(all three, zone_low < zone_high <= reclaim_above), new_closing_high_lookback (closing high of 3-20 "
        "sessions), gap_up_min_pct (open versus prior close, %), hold_above_open_minutes (minutes held above the "
        "open, multiple of 5), volume_pace_min (cumulative volume versus the 20-session average at the same elapsed "
        "time; the daily volume multiple for a daily_close confirmation), volume_dry_up_max (lowest volume of the "
        "last 5 sessions / 20-session average), confirm (5m_close or daily_close, default 5m_close), bars "
        "(consecutive 5-minute closes for 5m_close, default 2), earliest_session (entry session = 1), hold_sessions "
        "(consecutive closes above the level). One price level (price_above, reclaim_above, "
        "new_closing_high_lookback or gap_up_min_pct) is required.\n"
        "- target_allocation: cumulative target share of one slot, in 0.05 steps, at most 1.00, above the current "
        "allocation; one add is at most 0.25 and no larger than the previous buy (pyramid).\n"
        "- max_chase_pct: chase limit versus the trigger price in %, default and maximum 2.0; above it, no buy.\n"
        "- invalidation: close_below (a close below this price cancels every add), stall_sessions (cancel when the "
        "closing high since entry has not improved for this many sessions, 2-20), note.\n"
        "- Code safety rails no scenario can exceed: adds only above the average entry, the add is clipped so that a "
        "fall to the current effective stop (the higher of the initial stop and the holding's stop) loses no more "
        "than the initial one-slot stop risk, no add on a sell or stop signal day, a plan is valid for its one session only, and "
        "the cumulative allocation stays at or below 1.00.\n"
        "- Adds per session: one by default. When the price is at least 8% above the initial entry and the volume "
        "pace (cumulative volume versus the 20-session average at the same elapsed time; the daily volume for a "
        "close confirmation) is at least 1.5x, code marks an acceleration session and allows two adds in that "
        "session. The second add is another scenario that qualifies or, if none does, one more step of the scenario "
        "executed that day at the same trigger price and chase limit (at most 0.25 and no larger than the previous "
        "buy). Acceleration is decided from price and volume only, whatever the scenario type (acceleration "
        "included), so do not write a scenario just for the second add.\n"),
}

_EXAMPLE = {
    "KR": ('{"thesis_check": "사업·실적 논리 유지 여부 1줄", "invalidation": {"close_below": 48500, '
           '"stall_sessions": 5, "note": "..."}, "scenarios": [{"id": "breakout_1", "lens": ["oneil"], '
           '"type": "breakout", "trigger": {"price_above": 53200, "confirm": "5m_close", "bars": 2, '
           '"volume_pace_min": 1.5}, "target_allocation": 0.60, "max_chase_pct": 2.0, "rationale": "..."}, '
           '{"id": "pullback_1", "lens": ["minervini"], "type": "pullback_reclaim", "trigger": {"zone_low": 49800, '
           '"zone_high": 50600, "reclaim_above": 51200, "volume_dry_up_max": 0.8, "confirm": "daily_close"}, '
           '"target_allocation": 0.55, "rationale": "..."}]}'),
    "US": ('{"thesis_check": "one line on the business/earnings thesis", "invalidation": {"close_below": 48.50, '
           '"stall_sessions": 5, "note": "..."}, "scenarios": [{"id": "breakout_1", "lens": ["oneil"], '
           '"type": "breakout", "trigger": {"price_above": 53.20, "confirm": "5m_close", "bars": 2, '
           '"volume_pace_min": 1.5}, "target_allocation": 0.60, "max_chase_pct": 2.0, "rationale": "..."}, '
           '{"id": "pullback_1", "lens": ["minervini"], "type": "pullback_reclaim", "trigger": {"zone_low": 49.80, '
           '"zone_high": 50.60, "reclaim_above": 51.20, "volume_dry_up_max": 0.8, "confirm": "daily_close"}, '
           '"target_allocation": 0.55, "rationale": "..."}]}'),
}


def _lang(language):
    return "ko" if language == "ko" else "en"


def _money(value, market, language):
    if str(market).upper() == "US":
        return f"${float(value):,.2f}"
    return f"{float(value):,.0f}원" if language == "ko" else f"{float(value):,.0f} KRW"


def buy_block(market, language="ko", expected_initial=None):
    """BUY appendix asking for ``add_plan``; the caller appends it only while micro-split LIVE is on.

    The conviction tilt is deliberately never stated here (no score-inflation incentive);
    its add_plan targets are rebased in code (``add_plan.rebase_targets``).
    """
    lang, market = _lang(language), str(market).upper()
    initial = (f"{round(expected_initial * 100)}%" if expected_initial else None)
    if lang == "ko":
        sizing = ("최초 비중은 코드가 변동성(ATR)으로 정합니다(1슬롯의 30~80%"
                  + (f", 이 종목 예상 약 {initial}" if initial else "") + "). 증액은 그 위에서 1슬롯까지만 가능합니다.\n")
        return ("\n\n### 초분할 증액 시나리오 (add_plan)\n"
                "진입으로 판단하는 경우에만 기존 JSON에 \"add_plan\" 키를 함께 작성하십시오. add_plan은 진입 여부, "
                "buy_score, 점수 기준, 손절·목표가 판단을 바꾸지 않습니다. 진입하지 않으면 add_plan을 쓰지 마십시오.\n"
                f"- {sizing}"
                "- 이 계획은 진입 다음 세션 하루만 유효하고, 이후는 매일 보유 종목 점검이 다음 세션용 계획을 다시 세웁니다.\n"
                + _RULES["ko"] + "형식 예시:\n\"add_plan\": " + _EXAMPLE[market] + "\n")
    sizing = ("The first allocation is set by code from volatility (ATR), 30-80% of one slot"
              + (f", about {initial} expected for this stock" if initial else "") + "; adds may raise it up to one slot.\n")
    return ("\n\n### Micro-split add scenarios (add_plan)\n"
            "Only when you decide to enter, also write an \"add_plan\" key in the same JSON. add_plan never changes "
            "the entry decision, buy_score, score thresholds, stop or target. Omit it when you do not enter.\n"
            f"- {sizing}"
            "- The plan is valid for the session after the entry only; afterwards the daily holdings review writes "
            "the plan for each next session.\n"
            + _RULES["en"] + "Format example:\n\"add_plan\": " + _EXAMPLE[market] + "\n")


def _plan_summary(plan, market, language):
    if not plan:
        return "없음" if language == "ko" else "none"
    labels = "; ".join(P.scenario_label(s, market, language) for s in plan.get("scenarios") or [])
    return f"{plan.get('valid_for')} {plan.get('status')}" + (f" — {labels}" if labels else "")


def review_block(block, *, market, language, valid_for, stop_loss=None, pulse=None):
    """Holdings-review appendix asking for ``next_session_add_plan`` for a micro-split holding.

    ``pulse`` is the system Market Pulse (state, distribution days, window) or None. The block hands the
    model what the add plan needs so it does not look anything up for it: extra DB/price/news lookups
    pushed US micro-split sell reviews past the 180 s Codex limit (2026-10-06..10, 8 -> 18~26 tool calls).
    """
    lang, market = _lang(language), str(market).upper()
    allocation = round(float(block["allocation"]) * 100)
    legs = block["legs"]
    average = P.weighted_entry(legs)
    history = ", ".join(f"{leg['kind']} {round(float(leg['allocation']) * 100)}% @ "
                        f"{_money(leg['price'], market, lang)}" + (f" ({leg['scenario_id']})" if leg.get("scenario_id")
                                                                   else "") for leg in legs)
    stop = _money(stop_loss, market, lang) if stop_loss else ("미확인" if lang == "ko" else "unknown")
    current = _plan_summary(block.get("add_plan"), market, lang)
    room = max(Decimal(1) - Decimal(str(block["allocation"])), Decimal(0))
    if pulse:
        state, dd, window = pulse
        pulse_line = (f"시스템 Market Pulse: {state}, 분배일 {dd}회(최근 {window}세션).\n" if lang == "ko" else
                      f"System Market Pulse: {state}, distribution days {dd} (last {window} sessions).\n")
    else:
        pulse_line = ""
    if lang == "ko":
        return ("\n\n### 초분할 증액 계획 갱신 (next_session_add_plan)\n"
                f"이 종목은 초분할 보유 중입니다. 현재 비중 {allocation}% (1슬롯 기준, 남은 여유 {round(room * 100)}%p), "
                f"매수 기록: {history}. 평균 매수가 {_money(average, market, lang)}, 손절선 {stop}"
                "(최초 진입가 기준 손절 로직은 그대로입니다).\n"
                f"현재 증액 계획: {current}.\n"
                + pulse_line +
                "보유를 유지한다면 기존 JSON에 \"next_session_add_plan\" 키를 함께 쓰십시오. 매도 판단, 손절가, "
                "portfolio_adjustment 기준은 바뀌지 않습니다.\n"
                f"- 이 계획은 {valid_for} 세션 하루만 유효합니다. 키를 쓰지 않으면 그 세션에는 기존에 그 세션용으로 "
                "세운 계획이 없는 한 증액하지 않습니다.\n"
                "- 매도 판단에 이미 확인한 일봉·거래량, 위 매수 기록·현재 계획, 시장 국면과 Market Pulse·분배일을 "
                "반영하십시오. 증액 계획만을 위해 DB·시세·뉴스를 추가로 조회하지 마십시오(매도 판단은 시간 제한 안에 "
                "끝나야 합니다). "
                "시장 조건만으로 증액을 일괄 금지하지 말고, 약한 시장에서는 상대강도 증거를 더 요구하고 폭을 줄이십시오.\n"
                "- 계획보다 강하면(갭 상승 유지, 거래량 급증 등) acceleration 시나리오를 넣거나 트리거를 앞당기십시오. "
                "같은 세션의 두 번째 증액은 아래 가속 구간 규칙에 따라 코드가 정합니다.\n"
                "- 사업 논리가 훼손됐거나 며칠 힘없이 횡보하면 {\"cancel\": true, \"reason\": \"...\"}로 모든 증액을 "
                "취소하십시오. 작은 비중 그대로 기존 손절·청산 규칙을 따릅니다.\n"
                + _RULES["ko"] + "형식 예시:\n\"next_session_add_plan\": " + _EXAMPLE[market] + "\n")
    return ("\n\n### Micro-split add plan refresh (next_session_add_plan)\n"
            f"This is a micro-split holding: allocation {allocation}% of one slot (room {round(room * 100)}%p), "
            f"buys: {history}. Average entry {_money(average, market, lang)}, stop {stop} (the stop logic stays on "
            "the initial entry).\n"
            f"Current add plan: {current}.\n"
            + pulse_line +
            "If you keep holding, also write a \"next_session_add_plan\" key in the same JSON. The sell decision, stop "
            "and portfolio_adjustment rules are unchanged.\n"
            f"- The plan is valid for the {valid_for} session only. Without the key there is no add in that session "
            "unless a plan for that session already exists.\n"
            "- Use the daily bars and volume you already checked for the sell decision, the buys and current plan "
            "above, the market regime and the Market Pulse / distribution days. Do not run extra DB, price or news "
            "lookups just for the add plan (the sell review has a time limit). Never ban adds on market conditions alone; in a "
            "weak market require more relative-strength evidence and smaller adds.\n"
            "- If the stock is stronger than planned (gap held, volume surge), add an acceleration scenario or bring "
            "triggers forward. A second add in the same session is decided by code under the acceleration rule "
            "below.\n"
            "- If the business thesis is impaired or the stock has stalled weakly for days, cancel every add with "
            "{\"cancel\": true, \"reason\": \"...\"}; the small position then follows the existing stop/exit rules.\n"
            + _RULES["en"] + "Format example:\n\"next_session_add_plan\": " + _EXAMPLE[market] + "\n")
