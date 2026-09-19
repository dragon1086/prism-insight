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


@pytest.mark.parametrize('enabled', [True, False])
def test_all_symbols_removes_scope_without_changing_enabled_or_market_context(tmp_path, enabled):
    path = tmp_path / 'config.json'
    configure(path, False, market_context_enabled=True, validated_symbols={'US': ['MU']})
    result = configure(path, enabled, all_symbols=True)
    assert 'validated_symbols' not in result
    assert result['enabled'] is enabled
    assert result['market_context_enabled'] is True
    assert json.loads(path.read_text()) == result
    assert 'validated_symbols' not in configure(path, enabled)


@pytest.mark.parametrize('scope', [{}, {'US': ['MU']}])
def test_all_symbols_conflicting_scope_does_not_modify_config(tmp_path, scope):
    path = tmp_path / 'config.json'
    configure(path, False, validated_symbols={'KR': ['005930']})
    before = path.read_bytes()
    with pytest.raises(ValueError, match='mutually exclusive'):
        configure(path, True, validated_symbols=scope, all_symbols=True)
    assert path.read_bytes() == before


@pytest.mark.parametrize('all_symbols', ['false', 1, None])
def test_all_symbols_requires_boolean(tmp_path, all_symbols):
    path = tmp_path / 'config.json'
    with pytest.raises(TypeError, match='boolean'):
        configure(path, True, all_symbols=all_symbols)
    assert not path.exists()


def test_empty_scope_remains_explicit_deny_all_on_omission(tmp_path):
    path = tmp_path / 'config.json'
    configure(path, True, validated_symbols={})
    result = configure(path, True)
    assert result['validated_symbols'] == {}
    assert json.loads(path.read_text()) == result
    assert 'validated_symbols' not in configure(path, True, all_symbols=True)


def test_cli_all_symbols_readback_and_scope_restoration(tmp_path, monkeypatch, capsys):
    from tools.configure_report_research import main
    path = tmp_path / 'config.json'
    configure(path, False, market_context_enabled=True, validated_symbols={'US': ['MU']})
    monkeypatch.setattr('sys.argv', ['configure', '--enable', '--path', str(path), '--all-symbols'])
    main()
    result = json.loads(path.read_text())
    assert json.loads(capsys.readouterr().out) == result
    assert 'validated_symbols' not in result
    assert result['enabled'] is True
    assert result['market_context_enabled'] is True
    monkeypatch.setattr('sys.argv', ['configure', '--disable', '--path', str(path),
                                    '--validated-symbol', 'US:MU', '--validated-symbol', 'KR:005930'])
    main()
    result = json.loads(path.read_text())
    assert json.loads(capsys.readouterr().out) == result
    assert result['validated_symbols'] == {'US': ['MU'], 'KR': ['005930']}
    assert result['enabled'] is False
    assert result['market_context_enabled'] is True


def test_cli_conflicting_scope_options_do_not_modify_config(tmp_path, monkeypatch):
    from tools.configure_report_research import main
    path = tmp_path / 'config.json'
    configure(path, False, validated_symbols={})
    before = path.read_bytes()
    monkeypatch.setattr('sys.argv', ['configure', '--enable', '--path', str(path),
                                    '--all-symbols', '--validated-symbol', 'US:MU'])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert path.read_bytes() == before
