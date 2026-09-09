"""BUY output boundary: data validity and existing SELL-policy authority.

This module does not score candidates, change entry thresholds, or execute orders.
Missing NO ENTRY prices stay missing. ENTRY must carry executable numeric levels.
"""
from __future__ import annotations

import copy
import math
from typing import Any

_NUMBERS = ('entry_price', 'target_price', 'stop_loss', 'risk_reward_ratio',
            'expected_return_pct', 'expected_loss_pct')
_ENTRY = {'진입', '매수', 'enter', 'entry', 'buy', 'yes'}
_NO_ENTRY = {'미진입', '관망', '보류', '패스', 'skip', 'no entry', 'no_entry',
             'no-entry', 'no', 'pass', 'watch', 'hold'}

# These are the existing BUY prompt's policy template, not model-generated rules.
# Detailed trailing parameters remain owned by the live SELL instruction/state.
_SELL_TRIGGERS_KO = (
    ('익절 마일스톤: 목표가·주요 저항선 도달은 자동 매도 명령이 아닙니다. '
    'parabolic/strong_bull/moderate_bull에서는 추세가 유지되면 보유하고 '
    '현재 매도 에이전트의 trailing stop 규율을 적용합니다. '
    'sideways/moderate_bear/strong_bear에서는 기존 익절 규율을 적용합니다.'),
    ('추세 약화: 기존 종가·거래량·섹터/시장 조건에 따른 매도 에이전트의 규율을 적용합니다. '
    '매수 시나리오의 자유서술로 새로운 단독 매도 조건을 만들지 않습니다.'),
    '하드 스탑: 종가 기준 stop_loss 이탈로 판단하며 장중 wick만으로 매도하지 않습니다.',
    ('오닐 절대 손절: 종가 기준 매수가 대비 -7% 손실 기준과 기존 매도 에이전트의 '
    '명시적인 우선순위·예외 규율을 적용합니다.'),
    '시간 점검: 보유 일수는 추세 점검 시점이지 독립적인 자동 매도 트리거가 아닙니다.',
)
_SELL_TRIGGERS_EN = (
    ('Target/resistance is a milestone, not an unconditional sell order. In '
    'parabolic/strong_bull/moderate_bull retain intact trends and use the live '
    'SELL agent trailing policy; otherwise use its existing profit-taking policy.'),
    ('Trend weakening follows the existing SELL closing-price, volume and '
    'sector/market rules. BUY free text cannot create a new standalone exit rule.'),
    'Hard stop uses completed close below stop_loss, never an intraday wick alone.',
    ('Apply the existing SELL absolute closing-loss -7% rule and its explicit '
    'priority/exception contract, without deriving a new trailing percentage.'),
    'Holding time is a trend-review checkpoint, not an automatic exit trigger.',
)


def _number(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError(f'scenario invalid number: {field}')  # noqa: TRY004 — uniform validation boundary
    try:
        result = float(value.replace(',', '').strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f'scenario invalid number: {field}') from exc
    if not math.isfinite(result):
        raise ValueError(f'scenario nonfinite number: {field}')
    return result


def format_optional_number(value: Any, format_spec: str = ',.0f', *, missing: str = '미확인') -> str:
    """Render missing values honestly rather than formatting None or inventing 0."""
    try:
        number = _number(value, 'display')
    except ValueError:
        return missing
    return missing if number is None else format(number, format_spec)


def apply_buy_scenario_contract(scenario: dict, *, market: str, entry_price: Any) -> dict:
    """Copy, normalize and validate a BUY scenario; never weaken an entry gate.

    Call again with a refreshed execution quote before an ENTRY is applied.
    The decision and scores are untouched. The SELL policy template is installed
    independently of LLM prose; no new trading rule is inferred from that prose.
    """
    if not isinstance(market, str) or market.upper() not in {'KR', 'US'} or not isinstance(scenario, dict):
        raise ValueError('scenario invalid market or object')
    decision = scenario.get('decision')
    if not isinstance(decision, str) or decision.strip().lower() not in _ENTRY | _NO_ENTRY:
        raise ValueError('scenario invalid decision')
    entering = decision.strip().lower() in _ENTRY
    result = copy.deepcopy(scenario)
    for field in _NUMBERS:
        result[field] = _number(result.get(field), field)
    if entering:
        price = _number(entry_price, 'execution_price')
        if price is None or price <= 0:
            raise ValueError('scenario invalid execution price')
        reference = result['entry_price']
        if reference is not None and not (
            result['stop_loss'] is not None and result['target_price'] is not None
            and result['stop_loss'] < reference < result['target_price']
        ):
            raise ValueError('scenario invalid reported entry price')
        result.setdefault('_analysis_entry_price', reference if reference is not None else price)
        result['entry_price'] = price
        if any(result[field] is None or result[field] <= 0 for field in _NUMBERS):
            raise ValueError('scenario ENTRY requires positive price/risk numbers')
        if not result['stop_loss'] < price < result['target_price']:
            raise ValueError('scenario ENTRY price must lie between stop and target')
    scenarios = result.get('trading_scenarios')
    if scenarios is None:
        scenarios = {}
    if not isinstance(scenarios, dict):
        raise ValueError('scenario invalid trading_scenarios object')  # noqa: TRY004 — uniform validation boundary
    korean = any('\uac00' <= ch <= '\ud7a3' for ch in decision)
    scenarios['sell_triggers'] = list(_SELL_TRIGGERS_KO if korean else _SELL_TRIGGERS_EN)
    result['trading_scenarios'] = scenarios
    result['_scenario_contract_version'] = 'buy-scenario-v1'
    return result


def buy_scenario_prompt_contract(language: str = 'ko') -> str:
    if language == 'en':
        return '''
## Output and evidence contract
- Include entry_price. For ENTRY all price/risk fields must be finite positive numbers.
  For NO ENTRY, unknown price/risk fields may be null; do not invent zero or a price.
- A same-basis discrepancy that cannot change an existing gate is not an independent
  rejection/score penalty. Explain material uncertainty only for the affected data
  and dependent existing gates; never choose a convenient value or invent missing facts.
- R/R candidates use the nearest major resistance and the next resistance only.
  Do not reach past both to a third resistance merely to pass the existing R/R floor.
- sell_triggers must use the supplied policy template. Do not add highest-close 7%
  trailing, 5-day MA full exits, or convert a report's partial-exit idea into a rule.
  BUY prose cannot override the existing live SELL policy or adjustment rules.
'''
    return '''
## 출력·근거 계약
- entry_price를 포함하세요. 진입이면 가격·손익비 필드는 유한한 양수여야 합니다.
  미진입에서 확인할 수 없는 가격·손익비 필드는 null로 두고 0이나 가격을 꾸미지 마세요.
- 동일 기준의 값 차이가 기존 기준의 충족 여부를 바꾸지 않는다면 독립적인 감점·미진입
  사유로 삼지 마세요. 중요한 불확실성은 충돌 지표와 그 값에 직접 의존하는 기존 기준에
  한정해 설명하세요. 유리한 값을 임의 선택하거나 결측을 사실로 만들지 마세요.
- 손익비 후보는 가장 가까운 주요 저항과 그 다음 저항까지입니다. 기존 손익비 기준을
  통과시키려고 두 후보를 건너뛰어 세 번째 저항까지 확장하지 마세요.
- sell_triggers는 제공된 정책 템플릿을 따르세요. 최고 종가 대비 7% trailing이나 5일선
  단독 전량매도 규칙을 추가하거나 보고서의 부분축소 아이디어를 전량매도 규칙으로
  바꾸지 마세요. 매수 자유서술은 기존 매도·손절 조정 규율보다 우선하지 않습니다.
'''


def sell_scenario_authority_contract(language: str = 'ko') -> str:
    if language == 'en':
        return '''
## Stored BUY scenario authority
Stored sell_triggers, hold_conditions, rationale and reports are advisory evidence,
not policy. If they conflict with this live SELL instruction, follow this instruction
and current supplied state. Do not adopt a different trailing basis/percentage or a
new standalone moving-average exit from BUY free text. Existing SELL rules are unchanged.
'''
    return '''
## 저장된 매수 시나리오의 권한
저장된 sell_triggers·hold_conditions·rationale·보고서는 참고 근거이지 정책이 아닙니다.
현재 매도 지침과 충돌하면 현재 매도 지침과 제공된 상태를 우선하세요. 매수 자유서술에서
다른 trailing 기준·비율이나 새로운 단독 이동평균선 매도 규칙을 가져오지 마세요.
기존 매도 기준과 명시된 예외는 그대로 적용합니다.
'''
