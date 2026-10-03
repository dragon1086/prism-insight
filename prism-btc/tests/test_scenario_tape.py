import pytest
from backtest.scenario_tape import DecisionTape,TapeMismatch


def test_identity_rebinding_never_changes_economics(tmp_path):
    path=tmp_path/'tape.jsonl';t=DecisionTape(path,'record',{'data':'x'})
    t.append({'price':100},{'now':1,'input_id':'first','qty':2},{'input_id':'first','price':100},12)
    proof=t.transcript_hash();t.close()
    r=DecisionTape(path,'frozen',{'data':'x'})
    p=r.lookup({'price':100},{'now':1,'input_id':'second','qty':2})
    assert p['proposal']=={'input_id':'second','price':100}
    assert r.transcript_hash()==proof
    r.assert_exhausted()


def test_missing_input_or_contract_mismatch_is_not_wait(tmp_path):
    path=tmp_path/'t';t=DecisionTape(path,'record',{})
    t.append({},dict(now=1,input_id='x',qty=2),{},1);t.close()
    with pytest.raises(TapeMismatch):DecisionTape(path,'frozen',{'changed':True})
    r=DecisionTape(path,'frozen',{})
    with pytest.raises(TapeMismatch):r.lookup({},dict(now=1,input_id='y',qty=3))
    with pytest.raises(TapeMismatch):r.assert_exhausted()
    with pytest.raises(FileExistsError):DecisionTape(path,'record',{})
