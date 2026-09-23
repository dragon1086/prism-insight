"""No model/network calls: shared evidence boundaries and lossless publication."""
from cores.agents.report_agent import ReportAgent
from prism_core.us_report_consistency import (
    add_shared_context,
    evidence_appendix,
    reference_context,
)


def test_reference_context_keeps_quote_separate_and_unknown_not_close():
    context = reference_context({'stock_info': '| Current Price | $234.76 |\n| Previous Close | $244.88 |'}, 'en')
    assert '$234.76' in context and 'observed quote' in context
    assert 'not a confirmed close' in context
    assert 'fiscal period' in context and 'company guidance' in context


def test_reference_context_carries_only_exact_financial_calculation_blocks():
    from prism_core.report_financial_math import render_target_upside_calculations

    calculated = render_target_upside_calculations({'target_mean': 247.4}, 234.76)
    prefetched = {'stock_info': calculated + '\nRAW PROFILE NOT SHARED',
                  'financial_statements': 'RAW INCOME NOT SHARED',
                  'analysis_estimates': '| Mean target from another snapshot | 248 |'}
    for language in ('ko', 'en'):
        context = reference_context(prefetched, language)
        assert '5.3842%' in context and '247.40 USD / 234.76 USD' in context
        assert 'RAW PROFILE NOT SHARED' not in context and 'RAW INCOME NOT SHARED' not in context
        assert '248' not in context
    english = reference_context(prefetched, 'en')
    assert 'different target snapshot' in english and 'stock_info' in english


def test_optional_financial_appendix_retains_exact_record_without_mutation():
    record = '### Code-calculated annual leverage\n| Debt / (Debt + Equity) | 47.8735% |'
    original = {'company_status': 'READABLE STATUS'}
    public, appendix = evidence_appendix(original, 'en', financial_reference=record)
    assert public == original and record in appendix


def test_shared_news_is_untrusted_evidence_not_new_tools_or_verification():
    agent = ReportAgent('status', 'BASE', ('time',))
    changed = add_shared_context(agent, 'REFERENCE', 'NEWS GUIDANCE SENTINEL', 'en')
    assert agent.instruction == 'BASE'
    assert changed.server_names == agent.server_names
    assert 'NEWS GUIDANCE SENTINEL' in changed.instruction
    assert 'not independently verified' in changed.instruction
    assert 'REFERENCE' in changed.instruction


def test_appendix_preserves_complete_records_and_narrative():
    record = '#### Competitive Evidence\nEvidence ID: CE-test\n- field: margin; value: 12%; source: https://example.test\n'
    original = {'news_analysis': 'NEWS\n' + record + '\n### Conclusion\nCONCLUSION',
                'company_overview': 'OVERVIEW\n#### Competitive Evidence Handoff\nID NOTE\n\n' + record}
    public, appendix = evidence_appendix(original, 'ko')
    assert 'NEWS' in public['news_analysis'] and 'CONCLUSION' in public['news_analysis']
    assert 'OVERVIEW' in public['company_overview']
    assert 'field: margin' not in public['news_analysis']
    assert appendix.count('field: margin; value: 12%; source: https://example.test') == 2
    assert 'ID NOTE' in appendix and 'CE-test' in appendix
    assert record in original['news_analysis']


def test_fenced_pseudo_record_stays_untouched():
    original = {'news_analysis': '```markdown\n#### Competitive Evidence\nNOT A RECORD\n```'}
    public, appendix = evidence_appendix(original, 'en')
    assert public == original and not appendix


def test_canonical_calculation_record_moves_without_losing_values():
    reference = '### AUTHORITATIVE TECHNICAL FACTS\n| SMA50 | 233.3818 |\nUse exact values.'
    original = {'price_volume_analysis': 'READABLE PRICE PROSE\n\n' + reference}
    public, appendix = evidence_appendix(original, 'ko', reference)
    assert public['price_volume_analysis'] == 'READABLE PRICE PROSE'
    assert reference in appendix and original['price_volume_analysis'].endswith(reference)


def test_nested_record_headings_never_delete_following_conclusion():
    original = {'news_analysis': 'INTRO\n### Competitive Evidence\nOUTER\n'
                '#### Competitive Evidence Handoff\nINNER\n### Conclusion\nIMPORTANT CONCLUSION MUST STAY\n'}
    public, appendix = evidence_appendix(original, 'en')
    assert public['news_analysis'] == 'INTRO\n### Conclusion\nIMPORTANT CONCLUSION MUST STAY\n'
    assert appendix.count('INNER') == 1 and appendix.count('OUTER') == 1


def test_three_letter_ticker_not_duplicated_on_pdf_cover():
    from pdf_converter import _extract_report_info
    info = _extract_report_info('# Quest Diagnostics Incorporated (DGX) 분석 보고서')
    assert info['company_name'] == 'Quest Diagnostics Incorporated'
    assert info['company_code'] == 'DGX'


def test_existing_korean_preferred_share_code_is_preserved():
    from pdf_converter import _extract_report_info
    info = _extract_report_info('# CJ4우(전환) (00104K) 분석 보고서')
    assert info['company_name'] == 'CJ4우(전환)' and info['company_code'] == '00104K'
