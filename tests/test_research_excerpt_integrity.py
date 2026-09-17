from prism_core.report_research_prefetch import _bounded_excerpt


def test_table_keeps_header_and_all_peer_rows():
    table = '| Company | Q2 2026 |\n| --- | --- |\n| Alpha | 40% |\n| Beta | 60% |'
    excerpt, omitted = _bounded_excerpt('Revenue market share\n' + table, 150)
    assert table in excerpt
    assert not omitted


def test_oversized_table_is_omitted_not_partial():
    table = '| Company | Q2 2026 |\n| --- | --- |\n' + '| Alpha | 40% |\n' * 50
    excerpt, omitted = _bounded_excerpt('Revenue market share\n' + table, 100)
    assert '| Alpha' not in excerpt
    assert omitted
    assert len(excerpt) <= 100


def test_long_line_never_becomes_partial_claim():
    excerpt, omitted = _bounded_excerpt('Revenue ' + 'x' * 800, 700)
    assert excerpt == ''
    assert omitted


def test_unselected_text_is_reported_as_omitted():
    excerpt, omitted = _bounded_excerpt('Unrelated heading\n\n' * 20 + 'Revenue is unchanged.', 100)
    assert 'Revenue is unchanged.' in excerpt
    assert omitted


def test_long_chart_url_cannot_separate_basis_from_table():
    table = '| Company | Q2 2026 |\n| Alpha | 40% |\n| Beta | 60% |'
    image = '![Global DRAM market share by revenue](https://example.com/' + 'a' * 900 + ')'
    excerpt, _ = _bounded_excerpt(image + '\n' + table, 200)
    assert 'Chart caption: Global DRAM market share by revenue\n' + table == excerpt
    assert 'https://' not in excerpt


def test_oversized_chart_basis_and_table_are_omitted_together():
    table = '| Company | Q2 2026 |\n| Alpha | 40% |'
    image = '![' + 'Revenue basis ' * 50 + '](https://example.com/a)'
    excerpt, omitted = _bounded_excerpt(image + '\n' + table, 200)
    assert '| Alpha' not in excerpt
    assert omitted
