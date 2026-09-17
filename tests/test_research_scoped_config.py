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


def test_cli_preserves_and_explicitly_disables_market_context(tmp_path, monkeypatch):
    from tools.configure_report_research import main
    path = tmp_path / 'config.json'
    configure(path, False, market_context_enabled=True)
    monkeypatch.setattr('sys.argv', ['configure', '--enable', '--path', str(path), '--validated-symbol', 'US:MU'])
    main()
    assert json.loads(path.read_text())['market_context_enabled'] is True
    monkeypatch.setattr('sys.argv', ['configure', '--disable', '--path', str(path), '--disable-market-context'])
    main()
    result = json.loads(path.read_text())
    assert result['market_context_enabled'] is False
    assert result['validated_symbols'] == {'US': ['MU']}
