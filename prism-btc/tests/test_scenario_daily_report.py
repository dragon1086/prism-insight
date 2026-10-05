import json
import sqlite3

import pytest

from live.scenario_daily_report import build_report
from live.telegram_reporter import build_message


NOW = 1800000000.


def fixture():
    conn = sqlite3.connect(':memory:')
    conn.executescript('''
    CREATE TABLE llm_scenario_control(id INTEGER,body TEXT);
    CREATE TABLE btc_meta(mode TEXT,key TEXT,value TEXT);
    CREATE TABLE llm_scenario_broker_evidence(kind TEXT,captured_at REAL,body TEXT);
    CREATE TABLE llm_scenario_settlements(scenario_id TEXT,evidence TEXT);
    CREATE TABLE llm_scenario_decisions(slot INTEGER,context TEXT,proposal TEXT,outcome TEXT);
    CREATE TABLE btc_trading_history(net_pnl REAL, r_multiple REAL);
    INSERT INTO btc_trading_history VALUES(-999,0),(-999,0);
    ''')
    conn.execute('INSERT INTO llm_scenario_control VALUES(1,?)', (json.dumps(dict(version=1,state='active',main_uid='123',activated_at=NOW-1000)),))
    conn.execute('INSERT INTO btc_meta VALUES(?,?,?)', ('demo','shared_entry_policy_v1', json.dumps(dict(main_uid='123',swing_uid='456'))))
    observation = dict(captured_at=NOW-10,equity=9647.71,exchange_flat=True,legacy_fenced=False,
        position=dict(symbol='BTCUSDT',positionIdx=0,size='0',side='',leverage='10'),open_orders=[],native_stops=[])
    conn.execute('INSERT INTO llm_scenario_broker_evidence VALUES(?,?,?)', ('account',NOW-10,json.dumps(observation)))
    for index in range(5):
        settlement = dict(scenario_id=f's{index}',execution_ids=[f'e{index}'],flat_confirmed=True,
            orders_terminal=True,executions_complete=True,fees_complete=True,funding_complete=True,
            gross_pnl=11,fees=1,funding_net=0,net_pnl=10)
        conn.execute('INSERT INTO llm_scenario_settlements VALUES(?,?)',(f's{index}',json.dumps(settlement)))
    conn.execute('INSERT INTO llm_scenario_decisions VALUES(?,?,?,?)', (int(NOW//300), '{}',
        json.dumps(dict(action='WAIT',rationale='추세 확인 대기')), json.dumps(dict(status='wait'))))
    conn.commit()
    return conn


def test_legacy_two_trades_never_pollute_five_settled_scenarios(monkeypatch):
    conn = fixture()
    monkeypatch.setattr('live.scenario_daily_report.time.time', lambda: NOW)
    text = build_message(conn, 'demo')
    assert '종료 5건' in text and '+50.00 USDT' in text
    assert '9,647.71 USDT' in text and 'KST' in text
    assert '-999' not in text and '70' not in text and 'exchangefill' not in text


@pytest.mark.parametrize('state', ['active','paused','transition'])
def test_control_states_route_scenario_without_old_fallback(monkeypatch,state):
    conn=fixture()
    conn.execute('UPDATE llm_scenario_control SET body=?',(json.dumps(dict(version=1,state=state,main_uid='123',activated_at=NOW-1000,started_at=NOW-1200)),))
    monkeypatch.setattr('live.scenario_daily_report.time.time',lambda:NOW)
    assert '시나리오' in build_message(conn,'demo')


@pytest.mark.parametrize('damage', ['json','binding','empty_control'])
def test_bad_control_or_other_account_never_leaks_account_or_old_report(damage):
    conn=fixture()
    if damage=='json':conn.execute("UPDATE llm_scenario_control SET body='invalid'")
    elif damage=='binding':conn.execute('UPDATE btc_meta SET value=?',(json.dumps(dict(main_uid='999',swing_uid='456')),))
    else:conn.execute('DELETE FROM llm_scenario_control')
    text=build_message(conn,'demo')
    assert '확인 필요' in text and '9,647' not in text and '50.00' not in text


@pytest.mark.parametrize('age', [181,-10,1100])
def test_stale_future_or_preactivation_account_is_unknown_not_flat(age):
    conn=fixture()
    raw=json.loads(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0])
    raw['captured_at']=NOW-age
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?,captured_at=?',(json.dumps(raw),NOW-age))
    text=build_report(conn,now=NOW)
    assert '계좌 자료 미확인' in text and '9,647' not in text and '보유 없음' not in text


@pytest.mark.parametrize('field,value', [('net_pnl',999),('fees_complete',False),('scenario_id','other'),('gross_pnl',float('nan'))])
def test_invalid_settlement_warns_and_does_not_count(field,value):
    conn=fixture()
    raw=json.loads(conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id='s0'").fetchone()[0])
    raw[field]=value
    conn.execute("UPDATE llm_scenario_settlements SET evidence=? WHERE scenario_id='s0'",(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '종료 4건' in text and '+40.00 USDT' in text and '일부 미확인' in text


def test_unfilled_cancellation_excluded_and_missing_ledger_is_not_zero():
    conn=fixture()
    conn.execute('DELETE FROM llm_scenario_settlements')
    raw=dict(scenario_id='empty',execution_ids=[],flat_confirmed=True,orders_terminal=True,
        executions_complete=True,fees_complete=True,funding_complete=True,no_fills_confirmed=True,
        gross_pnl=0,fees=0,funding_net=0,net_pnl=0)
    conn.execute('INSERT INTO llm_scenario_settlements VALUES(?,?)',('empty',json.dumps(raw)))
    assert '종료 0건' in build_report(conn,now=NOW)
    conn.execute('DROP TABLE llm_scenario_settlements')
    assert '정산 자료 미확인' in build_report(conn,now=NOW)


def test_rejected_proposal_never_claimed_executed_and_text_is_sanitized():
    conn=fixture()
    conn.execute('UPDATE llm_scenario_decisions SET proposal=?,outcome=?',
        (json.dumps(dict(action='OPEN',rationale='[매수](https://secret) *비밀*')),json.dumps(dict(status='blocked',reason='secret'))))
    text=build_report(conn,now=NOW)
    assert '판단 보류' in text and '매수' not in text and 'secret' not in text
    conn.execute('UPDATE llm_scenario_decisions SET outcome=?',(json.dumps(dict(status='wait')),))
    text=build_report(conn,now=NOW)
    assert 'https' not in text and '*' not in text and '[' not in text


def test_readonly_render_performs_no_writes_and_old_decision_is_labeled():
    conn=fixture()
    conn.execute('UPDATE llm_scenario_decisions SET slot=?',(int((NOW-1200)//300),))
    conn.commit()
    conn.execute('PRAGMA query_only=ON')
    before=conn.total_changes
    text=build_report(conn,now=NOW)
    assert conn.total_changes==before and '지난 판단' in text


@pytest.mark.parametrize('side', ['Buy','Sell'])
def test_live_position_displays_verified_sl_and_live_tp_only(side):
    conn=fixture()
    stop,tp=(85600,86500) if side=='Buy' else (86500,85600)
    exit_side='Sell' if side=='Buy' else 'Buy'
    raw=json.loads(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0])
    raw.update(exchange_flat=False,position=dict(symbol='BTCUSDT',positionIdx=0,size='.004',side=side,
        leverage='10',avgPrice='86000',stopLoss=str(stop)),open_orders=[
        dict(orderId='sl',symbol='BTCUSDT',positionIdx=0,side=exit_side,reduceOnly=True,stopOrderType='StopLoss',
             orderType='Market',orderStatus='Untriggered',triggerBy='MarkPrice',triggerPrice=str(stop),qty='.004',cumExecQty='0',leavesQty='.004'),
        dict(orderId='tp',symbol='BTCUSDT',positionIdx=0,side=exit_side,reduceOnly=True,orderType='Limit',
             orderStatus='New',price=str(tp),leavesQty='.002')])
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?',(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '0.004 BTC' in text and '🛡 SL' in text and '🎯 TP' in text
    raw['open_orders'].pop()
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?',(json.dumps(raw),))
    assert '고정 TP 없음' in build_report(conn,now=NOW)


@pytest.mark.parametrize('change', [dict(orderType='Limit'),dict(orderId=''),dict(cumExecQty='.002'),dict(leavesQty='.002')])
def test_inexact_native_stop_never_claims_full_protection(change):
    conn=fixture()
    raw=json.loads(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0])
    order=dict(orderId='sl',symbol='BTCUSDT',positionIdx=0,side='Sell',reduceOnly=True,
        stopOrderType='StopLoss',orderType='Market',orderStatus='Untriggered',triggerBy='MarkPrice',
        triggerPrice='85600',qty='.004',cumExecQty='0',leavesQty='.004')
    order.update(change)
    raw.update(exchange_flat=False,position=dict(symbol='BTCUSDT',positionIdx=0,size='.004',side='Buy',
        leverage='10',avgPrice='85000',stopLoss='85600'),open_orders=[order])
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?',(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '확인 필요' in text and '남은 전량' not in text


@pytest.mark.parametrize('change', [dict(reduceOnly=0),dict(symbol='ETHUSDT'),dict(positionIdx=1),
    dict(orderId=''),dict(side='unknown'),dict(orderStatus='Cancelled')])
def test_unknown_pending_orders_are_not_counted_as_valid(change):
    conn=fixture()
    raw=json.loads(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0])
    order=dict(orderId='pending',symbol='BTCUSDT',positionIdx=0,side='Buy',reduceOnly=False,orderStatus='New')
    order.update(change)
    raw['open_orders']=[order]
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?',(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '진입 대기·보호주문 확인 필요' in text and '진입 대기 1건' not in text


def test_flat_but_fenced_account_is_not_reported_as_clean_zero_pending():
    conn=fixture()
    raw=json.loads(conn.execute('SELECT body FROM llm_scenario_broker_evidence').fetchone()[0])
    raw['legacy_fenced']=True
    conn.execute('UPDATE llm_scenario_broker_evidence SET body=?',(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '보유 없음' in text and '확인 필요' in text and '진입 대기 0건' not in text


def test_zero_pnl_is_valid_and_duplicate_execution_is_flagged():
    conn=fixture()
    raw=json.loads(conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id='s0'").fetchone()[0])
    raw.update(gross_pnl=1,fees=1,net_pnl=0)
    conn.execute("UPDATE llm_scenario_settlements SET evidence=? WHERE scenario_id='s0'",(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '종료 5건' in text and '+40.00 USDT' in text
    raw['execution_ids']=['e4']
    conn.execute("UPDATE llm_scenario_settlements SET evidence=? WHERE scenario_id='s0'",(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '일부 미확인' in text and '종료 3건' in text and '+30.00 USDT' in text


def test_filled_and_no_fills_flags_contradiction_is_excluded():
    conn=fixture()
    raw=json.loads(conn.execute("SELECT evidence FROM llm_scenario_settlements WHERE scenario_id='s0'").fetchone()[0])
    raw['no_fills_confirmed']=True
    conn.execute("UPDATE llm_scenario_settlements SET evidence=? WHERE scenario_id='s0'",(json.dumps(raw),))
    text=build_report(conn,now=NOW)
    assert '종료 4건' in text and '일부 미확인' in text


def test_intent_pending_is_historical_not_current_fill_assertion():
    conn=fixture()
    conn.execute('UPDATE llm_scenario_decisions SET outcome=?',(json.dumps(dict(status='intent_pending')),))
    assert '당시 주문 확인 대기' in build_report(conn,now=NOW)


@pytest.mark.parametrize('mode', ['demo','shadow','live'])
def test_legacy_routing_preserved_without_control_or_for_other_modes(mode,monkeypatch):
    from live import tracking
    conn=sqlite3.connect(':memory:')
    conn.row_factory=sqlite3.Row
    tracking.ensure_schema(conn)
    if mode!='demo':
        conn.execute('CREATE TABLE llm_scenario_control(id INTEGER,body TEXT)')
    def forbidden(*args,**kwargs):
        raise AssertionError('scenario renderer must not run')
    monkeypatch.setattr('live.scenario_daily_report.build_report',forbidden)
    assert '관망 중' in build_message(conn,mode)


def test_preview_real_main_is_readonly_and_never_sends(tmp_path,monkeypatch,capsys):
    from live import telegram_reporter as reporter,tracking
    path=tmp_path/'report.sqlite'
    source=fixture()
    with sqlite3.connect(path) as disk:
        source.backup(disk)
    original=path.read_bytes()
    monkeypatch.setattr('sys.argv',['telegram_reporter','--mode','demo','--root-db',str(path),'--preview'])
    monkeypatch.setattr('live.scenario_daily_report.time.time',lambda:NOW)
    monkeypatch.setattr(reporter,'_load_env',lambda:None)
    def forbidden(*args,**kwargs):
        raise AssertionError('no mutation or send')
    monkeypatch.setattr(tracking,'get_connection',forbidden)
    monkeypatch.setattr(tracking,'ensure_schema',forbidden)
    monkeypatch.setattr(reporter,'_send',forbidden)
    actual_build=reporter.build_message
    def check_readonly(conn,mode):
        assert conn.execute('PRAGMA query_only').fetchone()[0]==1
        assert conn.in_transaction
        with pytest.raises(sqlite3.OperationalError):
            conn.execute('CREATE TABLE forbidden(value TEXT)')
        return actual_build(conn,mode)
    monkeypatch.setattr(reporter,'build_message',check_readonly)
    assert reporter.main()==0
    assert '종료 5건' in capsys.readouterr().out
    assert path.read_bytes()==original


def test_missing_database_preview_does_not_create_empty_database(tmp_path,monkeypatch,capsys):
    from live import telegram_reporter as reporter
    path=tmp_path/'missing.sqlite'
    monkeypatch.setattr('sys.argv',['telegram_reporter','--root-db',str(path),'--preview'])
    monkeypatch.setattr(reporter,'_load_env',lambda:None)
    assert reporter.main()==0
    assert not path.exists() and '자료 확인 필요' in capsys.readouterr().out


def test_legacy_preview_never_creates_missing_market_database(tmp_path,monkeypatch,capsys):
    from live import telegram_reporter as reporter,tracking
    from collector import store
    root=tmp_path/'legacy.sqlite'
    with sqlite3.connect(root) as conn:
        tracking.ensure_schema(conn)
    market=tmp_path/'missing_parent'/'market.sqlite'
    monkeypatch.setattr(store,'_get_db_path',lambda:market)
    monkeypatch.setattr('sys.argv',['telegram_reporter','--root-db',str(root),'--preview'])
    monkeypatch.setattr(reporter,'_load_env',lambda:None)
    def forbidden(*args,**kwargs):
        raise AssertionError('no mutable market connection or sending')
    monkeypatch.setattr(store,'get_connection',forbidden)
    monkeypatch.setattr(reporter,'_send',forbidden)
    assert reporter.main()==0
    assert '관망 중' in capsys.readouterr().out
    assert not market.exists() and not market.parent.exists()


def test_long_reason_explicitly_marks_a_bounded_excerpt():
    from live.scenario_daily_report import _safe_reason
    text = '현재 근거를 다시 확인합니다. ' * 20
    result = _safe_reason(text)
    assert len(result) <= 100 and result.endswith('…')
    assert _safe_reason('짧은 근거입니다.') == '짧은 근거입니다.'
