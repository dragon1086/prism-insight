"""TODO bench: deferred — see VERIFICATION_PLAN §1 Tier D.

Latency assertions are non-deterministic in CI (cache state, IO jitter, GIL).
Run manually:

    python -m timeit -n 100 -s "from tests.memory.bench_latency import _build_ctx" "_build_ctx()"
"""

# TODO bench
