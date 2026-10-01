"""Pure decision-time input features for SHADOW measurement. Never a prompt input or gate.

Computed only from the daily frame the BUY path already fetched for its trend
facts, plus the decision's own scenario. Anything not derivable is MISSING with a
reason; nothing is estimated from neighbouring data.

Features answer the review of 2026-09-27: inputs the BUY rubric asks for but the
agent reported as unavailable (time-of-day volume, accumulation in the US,
extension at the decision price). Their value is measured later against the
decision's forward returns; they are not evidence of an LLM effect.
"""
from __future__ import annotations

import math
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

CONTRACT_VERSION = "decision_inputs_v1"
SESSIONS = {"KR": ("Asia/Seoul", time(9, 0), time(15, 30)), "US": ("America/New_York", time(9, 30), time(16, 0))}
_ALIASES = {"open": ("Open", "open", "시가"), "high": ("High", "high", "고가"), "low": ("Low", "low", "저가"),
            "close": ("Close", "close", "종가"), "volume": ("Volume", "volume", "거래량")}


def bars_from_frame(frame, limit=80):
    """Frame (any provider column case) -> sorted [{date, open, high, low, close, volume}]."""
    rows = []
    for index, row in frame.tail(limit).iterrows():
        label = index.date() if hasattr(index, "date") else date.fromisoformat(str(index)[:10])
        values = {}
        for field, names in _ALIASES.items():
            raw = next((row[name] for name in names if name in row), None)
            values[field] = None if raw is None else float(raw)
        rows.append({"date": label.isoformat(), **values})
    rows.sort(key=lambda r: r["date"])
    return rows


def _valid(bar):
    return all(bar.get(k) is not None and math.isfinite(bar[k]) and bar[k] > 0
               for k in ("open", "high", "low", "close")) and bar.get("volume") is not None \
        and math.isfinite(bar["volume"]) and bar["volume"] >= 0


def elapsed_fraction(market, observed_at):
    zone, start, end = SESSIONS[market]
    local = observed_at.astimezone(ZoneInfo(zone))
    opened = datetime.combine(local.date(), start, ZoneInfo(zone))
    closed = datetime.combine(local.date(), end, ZoneInfo(zone))
    if local <= opened:
        return 0.0
    if local >= closed:
        return 1.0
    return (local - opened) / (closed - opened)


def _pct(a, b):
    return None if a is None or b in (None, 0) else round((a / b - 1) * 100, 4)


def compute(bars, *, market, observed_at, current_price=None, scenario=None):
    """Return {status, features, missing} for one decision."""
    features, missing = {"contract_version": CONTRACT_VERSION}, {}
    zone = SESSIONS[market][0]
    today = observed_at.astimezone(ZoneInfo(zone)).date().isoformat()
    rows = [b for b in bars if _valid(b)]
    if len(rows) != len(bars):
        missing["invalid_bars"] = len(bars) - len(rows)
    forming = rows[-1] if rows and rows[-1]["date"] == today and elapsed_fraction(market, observed_at) < 1 else None
    completed = [b for b in rows if b["date"] < today or (b["date"] == today and forming is None)]
    fraction = elapsed_fraction(market, observed_at)
    # Event keys containing "session" are redacted by the sanitizer; keep this name.
    features.update(market_elapsed_fraction=round(fraction, 4), forming_bar_present=forming is not None)
    if len(completed) < 26:
        missing["completed_bars"] = len(completed)
        return {"status": "MISSING", "features": features, "missing": missing}
    last, prior20 = completed[-1], completed[-21:-1]
    features["last_completed_date"] = last["date"]
    avg20 = sum(b["volume"] for b in completed[-20:]) / 20
    features["rvol_last_completed"] = round(last["volume"] / (sum(b["volume"] for b in prior20) / 20), 4) \
        if sum(b["volume"] for b in prior20) > 0 else None
    # Rubric item 1 says "today or any of the last 3 sessions": each vs its own prior 20.
    recent = []
    for k in range(1, 4):
        base = completed[-20 - k:-k]
        if len(base) == 20 and sum(b["volume"] for b in base) > 0:
            recent.append(completed[-k]["volume"] / (sum(b["volume"] for b in base) / 20))
    features["rvol_max_last3_completed"] = round(max(recent), 4) if recent else None
    # Linear time scaling overstates early-session volume (U-shaped intraday profile);
    # it is recorded as an approximation and bucketed, never used as a signal.
    if forming is not None and 0.05 <= fraction < 1 and avg20 > 0:
        features["rvol_time_scaled_linear"] = round(forming["volume"] / (avg20 * fraction), 4)
    else:
        missing["rvol_time_scaled_linear"] = "no_forming_bar" if forming is None else "elapsed_fraction_out_of_range"
    true_ranges = []
    for prev, bar in zip(completed[-21:-1], completed[-20:]):
        true_ranges.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev["close"]), abs(bar["low"] - prev["close"])))
    atr20_pct = sum(true_ranges) / 20 / last["close"] * 100
    features["atr20_pct"] = round(atr20_pct, 4)
    price = current_price if current_price and current_price > 0 else (forming or last)["close"]
    if price:
        move = _pct(price, last["close"])
        features.update(price_basis=float(price), move_vs_prev_close_pct=move,
                        move_atr_multiple=round(move / atr20_pct, 4) if move is not None and atr20_pct else None,
                        dist_ma20_pct=_pct(price, sum(b["close"] for b in completed[-20:]) / 20),
                        dist_20d_high_pct=_pct(price, max(b["high"] for b in completed[-20:])))
    else:
        missing["price_basis"] = "no_decision_price"
    if forming is not None:
        gap = _pct(forming["open"], last["close"])
        features.update(gap_open_pct=gap, gap_atr_multiple=round(gap / atr20_pct, 4) if gap is not None and atr20_pct else None)
    else:
        missing["gap_open_pct"] = "no_forming_bar"
    up = sum(b["volume"] for p, b in zip(completed[-21:-1], completed[-20:]) if b["close"] > p["close"])
    down = sum(b["volume"] for p, b in zip(completed[-21:-1], completed[-20:]) if b["close"] < p["close"])
    features["up_down_volume_ratio_20"] = round(up / down, 4) if down > 0 else None
    window = list(zip(completed[-26:-1], completed[-25:]))
    features["accumulation_days_25"] = sum(b["close"] >= p["close"] * 1.002 and b["volume"] > p["volume"] for p, b in window)
    features["distribution_days_25"] = sum(b["close"] <= p["close"] * 0.998 and b["volume"] > p["volume"] for p, b in window)
    scenario = scenario or {}
    for key in ("buy_score", "effective_score", "min_score", "momentum_signal_count", "additional_confirmation_count"):
        value = scenario.get(key)
        features["scenario_" + key] = value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    rvol = features.get("rvol_time_scaled_linear") or 0
    features["rubric_probe"] = {
        # Would the rubric's volume momentum item be satisfiable with time-scaled volume?
        "volume_item_time_scaled": bool(rvol >= 2.0),
        "volume_item_last_completed": bool((features.get("rvol_last_completed") or 0) >= 2.0),
        "accumulation_ratio_ge_1_5": bool((features.get("up_down_volume_ratio_20") or 0) >= 1.5),
        "extended_move_ge_2_atr": bool((features.get("move_atr_multiple") or 0) >= 2.0),
    }
    return {"status": "OK" if not missing else "PARTIAL", "features": features, "missing": missing}


# --- BUY prompt facts (decision_inputs_v1) -------------------------------------------------
# Only completed-session and dated reference facts reach the prompt. The intraday volume
# estimate and the accumulation proxy stay SHADOW-only: the 2026-09-27 volume contract
# (docs/VOLUME_PROMPT_REVIEW_20260927_ko.md) forbids comparing intraday with full-session
# volume and inferring institutional accumulation from volume alone.
RVOL = 2.0
CHASE_PCT = 5.0            # O'Neil buy zone: up to 5% above the pivot (20-day high as a proxy)
CHASE_ATR = 2.0
EARNINGS_DAYS = 5
PEER_MIN_VALID = 3


def rubric_flags(features, peer=None, earnings=None):
    """Deterministic answers to rubric items the agent otherwise reports as unknown."""
    f = features or {}
    completed = f.get("rvol_max_last3_completed")
    high, move_atr = f.get("dist_20d_high_pct"), f.get("move_atr_multiple")
    days = (earnings or {}).get("calendar_days_to_earnings")
    peer = peer or {}
    return {"volume_item_completed": None if completed is None else bool(completed >= RVOL),
            "chase_zone": None if high is None else bool(high > CHASE_PCT or (move_atr or 0) >= CHASE_ATR),
            "earnings_within_5d": None if days is None else bool(days <= EARNINGS_DAYS),
            # Per metric: valid (positive) peer values, not selected peers (2026-10-01 003160:
            # 2 loss-making peers left a single PER behind a "3 peers" flag).
            "peer_usable": _peer_metric_usable(peer, "per"),
            "peer_usable_pbr": _peer_metric_usable(peer, "pbr")}


def _peer_metric_usable(peer, field):
    valid = peer.get("peer_valid_" + field)
    return peer.get("status") == "OK" and isinstance(valid, int) and valid >= PEER_MIN_VALID


def _yn(value, language):
    if value is None:
        return "결측" if language == "ko" else "missing"
    return ("예" if value else "아니오") if language == "ko" else ("yes" if value else "no")


def _num_lang(value, suffix="", digits=2, language="ko"):
    if value is None:
        return "결측" if language == "ko" else "missing"
    return f"{value:,.{digits}f}{suffix}"


def render_facts_block(result, peer=None, earnings=None, *, market, language="ko"):
    """Prompt block; every line states its basis so a missing value reads as missing."""
    f = (result or {}).get("features") or {}
    flags = rubric_flags(f, peer, earnings)
    peer = peer or {}
    ko = language == "ko"

    def _num(value, suffix="", digits=2):  # language-bound missing marker
        return _num_lang(value, suffix, digits, language)

    def _count(value):
        return str(value) if isinstance(value, int) else ("결측" if ko else "missing")

    asof = f.get("last_completed_date") or ("결측" if ko else "missing")
    lines = ["### 📐 보조 수치 팩트 (결정론적 계산 · decision_inputs_v1)" if ko
             else "### 📐 Supplementary numeric facts (deterministic · decision_inputs_v1)"]
    lines.append(
        f"- 확정 세션 거래량(기준 마지막 확정일 {asof}): 최근 3개 확정 세션 각각을 직전 20거래일 평균과 비교한 최대 "
        f"{_num(f.get('rvol_max_last3_completed'), '배')} → 3단계 1번(최근 3거래일 내 200%) 충족: "
        f"{_yn(flags['volume_item_completed'], language)}. 진행 중인 당일 봉은 포함하지 않았습니다." if ko else
        f"- Completed-session volume (last completed {asof}): max over the last 3 completed sessions, each vs its "
        f"preceding 20 sessions, {_num(f.get('rvol_max_last3_completed'), 'x')} → Step 3 item 1 (200% within the last 3 "
        f"sessions) met: {_yn(flags['volume_item_completed'], language)}. The unfinished current bar is excluded.")
    lines.append(
        f"- 위치(판단 시점 가격 {_num(f.get('price_basis'), '', 2)}): 20일 고가 대비 {_num(f.get('dist_20d_high_pct'), '%')}, "
        f"MA20 대비 {_num(f.get('dist_ma20_pct'), '%')}, 전일 종가 대비 {_num(f.get('move_vs_prev_close_pct'), '%')}"
        f"(ATR20의 {_num(f.get('move_atr_multiple'), '배')}) → 20일 고가 +5% 초과 또는 ATR 2배 이상: "
        f"{_yn(flags['chase_zone'], language)}" if ko else
        f"- Location (decision price {_num(f.get('price_basis'), '', 2)}): vs 20-day high {_num(f.get('dist_20d_high_pct'), '%')}, "
        f"vs MA20 {_num(f.get('dist_ma20_pct'), '%')}, vs prior close {_num(f.get('move_vs_prev_close_pct'), '%')} "
        f"({_num(f.get('move_atr_multiple'), 'x')} ATR20) → >5% above 20-day high or >=2 ATR: {_yn(flags['chase_zone'], language)}")
    if peer.get("status") == "OK":
        lines.append(
            f"- 동종업계 밸류에이션(선정 비교기업 {peer.get('peer_count')}개, 재무 {peer.get('period')}, "
            f"{peer.get('price_basis')} 기준): PER 중앙값 {_num(peer.get('peer_median_per'))}(유효값 "
            f"{_count(peer.get('peer_valid_per'))}개, 본 종목 {_num(peer.get('target_per'))}, 할인 "
            f"{_num(peer.get('per_discount_vs_median_pct'), '%')}), PBR 중앙값 {_num(peer.get('peer_median_pbr'))}(유효값 "
            f"{_count(peer.get('peer_valid_pbr'))}개, 본 종목 {_num(peer.get('target_pbr'))}) → 업종 평균 대용 가능"
            f"(지표별 유효값 3개 이상): PER {_yn(flags['peer_usable'], language)} · PBR "
            f"{_yn(flags['peer_usable_pbr'], language)}" if ko else
            f"- Peer valuation (selected {peer.get('peer_count')} peers, financials {peer.get('period')}): PER median "
            f"{_num(peer.get('peer_median_per'))} ({_count(peer.get('peer_valid_per'))} valid, this "
            f"{_num(peer.get('target_per'))}, discount {_num(peer.get('per_discount_vs_median_pct'), '%')}), PBR median "
            f"{_num(peer.get('peer_median_pbr'))} ({_count(peer.get('peer_valid_pbr'))} valid) → usable as industry "
            f"average (>=3 valid values per metric): PER {_yn(flags['peer_usable'], language)} · PBR "
            f"{_yn(flags['peer_usable_pbr'], language)}")
    if market == "US":
        if (earnings or {}).get("status") == "OK":
            lines.append(f"- 다음 실적 발표 예정: {earnings['next_earnings_date']} (D-{earnings['calendar_days_to_earnings']}, 달력일, "
                         f"yfinance 일정) → 5일 이내: {_yn(flags['earnings_within_5d'], language)}" if ko else
                         f"- Next scheduled earnings: {earnings['next_earnings_date']} (D-{earnings['calendar_days_to_earnings']}, "
                         f"calendar days, yfinance) → within 5 days: {_yn(flags['earnings_within_5d'], language)}")
        else:
            lines.append("- 다음 실적 발표 예정: 결측(확인 불가이며 일정이 없다는 뜻이 아닙니다)" if ko else
                         "- Next scheduled earnings: missing (unknown, not 'none scheduled')")
    return "\n".join(lines) + "\n", flags


def prompt_contract(language="ko", market="KR"):
    """How the BUY agent may use the facts block; appended to the BUY instruction."""
    us = market == "US"
    if language == "ko":
        text = """

## 보조 수치 팩트 사용법 (입력 '📐 보조 수치 팩트' 블록)

블록은 기존 채점 항목의 비어 있던 입력을 채우는 결정론적 계산입니다. 새 가점·감점·진입 차단 조건이 아닙니다.
항목이 '결측'이면 기존처럼 보고서로 판단하고, 같은 사실을 두 번 세지 마십시오. 거래량 해석 기준은 그대로 따릅니다.
- 3단계 1번(최근 3거래일 내 거래량 200%): 확정 세션 기준 '충족: 예'이면 충족입니다. '아니오'이면 확정 세션 기준 미충족이며,
  진행 중인 당일 봉은 기존 기준대로 미확정으로 둡니다.
- 4단계 'PER 30% 이상 저평가'와 미진입 단독 사유 2번(PER ≥ 업종 평균 2.5배): 보고서 2-1에 업종 평균이 없으면 블록의
  동종업계 중앙값을 업종 평균으로 씁니다. 비교군은 선정된 비교기업이며 업종 전체가 아님을 rationale에 밝히되, 자료 제공 업체명은 쓰지 마십시오.
  '업종 평균 대용 가능'은 지표별(PER·PBR)로 판정합니다. 해당 지표가 '아니오'(유효값 3개 미만)면 그 중앙값은 참고만 하고
  업종 평균으로 쓰지 마십시오.
- 위치 수치는 기존 '추격 위험 검토'와 손절·목표 설정의 근거 수치입니다. 20일 고가는 오닐 피벗의 근사치이며,
  '예'만으로 미진입하지 말고 돌파 실패·가격 밀림 등 기존 추격 위험 조건과 함께 판단하십시오."""
        if us:
            text += """
- (미국) 실적 발표 5일 이내 '예': 발표 결과에 따른 갭 위험을 rationale에 구체적으로 적고 판단에 반영하십시오."""
        return text + "\n"
    text = """

## Using the supplementary numeric facts (input block '📐 Supplementary numeric facts')

The block fills inputs that existing rubric items already require. It adds no score bonus, penalty or entry gate.
If an item is 'missing', judge from the report as before and never count the same fact twice. The volume interpretation rules still apply.
- Step 3 item 1 (volume 200% within the last 3 sessions): 'met: yes' on completed sessions counts as satisfied. 'no' means not met
  on completed sessions; the unfinished current bar stays unconfirmed as before.
- Step 4 'PE discount >= 30%' and standalone No-Entry 2 (PE >= 2.5x industry average): when report 2-1 lacks an industry average,
  use the block's peer median and state in the rationale that it is a selected peer set, not the whole industry, without naming the data vendor.
  Usability is judged per metric (PER, PBR). If a metric reads 'no' (fewer than 3 valid values), use that median
  for reference only, never as the industry average.
- Location figures support the existing chasing-risk assessment and stop/target placement. The 20-day high is only a proxy for
  the O'Neil pivot; do not reject on 'yes' alone, judge it together with the existing failed-breakout/price-retreat conditions."""
    if us:
        text += """
- (US) Earnings within 5 days 'yes': state the earnings gap risk concretely in the rationale and weigh it in the decision."""
    return text + "\n"


def prompt_facts_enabled():
    import os
    return os.getenv("PRISM_BUY_DECISION_FACTS", "true").strip().lower() in {"1", "true", "yes", "on"}
