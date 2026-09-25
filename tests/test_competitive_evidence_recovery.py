import copy

import pytest

from prism_core.competitive_evidence import (
    CompetitiveEvidenceIntegrityError, attach_competitive_evidence,
    detach_competitive_evidence, competitive_evidence_review_view,
)


def original():
    return {'news_analysis': 'News\n\n#### Competitive Evidence\n- metric: unsupported peer rank\n- source: exact source\n\n#### Other\nKeep this',
            'company_overview': 'Overview\n\n#### Other evidence\nPreserve unrelated facts'}


def attach(reports):
    return attach_competitive_evidence(reports, 'KR', '252990', '20260924')


def test_verified_attachment_roundtrip_and_idempotency():
    source = original()
    attached, receipt = attach(source)
    canonical, verified = detach_competitive_evidence(attached, 'KR', '252990', '20260924')
    assert canonical == source and verified['attached']
    assert verified['evidence_id'] == receipt['evidence_id']
    assert attach(attached) == (attached, receipt)


def test_review_removes_only_verified_derived_copy_not_canonical_or_unrelated_evidence():
    source = original()
    attached, _ = attach(source)
    view = competitive_evidence_review_view(attached, 'KR', '252990', '20260924')
    assert view['news_analysis'] == attached['news_analysis']
    assert view['company_overview'] == source['company_overview']
    assert 'unsupported peer rank' in view['news_analysis']


@pytest.mark.parametrize('where', ['news', 'copy', 'id', 'suffix', 'duplicate'])
def test_tampered_derived_ownership_is_rejected_without_mutation(where):
    attached, _ = attach(original())
    if where == 'news': attached['news_analysis'] = attached['news_analysis'].replace('peer rank', 'peer leadership')
    elif where == 'copy': attached['company_overview'] = attached['company_overview'].replace('peer rank', 'peer leadership')
    elif where == 'id': attached['news_analysis'] = attached['news_analysis'].replace('CE-', 'CE-f')
    elif where == 'suffix': attached['company_overview'] += '\nUnverified suffix'
    else: attached['company_overview'] += attached['company_overview']
    before = copy.deepcopy(attached)
    with pytest.raises(CompetitiveEvidenceIntegrityError):
        detach_competitive_evidence(attached, 'KR', '252990', '20260924')
    assert attached == before


def test_pre_attach_canonical_record_is_not_removed():
    source = original()
    canonical, receipt = detach_competitive_evidence(source, 'KR', '252990', '20260924')
    assert canonical == source and not receipt['attached']


def test_fenced_handoff_like_text_is_not_deduplicated():
    source = original()
    source['company_overview'] += '\n```\n#### Competitive Evidence Handoff\nEvidence ID: CE-fake\n```'
    assert competitive_evidence_review_view(source, 'KR', '252990', '20260924') == source


def test_unrelated_identifier_does_not_claim_code_owned_ce_identity():
    source = original()
    source['news_analysis'] += '\nEvidence ID: OTHER-source-record'
    assert detach_competitive_evidence(source, 'KR', '252990', '20260924')[0] == source
