import sqlite3
import subprocess
import sys
import pytest
from live.scenario_outbox import enqueue, flush


@pytest.mark.parametrize('targets,expected', [
    ([], '🎯 고정 TP 없음'),
    ([dict(price=85200, quantity=.06)], '🎯 익절 TP 85,200.00 USDT'),
])
def test_protection_current_targets_reach_plain_text_sender_once(tmp_path, targets, expected):
    from copy import deepcopy
    from tests.test_scenario_notice import position
    event = dict(kind='PROTECTION', timestamp=1000, protection_confirmed=True,
                 position_before=position(timestamp=900, take_profits=targets),
                 position_after=position(hard_stop=84600, take_profits=targets))
    original = deepcopy(event)
    conn = sqlite3.connect(tmp_path/'targets.db')
    delivered = []
    try:
        enqueue(conn, 'protection-targets', event)
        conn.commit()
        def sender(body, kind):
            assert kind == 'PROTECTION'
            assert expected in body and '\n\n🎯' in body
            assert '84,500.00 → 84,600.00' in body
            assert '0.1 BTC' in body and '평단 84,750.00' in body
            assert len(body.encode('utf-16-le'))//2 < 4096
            delivered.append(body)
            return 123
        assert flush(conn, sender=sender) == dict(sent=1, unknown=0)
        assert flush(conn, sender=lambda *args: pytest.fail('duplicate notice')) == dict(sent=0, unknown=0)
        assert len(delivered) == 1
        assert conn.execute('SELECT body,status FROM llm_scenario_outbox').fetchone() == (delivered[0], 'SENT')
        assert event == original
    finally:
        conn.close()


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


def test_receipt_saved_while_other_process_holds_trading_lock(tmp_path):
    c=setup(tmp_path);calls=[];holder=None
    def send(body,kind):
        nonlocal holder
        # Acquire the real cross-process flock only after the claim committed.
        holder=subprocess.Popen(
            [sys.executable,'-c',
             'import fcntl,sys; f=open(sys.argv[1],"a"); '
             'fcntl.flock(f,fcntl.LOCK_EX); print("locked",flush=True); sys.stdin.read()',
             str(tmp_path/'outbox.db.btc-execution.lock')],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,text=True,
        )
        assert holder.stdout.readline().strip()=='locked'
        calls.append(kind)
        return 42
    try:
        assert flush(c,sender=send)==dict(sent=1,unknown=0)
        assert c.execute('SELECT status,message_id FROM llm_scenario_outbox').fetchone()==('SENT',42)
        assert calls==['HALTED']
    finally:
        if holder is not None:
            holder.communicate(timeout=10)
        c.close()


def test_concurrent_flush_during_send_never_resends_claim(tmp_path):
    c=setup(tmp_path);calls=[]
    def send(body,kind):
        other=sqlite3.connect(tmp_path/'outbox.db')
        try:
            assert flush(other,sender=lambda *a:pytest.fail('duplicate send'))==dict(sent=0,unknown=0)
        finally:
            other.close()
        calls.append(kind)
        return 42
    assert flush(c,sender=send)==dict(sent=1,unknown=0)
    assert calls==['HALTED']
    c.close()


def test_sender_error_stays_unknown_without_retry(tmp_path):
    c=setup(tmp_path)
    def send(*args):
        raise RuntimeError('receipt lost')
    assert flush(c,sender=send)==dict(sent=0,unknown=1)
    assert c.execute('SELECT status,message_id FROM llm_scenario_outbox').fetchone()==('UNKNOWN',None)
    assert flush(c,sender=lambda *a:pytest.fail('no retry'))==dict(sent=0,unknown=0)
    c.close()


def test_receipt_database_failure_preserves_unknown_claim_without_retry(tmp_path):
    c=setup(tmp_path);calls=[]
    c.execute("""CREATE TRIGGER reject_receipt BEFORE UPDATE ON llm_scenario_outbox
        WHEN NEW.status='SENT' BEGIN SELECT RAISE(ABORT,'receipt unavailable'); END""")
    c.commit()
    def send(*args):
        calls.append(1)
        return 42
    with pytest.raises(sqlite3.IntegrityError,match='receipt unavailable'):
        flush(c,sender=send)
    assert not c.in_transaction
    assert c.execute('SELECT status,message_id FROM llm_scenario_outbox').fetchone()==('SENDING',None)
    assert flush(c,sender=lambda *a:pytest.fail('no retry'))==dict(sent=0,unknown=0)
    assert calls==[1]
    c.close()


def test_receipt_does_not_overwrite_changed_claim(tmp_path):
    c=setup(tmp_path)
    def send(*args):
        other=sqlite3.connect(tmp_path/'outbox.db')
        other.execute("UPDATE llm_scenario_outbox SET status='UNKNOWN'")
        other.commit();other.close()
        return 42
    with pytest.raises(RuntimeError,match='notice_receipt_claim_lost'):
        flush(c,sender=send)
    assert c.execute('SELECT status,message_id FROM llm_scenario_outbox').fetchone()==('UNKNOWN',None)
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
