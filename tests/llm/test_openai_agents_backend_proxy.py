from __future__ import annotations

from cores.llm.backends import openai_agents_backend as backend_module


def test_proxy_configuration_disables_sdk_tracing(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class FakeClient:
        def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            timeout: object,
            max_retries: int,
        ) -> None:
            calls.append(
                ("client", (base_url, api_key, timeout, max_retries))
            )

    monkeypatch.setattr(backend_module, "_sdk_available", True)
    monkeypatch.setattr(backend_module, "AsyncOpenAI", FakeClient)
    monkeypatch.setattr(
        backend_module,
        "set_tracing_disabled",
        lambda disabled: calls.append(("tracing_disabled", disabled)),
        raising=False,
    )
    monkeypatch.setattr(
        backend_module,
        "set_default_openai_client",
        lambda client: calls.append(("default_client", client)),
    )
    monkeypatch.setattr(
        backend_module,
        "set_default_openai_api",
        lambda api: calls.append(("default_api", api)),
    )
    monkeypatch.setattr(
        backend_module,
        "set_default_openai_key",
        lambda key: calls.append(("default_key", key)),
    )

    backend_module.configure_openai_agents_for_proxy("http://localhost:18741/v1")

    assert ("tracing_disabled", True) in calls
