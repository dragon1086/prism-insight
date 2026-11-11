"""Tool-less calls to the existing localhost ChatGPT OAuth Responses proxy.

No Codex CLI agent is launched: neither shell nor configured MCP tools can be
inherited. The host proxy owns OAuth; this client has no API-key fallback.
"""
from __future__ import annotations

import json
import math
import os
import re
import time
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler, ProxyHandler

MAX_BYTES = 1_048_576
MIN_RETRY_SECONDS = 5


def usage_counts(value):
    """Four bounded counters only; never persist attribution or account data."""
    if not isinstance(value, dict):
        return None
    def count(v):
        return v if type(v) is int and 0 <= v < 2**53 else None
    details = value.get('input_tokens_details')
    output_details = value.get('output_tokens_details')
    result = dict(input_tokens=count(value.get('input_tokens')), output_tokens=count(value.get('output_tokens')),
        cached_input_tokens=count(value.get('cached_input_tokens', details.get('cached_tokens') if isinstance(details,dict) else None)),
        reasoning_tokens=count(value.get('reasoning_tokens', output_details.get('reasoning_tokens') if isinstance(output_details,dict) else None)))
    for subset,total in [('cached_input_tokens','input_tokens'),('reasoning_tokens','output_tokens')]:
        if result[subset] is not None and (result[total] is None or result[subset] > result[total]):
            result[subset] = None
    return result if any(v is not None for v in result.values()) else None


class ScenarioOAuthTimeout(ValueError):
    """Safe timeout category for sealed review evidence."""


def safe_failure_reason(value):
    """Only closed categories can cross the trading evidence/notice boundary."""
    if not isinstance(value, str):
        return None
    if value in {'timeout', 'connection', 'upstream_error', 'rate_limited',
                 'authentication', 'incomplete', 'model_mismatch', 'invalid_response'}:
        return value
    return value if re.fullmatch(r'http_[1-5][0-9]{2}', value) else None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_error_key")
        result[key] = value
    return result


def _verified_truncation(error, endpoint):
    """Only the observed localhost proxy transport error, never model errors."""
    if error.code != 502 or error.geturl() != endpoint:
        return False
    try:
        raw = error.read(4097)
        if len(raw) > 4096:
            return False
        body = json.loads(raw, object_pairs_hook=_unique_object)
        if set(body) != {"error"} or set(body["error"]) != {"type", "message"}:
            return False
        failure = body["error"]
        return failure["type"] == "server_error" and re.fullmatch(
            r"Upstream connection error: Response payload is not completed: "
            r"<TransferEncodingError:\s*400,\s*message='Not enough data for satisfy transfer length header\.'>",
            failure["message"]) is not None
    except Exception:
        return False


def _read_response(request, open_url, endpoint, timeout, retry_transport):
    # Socket timeouts are not a hard wall-clock kill. Never accept late output.
    from live.scenario_recovery import record_model_transport_attempt
    started = time.monotonic()
    deadline = started + timeout
    for attempt in (1, 2):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or (attempt == 2 and remaining < MIN_RETRY_SECONDS):
            raise ValueError("oauth_deadline")
        status, reason, retry = None, "transport_failed", False
        try:
            with open_url(request, timeout=remaining) as response:
                status = response.status
                if response.geturl() != endpoint or status != 200:
                    raise ValueError("oauth_proxy_response")
                raw = response.read(MAX_BYTES + 1)
            if time.monotonic() > deadline:
                reason = "deadline_exceeded"
                raise ValueError("oauth_deadline")
            reason = "http_response"
            return raw
        except HTTPError as error:
            status = error.code
            try:
                known = _verified_truncation(error, endpoint)
            finally:
                error.close()
            reason = "upstream_transfer_truncated" if known else "http_error"
            retry = (retry_transport and known and attempt == 1
                     and deadline - time.monotonic() >= MIN_RETRY_SECONDS)
            if not retry:
                raise
        finally:
            recorded = False
            if retry_transport:
                recorded = record_model_transport_attempt(attempt=attempt, status=status, reason=reason,
                    elapsed_seconds=max(0., time.monotonic() - started), retry=retry)
        if retry and not recorded:
            raise ValueError("oauth_retry_audit_unavailable")
    raise ValueError("oauth_attempts_exhausted")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("oauth_redirect_forbidden")


def _local_open(request, timeout):
    # Do not leak trading inputs to environment HTTP proxies or redirects.
    return build_opener(ProxyHandler({}), _NoRedirect()).open(request, timeout=timeout)


def generate_scenario(*, system_prompt, user_prompt, model, reasoning_effort,
                      fast_tier, timeout, response_schema, mcp_profile=None, open_url=_local_open,
                      retry_transport=False):
    if type(retry_transport) is not bool:
        raise ValueError("scenario_retry_policy")
    if ((model, reasoning_effort) not in (("gpt-6.1-terra", "low"), ("gpt-6.1-sol", "low"), ("gpt-6-luna", "high"), ("gpt-6-luna", "xhigh"))
            or fast_tier is not False or mcp_profile is not None):
        raise ValueError("scenario_model_policy")
    if type(timeout) not in (int,float) or not math.isfinite(timeout) or not 0 < timeout <= 75:
        raise ValueError("scenario_timeout_policy")
    endpoint = os.environ.get("PRISM_BTC_SCENARIO_OAUTH_URL", "http://127.0.0.1:18741/v1/responses")
    url = urlsplit(endpoint)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "::1"}
            or url.path != "/v1/responses" or url.username or url.password or url.query or url.fragment):
        raise ValueError("local_oauth_proxy_required")
    payload = {"model":model,"reasoning":{"effort":reasoning_effort},
               "instructions":system_prompt,
               "input":[{"role":"user","content":user_prompt}],
               "tools":[],"tool_choice":"none","store":False,"stream":False}
    if not isinstance(response_schema, dict) or response_schema.get("type") != "object":
        raise ValueError("scenario_response_schema_required")
    payload["text"] = {"format": {"type": "json_schema", "name": "btc_scenario",
                                   "strict": True, "schema": response_schema}}
    request = Request(endpoint, data=json.dumps(payload,allow_nan=False).encode(),
                      headers={"Content-Type":"application/json"}, method="POST")
    try:
        raw = _read_response(request, open_url, endpoint, timeout, retry_transport)
        if len(raw)>MAX_BYTES:
            raise ValueError("oauth_response_size")
        result=json.loads(raw)
        if result.get("status") != "completed":
            error = result.get('error')
            code = error.get('code') if isinstance(error, dict) else None
            reason = {'server_error':'upstream_error', 'rate_limit_exceeded':'rate_limited',
                      'usage_limit_reached':'rate_limited', 'insufficient_quota':'rate_limited',
                      'authentication_error':'authentication'}.get(code, 'incomplete') if isinstance(code, str) else 'incomplete'
            raise ValueError('oauth_' + reason)
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
        usage = usage_counts(result.get('usage'))
        return SimpleNamespace(text="".join(texts), **({'usage':usage} if usage is not None else {}))
    except HTTPError as exc:
        reason = safe_failure_reason('http_' + str(exc.code))
        raise ValueError('scenario_oauth_failed:' + (reason or 'connection')) from None
    except (TimeoutError, URLError) as exc:
        if isinstance(exc, TimeoutError) or isinstance(getattr(exc, 'reason', None), TimeoutError):
            raise ScenarioOAuthTimeout("scenario_oauth_failed:timeout") from None
        raise ValueError("scenario_oauth_failed:connection") from None
    except Exception as exc:
        reason = {'oauth_deadline':'timeout', 'oauth_upstream_error':'upstream_error',
                  'oauth_rate_limited':'rate_limited', 'oauth_authentication':'authentication',
                  'oauth_incomplete':'incomplete', 'oauth_model_mismatch':'model_mismatch'}.get(str(exc), 'invalid_response')
        raise ValueError('scenario_oauth_failed:' + reason) from None
