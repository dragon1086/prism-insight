"""One isolated, opt-in real report using the Telegram bot's host configuration.

Run as the normal app user. Never publishes normal caches, messages or orders.
Candidate code may live in a Git worktree; credentials stay on the same host.
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operational-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--company', required=True)
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'))
    parser.add_argument('--run-model', action='store_true')
    args = parser.parse_args()
    if not args.run_model:
        parser.error('Explicit --run-model is required; no implicit model run')
    operational = args.operational_root.resolve()
    output = args.output.resolve()
    if not output.is_relative_to(operational / 'runtime' / 'report_validation'):
        parser.error('Output must be isolated under the host report_validation directory')
    output.mkdir(parents=True, exist_ok=False)

    from dotenv import load_dotenv
    load_dotenv(operational / '.env')
    load_dotenv(operational / '.env.report-oauth', override=True)
    if not (os.environ.get('OPENAI_BASE_URL', '').startswith('http://127.0.0.1:')
            and os.environ.get('OPENAI_API_KEY') == 'chatgpt-oauth-placeholder'):
        raise RuntimeError('The existing loopback ChatGPT OAuth route is required')
    if not os.environ.get('ARCHIVE_API_URL'):
        raise RuntimeError('The existing app-to-DB market-data route is required')
    os.environ.setdefault('PRISM_MARKET_DATA_REMOTE_URL', os.environ['ARCHIVE_API_URL'])
    os.environ['PRISM_MCP_PYTHON'] = sys.executable
    os.environ['PRISM_REPO_ROOT'] = str(ROOT)
    os.environ['PRISM_DISABLE_SIGNAL_PUBLISH'] = '1'
    os.environ['PATH'] = str(Path.home() / '.local/bin') + os.pathsep + os.environ.get('PATH', '')
    stock_map = operational / 'runtime' / 'stock_map.json'
    if stock_map.exists():
        os.environ.setdefault('PRISM_STOCK_MAP_PATH', str(stock_map))
    sys.path.insert(0, str(ROOT))
    os.chdir(ROOT)

    from cores.llm.config_loader import load_mcp_registry
    registry = load_mcp_registry(ROOT / 'cores/llm/mcp_servers.yaml',
                                 legacy_env_fallback=operational / 'mcp_agent.config.yaml')
    # Reuse only already-resolved tool credentials in memory, never write/copy
    # them into the candidate worktree or print them into the audit.
    for name, key in (('firecrawl', 'FIRECRAWL_API_KEY'), ('perplexity', 'PERPLEXITY_API_KEY')):
        value = registry.get(name).env.get(key)
        if value and not os.environ.get(key):
            os.environ[key] = value

    from cores import dart_deep_analysis
    from cores.analysis import analyze_stock
    from cores.market_data import default_chain
    from prism_core import kr_official_report_inputs, kr_peer_comparison
    if default_chain().names != ['kis-remote']:
        raise RuntimeError('Validation must not use local broker credentials')
    original_collect = kr_official_report_inputs.collect_kr_official_report_inputs
    original_write = dart_deep_analysis._write
    original_peers = kr_peer_comparison.collect_peer_comparison

    def save_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    async def collect(*a, **kw):
        value = await original_collect(*a, **kw)
        save_json('dart_inputs.json', value.get('dart_chapter_inputs', {}))
        return value

    async def write(agent, message):
        text, usage = await original_write(agent, message)
        (output / (agent.name + '.md')).write_text(text, encoding='utf-8')
        save_json(agent.name + '_usage.json', {'input_bytes': len((agent.instruction + message).encode()),
                                             'usage': usage, 'output_chars': len(text)})
        return text, usage

    async def peers(*a, **kw):
        value = await original_peers(*a, **kw)
        save_json('peer_receipt.json', value.get('private_receipt', {}))
        return value

    kr_official_report_inputs.collect_kr_official_report_inputs = collect
    dart_deep_analysis._write = write
    kr_peer_comparison.collect_peer_comparison = peers
    started = time.monotonic()
    body = asyncio.run(analyze_stock(args.ticker, args.company, args.date, require_dart_depth=True))
    from report_generator import _is_cacheable_report, _render_pdf_atomically
    if not _is_cacheable_report(body) or dart_deep_analysis.CHAPTER_START not in body:
        raise RuntimeError('Incomplete output was not promoted to a report')
    md = output / f'{args.ticker}_{args.date}_depth_analysis.md'
    pdf = md.with_suffix('.pdf')
    md.write_text(body, encoding='utf-8')
    _render_pdf_atomically(md, pdf)
    receipt = {'status': 'generated_content_review_required', 'ticker': args.ticker, 'date': args.date,
               'elapsed_seconds': round(time.monotonic() - started, 2), 'characters': len(body),
               'md': str(md), 'pdf': str(pdf), 'pdf_bytes': pdf.stat().st_size}
    save_json('generation_receipt.json', receipt)
    print(json.dumps(receipt, ensure_ascii=False))


if __name__ == '__main__':
    main()
