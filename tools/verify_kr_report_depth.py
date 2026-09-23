"""One isolated, opt-in real report using the Telegram bot's host configuration.

Run as the normal app user. Never publishes normal caches, messages or orders.
Candidate code may live in a Git worktree; credentials stay on the same host.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]


def reviewed_chapter(directory, packet, *, company_code, reference_date, peer_context='', **_kwargs):
    """Validation-only reuse after human review; never a normal report cache.

    Reuses just source-only DART prose, not prior market/technical/synthesis
    stages. Fail instead of silently regenerating when the source basis changes.
    """
    from cores.dart_deep_analysis import (
        CHAPTER_END,
        CHAPTER_START,
        _checked_prose,
        _source_urls,
    )
    from report_model_config import DART_REPORT_EFFORT, DART_REPORT_MODEL

    receipt = json.loads((directory / 'generation_receipt.json').read_text(encoding='utf-8'))['receipt']
    source_hash = packet.get('receipt', {}).get('core_union_sha256')
    identity = {'company_code': company_code, 'reference_date': reference_date,
                'peer_context_sha256': hashlib.sha256(peer_context.encode()).hexdigest()}
    if (not packet.get('ready') or not isinstance(source_hash, str) or len(source_hash) != 64
            or receipt.get('input_identity') != identity
            or source_hash != receipt['source_receipt'].get('core_union_sha256')
            or not packet['receipt'].get('core_conserved')):
        raise ValueError('Reviewed chapter source, date, company or peer basis changed')
    if set(receipt['writers']) != set(packet['contexts']):
        raise ValueError('Reviewed chapter writer coverage changed')
    for role, writer in receipt['writers'].items():
        if writer.get('model') != DART_REPORT_MODEL or writer.get('reasoning_effort') != DART_REPORT_EFFORT:
            raise ValueError('Reviewed chapter writer configuration changed')
        _checked_prose((directory / f'dart_depth_{role}.md').read_text(encoding='utf-8'),
                      _source_urls(packet['contexts'][role]))
    chapter = (directory / 'dart_chapter.md').read_text(encoding='utf-8')
    if (not chapter.startswith(CHAPTER_START) or not chapter.rstrip().endswith(CHAPTER_END)
            or hashlib.sha256(chapter.encode()).hexdigest() != receipt.get('chapter_sha256')):
        raise ValueError('Reviewed chapter content changed')
    return chapter, {**receipt, 'status': 'reused_reviewed_source_chapter', 'calls': 0,
                      'original_calls': receipt['calls'], 'reused_from': str(directory)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operational-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--company', required=True)
    parser.add_argument('--date', default=datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d'))
    parser.add_argument('--run-model', action='store_true')
    parser.add_argument('--peer-source-report', type=Path,
                        help='Read-only peer smoke from an existing report, without model calls')
    parser.add_argument('--render-source-report', type=Path,
                        help='Reapply publication formatting and PDF rendering, without model calls')
    parser.add_argument('--replay-inputs', type=Path,
                        help='Reuse a saved chapter packet; run only the three DART writers')
    parser.add_argument('--peer-receipt', type=Path,
                        help='Optional same-day peer receipt for chapter-only replay')
    parser.add_argument('--reuse-reviewed-chapter', type=Path,
                        help='Full integration only: reuse an explicitly reviewed same-source chapter')
    parser.add_argument('--resume-sections', type=Path,
                        help='Explicit staged validation: reuse six captured base drafts, not a production cache')
    args = parser.parse_args()
    if sum(map(bool, (args.run_model, args.peer_source_report, args.render_source_report))) != 1:
        parser.error('Choose one of --run-model, --peer-source-report or --render-source-report')
    operational = args.operational_root.resolve()
    output = args.output.resolve()
    if not output.is_relative_to(operational / 'runtime' / 'report_validation'):
        parser.error('Output must be isolated under the host report_validation directory')
    if args.replay_inputs and not args.replay_inputs.resolve().is_relative_to(
            operational / 'runtime' / 'report_validation'):
        parser.error('Replay inputs must come from the isolated validation directory')
    if args.peer_receipt and (not args.replay_inputs or not args.peer_receipt.resolve().is_relative_to(
            operational / 'runtime' / 'report_validation')):
        parser.error('Peer receipt is only supported inside an isolated chapter replay')
    if args.peer_source_report and (args.replay_inputs or not args.peer_source_report.resolve().is_relative_to(
            operational / 'runtime' / 'report_validation')):
        parser.error('Peer smoke source must be an isolated validation report')
    if args.reuse_reviewed_chapter and (not args.run_model or args.replay_inputs
            or not args.reuse_reviewed_chapter.resolve().is_relative_to(operational / 'runtime' / 'report_validation')):
        parser.error('Reviewed chapter reuse requires a full isolated integration run')
    if args.render_source_report and (args.replay_inputs or args.peer_receipt
            or not args.render_source_report.resolve().is_relative_to(operational / 'runtime' / 'report_validation')):
        parser.error('Render source must be an isolated validation report without model replay inputs')
    if args.resume_sections and (not args.run_model or args.replay_inputs
            or not args.resume_sections.resolve().is_relative_to(operational / 'runtime' / 'report_validation')):
        parser.error('Section resume is restricted to full isolated validation')
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

    from cores import (
        analysis,
        dart_deep_analysis,
        report_fact_editor,
        report_generation,
    )
    from cores.analysis import analyze_stock
    from cores.market_data import default_chain
    from prism_core import kr_official_report_inputs, kr_peer_comparison
    if default_chain().names != ['kis-remote']:
        raise RuntimeError('Validation must not use local broker credentials')
    original_collect = kr_official_report_inputs.collect_kr_official_report_inputs
    original_write = dart_deep_analysis._write
    original_peers = kr_peer_comparison.collect_peer_comparison
    original_editor = report_fact_editor.edit_and_summarize

    def save_json(name, value):
        (output / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')

    if args.run_model:
        backend = report_generation._get_report_backend()
        original_run = backend.run

        async def capture_editor_reply(spec, message):
            result = await original_run(spec, message)
            if spec.name == 'report_final_fact_editor':
                (output / 'final_editor_reply.json').write_text(result.text, encoding='utf-8')
            return result

        backend.run = capture_editor_reply

    async def capture_editor(*a, **kw):
        edited, summary, receipt = await original_editor(*a, **kw)
        save_json('final_editor_receipt.json', receipt)
        for section in ('company_status', 'company_overview'):
            (output / f'edited_{section}.md').write_text(edited.get(section, ''), encoding='utf-8')
        (output / 'edited_summary.md').write_text(summary, encoding='utf-8')
        return edited, summary, receipt

    report_fact_editor.edit_and_summarize = capture_editor

    resumed = {}
    source_checked = False
    save_json('validation_invocation.json', {
        'ticker': args.ticker, 'company': args.company, 'reference_date': args.date,
        'started_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
        'resume_sections': str(args.resume_sections) if args.resume_sections else None,
        'note': 'Staged drafts are explicitly selected by the operator; this is not a same-input A/B claim.'})

    # Preserve each generated section for source/contradiction review. Never
    # publish these intermediate drafts to the bot's ordinary report cache.
    def capture_section(original):
        async def captured(agent, section, *a, **kw):
            if section not in {'price_volume_analysis', 'investor_trading_analysis', 'company_status',
                               'company_overview', 'news_analysis', 'market_index_analysis'}:
                raise ValueError('Unknown report section artifact name')
            if args.resume_sections:
                if not source_checked:
                    raise ValueError('Current filing source basis was not verified before section resume')
                source = args.resume_sections / f'section_{section}.md'
                text = source.read_text(encoding='utf-8')
                if len(text.strip()) < 300:
                    raise ValueError('Captured section is not substantive')
                resumed[section] = {'source': str(source), 'sha256': hashlib.sha256(text.encode()).hexdigest(),
                                    'characters': len(text), 'model_calls': 0}
                save_json('staged_section_reuse.json', resumed)
            else:
                text = await original(agent, section, *a, **kw)
            (output / f'section_{section}.md').write_text(text, encoding='utf-8')
            return text
        return captured

    analysis.generate_report = capture_section(analysis.generate_report)
    analysis.generate_market_report = capture_section(analysis.generate_market_report)

    async def collect(*a, **kw):
        nonlocal source_checked
        value = await original_collect(*a, **kw)
        save_json('dart_inputs.json', value.get('dart_chapter_inputs', {}))
        if args.resume_sections:
            prior = json.loads((args.resume_sections / 'dart_inputs.json').read_text(encoding='utf-8'))
            current = value.get('dart_chapter_inputs', {})
            if (not prior.get('ready') or not current.get('ready')
                    or not prior['receipt'].get('core_union_sha256')
                    or prior['receipt']['core_union_sha256'] != current['receipt'].get('core_union_sha256')):
                raise ValueError('Captured base drafts belong to a different filing source basis')
            source_checked = True
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
    if args.reuse_reviewed_chapter:
        async def reuse(packet, **kwargs):
            chapter, receipt = reviewed_chapter(args.reuse_reviewed_chapter, packet, **kwargs)
            save_json('reviewed_chapter_reuse.json', receipt)
            return chapter, receipt
        dart_deep_analysis.generate_dart_chapter = reuse
    started = time.monotonic()
    if args.render_source_report:
        from prism_core.report_presentation import humanize_report_status
        from report_generator import _render_pdf_atomically
        original = args.render_source_report.read_text(encoding='utf-8')
        body = humanize_report_status(original, 'ko')
        if re.findall(r'\d+(?:[.,]\d+)*', original) != re.findall(r'\d+(?:[.,]\d+)*', body):
            raise RuntimeError('Publication formatting changed numeric tokens')
        md = output / f'{args.ticker}_{args.date}_analysis.md'
        pdf = md.with_suffix('.pdf')
        md.write_text(body, encoding='utf-8')
        _render_pdf_atomically(md, pdf)
        receipt = {'status': 'rendered_existing_report_without_model_calls', 'model_calls': 0,
                   'source_report': str(args.render_source_report), 'md': str(md), 'pdf': str(pdf),
                   'numeric_tokens_preserved': True, 'elapsed_seconds': round(time.monotonic() - started, 2)}
        save_json('generation_receipt.json', receipt)
        print(json.dumps(receipt, ensure_ascii=False))
        return
    if args.peer_source_report:
        from cores.analysis import _report_stock_names
        candidates = kr_peer_comparison.select_report_peer_candidates(
            [args.peer_source_report.read_text(encoding='utf-8')], args.ticker, _report_stock_names())
        packet = asyncio.run(original_peers(args.ticker, args.company, candidates, args.date))
        save_json('peer_receipt.json', {**packet['private_receipt'], 'probe': {
            'reference_date': datetime.strptime(args.date, '%Y%m%d').replace(tzinfo=ZoneInfo('Asia/Seoul')).date().isoformat(),
            'observed_at': datetime.now(ZoneInfo('Asia/Seoul')).isoformat(),
            'provider': 'native Firecrawl MCP', 'source_report': str(args.peer_source_report),
            'candidates': candidates}})
        (output / 'peer_comparison.md').write_text(packet['public_markdown'], encoding='utf-8')
        print(json.dumps({'status': packet['private_receipt']['status'], 'candidates': len(candidates),
                          'requested': packet['private_receipt'].get('requested', 0),
                          'received': packet['private_receipt'].get('received', 0),
                          'model_calls': 0, 'elapsed_seconds': round(time.monotonic() - started, 2)}))
        if packet['private_receipt']['status'] != 'available':
            raise RuntimeError('Native peer smoke did not produce a comparable peer snapshot')
        return
    if args.replay_inputs:
        from prism_core.dart_chapter_sources import enrich_dart_chapter_inputs
        packet = enrich_dart_chapter_inputs(json.loads(args.replay_inputs.read_text(encoding='utf-8')))
        save_json('dart_inputs.json', packet)
        peer_context = ''
        if args.peer_receipt:
            peers = json.loads(args.peer_receipt.read_text(encoding='utf-8'))
            if (peers.get('status') != 'available'
                    or peers.get('probe', {}).get('reference_date', '').replace('-', '') != args.date):
                raise RuntimeError('Peer replay must use a successful same-day probe')
            peer_context = kr_peer_comparison.render_peer_comparison(peers['snapshots'])
            (output / 'peer_comparison.md').write_text(peer_context, encoding='utf-8')
        chapter, receipt = asyncio.run(dart_deep_analysis.generate_dart_chapter(
            packet, company_name=args.company, company_code=args.ticker,
            reference_date=args.date, peer_context=peer_context))
        if not chapter:
            raise RuntimeError('Replay sources are not ready; no completed chapter')
        (output / 'dart_chapter.md').write_text(chapter, encoding='utf-8')
        save_json('generation_receipt.json', {
            'status': 'chapter_only_content_review_required',
            'elapsed_seconds': round(time.monotonic() - started, 2),
            'source_packet': str(args.replay_inputs.resolve()), 'receipt': receipt})
        print(json.dumps({'status': 'chapter_only_content_review_required',
                          'calls': receipt['calls'], 'output': str(output)}))
        return
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
