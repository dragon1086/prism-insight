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
