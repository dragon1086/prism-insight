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
                 '-L 127.0.0.1:18741:127.0.0.1:18741'):
        assert flag in unit
    config = (ROOT / 'deploy/report-oauth.env.example').read_text()
    assert 'http://127.0.0.1:18741/v1' in config
    assert 'OPENAI_API_KEY=chatgpt-oauth-placeholder' in config
