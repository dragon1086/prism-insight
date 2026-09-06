import pytest

from live.native_stop import native_stop_params


@pytest.mark.parametrize("side,entry,stop,expected", [
    ("long",100.,95.01,"95.1"), ("short",100.,104.99,"104.9")])
def test_entry_attached_stop_never_rounds_looser(side,entry,stop,expected):
    p=native_stop_params(side,entry,stop)
    assert p == {"stopLoss":expected,"slTriggerBy":"LastPrice","slOrderType":"Market","tpslMode":"Full"}


def test_partial_does_not_request_whole_position_sl():
    assert native_stop_params("long",100,95,"Partial")["tpslMode"] == "Partial"


@pytest.mark.parametrize("side,entry,stop", [
    ("long",100,100), ("long",100,99.99), ("short",100,100.01),
    ("short",100,99), ("long",0,10), ("long",100,float("nan")),
    ("long",100,float("inf")), ("bad",100,95)])
def test_bad_or_unprotective_inputs_cannot_emit_entry_parameters(side,entry,stop):
    with pytest.raises(ValueError):
        native_stop_params(side,entry,stop)


def test_stop_equal_to_wire_rounded_entry_is_rejected():
    with pytest.raises(ValueError):
        native_stop_params("short",99.99,100.0)


def test_boolean_is_not_a_price():
    with pytest.raises(ValueError):
        native_stop_params("short",True,100.0)
