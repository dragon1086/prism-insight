"""Usage is optional measurement, not inferred billing or a content log."""
import logging
from types import SimpleNamespace as NS

import pytest

from cores.llm.backends.openai_agents_backend import (
    OpenAIAgentsBackend,
    extract_run_usage,
)
from cores.llm.mcp_registry import McpServerRegistry
from cores.llm.ports import AgentSpec


def usage(**overrides):
    return NS(**({'requests': 1, 'input_tokens': 10, 'output_tokens': 5,
                 'total_tokens': 15} | overrides))


def result(*usages):
    return NS(raw_responses=[NS(usage=value) for value in usages])


@pytest.mark.parametrize('value', [NS(), result(), result(None), result(usage(requests=0)),
                                  result(usage(requests=True)), result(usage(requests=2))])
def test_missing_usage_is_unknown_not_zero(value):
    assert extract_run_usage(value) is None


def test_sdk_default_usage_does_not_become_free_call():
    from agents.usage import Usage
    assert extract_run_usage(result(Usage())) is None


def test_complete_counts_sum_each_response_once_not_context():
    value = result(usage(), usage(input_tokens=20, total_tokens=25))
    value.context_wrapper = NS(usage=usage(input_tokens=9999))
    assert extract_run_usage(value) == {'requests': 2, 'reported_requests': 2,
        'missing_usage_requests': 0, 'input_tokens': 30, 'output_tokens': 10, 'total_tokens': 40}


def test_explicit_reported_zero_output_is_preserved():
    assert extract_run_usage(result(usage(output_tokens=0)))['output_tokens'] == 0


def test_partial_response_coverage_never_understates_aggregate():
    value = extract_run_usage(result(usage(), None))
    assert value['reported_requests'] == 1 and value['missing_usage_requests'] == 1
    assert value['input_tokens'] is None and value['output_tokens'] is None
    assert value['total_tokens'] is None


@pytest.mark.parametrize('bad', [None, True, -1, '10', float('nan'), 10.0])
def test_missing_or_malformed_field_unknown_not_coerced(bad):
    value = extract_run_usage(result(usage(input_tokens=bad)))
    assert value['input_tokens'] is None
    assert value['output_tokens'] == 5


@pytest.mark.asyncio
async def test_backend_returns_usage_and_logs_counts_only(monkeypatch, caplog):
    import cores.llm.backends.openai_agents_backend as mod
    monkeypatch.setattr(mod, '_sdk_available', True)
    monkeypatch.setattr(mod, 'build_agent', lambda *_: object())
    value = result(usage())
    value.final_output = 'PRIVATE_RESPONSE'
    value.last_response_id = 'PRIVATE_ID'

    class Runner:
        async def run(self, *args, **kwargs):
            return value

    caplog.set_level(logging.INFO, logger=mod.__name__)
    registry = McpServerRegistry.from_yaml_dict({'mcp': {'servers': {}}})
    backend = OpenAIAgentsBackend(registry, runner=Runner())
    spec = AgentSpec(name='usage-test', instructions='PRIVATE_INSTRUCTION', model='test')
    observed = await backend.run(spec, 'PRIVATE_PROMPT')
    assert observed.usage['input_tokens'] == 10
    logs = [record.message for record in caplog.records if '[LLM_USAGE]' in record.message]
    assert len(logs) == 1 and 'input_tokens=10' in logs[0]
    assert 'PRIVATE_' not in caplog.text
