import pytest
from live.scenario_notice import render_notice


def position(**updates):
    value = dict(verified=True, timestamp=1000, side="LONG", quantity=.1,
        average_entry_price=84750, hard_stop=84500, exchange_leverage=10,
        take_profits=[dict(price=85200, quantity=.06)], partial_stops=[],
        scenario_initial_equity=9613.26741435, scenario_budget=192.265348287,
        account_snapshot=dict(same_event=True, same_account=True, timestamp=1000,
            position_margin=851.847125, equity=9613.09241435, margin_mode="REGULAR_MARGIN"))
    value.update(updates)
    return value


def filled(after=None, **updates):
    value = dict(kind="FILLED", timestamp=1000, side="LONG", fill_confirmed=True,
        protection_confirmed=True, price=84750, quantity=.1,
        position_after=position() if after is None else after)
    value.update(updates)
    return value


def test_readable_sample_math_and_distinct_risk_bases():
    out = render_notice(filled())
    for expected in ("8.86%", "보유물량 60%", "+0.53%", "+5.31%", "+27.00 USDT",
                     "+0.28%", "-25.00 USDT", "-0.26%", "-0.29%", "-2.95%", "2.00%",
                     "증거금은 최대손실", "수수료·슬리피지·펀딩"):
        assert expected in out


def test_add_uses_whole_position_average_not_last_fill():
    out = render_notice(filled(price=86000, quantity=.01))
    assert "이번 체결: 0.01 BTC" in out
    assert "전체 평균 진입가: 84,750.00" in out
    assert "+27.00 USDT" in out


def test_short_and_profitable_stop():
    out = render_notice(filled(position(side="SHORT", average_entry_price=100, hard_stop=90,
        take_profits=[dict(price=80, quantity=.06)]), side="SHORT"))
    assert "수익 보호" in out and "+10.00%" in out and "+100.00%" in out
    assert "+1.20 USDT" in out


def test_verified_before_after_includes_target_only_change_and_timestamps():
    before = position(timestamp=900)
    after = position(take_profits=[dict(price=85300, quantity=.06)])
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, change_type="updated", position_before=before, position_after=after))
    assert "변경 전" in out and "변경 후" in out and "변경 없음" in out
    assert "85,200.00" in out and "85,300.00" in out and "→" in out


@pytest.mark.parametrize("bad", [dict(verified=False), dict(timestamp=700), dict(quantity=float("nan")),
    dict(quantity=-1), dict(average_entry_price=float("inf"))])
def test_bad_position_does_not_produce_target_arithmetic(bad):
    assert "+27.00 USDT" not in render_notice(filled(position(**bad)))


def test_wrong_or_stale_account_never_used_as_percentage_basis():
    for updates in (dict(same_account=False), dict(timestamp=700)):
        after = position()
        after["account_snapshot"].update(updates)
        out = render_notice(filled(after))
        assert "8.86%" not in out and "+0.28%" not in out


@pytest.mark.parametrize("margin", [None, -1, float("nan")])
def test_missing_margin_does_not_hide_valid_equity_based_risk(margin):
    after = position()
    after["account_snapshot"]["position_margin"] = margin
    out = render_notice(filled(after))
    assert "8.86%" not in out and "+0.28%" in out and "-0.26%" in out


def test_unknown_leverage_not_invented_and_bad_target_quantity_rejected():
    assert "10배 단순환산" not in render_notice(filled(position(exchange_leverage=None)))
    for targets in ([dict(price=85200, quantity=.2)],
                    [dict(price=85200, quantity=.06), dict(price=85300, quantity=.06)],
                    [dict(price=float("inf"), quantity=.06)]):
        out = render_notice(filled(position(take_profits=targets)))
        assert "+27.00 USDT" not in out and "익절 목표: 미확인" in out


def test_initial_protection_never_fabricates_before():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, change_type="initial_protection", position_after=position()))
    assert "최초 확인" in out and "이전 상태 미확인" in out
    assert "변경 전:" not in out


def test_legacy_fill_does_not_compute_whole_position_from_fill_or_plan():
    event = filled()
    del event["position_after"]
    event.update(hard_stop=84500, take_profits=[dict(price=85200, fraction=.6)])
    out = render_notice(event)
    assert "+27.00 USDT" not in out and "전체 포지션 기준 수익률 미확인" in out


def test_malformed_optional_values_do_not_drop_confirmed_fill():
    out = render_notice(filled(position(hard_stop=float("nan"), exchange_leverage=-10,
        scenario_budget=float("inf"), take_profits=[None])))
    assert "이번 체결: 0.1 BTC" in out and "nan" not in out and "inf" not in out
    assert "환산 -" not in out and "익절 목표: 미확인" in out
    out = render_notice(filled(position(timestamp=10**100)))
    assert "전체 포지션 기준 수익률 미확인" in out
    event = filled()
    del event["position_after"]
    event["take_profits"] = [dict(price=85200, fraction=float("nan"))]
    assert "자료 미확인" in render_notice(event)


def test_partial_stop_and_flat_before_after_are_explicit():
    after = position(partial_stops=[dict(price=84600, quantity=.02)])
    out = render_notice(filled(after, position_before=position(quantity=0, average_entry_price=None,
        hard_stop=None, take_profits=[], partial_stops=[])))
    assert "0 BTC → 0.1 BTC" in out and "부분 손절 목표" in out
    assert "현재 보유물량 20%" in out and "-3.00 USDT" in out
    out = render_notice(filled(position(quantity=0, average_entry_price=None, hard_stop=None)))
    assert "현재 보유 포지션 없음" in out and "예상 손익" not in out


def test_legacy_bad_margin_and_unknown_leverage_never_invented():
    event = filled()
    del event["position_after"]
    event["account_snapshot"] = position()["account_snapshot"]
    event["account_snapshot"]["position_margin"] = -1
    out = render_notice(event)
    assert "-1.00" not in out and "10배" not in out


def test_plan_labels_requested_state_and_initial_equity_budget():
    out = render_notice(dict(kind="PLAN", timestamp=1000, side="LONG", price=84750,
        quantity=.1, hard_stop=84500, plan_action="ADJUST", scenario_budget=200,
        scenario_initial_equity=10000, before_quantity=.05, before_hard_stop=84000))
    assert "계획 수정 요청" in out and "아직 적용 전" in out and "2.00%" in out
    assert "현재 확인된 보유량: 0.05 BTC" in out and "새로 요청할 진입 수량: 0.1 BTC" in out
    assert "84,000.00 USDT → 84,500.00 USDT · 적용 미확정" in out


def test_account_and_position_overflow_never_prints_infinite_return():
    after = position(average_entry_price=1e-308, hard_stop=1e308,
        take_profits=[dict(price=1e308, quantity=.06)])
    after["account_snapshot"]["equity"] = 1e-308
    out = render_notice(filled(after))
    assert "inf" not in out and "nan" not in out


def test_partial_with_after_shows_actual_remaining_quantity():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, side="LONG", fill_confirmed=True,
        quantity=.06, position_after=position(quantity=.04, take_profits=[])))
    assert "전체 보유량: 0.04 BTC" in out and "정리 수량: 0.06 BTC" in out
    assert "정산 미확정" in out


def test_short_direction_return_and_reserved_risk_percent_are_unambiguous():
    out = render_notice(filled(position(side="SHORT", average_entry_price=100, hard_stop=101,
        take_profits=[dict(price=95, quantity=.06)], scenario_risk=96.1309241435,
        scenario_risk_includes_pending=True), side="SHORT"))
    assert "숏(가격 하락에 투자)" in out and "1배 포지션 수익률 +5.00%" in out
    assert "가격 기준" not in out
    assert "비용·미체결까지 반영한 계획 위험: 96.13 USDT (시나리오 시작 순자산의 1.00%)" in out
    assert "분할 진입 체결" not in out and "진입 체결" in out


@pytest.mark.parametrize("magnitude", [1, 1e250])
def test_twenty_targets_are_valid_and_message_is_bounded_without_wrong_runner(magnitude):
    targets = [dict(price=(85200+i)*magnitude, quantity=.005*magnitude) for i in range(20)]
    before = position(quantity=.1*magnitude, average_entry_price=84750*magnitude,
        hard_stop=84500*magnitude, take_profits=targets, partial_stops=targets)
    after = position(**{**before, "hard_stop":84600*magnitude})
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG", protection_confirmed=True,
        position_before=before, position_after=after))
    assert "총 20개" in out and "나머지 18개 상세 생략" in out
    assert "목표 익절 후 남길 물량" not in out
    assert len(out.encode("utf-16-le")) // 2 < 4096
    assert "실제 손실은 계획 한도를 넘을 수 있습니다" in out


def test_stop_before_after_impact_uses_each_snapshot_own_equity_and_size():
    before = position(timestamp=700)
    before["account_snapshot"].update(timestamp=700, equity=5000)
    after = position(hard_stop=84600, quantity=.2)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "SL 가격 정리 가정 손익: -25.00 → -30.00 USDT" in out
    assert "각 조회 시점 순자산 기준): -0.50% → -0.31%" in out
    before["account_snapshot"]["same_account"] = False
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "각 조회 시점 순자산 기준): 미확인 → -0.31%" in out


def test_profitable_short_stop_before_after_preserves_signed_gain():
    before = position(side="SHORT", average_entry_price=100, hard_stop=99,
        take_profits=[], timestamp=900)
    before["account_snapshot"].update(timestamp=900, equity=100)
    after = position(side="SHORT", average_entry_price=100, hard_stop=95,
        take_profits=[])
    after["account_snapshot"].update(equity=200)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="SHORT",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "SL 가격 정리 가정 손익: +0.10 → +0.50 USDT" in out
    assert "각 조회 시점 순자산 기준): +0.10% → +0.25%" in out


def test_plan_is_not_fill_and_no_raw_payload():
    out = render_notice(dict(kind="PLAN",timestamp=1000,side="LONG",price=100,hard_stop=99,
                             order_id="secret",rationale="sensitive payload",scenario_budget=20))
    assert "주문 전" in out and "체결을 뜻하지" in out
    assert "secret" not in out and "sensitive" not in out
    assert "거래소 적용 미확인" in out


@pytest.mark.parametrize("kind", ["FILLED","PARTIAL","CLOSED","PROTECTION","RESOLVED","MODEL_RECOVERED"])
def test_unverified_claim_rejected(kind):
    with pytest.raises(ValueError):
        render_notice(dict(kind=kind,timestamp=1000,side="LONG"))


def test_closed_cost_missing_never_zero():
    event=dict(kind="CLOSED",timestamp=1000,flat_confirmed=True,orders_terminal=True,
               settlement_confirmed=True,net_pnl=1,fees=0.1)
    with pytest.raises(ValueError,match="cost_evidence"):
        render_notice(event)
    out=render_notice({**event,"funding":0})
    assert "확정 순손익: 1.00" in out
    assert "계좌" not in out


def test_partial_unsettled_and_halt_keep_protection():
    out=render_notice(dict(kind="PARTIAL",timestamp=1000,fill_confirmed=True))
    assert "정산 미확정" in out
    out=render_notice(dict(kind="HALTED",timestamp=1000))
    assert "자동 재개하지" in out and "보호는 유지" in out


def test_wrong_account_snapshot_not_attached():
    event=dict(kind="FILLED",timestamp=1000,side="LONG",fill_confirmed=True,
               account_snapshot=dict(same_event=True,same_account=False,position_margin=123456,equity=200000,timestamp=1000))
    assert "123,456" not in render_notice(event)


def test_resolution_notice_specific_not_blanket_recovered():
    out=render_notice(dict(kind="RESOLVED",timestamp=1000,resolution_confirmed=True,resolution="CANCELLED_UNFILLED"))
    assert "미체결 취소 확인" in out


def test_new_notice_preserves_plan_reason_size_and_account_time():
    out=render_notice(dict(kind="FILLED",timestamp=1000,side="LONG",fill_confirmed=True,
        protection_confirmed=True,reason_code="BREAKOUT",remaining_quantity=.03,
        take_profits=[dict(price=101,fraction=.3)],account_snapshot=dict(
            same_event=True,same_account=True,timestamp=990,position_margin=100,equity=10000,
            margin_mode="REGULAR_MARGIN")))
    for text in ["수렴 뒤 돌파","분할 TP 계획","추세 추종 잔여 계획: 70%","총잔량: 0.03","계좌 자료 기준"]:
        assert text in out


def test_exit_preserves_holding_duration_and_reason():
    out=render_notice(dict(kind="PARTIAL",timestamp=1000,entry_timestamp=400,
                           fill_confirmed=True,reason_code="TAKE_PROFIT",remaining_quantity=.01))
    assert "부분 익절" in out and "10.0분" in out and "총잔량: 0.01" in out


def test_recent_post_fill_snapshot_is_labeled_not_backdated():
    event=dict(kind="FILLED",timestamp=1000,side="LONG",fill_confirmed=True,
        account_snapshot=dict(same_event=True,same_account=True,timestamp=1040,
            position_margin=100,equity=10000,margin_mode="REGULAR_MARGIN"))
    assert "체결 후 관측 증거금" in render_notice(event)
    event["account_snapshot"]["timestamp"]=1200
    assert "자료 미확인" in render_notice(event)


@pytest.mark.parametrize('reason', ['llm_output_contract_failed', 'llm_call_failed'])
def test_model_notice_never_exposes_payload_or_claims_exchange_uncertainty(reason):
    out=render_notice(dict(kind='MODEL_ERROR',timestamp=1000,reason_code=reason,
        details='secret exception',input_id='secret id',rationale='secret rationale'))
    assert 'LLM' in out and '이번 판단 미반영' in out
    assert '별도 확인 대상' in out and 'secret' not in out
    assert '체결·취소·보호·정산의 불명확' not in out
    out=render_notice(dict(kind='MODEL_RECOVERED',timestamp=1000,model_validation_confirmed=True))
    assert '검증을 통과' in out and '매매 재개를 뜻하지' in out


def test_model_error_requires_safe_reason_code():
    with pytest.raises(ValueError,match='model_error_reason_required'):
        render_notice(dict(kind='MODEL_ERROR',timestamp=1000,reason_code='raw exception'))
