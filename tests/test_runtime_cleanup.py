import asyncio
import subprocess
import sys
from types import SimpleNamespace

from cores.llm.runtime_cleanup import pending_runtime_tasks, shutdown_mcp_logging


def test_no_existing_bus_is_not_initialized(monkeypatch):
    monkeypatch.delitem(sys.modules, "mcp_agent.logging.transport", raising=False)
    assert asyncio.run(shutdown_mcp_logging()) is True
    assert "mcp_agent.logging.transport" not in sys.modules


def test_existing_bus_stopped(monkeypatch):
    calls = []

    async def stop():
        calls.append("stopped")

    module = SimpleNamespace(AsyncEventBus=SimpleNamespace(_instance=SimpleNamespace(stop=stop)))
    monkeypatch.setitem(sys.modules, "mcp_agent.logging.transport", module)
    assert asyncio.run(shutdown_mcp_logging()) is True
    assert calls == ["stopped"]


def test_cooperative_timeout_is_reported_without_killing_process(monkeypatch):
    async def stop():
        await asyncio.sleep(60)

    monkeypatch.setitem(sys.modules, "mcp_agent.logging.transport",
                        SimpleNamespace(AsyncEventBus=SimpleNamespace(_instance=SimpleNamespace(stop=stop))))
    assert asyncio.run(shutdown_mcp_logging(timeout_seconds=0.01)) is False


def test_task_summary_excludes_current_task():
    async def run():
        assert pending_runtime_tasks() == []
    asyncio.run(run())


def test_actual_mcp_lifecycle_cleanup_stops_bus_and_exits():
    # Fresh process avoids resetting an SDK singleton used by unrelated tests.
    code = '''
import asyncio
from mcp_agent.app import MCPApp
from prism_core.isolated_agent_runtime import configured_mcp_app
from cores.llm.runtime_cleanup import shutdown_mcp_logging, pending_runtime_tasks
async def main():
    app = configured_mcp_app(MCPApp, "cleanup_probe", lambda: {
        "openai": {"api_key": "fake", "base_url": "http://127.0.0.1:1/v1"},
        "mcp": {"servers": {}}})
    async with app.run():
        pass
    await asyncio.sleep(0.1)
    assert any("AsyncEventBus" in name for name in pending_runtime_tasks())
    assert await shutdown_mcp_logging()
    await asyncio.sleep(0)
    # SDK may leave a Queue.get child for asyncio.run to cancel. Do not claim
    # all SDK tasks are drained, or cancel unrelated application tasks here.
    assert all(name == "Queue.get" for name in pending_runtime_tasks()), pending_runtime_tasks()
asyncio.run(main())
print("CLEAN_EXIT")
'''
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    assert "CLEAN_EXIT" in result.stdout
