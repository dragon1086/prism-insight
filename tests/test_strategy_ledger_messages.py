from prism_core.strategy_ledger import StrategyLedger
from prism_core.strategy_ledger_messages import format_campaign

T0 = "2026-09-10T00:00:00Z"
T1 = "2026-09-10T01:00:00Z"


def setup(tmp_path):
    ledger = StrategyLedger(tmp_path / "messages.sqlite")
    ledger.create_book("book", "US", "USD", 1000, 100)
    ledger.apply_target("buy", "book", "campaign", "TEST", 50, 100, T0)
    return ledger


def test_half_position_is_readable_and_not_a_trade_signal(tmp_path):
    ledger = setup(tmp_path)
    state = ledger.mark("mark", "campaign", 110, T1)
    text = format_campaign(state, "campaign")
    assert "50%" in text and "0.500000주" in text and "$100.00" in text
    assert "평가손익: $5.00" in text and "장부 수익률: +0.50%" in text
    assert "실제 주문 신호가 아닙니다" in text
    assert "0주로 단정하지 않음" in text
    assert len(text) < 3500


def test_rejected_execution_never_erases_or_leaks_strategy(tmp_path):
    ledger = setup(tmp_path)
    state = ledger.observe_execution("broker", "campaign", "SECRET_PROFILE", "REJECTED", T1,
                                     intent_ref="SECRET_INTENT")
    text = format_campaign(state, "campaign")
    assert "주문 거절" in text and "0.500000주" in text
    assert "SECRET" not in text


def test_unresolved_old_execution_keeps_known_status(tmp_path):
    ledger = setup(tmp_path)
    text = format_campaign(ledger.snapshot("book"), "campaign", unresolved_execution_overlays=[
        {"campaign_id": "campaign", "status": "REJECTED", "observed_at": T1,
         "execution_profile_ref": "SECRET_PROFILE"}])
    assert "프로필 미확인: 주문 거절" in text
    assert "SECRET" not in text


def test_closed_campaign_and_execution_confirmation_separate(tmp_path):
    ledger = setup(tmp_path)
    state = ledger.sell("sell", "campaign", 110, T1)
    text = format_campaign(state, "campaign")
    assert "청산 완료" in text and "원장 실현손익: $5.00" in text
    assert "연결된 주문·체결 증거 없음" in text
