import json
from datetime import timedelta

from messaging.korean_trading_message import render_korean_trading_message
from prism_core.sell_message import allocation_after_adds, holding_text, plain_reason, return_line

PARTIAL = json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": "0.6796",
                                      "legs": [{"kind": "INITIAL", "allocation": "0.6796", "price": "7740.0"}]}})


def test_samyang_cement_notice_reads_plainly():
    reason = plain_reason("TIER1_STOPLOSS: price<=stop_loss(7400.5000)", sell_price=7320, market="KR")
    assert reason == "손절선 이탈 — 손절선 7,400원(장중 0.5% 여유 7,363원) 아래로 내려가 7,320원에 매도했습니다."
    assert return_line(-5.426356589147287, PARTIAL) == "수익률: ⬇️ -5.43% (실제 투입 비중 68% 반영 시 -3.69%)"
    assert holding_text(timedelta(hours=19, minutes=1)) == "19시간 1분"
    assert allocation_after_adds(PARTIAL, profit_rate=-5.43, market="KR") == ""
    text = render_korean_trading_message("\n".join([return_line(-5.43, PARTIAL), f"매도이유: {reason}"]))
    assert "실제 투입 비중 68% 반영 시 -3.69%" in text and "7,320원에 매도" in text


def test_other_exit_codes_and_full_slots():
    assert plain_reason("TIER1_ABS7: loss -7.12% <= -7.0%", sell_price=1, market="KR").startswith("최대 손실 한도 도달")
    assert "50일 이동평균선($101.50)" in plain_reason(
        "TIER1.5_MA50: below 50MA(101.5000) while losing (-3.20%)", sell_price=99, market="US")
    trail = plain_reason("TIER2_TRAIL: regime=sideways peak=120.0000 trail(-5%)=113.4300 >= price",
                         sell_price=113, market="US")
    assert trail.startswith("고점 대비 하락") and "$120.00에서 5%" in trail
    assert "관리종목" in plain_reason("TIER0_EVENT:KIS_STATUS:51(관리종목)", sell_price=1)
    assert plain_reason("AI가 쓴 매도 사유입니다.", sell_price=1) == "AI가 쓴 매도 사유입니다."
    assert return_line(12.5, "{}", label="수익률(전략 기준)") == "수익률(전략 기준): ⬆️ +12.50%"
    assert holding_text(timedelta(days=3, hours=2)) == "3일"


def test_adds_keep_the_average_entry_line():
    added = json.dumps({"micro_split": {"contract": "micro-split-live-v1", "allocation": "0.8", "legs": [
        {"kind": "INITIAL", "allocation": "0.35", "price": "10000"},
        {"kind": "ADD", "allocation": "0.45", "price": "10200"}]}})
    line = allocation_after_adds(added, profit_rate=2.0, market="KR")
    assert "평균 매수가" in line
