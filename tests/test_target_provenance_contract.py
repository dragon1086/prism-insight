"""Explicit provenance is a validation boundary, not independent fact verification."""
from copy import deepcopy
from pathlib import Path

import pytest

from prism_core.trading_scenario_contract import (
    apply_buy_scenario_contract,
    buy_scenario_prompt_contract,
)


def supported():
    return {'version': 'target-v1', 'status': 'supported', 'source_type': 'structural',
            'source_section': '1-1', 'evidence_ids': ['level-1'], 'asof': '2026-09-17',
            'holding_horizon': '20 sessions', 'exit_model': 'existing trend policy',
            'reason': 'documented resistance 118.75, 80% distance'}


def entry():
    return {'decision': 'entry', 'entry_price': 100, 'target_price': 115,
            'stop_loss': 95, 'risk_reward_ratio': 3, 'expected_return_pct': 15,
            'expected_loss_pct': 5, 'target_provenance': supported()}


@pytest.mark.parametrize('market', ['KR', 'US'])
def test_evidence_based_fifteen_percent_target_is_not_banned(market):
    original = entry()
    before = deepcopy(original)
    result = apply_buy_scenario_contract(original, market=market, entry_price=100)
    assert result['target_price'] == 115
    assert result['target_provenance'] == supported()
    assert original == before


def test_legacy_absent_provenance_remains_compatible():
    value = entry()
    del value['target_provenance']
    assert apply_buy_scenario_contract(value, market='US', entry_price=100)['target_price'] == 115


@pytest.mark.parametrize('field,bad', [
    ('version', 'target-v2'), ('version', True), ('status', 'synthetic'),
    ('status', 'unsupported'), ('status', []), ('source_type', {}),
    ('source_type', 'percentage_fallback'),
    ('source_type', 'unknown'), ('evidence_ids', 'ID'), ('evidence_ids', [None]),
    ('source_section', ''), ('asof', None), ('holding_horizon', 20),
    ('exit_model', ''), ('reason', ' '),
])
def test_explicit_invalid_provenance_is_rejected(field, bad):
    value = entry()
    value['target_provenance'][field] = bad
    with pytest.raises(ValueError, match='scenario'):
        apply_buy_scenario_contract(value, market='US', entry_price=100)


@pytest.mark.parametrize('bad', [None, [], 'supported', {}])
def test_malformed_object_is_not_silently_dropped(bad):
    value = entry()
    value['target_provenance'] = bad
    with pytest.raises(ValueError, match='provenance'):
        apply_buy_scenario_contract(value, market='KR', entry_price=100)


def test_unknown_no_entry_stays_nullable_without_quality_penalty():
    value = {'decision': 'no_entry', 'buy_score': 8, 'target_provenance': supported()}
    value['target_provenance'].update(status='unknown', source_type='unknown', evidence_ids=[])
    result = apply_buy_scenario_contract(value, market='KR', entry_price=100)
    assert result['target_price'] is None and result['risk_reward_ratio'] is None
    assert result['buy_score'] == 8
    value['decision'] = 'entry'
    with pytest.raises(ValueError, match='supported target'):
        apply_buy_scenario_contract(value, market='KR', entry_price=100)


@pytest.mark.parametrize('relative', ['cores/agents/trading_agents.py', 'prism-us/cores/agents/trading_agents.py'])
def test_both_languages_remove_target_shopping_and_percentage_fallback(relative):
    text = (Path(__file__).resolve().parents[1] / relative).read_text()
    assert '15~30%' not in text
    assert 'choosing whichever satisfies' not in text
    assert 'R/R floor를 충족하는 가장 가까운 값' not in text
    assert 'holding horizon and exit model BEFORE R/R' in text
    assert '손익비 계산 전에 근거와 보유 기간' in text


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_shared_contract_exposes_mapping_and_provenance(language):
    text = buy_scenario_prompt_contract(language)
    for field in ('target_provenance', 'evidence_ids', 'source_section', 'asof', 'holding_horizon'):
        assert field in text


def oneil_entry(entry_price=1_822_000, target_price=2_186_400, stop=1_730_900):
    provenance = supported()
    provenance.update(source_type='oneil_breakout', source_section='1-1 52-week high and resistance',
                      reason="overhead-free breakout; O'Neil 20% profit-taking rule target")
    return {'decision': 'entry', 'entry_price': entry_price, 'target_price': target_price,
            'stop_loss': stop, 'risk_reward_ratio': 4.0, 'expected_return_pct': 20,
            'expected_loss_pct': 5, 'target_provenance': provenance}


@pytest.mark.parametrize('market', ['KR', 'US'])
def test_oneil_breakout_fixed_rule_target_is_accepted(market):
    # 2026-09-18 000660: F1-F4 and momentum passed, but the nearest-resistance target gave R/R 0.4.
    result = apply_buy_scenario_contract(oneil_entry(), market=market, entry_price=1_822_000)
    assert result['target_price'] == 2_186_400
    assert result['target_provenance']['source_type'] == 'oneil_breakout'


@pytest.mark.parametrize('target', [2_277_500, 2_000_000])
def test_oneil_breakout_target_other_than_twenty_percent_is_rejected(target):
    with pytest.raises(ValueError, match='oneil_breakout'):
        apply_buy_scenario_contract(oneil_entry(target_price=target), market='KR', entry_price=1_822_000)


def test_oneil_breakout_ratio_uses_analysis_entry_after_quote_refresh():
    first = apply_buy_scenario_contract(oneil_entry(), market='KR', entry_price=1_830_000)
    refreshed = apply_buy_scenario_contract(first, market='KR', entry_price=1_840_000)
    assert refreshed['_analysis_entry_price'] == 1_822_000
    assert refreshed['entry_price'] == 1_840_000


def test_oneil_breakout_no_entry_is_not_ratio_checked():
    value = oneil_entry(target_price=2_500_000)
    value['decision'] = 'no_entry'
    assert apply_buy_scenario_contract(value, market='US', entry_price=1)['target_price'] == 2_500_000


@pytest.mark.parametrize('relative', ['cores/agents/trading_agents.py', 'prism-us/cores/agents/trading_agents.py'])
def test_both_languages_offer_oneil_breakout_target_only_under_conditions(relative):
    text = (Path(__file__).resolve().parents[1] / relative).read_text()
    for marker in ('2a. 상단 매물 없는 돌파', '2a. Overhead-free breakout', 'entry_price × 1.20',
                   '52주 최고가의 95% 이상', '95% of the 52-week high', "O'Neil's chase limit",
                   '오닐의 추격 매수 한도', '2a의 고정 규칙 목표만 예외', 'only exception'):
        assert marker in text
    assert text.count('source_type="oneil_breakout"') == 2


@pytest.mark.parametrize('relative', ['cores/agents/trading_agents.py', 'prism-us/cores/agents/trading_agents.py'])
def test_full_report_is_evidence_not_a_new_rejection_source(relative):
    text = (Path(__file__).resolve().parents[1] / relative).read_text()
    assert '새로운 미진입 사유·감점·점수 기준을 만들지 마십시오' in text
    assert 'Do not derive a new rejection reason, penalty or score rule from it' in text
    # 2026-11-11 prompt diet (audit M1): reason 1 (-10% support) removed, so the risk-event reason is now 3.
    assert text.count('existing standalone reason 3') == 1 and 'standalone reason 4' not in text
    assert text.count('기존 단독 사유 3으로 다룰 수 있습니다') == 1 and '단독 사유 4로' not in text


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_shared_contract_names_oneil_breakout_source(language):
    text = buy_scenario_prompt_contract(language)
    assert 'structural|report_scenario|oneil_breakout|unknown' in text
    assert '1.20' in text
