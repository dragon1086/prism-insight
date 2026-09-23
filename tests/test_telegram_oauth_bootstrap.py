"""Only startup environment statements, no Telegram or network initialization."""
import ast
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_oauth_override_loaded_before_worker_and_sdk_imports(monkeypatch):
    source = ROOT / 'telegram_ai_bot.py'
    tree = ast.parse(source.read_text())
    calls = []

    def load(path=None, override=False):
        calls.append((path, override))
        if path is not None:
            monkeypatch.setenv('OPENAI_BASE_URL', 'http://127.0.0.1:18741/v1')
            monkeypatch.setenv('OPENAI_API_KEY', 'chatgpt-oauth-placeholder')

    statements = []
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module in ('telegram', 'analysis_manager'):
            break
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name) and node.value.func.id == 'load_dotenv'):
            statements.append(node)
    exec(compile(ast.Module(body=statements, type_ignores=[]), str(source), 'exec'),  # noqa: S102 - only allowlisted startup calls
         {'load_dotenv': load, '__file__': str(source), 'Path': Path})
    assert calls == [(None, False), (ROOT / '.env.report-oauth', True)]
    assert os.environ['OPENAI_BASE_URL'] == 'http://127.0.0.1:18741/v1'
    assert os.environ['OPENAI_API_KEY'] == 'chatgpt-oauth-placeholder'


def test_tunnel_example_is_loopback_only_and_noninteractive():
    unit = (ROOT / 'deploy/systemd/prism-report-oauth-tunnel.service.example').read_text()
    assert 'User=prism' in unit
    for flag in ('BatchMode=yes', 'ExitOnForwardFailure=yes', 'StrictHostKeyChecking=yes',
                 '-L 127.0.0.1:18741:127.0.0.1:18742'):
        assert flag in unit
    config = (ROOT / 'deploy/report-oauth.env.example').read_text()
    assert 'http://127.0.0.1:18741/v1' in config
    assert 'OPENAI_API_KEY=chatgpt-oauth-placeholder' in config


def test_report_proxy_is_separate_from_batch_and_validates_host_auth(monkeypatch):
    import importlib.util

    from aiohttp import web

    from cores.chatgpt_proxy import proxy_server, token_manager
    spec = importlib.util.spec_from_file_location('report_proxy_test', ROOT / 'tools/run_report_oauth_proxy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls = []

    class Manager:
        def validate_or_fail(self):
            calls.append('validated')

    monkeypatch.setattr(token_manager, 'TokenManager', Manager)
    monkeypatch.setattr(proxy_server, 'create_app', lambda manager: calls.append('app') or 'APP')
    monkeypatch.setattr(web, 'run_app', lambda app, **kwargs: calls.append((app, kwargs)))
    module.main()
    assert calls == ['validated', 'app', ('APP', {'host': '127.0.0.1', 'port': 18742, 'access_log': None})]


def test_report_proxy_reloads_host_token_and_never_refreshes(monkeypatch):
    import asyncio
    import importlib.util
    import time

    import pytest

    from cores.chatgpt_proxy.token_manager import ChatGPTAuthExpiredError
    spec = importlib.util.spec_from_file_location('report_proxy_reader_test', ROOT / 'tools/run_report_oauth_proxy.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    manager = module.create_report_token_manager()
    current = {'access_token': 'synthetic-first', 'expires_at': time.time() + 3600}
    monkeypatch.setattr(manager, '_load_from_disk', lambda: dict(current))

    async def forbidden(*args):
        pytest.fail('Report reader must never refresh shared host credentials')

    monkeypatch.setattr(manager, '_refresh_token', forbidden)

    async def run():
        assert await manager.get_token() == 'synthetic-first'
        current['access_token'] = 'synthetic-rotated'
        assert await manager.get_token() == 'synthetic-rotated'
        current['expires_at'] = 0
        with pytest.raises(ChatGPTAuthExpiredError):
            await manager.get_token()

    asyncio.run(run())
