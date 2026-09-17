"""Explicit rollout scopes fail closed before network/cache access."""
import asyncio

import pytest

from prism_core.report_research_prefetch import VERSION, prefetch_report_research


@pytest.mark.parametrize('scope', [{}, {'US': []}, {'KR': ['005930']},
                                  {'US': ['MU']}, {'US': ''}, [], None])
def test_unvalidated_or_empty_scope_does_not_collect(tmp_path, scope):
    async def no_transport(*args):
        pytest.fail('out-of-scope symbol must not access the network')

    result = asyncio.run(prefetch_report_research(
        'US', 'SMCI', '20250918', 'Super Micro Computer', _transport=no_transport,
        _config={'version': VERSION, 'enabled': True, 'validated_symbols': scope},
        _cache_dir=tmp_path / 'cache'))
    assert result is None
    assert not (tmp_path / 'cache').exists()
