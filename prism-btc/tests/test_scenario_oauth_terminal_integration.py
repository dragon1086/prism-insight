"""Proxy SSE normalization through the strict BTC client; no network or orders."""
import json
import pytest
from cores.chatgpt_proxy.api_translator import collect_sse_to_response
from live.scenario_oauth import generate_scenario, ScenarioOAuthIncomplete


class Response:
    status=200
    def geturl(self):return "http://127.0.0.1:18742/v1/responses"
    def __init__(self, body):self.body=json.dumps(body).encode()
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self,limit):return self.body[:limit]


def invoke(text, monkeypatch):
    monkeypatch.setenv('PRISM_BTC_SCENARIO_OAUTH_URL','http://127.0.0.1:18742/v1/responses')
    body=collect_sse_to_response(text);calls=[]
    def open_url(*args,**kwargs):calls.append(1);return Response(body)
    try:
        return generate_scenario(system_prompt='test',user_prompt='test',model='gpt-6.1-terra',
            reasoning_effort='low',fast_tier=False,timeout=5,response_schema={'type':'object'},
            open_url=open_url,retry_transport=False)
    finally:
        assert len(calls)==1


def test_complete_typed_terminal_is_accepted_without_named_event_line(monkeypatch):
    text='event: response.output_text.delta\ndata: {"type":"response.output_text.delta","delta":"{}"}\n\n'
    text+='data: {"type":"response.completed","response":{"id":"resp_done","model":"gpt-6.1-terra","output":[]}}\n\n'
    assert invoke(text,monkeypatch).text=='{}'


def test_valid_looking_partial_json_is_rejected_without_a_completed_terminal(monkeypatch):
    with pytest.raises(ScenarioOAuthIncomplete)as e:
        invoke('data: {"type":"response.output_text.delta","delta":"{}"}\n\n',monkeypatch)
    assert e.value.response_summary['incomplete_reason']=='missing_terminal_event'
    assert e.value.response_summary['usage_available']is False


def test_typed_upstream_error_is_classified_without_retry(monkeypatch):
    with pytest.raises(ScenarioOAuthIncomplete,match='scenario_oauth_failed:rate_limited')as e:
        invoke('data: {"type":"error","code":"rate_limit_exceeded","message":"service busy"}\n\n',monkeypatch)
    assert e.value.response_summary['error_code']=='rate_limit_exceeded'
