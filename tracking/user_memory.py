"""
Legacy import path shim. The real implementation lives in `tracking.memory`.

Kept for backward compatibility with existing call sites:
    from tracking.user_memory import UserMemoryManager
"""

from tracking.memory.manager import UserMemoryManager  # noqa: F401

__all__ = ["UserMemoryManager"]
