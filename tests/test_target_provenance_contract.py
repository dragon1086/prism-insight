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
