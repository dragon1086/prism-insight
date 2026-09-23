import pandas as pd
import pytest

from prism_core.report_presentation import (
    humanize_report_status,
    report_narrative_contract,
)
from prism_core.report_technical_facts import (
    build_report_technical_facts,
    render_public_technical_facts,
    render_report_technical_facts,
)


def sample_frame():
    frame = pd.DataFrame({'Close': range(100, 320)}, index=pd.date_range('2026-01-01', periods=220))
    frame.attrs['price_basis'] = 'provider_unadjusted_close'
    return frame


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_public_facts_preserve_numbers_not_packet_codes(language):
    frame = sample_frame()
    before = frame.copy(deep=True)
    facts = build_report_technical_facts(frame)
    public = render_public_technical_facts(frame, language=language)
    assert facts['last_valid_close_date'] in public
    for value in facts['indicators'].values():
        if value is not None:
            assert f'{value:.4f}' in public
    for private in ('BAR_FINALITY_UNKNOWN', 'status=', 'reason=', 'MACD_SIGNAL', 'BB20_MIDDLE', 'N/A'):
        assert private not in public
    assert 'BAR_FINALITY_UNKNOWN' in render_report_technical_facts(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_missing_latest_close_stays_historical():
    frame = sample_frame().astype(float)
    frame.iloc[-1, 0] = float('nan')
    public = render_public_technical_facts(frame)
    assert '2026-08-08' in public
    assert '2026-08-07' in public
    assert '318.0000' in public
    assert '최신 거래일의 종가를 대신하지 않습니다' in public
    assert '319.0000' not in public


def test_empty_input_is_one_short_disclosure():
    public = render_public_technical_facts(None)
    assert '자료가 없어' in public
    assert 'N/A' not in public
    assert len(public) < 150


def test_humanizer_changes_only_known_token():
    source = 'BAR_FINALITY_UNKNOWN 상태. 234.76 USD 2026-09-22 source https://example.com CE-123 MISSING'
    result = humanize_report_status(source)
    assert result == source.replace('BAR_FINALITY_UNKNOWN', '마감 확정 여부를 확인하지 못한')
    assert humanize_report_status('다른 정보 42') == '다른 정보 42'


def test_contract_protects_dates_and_missing_values():
    assert '오늘의 종가' in report_narrative_contract()
    assert '수집 실패를 숨기지' in report_narrative_contract()
    assert "today's close" in report_narrative_contract('en')


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_evidence_public_labels_preserve_facts_urls_and_ids(language):
    source = ('#### Competitive Evidence\nEvidence ID: CE-123abc\n'
              '- **field:** 사업 비교 / **type:** price_leadership / **value:** 10.34 / '
              '**period:** 2026-09-23 / **source:** https://example.com/UNKNOWN?x=12&v=INCOMPARABLE / '
              '**status:** INCOMPARABLE / 비교 자료가 부족합니다.\n')
    result = humanize_report_status(source, language)
    assert '**field:**' not in result
    for fact in ('CE-123abc', '10.34', '2026-09-23', 'https://example.com/UNKNOWN?x=12&v=INCOMPARABLE',
                 '비교 자료가 부족합니다.'):
        assert fact in result
    assert humanize_report_status(result, language) == result
    assert ('동일 기준 비교' if language == 'ko' else 'like-for-like') in result


def test_evidence_translation_is_scoped_and_preserves_fences_and_malformed_data():
    before = '**status:** UNKNOWN / **type:** price_leadership\n'
    fenced = '```json\n{"value": "UNKNOWN", "source": "https://a.com/4"}\n```\n'
    inline = '`**value:** UNKNOWN`\n'
    broken = '**field broken UNKNOWN 12.3\n'
    after = '## Other\n**status:** NOT_FOUND\n'
    source = before + '#### Competitive Evidence\n' + fenced + inline + broken + after
    result = humanize_report_status(source)
    assert result.startswith(before)
    assert fenced in result and inline in result and broken in result
    assert result.endswith(after)


def test_evidence_unknowns_are_explicit_not_implied_absence_or_verification():
    result = humanize_report_status('#### Competitive Evidence Handoff\n'
        '원문의 상태·출처·기간을 따르세요.\n'
        '- **value:** UNKNOWN / **status:** NOT_FOUND\n'
        '- **status:** SOURCE_CHECKED\n')
    assert '확인되지 않음' in result and '이번 수집에서 근거를 확보하지 못함' in result
    assert '독립 검증을 뜻하지 않음' in result
    assert '따르세요' not in result


def test_known_collapsed_headers_repaired_without_changing_facts():
    source = ('주가·수급 지표 기준값기준일: 2026-09-23\n'
              '시장 지표 기준값기준일: 2026-09-23\n'
              '회사: SKT; 주요 공시; 공시일: 2026-08-13; 대상 기간: 2026-01-01~2026-06-30; '
              '연결 기준출처: https://dart.fss.or.kr/report?rcpNo=123\n')
    result = humanize_report_status(source)
    assert '### 주가·수급 지표 기준값\n\n기준일: 2026-09-23' in result
    assert '### 시장 지표 기준값\n\n기준일: 2026-09-23' in result
    assert '연결 기준\n\n출처: https://dart.fss.or.kr/report?rcpNo=123' in result
    assert humanize_report_status(result) == result
    ordinary = '연결 기준출처: https://example.com\n'
    assert humanize_report_status(ordinary) == ordinary


@pytest.mark.parametrize('state', ['RECORD_ABSENT', 'RECORD_EMPTY', 'RECORD_AMBIGUOUS',
                                 'RECORD_OVERSIZE', 'RECORD_MALFORMED'])
def test_handoff_failure_is_readable_without_implying_business_absence(state):
    source = ('#### Competitive Evidence Handoff\nHandoff status: ' + state + '\n'
              '뉴스 경쟁근거 기록을 전달하지 못했습니다. 이는 기업 경쟁력의 부재를 뜻하지 않습니다.\n')
    result = humanize_report_status(source)
    assert 'Handoff status:' not in result and state not in result
    assert '기업 경쟁력의 부재를 뜻하지 않습니다' in result
    assert humanize_report_status(result) == result


def test_nonclosing_fence_suffix_does_not_expose_code_to_translation():
    fenced = ('```text\n```not-a-closing-fence\n**status:** UNKNOWN\n'
              'price_leadership는\n원문의 상태·출처·기간을 따르세요.\n```\n')
    source = '#### Competitive Evidence\n' + fenced + '**status:** UNKNOWN\n'
    result = humanize_report_status(source)
    assert fenced in result
    assert result.endswith('**근거 확인 범위:** 확인되지 않음\n')


def test_handoff_note_translation_respects_inline_code_and_urls():
    note = '원문의 상태·출처·기간을 따르세요.'
    code = f'`{note}`'
    url = 'https://example.com/원문의 상태·출처·기간을 따르세요.'.replace(' ', '%20')
    source = f'#### Competitive Evidence Handoff\n{code} {url}\n{note}\n'
    result = humanize_report_status(source)
    assert code in result and url in result
    assert '확인 범위·출처·대상 기간을 유지한 기록입니다.' in result


def test_known_narrative_alias_leaves_other_identifiers_code_urls_unchanged():
    source = ('**price_leadership는 잠정적인 중기 강세 정황만 있습니다.** 12.3%\n'
              '`price_leadership는` https://example.com/price_leadership는\n'
              'custom_price_leadership는 UNKNOWN\n')
    result = humanize_report_status(source)
    assert '**주가 상대강도는 잠정적인 중기 강세 정황만 있습니다.** 12.3%' in result
    assert '`price_leadership는` https://example.com/price_leadership는' in result
    assert 'custom_price_leadership는 UNKNOWN' in result
