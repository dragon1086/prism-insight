"""약세·횡보장 top-down 억제 옵션(REGIME_WEAK_NO_TOPDOWN) 단위테스트.

기본 off = 현행 슬롯 유지. ON 시 sideways/moderate_bear의 top-down 슬롯 0(매수 절제),
강세장(bull)은 무영향.
"""
import os
import sys
import types

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Selection tests do not use KRX. Keep them runnable when the optional client is
# absent instead of silently skipping the hard-cap and SHADOW contracts. The
# production module loads .env at import time, so restore the test process
# environment afterward to avoid order-dependent failures in unrelated tests.
_environment_before_import = dict(os.environ)
try:
    try:
        import trigger_batch
    except ModuleNotFoundError as error:
        if error.name != "krx_data_client":
            raise
        stub = types.ModuleType("krx_data_client")

        def _network_unavailable(*_args, **_kwargs):
            raise RuntimeError("krx_data_client test stub")

        for name in (
            "_get_client",
            "get_market_ohlcv_by_ticker",
            "get_nearest_business_day_in_a_week",
            "get_market_cap_by_ticker",
            "get_market_ticker_name",
        ):
            setattr(stub, name, _network_unavailable)
        sys.modules["krx_data_client"] = stub
        import trigger_batch
finally:
    os.environ.clear()
    os.environ.update(_environment_before_import)


def _slots(regime, flag):
    previous = os.environ.get("REGIME_WEAK_NO_TOPDOWN")
    # Production loads .env while importing regime-policy helpers. Explicitly
    # pin OFF for the default-path assertion so a server's live true value
    # cannot leak back into this unit test during the function call.
    os.environ["REGIME_WEAK_NO_TOPDOWN"] = "false" if flag is None else flag
    try:
        return trigger_batch._get_regime_slots(regime)
    finally:
        if previous is None:
            os.environ.pop("REGIME_WEAK_NO_TOPDOWN", None)
        else:
            os.environ["REGIME_WEAK_NO_TOPDOWN"] = previous


def test_default_off_preserves_current():
    assert _slots("sideways", None) == (1, 2)
    assert _slots("moderate_bear", None) == (1, 2)
    assert _slots("moderate_bull", None) == (1, 2)


def test_on_suppresses_topdown_in_weak_regimes():
    assert _slots("sideways", "true") == (0, 2)
    assert _slots("moderate_bear", "true") == (0, 2)


def test_on_does_not_touch_bull_or_strong_bear():
    assert _slots("moderate_bull", "true") == (1, 2)
    assert _slots("strong_bull", "true") == (2, 1)
    assert _slots("strong_bear", "true") == (0, 3)  # 이미 top-down 0


def test_selection_plan_uses_slot_sum_as_hard_total(monkeypatch):
    monkeypatch.setattr(
        trigger_batch, "_get_regime_slots", lambda _regime: (1, 0)
    )

    assert trigger_batch._get_regime_selection_plan("moderate_bull") == (
        1,
        0,
        1,
    )


def test_final_selection_does_not_refill_past_regime_total(monkeypatch):
    monkeypatch.setattr(
        trigger_batch, "_get_regime_slots", lambda _regime: (0, 2)
    )
    triggers = {
        "Trigger A": trigger_batch.pd.DataFrame(
            {"CompositeScore": [3.0], "종목명": ["A"]}, index=["AAA"]
        ),
        "Trigger B": trigger_batch.pd.DataFrame(
            {"CompositeScore": [2.0], "종목명": ["B"]}, index=["BBB"]
        ),
        # Samwha Capacitor occupied this third per-trigger slot even though the
        # weak-regime plan allowed only two bottom-up selections.
        "Trigger C": trigger_batch.pd.DataFrame(
            {"CompositeScore": [1.0], "종목명": ["C"]}, index=["001820"]
        ),
    }

    result = trigger_batch.select_final_tickers(
        triggers,
        use_hybrid=False,
        macro_context={"market_regime": "moderate_bear", "leading_sectors": []},
    )

    selected = [ticker for frame in result.values() for ticker in frame.index]
    assert selected == ["AAA", "BBB"]
    assert "001820" not in selected


def test_no_macro_context_preserves_legacy_three_candidate_limit(monkeypatch):
    monkeypatch.setattr(
        trigger_batch, "_get_regime_slots", lambda _regime: (0, 1)
    )
    triggers = {
        name: trigger_batch.pd.DataFrame(
            {"CompositeScore": [score], "종목명": [name]}, index=[ticker]
        )
        for name, ticker, score in (
            ("Trigger A", "AAA", 3.0),
            ("Trigger B", "BBB", 2.0),
            ("Trigger C", "CCC", 1.0),
        )
    }

    result = trigger_batch.select_final_tickers(
        triggers,
        use_hybrid=False,
        macro_context=None,
    )

    assert sum(len(frame) for frame in result.values()) == 3


def test_third_slot_shadow_records_counterfactual_without_changing_selection(
    monkeypatch,
):
    monkeypatch.setenv("REGIME_WEAK_NO_TOPDOWN", "true")
    monkeypatch.setenv("REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED", "true")
    monkeypatch.setattr(
        trigger_batch, "_get_regime_slots", lambda _regime: (0, 2)
    )
    captured = []
    monkeypatch.setattr(
        "observability.third_slot_shadow.emit_evaluation",
        lambda **kwargs: captured.append(kwargs),
    )
    triggers = {
        name: trigger_batch.pd.DataFrame(
            {
                "CompositeScore": [score],
                "Close": [price],
                "종목명": [name],
            },
            index=[ticker],
        )
        for name, ticker, score, price in (
            ("Trigger A", "AAA", 3.0, 100.0),
            ("Trigger B", "BBB", 2.0, 200.0),
            ("Trigger C", "CCC", 1.0, 300.0),
        )
    }

    result = trigger_batch.select_final_tickers(
        triggers,
        trade_date="20260904",
        use_hybrid=False,
        macro_context={"market_regime": "moderate_bear", "leading_sectors": []},
        trigger_mode="morning",
    )

    selected = [ticker for frame in result.values() for ticker in frame.index]
    assert selected == ["AAA", "BBB"]
    assert len(captured) == 1
    assert [row["ticker"] for row in captured[0]["candidates"]] == [
        "AAA",
        "BBB",
        "CCC",
    ]
    assert [row["role"] for row in captured[0]["candidates"]] == [
        "LIVE_SELECTED",
        "LIVE_SELECTED",
        "SHADOW_THIRD",
    ]


def test_third_slot_shadow_disabled_does_not_emit(monkeypatch):
    monkeypatch.setenv("REGIME_WEAK_NO_TOPDOWN", "true")
    monkeypatch.delenv("REGIME_WEAK_THIRD_SLOT_SHADOW_ENABLED", raising=False)
    monkeypatch.setattr(
        trigger_batch, "_get_regime_slots", lambda _regime: (0, 2)
    )
    captured = []
    monkeypatch.setattr(
        "observability.third_slot_shadow.emit_evaluation",
        lambda **kwargs: captured.append(kwargs),
    )
    triggers = {
        name: trigger_batch.pd.DataFrame(
            {"CompositeScore": [score], "Close": [100.0]}, index=[ticker]
        )
        for name, ticker, score in (
            ("A", "AAA", 3.0),
            ("B", "BBB", 2.0),
            ("C", "CCC", 1.0),
        )
    }

    result = trigger_batch.select_final_tickers(
        triggers,
        trade_date="20260904",
        use_hybrid=False,
        macro_context={"market_regime": "sideways", "leading_sectors": []},
        trigger_mode="afternoon",
    )

    assert sum(len(frame) for frame in result.values()) == 2
    assert captured == []
