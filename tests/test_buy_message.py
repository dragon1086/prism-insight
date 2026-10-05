"""Shared KR/US buy message: Korean, compact, no repeated SELL-policy template."""
from firebase_bridge import detect_market, detect_type
from messaging.korean_trading_message import render_korean_trading_message
from prism_core.buy_message import render_buy_message
from prism_core.trading_scenario_contract import _SELL_TRIGGERS_KO


def _scenario(**extra):
    scenario = {
        "target_price": 934.24, "stop_loss": 852.0, "investment_period": "단기", "sector": "Technology",
        "rationale": "최근 두 분기 영업흑자를 확인했습니다.", "valuation_analysis": "선행 PER 16.05배입니다.",
        "sector_outlook": "기술 섹터는 상승 추세입니다.",
        "trading_scenarios": {
            "key_levels": {"primary_resistance": 945.57, "secondary_resistance": "$979.52",
                           "primary_support": 877.5, "secondary_support": 852.0,
                           "volume_baseline": "직전 20거래일 평균 4,294,080주입니다."},
            "sell_triggers": list(_SELL_TRIGGERS_KO),
            "hold_conditions": ["20·50일선 위의 상승 추세가 이어지는 동안 보유합니다."],
            "portfolio_context": "최대 8슬롯을 적용합니다."},
    }
    scenario.update(extra)
    return scenario


def _us(**kwargs):
    defaults = dict(
        market="US", company_name="Seagate Technology Holdings PLC", ticker="STX", current_price=888.0,
        scenario=_scenario(),
        rank_change_msg="Trading value: $2268.7M (prev: $11509.5M, change: ▼80.3%), Volume ratio: 0.53x",
        trigger_win_rate="📡 실제 매매: 58건, 승률 33%, PF 1.61 / 관찰 후보: 30일 상승 비율 43% (n=443)")
    defaults.update(kwargs)
    return render_korean_trading_message(render_buy_message(**defaults))


def test_us_buy_message_is_korean_and_compact():
    text = _us()
    assert text.startswith("📈 신규 매수 | Seagate Technology Holdings PLC (STX)\n💵 매수가 $888.00\n")
    assert "🎯 목표가 $934.24 (+5.2%)" in text
    assert "⛔ 손절가 $852.00 (-4.1%, 장중 이탈 시 즉시 매도)" in text
    assert "📊 거래대금 $2,268.7M (전일 $11,509.5M, ▼80.3%), 거래량 0.53배" in text
    assert "  • 관찰 후보: 30일 상승 비율 43% (표본 443건)" in text
    assert "  저항 1차 $945.57 · 2차 $979.52\n  ━ 현재 $888.00 ━\n  지지 1차 $877.50 · 2차 $852.00" in text
    for label in ("New Buy", "Buy Price", "Target:", "Stop Loss", "Trading Scenario", "Sell Signals",
                  "Volume ratio", "Rationale", "=" * 10, "Whole shares"):
        assert label not in text
    # The SELL-policy template and the volume-baseline prose are not repeated in every message.
    assert "하드 스탑" not in text and "시간 점검" not in text and "4,294,080주" not in text


def test_bridge_still_classifies_the_buy_message_as_analysis():
    # No "트리거" label in the message. (A name containing "Holdings" is a separate bridge issue.)
    text = _us(company_name="Seagate Technology")
    assert detect_type(text) == "analysis" and detect_market(text) == "us"


def test_kr_message_and_add_entry_header():
    text = render_buy_message(
        market="KR", company_name="삼성전자", ticker="005930", current_price=70000,
        scenario={"target_price": 77000, "stop_loss": 66500, "sector": "반도체", "rationale": "근거"},
        add_entry=(2, 68500))
    assert text.startswith("📈 추가 진입 (2차) | 삼성전자 (005930)\n💵 이번 진입가 70,000원 · 누적 평단가 68,500원\n")
    assert "🎯 목표가 77,000원 (+10.0%)" in text and "⛔ 손절가 66,500원 (-5.0%, 장중 이탈 시 즉시 매도)" in text
    assert detect_market(text) == "kr"


def test_missing_prices_are_shown_as_unknown_without_a_percentage():
    text = render_buy_message(market="US", company_name="X", ticker="X", current_price=10.0,
                              scenario={"rationale": "근거"})
    assert "🎯 목표가 미확인\n" in text and "⛔ 손절가 미확인 (장중 이탈 시 즉시 매도)" in text
    assert "🏷 업종 미확인 · 단기" in text
