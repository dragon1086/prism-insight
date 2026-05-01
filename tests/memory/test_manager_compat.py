"""V1 method-signature parity (introspection-based)."""

import inspect

import pytest

from tracking.memory.manager import UserMemoryManager


REQUIRED_V1_METHODS = {
    "save_memory": {"user_id", "memory_type", "content", "ticker", "ticker_name",
                    "market_type", "importance_score", "command_source", "message_id", "tags"},
    "get_memories": {"user_id", "memory_type", "ticker", "limit", "include_compressed"},
    "save_journal": {"user_id", "text", "ticker", "ticker_name", "market_type", "message_id"},
    "get_journals": {"user_id", "ticker", "limit"},
    "build_llm_context": {"user_id", "ticker", "max_tokens", "user_message"},
    "compress_old_memories": {"layer1_days", "layer2_days"},
    "get_user_preferences": {"user_id"},
    "update_user_preferences": {"user_id", "preferred_tone", "investment_style", "favorite_tickers"},
    "get_memory_stats": {"user_id"},
    "delete_memory": {"memory_id", "user_id"},
}


@pytest.mark.parametrize("name,expected", list(REQUIRED_V1_METHODS.items()))
def test_v1_method_signature_preserved(name, expected):
    method = getattr(UserMemoryManager, name)
    sig = inspect.signature(method)
    params = set(sig.parameters.keys()) - {"self"}
    missing = expected - params
    assert not missing, f"V1 method {name} is missing params: {missing}"


def test_new_v2_methods_exist():
    for name in ("search_memories", "get_facts", "derive_profile"):
        assert hasattr(UserMemoryManager, name), f"missing V2 method {name}"


def test_extract_tickers_from_text_preserved():
    # Some V1 callers reach into this private API — keep it.
    assert hasattr(UserMemoryManager, "_extract_tickers_from_text")


def test_constants_preserved():
    assert UserMemoryManager.MEMORY_JOURNAL == "journal"
    assert UserMemoryManager.MEMORY_EVALUATION == "evaluation"
    assert UserMemoryManager.LAYER_DETAILED == 1
    assert UserMemoryManager.LAYER_SUMMARY == 2
    assert UserMemoryManager.LAYER_COMPRESSED == 3


def test_save_then_get_round_trip(tmp_db):
    m = UserMemoryManager(tmp_db)
    mid = m.save_memory(1, "journal", {"text": "hello world"}, ticker="005930",
                        ticker_name="삼성전자", tags=["t1", "t2"])
    assert mid > 0
    rows = m.get_memories(1)
    assert len(rows) == 1
    r = rows[0]
    assert r["content"]["text"] == "hello world"
    assert r["ticker"] == "005930"
    assert r["tags"] == ["t1", "t2"]


def test_save_journal_uses_journal_command_source(tmp_db):
    m = UserMemoryManager(tmp_db)
    mid = m.save_journal(1, "오늘 단타 후회", ticker="005930", ticker_name="삼성전자")
    assert mid > 0
    rows = m.get_journals(1)
    assert rows and rows[0]["command_source"] == "/journal"
    assert rows[0]["importance_score"] == 0.7


def test_build_llm_context_returns_str(tmp_db):
    m = UserMemoryManager(tmp_db)
    m.save_journal(1, "삼성 매수", ticker="005930", ticker_name="삼성전자")
    out = m.build_llm_context(1, ticker="005930")
    assert isinstance(out, str)


# ---------------------------------------------------------------------------
# Task 6: lazy Anthropic client — must be None at init when no key is set
# ---------------------------------------------------------------------------
def test_anthropic_client_is_none_at_init_without_key(tmp_db, monkeypatch):
    """Without ANTHROPIC_API_KEY, anthropic_client stays None after init."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = UserMemoryManager(tmp_db)
    assert m.anthropic_client is None, (
        "anthropic_client should be None at init when ANTHROPIC_API_KEY is absent"
    )


def test_anthropic_client_is_none_when_passed_none(tmp_db, monkeypatch):
    """Passing anthropic_client=None explicitly keeps it None (no eager init)."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = UserMemoryManager(tmp_db, anthropic_client=None)
    assert m.anthropic_client is None


def test_passed_client_is_preserved(tmp_db):
    """A non-None client passed in __init__ is kept as-is (no override)."""
    sentinel = object()
    m = UserMemoryManager(tmp_db, anthropic_client=sentinel)
    assert m.anthropic_client is sentinel


def test_ensure_anthropic_client_returns_none_without_key(tmp_db, monkeypatch):
    """_ensure_anthropic_client() returns None when no SDK/key present."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    m = UserMemoryManager(tmp_db)
    result = m._ensure_anthropic_client()
    assert result is None
