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


@pytest.mark.parametrize('invalid', ['missing', 'nonflat', 'wrong_account', 'wrong_event', 'unverified', 'stale', 'prefill', 'negative', 'nan', 'infinity', 'boolean'])
def test_closed_equity_requires_same_event_verified_post_exit_flat_snapshot(invalid):
    snapshot = position(quantity=0, timestamp=1063)
    snapshot['account_snapshot'].update(timestamp=1063, equity=10046.15)
    if invalid == 'missing':
        snapshot = None
    elif invalid == 'nonflat':
        snapshot['quantity'] = .1
    elif invalid == 'unverified':
        snapshot['verified'] = False
    elif invalid in ('stale', 'prefill'):
        snapshot['timestamp'] = 1121 if invalid == 'stale' else 999
        snapshot['account_snapshot']['timestamp'] = snapshot['timestamp']
    else:
        account = snapshot['account_snapshot']
        if invalid in ('wrong_account', 'wrong_event'):
            account['same_account' if invalid == 'wrong_account' else 'same_event'] = False
        else:
            account['equity'] = {'negative': -1, 'nan': float('nan'), 'infinity': float('inf'), 'boolean': True}[invalid]
    event = dict(kind='CLOSED', timestamp=1000, position_after=snapshot,
                 settlement_confirmed=True, flat_confirmed=True, orders_terminal=True,
                 net_pnl=46.15, fees=1., funding=0., scenario_initial_equity=10000)
    out = render_notice(event)
    assert '💰 종료 후 순자산: 미확인' in out
    assert '10,046.15 USDT' not in out


@pytest.mark.parametrize('account_time', [999, 1000, 1064, 1121])
def test_closed_equity_account_and_flat_observation_must_be_identical(account_time):
    snapshot = position(quantity=0, timestamp=1063)
    snapshot['account_snapshot'].update(timestamp=account_time, equity=10046.15)
    event = dict(kind='CLOSED', timestamp=1000, position_after=snapshot,
                 settlement_confirmed=True, flat_confirmed=True, orders_terminal=True,
                 net_pnl=46.15, fees=1., funding=0., scenario_initial_equity=10000)
    assert '종료 후 순자산: 미확인' in render_notice(event)


def filled(after=None, **updates):
    value = dict(kind="FILLED", timestamp=1000, side="LONG", fill_confirmed=True,
        protection_confirmed=True, price=84750, quantity=.1,
        position_after=position() if after is None else after)
    value.update(updates)
    return value


def test_protection_always_shows_unchanged_holdings_and_tp():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_before=position(timestamp=900), position_after=position(hard_stop=84600)))
    for text in ("0.1 BTC", "평단 84,750.00", "🎯 익절 TP 85,200.00", "+27.00 USDT", "60%(0.06 BTC)"):
        assert text in out


@pytest.mark.parametrize('side,prices', [('LONG', [85200,85500,86000]), ('SHORT',[84300,84000,83500])])
def test_three_tp_tiers_are_all_visible_with_quantities_and_pnl(side,prices):
    after=position(side=side,take_profits=[dict(price=p,quantity=q) for p,q in zip(prices,[.02,.03,.05])])
    out=render_notice(filled(after,side=side))
    for p in prices:assert f'{p:,.2f}' in out
    for q in ('0.02 BTC','0.03 BTC','0.05 BTC'):assert q in out
    assert out.count('해당 물량 예상 손익')==3
    assert '나머지 1개 상세 생략' not in out
    assert len(out.encode('utf-16-le'))//2<4096


def test_three_original_plan_targets_remain_visible_without_live_setting_claim():
    event=filled(position(timestamp=1),take_profits=[dict(price=p,fraction=f) for p,f in
        ((85200,.2),(85500,.3),(86000,.5))],take_profits_scope='entry_intent_plan')
    out=render_notice(event)
    assert '86,000.00' in out and '계획 물량의 50%' in out
    assert 'TP 설정 미확인' in out
    assert '해당 물량 예상 손익' not in out


def test_protection_empty_tp_explicit_runner_and_compact_accounting():
    after = position(quantity=.072, average_entry_price=84780, hard_stop=85140,
                     take_profits=[], scenario_realized_net_pnl=24.10042936,
                     scenario_accounting_confirmed=True, scenario_risk=30,
                     scenario_risk_includes_pending=True)
    after['account_snapshot'].update(equity=9662.79822, position_margin=617.1513192)
    before = dict(after, timestamp=900, hard_stop=85000)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, entry_timestamp=500,
                             protection_confirmed=True, position_before=before, position_after=after))
    for text in ("고정 TP 없음", "남은 전량 추세 추종", "0.072 BTC", "평단 84,780.00",
                 "85,000.00 → 85,140.00", "+25.92 USDT", "+50.02 USDT", "계좌 +0.52%"):
        assert text in out
    assert "첫 진입" not in out
    assert out.count("01/01 09:16:40 KST") == 1
    assert len(out) <= 550 and len(out.splitlines()) <= 23


def test_protection_missing_tp_is_not_reported_as_no_tp():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_before=position(timestamp=900), position_after=position(take_profits=None)))
    assert "익절 TP" in out and "미확인" in out
    assert "고정 TP 없음" not in out


def test_current_targets_keep_distinct_account_observation_time():
    after = position()
    after['account_snapshot']['timestamp'] = 990
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_before=position(timestamp=900), position_after=after))
    assert "계좌 01/01 09:16:30 KST" in out
    assert out.count("01/01 09:16:40 KST") == 1


def test_empty_tp_does_not_assert_unconfirmed_sl_protection():
    out = render_notice(filled(position(take_profits=[], hard_stop=None), protection_confirmed=False))
    assert "고정 TP 없음" in out
    assert "남은 전량 추세 추종 · SL 보호 미확인" in out
    assert "⚠ 보호주문 적용 미확인" in out


def test_compact_initial_status_is_not_a_new_entry():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000,
        entry_timestamp=900, protection_confirmed=True, change_type="initial_protection",
        position_after=position(scenario_risk=48.57, scenario_risk_includes_pending=True)))
    assert "기존 포지션" in out and "신규 진입 아님" in out
    assert "비용·미체결 포함" in out
    assert len(out) <= 750 and len(out.splitlines()) <= 28


def test_compact_change_compares_price_and_keeps_current_targets():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_before=position(timestamp=900), position_after=position(hard_stop=84600)))
    assert "84,500.00 → 84,600.00" in out
    assert "예상 손익: -15.00 USDT" in out
    assert "SL 손익 -25.00 →" not in out
    assert "변경 없음" not in out and "익절 목표" not in out
    assert len(out) <= 650 and len(out.splitlines()) <= 24


def test_protection_without_before_keeps_event_timestamp():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_after=position(timestamp=1050)))
    assert "01/01 09:16:40 KST" in out.splitlines()[1]
    assert "01/01 09:17:30 KST" in out


@pytest.mark.parametrize("unknown_targets", [None, [None], "invalid"])
def test_changed_notice_groups_unknown_protection_not_unchanged(unknown_targets):
    before = position(timestamp=900, hard_stop=None, take_profits=unknown_targets,
        partial_stops=unknown_targets)
    after = position(hard_stop=None, take_profits=unknown_targets, partial_stops=unknown_targets)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, protection_confirmed=True,
        position_before=before, position_after=after))
    assert "미확인: 손절 SL·익절 TP·부분 손절" in out
    assert out.count("미확인:") == 1
    assert "변경 없음" not in out


@pytest.mark.parametrize("stage,title", [("initial", "첫 진입"), ("additional", "추가 체결"), (None, "체결 확인")])
def test_entry_stage_has_explicit_lifecycle_title(stage, title):
    out = render_notice(filled(entry_stage=stage, entry_timestamp=900, reason_code="BREAKOUT"))
    assert title in out
    assert len(out) <= 750 and len(out.splitlines()) <= 28


def test_additional_fill_compares_verified_quantity_and_average():
    out = render_notice(filled(entry_stage="additional", position_before=position(
        timestamp=900, quantity=.05, average_entry_price=84000)))
    assert "0.05 BTC → 0.1 BTC" in out
    assert "84,000.00 USDT → 84,750.00 USDT" in out
    assert "+27.00 USDT" in out


def test_final_exit_fill_is_not_called_partial_or_settled():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        position_after=position(quantity=0, average_entry_price=None, hard_stop=None)))
    assert "전량 청산 체결" in out and "정산 미확정" in out
    assert "부분 청산" not in out and "정산 확인" not in out


def test_unknown_exit_quantity_does_not_assert_partial_or_flat():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True))
    assert "잔량 확인 중" in out
    assert "부분 청산" not in out and "전량 청산" not in out


def test_partial_exit_is_compact_and_keeps_remaining_protection_and_settlement():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        protection_confirmed=True, quantity=.06, price=85200, entry_price=84750,
        entry_timestamp=500, reason_code="TAKE_PROFIT",
        position_after=position(quantity=.04, take_profits=[])))
    assert "부분 청산" in out and "보유 0.04 BTC" in out
    assert "손절 SL" in out and "남은 전량" in out and "정산 미확정" in out
    assert len(out) <= 850 and len(out.splitlines()) <= 38


def test_readable_sample_math_and_distinct_risk_bases():
    out = render_notice(filled())
    for expected in ("8.86%", "60%(0.06 BTC)", "+0.53%", "+5.31%", "+27.00 USDT",
                     "+0.28%", "-25.00 USDT", "-0.26%", "-0.29%", "-2.95%", "2.00%",
                     "증거금≠손실한도", "수수료·슬리피지·펀딩"):
        assert expected in out


def test_add_uses_whole_position_average_not_last_fill():
    out = render_notice(filled(price=86000, quantity=.01))
    assert "이번 체결: 0.01 BTC" in out
    assert "평단 84,750.00" in out
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
    assert out.count("01/01 09:16:40 KST") == 1 and "변경 없음" not in out
    assert "🛡 손절 SL 84,500.00 USDT" in out
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
    out = render_notice(filled(position(exchange_leverage=None)))
    assert "10배" not in out and "배율 미확인" in out
    for targets in ([dict(price=85200, quantity=.2)],
                    [dict(price=85200, quantity=.06), dict(price=85300, quantity=.06)],
                    [dict(price=float("inf"), quantity=.06)]):
        out = render_notice(filled(position(take_profits=targets)))
        assert "+27.00 USDT" not in out and "미확인: 익절 TP" in out


def test_initial_protection_never_fabricates_before():
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, change_type="initial_protection", position_after=position()))
    assert "기존 포지션" in out and "이전 상태 미확인" in out
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
    assert "환산 -" not in out and "미확인: 손절 SL·익절 TP" in out
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
    assert "0 BTC → 0.1 BTC" in out and "부분 손절" in out
    assert "20%(0.02 BTC)" in out and "-3.00 USDT" in out
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
    assert "보유 0.04 BTC" in out and "이번 체결: 0.06 BTC" in out
    assert "정산 미확정" in out


def test_short_direction_return_and_reserved_risk_percent_are_unambiguous():
    out = render_notice(filled(position(side="SHORT", average_entry_price=100, hard_stop=101,
        take_profits=[dict(price=95, quantity=.06)], scenario_risk=96.1309241435,
        scenario_risk_includes_pending=True), side="SHORT"))
    assert "숏" in out and "1배 +5.00%" in out
    assert "가격 기준" not in out
    assert "누적손실+잔여위험 1.00%(96.13 USDT)" in out
    assert "분할 진입 체결" not in out and "진입 체결" in out


@pytest.mark.parametrize("magnitude", [1, 1e250])
def test_twenty_targets_are_valid_and_message_is_bounded_without_wrong_runner(magnitude):
    targets = [dict(price=(85200+i)*magnitude, quantity=.005*magnitude) for i in range(20)]
    before = position(quantity=.1*magnitude, average_entry_price=84750*magnitude,
        hard_stop=84500*magnitude, take_profits=targets, partial_stops=targets)
    after = position(**{**before, "hard_stop":84600*magnitude})
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG", protection_confirmed=True,
        position_before=before, position_after=after))
    assert "익절 TP 총 20개" in out  # current TP remains visible even unchanged
    assert len(out.encode("utf-16-le")) // 2 < 4096
    assert "실제 손실은 목표 초과 가능" in out
    changed_targets = [dict(price=item['price'] + 100*magnitude, quantity=item['quantity'])
                       for item in targets]
    changed = position(**{**after, "take_profits": changed_targets, "partial_stops": changed_targets})
    events = [filled(after), dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, position_before=before, position_after=changed)]
    for event in events:
        out = render_notice(event)
        assert out.count("총 20개") == 2
        assert out.count("나머지 17개 상세 생략") == 1  # three TPs remain visible
        assert out.count("나머지 18개 상세 생략") == 1  # partial SL remains compact
        assert "익절 후" not in out  # all quantity is assigned across the full 20 targets
        assert len(out.encode("utf-16-le")) // 2 < 4096
        assert "실제 손실은 목표 초과 가능" in out


def test_current_stop_impact_uses_current_equity_and_size_not_prior_account():
    before = position(timestamp=700)
    before["account_snapshot"].update(timestamp=700, equity=5000)
    after = position(hard_stop=84600, quantity=.2)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "예상 손익: -30.00 USDT · 계좌 -0.31%" in out
    assert "-0.50%" not in out
    before["account_snapshot"]["same_account"] = False
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="LONG",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "예상 손익: -30.00 USDT · 계좌 -0.31%" in out


def test_profitable_short_stop_before_after_preserves_signed_gain():
    before = position(side="SHORT", average_entry_price=100, hard_stop=99,
        take_profits=[], timestamp=900)
    before["account_snapshot"].update(timestamp=900, equity=100)
    after = position(side="SHORT", average_entry_price=100, hard_stop=95,
        take_profits=[])
    after["account_snapshot"].update(equity=200)
    out = render_notice(dict(kind="PROTECTION", timestamp=1000, side="SHORT",
        protection_confirmed=True, position_before=before, position_after=after))
    assert "99.00 → 95.00 USDT" in out
    assert "예상 손익: +0.50 USDT · 계좌 +0.25%" in out


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


def test_recovered_closed_notice_is_old_trade_and_keeps_evidence_guard():
    event = dict(kind="CLOSED", timestamp=1000, entry_timestamp=900, recovery_confirmation=True,
                 flat_confirmed=True, orders_terminal=True, settlement_confirmed=True,
                 quantity=.1, entry_price=100, price=101, net_pnl=.08, fees=.02, funding=0)
    text = render_notice(event)
    assert "이전 매매 정산 확인 · 지연 안내" in text
    assert "BTC 데모" in text
    assert "새 진입·추가 주문이 아닙니다" in text
    event["flat_confirmed"] = False
    with pytest.raises(ValueError, match="closure_evidence_required"):
        render_notice(event)


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


def test_readable_blocks_show_notional_and_verified_equity_without_fake_margin():
    out = render_notice(filled(quantity=.03, price=85000, entry_stage="additional",
        position_before=position(timestamp=900, quantity=.07)))
    for text in ("\n\n🧾", "\n\n📦", "\n\n💰", "\n\n🛡", "\n\n🎯",
                 "체결금액(명목): 2,550.00 USDT · 증거금 아님",
                 "계좌 순자산: 9,613.09 USDT", "0.07 BTC → 0.1 BTC",
                 "총 증거금 851.85 USDT", "관측 기준"):
        assert text in out
    assert "추가 증거금" not in out and "**" not in out and "<b>" not in out


def test_partial_preserves_remaining_target_money_and_equity_percent():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        protection_confirmed=True, quantity=.06, price=85200, entry_price=84750,
        position_before=position(timestamp=900), position_after=position(quantity=.04,
            take_profits=[dict(price=85500, quantity=.02)])))
    for text in ("0.1 BTC → 0.04 BTC", "-10.00 USDT", "계좌 -0.10%",
                 "+15.00 USDT", "계좌 +0.16%", "9,613.09 USDT", "정산 미확정"):
        assert text in out
    assert "확정 순손익:" not in out


@pytest.mark.parametrize("initial", [None, 0, -10, float("nan"), True])
def test_closed_invalid_start_equity_does_not_invent_account_return(initial):
    out = render_notice(dict(kind="CLOSED", timestamp=1000, flat_confirmed=True,
        orders_terminal=True, settlement_confirmed=True, net_pnl=-11.71,
        fees=6.35, funding=.04, scenario_initial_equity=initial))
    assert "대비" not in out


def test_closed_account_percent_uses_start_not_current_account():
    out = render_notice(dict(kind="CLOSED", timestamp=1000, flat_confirmed=True,
        orders_terminal=True, settlement_confirmed=True, net_pnl=-11.71,
        fees=6.35, funding=.04, scenario_initial_equity=10000,
        account_snapshot=dict(same_event=True, same_account=True, timestamp=1000, equity=100)))
    assert "시작 계좌 10,000.00 USDT 대비 -0.12%" in out
    assert "-11.71%" not in out


def test_confirmed_cumulative_net_and_remaining_stop_are_not_double_counted():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        protection_confirmed=True, quantity=.06, price=85200,
        position_after=position(quantity=.04, take_profits=[],
            scenario_realized_net_pnl=23, scenario_accounting_confirmed=True)))
    assert "누적 실현손익(기록된 비용 포함): +23.00 USDT" in out
    assert "SL 시 매매 전체 예상: +13.00 USDT · 계좌 +0.14%" in out
    assert "TP/SL은 비용 전, 전체 예상은 향후 비용 전(수수료·슬리피지·펀딩)" in out
    assert "정산 미확정" in out  # aggregate accounting does not settle this fill


@pytest.mark.parametrize("confirmed,net", [(False, 23), (None, 23), (True, float("nan")), (True, None)])
def test_unconfirmed_cumulative_net_never_used_for_combined_result(confirmed, net):
    out = render_notice(filled(position(scenario_realized_net_pnl=net,
        scenario_accounting_confirmed=confirmed)))
    assert "SL 시 매매 전체 예상" not in out
    assert "누적 실현손익" not in out


def test_exact_partial_gross_is_not_promoted_to_settled_net():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        protection_confirmed=True, quantity=.06, price=85200, fill_gross_pnl=27,
        fill_gross_pnl_confirmed=True,
        position_after=position(quantity=.04, take_profits=[])))
    assert "이번 청산 가격손익: +27.00 USDT · 계좌 +0.28%" in out
    assert "수수료·펀딩 전 · 확정 순손익 아님" in out
    assert "정산 미확정" in out
    assert "확정 순손익:" not in out


@pytest.mark.parametrize("confirmed", [None, False])
def test_partial_gross_requires_specific_confirmation_flag(confirmed):
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        fill_gross_pnl_confirmed=confirmed,
        fill_gross_pnl=27, position_after=position(quantity=.04, take_profits=[])))
    assert "이번 청산 가격손익:" not in out


def test_partial_result_precedes_remaining_risk_and_is_not_repeated():
    out = render_notice(dict(kind="PARTIAL", timestamp=1000, fill_confirmed=True,
        fill_gross_pnl_confirmed=True, fill_gross_pnl=27,
        position_after=position(quantity=.04, take_profits=[])))
    assert out.index("이번 청산 가격손익") < out.index("📦 전체 포지션")
    assert out.index("정산 미확정") < out.index("📦 전체 포지션")
    assert out.count("정산 미확정") == 1
    assert "💵 청산 결과" not in out
    assert "TP/SL은 비용 전, 전체 예상은 향후 비용 전(수수료·슬리피지·펀딩)" in out
