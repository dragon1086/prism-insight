"""
Memory V2 — Semantic Recall + Async Fact Extraction.

Public surface preserves V1 `UserMemoryManager` API and adds:
    - search_memories(user_id, query, k)
    - get_facts(user_id, category=None, k=10)
    - derive_profile(user_id)
"""

from tracking.memory.manager import UserMemoryManager  # noqa: F401

__all__ = ["UserMemoryManager"]
