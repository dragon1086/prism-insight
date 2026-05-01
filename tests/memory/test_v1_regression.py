"""Regression guard: V1 invariants must still hold (per VERIFICATION_PLAN §3)."""

from tracking.memory.manager import UserMemoryManager
from tracking.user_memory import UserMemoryManager as ShimUMM


def test_shim_imports_to_v2_class():
    assert ShimUMM is UserMemoryManager


def test_save_memory_returns_positive_int(tmp_db):
    m = UserMemoryManager(tmp_db)
    mid = m.save_memory(1, "journal", {"text": "x"})
    assert isinstance(mid, int) and mid > 0


def test_get_memories_ordering_desc_by_created_at(tmp_db):
    m = UserMemoryManager(tmp_db)
    a = m.save_memory(1, "journal", {"text": "first"})
    b = m.save_memory(1, "journal", {"text": "second"})
    c = m.save_memory(1, "journal", {"text": "third"})
    rows = m.get_memories(1, limit=10)
    ids = [r["id"] for r in rows]
    # Most recent first.
    assert ids[0] == c
    assert ids[-1] == a


def test_get_user_preferences_returns_v1_keys(tmp_db):
    m = UserMemoryManager(tmp_db)
    m.update_user_preferences(1, preferred_tone="friendly", investment_style="value",
                              favorite_tickers=["005930"])
    prefs = m.get_user_preferences(1)
    assert prefs is not None
    expected = {
        "user_id", "preferred_tone", "investment_style", "favorite_tickers",
        "total_evaluations", "total_journals", "created_at", "last_active_at",
    }
    assert expected.issubset(set(prefs.keys()))


def test_compress_returns_dict_with_required_keys(tmp_db):
    m = UserMemoryManager(tmp_db)
    out = m.compress_old_memories()
    assert isinstance(out, dict)
    assert "layer2_count" in out and "layer3_count" in out


def test_build_llm_context_returns_str_never_raises(tmp_db):
    m = UserMemoryManager(tmp_db)
    out = m.build_llm_context(99999)  # nonexistent user
    assert isinstance(out, str)


def test_delete_memory_respects_ownership(tmp_db):
    m = UserMemoryManager(tmp_db)
    mid = m.save_memory(1, "journal", {"text": "secret"})
    # Wrong user can't delete.
    assert m.delete_memory(mid, user_id=2) is False
    # Right user can.
    assert m.delete_memory(mid, user_id=1) is True
