import ast
import copy
from pathlib import Path

import pytest

from prism_core.market_report_context import market_report_context


def test_off_is_empty():
    assert market_report_context(None) == ""
    assert market_report_context({"market_regime": "sideways"}) == ""


@pytest.mark.parametrize("language", ["ko", "en"])
def test_same_facts_period_missing_denominator_and_id_reach_pdf(tmp_path, language):
    from reportlab.pdfgen import canvas

    from pdf_converter import pdf_to_markdown_text
    context = {"market_regime": "sideways", "market_intelligence": {
        "price_asof": "2026-09-16", "source": "yfinance_adjusted_daily", "input_sha256": "abcdef1234567890",
        "rows": [{"symbol": "IWF", "returns_pct": {"20": 4.2}, "relative_spy_pp": {"20": 1.3}}],
        "participation": {"advance": 3, "decline": 1, "unchanged": 0, "valid_count": 4,
                          "universe_count": 7, "missing_count": 3, "asof": "20260917"}}}
    text = market_report_context(context, language)
    assert "?/4.2/?" in text and "?/1.3/?" in text
    assert "valid=4/7" in text and "missing=3" in text
    assert "MI-abcdef1234567890" in text
    path = tmp_path / "market.pdf"
    pdf = canvas.Canvas(str(path))
    item = pdf.beginText(30, 800)
    item.setFont("Helvetica", 8)
    for line in text.splitlines():
        item.textLine(line)
    pdf.drawText(item)
    pdf.save()
    extracted = pdf_to_markdown_text(str(path))
    assert "MI-abcdef1234567890" in extracted
    assert "?/4.2/?" in extracted


@pytest.mark.parametrize("relative", ["cores/analysis.py", "prism-us/cores/us_analysis.py"])
def test_shared_market_evidence_precedes_strategy_and_summary(relative):
    path = Path(__file__).resolve().parents[1] / relative
    calls = [node for node in ast.walk(ast.parse(path.read_text()))
             if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)]
    source = next(node.lineno for node in calls if node.func.id == "market_report_context")
    assert all(source < node.lineno for node in calls
               if node.func.id in {"generate_investment_strategy", "generate_summary"})


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_wholly_unmeasured_optional_rows_are_omitted_with_coverage_note(language):
    context = {'market_regime': 'sideways', 'market_intelligence': {
        'price_asof': '2026-09-24', 'source': 'yfinance_adjusted_daily',
        'rows': [
            {'symbol': 'SPY', 'returns_pct': {'5': 1.2}, 'relative_spy_pp': {'5': 0.0}},
            {'symbol': 'IWF', 'returns_pct': {}, 'relative_spy_pp': {}, 'status': 'UNKNOWN'},
            {'symbol': 'XLK', 'returns_pct': {}, 'relative_spy_pp': {}},
            {'symbol': 'IWM', 'returns_pct': {'20': 0.0}, 'relative_spy_pp': {'20': -1.0}},
        ]}}
    original = copy.deepcopy(context)
    text = market_report_context(context, language)
    assert '- IWF:' not in text and '- XLK:' not in text
    assert '- SPY: 1.2/?/? · 0.0/?/?' in text
    assert '- IWM: ?/0.0/? · ?/-1.0/?' in text
    assert 'IWF, XLK' in text
    assert ('미측정 선택 지표 2개' if language == 'ko' else '2 unmeasured optional indicators') in text
    assert context == original


@pytest.mark.parametrize('value', [None, '?', 'UNKNOWN', float('nan'), float('inf'), True])
def test_non_numeric_optional_values_are_not_presented_as_measurements(value):
    context = {'market_intelligence': {'rows': [
        {'symbol': 'IWF', 'returns_pct': {'5': value}, 'relative_spy_pp': {'60': value}}]}}
    text = market_report_context(context)
    assert '- IWF:' not in text and '미측정 선택 지표 1개' in text


@pytest.mark.parametrize('language', ['ko', 'en'])
def test_critical_unknowns_and_partial_measurement_remain_visible(language):
    context = {'market_intelligence': {
        'rows': [{'symbol': 'SPY', 'returns_pct': {}, 'relative_spy_pp': {}},
                 {'symbol': 'XLV', 'returns_pct': {}, 'relative_spy_pp': {'5': 0.0}},
                 {'symbol': 'RSP', 'returns_pct': {}, 'relative_spy_pp': {}}],
        'participation': {'valid_count': 4, 'universe_count': 9, 'missing_count': 5}}}
    text = market_report_context(context, language)
    assert '- SPY: ?/?/? · ?/?/?' in text
    assert '- XLV: ?/?/? · 0.0/?/?' in text
    assert '- RSP:' not in text
    assert 'valid=4/9' in text and 'missing=5' in text and 'asof=UNKNOWN' in text
    assert ('완료 일봉 기준: UNKNOWN' if language == 'ko' else 'Completed price date: UNKNOWN') in text
    assert ('확정된 매매 국면:' if language == 'ko' else 'Authoritative trading regime:') in text


def test_all_optional_rows_missing_does_not_leave_an_empty_returns_header():
    context = {'market_intelligence': {'rows': [{'symbol': 'IWD', 'returns_pct': {}, 'relative_spy_pp': {}}]}}
    text = market_report_context(context, 'en')
    assert '5/20/60-session returns' not in text
    assert '1 unmeasured optional indicator' in text and 'IWD' in text
    assert 'Uncollected fields remain unknown' in text


def test_real_html_pdf_boundary_preserves_measured_rows_and_missing_coverage(tmp_path):
    import asyncio
    import re
    from playwright.async_api import async_playwright
    from pdf_converter import _render_markdown_page, pdf_to_markdown_text

    context = {'market_regime': 'sideways', 'market_intelligence': {
        'price_asof': '2026-09-24', 'source': 'yfinance_adjusted_daily',
        'input_sha256': '213889d8d50e1586' + '0' * 48,
        'rows': [
            {'symbol': 'SPY', 'returns_pct': {'5': 1.2}, 'relative_spy_pp': {'5': 0.0}},
            {'symbol': 'IWF', 'returns_pct': {}, 'relative_spy_pp': {}},
            {'symbol': 'XLK', 'returns_pct': {}, 'relative_spy_pp': {}},
            {'symbol': 'IWM', 'returns_pct': {'20': 0.0}, 'relative_spy_pp': {'20': -1.0}}],
        'participation': {'valid_count': 4, 'universe_count': 9, 'missing_count': 5}}}
    md, pdf = tmp_path / 'market.md', tmp_path / 'market.pdf'
    md.write_text('# Market Analysis\n' + market_report_context(context, 'en'), encoding='utf-8')

    async def render():
        async with async_playwright() as playwright:
            executable = Path(playwright.chromium.executable_path)
            if not executable.exists():
                pytest.skip('Installed Chromium required for actual HTML-to-PDF boundary')
            browser = await playwright.chromium.launch(headless=True)
            try:
                isolated = await browser.new_context()
                # Real HTML/layout/PDF, but no external resources or network I/O.
                await isolated.route('http://**/*', lambda route: route.abort())
                await isolated.route('https://**/*', lambda route: route.abort())
                assert await _render_markdown_page(isolated, str(md), str(pdf), add_theme=False) == str(pdf)
            finally:
                await browser.close()

    asyncio.run(render())
    extracted = pdf_to_markdown_text(str(pdf))
    compact = re.sub(r'\s+', '', extracted)
    assert pdf.read_bytes().startswith(b'%PDF')
    assert 'SPY:1.2/?/?' in compact and 'IWM:?/0.0/?' in compact
    assert 'IWF:' not in extracted and 'XLK:' not in extracted
    assert '2unmeasuredoptionalindicatorsomitted:IWF,XLK' in compact
    assert 'valid=4/9' in compact and 'missing=5' in compact
    assert 'MI-213889d8d50e1586' in extracted
