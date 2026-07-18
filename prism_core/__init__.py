"""prism_core — shared, dependency-free pure functions for the PRISM trading core.

Phase 1 of issue #412 (order-execution architecture refactor). This package holds
logic-preserving pure functions extracted from the KR/US tracking agents so both
forks can share one tested implementation. No I/O, no live clock, no DB, no self.
"""
