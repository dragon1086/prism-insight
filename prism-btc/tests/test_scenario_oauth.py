import json

import pytest

from live.scenario_oauth import generate_scenario


def call(open_url,**kw):
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
