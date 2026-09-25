"""Fresh, opt-in bot report-service validation without polling or delivery.

Uses the real cache lookup, subprocess generator, Markdown save and PDF save.
Reports/PDFs/diagnostics are isolated in output. Existing production code also
writes candidate-root logs/subprocess and sibling charts; these are recorded,
not misrepresented as output-only effects. Never run against the operational
checkout. No staged drafts, model wrappers, or source reuse are installed.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]


def _configure_environment(operational, output):
    from dotenv import load_dotenv
    load_dotenv(operational / '.env')
    load_dotenv(operational / '.env.report-oauth', override=True)
    endpoint = urlsplit(os.environ.get('OPENAI_BASE_URL', ''))
    if (endpoint.scheme != 'http' or endpoint.hostname != '127.0.0.1' or endpoint.port is None
            or endpoint.username or endpoint.password
            or os.environ.get('OPENAI_API_KEY') != 'chatgpt-oauth-placeholder'):
        raise RuntimeError('Existing loopback ChatGPT OAuth route is required')
    remote = os.environ.get('ARCHIVE_API_URL')
    if not remote:
        raise RuntimeError('Existing app-to-DB market-data route is required')
    os.environ['PRISM_MARKET_DATA_REMOTE_URL'] = remote
    os.environ['PRISM_MCP_PYTHON'] = sys.executable
    os.environ['PRISM_REPO_ROOT'] = str(ROOT)
    os.environ['PRISM_DISABLE_SIGNAL_PUBLISH'] = '1'
    os.environ['PRISM_REPORT_DIAGNOSTICS_DIR'] = str(output / 'diagnostics')
    os.environ['PATH'] = str(Path.home() / '.local/bin') + os.pathsep + os.environ.get('PATH', '')
    stock_map = operational / 'runtime' / 'stock_map.json'
    if stock_map.exists():
        os.environ.setdefault('PRISM_STOCK_MAP_PATH', str(stock_map))
    from cores.llm.config_loader import load_mcp_registry
    registry = load_mcp_registry(ROOT / 'cores/llm/mcp_servers.yaml',
                                 legacy_env_fallback=operational / 'mcp_agent.config.yaml')
    for name, key in (('firecrawl', 'FIRECRAWL_API_KEY'), ('perplexity', 'PERPLEXITY_API_KEY')):
        value = registry.get(name).env.get(key)
        if value and not os.environ.get(key):
            os.environ[key] = value
    from cores.market_data import default_chain
    if default_chain().names != ['kis-remote']:
        raise RuntimeError('Validation must use the existing remote data route')


def _invocation_date(requested, market):
    current = datetime.now(ZoneInfo('Asia/Seoul')).strftime('%Y%m%d')
    if requested is not None and requested != current:
        raise ValueError('--date must equal the current Asia/Seoul invocation day')
    if market == 'kr' and datetime.now().strftime('%Y%m%d') != current:
        raise ValueError('Host and Asia/Seoul dates differ; do not pretend historical bot parity')
    return current


def _artifact(path, output, suffix):
    if path is None:
        raise ValueError('Missing saved report artifact')
    path = Path(path)
    resolved = path.resolve()
    if (path.is_symlink() or not resolved.is_relative_to(output)
            or resolved.suffix != suffix or not resolved.is_file() or resolved.stat().st_size == 0):
        raise ValueError('Report artifact is missing, empty, or outside isolated output')
    return {'path': str(resolved), 'bytes': resolved.stat().st_size,
            'sha256': hashlib.sha256(resolved.read_bytes()).hexdigest()}


def run_validation(*, operational_root, output, ticker, company, market='kr', date=None):
    operational, output = Path(operational_root).resolve(), Path(output).resolve()
    candidate = ROOT.resolve()
    if candidate == operational or candidate.is_relative_to(operational):
        raise ValueError('A separate candidate worktree outside the operational checkout is required')
    validation_root = operational / 'runtime' / 'report_validation'
    if output == validation_root or not output.is_relative_to(validation_root):
        raise ValueError('Output must be a new directory under operational runtime/report_validation')
    if market not in {'kr', 'us'}:
        raise ValueError('Unsupported market')
    if not re.fullmatch(r'[0-9]{6}' if market == 'kr' else r'[A-Z][A-Z0-9]*(?:[.-][A-Z0-9]+)?', ticker):
        raise ValueError('Invalid stock identifier')
    if (not isinstance(company, str) or not company.strip()
            or any(character in company for character in ('/', '\\', '\0'))):
        raise ValueError('Company name is required')
    invocation_date = _invocation_date(date, market)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    receipt = {'status': 'started', 'market': market, 'ticker': ticker,
               'invocation_date': invocation_date,
               'reference_policy': 'production_host_current_date' if market == 'kr' else 'production_last_trading_day',
               'fresh_bot_service_entry': True, 'delivery_tested': False,
               'candidate_root': str(candidate),
               'declared_side_effect_paths': [str(candidate / 'logs' / 'subprocess'), str(candidate.parent / 'charts')],
               'note': 'Fresh service/subprocess path; no staged drafts, model wrappers, bot polling, or delivery.'}
    started = time.monotonic()
    previous_cwd = Path.cwd()
    previous_path = sys.path[:]
    generator = None
    original_dirs = {}
    try:
        sys.path.insert(0, str(candidate))
        _configure_environment(operational, output)
        # Isolate import-time relative artifact directories as well.
        os.chdir(output)
        import report_generator as generator
        from prism_core import report_service
        if Path(generator.__file__).resolve().parent != candidate:
            raise ValueError('Report generator is not imported from the candidate worktree')
        reports, pdfs = output / 'reports', output / 'pdf_reports'
        reports.mkdir(exist_ok=True)
        pdfs.mkdir(exist_ok=True)
        names = ('REPORTS_DIR', 'PDF_REPORTS_DIR') if market == 'kr' else ('US_REPORTS_DIR', 'US_PDF_REPORTS_DIR')
        for name, directory in zip(names, (reports, pdfs)):
            original_dirs[name] = getattr(generator, name)
            setattr(generator, name, directory)
        if any(reports.iterdir()) or any(pdfs.iterdir()):
            raise ValueError('Validation cache directories must start empty')
        os.chdir(candidate)
        artifact = report_service.generate_report(ticker, company, market=market, cache_only=False)
        if artifact.status != report_service.COMPLETED or artifact.cached:
            raise ValueError('Fresh report service generation did not complete')
        if not generator._is_cacheable_report(artifact.content):
            raise ValueError('Generated report failed the production cacheability contract')
        if market == 'kr':
            from cores.dart_deep_analysis import CHAPTER_START, CHAPTER_END
            if CHAPTER_START not in artifact.content or CHAPTER_END not in artifact.content:
                raise ValueError('Generated Korean report lacks the completed DART chapter')
        markdown = _artifact(artifact.markdown_path, output, '.md')
        pdf = _artifact(artifact.pdf_path, output, '.pdf')
        if Path(markdown['path']).read_text(encoding='utf-8') != artifact.content:
            raise ValueError('Saved Markdown does not match the generated report')
        receipt.update(status='completed', cached=False, markdown=markdown, pdf=pdf)
        return receipt
    except Exception as error:
        receipt.update(status='failed', error_type=type(error).__name__)
        raise
    finally:
        receipt['duration_seconds'] = round(time.monotonic() - started, 3)
        try:
            (output / 'bot_entry_receipt.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        finally:
            if generator is not None:
                for name, directory in original_dirs.items():
                    setattr(generator, name, directory)
            sys.path[:] = previous_path
            os.chdir(previous_cwd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--operational-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ticker', required=True)
    parser.add_argument('--company', required=True)
    parser.add_argument('--market', choices=('kr', 'us'), default='kr')
    parser.add_argument('--date')
    args = parser.parse_args(argv)
    try:
        receipt = run_validation(operational_root=args.operational_root, output=args.output,
                                 ticker=args.ticker, company=args.company, market=args.market, date=args.date)
    except Exception as error:
        print(json.dumps({'status': 'failed', 'error_type': type(error).__name__}))
        return 1
    print(json.dumps(receipt, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
