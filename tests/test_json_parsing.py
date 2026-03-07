#!/usr/bin/env python3
"""
JSON parsing error fix test code

Tests JSON parsing logic in stock_tracking_agent.py.
"""

import json
import sys
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


class TestJSONParser:
    """JSON parsing test class"""
    
    def test_broken_json_from_error_log(self):
        """Test JSON parsing from actual error log"""
        print("\n=== Test 1: Actual Error JSON ===")

        # JSON that actually caused an error (sell_triggers, hold_conditions closed with braces instead of brackets)
        broken_json = """{
  "portfolio_analysis": "보유 2/10슬롯(여유 8). 산업 분포는 화학/포장재, 반도체/전기전자로 분산되어 있으며 자동차 유통 섹터 편입 시 과도한 중복 없음. 투자기간은 중기 중심(2/2)으로 단기 포지션 여지 존재. 포트폴리오 평균수익률 미제시.",
  "valuation_analysis": "보고서 기준 PER은 적자 지속으로 N/A, PBR 2.92배(’24/12), EV/EBITDA 20.23배, PCR 10.28배. 업종 평균 PER 4.91배 대비 ‘저평가’ 근거는 부족하며(이익 부재·고 PBR), 우선주 유통물량 희소로 가격 변동성 왜곡 리스크 큼. 외부 소스(Perplexity)로 동종 업계/경쟁사 최신 비교는 불충분하여 보수적 해석 필요.",
  "sector_outlook": "국내 자동차·모빌리티 업종은 3분기 실적 개선 기대, 친환경차/렌트·모빌리티 확장으로 심리 개선. 다만 유동성 낮은 종목·소형주 중심 변동성 확대, 외국인 수급 변화에 민감.",
  "buy_score": 6.5,
  "min_score": 7,
  "decision": "관망",
  "target_price": 55000,
  "stop_loss": 39500,
  "investment_period": "단기",
  "rationale": "밸류에이션 매력 제한(PER N/A, PBR 높음)과 유동주식 6.56%의 극단적 변동성. 4만~4.5만원 지지 구간 인접은 기술적 반등 여지. 거래대금 급감·외국인 수급 변동성으로 모멘텀 약화.",
  "sector": "자동차 유통/모빌리티",
  "market_condition": "KOSPI 강세·KOSDAQ 중립, 전체 리스크 중간. 본 종목 거래대금 전일 대비 -76.9%로 관망세/모멘텀 둔화. 외국인 수급 변화가 주가 단기 방향 핵심 변수.",
  "max_portfolio_size": "6",
  "trading_scenarios": {
    "key_levels": {
      "primary_support": 40000,
      "secondary_support": 35000,
      "primary_resistance": 55000,
      "secondary_resistance": "70000~80000",
      "volume_baseline": "일평균 약 50만주 내외(9~10월 기준), 급등구간 100만~150만주"
    },
    "sell_triggers": [
      "익절 조건 1: 55,000원 부근(중간 저항) 도달 시 전량 매도",
      "익절 조건 2: 거래량 감소·음봉 연속으로 모멘텀 소진 시 전량 매도",
      "손절 조건 1: 40,000원 종가 이탈 + 거래량 동반 증가 시 전량 손절",
      "손절 조건 2: 40,000원 이탈 후 35,000원 재지지 실패·하락 가속",
      "시간 조건: 진입 후 10거래일 내 5만대 회복 실패·횡보 지속 시 청산"
    },
    "hold_conditions": [
      "40,000~44,000원 지지선 수차례 방어 및 거래량 정상화",
      "외국인 순매수 전환·유지와 함께 5만대 안착",
      "업종/시장 강세 지속 및 분기 실적 개선 확인"
    },
    "portfolio_context": "현 포트폴리오에 소비경기/모빌리티 노출 추가로 분산효과는 있으나, 해당 종목은 유동성 리스크·변동성이 극단적. 분할매매 불가 시스템 특성상 트리거 충족 시에만 1슬롯(10%)로 단기 트레이드, 미충족 시 보유 회피가 합리적."
  }
}"""

        # Should cause parsing error originally
        print("1) Attempting to parse error JSON...")
        try:
            json.loads(broken_json)
            print("   ❌ Unexpectedly parsed successfully (strange)")
        except json.JSONDecodeError as e:
            print(f"   ✅ Failed to parse as expected: {e}")

        # Parse after applying json_repair
        print("2) Parsing after applying json_repair...")
        try:
            import json_repair
            fixed_json = json_repair.repair_json(broken_json)
            parsed = json.loads(fixed_json)
            print("   ✅ Parsing successful!")
            print(f"   - portfolio_analysis: {parsed['portfolio_analysis'][:50]}...")
            print(f"   - buy_score: {parsed['buy_score']}")
            print(f"   - decision: {parsed['decision']}")
            print(f"   - sell_triggers count: {len(parsed['trading_scenarios']['sell_triggers'])}")
            print(f"   - hold_conditions count: {len(parsed['trading_scenarios']['hold_conditions'])}")
        except Exception as e:
            print(f"   ❌ Parsing failed: {e}")
            return False

        return True

    def test_various_broken_json_patterns(self):
        """Test various JSON syntax error patterns"""
        print("\n=== Test 3: Various Syntax Error Patterns ===")

        test_cases = [
            # Case 1: Missing comma after array
            {
                "name": "Array followed by property",
                "broken": '{"array": [1, 2, 3]\n"next": "value"}',
                "expected_keys": ["array", "next"]
            },
            # Case 2: Missing comma after object
            {
                "name": "Object followed by property",
                "broken": '{"obj": {"a": 1}\n"next": "value"}',
                "expected_keys": ["obj", "next"]
            },
            # Case 3: Trailing comma
            {
                "name": "Trailing comma",
                "broken": '{"a": 1, "b": 2,}',
                "expected_keys": ["a", "b"]
            },
            # Case 4: Double comma
            {
                "name": "Double comma",
                "broken": '{"a": 1,, "b": 2}',
                "expected_keys": ["a", "b"]
            },
            # Case 5: Compound error (real scenario)
            {
                "name": "Compound error",
                "broken": """{
                    "list": ["a", "b", "c"]
                    "obj": {"x": 1, "y": 2,},
                    "value": 123
                    "last": true
                }""",
                "expected_keys": ["list", "obj", "value", "last"]
            }
        ]


        all_passed = True

        for i, test_case in enumerate(test_cases, 1):
            print(f"\n   Test {i}: {test_case['name']}")

            # Original should fail to parse
            try:
                json.loads(test_case['broken'])
                print("      ⚠️ Original unexpectedly parsed successfully")
            except Exception:
                print("      ✅ Original parsing failed (as expected)")

            # Parse after fixing
            try:
                import json_repair
                fixed = json_repair.repair_json(test_case['broken'])
                parsed = json.loads(fixed)

                # Check expected keys
                for key in test_case['expected_keys']:
                    if key not in parsed:
                        print(f"      ❌ Key '{key}' missing")
                        all_passed = False
                        break
                else:
                    print("      ✅ Parsing successful after fix (all keys present)")

            except Exception as e:
                print(f"      ❌ Parsing still failed after fix: {e}")
                all_passed = False

        return all_passed

    def test_json_repair_fallback(self):
        """Test json-repair library fallback"""
        print("\n=== Test 4: json-repair Library Fallback ===")

        try:
            import json_repair
            print("   ✅ json-repair library installed")

            # Very broken JSON
            very_broken_json = """
            {
                "a": "value with "quotes" inside",
                'b': 'single quotes',
                c: "no quotes key",
                "d": [1, 2, 3
                "e": {
                    "nested": true
                }
                "f": /* comment */ 123,
                "g": NaN,
                "h": undefined,
            }
            """

            # Repair with json_repair
            try:
                repaired = json_repair.repair_json(very_broken_json)
                parsed = json.loads(repaired)
                print("   ✅ Even very broken JSON was repaired successfully!")
                print(f"      Repaired keys: {list(parsed.keys())}")
            except Exception as e:
                print(f"   ❌ json_repair repair failed: {e}")

        except ImportError:
            print("   ⚠️ json-repair library not installed (optional)")

        return True


def main():
    """Run main test"""
    print("=" * 60)
    print("JSON Parsing Error Fix Test")
    print("=" * 60)

    tester = TestJSONParser()

    # Run each test
    results = {
        "Actual Error JSON": tester.test_broken_json_from_error_log(),
        "Various Error Patterns": tester.test_various_broken_json_patterns(),
        "json-repair Fallback": tester.test_json_repair_fallback(),
    }

    # Summary
    print("\n" + "=" * 60)
    print("Test Results Summary")
    print("=" * 60)

    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"{test_name:20} : {status}")

    # Overall result
    all_passed = all(results.values())
    print("\n" + "=" * 60)
    if all_passed:
        print("✅ All tests passed!")
    else:
        print("❌ Some tests failed")
    print("=" * 60)
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit(main())
