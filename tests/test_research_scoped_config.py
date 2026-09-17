import json

import pytest

from tools.configure_report_research import configure


def test_enable_and_disable_preserve_market_and_validated_scope(tmp_path):
    path = tmp_path / 'config.json'
    configure(path, False, market_context_enabled=True)
    config = configure(path, True, validated_symbols={'KR': ['005930'], 'US': ['MU']})
    assert config['market_context_enabled'] is True
    disabled = configure(path, False)
    assert disabled['validated_symbols'] == config['validated_symbols']
    assert disabled['market_context_enabled'] is True
    assert json.loads(path.read_text()) == disabled


@pytest.mark.parametrize('scope', [[], {'XX': ['MU']}, {'US': 'MU'}, {'US': [1]}, {'US': ['../secret']}])
def test_invalid_scope_does_not_modify_config(tmp_path, scope):
    path = tmp_path / 'config.json'
    configure(path, False)
    before = path.read_bytes()
    with pytest.raises(ValueError):
        configure(path, True, validated_symbols=scope)
    assert path.read_bytes() == before
