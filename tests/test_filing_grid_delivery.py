"""Full grids save repeated coordinates, never select a preferred financial fact."""
import copy
import json

import pytest

from prism_core.filing_html import parse_filing_html
from prism_core.filing_html_codec import expand_html_table_excerpt
from prism_core.filing_report_evidence import _record_blocks
from tools.evaluate_large_filing_html import audit_delivered_provenance


def fixture():
    rows = ''.join('<tr>' + ''.join(f'<td>{r * 30 + c}</td>' for c in range(30)) + '</tr>'
                   for r in range(12))
    html = ('<h3>3. 연결재무제표 주석</h3><p>28. 우발부채와 약정사항</p>'
            '<p>당반기 단위: 백만원</p><table>' + rows + '</table>'
            '<p>주) 보증 의무의 실행 금액은 조건에 따라 달라질 수 있습니다.</p>')
    parsed = parse_filing_html(html)
    record = next(r for r in parsed['records'] if r['kind'] == 'table')
    return parsed, record


def blocks(parsed, record):
    return _record_blocks([record], material_notes=True, representation='DART_VIEWER_HTML',
        digest=parsed['source_sha256'], md_hash=None, gaps=[], parsed=parsed)[0]


def test_large_full_grid_preserves_original_cells_provenance_and_projection_decision(monkeypatch):
    from prism_core import filing_html_codec as codec
    parsed, record = fixture()
    current = blocks(parsed, record)[0]
    assert current['provenance']['excerpt_encoding'] == 'html_cell_grid_v1'
    assert expand_html_table_excerpt(current['excerpt']) == record['text']
    monkeypatch.setattr(codec, 'compact_html_grid_excerpt', lambda value: value['text'])
    previous = blocks(parsed, record)[0]
    assert previous['provenance']['excerpt_encoding'] == 'html_cell_tuples_v1'
    visible_size = lambda value: len(json.dumps({k: v for k, v in value.items() if not k.startswith('_')}).encode())
    assert visible_size(current) < visible_size(previous)
    for value in (current, previous):
        value.pop('_packing_original', None)
        value['excerpt'] = expand_html_table_excerpt(value['excerpt'])
        value['provenance'].pop('excerpt_encoding')
    assert current == previous


@pytest.mark.parametrize('mutation', [None, 'marker', 'cell', 'shape', 'footnote', 'projected',
                                    '[]', 'null', '1', '"text"'])
def test_grid_delivery_is_independently_bound_to_original_even_if_packet_agrees(mutation):
    parsed, record = fixture()
    block = blocks(parsed, record)[0]
    assert block['provenance']['excerpt_encoding'] == 'html_cell_grid_v1'
    if mutation == 'marker':
        block['provenance']['excerpt_encoding'] = 'html_cell_tuples_v1'
    elif mutation in {'cell', 'shape'}:
        value = json.loads(block['excerpt'])
        if mutation == 'cell':
            value['rows'][0][0] = 'changed'
        else:
            value['shape'][0] = True
        block['excerpt'] = json.dumps(value)
    elif mutation == 'footnote':
        block['provenance']['footnotes'] = ''
    elif mutation == 'projected':
        block['provenance']['projected'] = True
    elif mutation in {'[]', 'null', '1', '"text"'}:
        block['excerpt'] = mutation
    note = {'sources': [{**copy.deepcopy(block), 'source_id': 'S'}]}
    errors = audit_delivered_provenance([block], note, parsed, source_id='S')
    assert bool(errors) is (mutation is not None)


def test_material_input_explains_full_grid_without_changing_tool_permissions():
    from cores.agents.report_agent import ReportAgent
    from prism_core.report_research_context import apply_section_research

    agent = ReportAgent('news', 'Original instructions.', ['perplexity'])
    evidence = {'evidence_id': 'S', 'news_usable': False,
                'receipt': {'filing_parser': 'material_v2', 'usable_sources': 1},
                'section_notes': {'news_analysis': '{"sources":[]}'}}
    output = apply_section_research(agent, 'news_analysis', {'report_research': evidence},
                                    '20260921', 'ko')
    assert 'html_cell_grid_v1 retains the whole table' in output.instruction
    assert 'null is no original cell' in output.instruction
    assert output.server_names == agent.server_names


@pytest.mark.parametrize('tamper', [False, True])
def test_conservative_original_representation_fallback_is_still_source_audited(tamper):
    from prism_core.report_insight_prefetch import _packing_original_block

    parsed, record = fixture()
    block = blocks(parsed, record)[0]
    if tamper:
        block['_packing_original']['excerpt'] = block['_packing_original']['excerpt'].replace('"359"', '"999"')
    fallback = _packing_original_block(block)
    note = {'sources': [{**fallback, 'source_id': 'S'}]}
    errors = audit_delivered_provenance([block], note, parsed, source_id='S')
    assert bool(errors) is tamper
