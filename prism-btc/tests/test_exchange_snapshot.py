from live.exchange_snapshot import read_complete


def response(rows, cursor=""):
    return {"retCode":0,"result":{"list":rows,"nextPageCursor":cursor}}


def test_real_flat_shape_with_repeated_terminal_position():
    row={"symbol":"BTCUSDT","positionIdx":0,"side":"","size":"0","stopLoss":""}
    calls=[]
    def call(method,**kw):
        calls.append(kw)
        return response([row], "next" if not kw.get("cursor") else "")
    result=read_complete(call,"get_positions",symbol="BTCUSDT")
    assert result["result"]["list"] == [row]
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
