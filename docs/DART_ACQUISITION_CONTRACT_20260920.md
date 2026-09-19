# P4b: bounded public DART acquisition into existing filing selector

Baseline1dde403c. User asks continue and take ownership of TV-use risk decision. Official terms3 explicitly
restrict TV-fed algorithmic use, no public MCP exception found. Operational decision NO-GO for TV-fed
automatic BUY/SELL; user-requested human research is distinct. Continue source-quality development using
direct official DART without TV. No broker/score/runtime/cron/credential changes or new dependencies.

Implement one module `prism_core/dart_public_filings.py` + `tests/test_dart_public_filings.py`.
Existing lxml>=4.9 and HTTPX already installed. No JS execution. Reuse FilingCandidate/select_periodic_filings.
API async collect_dart_periodic_filings(*, corp_code, decision_at, start_date, scope,
client_factory=None, max_pages=3, max_filings=8, max_calls=28, timeout_seconds=90).
Explicit public-source diagnostic API, not automatic application hook. Exact8digit corp_code;
timezone-aware decision <= clock now (caller historical cutoff); start date <= market cutoff date;
scope consolidated/standalone. Caps validated before network; max_pages1..5,max_filings1..20,
max_calls1..64,total timeout0< <=180. Per HTTP timeout15s. Sequential calls; no retries.

Observed protocol from official page JS/forms:
POST https://dart.fss.or.kr/dsab001/searchCorp.ax with currentPage, maxResults=100, maxLinks=10,
sort=date,series=desc,textCrpCik=<corp>,pageGubun=corp,startDate/endDate YYYYMMDD,
repeated publicType=A001,A002,A003. OMIT finalReport (do not ask final-only). No invented API key.
GET main URLs ONLY actual href returned in rows, exact DART host/path and rcpNo14digits.
Read main openCorpInfoNew8digit ID and match requested corp. Viewer node literal assignments for
text,rcpNo,dcmNo,eleId,offset,length,dtd parsed statically. Whitelist numeric fields/dartN.xsd format;
expected receipt matches; source function must contain /report/viewer.do. Never executeJS or invent IDs.
Construct ONLY fixed /report/viewer.do query from the complete returned tuple. Main URL stays candidate
source_url; viewer URL and tuple are section provenance. Redirects false; trust_env false.

HTTP request Accept-Encoding:identity and reject nonidentity response encoding before reading stream.
Per-response max2MiB decoded UTF8 (also streaming raw identity bytes), cumulative8MiB, no giant model input.
Non200/timeout/htmlblocked/malformed return code-only errors, never no-filings. No raw exceptions/credentials.
Client factory injected for actual httpx.MockTransport tests. Whole collector total uses wait_for;
cancellation propagates and closes client. No cookies/cache/auth/file writes.

Parsers:
parse_catalog_page(html, corp_code) -> rows with ordinal, actual main URL+receipt ID, displayname,
kind from exact 사업/반기/분기보고서 name, report_period_label (discovery only), submitted date,
is_correction title contains 정정, plus page/total_pages/total parsed .pageInfo [1/1] [총 7건].
tbody#tbody direct rows:6cells expected, corporateID from onclick not text-name guess; malformed/duplicate/
wrongcorp/outofquerydate => error and incomplete. Explicit normal zero-results text+pageInfo needed for EMPTY.
Validate all pages requested in sequence, expected count and consistent page totals/total_count;
unique IDs+ordinal contiguity and row counts. Complete within queried dates/types only, global false.

parse_viewer_nodes(html, receipt_id, corp_code): isolate each nodeN text-led record, JSON double-string
literals only, no eval. Reject dynamic/duplicate/contradictory fields, duplicate node identities.
Different nodes may share document ID but cannot reuse same eleId with different metadata.
Do not infer stock ticker from corporate name; select entity_id=DART:<corp_code>.

parse_cover_metadata(html): actual 사업연도 labeled table and following '부터'/'까지' rows -> exact date
start/end; legal company name from 회사명 row; cover kind from spaced heading. Cover date in
한국거래소귀중 row must match catalog submission date. Missing/duplicate/conflicting labels -> gap.
Do not infer January1 or month-end from fiscal label. Periodend month must match listingYYYY.MM hint.

Fetch each filing (bounded newest discovery-period-label first, preserve full list) main+cover+requested
scope financial-statements section. Prefer exact heading ^2. 연결재무제표 or ^4. 재무제표 observed current
format but allow heading number variation anchored full Korean label. Verify actual scope body via
explicit 연결재무상태표 (consolidated) / 재무상태표 without 연결 in table-heading for standalone, nonempty
table containing numeric cells and financial-label assets/liabilities; no '해당사항없음'-only success.
Return body_coverage=selected_sections; available means selected scope section, not full report/notes.
No raw full HTML in return; retain UTF8 byte count, SHA256, exact URL/tuple and bounded plain text
preview <=1200 chars labeled preview_not_complete (not input to trading/model). Tables stay out of
model until subsequent extraction phase; no numerical facts certified from previews.

Bridge every catalog row into candidate; successful cover provides exact period/scope. Failed/unfetched
rows keep unresolved (periodNone/scopeNone), never silently dropped so older read body cannotbe calledlatest.
If cover valid but requested scope body unavailable, retain requested scope + body_status=unavailable,
record scope verification=false; never fallback to an old document as current. A body-available scope
requires explicit section verification. publication_verified=True only for valid official catalog dates.
Any potentially cutoff-eligible correction with unresolved lineage blocks primary selection with
CORRECTION_LINEAGE_UNRESOLVED (no invented amendment_of). Verified future/date-later corrections ignored;
same-day date-only corrections treated unresolved. No generic whole-document correction merge.
Always call existing selector listing_complete=False: queried-window coverage != universal completeness.
Selection uses source publication dates but historical_version_verified=False: current retrieval alone
does not prove unchanged historical body. Preserve observed_at separately (UTC), receipt and dcmNo/hash.

Return schema dict {status, query, observed_at, coverage{complete_within_query,global_complete:false,
expected_count,seen_count}, filings:[...], selection, limitations, metrics{calls,response_bytes},
historical_version_verified:false, fact_validated:false}. status COMPLETE_WITHIN_QUERY only all rows
parsed/covers/scope bodies successful + no unresolved correction; otherwise PARTIAL/FAILED/EMPTY.
Limitations include QUERY_WINDOW_ONLY, CURRENT_RETRIEVAL_NOT_HISTORICAL_SNAPSHOT, DATE_ONLY_PUBLICATION,
SELECTED_SECTIONS_NOT_FULL_DOCUMENT. Source failure never becomes no-news/no-company-fundamentals.

Actual read-only RF smoke after tests: corp01343665, cutoff2026-09-18T15:30:00+09:00,start2025-01-01,
consolidated. Official query returned7 receipts including 20260814001631 half,20260515000862 quarter,
20260318001224 annual. Expect half as base, annual2025 supplemental; only in-query latest, notglobal.
No source raw report text in public repo; synthetic fixtures with same shape, local hash/receipts only.

## Critic revision: collector-side no-fallback wrapper

Do not change existing selector semantics. After select_periodic_filings, apply collector guard:
- Any incomplete page/row validation or max_pages truncation: withhold primary (unknown missing
  records may be newer). max_filings truncation is NOT page coverage failure; evaluate below.
- Any cutoff-eligible is_correction record with no proven lineage: withhold primary with status
  CORRECTION_LINEAGE_UNRESOLVED. Never convert it to original amendment_of=None for final use.
- Any failed/unfetched/ambiguous-date record whose validated cover period_end is >= chosen primary
  period_end, OR whose only discoveryYYYY.MM label is >= chosen YYYY.MM, OR whose period label is
  unknown: withhold. Older clearly labeled records may remain gaps without hiding the usable newer
  body. Labels only trigger conservative suppression; they never certify period/date/scope.
- No chosen eligible candidate + any unresolved acquisition => withhold, not no-filings.

Withholding sets selection.status=ACQUISITION_INCOMPLETE (or correction status above),
primary_id/latest_candidate_id/annual_supplement_id=None, latest_confirmed=False,
best_known_candidate_id=<inner latest_candidate_id, diagnostic only>, blocked_by=sorted receiptIDs
and/or PAGE_COVERAGE_UNCONFIRMED. Preserve inner reasons/publication bounds and append own reason.
Collector status PARTIAL when any rows remain, FAILED when no interpretable rows; only verified
zero-row normal catalog gets EMPTY. Verified future-date records don't block historical selection.
Missing scope body of newest with valid cover explicitly blocks; no annual as default fallback.

## Diagnostic CLI (parent-owned independent surface)
tools/probe_dart_filings.py with tests/test_probe_dart_filings.py. Requires --live plus --corp-code,
--decision-at timezone-aware ISO, --start-date ISO, --scope. Missing --live returns2 without collector
import/call. Optional --out creates a new UTF8 JSON receipt with exclusive creation (never overwrites).
Default stdout; no raw HTML/body/previews retained, recursively strip keys containing preview and
html/body/raw_content. Preserve identities, exact source URLs, metadata, hashes, selection and metrics.
No arbitrary endpoint/transport options exposed. Collector injected for tests. COMPLETE_WITHIN_QUERY/
EMPTY exit0; PARTIAL/FAILED exit2. Invalidargs/no-live/existingoutput tested no network. Unexpected
errors emit static PROBE_FAILED, no exceptions or source bodies. This is diagnostic not runtimehook.

## Actual-source feedback, 2026-09-20
Observed DART row ordinals ascend1..N even when reports sort descending by date; page2 continues101.
Actual viewDoc function has nested if before literal /report/viewer.do; parse lexically without executing
JS, excluding comments/strings from assignment/declaration positions. Unsupported meaningful node writes
(e.g. dot offset overriding a bracket literal) cause rejection rather than stale tuple reuse. Single-pass
or explicitly bounded token processing; no quadratic per-function suffix rescans.
Actual zero-periodic-result response has NO pageInfo: tbody#tbody sole tr, sole td class containsno_data,
colspan=6, exact normalized text '조회 결과가 없습니다.'. This verified canonical empty-state may normalize
page1/totalpages1/total0 with pagination_basis=empty_state; never infer empty from generic error phrasing.
If optional pageInfo exists it must independently be consistent zero; no anchors/extra rows/filings allowed.
Preserve no-auth/no-body ambiguity as failure, not a zero-result response.
Tests assert all3 chosen IDs None plus reason/blocker for newest failure/pages/correction, and retained
newer primary but no annual supplement when only a clearly older annual body fails.
# Regression contract

Before code: synthetic end-to-end7catalog rows+mainnodes+cover+scopeHTML yields half base and annual
supplement. Existing selector used, no fake calendar periods. RED then GREEN; no network tests.
Rows: wrongcorp, badreceiptURL, duplicate/missingordinals, repeated/changedpagination,7total vs6rows,
missingpageInfo, HTTP200errorpage, explicitzero vs missingtbody, datesoutsidequery, correction unknown.
Cover: absentdate, ambiguousdate, FYnotJan1, legalnamepresent, kind/periodmonth/submission mismatch.
Nodes: dynamicJS,noexec,sameidconflict,wrongrcpt/corp,foreignurl,negative/largeoffset,dtdunexpected,
noactualviewerpath, missingselectedsection, linkedviewasprovided. All unfamiliar shapes fail closed.
Body: tablecaption alone/no numeric or no assets/liability labels, emptyblocked, negatives/units
notcomputed by collector; individual section byte caps and cumulative limit retain explicit partial.
Network: fixedhost/paths,no redirects/envproxy, compressedbody rejectedbeforeconsumption, timeout,
callbudget,filing/pagecaps, external cancellation and proper clientclose, no args/rawexceptions inreason.
Historical: same-day date only uncertain, future correction notused; observed_at never becomespublished;
complete_within_query never toggles global/latest_confirmed true. Unfetched/failednewest blocksfallback.
Existing150/830gate remains protected, CI register newtests. Actualsmoke separately counts calls/bytes/time.
