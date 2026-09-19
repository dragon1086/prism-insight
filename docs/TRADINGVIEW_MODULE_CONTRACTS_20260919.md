# 초기 모듈 구현 설계

P1 prism_core/source_observation_quality.py:
immutable PriceObservation/ObservationPolicy; timezone-aware datetime만 사용.
assess_observation은 expected_asof 기준으로 freshness를 계산하고 observed/captured/available 시간을
decision_at과 대조한다. source 문자열은 식별에만 사용. explicit scope 비교. 결과에는 reason code와
age를 남기며 provider ranking/가격 overwrite/거래 승인 없음.

P2 prism_core/tradingview_collection.py:
TradingViewOptions.parse가 명시적 enabled=true만 수용하며 잘못된 옵션은 OFF.
ReadRequest와 bounded collect_tradingview(config, requests, transport_factory) API.
ON에서만 async context manager factory를 열고 injected call(tool,args)로 순차 호출한다.
public read allowlist, 요청 수/각 호출/세션/각 응답/누적 응답 제한. 기존 normalize_evidence 재사용.
transport는 decoded JSON object를 반환하는 좁은 인터페이스; SDK/OAuth 구현은 P3.
No cache prevents intraday date-key contamination. Dict output is research-only with collection status,
per-request reasons and bounded normalized evidence. Caller report/screening hook not wired yet.

P1과 P2는 파일 소유권을 분리하여 병렬 구현 가능. 부모는 P1, executor는 P2.
계획 critic 승인 후 테스트부터 작성. 최종 architect/security/code 리뷰는 구현과 분리한다.

## Critic feedback revision 1: exact P1 contract

PriceObservation: source(str), symbol/interval/session/adjustment/currency(str or None),
asof/captured_at/available_at(aware datetime or None), final(bool or None).
asof is actual observed snapshot instant or completed bar END, never a future nominal bar end.
captured_at is original acquisition time, not the current replay run time. P1 compares archived
price observations captured by decision_at; later retrieval of historical filings is a different P4 contract.
ObservationPolicy: decision_at, expected_asof (aware datetimes, expected <= decision),
max_lag_seconds (finite nonnegative number, not bool), symbol/interval/session/adjustment/currency
(nonempty expected strings), require_final (bool, default true). Caller supplies calendar-aware
expected_asof; this module does not invent market close/weekend rules or latency defaults.
age_seconds = (expected_asof - observation.asof).total_seconds().
Unknown timestamps/finality/scope yield UNKNOWN. Bad timestamp types, asof > expected_asof,
any time > decision_at, available_at < asof, captured_at < available_at yield INVALID.
Nonmatching known scope fields yield INCOMPARABLE. final=false with require_final yields INCOMPARABLE.
Known stale age > max_lag_seconds yields STALE. Remaining valid full metadata yields FIT_FOR_COMPARISON.
Precedence INVALID > INCOMPARABLE > UNKNOWN > STALE > FIT_FOR_COMPARISON; all reasons retained.
Result includes source, age, reasons, suitability status, fact_validated=false, execution_authorized=false.
Policy errors raise ValueError; observed malformed data returns status, never provider-specific fallback.
Examples: expected 15:00, asof 14:45, allowed 1200s => FIT for any provider with complete metadata.
Saturday decision with expected Friday 16:00 and completed Friday 16:00 => age 0, FIT.
asof later than expected => INVALID even if provider is KIS. No latest-source winner selection.

## Critic feedback revision 1: exact P2 contract

Public allowlist (only these canonical names, no account reads): get_ohlcv, get_documents,
get_document_view, get_news, get_news_story, get_financial_history, get_forecasts,
get_earnings_calendar, get_dividends_calendar, get_economic_calendar.
These are existing normalizer adapters. Rich screener custom columns and technical snapshots wait for P3.
ReadRequest: tool(str canonical exact name), arguments(dict JSON-compatible), requested_symbols
(optional tuple[str,...] used for returned coverage). Collector transport receives canonical names;
the P3 SDK adapter maps them to catalog names. Requests are a list/tuple of ReadRequest.
The complete request list is validated before entering the transport; any invalid request returns
INVALID_REQUEST with zero I/O, not partial execution. Empty list returns EMPTY with zero I/O.
OFF returns None. Config is a dedicated dict with enabled=true plus known budget keys;
missing/malformed/unrecognized keys disable the collector. Config cannot select servers or endpoints.
Default/hard maximum: max_calls 4/8; per_call_seconds 15/30; total_seconds 45/90;
max_response_bytes 262144/1048576; max_evidence_bytes 12000/24000;
total_evidence_bytes 36000/72000; max_request_bytes fixed 4096; full request count <= max_calls.
All integer limits require true int; durations finite positive int/float, not bool.
Budgets are decoded JSON processing/admission limits, NOT transport receive-memory/network limits.
JSON serialization rejects NaN and unsupported objects before admission; request deep-copy via JSON.
Per-response oversized input returns RESPONSE_LIMIT; normalized evidence over per-result or cumulative
limit returns EVIDENCE_LIMIT/RUN_EVIDENCE_LIMIT. Whole record omitted, no string cuts.
Errors/timeouts keep bounded code-only receipt and subsequent calls may proceed within total budget.
Factory/open/call/close are under a total wait_for deadline, subject to cooperative asyncio cancellation.
Cancellation propagates. No raw exception/arguments/payload in receipt. Top status COMPLETE only if
all requests have admitted AVAILABLE normalized evidence; other mixed results PARTIAL/FAILED.
Metrics distinguish calls attempted, raw bytes processed and admitted evidence bytes; no token claims.

## Next independent phase P3a: lazy fixed-endpoint HTTP adapter

prism_core/tradingview_transport.py exposes make_transport_factory(credential_supplier, ...test deps)
returning P2-compatible async CM factory. Creation has zero I/O/import; only entering ON invokes
async credential_supplier once. Accept TVAccessGrant(access_token repr=False, expires_at aware datetime).
Grant valid only if nonempty printable token without whitespace/control chars, <=8192 chars,
expires_at > injected aware clock()+30s; no refresh/login/file reads/cache in P3a.
Missing/invalid grant raises bounded TradingViewTransportError('AUTH_UNAVAILABLE').
Lazy import httpx, mcp.ClientSession, mcp.client.streamable_http.streamable_http_client on enter.
Use fixed https://mcp.tradingview.com/mcp, httpx AsyncClient(trust_env=False, follow_redirects=False,
timeout=15, headers Authorization) and one ClientSession initialize per collection. No adapter-level
tool retry. Pinned SDK protocol SSE reconnects can occur within the cooperative total deadline;
these are not P2 tool calls and must not be reported as zero network retries.
P2 owns cooperative total deadline. Never append TV tools to default agent or generic stdio registry.
Map only P2's canonical allowlist to mcp-tv- + hyphenated name, reject unsupported before call.
Decoder: MCP isError yields {'success':false,'error':'MCP_ERROR'} without copying body. Only JSON
object payload accepted. Text must be one TextContent with object JSON; nontext/extra blocks rejected.
structuredContent object may be used if no text. When both structured and text exist require deep
equality, otherwise bounded error AMBIGUOUS_PAYLOAD (do not select the convenient branch).
SDK object fields are accessed without dumping/logging secret-bearing raw payload. SDK errors are
re-raised as static transport codes, cancellation propagates. Grant repr hides token.
Dependencies injectable as a loader bundle (http_client_factory, stream_factory, session_factory)
for offline tests; defaults resolve installed pinned mcp/httpx lazily.
Tests RED/GREEN: factory OFF no deps/supplier; expiry/invalid token no HTTP; one session and exact
tool mapping; authorization fixed host; no redirects/environment proxy; cleanup; isError; malformed/
contradictory branches; credential-bearing exception suppressed; cancellation. No current credential
validity or actual endpoint success claims; P3b refresh/source smoke and P4+ remain separate.

P3a critic revision: SDK logging scope is a context-local sanitizing LogRecordFactory wrapper.
Install only on ON entry, reference-count concurrent scopes with a threading lock, restore previous
factory on last exit when still owned. A ContextVar flags TV tasks (including inherited SDK tasks).
Outside TV context, records are unchanged, including concurrent unrelated tasks. Within TV context,
replace msg/args/exc_info/exc_text/stack_info with static diagnostic text before logging handlers run.
No credentials/request/response body logs. Never globally disable logging or set global levels.
No I/O at import/factory construction. Tests: real pinned SDK with httpx.MockTransport exercises
malformed initialize/JSON and SSE marker payloads, assert marker absent in caplog and adapter exception;
test concurrent unrelated logging remains visible and nested scopes restore factory after cancellation.
This wrapper protects standard SDK/HTTP log record fields, not arbitrary application-added log extras
or memory dumps. P3b cannot claim universal secret erasure on this basis.
