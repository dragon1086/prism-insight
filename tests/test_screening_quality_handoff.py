"""Only versioned, same-session quality observations enter decision context."""
import copy

import pandas as pd

from prism_core.screening_quality import build_screening_quality_context, load_quality_candidates
from observability.trading_context import build_trading_context


def fixture():
    history = pd.DataFrame({'Close': range(100, 125)}, index=pd.bdate_range(end='2026-09-11', periods=25))
    context = build_screening_quality_context(history, '20260914')
    return {'trade_date': '20260914', 'screening_quality_candidates': {'AAA': context}}


def test_same_session_whitelist_does_not_mutate_scenario_or_decision():
    metadata = fixture()
    metadata['screening_quality_candidates']['AAA']['arbitrary_prompt'] = 'not allowed'
    cleaned = load_quality_candidates(metadata)
    assert 'arbitrary_prompt' not in cleaned['AAA']
    scenario = {'buy_score': 7, 'target_price': 110, 'stop_loss': 95}
    before = copy.deepcopy(scenario)
    decision = {'selected_for_entry': True, 'price': 100}
    baseline = build_trading_context(market='US', scenario=scenario, decision_context=decision)
    observed = build_trading_context(market='US', scenario=scenario,
                                    decision_context={**decision, 'screening_quality_context': cleaned['AAA']})
    assert observed['decision_context'].pop('screening_quality_context') == cleaned['AAA']
    baseline.pop('captured_at')
    observed.pop('captured_at')
    assert observed == baseline
    assert scenario == before


def test_stale_invalid_and_non_observational_payloads_are_not_attached():
    for key, value in [('requested_trade_date', '2026-09-15'), ('schema_version', 'future'),
                       ('scoring_applied', True), ('status', 'BUY')]:
        metadata = fixture()
        metadata['screening_quality_candidates']['AAA'][key] = value
        assert load_quality_candidates(metadata) == {}
    assert load_quality_candidates(None) == {}


def test_malformed_optional_values_do_not_escape_into_trade_loading():
    metadata = fixture()
    metadata['screening_quality_candidates']['AAA']['status'] = []
    assert load_quality_candidates(metadata) == {}
    metadata = fixture()
    metadata['screening_quality_candidates']['AAA']['completed_row_count'] = 10 ** 1000
    assert 'completed_row_count' not in load_quality_candidates(metadata)['AAA']
