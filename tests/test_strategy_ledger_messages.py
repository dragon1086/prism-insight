from prism_core.strategy_ledger import StrategyLedger
from prism_core.strategy_ledger_messages import format_campaign

T0 = "2026-09-10T00:00:00Z"
T1 = "2026-09-10T01:00:00Z"


def setup(tmp_path):
    ledger = StrategyLedger(tmp_path / "messages.sqlite")
    ledger.create_book("book", "US")
    ledger.apply_target("buy", "book", "campaign", "TEST", 50, 100, T0)
    return ledger


def test_half_position_is_readable_and_not_a_trade_signal(tmp_path):
    ledger = setup(tmp_path)
    state = ledger.mark("mark", "campaign", 110, T1)
    text = format_campaign(state, "campaign")
    assert "50%" in text and "점유 슬롯: 1개" in text and "100.00 USD" in text
    assert "미실현 1슬롯 기여: +5.00%" in text and "고정 10슬롯 기준 기여: +0.50%" in text
    assert "투입분 가격수익률: +10.00%" in text
    assert "조건 충족 시에만 가능" in text
    assert not any(word in text for word in ("가상 수량", "가상 현금", "평가자산", "미투입 예산"))
    assert "실제 주문 신호가 아닙니다" in text
    assert "0주로 단정하지 않음" in text
    assert len(text) < 3500


def test_rejected_execution_never_erases_or_leaks_strategy(tmp_path):
    ledger = setup(tmp_path)
    state = ledger.observe_execution("broker", "campaign", "SECRET_PROFILE", "REJECTED", T1,
                                     intent_ref="SECRET_INTENT")
    text = format_campaign(state, "campaign")
    assert "주문 거절" in text and "점유 슬롯: 1개" in text
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
    assert "청산 완료" in text and "실현 1슬롯 기여: +5.00%" in text
    assert "연결된 주문·체결 증거 없음" in text


def test_isolated_partial_exit_cancels_remainder_and_omits_aggregation(tmp_path):
    ledger = StrategyLedger(tmp_path / "isolated.sqlite")
    ledger.create_book("isolated:campaign", "KR", cohort=None, mode="VALIDATION_ONLY")
    ledger.apply_target("buy", "isolated:campaign", "c", "**TEST_[link](url)", 50, 100, T0)
    state = ledger.sell("reduce", "c", 110, T1, quantity=".0025")
    text = format_campaign(state, "c")
    assert "잔여 배분: 25%" in text
    assert "추가 가능 잔여 배분: 0%" in text
    assert "권한이 취소" in text
    assert "합산 금지" in text and "고정 10슬롯" not in text
    assert "[link]" not in text


def test_many_execution_records_are_bounded_and_identifiers_hidden(tmp_path):
    ledger = setup(tmp_path)
    for i in range(20):
        ledger.observe_execution(f"exec-{i}", "campaign", f"SECRET-{i}", "FILLED", T1,
                                 confirmed_quantity=1, confirmed_price=100, evidence_source="SECRET",
                                 intent_ref=f"SECRET-INTENT-{i}")
    text = format_campaign(ledger.snapshot("book"), "campaign")
    assert "외 집행 기록 17건 생략" in text
    assert "SECRET" not in text and len(text) < 3500
