"""Offline contracts for source-preserving HTML table geometry."""

import pytest
from lxml import html

from prism_core.filing_html_tables import parse_html_table


def parse(markup, **kwargs):
    return parse_html_table(html.fromstring(markup), **kwargs)


def test_mixed_spans_preserve_last_cell_and_origins():
    result = parse('<table><tr><th rowspan="2">A</th><th colspan="2">B</th></tr>'
                   '<tr><td>C</td><td>D</td><td>E</td></tr></table>')
    assert result['status'] == 'COMPLETE'
    assert [cell['text'] for cell in result['cells']] == ['A', 'B', 'C', 'D', 'E']
    assert result['grid'] == [[0, 1, 1, None], [0, 2, 3, 4]]
    assert result['cells'][-1]['col'] == 3
    assert result['cells'][0]['rowspan'] == 2


def test_values_headers_and_source_locations_are_not_inferred():
    element = html.fromstring('<table><tr><th colspan="2">당기 (단위 미상)</th></tr>'
                            '<tr><td>-</td><td></td></tr></table>')
    result = parse_html_table(element)
    assert result['status'] == 'COMPLETE'
    assert [cell['text'] for cell in result['cells']] == ['당기 (단위 미상)', '-', '']
    assert result['cells'][0]['tag'] == 'th'
    for cell in result['cells']:
        assert len(element.getroottree().xpath(cell['source_path'])) == 1


@pytest.mark.parametrize('span', ['0', '-1', '1.5', 'x', '', '9' * 1000])
def test_bad_spans_fail_closed(span):
    result = parse(f'<table><tr><td colspan="{span}">x</td></tr></table>')
    assert result['status'] in {'UNSUPPORTED', 'LIMIT_EXCEEDED'}
    assert result['cells'] == [] and result['grid'] == []


def test_rowspan_beyond_existing_rows_is_rejected():
    assert parse('<table><tr><td rowspan="2">x</td></tr></table>')['status'] == 'UNSUPPORTED'


def test_nested_table_cannot_contaminate_parent():
    result = parse('<table><tr><td>outer<table><tr><td>inner</td></tr></table></td></tr></table>')
    assert result['status'] == 'UNSUPPORTED'
    assert not result['cells']


@pytest.mark.parametrize('attributes', ['hidden', 'aria-hidden="true"', 'style="display: none"',
                                      'style="visibility : hidden !important"'])
def test_hidden_content_is_explicitly_unsupported(attributes):
    assert parse(f'<table><tr><td><span {attributes}>99</span>1</td></tr></table>')['status'] == 'UNSUPPORTED'


def test_script_style_and_comments_never_become_cell_text():
    result = parse('<table><tr><td>1<script>99</script><style>88</style><!--77-->2<br>3</td></tr></table>')
    assert result['cells'][0]['text'] == '12 3'


def test_inline_markup_does_not_split_financial_number():
    assert parse('<table><tr><td>1<span>,000</span></td></tr></table>')['cells'][0]['text'] == '1,000'


@pytest.mark.parametrize('tag', ['noscript', 'iframe', 'object', 'embed'])
def test_active_or_alternate_content_is_not_financial_text(tag):
    result = parse(f'<table><tr><td>10<{tag}>999</{tag}></td></tr></table>')
    assert result['status'] == 'UNSUPPORTED'
    assert result['errors'] == ['ACTIVE_OR_ALTERNATE_CONTENT']
    assert result['cells'] == [] and result['grid'] == []


@pytest.mark.parametrize('attribute', ['max_rows', 'max_columns', 'max_cells'])
def test_hard_resource_ceilings_cannot_be_disabled(attribute):
    with pytest.raises(ValueError):
        parse('<table/>', **{attribute: 10**20})


def test_large_text_is_bounded():
    result = parse('<table><tr><td>' + 'x' * (2 * 1024 * 1024 + 1) + '</td></tr></table>')
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert not result['cells']


@pytest.mark.parametrize('kwargs', [{'max_rows': 1}, {'max_columns': 1}, {'max_cells': 3}])
def test_limits_checked_before_expansion(kwargs):
    result = parse('<table><tr><td rowspan="2" colspan="2">A</td></tr><tr></tr></table>', **kwargs)
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert not result['grid']


def test_overlapping_span_rejected_instead_of_shifting_cells():
    result = parse('<table><tr><td>A</td><td rowspan="2">B</td></tr>'
                   '<tr><td colspan="2">C</td></tr></table>')
    assert result['status'] == 'UNSUPPORTED'


@pytest.mark.parametrize('value', [0, -1, True, 1.5, '3'])
def test_invalid_limit_rejected(value):
    with pytest.raises(ValueError):
        parse('<table/>', max_rows=value)


def test_empty_or_wrong_element_not_reported_complete():
    assert parse('<table/>')['status'] == 'UNSUPPORTED'
    assert parse('<div/>')['status'] == 'UNSUPPORTED'


def rectangle(rows, columns):
    return '<table>' + ''.join('<tr>' + ''.join(
        f'<td>{r}:{c}</td>' for c in range(columns)) + '</tr>' for r in range(rows)) + '</table>'


@pytest.mark.parametrize('rows,columns', [(15, 99), (15, 85), (99, 15), (85, 15)])
def test_wide_and_tall_coordinates_remain_in_original_order(rows, columns):
    element = html.fromstring(rectangle(rows, columns))
    result = parse_html_table(element)
    assert result['status'] == 'COMPLETE'
    assert result['grid'] == [[r * columns + c for c in range(columns)] for r in range(rows)]
    for i, cell in enumerate(result['cells']):
        r, c = divmod(i, columns)
        assert (cell['row'], cell['col'], cell['rowspan'], cell['colspan']) == (r, c, 1, 1)
        assert cell['text'] == f'{r}:{c}'
        assert element.getroottree().xpath(cell['source_path'])[0].text == f'{r}:{c}'


@pytest.mark.parametrize('rows,columns,accepted', [
    (80, 81, True), (81, 80, True), (81, 81, False),
    (1, 300, True), (300, 1, True), (1, 301, False), (301, 1, False),
    (40, 300, True), (41, 300, False),
])
def test_shape_axis_and_area_boundaries(rows, columns, accepted):
    result = parse(rectangle(rows, columns))
    assert result['status'] == ('COMPLETE' if accepted else 'LIMIT_EXCEEDED')
    if accepted:
        assert (result['row_count'], result['column_count']) == (rows, columns)
    else:
        assert result['grid'] == result['cells'] == []


@pytest.mark.parametrize('span', [98, 84])
def test_wide_colspan_with_rowspan_preserves_exact_occupancy(span):
    result = parse(f'<table><tr><th rowspan="2">A</th><th colspan="{span}">B</th></tr>'
                   f'<tr><td colspan="{span}">C</td></tr></table>')
    assert result['status'] == 'COMPLETE'
    assert result['grid'] == [[0] + [1] * span, [0] + [2] * span]
    assert [(cell['row'], cell['col'], cell['rowspan'], cell['colspan']) for cell in result['cells']] == [
        (0, 0, 2, 1), (0, 1, 1, span), (1, 1, 1, span)]


def test_cumulative_end_column_includes_rowspan_occupancy():
    result = parse('<table><tr><td rowspan="2" colspan="200">A</td></tr>'
                   '<tr><td colspan="101">B</td></tr></table>')
    assert result['status'] == 'LIMIT_EXCEEDED' and result['errors'] == ['GRID_LIMIT']
    assert result['grid'] == result['cells'] == []


@pytest.mark.parametrize('kwargs', [{'max_columns': 98}, {'max_rows': 14}, {'max_cells': 1484}])
def test_lowered_limits_cannot_be_bypassed_by_wide_shape(kwargs):
    result = parse(rectangle(15, 99), **kwargs)
    assert result['status'] == 'LIMIT_EXCEEDED'
    assert result['grid'] == result['cells'] == []


def test_wide_span_overlap_is_not_shifted_or_truncated():
    result = parse('<table><tr><td>A</td><td rowspan="2" colspan="98">B</td></tr>'
                   '<tr><td colspan="99">C</td></tr></table>')
    assert result['status'] == 'UNSUPPORTED'
    assert result['errors'] == ['OVERLAPPING_SPAN']
    assert result['grid'] == result['cells'] == []
