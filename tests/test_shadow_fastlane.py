from datetime import datetime, timedelta, timezone

import pytest

from cores.oneil_fallback import SellInputs, evaluate_tier1_hardstop
from prism_core.shadow_fastlane import (
    Bar, Holding, Quote, Session, observe, source_cadence_matches,
)

NOW = datetime(2026, 9, 10, 1, 6, tzinfo=timezone.utc)
SESSION = Session(NOW.replace(hour=0, minute=0), NOW.replace(hour=6, minute=30), "fixture-calendar")
H = Holding("one", "005930", 100, 98, NOW - timedelta(hours=1))


def run(price=97, bar=None, quote=True, session=SESSION):
    quotes = {H.symbol: Quote(price, NOW, NOW, "source-1")} if quote else {}
    return observe((H,), quotes, {H.symbol: bar} if bar else {}, at=NOW,
                   market="KR", session=session, max_quote_age_seconds=30)[0]


def bar(close=97, **kw):
    return Bar(kw.get("start", NOW.replace(minute=0)),
               kw.get("end", NOW.replace(minute=5)), close,
               kw.get("available_at", NOW), "bar-source-1")


def test_baseline_reuses_exact_production_predicate():
    for price in [90, 93, 93.00001, 97, 97.51, 98, 110]:
        result = run(price, bar(price))
        expected = evaluate_tier1_hardstop(SellInputs(100, price, 98))[0]
        assert result.baseline == ("EXIT" if expected else "HOLD")


def test_ordinary_requires_completed_confirmation_not_wick():
    assert run(bar=bar()).candidate == "EXIT"
    assert run(bar=bar(99)).candidate == "WAIT"
    assert run().candidate == "UNKNOWN"
    assert run(price=99, bar=bar()).candidate == "HOLD"


@pytest.mark.parametrize("bad_bar", [bar(end=NOW + timedelta(minutes=4)),
    bar(available_at=NOW + timedelta(seconds=1)),
    bar(start=NOW.replace(minute=0)-timedelta(minutes=5), end=NOW.replace(minute=0)),
    bar(close=float("nan"))])
def test_missing_future_stale_invalid_bar_never_confirms(bad_bar):
    assert run(bar=bad_bar).candidate == "UNKNOWN"


def test_catastrophic_never_waits_for_bar_even_when_scenario_reason_wins():
    result = run(90)
    assert result.baseline == result.candidate == "EXIT"
    assert result.catastrophic is True
    assert result.quote_source_id == "source-1"
    assert result.at == NOW


def test_missing_quote_and_session_are_explicit_unknown():
    assert run(quote=False).baseline == "UNKNOWN"
    assert run(session=None).candidate == "UNKNOWN"
    assert run(session=None).baseline == "EXIT"
    assert run(90, session=None).candidate == "EXIT"


@pytest.mark.parametrize("quote", [
    Quote(float("inf"), NOW, NOW, "q"),
    Quote(97, NOW - timedelta(seconds=31), NOW, "q"),
    Quote(97, NOW, NOW + timedelta(seconds=1), "q"),
    Quote(97, NOW.replace(tzinfo=None), NOW, "q"),
])
def test_bad_quote_is_unknown_in_both_arms(quote):
    result = observe((H,), {H.symbol: quote}, {}, at=NOW, market="KR",
                     session=SESSION, max_quote_age_seconds=30)[0]
    assert result.baseline == result.candidate == "UNKNOWN"


def test_pre_entry_bar_and_closed_session_do_not_delay_catastrophic():
    closed = Session(NOW-timedelta(hours=2), NOW-timedelta(hours=1), "early-close")
    assert run(90, session=closed).candidate == "EXIT"
    assert run(session=closed).candidate == "UNKNOWN"
    holding = Holding("new", H.symbol, 100, 98, NOW-timedelta(minutes=2))
    result = observe((holding,), {H.symbol: Quote(97, NOW, NOW, "q")},
                     {H.symbol: bar()}, at=NOW, market="KR", session=SESSION,
                     max_quote_age_seconds=30)[0]
    assert result.baseline == "EXIT" and result.candidate == "UNKNOWN"


def test_pure_module_has_no_runtime_action_capability():
    import ast
    import inspect
    import prism_core.shadow_fastlane as module
    tree = ast.parse(inspect.getsource(module))
    imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
    assert imports == {"__future__", "dataclasses", "datetime", "typing", "zoneinfo",
                       "cores.oneil_fallback"}
    assert module.ENABLED is False
    assert not any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                   and node.func.id in {"open", "exec", "eval", "__import__"}
                   for node in ast.walk(tree))


def test_all_holdings_covered_with_no_llm_budget_or_broker_arguments():
    holdings = (H, Holding("two", "other", 100, 98, H.entered_at))
    results = observe(holdings, {}, {}, at=NOW, market="KR", session=SESSION,
                      max_quote_age_seconds=30)
    assert [r.holding_id for r in results] == ["one", "two"]
    assert all(r.baseline == r.candidate == "UNKNOWN" for r in results)


@pytest.mark.parametrize("market,stamp,match", [
    ("KR", "2026-09-10T09:00:00+09:00", True),
    ("KR", "2026-09-10T15:56:00+09:00", True),
    ("KR", "2026-09-10T09:04:00+09:00", False),
    ("US", "2026-09-10T09:04:00-04:00", True),
    ("US", "2026-12-10T16:58:00-05:00", True),
    ("US", "2026-09-12T10:04:00-04:00", False),
])
def test_source_cadence_not_exchange_calendar(market, stamp, match):
    assert source_cadence_matches(market, datetime.fromisoformat(stamp)) is match


def test_duplicate_holding_identity_rejected():
    with pytest.raises(ValueError):
        observe((H, H), {}, {}, at=NOW, market="KR", session=SESSION,
                max_quote_age_seconds=30)
