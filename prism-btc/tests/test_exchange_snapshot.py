import pytest
from live.exchange_snapshot import read_complete


def response(rows, cursor=""):
    return {"retCode":0,"result":{"list":rows,"nextPageCursor":cursor}}


def test_real_flat_shape_with_repeated_terminal_position():
    row={"symbol":"BTCUSDT","positionIdx":0,"side":"","size":"0","stopLoss":"","avgPrice":"0"}
    calls=[]
    def call(method,**kw):
        calls.append(kw)
        return response([row] if not kw.get("cursor") else [{**row,"avgPrice":""}],
                        "next" if not kw.get("cursor") else "")
    result=read_complete(call,"get_positions",symbol="BTCUSDT")
    assert result["result"]["list"] == [{**row,"avgPrice":""}]
    assert len(calls) == 2
    assert calls[1]["cursor"] == "next"


def test_cycle_is_unknown():
    assert read_complete(lambda *a,**kw:response([],"same"),"get_positions") is None


def test_late_failure_cannot_be_mistaken_for_flat():
    def call(*a,**kw):
        return {"retCode":10001} if kw.get("cursor") else response([],"next")
    assert read_complete(call,"get_positions") is None


def test_conflicting_duplicate_position_is_unknown():
    def call(*a,**kw):
        return response([{"symbol":"BTCUSDT","size":"1" if kw.get("cursor") else "0"}],
                        "" if kw.get("cursor") else "next")
    assert read_complete(call,"get_positions") is None


def test_open_orders_merge_without_dropping_pages():
    def call(*a,**kw):
        return response([{"orderId":"b" if kw.get("cursor") else "a"}],
                        "" if kw.get("cursor") else "next")
    assert len(read_complete(call,"get_open_orders")["result"]["list"]) == 2


def test_conflicting_protection_order_type_is_unknown():
    def call(*a,**kw):
        return response([{"orderId":"same","orderType":"Limit" if kw.get("cursor") else "Market"}],
                        "" if kw.get("cursor") else "next")
    assert read_complete(call,"get_open_orders") is None


def test_conflicting_parent_or_entry_link_is_unknown():
    for field in ("orderLinkId", "parentOrderLinkId"):
        def call(*args, _field=field, **kwargs):
            row = {"orderId": "same", _field: "foreign" if kwargs.get("cursor") else "ours"}
            return response([row], "" if kwargs.get("cursor") else "next")
        assert read_complete(call, "get_open_orders") is None


def _active_and_placeholder():
    active = {"symbol": "BTCUSDT", "positionIdx": 0, "side": "Buy", "size": "0.089",
              "avgPrice": "78188.2", "stopLoss": "76569.1", "takeProfit": "",
              "createdTime": "1785875525065", "updatedTime": "1789430529487",
              "positionStatus": "Normal"}
    blank = {**active, "side": "", "size": "0", "avgPrice": "", "stopLoss": "",
             "createdTime": "", "updatedTime": "", "positionStatus": ""}
    return active, blank


def test_terminal_empty_placeholder_requires_fresh_identical_active_position():
    active, blank = _active_and_placeholder()
    calls = []
    def call(method, **kw):
        calls.append(kw)
        return response([blank]) if kw.get("cursor") else response([active], "next")
    result = read_complete(call, "get_positions", category="linear", symbol="BTCUSDT")
    assert result is not None
    assert result["result"]["list"] == [active]
    assert len(calls) == 3 and "cursor" not in calls[-1]


def test_placeholder_does_not_hide_changed_or_closed_position():
    active, blank = _active_and_placeholder()
    for change in ({"size": "0"}, {"size": "0.088"}, {"stopLoss": "76000"},
                   {"updatedTime": "1789430529488"}, {"symbol": "ETHUSDT"}):
        calls = []
        def call(method, _calls=calls, _change=change, **kw):
            _calls.append(kw)
            if kw.get("cursor"):
                return response([blank])
            return response([active if len(_calls) == 1 else {**active, **_change}], "next")
        assert read_complete(call, "get_positions", category="linear", symbol="BTCUSDT") is None


def test_real_terminal_flat_is_not_a_placeholder():
    active, blank = _active_and_placeholder()
    blank.update(createdTime=active["createdTime"], updatedTime="1789430529999",
                 positionStatus="Normal")
    def call(method, **kw):
        return response([blank]) if kw.get("cursor") else response([active], "next")
    assert read_complete(call, "get_positions", category="linear", symbol="BTCUSDT") is None


@pytest.mark.parametrize("case", ["missing_marker", "hedge", "foreign", "nonterminal",
                                  "multiple_rows", "refresh_error", "refresh_empty",
                                  "refresh_status", "refresh_side", "refresh_tp",
                                  "refresh_bool_index", "bad_timestamp"])
def test_placeholder_exception_stays_narrow(case):
    active, blank = _active_and_placeholder()
    if case == "missing_marker":
        blank.pop("updatedTime")
    if case == "hedge":
        active["positionIdx"] = blank["positionIdx"] = 1
    if case == "foreign":
        active["symbol"] = blank["symbol"] = "ETHUSDT"
    if case == "bad_timestamp":
        active["createdTime"] = "1.5"
    calls = []
    def call(method, **kw):
        calls.append(kw)
        if kw.get("cursor"):
            return response([blank, blank] if case == "multiple_rows" else [blank],
                            "next" if case == "nonterminal" else "")
        if len(calls) > 1:
            if case == "refresh_error":
                return {"retCode": 10001}
            if case == "refresh_empty":
                return response([])
            changes = {"refresh_status": {"positionStatus": "Liq"},
                       "refresh_side": {"side": "Sell"}, "refresh_tp": {"takeProfit": "80000"},
                       "refresh_bool_index": {"positionIdx": False}}
            return response([{**active, **changes.get(case, {})}], "next")
        return response([active], "next")
    assert read_complete(call, "get_positions", category="linear", symbol="BTCUSDT") is None
    assert len(calls) <= 3
