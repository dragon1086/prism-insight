from copy import deepcopy

import pytest

from prism_core.trading_scenario_contract import (
    apply_buy_scenario_contract,
    buy_scenario_prompt_contract,
    format_optional_number,
    sell_scenario_authority_contract,
)


def entry():
    return {
        'decision': '진입', 'target_price': 120, 'stop_loss': 93,
        'risk_reward_ratio': 2.9, 'expected_return_pct': 20,
        'expected_loss_pct': 7, 'buy_score': 7,
        'trading_scenarios': {'key_levels': {'primary_support': 93},
                              'sell_triggers': ['highest close minus 7%', '5-day MA full exit']},
    }


@pytest.mark.parametrize('market', ['KR', 'US'])
def test_valid_entry_preserves_economic_inputs_and_replaces_untrusted_exit_rules(market):
    value = entry()
    before = deepcopy(value)
    result = apply_buy_scenario_contract(value, market=market, entry_price=100)
    assert value == before
    assert result['entry_price'] == 100
    assert result['target_price'] == 120 and result['stop_loss'] == 93
    assert result['buy_score'] == 7
    assert result['trading_scenarios']['key_levels'] == before['trading_scenarios']['key_levels']
    assert '5-day MA' not in str(result['trading_scenarios']['sell_triggers'])
    assert 'highest close' not in str(result['trading_scenarios']['sell_triggers'])
    assert result['_scenario_contract_version'] == 'buy-scenario-v1'


@pytest.mark.parametrize('field', ['target_price', 'stop_loss', 'risk_reward_ratio', 'expected_return_pct', 'expected_loss_pct'])
@pytest.mark.parametrize('bad', [None, float('nan'), float('inf'), True, 'unknown', -1])
def test_entry_rejects_missing_nonfinite_and_invalid_risk_values(field, bad):
    value = entry()
    value[field] = bad
    with pytest.raises(ValueError, match='scenario'):
        apply_buy_scenario_contract(value, market='KR', entry_price=100)


@pytest.mark.parametrize('price', [90, 93, 120, 121, None, True, float('nan')])
def test_fresh_price_must_still_fit_existing_price_levels(price):
    with pytest.raises(ValueError, match='scenario'):
        apply_buy_scenario_contract(entry(), market='US', entry_price=price)


@pytest.mark.parametrize('decision', ['미진입', 'no_entry', 'No Entry', 'Skip', '관망'])
def test_no_entry_unknowns_remain_unknown_and_display_without_fabrication(decision):
    value = {'decision': decision, 'target_price': None, 'stop_loss': None,
             'risk_reward_ratio': None, 'rationale': 'Conflicting closing prices'}
    result = apply_buy_scenario_contract(value, market='KR', entry_price=100)
    assert result['target_price'] is None and result['stop_loss'] is None
    assert result['entry_price'] is None
    assert result['risk_reward_ratio'] is None
    assert result['rationale'] == value['rationale']
    assert format_optional_number(result['target_price']) == '미확인'
    assert format_optional_number(None, missing='Unknown') == 'Unknown'


def test_string_numbers_are_accepted_without_accepting_currency_prose():
    value = entry()
    value['target_price'] = '1,200'
    assert apply_buy_scenario_contract(value, market='KR', entry_price=100)['target_price'] == 1200
    value['stop_loss'] = '93원'
    with pytest.raises(ValueError):
        apply_buy_scenario_contract(value, market='KR', entry_price=100)


def test_optional_format_does_not_display_nonfinite_prices():
    assert format_optional_number(float('inf')) == '미확인'
    assert format_optional_number(1234.5, '.2f') == '1234.50'


def test_stored_entry_tracks_refreshed_quote_without_losing_original_reference():
    scenario = entry()
    scenario['entry_price'] = 100
    once = apply_buy_scenario_contract(scenario, market='KR', entry_price=101)
    twice = apply_buy_scenario_contract(once, market='KR', entry_price=102)
    assert twice['entry_price'] == 102
    assert twice['_analysis_entry_price'] == 100
    assert twice['expected_return_pct'] == 20  # reported math is not silently repaired
    assert scenario['entry_price'] == 100


def test_impossible_model_reference_is_not_silently_hidden_by_fresh_quote():
    scenario = entry()
    scenario['entry_price'] = 99999
    with pytest.raises(ValueError, match='scenario invalid reported entry'):
        apply_buy_scenario_contract(scenario, market='US', entry_price=100)


@pytest.mark.parametrize('value', [None, [], {}, {'decision': 'maybe later'}, {'decision': True}])
def test_malformed_decision_is_not_silently_treated_as_no_entry(value):
    with pytest.raises(ValueError, match='scenario'):
        apply_buy_scenario_contract(value, market='KR', entry_price=100)


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_prompt_contract_pins_existing_rules_without_relaxing_thresholds(language):
    buy = buy_scenario_prompt_contract(language)
    sell = sell_scenario_authority_contract(language)
    assert 'entry_price' in buy and 'null' in buy
    assert 'sell_triggers' in buy and 'sell_triggers' in sell
    assert '5' in buy and '7%' in buy
