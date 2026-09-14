"""Single-symbol price columns must be Series, regardless of provider layout."""
import pandas as pd
import pytest

from prism_core.ohlcv_shape import normalize_single_ticker_ohlcv


@pytest.mark.parametrize("order", ["flat", "price_first", "ticker_first"])
def test_preserves_prices_and_index_without_mutation(order):
    expected = pd.DataFrame({"High": [102., 103.], "Close": [101., 102.]},
                            index=pd.date_range("2026-09-10", periods=2))
    source = expected.copy()
    if order != "flat":
        source.columns = pd.MultiIndex.from_tuples(
            [(column, "CRWD") if order == "price_first" else ("CRWD", column)
             for column in source.columns])
    original = source.copy()
    actual = normalize_single_ticker_ohlcv(source, "CRWD")
    pd.testing.assert_frame_equal(actual, expected)
    pd.testing.assert_frame_equal(source, original)
    assert actual["High"].tolist() == [102., 103.]


def test_selects_exact_requested_ticker_not_first_column():
    frame = pd.DataFrame([[999., 101.]], columns=pd.MultiIndex.from_tuples(
        [("High", "WRONG"), ("High", "CRWD")]))
    assert normalize_single_ticker_ohlcv(frame, "CRWD")["High"].tolist() == [101.]
    assert normalize_single_ticker_ohlcv(frame, "MISSING").empty


@pytest.mark.parametrize("columns", [
    ["High", "High"],
    pd.MultiIndex.from_tuples([("High", "CRWD"), ("High", "CRWD")]),
    pd.MultiIndex.from_tuples([("High", "CRWD", "extra"), ("Low", "CRWD", "extra")]),
])
def test_rejects_ambiguous_duplicate_or_extra_levels(columns):
    frame = pd.DataFrame([[101., 102.]], columns=columns)
    assert normalize_single_ticker_ohlcv(frame, "CRWD").empty


def test_none_input_is_missing():
    assert normalize_single_ticker_ohlcv(None, "CRWD").empty
