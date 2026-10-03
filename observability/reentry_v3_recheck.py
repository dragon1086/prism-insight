"""LLM recheck of re-entry v3 SHADOW triggers (campaign re-entry, lunch-time decision).

Reuses the v2 machinery (observability/reentry_v2_recheck.py): the production BUY
instruction with a recheck section appended, BUY Codex settings
(prism_core.codex_config.resolve_buy_codex_settings), no MCP tools, the frozen report checked
by hash, one retry after a failure. v3 differs only in the recheck section, the user prompt
(campaign level L, trigger type, deterministic target/stop/R/R, attempt number, campaign
rules and prior attempts) and the micro-split BUY appendix, which is frozen at the trigger
exactly as the production BUY would have received it.

The verdict is recorded beside the deterministic virtual position; it never opens, blocks or
sizes anything. No orders and no DB writes. Design: docs/REENTRY_V3_CAMPAIGN_SHADOW_ko.md.
"""
from __future__ import annotations

from observability import reentry_v2_recheck as RC
from observability.reentry_recheck_inputs import recheck_instruction, strip_embedded_images

RESULT_CONTRACT = "reentry_v3_recheck_result_v1"
MAX_ATTEMPTS = RC.MAX_ATTEMPTS
FINAL = RC.FINAL

# Wording reviewed with the harness's prompt framing & logical-consistency review
# (docs/REENTRY_V3_CAMPAIGN_SHADOW_ko.md section 6). Direction-splitting contradictions with
# the shared instruction and the micro-split appendix are listed there and are NOT resolved here.
RECHECK_V3_KO = """

## 재진입 재점검 모드 — v3 캠페인 (이번 요청에만 적용)

이 종목은 과거에 손절됐거나(STOP_EXIT) 분석 후 보류·차단된 종목입니다. 시스템은 기준 레벨 L을 중심으로 최대
60세션 동안 재진입 기회를 감시합니다. L은 손절 종목이면 첫 매수가 돌파했던 레벨(원래 진입가 바로 아래의 원래
시나리오 가격 수준), 보류·차단 종목이면 원래 판단 당시의 1차 저항입니다. 오늘 장중(점심 시점) 현재가로 아래 둘 중
하나가 성립했습니다(입력의 트리거 참조).
- ①C 재돌파: 캠페인 중 L 아래로 마감한 적이 있고, 직전 종가가 L 이하이며, 현재가가 L 위 +3% 이내입니다.
- ②S 박스 바닥 재시험: 직전 종가가 L 이상이고, 오늘 현재까지의 저가가 L ±3% 안에 닿았으며, 현재가가 L 이상
  +5% 이내입니다.
입력의 보고서는 트리거 전날까지 작성된 가장 최근 보고서이며 작성일을 확인하십시오.
- 원래 손절·보류·차단 사유가 현재 기술적 사실(추세, 위치, 거래량)과 시장 상태로 해소됐는지 재점검하십시오.
- 원래 시나리오 가격 수준과 L을 비교해 지지 구조가 유지되는지를 판단 근거로 쓰십시오. 이미 넘어선 과거 저항은
  새 지지 후보입니다.
- 재무 F1~F4는 보고서 기준 판단을 유지합니다. 보고서 이후의 새 정보는 없으므로 추정하지 마십시오.
- 이번 요청에는 도구가 제공되지 않습니다. 시각·시세 조회 등 도구 사용 지시는 적용되지 않으며, 입력에 있는 사실만
  사용하고 입력에 없는 값은 미확인으로 두십시오.
- 진입 가격(entry_price)은 입력의 판단 시점 현재가입니다. 오늘 봉은 미완성이므로 장중 누적 거래량을 확정 거래량과
  같은 것처럼 비교하지 마십시오(거래량 해석 기준 그대로).
- 입력의 "SHADOW 가상 포지션 수치"(구조적 손절·다음 저항 목표·손익비)는 시스템 기록용 결정론 값입니다. 시나리오의
  stop_loss·target_price·손익비는 기존 산정 규칙대로 쓰십시오.
- 입력의 "이전 시도"는 SHADOW 가상 기록이며 실제 매매가 아닙니다. 원래 판단과 손절은 실제 기록입니다.
- 출력 JSON 형식과 채점 규칙은 기존과 동일합니다.
"""

TRIGGER_TEXT = {
    "R1C": "①C 재돌파",
    "R2S": "②S 박스 바닥 재시험",
}
BASIS_TEXT = {
    "primary_support": "원래 진입가 바로 아래의 1차 지지 = 첫 매수가 돌파했던 레벨",
    "secondary_support": "원래 진입가 바로 아래의 2차 지지 = 첫 매수가 돌파했던 레벨",
    "primary_resistance": "원래 판단 당시 1차 저항",
    "secondary_resistance": "원래 진입가 바로 아래의 2차 저항 = 첫 매수가 돌파했던 레벨",
    "primary_resistance_fallback": "원래 진입가 아래 레벨이 없어 1차 저항으로 대체",
}


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
        lines.append(f"- 이번 시도: {info['attempt']}/{info['max']} (종료 규칙 {rule})")
        if not prior:
            continue
        lines.append(f"  이전 시도(SHADOW 가상 기록, 실제 매매 아님, 규칙 {rule}):")
        for p in prior:
            ret = "보유 중" if p.get("ret") is None else f"{p['ret'] * 100:+.2f}%"
            lines.append(f"  · {p['date']} {TRIGGER_TEXT.get(p['trigger'], p['trigger'])} 진입 {_money(p['entry'])} → "
                         f"{p.get('exit_date') or '-'} {p.get('exit_reason') or ''} {ret}")
    return "\n".join(lines) + "\n"


def _trigger_lines(item):
    t, level = item["trigger"], item["level"]["L"]
    price, low, prev = item["decision_price"], item.get("day_low"), item.get("prev_close")
    head = f"### 🚀 재진입 트리거: {TRIGGER_TEXT.get(t, t)} (판단 시각 {item['decision_time_local']}, 현재가 기준, 오늘 봉 미완성)\n"
    if t == "R1C":
        body = (f"- 직전 종가 {_money(prev)} ≤ L {_money(level)}, 캠페인 중 L 아래 종가 있음\n"
                f"- 현재가 {_money(price)} (L 대비 {_pct(price, level)}, 추격 한도 +3%)\n")
    elif t == "R2S":
        body = (f"- 직전 종가 {_money(prev)} ≥ L {_money(level)}\n"
                f"- 오늘 현재까지 저가 {_money(low)} (L 대비 {_pct(low, level)}, 허용 ±3%)\n"
                f"- 현재가 {_money(price)} (L 대비 {_pct(price, level)}, 추격 한도 +5%)\n")
    else:
        body = f"- 현재가 {_money(price)} (L 대비 {_pct(price, level)})\n"
    return head + body


def _levels_block(item):
    entry, stop, target = item["decision_price"], item["stop"], item.get("target")
    target_line = (f"- 다음 저항 목표: {_money(target)} (진입가 대비 {_pct(target, entry)}, 출처 {item.get('target_source')})\n"
                   if target else "- 다음 저항 목표: 없음(진입가 +2% 위 1·2차 저항·직전 60세션 고점 없음, NO_OVERHEAD)\n")
    rr = "산출 불가(목표 없음)" if item.get("rr") is None else f"{item['rr']:.2f}"
    return ("### 📏 SHADOW 가상 포지션 수치 (결정론적 계산, 기록용)\n" + target_line
            + f"- 구조적 손절: {_money(stop)} (진입가 대비 {_pct(stop, entry)}) = max(L×0.97, 진입가×0.90)\n"
            + f"- 손익비: {rr} (결정론 국면 {item.get('regime') or '미제공'} floor {item.get('rr_floor')})\n")


def user_prompt(item, report_text):
    original, ref, campaign = item["original"], item["report_ref"], item["campaign"]
    score = "/".join("미제공" if original.get(k) is None else str(original[k]) for k in ("buy_score", "min_score"))
    lines = ["재진입 재점검 요청입니다 (v3 캠페인, 장중 판단).\n", "### 원래 판단",
             f"- 출처: {item['source']} / 원래 판단일 {original['decided_on']}",
             f"- 원래 점수/최소점수(원래 판단 당시 기준): {score}",
             f"- 원래 사유: {str(original.get('reason') or '')[:500]}"]
    text = "\n".join(lines) + "\n" + RC._levels_line(original.get("key_levels"))
    if item["source"] == "STOP_EXIT":
        text += (f"- 원래 진입가 {_money(original.get('entry_price'))} → 손절 청산가 {_money(original.get('exit_price'))} "
                 f"({original.get('realized_pct')}%)\n")
    level = item["level"]
    ends = campaign.get("end_levels") or {}
    end_text = " / ".join(f"{rule} 종가 < {_money(value)}" for rule, value in ends.items())
    text += ("\n### 캠페인 상태 (결정론적 계산)\n"
             f"- 기준 레벨 L: {_money(level['L'])} ({BASIS_TEXT.get(level['basis'], level['basis'])})\n"
             f"- 캠페인: 기준일 {campaign['anchor_date']} 다음 세션부터 최대 {campaign['horizon']}세션, "
             f"오늘은 {campaign['elapsed'] + 1}번째 세션\n"
             f"- 종료 기준(병행 기록): {end_text or '미제공'}\n"
             + _attempts_block(item["attempts"]))
    text += "\n" + _trigger_lines(item) + "\n" + _levels_block(item)
    text += (f"\n### 시장 국면(결정론적 계산): {item.get('regime') or '미제공'} / Market Pulse: "
             f"{item.get('market_pulse') or '미제공'}\n\n{item['facts_text']}\n"
             f"### 보고서 작성일: {ref['report_date']} (트리거일까지 {ref['age_days']}일 경과)\n\n"
             f"### Report Content:\n{strip_embedded_images(report_text)}\n")
    return text + (item.get("appendix_text") or "")


def recheck(item, *, reports_root, archive_db, llm=None, system=None):
    return RC.recheck(item, reports_root=reports_root, archive_db=archive_db, llm=llm,
                      instruction=system if system is not None else instruction(item["market"]),
                      prompt_fn=user_prompt, contract=RESULT_CONTRACT)
