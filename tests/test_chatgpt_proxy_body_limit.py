"""Exercise real aiohttp body parsing with a stubbed, never-networked upstream."""
import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from cores.chatgpt_proxy import proxy_server


@pytest.mark.parametrize("route", ["/v1/chat/completions", "/v1/responses"])
@pytest.mark.parametrize("content_size,expected_status", [(1100 * 1024, 200), (8 * 1024 * 1024 + 1, 413)])
def test_large_body_forwarded_only_within_finite_limit(monkeypatch, route, content_size, expected_status):
    forwarded = []

    async def fake_forward(body):
        forwarded.append(body)
        return {"id": "test", "output": [], "usage": {}}, None

    monkeypatch.setattr(proxy_server, "_forward_to_codex", fake_forward)
    # A truthy sentinel exercises the real chat precondition without reading auth.
    monkeypatch.setattr(proxy_server, "_token_manager", None)

    async def run():
        app = proxy_server.create_app(object())
        async with TestClient(TestServer(app)) as client:
            content = "x" * content_size
            key = "messages" if route.endswith("completions") else "input"
            body = {"model": "gpt-5", key: [{"role": "user", "content": content}]}
            async with client.post(route, json=body) as response:
                assert response.status == expected_status
                await response.read()
        assert len(forwarded) == (1 if expected_status == 200 else 0)
        if forwarded:
            # Size fixes must never truncate sources or change translation policies.
            assert forwarded[0]["input"][0]["content"] == content

    asyncio.run(run())
