import asyncio
import copy
from datetime import datetime, timezone

import pytest
from test_dart_note_fragment_context import CORP
from test_dart_note_fragment_context import fixture as fragment_fixture
from test_dart_report_evidence import fixture

from prism_core import dart_identity, dart_public_filings, dart_report_evidence
from prism_core.dart_chapter_sources import build_dart_chapter_inputs


@pytest.mark.parametrize('fragment', [False, True])
def test_same_collection_preserves_unfiltered_raw_and_fragment_parent(monkeypatch, fragment):
    if fragment:
        row, section, main = fragment_fixture(child_title='15-3. 우발약정 (연결)')
        row.update(period_start='2025-01-01', period_end='2025-12-31', submitted_date='2026-03-01',
                   note_main_html=main, note_fragment_selection={})
        section.update(parent_key='123:3', child_key='123:4')
        row['note_fragments'] = [section]
    else:
        row, section = fixture()
        row['sections'] = {'financial_notes': section}
    original = copy.deepcopy(row)
    calls = []
    async def identity(*args, **kwargs):
        calls.append('identity')
        return {'status': 'OK', 'ticker_verified_from_company_profile': True, 'corp_code': CORP}
    async def filings(**kwargs):
        calls.append('filings')
        return {'metrics': {'calls': 1, 'response_bytes': 1}, 'selection': {
            'primary_id': row['receipt_id'], 'latest_confirmed': True, 'reasons': []},
            'coverage': {}, 'observed_at': '2026-09-23', 'limitations': [], 'errors': [], 'filings': [row]}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', filings)
    # Material-filtered legacy blocks may be empty; raw admission must survive.
    monkeypatch.setattr(dart_report_evidence, '_section_record_blocks', lambda records, parsed, section, gaps, **kw: ([], gaps))
    progress, sink = {'sources': [], 'gaps': []}, []
    asyncio.run(dart_report_evidence.collect_latest('017670', '회사', datetime(2026, 9, 23, tzinfo=timezone.utc),
                                                    'consolidated', progress, source_sink=sink))
    assert calls == ['identity', 'filings']
    assert row == original and len(sink) == 1
    assert sink[0]['html'] == section['html']
    packet = build_dart_chapter_inputs(sink)
    assert packet['ready']
    assert not progress['sources']
    if fragment:
        assert sink[0]['filing']['section'] == 'financial_notes_fragment'
        assert sink[0]['scope_context']['child_title'] == '15-3. 우발약정 (연결)'
        assert '약정' in packet['contexts']['risks']
