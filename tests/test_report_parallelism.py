from cores.analysis import _report_parallel_limit


def test_parallel_report_limit_defaults_to_all_sections():
    assert _report_parallel_limit(6, {}) == 6


def test_parallel_report_limit_honors_configured_bound():
    environ = {"PRISM_PARALLEL_REPORT_MAX_CONCURRENCY": "3"}

    assert _report_parallel_limit(6, environ) == 3


def test_parallel_report_limit_is_clamped_to_safe_range():
    assert _report_parallel_limit(6, {"PRISM_PARALLEL_REPORT_MAX_CONCURRENCY": "0"}) == 1
    assert _report_parallel_limit(6, {"PRISM_PARALLEL_REPORT_MAX_CONCURRENCY": "20"}) == 6


def test_invalid_parallel_report_limit_preserves_legacy_behavior():
    environ = {"PRISM_PARALLEL_REPORT_MAX_CONCURRENCY": "invalid"}

    assert _report_parallel_limit(6, environ) == 6
