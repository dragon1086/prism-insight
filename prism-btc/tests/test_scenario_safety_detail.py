"""Only fixed failure categories may leave the runtime safety boundary."""
import pytest

from live.scenario_runtime import _fresh_safety_detail, _fresh_safety_field
from live.scenario_review_guard import _OPTIONAL_STABLE, _REQUIRED_STABLE


@pytest.mark.parametrize(('code', 'detail'), [
    ('review_guard_context_changed:account_version', 'context_changed'),
    ('review_guard_context_changed:private-account-value', 'context_changed'),
    ('review_guard_effective_plan_changed', 'effective_plan_changed'),
    ('review_guard_stale_account', 'stale_account'),
    ('stop_crossed_market', 'stop_crossed_market'),
])
def test_safety_detail_is_a_fixed_category(code, detail):
    assert _fresh_safety_detail(code) == detail
    assert 'private-account-value' not in detail


@pytest.mark.parametrize('code', [None, '', 'unknown-secret', 'review_guard_stale_account:secret',
                                    'review_guard_context_changed', 42])
def test_other_errors_do_not_acquire_a_detail(code):
    assert _fresh_safety_detail(code) is None


@pytest.mark.parametrize('field', _REQUIRED_STABLE + _OPTIONAL_STABLE)
def test_known_stable_field_can_be_recorded_without_its_value(field):
    assert _fresh_safety_field('review_guard_context_changed:' + field) == field


@pytest.mark.parametrize('code', [None, 42, '', 'review_guard_context_changed',
    'review_guard_context_changed:private-account-value',
    'review_guard_context_changed:account_version:secret',
    'review_guard_stale_account:account_version'])
def test_unknown_or_injected_field_is_not_recorded(code):
    assert _fresh_safety_field(code) is None
