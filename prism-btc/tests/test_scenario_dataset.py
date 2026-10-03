import pytest
from analysis.scenario_dataset import candles,digest


def test_bounded_pagination_no_future_rows():
    calls=[]
    def get(path,params):
        calls.append(params)
        return [["60000","100","102","99","101","4"],["0","99","101","98","100","3"]]
    result=candles('1',0,120000,get=get)
    assert [r['open_time'] for r in result]==[0,60000]
    assert calls[0]['end']==119999


def test_unrequested_future_candle_rejected():
    with pytest.raises(ValueError,match='outside_request'):
        candles('1',0,60000,get=lambda *a:[["60000","1","1","1","1","1"]])


def test_hash_is_order_independent_not_value_independent():
    assert digest({'a':1,'b':2})==digest({'b':2,'a':1})
    assert digest({'a':1})!=digest({'a':2})
