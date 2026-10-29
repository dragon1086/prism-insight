import json
import io
from urllib.error import HTTPError

import pytest

from live.scenario_oauth import generate_scenario
from live.scenario_recovery import record_model_transport_attempt as real_transport_journal


@pytest.fixture(autouse=True)
def transport_journal(monkeypatch):
    from live import scenario_recovery
    records = []
    def record(**body):
        records.append(body)
        return True
    monkeypatch.setattr(scenario_recovery, 'record_model_transport_attempt', record)
    return records

INCIDENT = ("Upstream connection error: Response payload is not completed: "
            "<TransferEncodingError: 400, message='Not enough data for satisfy transfer length header.'>")


def http_error(status=502, message=INCIDENT, error_type="server_error"):
    return HTTPError('http://127.0.0.1:18741/v1/responses', status, 'failure', {},
                     io.BytesIO(json.dumps({'error': {'type': error_type, 'message': message}}).encode()))


def test_verified_truncation_retries_identical_payload_once(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls = []
    def open_url(req, timeout):
        calls.append((req.data, timeout))
        if len(calls) == 1:
            raise http_error()
        return Response(result())
    assert call(open_url).text == '{}'
    assert len(calls) == 2 and calls[0][0] == calls[1][0]
    assert 0 < calls[1][1] <= calls[0][1] <= 75


@pytest.mark.parametrize('status,message,error_type', [
    (401, INCIDENT, 'server_error'), (429, INCIDENT, 'server_error'),
    (502, 'generic failure', 'server_error'), (502, INCIDENT, 'terminal_error'),
    (502, INCIDENT + ' terminal error', 'server_error'), (503, INCIDENT, 'server_error')])
def test_other_http_errors_never_retry(monkeypatch, status, message, error_type):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls = []
    def open_url(*args, **kw):
        calls.append(1)
        raise http_error(status, message, error_type)
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(calls) == 1


def test_verified_truncation_exhausted_two_attempts(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls = []
    def open_url(*args, **kw):
        calls.append(1)
        raise http_error()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(calls) == 2


def test_default_research_client_never_retries(transport_journal):
    calls = []
    def open_url(*args, **kw):
        calls.append(1)
        raise http_error()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        generate_scenario(system_prompt='test', user_prompt='test', model='gpt-6-luna',
            reasoning_effort='high', fast_tier=True, timeout=75, open_url=open_url,
            response_schema={'type': 'object'})
    assert len(calls) == 1
    assert transport_journal == []


def test_failed_journal_denies_retry_but_not_success(monkeypatch):
    from live import scenario_recovery
    monkeypatch.setattr(scenario_recovery, 'record_model_transport_attempt', lambda **kw: False)
    calls = []
    def open_url(*args, **kw):
        calls.append(1)
        raise http_error()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(calls) == 1
    assert call(lambda *a, **kw: Response(result())).text == '{}'


@pytest.mark.parametrize('variant', ['url', 'oversized', 'invalid_json', 'extra_terminal',
                                   'duplicate_root', 'duplicate_type', 'timeout'])
def test_unverified_transport_never_retries(variant, transport_journal):
    error = http_error()
    raw = None
    if variant == 'url':
        error.url = 'http://example.com/v1/responses'
    elif variant == 'oversized':
        raw = b' ' * 4097
    elif variant == 'invalid_json':
        raw = b'data: incomplete'
    elif variant == 'extra_terminal':
        raw = json.dumps({'error': {'type': 'server_error',
            'message': INCIDENT, 'code': 'terminal_error'}}).encode()
    elif variant == 'duplicate_root':
        raw = ('{"error":{"type":"terminal_error"},"error":' +
               json.dumps({'type': 'server_error', 'message': INCIDENT}) + '}').encode()
    elif variant == 'duplicate_type':
        raw = ('{"error":{"type":"terminal_error","type":"server_error","message":' +
               json.dumps(INCIDENT) + '}}').encode()
    else:
        error = TimeoutError('not retryable')
    if raw is not None:
        error = HTTPError('http://127.0.0.1:18741/v1/responses', 502, 'failure', {}, io.BytesIO(raw))
    def open_url(*args, **kw):
        raise error
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(transport_journal) == 1
    assert transport_journal[0]['retry'] is False


def test_real_journal_absence_or_closed_connection_never_throws():
    import sqlite3
    from live.scenario_recovery import capture_wire
    values = dict(attempt=1, status=502, reason='upstream_transfer_truncated',
                  elapsed_seconds=1, retry=True)
    assert real_transport_journal(**values) is False
    conn = sqlite3.connect(':memory:')
    conn.close()
    with capture_wire(conn, 123, lambda: 1000):
        assert real_transport_journal(**values) is False


def test_journal_delay_cannot_extend_retry_window(monkeypatch):
    from live import scenario_oauth, scenario_recovery
    now = [0.]
    monkeypatch.setattr(scenario_oauth.time, 'monotonic', lambda: now[0])
    def record(**body):
        now[0] = 72.
        return True
    monkeypatch.setattr(scenario_recovery, 'record_model_transport_attempt', record)
    calls = []
    def open_url(*args, **kwargs):
        calls.append(1)
        now[0] = 10.
        raise http_error()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(calls) == 1


def test_semantic_and_sse_failures_do_not_retry(transport_journal):
    cases = [result([{'type': 'function_call', 'name': 'place_order'}]),
             result([{'type': 'message', 'role': 'assistant', 'content': [{'type': 'refusal'}]}]),
             {**result(), 'status': 'incomplete'}, {**result(), 'model': 'other'},
             {'error': {'type': 'terminal_error'}}]
    for body in cases:
        transport_journal.clear()
        with pytest.raises(ValueError, match='scenario_oauth_failed'):
            call(lambda *a, **kw: Response(body))
        assert len(transport_journal) == 1
    class SSE(Response):
        def read(self, limit):
            return b'data: {"type":"response.completed"}\n\n'
    transport_journal.clear()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(lambda *a, **kw: SSE({}))
    assert len(transport_journal) == 1


def test_operational_wrapper_alone_opts_in(monkeypatch):
    from live import scenario_runner, scenario_oauth
    def propose(*args, **kwargs):
        assert kwargs['generate'].func is scenario_oauth.generate_scenario
        assert kwargs['generate'].keywords == {'retry_transport': True}
        return 'wrapped'
    monkeypatch.setattr(scenario_runner, 'propose', propose)
    assert scenario_runner.operational_propose({}, {}, {}) == 'wrapped'


def test_runtime_one_logical_decision_one_order_with_two_attempts(monkeypatch, tmp_path):
    import sqlite3
    from functools import partial
    from live import scenario_recovery
    from live.scenario_llm import propose
    from live.scenario_preview import response_contract
    from live.scenario_runtime import ScenarioRuntime
    from tests.test_scenario_runtime import Broker
    from tests.test_scenario_llm import wire
    monkeypatch.setattr(scenario_recovery, 'record_model_transport_attempt', real_transport_journal)
    conn = sqlite3.connect(tmp_path / 'runtime.db')
    broker = Broker()
    calls = []
    def proposal(snapshot, context):
        output = wire(context, 'OPEN')
        output.update(expires_at=context['now'] + 90, hard_stop=90,
                      entries=[{'id': 'entry-1', 'price': 100, 'quantity': 1}],
                      take_profits=[{'id': 'tp-1', 'price': 120, 'fraction': .5}])
        def open_url(*args, **kw):
            calls.append(1)
            if len(calls) == 1:
                raise http_error()
            return Response(dict(result([{'type': 'message', 'role': 'assistant',
                'content': [{'type': 'output_text', 'text': json.dumps(output)}]}]),
                model=json.loads(args[0].data)['model']))
        return propose(snapshot, context, response_contract(context), clock=lambda: 1800000000.,
            generate=partial(generate_scenario, retry_transport=True, open_url=open_url))
    runtime = ScenarioRuntime(conn, broker, proposal,
        lambda: {'valid': True, 'as_of_ms': 1800000000000}, clock=lambda: 1800000000., recovery_enabled=True)
    outcome = runtime.tick()
    assert outcome['status'] == 'intent_pending', (outcome, conn.execute('SELECT kind,body FROM llm_scenario_recovery_events').fetchall())
    assert len(broker.executed) == 1 and len(calls) == 2
    events = conn.execute('SELECT kind,slot,body FROM llm_scenario_recovery_events').fetchall()
    assert sum(row[0] == 'MODEL_REQUEST' for row in events) == 1
    assert sum(row[0] == 'MODEL_RAW' for row in events) == 1
    assert not any(row[0] == 'MODEL_ERROR' for row in events)
    attempts = [json.loads(row[2]) for row in events if row[0] == 'MODEL_TRANSPORT_ATTEMPT']
    assert [row['attempt'] for row in attempts] == [1, 2]
    assert [row['retry'] for row in attempts] == [True, False]
    assert attempts[0]['input_id'] == attempts[1]['input_id']
    assert all(set(row) == {'input_id', 'attempt', 'status', 'reason', 'elapsed_seconds', 'retry'} for row in attempts)


@pytest.mark.parametrize('elapsed,retry', [(71, False), (74, False), (76, False), (10, True)])
def test_original_deadline_and_late_success(monkeypatch, elapsed, retry):
    from live import scenario_oauth
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    now = [0.0]
    monkeypatch.setattr(scenario_oauth.time, 'monotonic', lambda: now[0])
    calls = []
    def open_url(req, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            now[0] = elapsed
            raise http_error()
        assert timeout == 75 - elapsed
        now[0] = 76
        return Response(result())
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        call(open_url)
    assert len(calls) == (2 if retry else 1)


def call(open_url,**kw):
    kw.setdefault('retry_transport', True)
    return generate_scenario(system_prompt='test',user_prompt='test',model='gpt-6-luna',
        reasoning_effort='high',fast_tier=True,timeout=75,open_url=open_url,
        response_schema={'type':'object','properties':{},'required':[], 'additionalProperties':False},**kw)


class Response:
    status=200
    def __init__(self,result): self.result=result
    def geturl(self):return 'http://127.0.0.1:18741/v1/responses'
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit):return json.dumps(self.result).encode()


@pytest.mark.parametrize('model,effort', [('gpt-6.1-terra','low'), ('gpt-6.1-sol','low'), ('gpt-6-luna','high'), ('gpt-6-luna','xhigh')])
def test_exact_approved_model_effort_pairs_keep_no_tools_and_no_default_retry(monkeypatch, model, effort):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls = []
    def open_url(request, timeout):
        body = json.loads(request.data)
        calls.append(body)
        assert body['model'] == model and body['reasoning']['effort'] == effort
        assert body['tools'] == [] and body['tool_choice'] == 'none'
        assert timeout <= 75
        return Response(dict(result(), model=model))
    assert generate_scenario(system_prompt='test', user_prompt='test', model=model,
        reasoning_effort=effort, fast_tier=True, timeout=75, open_url=open_url,
        response_schema={'type':'object'}).text == '{}'
    assert len(calls) == 1


@pytest.mark.parametrize('model,effort', [('gpt-6.1-terra','high'), ('gpt-6.1-sol','high'), ('gpt-6.1-sol','xhigh'),
    ('gpt-6-luna','low'), ('gpt-6.1-sol','medium'), ('other','low')])
def test_crossed_model_effort_pairs_are_rejected_before_transport(model, effort):
    with pytest.raises(ValueError, match='scenario_model_policy'):
        generate_scenario(system_prompt='test', user_prompt='test', model=model,
            reasoning_effort=effort, fast_tier=True, timeout=75,
            open_url=lambda *_args, **_kw: pytest.fail('transport must not run'), response_schema={'type':'object'})


def test_sol_response_model_must_match_without_fallback(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls=[]
    def open_url(*_args, **_kwargs):
        calls.append(1)
        return Response(result())  # Legacy Luna response cannot satisfy a Sol request.
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        generate_scenario(system_prompt='test', user_prompt='test', model='gpt-6.1-sol',
            reasoning_effort='low', fast_tier=True, timeout=75, open_url=open_url, response_schema={'type':'object'})
    assert len(calls)==1


def test_operating_default_is_sol_low_for_both_calculated_phases():
    from live.scenario_llm import MODEL, EFFORT, TIMEOUT_SECONDS
    from tests.test_scenario_calculated_review import run
    assert (MODEL, EFFORT, TIMEOUT_SECONDS) == ('gpt-6.1-terra', 'low', 75)
    _, _, calls = run()
    assert len(calls) == 2
    assert all((c['model'], c['reasoning_effort'], c['retry_transport']) == ('gpt-6.1-terra','low',False) for c in calls)
    assert all('combine transitions only when their respective before/after source values match' in c['system_prompt'] for c in calls)


def test_sol_transport_failure_does_not_retry_or_fallback(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls=[]
    def open_url(request, **_kwargs):
        calls.append(json.loads(request.data)['model'])
        raise http_error()
    with pytest.raises(ValueError, match='scenario_oauth_failed'):
        generate_scenario(system_prompt='test', user_prompt='test', model='gpt-6.1-sol',
            reasoning_effort='low', fast_tier=True, timeout=75, open_url=open_url, response_schema={'type':'object'})
    assert calls == ['gpt-6.1-sol']


@pytest.mark.parametrize('override,code', [({'timeout':76},'scenario_timeout_policy'),
    ({'fast_tier':False},'scenario_model_policy'), ({'mcp_profile':'tools'},'scenario_model_policy')])
def test_sol_does_not_expand_timeout_or_tool_authority(override, code):
    arguments=dict(system_prompt='test',user_prompt='test',model='gpt-6.1-sol',reasoning_effort='low',
        fast_tier=True,timeout=75,response_schema={'type':'object'},
        open_url=lambda *_a, **_kw: pytest.fail('transport must not run'))
    arguments.update(override)
    with pytest.raises(ValueError,match=code):
        generate_scenario(**arguments)


def result(output=None):
    return dict(model='gpt-6-luna',status='completed',output=output or [dict(type='message',role='assistant',content=[dict(type='output_text',text='{}')])])


def test_request_has_no_tools_no_keys_and_exact_model_tier(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL',raising=False)
    def open_url(req,timeout):
        p=json.loads(req.data)
        assert p['tools']==[] and p['tool_choice']=='none'
        assert p['model']=='gpt-6-luna' and p['service_tier']=='priority'
        assert p['reasoning']=={'effort':'high'}
        assert p['text']['format']['type']=='json_schema'
        assert p['text']['format']['strict'] is True
        assert not req.has_header('Authorization')
        return Response(result())
    assert call(open_url).text=='{}'


@pytest.mark.parametrize('effort', ['high', 'xhigh'])
def test_same_model_supports_only_approved_high_and_xhigh_payload(monkeypatch, effort):
    from live.scenario_llm import EFFORT
    assert EFFORT == 'low'  # Legacy adapter capability does not change operating policy.
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL', raising=False)
    calls = []
    def opener(request, timeout):
        calls.append(json.loads(request.data))
        assert 0 < timeout <= 75
        return Response(result())
    response = generate_scenario(system_prompt='same', user_prompt='same',
        model='gpt-6-luna', reasoning_effort=effort, fast_tier=True, timeout=75,
        response_schema={'type': 'object'}, open_url=opener, retry_transport=False)
    assert response.text == '{}' and len(calls) == 1
    assert calls[0]['reasoning'] == {'effort': effort}
    assert calls[0]['model'] == 'gpt-6-luna' and calls[0]['service_tier'] == 'priority'
    assert calls[0]['tools'] == [] and calls[0]['tool_choice'] == 'none'


@pytest.mark.parametrize('override', [
    {'reasoning_effort': 'medium'}, {'reasoning_effort': 'max'},
    {'model': 'other'}, {'fast_tier': False}, {'mcp_profile': 'tools'},
])
def test_extra_effort_does_not_widen_other_model_authority(override):
    arguments = dict(system_prompt='same', user_prompt='same', model='gpt-6-luna',
        reasoning_effort='xhigh', fast_tier=True, timeout=75, mcp_profile=None,
        response_schema={'type': 'object'}, open_url=lambda *_a, **_kw: pytest.fail('network'))
    arguments.update(override)
    with pytest.raises(ValueError, match='scenario_model_policy'):
        generate_scenario(**arguments)


def test_tool_output_and_wrong_model_rejected(monkeypatch):
    monkeypatch.delenv('PRISM_BTC_SCENARIO_OAUTH_URL',raising=False)
    for r in [result([dict(type='function_call',name='place_order')]),
              result([dict(type='message',role='assistant',content=[dict(type='refusal',refusal='no')])]),
              {**result(),'model':'other'}, {**result(),'status':'incomplete'}]:
        with pytest.raises(ValueError,match='scenario_oauth_failed'):
            call(lambda *a,**kw:Response(r))


@pytest.mark.parametrize('url',['https://example.com/v1/responses','http://localhost:18741/v1/responses','http://127.0.0.1/other','http://user@127.0.0.1/v1/responses'])
def test_remote_proxy_not_allowed(monkeypatch,url):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL',url)
    with pytest.raises(ValueError,match='local_oauth_proxy_required'):
        call(lambda *a,**kw:pytest.fail('no network'))

@pytest.mark.parametrize('wrapped', [False, True])
def test_timeout_type_is_preserved_without_transport_details(wrapped):
    from urllib.error import URLError
    error = TimeoutError('sensitive transport details')
    def open_url(*args, **kwargs):
        raise URLError(error) if wrapped else error
    with pytest.raises(ValueError, match='^scenario_oauth_failed$') as exc:
        generate_scenario(system_prompt='test', user_prompt='test', model='gpt-6-luna',
            reasoning_effort='high', fast_tier=True, timeout=75, open_url=open_url,
            response_schema={'type': 'object'})
    assert type(exc.value).__name__ == 'ScenarioOAuthTimeout'
    assert exc.value.__cause__ is None


def test_other_transport_error_does_not_claim_timeout():
    from urllib.error import URLError
    def open_url(*args, **kwargs):
        raise URLError('sensitive connection failure')
    with pytest.raises(ValueError, match='^scenario_oauth_failed$') as exc:
        generate_scenario(system_prompt='test', user_prompt='test', model='gpt-6-luna',
            reasoning_effort='high', fast_tier=True, timeout=75, open_url=open_url,
            response_schema={'type': 'object'})
    assert type(exc.value) is ValueError
