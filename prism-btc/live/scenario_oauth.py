"""Tool-less calls to the existing localhost ChatGPT OAuth Responses proxy.

No Codex CLI agent is launched: neither shell nor configured MCP tools can be
inherited. The host proxy owns OAuth; this client has no API-key fallback.
"""
from __future__ import annotations

import json
import math
import os
from types import SimpleNamespace
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MAX_BYTES = 1_048_576


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("oauth_redirect_forbidden")


def _local_open(request, timeout):
    # Do not leak trading inputs to environment HTTP proxies or redirects.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def generate_scenario(*, system_prompt, user_prompt, model, reasoning_effort,
                      fast_tier, timeout, mcp_profile=None, open_url=_local_open):
    if (model != "gpt-6-luna" or reasoning_effort != "high"
            or fast_tier is not True or mcp_profile is not None):
        raise ValueError("scenario_model_policy")
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 75:
        raise ValueError("scenario_timeout_policy")
    endpoint = os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL", "http://127.0.0.1:18741/v1/responses")
    url = urlsplit(endpoint)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
            or url.path != "/v1/responses" or url.username or url.password or url.query or url.fragment):
        raise ValueError("local_oauth_proxy_required")
    payload = {"model":model,"reasoning":{"effort":reasoning_effort},
               "service_tier":"priority", "instructions":system_prompt,
               "input":[{"role":"user","content":user_prompt}],
               "tools":[],"tool_choice":"none","store":False,"stream":False}
    request = Request(endpoint, data=json.dumps(payload,allow_nan=False).encode(),
                      headers={"Content-Type":"application/json"}, method="POST")
    try:
        with open_url(request,timeout=timeout) as response:
            if response.geturl() != endpoint or response.status != 200:
                raise ValueError("oauth_proxy_response")
            raw = response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:
            raise ValueError("oauth_response_size")
        result=json.loads(raw)
        if result.get("status") != "completed":
            raise ValueError("oauth_not_completed")
        if result.get("model") != model:
            raise ValueError("oauth_model_mismatch")
        texts=[]
        for item in result.get("output",[]):
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or item.get("role") != "assistant":
                raise ValueError("model_tool_output_forbidden")
            for block in item.get("content",[]):
                if block.get("type") != "output_text" or not isinstance(block.get("text"),str):
                    raise ValueError("model_nontext_output")
                texts.append(block["text"])
        if not texts:
            raise ValueError("model_text_missing")
        return SimpleNamespace(text="".join(texts))
    except Exception:
        raise ValueError("scenario_oauth_failed") from None
