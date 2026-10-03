"""LLM recheck of re-entry v3 SHADOW triggers (campaign re-entry decided before the close).

Reuses the v2 machinery (observability/reentry_v2_recheck.py): the production BUY
instruction with a recheck section appended, BUY Codex settings
(prism_core.codex_config.resolve_buy_codex_settings), no MCP tools, the frozen report checked
by hash, one retry after a failure. v3 differs only in the recheck section, the user prompt
(reference price, signal type, deterministic target/stop/R/R, attempt number, watch rules and
prior attempts) and the micro-split BUY appendix, frozen at the trigger exactly as the
production BUY would have received it.

The verdict is recorded beside the deterministic virtual position; it never opens, blocks or
sizes anything. No orders and no DB writes. Design: docs/REENTRY_V3_CAMPAIGN_SHADOW_ko.md.
Prompt wording uses plain investor terms (user decision 2026-10-04); code ids stay English.
"""
from __future__ import annotations

from observability import reentry_v2_recheck as RC
from observability.reentry_recheck_inputs import recheck_instruction, strip_embedded_images

RESULT_CONTRACT = "reentry_v3_recheck_result_v1"
MAX_ATTEMPTS = RC.MAX_ATTEMPTS
FINAL = RC.FINAL

# Reviewed with the harness's prompt framing & logical-consistency review; the user decided the
# direction-splitting points on 2026-10-04 (docs/REENTRY_V3_CAMPAIGN_SHADOW_ko.md section 6).
RECHECK_V3_KO = """

## 재진입 재점검 모드 — 재진입 감시 기간 (이번 요청에만 적용)

이 종목은 과거에 손절됐거나 분석 후 보류·차단된 종목입니다. 시스템은 기준 가격(첫 매수 때 돌파했던 가격대.
보류·차단 종목은 원래 판단 당시의 1차 저항)을 중심으로 최대 60거래일 동안 재진입 기회를 지켜봅니다(재진입 감시 기간).
오늘 장 마감 전 판단 시각(한국 14:00, 미국 13:50)의 현재가로 아래 매수 신호 중 하나가 나왔습니다(입력의 매수 신호 참조).
- 기준 가격 재돌파 매수: 감시 기간 중 기준 가격 아래로 마감한 적이 있고, 직전 종가가 기준 가격 이하이며, 현재가가
  기준 가격 위 +3% 이내입니다.
- 기준 가격 눌림 지지 매수: 직전 종가가 기준 가격 이상이고, 오늘 현재까지의 저가가 기준 가격 ±3% 안에 닿았으며,
  현재가가 기준 가격 이상 +5% 이내입니다.
- 흔들기 후 회복 매수: 종가가 기준 가격의 97% 아래로 내려간 뒤 5거래일 안에 현재가가 기준 가격 위 +5% 이내로 회복했고,
  판단 시각 누적 거래량으로 추정한 오늘 거래량이 20일 평균 이상입니다.
입력의 보고서는 매수 신호 전날까지 작성된 가장 최근 보고서이며 작성일을 확인하십시오.
- 원래 손절·보류·차단 사유가 현재 기술적 사실(추세, 위치, 거래량)과 시장 상태로 해소됐는지 재점검하십시오.
- 원래 시나리오 가격 수준과 기준 가격을 비교해 지지 구조가 유지되는지를 판단 근거로 쓰십시오. 이미 넘어선 과거 저항은
  새 지지 후보입니다.
- 재무 F1~F4는 보고서 기준 판단을 유지합니다. 보고서 이후의 새 정보는 없으므로 추정하지 마십시오.
- 1.6단계(상습 손절 종목 게이트)는 이번 판단에 적용하지 않습니다. 이 종목은 기준 가격이 유지되는 재진입 감시 기간
  안에 있고, 그 안의 재진입은 손절을 몇 번 감수하더라도 다시 시도하는 것이 이 감시의 목적입니다. 원래 손절과 이 기간의
  이전 시도 손절은 미진입 사유가 아닙니다. 이 항목은 공유 지시문의 1.6단계와 아래 초분할 부록의 '바뀌지 않는 것' 중
  1.6단계 항목을 이번 재진입에 한해 대신합니다. 나머지 게이트와 미진입 사유는 그대로입니다.
- 입력의 목표·손절·손익비는 기존 BUY 산정 규칙과 같은 방식으로 계산한 결정론 값입니다: 목표는 진입가 위 가장 가까운
  확정 주요 저항까지 거리의 80%(강세 국면에서 +3% 이내 저항은 증액 조건이고 그 다음 저항 기준), 확정 저항이 없으면
  2a 규칙(진입가×1.20) 또는 목표 근거 없음, 손절은 시장 상황별 진입 기준표(매트릭스)의 최대 손절폭보다 넓지 않습니다.
  시나리오의 target_price·stop_loss·손익비도 같은 규칙으로 쓰십시오.
- 이번 요청에는 도구가 제공되지 않습니다. 시각·시세 조회 등 도구 사용 지시는 적용되지 않으며, 입력에 있는 사실만
  사용하고 입력에 없는 값은 미확인으로 두십시오.
- 진입 가격(entry_price)은 입력의 판단 시각 현재가입니다. 오늘 봉은 미완성이므로 장중 누적 거래량을 확정 거래량과
  같은 것처럼 비교하지 마십시오(거래량 해석 기준 그대로). 입력의 '추정 오늘 거래량'은 시간대 비중으로 나눈 추정치입니다.
- 입력의 "이전 시도"는 SHADOW 가상 기록이며 실제 매매가 아닙니다. 원래 판단과 손절은 실제 기록입니다.
- 출력 JSON 형식과 채점 규칙은 기존과 동일합니다.
"""

TRIGGER_TEXT = {
    "R1C": "기준 가격 재돌파 매수",
    "R2S": "기준 가격 눌림 지지 매수",
    "SHAKEOUT_RECLAIM": "흔들기 후 회복 매수",
}
BASIS_TEXT = {
    "primary_support": "첫 매수 때 돌파했던 가격대 = 원래 진입가 바로 아래의 1차 지지",
    "secondary_support": "첫 매수 때 돌파했던 가격대 = 원래 진입가 바로 아래의 2차 지지",
    "primary_resistance": "원래 판단 당시 1차 저항",
    "secondary_resistance": "첫 매수 때 돌파했던 가격대 = 원래 진입가 바로 아래의 2차 저항",
    "primary_resistance_fallback": "원래 진입가 아래 가격 수준이 없어 1차 저항으로 대체",
}
TARGET_SOURCE_TEXT = {
    "primary_resistance": "원래 시나리오 1차 저항",
    "secondary_resistance": "원래 시나리오 2차 저항",
    "prior_60_high": "직전 60거래일 확정 고가",
    "oneil_breakout_2a": "2a 규칙(상단 매물 없는 돌파, 진입가×1.20)",
}
RULE_TEXT = {"L97": "기준 가격×0.97 기준", "SS": "2차 지지 기준"}


def instruction(market):
    return recheck_instruction(market, section=RECHECK_V3_KO)


def micro_split_appendix(market, expected_initial):
    """The per-report micro-split BUY blocks the production BUY would append now ('' when LIVE is off).

    Mirrors stock_tracking_agent / us_stock_tracking_agent: the entry frame/threshold block
    (recheck instruction language "ko") and the add_plan block (KR "ko", US "en"). add_plan_buy_block
    needs a live agent for the expected initial allocation; the recheck passes the B3 allocation
    it already computed from the same ATR14 instead.
    """
    from prism_core import add_plan_prompts
    from prism_core import micro_split_live as live
    if not live.live_enabled(market):
        return ""
    return (live.buy_prompt_block(market, "ko")
            + add_plan_prompts.buy_block(market, "ko" if market == "KR" else "en", expected_initial=expected_initial))


def _money(value):
    return "미제공" if value is None else f"{value:,.2f}"


def _pct(value, base):
    return "결측" if value is None or not base else f"{(value / base - 1) * 100:+.2f}%"


def _attempts_block(attempts):
    lines = []
    for rule, info in attempts.items():
        prior = info.get("prior") or []
        lines.append(f"- 이번 시도: {info['attempt']}/{info['max']}번째 ({RULE_TEXT.get(rule, rule)} 감시)")
        if not prior:
            continue
        lines.append("  이전 시도(SHADOW 가상 기록, 실제 매매 아님):")
        for p in prior:
            ret = "보유 중" if p.get("ret") is None else f"{p['ret'] * 100:+.2f}%"
            lines.append(f"  · {p['date']} {TRIGGER_TEXT.get(p['trigger'], p['trigger'])} 진입 {_money(p['entry'])} → "
                         f"{p.get('exit_date') or '-'} {p.get('exit_reason') or ''} {ret}")
    return "\n".join(lines) + "\n"


def _trigger_lines(item):
    t, level = item["trigger"], item["level"]["L"]
    price, low, prev = item["decision_price"], item.get("day_low"), item.get("prev_close")
    head = (f"### 🚀 매수 신호: {TRIGGER_TEXT.get(t, t)} "
            f"(판단 시각 {item['decision_time_local']}, 현재가 기준, 오늘 봉 미완성)\n")
    if t == "R1C":
        body = (f"- 직전 종가 {_money(prev)} ≤ 기준 가격 {_money(level)}, 감시 기간 중 기준 가격 아래 종가 있음\n"
                f"- 현재가 {_money(price)} (기준 가격 대비 {_pct(price, level)}, 추격 한도 +3%)\n")
    elif t == "R2S":
        body = (f"- 직전 종가 {_money(prev)} ≥ 기준 가격 {_money(level)}\n"
                f"- 오늘 현재까지 저가 {_money(low)} (기준 가격 대비 {_pct(low, level)}, 허용 ±3%)\n"
                f"- 현재가 {_money(price)} (기준 가격 대비 {_pct(price, level)}, 추격 한도 +5%)\n")
    elif t == "SHAKEOUT_RECLAIM":
        shake = item.get("shakeout") or {}
        reclaim = item["campaign"].get("reclaim_level") or level
        body = (f"- {shake.get('window_start')} 종가가 기준 가격의 97% 아래로 내려가 흔들기 확인 기간(5거래일)이 시작됨, "
                f"흔들기 저점 {_money(shake.get('shakeout_low'))}\n"
                f"- 현재가 {_money(price)} (회복 기준 {_money(reclaim)} 대비 {_pct(price, reclaim)}, 한도 +5%)\n"
                f"- 추정 오늘 거래량: 20일 평균의 {shake.get('volume_ratio')}배 (판단 시각 누적 거래량 ÷ 이 시각까지의 "
                f"하루 거래량 비중 {item.get('volume_share')})\n")
    else:
        body = f"- 현재가 {_money(price)} (기준 가격 대비 {_pct(price, level)})\n"
    return head + body


def _levels_block(item):
    entry, stop, target = item["decision_price"], item["stop"], item.get("target")
    lines = ["### 📏 목표·손절·손익비 (BUY 산정 규칙과 같은 방식의 결정론 계산)"]
    if item.get("add_condition"):
        add = item["add_condition"]
        lines.append(f"- 진입가 +3% 이내 저항 {_money(add['price'])}({TARGET_SOURCE_TEXT.get(add['source'], add['source'])})"
                     "은 강세 국면이라 목표가 아니라 증액 조건")
    if target:
        basis = (f"저항 {_money(item.get('resistance'))}까지 거리의 80%" if item.get("resistance") else "진입가×1.20")
        lines.append(f"- 목표: {_money(target)} (진입가 대비 {_pct(target, entry)}, "
                     f"{TARGET_SOURCE_TEXT.get(item.get('target_source'), item.get('target_source'))}, {basis})")
    else:
        lines.append(f"- 목표: 근거 없음(확정 저항 없음, 2a 불충족: {item.get('target_unsupported')})")
    lines.append(f"- 손절: {_money(stop)} (진입가 대비 {_pct(stop, entry)}; 시장 상황별 진입 기준표(매트릭스) 최대 "
                 f"손절폭 -{(item.get('max_stop') or 0) * 100:.0f}%보다 넓지 않음)")
    rr = "산출 불가(목표 없음)" if item.get("rr") is None else f"{item['rr']:.2f}"
    lines.append(f"- 손익비: {rr} (결정론 국면 {item.get('regime') or '미제공'} 기준 floor {item.get('rr_floor')})")
    return "\n".join(lines) + "\n"


def user_prompt(item, report_text):
    original, ref, campaign = item["original"], item["report_ref"], item["campaign"]
    score = "/".join("미제공" if original.get(k) is None else str(original[k]) for k in ("buy_score", "min_score"))
    lines = ["재진입 재점검 요청입니다 (재진입 감시 기간, 장 마감 전 판단).\n", "### 원래 판단",
             f"- 출처: {item['source']} / 원래 판단일 {original['decided_on']}",
             f"- 원래 점수/최소점수(원래 판단 당시 기준): {score}",
             f"- 원래 사유: {str(original.get('reason') or '')[:500]}"]
    text = "\n".join(lines) + "\n" + RC._levels_line(original.get("key_levels"))
    if item["source"] == "STOP_EXIT":
        text += (f"- 원래 진입가 {_money(original.get('entry_price'))} → 손절 매도가 "
                 f"{_money(original.get('exit_price'))} ({original.get('realized_pct')}%)\n")
    level = item["level"]
    ends = campaign.get("end_levels") or {}
    end_text = " / ".join(f"{RULE_TEXT.get(rule, rule)} 종가 < {_money(value)}" for rule, value in ends.items())
    text += ("\n### 재진입 감시 상태 (결정론적 계산)\n"
             f"- 기준 가격: {_money(level['L'])} ({BASIS_TEXT.get(level['basis'], level['basis'])})\n"
             f"- 감시 기간: {campaign['anchor_date']} 다음 거래일부터 최대 {campaign['horizon']}거래일, "
             f"오늘은 {campaign['elapsed'] + 1}번째 거래일\n"
             f"- 흔들기 확인 시작선: {end_text or '미제공'} → 그 뒤 5거래일 안에 {_money(campaign.get('reclaim_level'))} 위로 "
             f"마감하지 못하거나 {_money(campaign.get('deep_level'))} 아래로 마감하면 감시 종료\n"
             + _attempts_block(item["attempts"]))
    text += "\n" + _trigger_lines(item) + "\n" + _levels_block(item)
    text += (f"\n### 시장 국면(결정론적 계산): {item.get('regime') or '미제공'} / Market Pulse: "
             f"{item.get('market_pulse') or '미제공'}\n\n{item['facts_text']}\n"
             f"### 보고서 작성일: {ref['report_date']} (매수 신호일까지 {ref['age_days']}일 경과)\n\n"
             f"### Report Content:\n{strip_embedded_images(report_text)}\n")
    return text + (item.get("appendix_text") or "")


def recheck(item, *, reports_root, archive_db, llm=None, system=None):
    return RC.recheck(item, reports_root=reports_root, archive_db=archive_db, llm=llm,
                      instruction=system if system is not None else instruction(item["market"]),
                      prompt_fn=user_prompt, contract=RESULT_CONTRACT)
