"""Execute the real fallback branches, without model/network or broker calls."""
import ast
import asyncio
from contextlib import asynccontextmanager
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from prism_core.isolated_agent_runtime import configured_mcp_app, attach_isolated_llm

ROOT = Path(__file__).resolve().parents[1]


def lazy_class(path, app_class):
    tree = ast.parse((ROOT / path).read_text())
    node = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "_LazyMCPApp")
    namespace = {"MCPApp": app_class, "configured_mcp_app": configured_mcp_app,
                 "asynccontextmanager": asynccontextmanager}
    exec(compile(ast.Module(body=[node], type_ignores=[]), path, "exec"), namespace)
    return namespace["_LazyMCPApp"]


@pytest.mark.asyncio
@pytest.mark.parametrize("path,market,side", [
    ("stock_tracking_agent.py", "kr", "buy"),
    ("stock_tracking_enhanced_agent.py", "kr", "sell"),
    ("prism-us/us_stock_tracking_agent.py", "us", "buy"),
    ("prism-us/us_stock_tracking_agent.py", "us", "sell"),
])
@pytest.mark.parametrize("codex_runtime", [False, True])
async def test_every_fallback_uses_instance_app_and_binds_agent_llm_context(path, market, side, codex_runtime):
    host_calls, llm_calls = [], []
    class App:
        def __init__(self, **kwargs):
            self._dotenv_loaded = False
            self.context = SimpleNamespace(config=kwargs["settings"])
            self.kwargs = kwargs
        @asynccontextmanager
        async def run(self):
            assert self._dotenv_loaded is True
            host_calls.append(self.kwargs)
            yield self
    class LLM:
        def __init__(self, *, context, agent):
            assert context is not None and agent.context is context
            self.context = context
        async def generate_str(self, **kwargs):
            llm_calls.append(self.context.config.openai.base_url)
            return '{"decision":"hold"}'
    class Agent:
        context = None
        async def attach_llm(self, factory):
            return factory(agent=self)
    @asynccontextmanager
    async def forbidden_global_host():
        raise AssertionError("global MCP app must not be used")
        yield
    async def kr_scenario(llm, message):
        return json.loads(await llm.generate_str(message=message))

    lazy_path = "stock_tracking_agent.py" if market == "kr" else "prism-us/us_stock_tracking_agent.py"
    Lazy = lazy_class(lazy_path, App)
    tree = ast.parse((ROOT / path).read_text())
    method_name = "_extract_trading_scenario" if side == "buy" else "_analyze_sell_decision"
    method = next(n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef) and n.name == method_name)
    variable = "scenario_json" if side == "buy" else "response"
    branch = next(n for n in ast.walk(method) if isinstance(n, ast.If)
                  and ast.unparse(n.test) == variable + " is None"
                  and any(isinstance(child, ast.AsyncFunctionDef) for child in n.body))
    body = [ast.Assign(targets=[ast.Name(id=variable, ctx=ast.Store())], value=ast.Constant(None)),
            branch, ast.Return(value=ast.Name(id=variable, ctx=ast.Load()))]
    wrapper = ast.AsyncFunctionDef(name="dispatch", args=ast.arguments(
        posonlyargs=[], args=[], kwonlyargs=[], kw_defaults=[], defaults=[]), body=body, decorator_list=[])
    harness = ast.fix_missing_locations(ast.Module(body=[wrapper], type_ignores=[]))
    for number in (1, 2):
        base_url = f"http://127.0.0.1:{9000 + number}/v1"
        factory = lambda url=base_url: {"mcp": {"servers": {}}, "openai": {"base_url": url, "api_key": "explicit-test-key"}}
        instance_app = Lazy("isolated-" + str(number), factory)
        instance = SimpleNamespace(_instance_mcp_app=instance_app, trading_agent=Agent(),
                                   sell_decision_agent=Agent(), _get_legacy_fallback_lock=asyncio.Lock)
        namespace = {"self": instance, "app": SimpleNamespace(run=forbidden_global_host),
                     "attach_isolated_llm": attach_isolated_llm, "OpenAIAugmentedLLM": LLM,
                     "RequestParams": SimpleNamespace, "prompt_message": "synthetic no-order analysis",
                     "parse_llm_json": lambda text, **kwargs: json.loads(text),
                     "_generate_trading_scenario_json": kr_scenario,
                     "asyncio": asyncio, "logger": logging.getLogger("isolated-test"), "ticker_tag": "SYNTHETIC",
                     f"_{market}_codex_runtime_enabled": lambda: codex_runtime}
        exec(compile(harness, path, "exec"), namespace)
        result = await namespace["dispatch"]()
        assert result == ({"decision": "hold"} if side == "buy" else '{"decision":"hold"}')
        assert instance_app.context is None
        assert host_calls[-1]["settings"].openai.base_url == base_url
    assert llm_calls == ["http://127.0.0.1:9001/v1", "http://127.0.0.1:9002/v1"]
    assert len(host_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"])
async def test_default_lazy_app_constructor_contract_unchanged(path):
    calls = []
    class App:
        def __init__(self, **kwargs):
            calls.append(kwargs)
        @asynccontextmanager
        async def run(self):
            yield self
    Lazy = lazy_class(path, App)
    default = Lazy("production-default")
    assert calls == []
    async with default.run():
        pass
    assert calls == [{"name": "production-default"}]


def explicit_factory():
    return {"mcp": {"servers": {}},
            "openai": {"base_url": "http://127.0.0.1:9999/v1", "api_key": "explicit-test-key"}}


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"])
async def test_delayed_start_reserves_before_first_await(path):
    entered, release = asyncio.Event(), asyncio.Event()
    created = []
    class App:
        def __init__(self, **kwargs):
            self._dotenv_loaded = False
            self.context = None
            created.append(self)
        @asynccontextmanager
        async def run(self):
            entered.set()
            await release.wait()
            self.context = SimpleNamespace()
            yield self
    instance = lazy_class(path, App)("isolated", explicit_factory)
    async def run():
        async with instance.run():
            assert instance.context is not None
    first = asyncio.create_task(run())
    await entered.wait()
    try:
        with pytest.raises(RuntimeError, match="already active"):
            await asyncio.wait_for(run(), timeout=0.03)
        assert len(created) == 1
    finally:
        release.set()
        await first
    assert instance.context is None and instance._run_reserved is False


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["stock_tracking_agent.py", "prism-us/us_stock_tracking_agent.py"])
@pytest.mark.parametrize("outcome", ["error", "cancel"])
async def test_startup_failure_or_cancellation_releases_reservation(path, outcome):
    entered, never = asyncio.Event(), asyncio.Event()
    created = []
    class App:
        def __init__(self, **kwargs):
            self._dotenv_loaded = False
            self.context = None
            self.first = not created
            created.append(self)
        @asynccontextmanager
        async def run(self):
            if self.first:
                entered.set()
                if outcome == "error":
                    raise RuntimeError("startup failed")
                await never.wait()
            self.context = SimpleNamespace()
            yield self
    instance = lazy_class(path, App)("isolated", explicit_factory)
    async def run():
        async with instance.run():
            assert instance.context is not None
    first = asyncio.create_task(run())
    await entered.wait()
    if outcome == "cancel":
        first.cancel()
    with pytest.raises(asyncio.CancelledError if outcome == "cancel" else RuntimeError):
        await first
    assert instance.context is None and instance._run_reserved is False
    await run()
    assert len(created) == 2
    assert instance.context is None and instance._run_reserved is False
