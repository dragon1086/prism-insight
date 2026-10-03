import pytest
from live.scenario_notice import render_notice


def test_plan_is_not_fill_and_no_raw_payload():
    out = render_notice(dict(kind="PLAN",timestamp=1000,side="LONG",price=100,hard_stop=99,
                             order_id="secret",rationale="sensitive payload",scenario_budget=20))
    assert "주문 전" in out and "체결을 뜻하지" in out
    assert "secret" not in out and "sensitive" not in out
    assert "거래소 적용 미확인" in out


@pytest.mark.parametrize("kind", ["FILLED","PARTIAL","CLOSED","PROTECTION","RESOLVED"])
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
