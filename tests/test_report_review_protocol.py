"""Review reporting and repair authority are separate, fail-closed contracts."""
import pytest

from cores.report_review_protocol import (
    SECTION_POLICIES, ReportFactEditorError, ReportFactConflictError,
    ReportSourceConflictError, review_output_schema, validate_review_envelope,
)


def reports():
    return {key: 'Provided source or model draft' for key in SECTION_POLICIES}


def conflicts(section, evidence='shared_reference', kind='contradiction', source_roles=None):
    return {'status': 'CONFLICTS', 'summary': None, 'edits': [], 'unresolved': [
        {'section': section, 'issue': 'period mismatch', 'evidence_section': evidence, 'kind': kind,
         'source_roles': [] if source_roles is None else source_roles}]}


@pytest.mark.parametrize('target', ['price_volume_analysis', 'investor_trading_analysis',
                                  'company_status', 'company_overview', 'news_analysis', 'market_index_analysis'])
def test_six_base_drafts_report_factual_conflicts_without_summary(target):
    with pytest.raises(ReportFactConflictError) as caught:
        validate_review_envelope(reports(), conflicts(target))
    assert caught.value.targets == (target,)
    assert not caught.value.repair_dart and not caught.value.strategy_only


@pytest.mark.parametrize('target', ['dart_deep_analysis', 'investment_strategy'])
def test_known_model_sections_have_distinct_rebuild_authority(target):
    with pytest.raises(ReportFactConflictError) as caught:
        validate_review_envelope(reports(), conflicts(target, 'company_status'))
    assert caught.value.targets == ()
    assert caught.value.repair_dart is (target == 'dart_deep_analysis')
    assert caught.value.strategy_only is (target == 'investment_strategy')


@pytest.mark.parametrize('target', ['shared_reference', 'peer_comparison', 'macro_context', 'dart_depth_limit'])
def test_immutable_source_conflict_is_blocked_not_schema_error(target):
    with pytest.raises(ReportSourceConflictError) as caught:
        validate_review_envelope(reports(), conflicts(target))
    assert caught.value.code == 'SOURCE_CONFLICT'


@pytest.mark.parametrize('mutation,code', [
    ('unknown_section', 'UNKNOWN_SECTION'), ('absent_section', 'MISSING_SECTION'),
    ('missing_pointer', 'MISSING_EVIDENCE'), ('unknown_pointer', 'UNKNOWN_EVIDENCE'),
    ('empty_pointer', 'MISSING_EVIDENCE'), ('malformed', 'INVALID_SCHEMA'),
    ('extra', 'INVALID_SCHEMA'), ('issue_size', 'CAPACITY_EXCEEDED'),
    ('summary', 'INVALID_ENVELOPE'), ('edits', 'INVALID_ENVELOPE'),
])
def test_failures_are_classified_without_untrusted_issue_disclosure(mutation, code):
    data, payload = reports(), conflicts('company_status')
    item = payload['unresolved'][0]
    if mutation == 'unknown_section': item['section'] = 'not_known'
    elif mutation == 'absent_section': del data['company_status']
    elif mutation == 'missing_pointer': item['evidence_section'] = None
    elif mutation == 'unknown_pointer': item['evidence_section'] = 'not_known'
    elif mutation == 'empty_pointer': data['shared_reference'] = ''
    elif mutation == 'malformed': item['issue'] = 1
    elif mutation == 'extra': item['extra'] = True
    elif mutation == 'issue_size': item['issue'] = 'PRIVATE' * 400
    elif mutation == 'summary': payload['summary'] = 'unused invalid 99999 summary'
    else: payload['edits'] = [{'bad': 'unused'}]
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(data, payload)
    assert caught.value.code == code
    assert 'PRIVATE' not in str(caught.value.details)


def test_ready_assessment_is_not_required_to_generate_summary():
    payload = {'status': 'READY', 'summary': None, 'edits': [], 'unresolved': []}
    assert validate_review_envelope(reports(), payload, stage='assessment') == payload
    with pytest.raises(ReportFactEditorError):
        validate_review_envelope(reports(), dict(payload, summary='not requested'), stage='assessment')


def test_dynamic_schema_uses_actual_section_keys_and_nullable_evidence():
    schema = review_output_schema({'company_status': 'draft', 'shared_reference': 'source'})
    payload = conflicts('company_status', None)
    assert schema.model_validate(payload).model_dump() == payload
    with pytest.raises(ValueError):
        schema.model_validate(conflicts('news_analysis'))


def test_edit_reason_is_a_code_not_freeform_explanation():
    schema = review_output_schema(reports())
    edit = {'section': 'news_analysis', 'original': '9월 24일 하락 원인입니다.',
            'replacement': '9월 24일은 휴장입니다.', 'reason': 'session_timing'}
    payload = {'status': 'READY', 'summary': 'summary', 'edits': [edit], 'unresolved': []}
    assert schema.model_validate(payload).edits[0].reason == 'session_timing'
    edit['reason'] = 'session_timing: 제공된 XKRX 달력에서 휴장일입니다.'
    with pytest.raises(ValueError):
        schema.model_validate(payload)
    with pytest.raises(ReportFactEditorError):
        validate_review_envelope(reports(), payload)


def test_mixed_source_and_model_conflicts_never_partially_repair():
    payload = conflicts('company_status')
    payload['unresolved'] += conflicts('shared_reference')['unresolved']
    with pytest.raises(ReportSourceConflictError):
        validate_review_envelope(reports(), payload)


def test_conflicts_are_immutable_and_targets_use_stable_base_order():
    payload = conflicts('news_analysis')
    payload['unresolved'] += conflicts('price_volume_analysis')['unresolved']
    with pytest.raises(ReportFactConflictError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.targets == ('price_volume_analysis', 'news_analysis')
    payload['unresolved'][0]['issue'] = 'changed'
    assert caught.value.conflicts[0][1] == 'period mismatch'
    with pytest.raises(AttributeError):
        caught.value.conflicts = ()


@pytest.mark.parametrize('count', [8, 9])
def test_conflict_count_limit_is_enforced_without_truncation(count):
    payload = conflicts('company_status')
    payload['unresolved'] *= count
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.code == ('FACT_CONFLICTS' if count == 8 else 'CAPACITY_EXCEEDED')


@pytest.mark.parametrize('status,summary,unresolved', [
    ('CONFLICTS', None, []), ('READY', 'summary', conflicts('company_status')['unresolved']),
])
def test_status_and_payload_branch_must_agree(status, summary, unresolved):
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), {'status': status, 'summary': summary,
                                            'edits': [], 'unresolved': unresolved})
    assert caught.value.code == 'INVALID_ENVELOPE'


def test_missing_field_is_schema_error_not_repair_authority():
    payload = conflicts('company_status')
    del payload['unresolved'][0]['evidence_section']
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.code == 'INVALID_SCHEMA'


@pytest.mark.parametrize('field', ['summary', 'edits'])
def test_ready_size_limits_apply_before_content_guards(field):
    payload = {'status': 'READY', 'summary': 'summary', 'edits': [], 'unresolved': []}
    payload[field] = 'x' * 6001 if field == 'summary' else [{}] * 9
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.code == 'CAPACITY_EXCEEDED'


@pytest.mark.parametrize('target', ['company_overview', 'price_volume_analysis', 'dart_deep_analysis', 'investment_strategy'])
def test_unsupported_claim_without_source_permits_only_withdrawal_review(target):
    payload = conflicts(target, None, 'unsupported_claim')
    with pytest.raises(ReportFactConflictError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.kinds == ('unsupported_claim',)
    assert caught.value.evidence_sections == (None,)


@pytest.mark.parametrize('target', ['shared_reference', 'peer_comparison', 'macro_context', 'dart_depth_limit'])
def test_unsupported_claim_never_grants_immutable_source_rewrite(target):
    with pytest.raises(ReportSourceConflictError) as caught:
        validate_review_envelope(reports(), conflicts(target, None, 'unsupported_claim'))
    assert caught.value.code == 'SOURCE_CONFLICT'


@pytest.mark.parametrize('kind', [None, 'guess', 1])
def test_conflict_kind_must_be_known(kind):
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), conflicts('company_status', None, kind))
    assert caught.value.code == 'INVALID_SCHEMA'


def test_conflict_kind_is_required_by_sdk_schema():
    payload = conflicts('company_status')
    del payload['unresolved'][0]['kind']
    with pytest.raises(ValueError):
        review_output_schema(reports()).model_validate(payload)


def test_legacy_direct_error_defaults_to_contradiction_and_keeps_missing_pointer_guard():
    error = ReportFactConflictError((('company_status', 'period mismatch'),), evidence_sections=('shared_reference',))
    assert error.kinds == ('contradiction',)
    with pytest.raises(ReportFactEditorError):
        ReportFactConflictError((('company_status', 'period mismatch'),), evidence_sections=(None,))


@pytest.mark.parametrize('roles', [[], ['unknown'], ['finance', 'finance'],
                                  ['finance', 'business', 'risks', 'finance'], None])
def test_dart_contradiction_requires_valid_unique_explicit_source_roles(roles):
    payload = conflicts('company_status', 'dart_deep_analysis')
    payload['unresolved'][0]['source_roles'] = roles
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), payload)
    assert not isinstance(caught.value, ReportFactConflictError)


def test_dart_source_role_locator_is_preserved_without_issue_inference():
    payload = conflicts('company_status', 'dart_deep_analysis', source_roles=['risks', 'finance'])
    payload['unresolved'][0]['issue'] = 'business keyword does not change explicit roles'
    with pytest.raises(ReportFactConflictError) as caught:
        validate_review_envelope(reports(), payload)
    assert caught.value.source_roles == (('risks', 'finance'),)


def test_non_dart_source_never_carries_dart_roles():
    with pytest.raises(ReportFactEditorError) as caught:
        validate_review_envelope(reports(), conflicts('company_status', source_roles=['finance']))
    assert not isinstance(caught.value, ReportFactConflictError)


def test_sdk_schema_requires_source_roles_and_rejects_unknown_role():
    schema = review_output_schema(reports())
    payload = conflicts('company_status')
    del payload['unresolved'][0]['source_roles']
    with pytest.raises(ValueError): schema.model_validate(payload)
    payload['unresolved'][0]['source_roles'] = ['unknown']
    with pytest.raises(ValueError): schema.model_validate(payload)


def test_legacy_direct_constructor_defaults_to_aligned_empty_roles():
    error = ReportFactConflictError((('company_status', 'conflict'), ('news_analysis', 'conflict')))
    assert error.source_roles == ((), ())
