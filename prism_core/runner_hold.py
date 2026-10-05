"""Runner hold rule (O'Neil 8-week rule, R5 variant), KR/US: pure detection, sell guard and prompt text. No I/O.

Detection (first completed CLOSE at or above +20% over the initial entry — the first
micro-split leg, else the row's buy_price — within 15 sessions of the entry session):

  * RUNNER   when that close came on session 4..15 (not a 1-3 session spike) AND the
             close was at most 1.40 x its own 50-day MA;
  * EXCLUDED otherwise (fast spike, extended far above the 50-day MA, or no 50-day MA).
             Excluded holdings keep the existing exit logic unchanged.

Phases of a runner (the AI trailing stop never applies to a runner):

  * HOLD     (through ``hold_until`` = entry session + 40 sessions): sell only on a
             completed close below the 50-day MA or below the initial entry (breakeven);
  * EXTENDED (after ``hold_until``): additionally on a completed close below the 20-day
             MA — then the normal sell rules apply again.

In both phases the intraday hard stop (row stop_loss) is the initial entry: it is set
to the entry when the runner is found, even when an earlier AI trailing raise had put
it higher (the one explicit exception to the one-way stop ratchet, user decision
2026-10-04). Confirmed corporate events (KIS status TIER0, prompt core-0 tender
offer/delisting) stay valid exits.
Design, autopsy numbers and user decisions: docs/RUNNER_HOLD_RULE_ko.md.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

VERSION = "runner-hold-r5-v1"
SCENARIO_KEY = "runner"
RUNNER, EXCLUDED = "RUNNER", "EXCLUDED"
GAIN_THRESHOLD = Decimal("0.20")
MIN_SESSIONS = 4          # +20% on session 1-3 is a spike, not a runner
DETECT_SESSIONS = 15
MAX_MA50_EXTENSION = Decimal("1.40")
HOLD_SESSIONS = 40
MA_LONG, MA_SHORT = 50, 20
# A daily bar is final this long after the regular close (KR 16:00 KST, US 16:30 ET).
CLOSE_BUFFER = timedelta(minutes=30)
# holdings.buy_date is the server's naive local time (KST) in both markets.
BUY_DATE_TZ = ZoneInfo(os.getenv("RUNNER_HOLD_BUY_DATE_TZ", "Asia/Seoul"))
CORPORATE_PREFIXES = ("TIER0_EVENT", "[법인이벤트]", "[CORP_EVENT]")
EXIT_CODES = ("MA50_CLOSE", "BREAKEVEN_CLOSE", "MA20_CLOSE")
HOLD, EXTENDED = "HOLD", "EXTENDED"

# Reason buckets for the [RUNNER_HOLD] log only (never for the decision itself).
_REASON_BUCKETS = (
    ("TRAILING", ("tier2", "trailing", "트레일링")),
    ("TARGET", ("tier3", "target", "목표가", "익절")),
    ("MA20", ("20일선", "20-day", "20ma", "ma20", "20 ma")),
    ("OVERHEAT", ("과열", "overheat", "overextend", "rsi", "급등", "climax")),
    ("MOMENTUM", ("모멘텀", "momentum")),
    ("TIME", ("보유 기간", "보유기간", "시간", "holding period", "days", "time")),
    ("SECTOR", ("섹터", "업종", "sector")),
    ("TREND", ("추세", "trend", "tier1.5")),
)


def enabled(market):
    """RUNNER_HOLD_ENABLED (default true) for markets in RUNNER_HOLD_MARKETS (default KR,US)."""
    flag = os.getenv("RUNNER_HOLD_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}
    markets = {m.strip().upper() for m in os.getenv("RUNNER_HOLD_MARKETS", "KR,US").split(",") if m.strip()}
    return flag and str(market).upper() in markets


def _dec(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() and number > 0 else None


def _num(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _sessions():
    from prism_core.add_plan import SESSIONS
    return SESSIONS


def load_scenario(scenario):
    if isinstance(scenario, dict):
        return scenario
    try:
        loaded = json.loads(scenario or "{}")
    except (TypeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def record(scenario):
    """The stored runner block (RUNNER or EXCLUDED) of a scenario (dict or JSON), or None."""
    block = load_scenario(scenario).get(SCENARIO_KEY)
    return block if isinstance(block, dict) and block.get("version") == VERSION else None


def entry_reference(scenario, buy_price):
    """Initial entry: the first micro-split leg, else the row's buy_price (which stays the initial entry)."""
    from prism_core.micro_split_live import record as micro_record
    block = micro_record(load_scenario(scenario))
    if block is not None and block.get("legs"):
        first = _num(block["legs"][0].get("price"))
        if first is not None:
            return first
    return _num(buy_price)


def local_today(market, now):
    return now.astimezone(_sessions()[str(market).upper()][0]).date()


def completed_bars(bars, *, market, now):
    """Valid daily bars of completed sessions, oldest first (today's bar only after close + 30 min)."""
    tz, _, close_at, _ = _sessions()[str(market).upper()]
    local = now.astimezone(tz)
    today = local.date().isoformat()
    after_close = local >= datetime.combine(local.date(), close_at, tz) + CLOSE_BUFFER
    rows = []
    for bar in bars or []:
        day = str(bar.get("date") or "")[:10]
        if _num(bar.get("close")) is None or not day:
            continue
        if day < today or (day == today and after_close):
            rows.append(dict(bar, date=day))
    rows.sort(key=lambda b: b["date"])
    return rows


def entry_session(market, buy_date):
    """The session an entry belongs to (US reserved fills after the close belong to the next session)."""
    from prism_core.add_plan import entry_session_date
    naive = datetime.strptime(str(buy_date)[:19], "%Y-%m-%d %H:%M:%S")
    return entry_session_date(market, naive.replace(tzinfo=BUY_DATE_TZ).isoformat())


def session_after(market, start, count):
    """The trading session ``count`` sessions after ``start`` (exchange calendar)."""
    from prism_core.add_plan import next_session
    day = start if isinstance(start, date) else date.fromisoformat(str(start))
    for _ in range(count):
        day = next_session(market, day)
    return day


def sessions_since(bars, start, upto):
    """Completed sessions after ``start`` up to and including ``upto`` (entry session = 0)."""
    start, upto = str(start), str(upto)
    return sum(1 for b in bars if start < b["date"] <= upto)


def moving_average(bars, index, window):
    """Simple MA of the ``window`` closes ending at ``bars[index]`` (inclusive), or None."""
    if index + 1 < window:
        return None
    closes = [float(b["close"]) for b in bars[index + 1 - window:index + 1]]
    return sum(closes) / window


def detect(bars, *, entry_ref, entry_session):
    """Classify the first completed close >= +20% over ``entry_ref`` within sessions 0..15.

    Returns None when no such close exists (or the bars do not cover the entry
    session, so the session count is unknown); otherwise a dict with ``status``
    RUNNER or EXCLUDED and ``reason`` (SPIKE_FAST, EXTENDED_FROM_MA50, MA50_UNKNOWN).
    Only closes count: an intraday high above +20% is not a runner.
    """
    ref = _dec(entry_ref)
    start = str(entry_session)
    if ref is None or not bars or bars[0]["date"] > start:
        return None
    threshold = ref * (Decimal(1) + GAIN_THRESHOLD)
    count = 0
    for index, bar in enumerate(bars):
        if bar["date"] < start:
            continue
        if bar["date"] > start:
            count += 1
        if count > DETECT_SESSIONS:
            return None
        close = _dec(bar["close"])
        if close is None or close < threshold:
            continue
        ma50 = moving_average(bars, index, MA_LONG)
        extension = close / Decimal(str(ma50)) if ma50 else None
        found = {"since": bar["date"], "session": count, "trigger_close": float(close),
                 "gain_pct": round(float(close / ref - 1) * 100, 2),
                 "ma50_at_trigger": round(ma50, 4) if ma50 else None,
                 "ma50_extension": round(float(extension), 4) if extension is not None else None}
        if count < MIN_SESSIONS:
            return dict(found, status=EXCLUDED, reason="SPIKE_FAST")
        if extension is None:
            return dict(found, status=EXCLUDED, reason="MA50_UNKNOWN")
        if extension > MAX_MA50_EXTENSION:
            return dict(found, status=EXCLUDED, reason="EXTENDED_FROM_MA50")
        return dict(found, status=RUNNER, reason="RUNNER")
    return None


def new_record(detection, *, market, entry_ref, entry_session, detected_at):
    block = {"version": VERSION, "market": str(market).upper(), "status": detection["status"],
             "reason": detection["reason"], "entry_ref": float(entry_ref), "entry_session": str(entry_session),
             "since": detection["since"], "session": detection["session"],
             "trigger_close": detection["trigger_close"], "gain_pct": detection["gain_pct"],
             "ma50_at_trigger": detection["ma50_at_trigger"], "ma50_extension": detection["ma50_extension"],
             "detected_at": detected_at}
    if detection["status"] == RUNNER:
        block.update(peak_close=detection["trigger_close"],
                     hold_until=session_after(market, entry_session, HOLD_SESSIONS).isoformat(),
                     stop_set_to=None, stop_set_from=None, stop_set_at=None)
    return block


def peak_close(bars, entry_session):
    closes = [b["close"] for b in bars if b["date"] >= str(entry_session) and _num(b.get("close"))]
    return max(closes) if closes else None


def phase(block, today):
    """HOLD through hold_until, EXTENDED after it (until a close-below-MA20 exit), None for non-runners."""
    if not block or block.get("status") != RUNNER:
        return None
    return HOLD if str(today) <= str(block.get("hold_until") or "") else EXTENDED


def runner_exit(bars, entry_ref, current_phase=HOLD):
    """(exit code or None, facts) on the latest completed bar: close below the initial entry
    (BREAKEVEN_CLOSE) or its 50-day MA (MA50_CLOSE); in EXTENDED also below its 20-day MA
    (MA20_CLOSE). A missing moving average is unknown, never an exit."""
    if not bars:
        return None, {"status": "UNKNOWN", "reason": "NO_BARS"}
    last = bars[-1]
    close = float(last["close"])
    ma50 = moving_average(bars, len(bars) - 1, MA_LONG)
    ma20 = moving_average(bars, len(bars) - 1, MA_SHORT)
    entry = _num(entry_ref)
    facts = {"status": "OK" if ma50 is not None else "UNKNOWN", "date": last["date"], "close": close,
             "ma50": round(ma50, 4) if ma50 is not None else None,
             "ma20": round(ma20, 4) if ma20 is not None else None}
    if entry is not None and close < entry:
        return "BREAKEVEN_CLOSE", facts
    if ma50 is not None and close < ma50:
        return "MA50_CLOSE", facts
    if current_phase == EXTENDED and ma20 is not None and close < ma20:
        return "MA20_CLOSE", facts
    return None, facts


def hard_stop_hit(current_price, stop_loss, buy_price):
    """Raised stop (or the original stop / absolute -7%) reached at the current price."""
    price, stop, entry = _num(current_price), _num(stop_loss), _num(buy_price)
    if price is None:
        return False
    return bool((stop is not None and price <= stop) or (entry is not None and price <= entry * 0.93))


def is_corporate_event(reason):
    """Official corporate-event sell: a known prefix within the first 40 characters,
    ignoring markdown emphasis the LLM may add (``**[법인이벤트]**``, ``핵심-0: [법인이벤트]``)."""
    head = str(reason or "").replace("*", "").replace("`", "").strip()[:40]
    return any(prefix in head for prefix in CORPORATE_PREFIXES)


def classify_reason(reason):
    text = str(reason or "").lower()
    for code, words in _REASON_BUCKETS:
        if any(word in text for word in words):
            return code
    return "OTHER"


def sell_verdict(*, protected, exit_code, current_price, stop_loss, buy_price, reason):
    """(allow, code) for a proposed sell. Only a protected runner restricts sells."""
    if not protected:
        return True, "NOT_RUNNER"
    if exit_code in EXIT_CODES:
        return True, exit_code
    if hard_stop_hit(current_price, stop_loss, buy_price):
        return True, "HARD_STOP"
    if is_corporate_event(reason):
        return True, "CORPORATE_EVENT"
    return False, classify_reason(reason)


def stop_target(block, current_stop, current_price):
    """The runner's intraday floor (initial entry) when the row stop differs and the price is above it.

    Raises a lower stop and also resets a higher AI trailing stop down to the entry:
    the runner rule replaces the trailing stop (explicit ratchet exception). A runner
    found while the price is unknown or at/below the entry keeps its stop until the price is
    back above it, so setting the floor never sells by itself; the breakeven-close exit
    still protects it.
    """
    target = _num((block or {}).get("entry_ref"))
    if target is None:
        return None
    stop = _num(current_stop) or 0.0
    price = _num(current_price)
    if price is None or abs(stop - target) <= target * 1e-9 or price <= target:
        return None
    return target


def _money(value, market):
    number = _num(value)
    if number is None:
        return "미확인"
    return f"${number:,.2f}" if str(market).upper() == "US" else f"{number:,.0f}원"


def _money_en(value, market):
    number = _num(value)
    if number is None:
        return "unknown"
    return f"${number:,.2f}" if str(market).upper() == "US" else f"{number:,.0f} KRW"


def _mmdd(day):
    return f"{str(day)[5:7]}/{str(day)[8:10]}"


def exit_reason(view, market, language):
    """Sell reason text for the deterministic runner exit (prefix RUNNER_MA50 keeps exit_kind trend_exit)."""
    facts = view.get("exit_facts") or {}
    block = view.get("record") or {}
    code = view.get("exit")
    if language == "en":
        money = _money_en
        why = {"BREAKEVEN_CLOSE": f"below the initial entry {money(block.get('entry_ref'), market)} (breakeven)",
               "MA20_CLOSE": f"below the 20-day MA {money(facts.get('ma20'), market)} after the hold window",
               }.get(code, f"below the 50-day MA {money(facts.get('ma50'), market)}")
        return (f"RUNNER_MA50: runner hold rule exit — completed close {money(facts.get('close'), market)} "
                f"({facts.get('date')}) {why}.")
    why = {"BREAKEVEN_CLOSE": f"최초 매수가 {_money(block.get('entry_ref'), market)}(본전) 아래",
           "MA20_CLOSE": f"보유 기한 이후 20일선 {_money(facts.get('ma20'), market)} 아래",
           }.get(code, f"50일선 {_money(facts.get('ma50'), market)} 아래")
    return (f"RUNNER_MA50: 주도주 보유 규칙 매도 — {facts.get('date')} 확정 종가 {_money(facts.get('close'), market)}, "
            f"{why}로 마감했습니다.")


def blocked_reason(code, reason, language):
    original = " ".join(str(reason or "").split())[:160]
    if language == "en":
        return f"[RUNNER_HOLD] sell blocked ({code}); runner held until the 50-day MA or the entry breaks: {original}"
    return (f"[RUNNER_HOLD] 주도주 보유 규칙으로 매도 보류({code}), 50일선·최초 매수가 이탈 전까지 보유합니다: "
            f"{original}")


def prompt_block(view, *, market, language):
    """Holdings-review appendix for a protected runner; '' otherwise (non-runner prompts stay byte-identical)."""
    if not view or not view.get("phase") or not view.get("record"):
        return ""
    block, facts, ex = view["record"], view.get("facts") or {}, view.get("exit_facts") or {}
    market = str(market).upper()
    en = language == "en"
    money = _money_en if en else _money
    gain_now = facts.get("gain_now_pct")
    ma50 = ex.get("ma50")
    price = _num(facts.get("current_price"))
    distance = f"{(price / ma50 - 1) * 100:+.1f}%" if price and ma50 else ("unknown" if en else "미확인")
    sessions = facts.get("sessions_since_entry")
    extended = view["phase"] == EXTENDED
    if en:
        status = ("not met on the latest completed bar" if ex.get("status") == "OK"
                  else "cannot be verified (daily bars unavailable)")
        window = (f"The 40-session hold window ended on {block['hold_until']}; until a completed close below the "
                  "20-day MA this holding is still sold only in the cases below, then the normal SELL rules apply."
                  if extended else
                  f"Until {block['hold_until']} (40 sessions after the entry, O'Neil's 8-week rule) this holding is "
                  "sold only in the cases below; after that date a completed close below the 20-day MA is added as "
                  "an exit, then the normal SELL rules apply.")
        exits = ("(1) a completed close below the 50-day MA, (2) a completed close below the initial entry "
                 "(breakeven)" + (", (3) a completed close below the 20-day MA" if extended else "")
                 + " — the system checks these deterministically and sells; the intraday hard stop at the initial "
                 "entry — the system executes it. An officially confirmed corporate event (Core-0) remains a sell "
                 "reason.")
        return (
            "\n\n### Runner hold rule (takes precedence for this holding)\n"
            f"This holding is a runner: {block['session']} sessions after the entry its completed close reached "
            f"{block['gain_pct']:+.1f}% over the initial entry {money(block['entry_ref'], market)} without being an "
            "overextended spike. PRISM's purpose is to ride the few big winners that grow the account in steps; "
            "selling a runner early for a small gain near the target costs far more than holding through normal "
            f"pullbacks. {window}\n"
            f"- Facts: current price {money(price, market)} ("
            + (f"{gain_now:+.1f}%" if gain_now is not None else "unknown") + " vs the initial entry), "
            f"{sessions if sessions is not None else 'unknown'} sessions since the entry, 50-day MA on completed "
            f"closes {money(ma50, market)} (price {distance} from it), 20-day MA {money(ex.get('ma20'), market)}, "
            f"latest completed close {money(ex.get('close'), market)} ({ex.get('date') or 'unknown'}), stop "
            f"{money(facts.get('stop_loss'), market)} (the initial entry), hold window until {block['hold_until']}. "
            f"Exit condition: {status}.\n"
            f"- The only valid sells: {exits}\n"
            "- Not sell reasons while the rule is on: target reached (in any regime, including the sideways/bear "
            "'exit at the target'), overheating, a short-term surge or RSI, slowing momentum, a 20-day MA break "
            + ("" if extended else "(before the hold window ends) ") +
            "or the trend-weakness composite, a trailing-stop break, holding period or time review, sector weakness. "
            "The stored BUY scenario sell_triggers and the matching parts of the shared SELL instruction (target "
            "milestone, trailing stop, trend weakness, take-profit and time management) do not apply to this holding. "
            "A should_sell=true for such a reason is turned into a hold by the system. Keep should_sell=false and "
            "write the hold rationale.\n"
            "- The system manages this position's stop and target: the stop is the initial entry and the shared "
            "trailing-stop rules (activation at +5%, peak x0.92/x0.95, ratchet, adjustment threshold) are superseded "
            "for this holding. Return portfolio_adjustment.needed=false with new_stop_loss and new_target_price null; "
            "any adjustment is ignored.\n"
            "- For a micro-split holding the next_session_add_plan request above stays valid: a runner is the "
            "position to build up. Its sentence 'the sell decision, stop and portfolio_adjustment rules are "
            "unchanged' is replaced by this rule for this holding.\n")
    status = "최근 확정 일봉 기준 미충족" if ex.get("status") == "OK" else "확인 불가(일봉 데이터 없음)"
    window = (f"매수 후 40거래일 보유 기한({block['hold_until']})은 지났습니다. 확정 종가가 20일선 아래로 마감할 때까지는 "
              "아래 경우에만 매도하고, 그 뒤에는 기존 매도 규칙으로 돌아갑니다." if extended else
              f"그래서 {block['hold_until']}까지(매수 후 40거래일, 오닐의 8주 보유 규칙) 아래 경우가 아니면 매도하지 "
              "않습니다. 그 뒤에는 확정 종가 20일선 이탈이 매도 조건으로 더해지고, 그다음에는 기존 매도 규칙으로 "
              "돌아갑니다.")
    exits = ("(1) 확정 종가가 50일선 아래로 마감한 경우, (2) 확정 종가가 최초 매수가(본전) 아래로 마감한 경우"
             + (", (3) 확정 종가가 20일선 아래로 마감한 경우" if extended else "")
             + " — 시스템이 결정론적으로 확인해 매도합니다. 장중에 최초 매수가(손절가) 아래로 내려가면 하드스탑이 자동 "
             "실행합니다. 공식 확인된 법인 이벤트(핵심-0)는 그대로 매도 사유입니다.")
    return (
        "\n\n### 주도주 보유 규칙 (이 종목에 우선 적용)\n"
        f"이 종목은 매수 후 {block['session']}거래일 만에 확정 종가가 최초 매수가 {money(block['entry_ref'], market)} 대비 "
        f"{block['gain_pct']:+.1f}%에 도달했고, 단기 급등으로 50일선에서 지나치게 멀어진 경우도 아닌 주도주입니다. "
        "PRISM의 목적은 이런 소수의 크게 가는 종목을 끝까지 타서 자산을 계단식으로 키우는 것입니다. 목표가 근처의 작은 "
        f"이익을 챙기려고 주도주를 일찍 파는 비용이 정상적인 눌림을 견디는 비용보다 훨씬 큽니다. {window}\n"
        f"- 현재 사실: 현재가 {money(price, market)}(최초 매수가 대비 "
        + (f"{gain_now:+.1f}%" if gain_now is not None else "미확인") + "), "
        f"매수 후 {sessions if sessions is not None else '미확인'}거래일, 확정 종가 기준 50일선 {money(ma50, market)}"
        f"(현재가와의 거리 {distance}), 20일선 {money(ex.get('ma20'), market)}, 직전 확정 종가 "
        f"{money(ex.get('close'), market)}({ex.get('date') or '미확인'}), 손절가 {money(facts.get('stop_loss'), market)}"
        f"(최초 매수가), 보유 기한 {block['hold_until']}. 매도 조건: {status}.\n"
        f"- 매도가 유효한 경우는 다음뿐입니다. {exits}\n"
        "- 이 규칙이 적용되는 동안에는 다음이 매도 사유가 아닙니다: 목표가 도달(시장 국면과 무관하며, 횡보·약세 국면의 "
        "'도달 즉시 전량 매도'도 포함), 과열·단기 급등·RSI, 모멘텀 둔화, "
        + ("" if extended else "(보유 기한 전의) ") +
        "20일선 이탈과 추세 약화 복합 조건, trailing stop 이탈, 보유 기간·시간 점검, 업종 약세. 저장된 매수 시나리오의 "
        "sell_triggers와 공유 매도 지침의 해당 항목(익절 마일스톤, trailing stop, 추세 약화, 익절, 시간 관리)은 이 종목에 "
        "적용하지 않습니다. 이런 이유로 should_sell=true를 내도 시스템이 보유로 바꿉니다. should_sell은 false로 두고 보유 "
        "근거를 쓰십시오.\n"
        "- 이 종목의 손절가와 목표가는 시스템이 관리합니다. 손절가는 최초 매수가이고, 공유 지침의 trailing stop 규칙"
        "(+5% 활성화, 고점×0.92/×0.95, 래칫, 조정 임계값)은 이 종목에 적용하지 않습니다. portfolio_adjustment는 "
        "needed=false, new_stop_loss와 new_target_price는 null로 쓰십시오. 조정을 써도 반영되지 않습니다.\n"
        "- 초분할 보유라면 위의 증액 계획(next_session_add_plan) 요청은 그대로 유효합니다. 주도주는 비중을 키울 "
        "대상입니다. 증액 블록의 '매도 판단, 손절가, portfolio_adjustment 기준은 바뀌지 않습니다' 문장은 이 종목에 한해 "
        "이 규칙으로 대체됩니다.\n")


def message_line(scenario, *, today, indent=""):
    """Portfolio line for a runner ('' otherwise), plain Korean."""
    block = record(scenario)
    current = phase(block, today)
    if current is None:
        return ""
    if current == EXTENDED:
        return (f"{indent}🏃 주도주 보유 규칙 적용: 매수 후 {block['session']}거래일 {block['gain_pct']:+.1f}%, "
                "보유 기한이 지나 20일선 이탈 전까지 보유\n")
    return (f"{indent}🏃 주도주 보유 규칙 적용: 매수 후 {block['session']}거래일 {block['gain_pct']:+.1f}%, "
            f"50일선 이탈 전까지 보유 (~{_mmdd(block['hold_until'])})\n")


def sell_message_line(scenario, *, language="ko"):
    """Sell-message line for a holding that was a runner ('' otherwise)."""
    block = record(scenario)
    if block is None or block.get("status") != RUNNER:
        return ""
    if language == "en":
        return (f"🏃 Runner hold rule: runner since {block['session']} sessions after entry "
                f"({block['gain_pct']:+.1f}%), hold window until {_mmdd(block['hold_until'])}")
    return (f"🏃 주도주 보유 규칙 적용 종목: 매수 후 {block['session']}거래일 {block['gain_pct']:+.1f}%로 주도주 판정, "
            f"보유 기한 ~{_mmdd(block['hold_until'])}")


EXIT_PREFIX = "RUNNER_MA50:"


def public_reason(reason):
    """Sell reason for channel messages: the internal RUNNER_MA50 prefix (kept in the DB for exit_kind) is dropped."""
    text = str(reason or "")
    return text[len(EXIT_PREFIX):].lstrip() if text.startswith(EXIT_PREFIX) else text


def notice(block, *, market, company_name, ticker, today, detected, stop_change=None, ma50=None, deferred=False):
    """Channel notice for a runner: ``detected`` (became a runner in this review) or a later stop move to the
    initial entry (``stop_change`` = (old, new) without a new detection). Korean for both markets.

    ``deferred``: found while the price is at/below the entry, so the stop moves on a later review.
    """
    block = block or {}
    extended = phase(block, today) == EXTENDED
    until = _mmdd(block.get("hold_until") or "")
    gain, session = float(block.get("gain_pct") or 0), block.get("session")
    floor = f"50일선({_money(ma50, market)})" if _num(ma50) is not None else "50일선"
    keep = (f"보유 기한이 지나 20일선, {floor} 또는 매수가 아래로 마감하면 매도합니다." if extended
            else f"{floor} 또는 매수가 아래로 마감하기 전까지 보유합니다 (~{until}).")
    if stop_change:
        stop = f"손절가: {_money(stop_change[0], market)} → 매수가 {_money(stop_change[1], market)}\n"
    else:
        stop = "손절가: 현재가가 매수가 위로 올라오면 매수가로 옮깁니다\n" if deferred else ""
    if not detected:
        return f"🏃 주도주 손절가 조정: {company_name}({ticker})\n{stop}{keep}\n"
    return (f"🏃 주도주 전환: {company_name}({ticker})\n"
            f"매수 후 {session}거래일째 종가가 매수가 대비 {gain:+.1f}%로 주도주로 판정했습니다.\n{stop}{keep}\n"
            "목표가 도달·과열·단기 추세 이탈로는 팔지 않습니다.\n")
