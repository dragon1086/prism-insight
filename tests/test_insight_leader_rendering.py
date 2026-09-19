import json

import pandas as pd

from prism_core.report_insight_manifest import build_insight_manifest, section_manifest
from prism_core.snapshot_price_leaders import build_snapshot_price_leaders


def fixture():
    current = pd.DataFrame({'Close': [110., 120., 130.]}, index=['005930', '000660', '035420'])
    current.attrs['observed_date'] = '20260918'
    previous = pd.DataFrame({'Close': [100., 100., 100.]}, index=current.index)
    sectors = {'005930': '전기전자', '000660': '전기전자', '035420': '서비스'}
    packet = build_snapshot_price_leaders(current, previous, sectors, prior_date='20260917',
                                           source='KIS', trigger_mode='morning')
    return {'snapshot_price_leaders': packet, 'sector_map': sectors}


def test_only_target_classification_group_reaches_price_section():
    context = fixture()
    result = build_insight_manifest('KR', '005930', '20260918', {}, context)
    text = section_manifest(result, 'price_volume_analysis')
    parsed = json.loads(text)['categories']['industry_price_leadership']
    assert parsed['insight_status'] == 'DESCRIPTIVE_OBSERVED_GROUP_ONLY'
    assert '000660' in text and '035420' not in text
    assert 'NOT whole-industry' in text
    assert parsed['observation']['intraday'] is True
    assert len(text.encode()) <= 1800


def test_us_and_future_packets_cannot_invent_rank():
    context = fixture()
    for market, day in [('US', '20260918'), ('KR', '20260916')]:
        result = build_insight_manifest(market, '005930', day, {}, context)
        assert result['categories']['industry_price_leadership']['insight_status'] == 'UNKNOWN'


def test_sector_hypotheses_and_selected_stocks_are_not_rank_inputs():
    result = build_insight_manifest('KR', '005930', '20260918', {},
        {'leading_sectors': ['반도체'], 'selected_tickers': ['005930'], 'sector_map': {'005930': '반도체'}})
    assert result['categories']['industry_price_leadership']['insight_status'] == 'UNKNOWN'
