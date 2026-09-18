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


def test_markdown_escaped_rounding_note_is_atomic_with_table():
    table = 'Global market share by revenue\n| Company | Q2 2026 |\n| Alpha | 33% |\n| Beta | 68% |'
    note = r'_\*Due to rounding, the total may not add up to 100%._'
    full, _ = _bounded_excerpt(table + '\n\n' + note, 250)
    assert note in full
    small, omitted = _bounded_excerpt(table + '\n\n' + note, len(table) + 1)
    assert '| Alpha' not in small
    assert omitted


def test_multisentence_scope_footnote_cannot_be_partially_kept():
    table = 'Revenue share\n| Company | Q2 2026 |\n| Alpha | 33% |'
    note = 'Note: values are reported as provided. Comparisons exclude a major subsidiary in 2026.'
    excerpt, omitted = _bounded_excerpt(table + '\n' + note, len(table) + 40)
    assert '| Alpha' not in excerpt
    assert omitted


def test_competitor_bullet_list_keeps_its_relationship_context():
    text = 'Our direct competitors include:\n- Alpha Corp\n- Beta Corp\n- Gamma Corp'
    excerpt, omitted = _bounded_excerpt(text, 60)
    assert 'competitors include' not in excerpt
    assert 'Alpha Corp' not in excerpt
    assert omitted


def test_pdf_unicode_competitor_bullets_are_also_atomic():
    text = 'Our current competitors include:\n• Alpha Corp\n• Beta Corp\n• Gamma Corp'
    excerpt, omitted = _bounded_excerpt(text, 60)
    assert 'competitors include' not in excerpt
    assert 'Alpha Corp' not in excerpt
    assert omitted


def test_pdf_unicode_bullets_without_space_keep_whole_sentences():
    text = 'Our current competitors include:\n•Alpha Corp. It competes only in optical equipment.\n•Beta Corp'
    excerpt, omitted = _bounded_excerpt(text, 70)
    assert 'competitors include' not in excerpt
    assert 'Alpha Corp' not in excerpt
    assert omitted
