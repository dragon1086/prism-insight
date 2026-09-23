import re

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


def test_plain_financial_table_missing_cells_are_public_labels_not_variable_names():
    source = ('| 배당성향 | 86.55% | 52.36% | UNKNOWN |\n'
              '| 다른 값 | MISSING | N/A | 0 |\n'
              '| 출처 | https://example.com/UNKNOWN | `UNKNOWN` | 2026 |\n'
              '```text\n| UNKNOWN | MISSING |\n```\n'
              '    | UNKNOWN | MISSING |\n')
    result = humanize_report_status(source)
    assert '| 86.55% | 52.36% | 확인되지 않음 |' in result
    assert '| 확인되지 않음 | 확인되지 않음 | 0 |' in result
    assert 'https://example.com/UNKNOWN | `UNKNOWN` | 2026' in result
    assert '```text\n| UNKNOWN | MISSING |\n```' in result
    assert '    | UNKNOWN | MISSING |' in result
    assert '| 86.55% | 52.36% | Not available |' in humanize_report_status(source, 'en')


def test_plain_ce_record_humanized_with_number_url_value_order_preserved():
    record = ('- field: 통신 경쟁력, type: business_competitive_position, entity: SK텔레콤(017670), '
              'peer_universe: KT·LG유플러스, metric: 매출, 영업이익, value: 1,362.50, unit: 억원, '
              'period: 2026년 9월 17~23일, geography: 한국, '
              'source: https://example.com/UNKNOWN?id=1674, publication_date: 2026-08-05, '
              'status: INCOMPARABLE, supporting excerpt: 숫자 92.5%와 기간을 유지합니다.\n')
    source = '#### Competitive Evidence\n' + record
    result = humanize_report_status(source)
    assert '**점검 항목:** 통신 경쟁력 / **비교 주제:** 사업 경쟁력' in result
    assert '**비교 지표:** 매출, 영업이익 / **수치·내용:** 1,362.50' in result
    assert '**근거 설명:** 숫자 92.5%와 기간을 유지합니다.' in result
    assert '**근거 확인 범위:** 동일 기준 비교에 필요한 근거가 부족함' in result
    assert 'https://example.com/UNKNOWN?id=1674' in result
    assert re.findall(r'\d+(?:[,.]\d+)*', source) == re.findall(r'\d+(?:[,.]\d+)*', result)
    assert humanize_report_status(result) == result


def test_plain_combined_metric_and_missing_status_labels():
    source = ('#### **Competitive Evidence**\n- field: 수요, type: sector_tailwind, '
              'peer_universe: KT·LG유플러스, metric/value/unit/period/geography: UNKNOWN, '
              'source: UNKNOWN, publication_date: UNKNOWN, status: NOT_FOUND, supporting excerpt: 이번 조회 범위\n')
    result = humanize_report_status(source)
    assert '**지표·수치·단위·기간·지역:** 확인되지 않음' in result
    assert '**근거 확인 범위:** 이번 수집에서 근거를 확보하지 못함' in result
    assert 'UNKNOWN' not in result and 'NOT_FOUND' not in result


@pytest.mark.parametrize('record', [
    '- field: 비교, type: sector_tailwind, peer_universe: KT, future_field: UNKNOWN, status: NOT_FOUND\n',
    '- field: 비교, type: sector_tailwind, peer_universe: KT, status: UNKNOWN, status: NOT_FOUND\n',
    '- field: 비교, type: sector_tailwind, peer_universe: KT, source: , status: UNKNOWN\n',
    '- field: 비교, type: sector_tailwind, peer_universe: KT, source: `UNKNOWN`, status: NOT_FOUND\n',
])
def test_plain_ce_unknown_broken_or_inline_code_records_unchanged(record):
    assert humanize_report_status('#### Competitive Evidence\n' + record).endswith(record)


def test_plain_ce_fences_and_outside_sections_unchanged():
    record = '- field: 비교, type: sector_tailwind, peer_universe: KT, source: UNKNOWN, status: NOT_FOUND\n'
    source = record + '#### Competitive Evidence\n```text\n' + record + '```\n### Other\n' + record
    result = humanize_report_status(source)
    assert result.count(record) == 3
