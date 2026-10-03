import sqlite3
import pytest
from live.scenario_outbox import enqueue, flush


def setup(tmp_path):
    conn=sqlite3.connect(tmp_path/'outbox.db')
    enqueue(conn,'e1',dict(kind='HALTED',timestamp=1000,reason_code='THREE_LOSSES'))
    conn.commit()
    return conn


def test_exact_ack_once_and_no_lock_during_send(tmp_path):
    c=setup(tmp_path);calls=[]
    def send(body,kind):
        other=sqlite3.connect(tmp_path/'outbox.db',timeout=.1)
        other.execute("BEGIN IMMEDIATE");other.rollback();other.close()
        calls.append(kind)
        return 42
    assert flush(c,sender=send)==dict(sent=1,unknown=0)
    assert flush(c,sender=send)==dict(sent=0,unknown=0)
    assert calls==['HALTED']
    c.close()


def test_unknown_and_crash_never_retried(tmp_path):
    c=setup(tmp_path)
    assert flush(c,sender=lambda *a:None)['unknown']==1
    assert flush(c,sender=lambda *a:pytest.fail('no retry'))['sent']==0
    c.execute("UPDATE llm_scenario_outbox SET status='SENDING'");c.commit()
    assert flush(c,sender=lambda *a:pytest.fail('no retry'))['sent']==0
    c.close()


def test_conflicting_identity_does_not_replace_notice(tmp_path):
    c=setup(tmp_path)
    with pytest.raises(ValueError,match='conflicting_notice_identity'):
        enqueue(c,'e1',dict(kind='HALTED',timestamp=2000))
    c.close()


@pytest.mark.parametrize('kind', ['MODEL_ERROR','MODEL_RECOVERED','PENDING','RESOLVED'])
def test_incident_notices_are_private_without_public_fallback(monkeypatch, kind):
    from live import ops_alerts
    from live.scenario_outbox import _destination
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN','public-token')
    monkeypatch.setenv('TELEGRAM_CHANNEL_ID','public-channel')
    monkeypatch.setattr(ops_alerts,'_resolve_ops_destination',lambda:('private-token','private-chat'))
    assert _destination(kind)==('private-token','private-chat')
    monkeypatch.setattr(ops_alerts,'_resolve_ops_destination',lambda:(None,None))
    assert _destination(kind)==(None,None)
