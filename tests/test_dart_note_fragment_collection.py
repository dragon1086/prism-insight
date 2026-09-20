"""Synthetic streamed collector fallback, not replay of an oversized source."""
import asyncio
import hashlib
import json
from datetime import date, datetime, timezone

import httpx
import pytest
from test_dart_public_filings import BODY, CORP, RECORDS, catalog, cover
from test_dart_viewer_tree import CORP as GRAPH_CORP
from test_dart_viewer_tree import RECEIPT, node, page

from prism_core import dart_identity, dart_public_filings
from prism_core.dart_report_evidence import collect_latest
from prism_core.report_insight_prefetch import packet

LIMIT = 2 * 1024 * 1024
NOTES = '<h2>3. 연결재무제표 주석</h2><p>차입금 약정 조건입니다.</p>'
TITLES = {4: '1. 일반사항 (연결)', 5: '2. 차입금 및 약정 (연결)', 6: '3. 특수관계자 거래 (연결)'}
DAY = datetime(2026, 9, 18, tzinfo=timezone.utc)


class Stream(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body

    async def __aiter__(self):
        for offset in range(0, len(self.body), 256 * 1024):
            yield self.body[offset:offset + 256 * 1024]


def padded(body, size):
    raw = body.encode()
    return raw + b' ' * (size - len(raw))


def main(record, doc):
    rid, kind, *_ = record
    build = node('node1', 1, text=kind + '보고서', dcmNo=doc) + 'treeData.push(node1);'
    build += node('node1', 2, text='2. 연결재무제표', dcmNo=doc) + 'treeData.push(node1);'
    build += node('node1', 3, text='3. 연결재무제표 주석', dcmNo=doc) + "node1['children']=[];"
    for ele, title in TITLES.items():
        build += node('node2', ele, text=title, dcmNo=doc) + "node1['children'].push(node2);"
    build += 'treeData.push(node1);'
    return page(build).replace(GRAPH_CORP, CORP).replace(RECEIPT, rid).replace('"123","1","100"', f'"{doc}","1","100"')


class Harness:
    def __init__(self, records=None, parents=None, child_hook=None, financial_size=None, main_mutate=None):
        self.records = records or [RECORDS[0]]
        self.parents = parents or {}
        self.child_hook = child_hook
        self.financial_size = financial_size
        self.main_mutate = main_mutate
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        if request.method == 'POST':
            body = catalog(self.records).encode()
        else:
            rid = request.url.params['rcpNo']
            index = next(i for i, r in enumerate(self.records) if r[0] == rid)
            record = self.records[index]
            if request.url.path.endswith('main.do'):
                text = main(record, str(1000 + index))
                body = (self.main_mutate(text) if self.main_mutate else text).encode()
            else:
                ele = int(request.url.params['eleId'])
                if ele == 1:
                    body = cover(record).encode()
                elif ele == 2:
                    body = padded(BODY, self.financial_size) if self.financial_size else BODY.encode()
                elif ele == 3:
                    body = self.parents.get(rid, padded(NOTES, LIMIT + 1))
                else:
                    body = self.child_hook(request) if self.child_hook else None
                    if body is None:
                        body = (f'<p>{TITLES[ele]}</p><p>차입금 약정 조건은 위반하지 않았습니다.</p>').encode()
                if isinstance(body, Exception):
                    raise body
                if isinstance(body, httpx.Response):
                    return body
        return httpx.Response(200, stream=Stream(body))

    def factory(self, **opts):
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler), **opts)

    def collect(self, **opts):
        return asyncio.run(dart_public_filings.collect_dart_periodic_filings(
            corp_code=CORP, decision_at=DAY, start_date=date(2025, 1, 1), scope='consolidated',
            client_factory=self.factory, include_section_bodies=opts.pop('include', True), **opts))

    def supplementary(self):
        return [(r.url.params['rcpNo'], int(r.url.params['eleId'])) for r in self.requests
                if r.url.path.endswith('viewer.do') and int(r.url.params['eleId']) >= 3]


@pytest.fixture(autouse=True)
def no_pacing(monkeypatch):
    # Exercise the legacy per-body fallback independently of the total budget.
    monkeypatch.setattr(dart_public_filings, 'MAX_HTML_BYTES', LIMIT)
    async def no_wait(seconds):
        pass
    monkeypatch.setattr(dart_public_filings.asyncio, 'sleep', no_wait)


def test_individual_overflow_collects_two_label_prioritized_children_without_parent_success():
    harness = Harness()
    result = harness.collect()
    row = result['filings'][0]
    assert harness.supplementary() == [(RECORDS[0][0], 3), (RECORDS[0][0], 5), (RECORDS[0][0], 6)]
    assert row['section_delivery']['status'] == 'PARTIAL'
    assert row['section_delivery']['missing_sections'] == ['financial_notes']
    assert 'RESPONSE_BYTES_EXCEEDED' in row['section_delivery']['errors']
    assert 'financial_notes' not in row['sections']
    assert len(row['note_fragments']) == 2
    assert hashlib.sha256(row['note_main_html'].encode()).hexdigest() == row['main_sha256']
    state = row['note_fragment_selection']
    assert state['planned_keys'] == state['requested_keys'] == state['acquired_keys'] == ['1000:5', '1000:6']
    assert state['child_total'] == state['eligible_total'] == 3
    assert state['unselected_count'] == 1
    assert state['full_notes_acquired'] is False


def test_selected_whole_parents_precede_round_robin_children_and_unused_notes():
    harness = Harness(records=RECORDS[:3], parents={RECORDS[1][0]: NOTES.encode()})
    result = harness.collect()
    assert harness.supplementary() == [(RECORDS[0][0], 3), (RECORDS[2][0], 3),
                                       (RECORDS[0][0], 5), (RECORDS[2][0], 5), (RECORDS[1][0], 3)]
    assert result['metrics']['calls'] == 15
    assert sum(len(r.get('note_fragments', [])) for r in result['filings']) == 2


@pytest.mark.parametrize('include', [False, True])
def test_normal_parent_has_no_new_state_or_raw_main(include):
    harness = Harness(parents={RECORDS[0][0]: padded(NOTES, LIMIT)})
    result = harness.collect(include=include)
    row = result['filings'][0]
    assert not any(k.startswith('note_') for k in row)
    assert len(harness.requests) == (5 if include else 4)
    if include:
        assert len(row['sections']['financial_notes']['html'].encode()) == LIMIT


@pytest.mark.parametrize('failure', [httpx.Response(403), httpx.Response(200, headers={'content-encoding': 'gzip'}),
                                  b'\xff', httpx.ReadTimeout('secret'), httpx.RemoteProtocolError('secret')])
def test_selected_parent_nonindividual_failure_stops_all_supplementary(failure):
    harness = Harness(records=RECORDS[:3], parents={RECORDS[2][0]: failure})
    result = harness.collect()
    assert all(ele == 3 for _, ele in harness.supplementary())
    assert RECORDS[1][0] not in [rid for rid, _ in harness.supplementary()]
    assert not any(r.get('note_fragments') for r in result['filings'])
    assert result['filings'][0]['body_status'] == 'available'
    assert result['filings'][0]['note_fragment_selection']['failures']


def test_aggregate_exhaustion_takes_precedence_over_individual_overflow():
    harness = Harness(records=RECORDS[:3], financial_size=LIMIT)
    result = harness.collect()
    assert harness.supplementary() == [(RECORDS[0][0], 3)]
    assert not any(r.get('note_fragments') for r in result['filings'])
    assert result['filings'][0]['note_fragment_selection']['child_total'] is None


def test_remaining_call_budget_is_not_increased_for_children():
    harness = Harness()
    result = harness.collect(max_calls=6)
    row = result['filings'][0]
    assert len(harness.requests) == 6
    assert len(row['note_fragments']) == 1
    assert row['note_fragment_selection']['budget_omitted_keys'] == ['1000:6']


def test_child_access_failure_stops_second_child_and_nonselected_notes():
    harness = Harness(records=RECORDS[:3], parents={RECORDS[2][0]: NOTES.encode()},
                      child_hook=lambda _: httpx.Response(403))
    result = harness.collect()
    assert harness.supplementary() == [(RECORDS[0][0], 3), (RECORDS[2][0], 3), (RECORDS[0][0], 5)]
    assert not any(r.get('note_fragments') for r in result['filings'])


def test_oversize_child_does_not_recurse_and_other_planned_child_can_finish():
    harness = Harness(child_hook=lambda req: b'x' * (LIMIT + 1) if req.url.params['eleId'] == '5' else None)
    result = harness.collect()
    assert [e for _, e in harness.supplementary()] == [3, 5, 6]
    assert [s['child_key'] for s in result['filings'][0]['note_fragments']] == ['1000:6']


def test_graph_failure_does_not_invent_zero_children():
    harness = Harness(main_mutate=lambda s: s.replace('makeToc();', 'if(false)makeToc();'))
    result = harness.collect()
    state = result['filings'][0]['note_fragment_selection']
    assert state['child_total'] is None and state['eligible_total'] is None
    assert not state['planned_keys']
    assert harness.supplementary() == [(RECORDS[0][0], 3)]


def test_real_adapter_and_packet_use_unique_child_sources_without_raw_context(monkeypatch):
    async def identity(*args, **kwargs):
        return {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE', 'corp_code': CORP,
                'ticker_verified_from_company_profile': True, 'metrics': {'calls': 0, 'response_bytes': 0}}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    harness = Harness()
    progress = {'gaps': [], 'sources': [], 'calls': 0}
    asyncio.run(collect_latest('000000', '샘플', DAY, 'consolidated', progress, client_factory=harness.factory))
    fragments = [s for s in progress['sources'] if s['filing']['section'] == 'financial_notes_fragment']
    assert len(fragments) == 2
    assert len({s['source_id'] for s in fragments}) == 2
    assert all(s['filing']['period_end'] == '2026-06-30' for s in fragments)
    assert all(s['filing']['role'] == 'primary' for s in fragments)
    state = progress['filing_selection']['selected_note_fragments'][RECORDS[0][0]]
    assert all(f['context_verified'] for f in state['fragments'].values())
    serialized = json.dumps(progress)
    assert 'note_main_html' not in serialized and '"html"' not in serialized and 'function makeToc' not in serialized
    output = packet('KR', '000000', '2026-09-18', progress)
    assert all(len(note.encode()) <= 6000 for note in output['section_notes'].values())
    assert any('DART_EXPLICIT_VIEWER_TREE' in n for n in output['section_notes'].values())


def test_first_selected_access_failure_skips_annual_whole_and_all_children():
    harness = Harness(records=RECORDS[:3], parents={RECORDS[0][0]: httpx.Response(403)})
    result = harness.collect()
    assert harness.supplementary() == [(RECORDS[0][0], 3)]
    assert result['filings'][0]['note_fragment_selection']['failures'] == [{'child_key': None, 'code': 'HTTP_STATUS_FAILURE'}]


def test_child_retry_uses_existing_shared_call_budget():
    attempts = 0

    def transient(request):
        nonlocal attempts
        if request.url.params['eleId'] == '5':
            attempts += 1
            if attempts == 1:
                raise httpx.RemoteProtocolError('private')

    harness = Harness(child_hook=transient)
    result = harness.collect(max_calls=7)
    assert result['metrics']['calls'] == 7
    assert result['metrics']['transport_retries'] == 1
    state = result['filings'][0]['note_fragment_selection']
    assert state['requested_keys'] == state['acquired_keys'] == ['1000:5']
    assert state['budget_omitted_keys'] == ['1000:6']


def test_child_aggregate_failure_keeps_first_child_and_stops_unused_notes():
    harness = Harness(records=RECORDS[:3], child_hook=lambda _: b'x' * LIMIT)
    result = harness.collect()
    assert harness.supplementary() == [(RECORDS[0][0], 3), (RECORDS[2][0], 3),
                                       (RECORDS[0][0], 5), (RECORDS[2][0], 5)]
    rows = {r['receipt_id']: r for r in result['filings']}
    assert len(rows[RECORDS[0][0]]['note_fragments']) == 1
    assert not rows[RECORDS[2][0]].get('note_fragments')
    assert any(f['code'] == 'RESPONSE_BYTES_EXCEEDED' for f in rows[RECORDS[2][0]]['note_fragment_selection']['failures'])


def test_final_selection_cancellation_discards_raw_fragments_but_not_request_history():
    def mismatch_old_main(source):
        return source.replace(CORP, '00999999') if RECORDS[2][0] in source else source

    harness = Harness(records=[RECORDS[0], RECORDS[2]], main_mutate=mismatch_old_main)
    result = harness.collect()
    assert result['selection']['primary_id'] is None
    row = result['filings'][0]
    assert 'note_fragments' not in row and 'note_main_html' not in row
    assert row['note_fragment_selection']['acquired_keys'] == ['1000:5', '1000:6']
    assert row['note_fragment_selection']['discarded_due_to_final_selection'] is True


def test_final_annual_change_discards_only_former_annual(monkeypatch):
    original = dart_public_filings.select_periodic_filings
    calls = 0

    def changed(*args, **kwargs):
        nonlocal calls
        calls += 1
        result = original(*args, **kwargs)
        if calls == 2:
            result['annual_supplement_id'] = RECORDS[6][0]
        return result

    monkeypatch.setattr(dart_public_filings, 'select_periodic_filings', changed)
    harness = Harness(records=[RECORDS[0], RECORDS[2], RECORDS[6]], parents={RECORDS[6][0]: NOTES.encode()})
    result = harness.collect()
    rows = {r['receipt_id']: r for r in result['filings']}
    assert rows[RECORDS[0][0]]['note_fragments']
    assert 'note_fragments' not in rows[RECORDS[2][0]] and 'note_main_html' not in rows[RECORDS[2][0]]
    assert rows[RECORDS[2][0]]['note_fragment_selection']['discarded_due_to_final_selection'] is True


def adapter_run(monkeypatch, harness):
    async def identity(*args, **kwargs):
        return {'status': 'RESOLVED_WITH_OFFICIAL_PROFILE', 'corp_code': CORP,
                'ticker_verified_from_company_profile': True, 'metrics': {'calls': 0, 'response_bytes': 0}}
    monkeypatch.setattr(dart_identity, 'resolve_dart_identity', identity)
    progress = {'sources': [], 'gaps': [], 'calls': 0}
    asyncio.run(collect_latest('000000', '샘플', DAY, 'consolidated', progress, client_factory=harness.factory))
    return progress


def test_context_approved_with_zero_candidates_is_not_mislabeled_unverified(monkeypatch):
    def body(request):
        ele = int(request.url.params['eleId'])
        return (f'<p>{TITLES[ele]}</p><p>' + '설명' * 2000 + '</p>').encode()

    progress = adapter_run(monkeypatch, Harness(child_hook=body))
    states = progress['filing_selection']['selected_note_fragments'][RECORDS[0][0]]['fragments']
    assert states and all(s['context_verified'] is True and s['candidate_count'] == 0 for s in states.values())
    assert all(s['sha256'] and s['utf8_bytes'] > 0 and s['url'].startswith('https://dart.fss.or.kr') for s in states.values())
    assert not progress['sources']


def test_acquired_child_with_wrong_heading_remains_unverified(monkeypatch):
    progress = adapter_run(monkeypatch, Harness(child_hook=lambda _: b'<p>unrelated content</p>'))
    states = progress['filing_selection']['selected_note_fragments'][RECORDS[0][0]]['fragments']
    assert states and all(s['context_verified'] is False and s['candidate_count'] == 0 for s in states.values())


def test_discarded_selection_cannot_enter_adapter_or_model(monkeypatch):
    harness = Harness(records=[RECORDS[0], RECORDS[2]], main_mutate=lambda source:
                      source.replace(CORP, '00999999') if RECORDS[2][0] in source else source)
    progress = adapter_run(monkeypatch, harness)
    assert progress['sources'] == []
    state = progress['filing_selection']['selected_note_fragments'][RECORDS[0][0]]
    assert state['discarded_due_to_final_selection'] is True and state['acquired_keys']
    assert state['fragments'] == {}
    assert 'note_main_html' not in json.dumps(progress) and '"html"' not in json.dumps(progress)


def test_deadline_in_annual_whole_prevents_child_requests_and_keeps_core():
    async def exercise():
        harness = Harness(records=[RECORDS[0], RECORDS[2]])

        async def handler(request):
            if request.url.params.get('rcpNo') == RECORDS[2][0] and request.url.params.get('eleId') == '3':
                harness.requests.append(request)
                await asyncio.Event().wait()
            return harness.handler(request)

        result = await dart_public_filings.collect_dart_periodic_filings(
            corp_code=CORP, decision_at=DAY, start_date=date(2025, 1, 1), scope='consolidated',
            client_factory=lambda **opts: httpx.AsyncClient(transport=httpx.MockTransport(handler), **opts),
            include_section_bodies=True, timeout_seconds=0.1)
        assert result['errors'] == ['TOTAL_TIMEOUT']
        assert all(ele == 3 for _, ele in harness.supplementary())
        assert result['filings'][0]['body_status'] == 'available'
        assert {'child_key': None, 'code': 'TOTAL_TIMEOUT'} in result['filings'][0]['note_fragment_selection']['failures']

    asyncio.run(exercise())


def test_deadline_during_child_pacing_does_not_claim_request_started(monkeypatch):
    harness = Harness()

    async def pace(seconds):
        if len(harness.requests) >= 5:
            await asyncio.Event().wait()

    monkeypatch.setattr(dart_public_filings.asyncio, 'sleep', pace)
    result = harness.collect(timeout_seconds=0.1)
    state = result['filings'][0]['note_fragment_selection']
    assert len(harness.requests) == result['metrics']['calls'] == 5
    assert state['requested_keys'] == []
    assert state['budget_omitted_keys'] == ['1000:5', '1000:6']


def test_no_remaining_calls_preserves_plan_without_raw_main():
    harness = Harness()
    result = harness.collect(max_calls=5)
    row = result['filings'][0]
    state = row['note_fragment_selection']
    assert state['planned_keys'] == state['budget_omitted_keys'] == ['1000:5', '1000:6']
    assert state['requested_keys'] == state['acquired_keys'] == []
    assert 'note_main_html' not in row and 'note_fragments' not in row


def test_opposite_scope_child_is_ineligible_and_label_order_remains_generic():
    harness = Harness(main_mutate=lambda body: body.replace('2. 차입금 및 약정 (연결)', '2. 차입금 및 약정 (별도)'))
    result = harness.collect()
    state = result['filings'][0]['note_fragment_selection']
    assert state['child_total'] == 3 and state['eligible_total'] == 2
    assert [ele for _, ele in harness.supplementary()] == [3, 6, 4]


def test_confirmed_graph_with_no_eligible_children_reports_known_zero():
    harness = Harness(main_mutate=lambda body: body.replace('(연결)', '(별도)'))
    result = harness.collect()
    state = result['filings'][0]['note_fragment_selection']
    assert state['child_total'] == 3 and state['eligible_total'] == 0
    assert state['planned_keys'] == [] and state['unselected_count'] == 3
    assert harness.supplementary() == [(RECORDS[0][0], 3)]


@pytest.mark.parametrize('failure', [httpx.Response(429), httpx.Response(200, headers={'content-encoding': 'gzip'}),
                                  b'\xff', httpx.ReadTimeout('secret'), httpx.RemoteProtocolError('secret')])
def test_child_access_or_decoding_errors_do_not_trigger_next_supplement(failure):
    harness = Harness(child_hook=lambda _: failure)
    result = harness.collect()
    assert all(ele != 6 for _, ele in harness.supplementary())
    state = result['filings'][0]['note_fragment_selection']
    assert state['requested_keys'] == ['1000:5']
    assert state['budget_omitted_keys'] == ['1000:6']
    assert state['acquired_keys'] == []
    assert 'secret' not in json.dumps(result)


def test_child_retry_budget_exhaustion_does_not_reclassify_attempted_child_as_omitted():
    harness = Harness(child_hook=lambda _: httpx.RemoteProtocolError('private'))
    result = harness.collect(max_calls=6)
    state = result['filings'][0]['note_fragment_selection']
    assert state['requested_keys'] == ['1000:5']
    assert state['budget_omitted_keys'] == ['1000:6']
    assert result['metrics']['calls'] == 6
    assert {'child_key': '1000:5', 'code': 'CALL_BUDGET_EXHAUSTED'} in state['failures']


def test_deadline_after_child_request_start_keeps_attempt_history():
    async def exercise():
        harness = Harness()

        async def handler(request):
            if request.url.params.get('eleId') == '5':
                harness.requests.append(request)
                await asyncio.Event().wait()
            return harness.handler(request)

        result = await dart_public_filings.collect_dart_periodic_filings(
            corp_code=CORP, decision_at=DAY, start_date=date(2025, 1, 1), scope='consolidated',
            client_factory=lambda **opts: httpx.AsyncClient(transport=httpx.MockTransport(handler), **opts),
            include_section_bodies=True, timeout_seconds=0.1)
        state = result['filings'][0]['note_fragment_selection']
        assert state['requested_keys'] == ['1000:5'] and state['acquired_keys'] == []
        assert state['budget_omitted_keys'] == ['1000:6']
        assert {'child_key': None, 'code': 'TOTAL_TIMEOUT'} in state['failures']

    asyncio.run(exercise())


def test_budget_stop_reason_survives_collector_adapter_and_summary(monkeypatch):
    from tools.evaluate_general_filing_reports import _summary

    harness = Harness()
    result = harness.collect(max_calls=5)
    state = result['filings'][0]['note_fragment_selection']
    assert state.get('stop_reason') == 'CALL_BUDGET_EXHAUSTED'
    original = dart_public_filings.collect_dart_periodic_filings

    async def limited(**kwargs):
        kwargs['max_calls'] = 5
        return await original(**kwargs)

    monkeypatch.setattr(dart_public_filings, 'collect_dart_periodic_filings', limited)
    progress = adapter_run(monkeypatch, Harness())
    receipt = progress['filing_selection']['selected_note_fragments'][RECORDS[0][0]]
    assert receipt['stop_reason'] == 'CALL_BUDGET_EXHAUSTED'
    output = packet('KR', '000000', '2026-09-18', progress)
    summary = _summary(progress, output)
    assert summary['fragment_delivery'][RECORDS[0][0]]['stop_reason'] == 'CALL_BUDGET_EXHAUSTED'
