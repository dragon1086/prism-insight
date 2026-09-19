"""Offline consumer contracts, not generated-report or investment-quality proof.

The report backend returns a fixed source-preserving fixture. PDF rendering uses
ReportLab, not the production layout renderer. The real PDF reader and KR BUY
prompt builder run; no model, broker, authentication or channel calls run.
US BUY and fresh SELL evidence delivery are explicitly outside this test.
"""
import json
import logging
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from cores.agents.report_agent import ReportAgent
from cores.buy_gate import evaluate_production_buy_gate
from cores.llm.ports import LLMResult
from prism_core.filing_report_evidence import filing_blocks
from prism_core.order_budget import whole_share_quantity
from prism_core.report_insight_prefetch import packet
from prism_core.report_research_context import apply_section_research

URL = 'https://kind.krx.co.kr/external/2026/03/18/001712/20260318007942/11011.htm'
CLAIM = 'A covenant exists but no breach occurred; waiver conditions still require review.'
PERIOD = 'Period: 2026-01-01 to 2026-06-30.'
SOURCE = ('## III. 재무에 관한 사항\n### 3. 연결재무제표 주석\n'
          f'38. 차입금 및 재무약정\n\n{CLAIM} {PERIOD}\n')


def material_packet():
    blocks, gaps = filing_blocks({'markdown': SOURCE}, URL, material_notes=True)
    assert blocks and not gaps
    assert blocks[0]['status'] == 'SOURCE_TEXT_NOT_FACT_VALIDATED'
    assert blocks[0]['provenance']['scope'] == 'consolidated'
    return packet('KR', '327260', '2026-09-18', {
        'sources': [{'source_id': 'M-1', 'url': URL, 'blocks': blocks}],
        'gaps': gaps, 'calls': 0, 'filing_parser': 'material_v2',
    })


def fixture_report():
    return (f'### Filing review\n{CLAIM}\n{PERIOD}\nScope: consolidated.\n'
            f'Source: M-1\n{URL}\nSource text is not independently fact-validated.')


def pdf_text(report, path):
    from reportlab.pdfgen import canvas

    from pdf_converter import pdf_to_markdown_text

    pdf = canvas.Canvas(str(path))
    text = pdf.beginText(36, 800)
    text.setFont('Helvetica', 8)
    for line in report.splitlines():
        for wrapped in textwrap.wrap(line, width=90, break_long_words=False):
            text.textLine(wrapped)
    pdf.drawText(text)
    pdf.save()
    extracted = pdf_to_markdown_text(str(path))
    for preserved in (CLAIM, PERIOD, 'Scope: consolidated.', 'M-1', URL,
                      'not independently fact-validated'):
        assert preserved in ' '.join(extracted.split())
    return extracted


@pytest.fixture
def extracted_pdf(tmp_path):
    return pdf_text(fixture_report(), tmp_path / 'material.pdf')


@pytest.mark.asyncio
@pytest.mark.parametrize('market,symbol', [('KR', '327260'), ('US', 'EXAMPLE')])
async def test_real_report_and_synthesis_boundaries_preserve_fixture_material(monkeypatch, tmp_path, market, symbol):
    """US parameter is common synthesis compatibility, not US DART collection."""
    import cores.report_generation as generation

    seen = []

    class Backend:
        async def run(self, spec, user_input):
            seen.append((spec, user_input))
            return LLMResult(text=fixture_report())

    monkeypatch.setattr(generation, '_report_backend', Backend())
    evidence = material_packet()
    owner = next(key for key, note in evidence['section_notes'].items() if CLAIM in note)
    original = ReportAgent('Material review', 'Existing report rules.', ['firecrawl'])
    agent = apply_section_research(original, owner, {'report_research': evidence}, '20260918', 'en')
    assert original.instruction == 'Existing report rules.'
    report = await generation.generate_report(agent, owner, 'Example', symbol, '20260918', Mock(), 'en')
    reports = {owner: report}
    await generation.generate_investment_strategy(reports, report, 'Example', symbol, '20260918', Mock(), 'en')
    summary = await generation.generate_summary(reports, 'Example', symbol, '20260918', Mock(), 'en')
    assert len(seen) == 3
    instruction = seen[0][0].instructions
    assert 'not an automatic BUY/SELL rule' in instruction
    for fragment in (CLAIM, PERIOD, URL, 'M-1', 'SOURCE_TEXT_NOT_FACT_VALIDATED'):
        assert fragment in instruction
    for _, user_input in seen[1:]:
        for fragment in (CLAIM, PERIOD, URL, 'M-1', 'Scope: consolidated.',
                         'not independently fact-validated'):
            assert fragment in user_input
    pdf_text(summary, tmp_path / f'{market}-synthesis.pdf')


@pytest.mark.asyncio
async def test_real_kr_buy_prompt_receives_pdf_material_without_orders(monkeypatch, extracted_pdf):
    import dotenv

    class NoFileHandler(logging.NullHandler):
        def __init__(self, *args, **kwargs):
            super().__init__()

    monkeypatch.setattr(logging, 'FileHandler', NoFileHandler)
    monkeypatch.setattr(dotenv, 'load_dotenv', lambda *args, **kwargs: False)
    import stock_tracking_agent as tracking

    monkeypatch.setenv('PRISM_KR_CODEX_FAST_TRADING', '1')
    agent = tracking.StockTrackingAgent.__new__(tracking.StockTrackingAgent)
    agent.cursor = Mock()
    agent.cursor.fetchall.return_value = []
    agent._account_scope = Mock(return_value=('isolated', None))
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent.max_slots = 10
    agent.enable_journal = False
    agent.language = 'en'
    agent.trading_agent = SimpleNamespace(instruction='Existing BUY rules.', attach_llm=AsyncMock())
    agent._stamp_scenario_market_regime = lambda value: value
    backend = AsyncMock(return_value=SimpleNamespace(
        text='{"decision":"No Entry"}', latency_s=0, mcp_calls=[]))
    monkeypatch.setattr(tracking, 'generate_codex_fast_async', backend)
    broker = Mock(side_effect=AssertionError('No broker access permitted'))
    monkeypatch.setattr(tracking.ExecutionService, 'domestic', broker)
    scenario = await agent._extract_trading_scenario(extracted_pdf)
    assert scenario['decision'] == 'No Entry'  # fixed response, not judgment quality
    prompt = backend.await_args.kwargs['user_prompt']
    for fragment in (CLAIM, PERIOD, URL, 'M-1', 'Scope: consolidated.',
                     'not independently fact-validated'):
        assert fragment in ' '.join(prompt.split())
    assert backend.await_args.kwargs['system_prompt'] == 'Existing BUY rules.'
    agent.trading_agent.attach_llm.assert_not_awaited()
    broker.assert_not_called()
    assert not agent._get_db_lock().locked()


CONTEXTS = [
    'Normal growth with cash flow matching earnings.',
    'Seasonal negative cash flow reflects inventory build; no impairment confirmed.',
    'Bank cash flow is not comparable to manufacturing working capital.',
    'Biotech conditional milestone is not booked revenue.',
    'Convertible bonds were fully redeemed; no remaining conversion rights.',
    'Convertible bonds remain outstanding; future dilution is conditional.',
    CLAIM,
    'An actual covenant breach occurred; waiver was not obtained.',
]


@pytest.mark.parametrize('context', CONTEXTS)
@pytest.mark.parametrize('valid', [True, False])
def test_same_candidate_material_metadata_cannot_override_gate_or_quantity(context, valid):
    original = {'buy_score': 8, 'min_score': 5, 'target_price': 115.0 if valid else 99.0,
                'stop_loss': 95.0, 'risk_reward_ratio': 3.0, 'market_condition': 'moderate_bull'}
    enriched = {**original, 'material_filing_review': {
        'text': context, 'source': URL, 'period': PERIOD, 'scope': 'consolidated',
        'status': 'SOURCE_TEXT_NOT_FACT_VALIDATED',
    }}
    baseline = evaluate_production_buy_gate(original, current_price=100, market_regime='moderate_bull')
    result = evaluate_production_buy_gate(enriched, current_price=100, market_regime='moderate_bull')
    assert baseline['allowed'] is valid
    assert result == baseline
    assert result['hard_findings'] == baseline['hard_findings']
    # Pure sizing has no evidence argument; no score, cash or stop widening is introduced.
    assert whole_share_quantity(1050, 100) == 10
    assert original['stop_loss'] == enriched['stop_loss'] == 95
    assert json.loads(json.dumps(enriched))['material_filing_review']['text'] == context
