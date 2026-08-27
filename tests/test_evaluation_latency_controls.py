from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
import yaml

import report_generator


def test_evaluate_handlers_apply_cache_and_total_timeout() -> None:
    root = Path(__file__).resolve().parents[1]
    tree = ast.parse((root / "telegram_ai_bot.py").read_text())
    methods = {
        node.name: node
        for class_node in tree.body
        if isinstance(class_node, ast.ClassDef)
        for node in class_node.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name in {"handle_background_input", "handle_us_background_input"}
    }

    assert methods.keys() == {"handle_background_input", "handle_us_background_input"}
    for method in methods.values():
        call_names = {
            node.func.id
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        attribute_calls = {
            node.func.attr
            for node in ast.walk(method)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        assert "get_recent_evaluation_report" in call_names
        assert "wait_for" in attribute_calls


def test_evaluation_mcp_servers_have_bounded_read_timeouts() -> None:
    root = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((root / "cores/llm/mcp_servers.yaml").read_text())
    servers = config["servers"]

    assert servers["time"]["read_timeout_seconds"] == 10
    assert servers["kospi_kosdaq"]["read_timeout_seconds"] == 45
    assert servers["perplexity"]["read_timeout_seconds"] == 75
    assert servers["yahoo_finance"]["read_timeout_seconds"] == 45


def test_generate_telegram_text_enforces_timeout(monkeypatch) -> None:
    class HangingBackend:
        async def run(self, _spec, _message):
            await asyncio.sleep(1)

    monkeypatch.setattr(report_generator, "_telegram_backend", HangingBackend())
    agent = report_generator.Agent(
        name="timeout-agent",
        instruction="test",
        server_names=[],
    )

    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(
            report_generator._generate_telegram_text(
                agent=agent,
                message="test",
                max_tokens=100,
                timeout_seconds=0.01,
            )
        )


def test_cached_kr_report_avoids_perplexity_and_supports_no_tool_fallback(
    monkeypatch, tmp_path
) -> None:
    report = tmp_path / "009150_report.md"
    report.write_text("# 삼성전기\n최근 보고서 근거", encoding="utf-8")
    calls = []

    async def fake_generate(*, agent, message, max_tokens, timeout_seconds=None):
        calls.append((tuple(agent.server_names), timeout_seconds, message))
        if len(calls) == 1:
            raise asyncio.TimeoutError
        return "📌 결론: 보유\n보고서 기준 대응"

    monkeypatch.setattr(report_generator, "_generate_telegram_text", fake_generate)

    result = asyncio.run(
        report_generator.generate_evaluation_response(
            "009150",
            "삼성전기",
            1_340_000,
            6,
            "간결하게",
            "없음",
            report_path=str(report),
        )
    )

    assert "보유" in result
    assert "perplexity" not in calls[0][0]
    assert calls[0][1] == report_generator.EVALUATION_AGENT_TIMEOUT_SECONDS
    assert calls[1][0] == ()
    assert calls[1][1] == report_generator.EVALUATION_FALLBACK_TIMEOUT_SECONDS


def test_uncached_kr_evaluation_keeps_full_tool_set(monkeypatch) -> None:
    calls = []

    async def fake_generate(*, agent, message, max_tokens, timeout_seconds=None):
        calls.append(tuple(agent.server_names))
        return "📌 결론: 관망"

    monkeypatch.setattr(report_generator, "_generate_telegram_text", fake_generate)

    result = asyncio.run(
        report_generator.generate_evaluation_response(
            "009150", "삼성전기", 1_340_000, 6, "간결하게", "없음"
        )
    )

    assert "관망" in result
    assert calls == [("perplexity", "kospi_kosdaq", "time")]
