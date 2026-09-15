"""Public summaries remain readable without borrowing current exit data."""
from types import SimpleNamespace

import pytest
from live import notifier, swing
from live.swing_entry_notice import build_recovered_message


def position():
    return SimpleNamespace(side="long", qty=.089, entry_price=78188.2,
        sl_price=76569.1, initial_risk=144.1, leverage=.72, tranche_index=0,
        tp1_price=80000, tp2_price=81000, tp3_price=82000, liq_price=0,
        entry_time="2026-09-15T00:02:09Z")


def snapshot():
    return {"captured_at": "2026-09-15T02:49:00Z", "account_scope": "swing:UNIFIED",
        "wallet_currency": "USD", "position_currency": "USDT",
        "account": {"margin_mode": "REGULAR_MARGIN"},
        "wallet": {"equity": 178000, "usdt_usd_rate": 1},
        "position": {"symbol": "BTCUSDT", "side": "long", "position_idx": 0,
            "leverage": 5, "position_im": 1388, "qty": .089}}


def test_swing_exit_is_short_and_does_not_borrow_current_position():
    text = swing._build_exit_message(position(), 76810.4, "swing_trend", "exchange",
        -130.29, 7.59, -.9, 9600, 177588, "2026-09-15T08:02:09Z",
        gross_pnl=-122.62, funding_paid=.08, settlement_confirmed=True,
        account_snapshot=snapshot(), operating_capital=9600)
    assert len(text.splitlines()) <= 12
    assert "데모" in text and "-130.29 USDT" in text
    assert "17:02" in text and "KST" in text
    for forbidden in ("177,588", "178,000", "14.5%", "5배", "-0.9배", "positionIM", "스냅샷"):
        assert forbidden not in text
    assert "7.59" in text and "0.08" in text


def test_entry_and_recovery_prioritize_operating_margin_usage():
    pos = position()
    normal = swing._build_entry_message(pos, "exchange", 9600, {},
        native_sl_attached=True, account_snapshot=snapshot())
    record = {**vars(pos), "initial_sl": pos.sl_price}
    recovery = build_recovered_message(record, pos, "demo", snapshot(), 9600)
    for text in (normal, recovery):
        assert len(text.splitlines()) <= 15
        assert "14.5%" in text and "9,600.00 USD" in text
        assert "교차" in text and "5배" in text and "데모" in text
        assert "178,000" not in text and "0.78%" not in text
    assert "새 진입이 아닙니다" in recovery


def test_unconfirmed_exit_keeps_one_financial_warning():
    text = swing._build_exit_message(position(), 76810, "swing_sl", "exchange",
        -130, 7, -.9, 9600, 9600, "2026-09-15T08:02:09Z")
    assert "추정" in text and "정산 미확정" in text
    assert text.count("⚠️") == 1


def test_main_shadow_is_not_labeled_real_money():
    text = notifier._build_entry_message(vars(position()), "shadow")
    assert "가상" in text and "실전" not in text


@pytest.mark.parametrize("mode", ["shadow", "live"])
def test_mode_name_does_not_falsely_claim_real_money(mode):
    pos = position()
    texts = [notifier._build_entry_message(vars(pos), mode),
             build_recovered_message({**vars(pos), "initial_sl": pos.sl_price},
                                     pos, mode, snapshot(), 9600)]
    for text in texts:
        assert "실거래" not in text and "실전" not in text
        assert "가상자금" in text


def test_missing_margin_fields_are_one_warning_and_never_strategy_leverage():
    from live.position_snapshot import compact_entry_lines
    text = "\n".join(compact_entry_lines({}, position()))
    assert text.count("⚠️") == 1
    assert "0.72" not in text and "14.5%" not in text


def test_missing_main_pnl_is_not_zero_profit():
    row = {**vars(position()), "exit_price": 76810, "exit_reason": "sl",
           "exit_time": "2026-09-15T08:02:09Z", "fee_paid": 7, "funding_paid": 0}
    text = notifier._build_exit_message(row, "demo")
    assert "순손익: 확인 불가" in text and "본전" not in text
    assert text.count("⚠️") == 1


@pytest.mark.parametrize("residual,label", [(.08, "비용"), (-.08, "수취")])
def test_exit_residual_cost_and_receipt_are_not_reversed(residual, label):
    text = swing._build_exit_message(position(), 76810, "swing_sl", "exchange",
        -130, 7, -.9, 9600, 9600, "2026-09-15T08:02:09Z",
        funding_paid=residual, settlement_confirmed=True)
    assert f"기타 {label}(추정): 0.08 USDT" in text
    assert "확정 펀딩" not in text
