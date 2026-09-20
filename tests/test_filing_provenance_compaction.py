from prism_core.filing_report_evidence import (
    compact_html_provenance,
    expand_html_provenance,
)


def test_dom_locator_compaction_is_exactly_reversible():
    paths = [f'/html[1]/body[1]/main[1]/p[{i}]/text()[1]' for i in range(10, 30)]
    source = {'source_path': paths[0], 'source_paths': paths, 'footnote_paths': [paths[-1]],
              'section_path': ['III. 재무에 관한 사항'], 'scope': 'consolidated', 'footnotes': '다만 조건이 있습니다.'}
    compact = compact_html_provenance(source)
    assert 'dom_paths' in compact
    assert expand_html_provenance(compact) == source
    assert source['source_paths'] == paths  # no caller mutation


def test_non_html_and_short_metadata_are_not_modified():
    source = {'source_spans': [[1, 4]], 'scope': 'unknown'}
    assert compact_html_provenance(source) == source
    assert expand_html_provenance(source) == source


def test_position_and_xpath_literal_distinctions_survive():
    a = "/html[1]/body[1]/*[name()='o:p'][1]"
    b = "/html[1]/body[1]/*[name()='o:p'][2]"
    source = {'source_path': a, 'source_paths': [a, b], 'footnote_paths': []}
    assert expand_html_provenance(compact_html_provenance(source)) == source


def test_conflicting_dual_forms_are_not_silently_overwritten():
    invalid = {'source_path': '/wrong', 'dom_paths': {'base': '/html/', 'source': 'body', 'parts': ['body']}}
    for operation in (compact_html_provenance, expand_html_provenance):
        with pytest.raises(ValueError, match='CONFLICTING_DOM_PROVENANCE'):
            operation(invalid)


def test_compact_expansion_has_a_finite_work_budget():
    with pytest.raises(ValueError, match='COMPACT_DOM_PROVENANCE_LIMIT'):
        expand_html_provenance({'dom_paths': {'base': '/' + 'a' * 10000 + '/',
                                            'source': 'body', 'parts': ['x'] * 2000}})
import pytest
