"""Fixed PDF evidence delivery to real US BUY consumer, not SEC/LLM quality proof."""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUN_CONSUMER = r'''
import asyncio
import logging
from pathlib import Path
import socket
import sys
import textwrap
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

root, output = map(Path, sys.argv[1:3])
sys.path.insert(0, str(root / 'prism-us'))
sys.path.insert(1, str(root))

def forbidden(*args, **kwargs):
    raise AssertionError('No external side effects permitted')

class NoFileHandler(logging.NullHandler):
    def __init__(self, *args, **kwargs):
        super().__init__()

import dotenv
with patch.object(dotenv, 'load_dotenv', return_value=False), \
     patch.object(logging, 'FileHandler', NoFileHandler), \
     patch.object(socket.socket, 'connect', forbidden), \
     patch.object(socket, 'create_connection', forbidden):
    # Bind US package namespaces before the consumer inserts the KR root path.
    import cores
    import tracking
    assert Path(cores.__file__).resolve().is_relative_to(root / 'prism-us')
    assert Path(tracking.__file__).resolve().is_relative_to(root / 'prism-us')
    import us_stock_tracking_agent as consumer
    from pdf_converter import pdf_to_markdown_text
    from reportlab.pdfgen import canvas

    claim = 'A covenant exists but no breach occurred; waiver conditions still require review.'
    period = 'Period: 2026-01-01 to 2026-06-30.'
    scope = 'Scope: consolidated.'
    url = 'https://www.sec.gov/Archives/edgar/data/0000000000/fixture.htm'
    source = 'Source: US-FIXTURE-1'
    caveat = 'Fixed source fixture; not independently fact-validated.'
    fragments = (claim, period, scope, url, source, caveat)
    pdf = canvas.Canvas(str(output))
    text = pdf.beginText(36, 800)
    text.setFont('Helvetica', 8)
    for line in fragments:
        for wrapped in textwrap.wrap(line, width=90, break_long_words=False):
            text.textLine(wrapped)
    pdf.drawText(text)
    pdf.save()
    extracted = pdf_to_markdown_text(str(output))
    for fragment in fragments:
        assert fragment in ' '.join(extracted.split())

    agent = consumer.USStockTrackingAgent.__new__(consumer.USStockTrackingAgent)
    agent.cursor = Mock()
    agent.cursor.fetchall.return_value = []
    agent._account_scope = Mock(return_value=('isolated', None))
    agent._get_current_slots_count = AsyncMock(return_value=0)
    agent.max_slots = 10
    agent.enable_journal = False
    agent.language = 'en'
    agent.trading_agent = SimpleNamespace(instruction='Existing US BUY rules.', attach_llm=AsyncMock(side_effect=forbidden))
    agent._stamp_scenario_market_regime = lambda value: value
    backend = AsyncMock(return_value=SimpleNamespace(text='{"decision":"No Entry"}', latency_s=0, mcp_calls=[]))
    with patch.object(consumer, 'generate_codex_fast_async', backend), \
         patch.object(consumer.ExecutionService, 'us', side_effect=forbidden) as broker, \
         patch.object(consumer.Bot, 'send_message', side_effect=forbidden) as channel:
        scenario = asyncio.run(agent._extract_trading_scenario(extracted))
    assert scenario['decision'] == 'No Entry'
    backend.assert_awaited_once()
    prompt = ' '.join(backend.await_args.kwargs['user_prompt'].split())
    assert 'US stock' in prompt
    for fragment in fragments:
        assert fragment in prompt
    assert backend.await_args.kwargs['system_prompt'] == 'Existing US BUY rules.'
    assert backend.await_args.kwargs['mcp_profile'] == 'us_trading'
    agent.trading_agent.attach_llm.assert_not_awaited()
    broker.assert_not_called()
    channel.assert_not_called()
    assert not agent._get_db_lock().locked()
print('US_PDF_BUY_DELIVERY_VERIFIED')
'''


def test_real_us_buy_receives_pdf_material_in_isolated_namespace(tmp_path):
    # Do not inherit API keys, provider configuration, or production HOME.
    env = {key: os.environ[key] for key in ('PATH', 'SYSTEMROOT') if key in os.environ}
    env.update(HOME=str(tmp_path), PRISM_US_CODEX_FAST_TRADING='1', PYTHONHASHSEED='0',
               PRISM_DISABLE_SIGNAL_PUBLISH='1', OTEL_SDK_DISABLED='true')
    result = subprocess.run(
        [sys.executable, '-c', RUN_CONSUMER, str(ROOT), str(tmp_path / 'us-material.pdf')],
        cwd=tmp_path, env=env, text=True, capture_output=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'US_PDF_BUY_DELIVERY_VERIFIED' in result.stdout
