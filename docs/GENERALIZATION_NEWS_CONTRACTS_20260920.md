# Continued work on Draft PR752: generalization + bounded news discovery recovery

User confirms same PR should accumulate changes, merge/deploy once all work and gates pass.
Do NOT merge an incomplete research branch now. Keep default OFF/non-users unchanged, no trading policy
changes. User is willing to proceed despite discussed TV contractual risk; do not equate this with a
provider license or claim that personal/open-source/low-enforcement makes use authorized. Do not repeat
the policy debate instead of development. No live TV activation in this step.

## Preregistered DART cohort (freeze before results or fixes)
Fixed cutoff2026-09-18T15:30:00+09:00, start2025-01-01, requested consolidated statements.
RF327260 remains control, not independent validation.
Development generalization group: 삼성전자005930 semiconductor, 현대차005380 auto,
KB금융105560 financial holding, 오스코텍039200 biotech, LG화학051910 diversified industrial.
Holdout after fixes: 유한양행000100 pharmaceutical, 메리츠금융지주138040 finance holding,
에스엠041510 entertainment. These are unused by this DART collector; do not claim never-seen anywhere
in the broader project. Never replace failed samples with easier companies. Once inspected, a holdout
becomes a regression sample, not a fresh independent holdout.

Discover corp IDs only from official DART company-name query, exact displayed-name match and unique8digit
openCorpInfoNew link. No guessed mappings. Preserve discovery request/response hash and IDs; if ambiguous,
report IDENTITY_UNRESOLVED and retain case. No new API key or credentials.
Use existing diagnostic pipeline/limits. Preserve baseline receipts before changes and rerun those same
inputs after generic fixes. Final untouched holdout run once after fixes; failures stay in denominator.
Measures: catalog coverage, identified selected period, cover/scope/body availability, correction blockers,
source/period distortion, calls/bytes/elapsed. Successful fetch != insight quality or model/BUY validation.
No company-code special cases. Correct parser/edition logic via minimal generalized changes with red tests,
independent review and full regression. KR DART results do not establish US/SEC/general TV correctness.

## Next news module scope
Investigate existing SDK guard + actual RF oversized Perplexity response and implement bounded discovery
fallback only if authoritative record URLs/date hints can be preserved without treating search synthesis
as verified original evidence. No unlimited token/byte caps or additional LLM. Preserve original omission
and fallback outcomes separately. Exact contract to be fixed after architecture inspection.
# Reproducible cohort diagnostic

tools/validate_dart_cohort.py + tests/test_validate_dart_cohort.py. Manifest preregistered at
tools/fixtures/dart_generalization_20260920.json; baseline must precede parser modifications.
CLI requires --live --group(control/development/holdout) --out NEWfile (exclusivecreation), optional
--manifest path. No-live returns2 before manifest/client/collector work. Existingoutput refusesbeforeHTTP.
Input <=10 named cases/group, name/ticker/sector boundedstrings, fixedawarecutoff/date/scope.

Resolve DARTcorp via fixed official POST sameperiodic filters/window, textCrpNm exactly manifestname,
textCrpCik='',autoSearchCorp=Y. Stream max1MiB/15s, identityencoding, no redirects/proxyenv. Corporate
anchors from official tbody openCorpInfoNew exact8digit; require exact whitespace-normalized displayname
and unique ID. No guessedcorpIDs/ticker verification claims. Record metadata/hash, not rawresponse.
Identity failure stays in denominator withoutcollector invocation. Casessequential; sourceHTTPbounded
by existingcollectordefaults. Catch percase errors static; cancellationpropagates. No rawbody/previews
in output (reuse probe _receipt). Record head/collectorhash, metrics, selectedperiod/source IDs/status,
discoveryhash/ID, elapsed, and eachcase structuredreceipt for subsequentdiagnosis.

Tests fakeHTTPX + injectedcollector, exactnames/multipleIDs/none, identityfail skips onlyonecase,
partial/failedcasesretained, no-live/exclusiveoutput, unknownsourcefields no factclaims. Firstlive
development5, inspect failures, genericregressions beforefix, replay same5, then untouchedholdout3.
No threshold/code tuned on holdout without marking it regression and declaring a new holdout separately.

## Baseline feedback (before holdout)
5development baseline:2COMPLETE, 현대차name identityunresolved,KB/LGpartialduecorrections.
Generic identity fix: official search form explicitly labels textCrpNm as 회사명/종목코드; ticker005380
query returns unique 현대자동차 corp00164742. Official /js/common.js openCorpInfoNew sends selectKey
to /dsae001/selectPopup.ax; actual profile has 종목코드005380. Use exact-name first, then officialticker
query only when unresolved/ambiguous (no handwrittenaliases), require uniquecorp and verify exactticker
in officialpopup. If name lookupunique but profiletickerconflicts, do not silentlyswitchcompany.
Keep requestedname and officialdisplayname separately. Bound all lookup/profile requests together to15s,
max3calls,1MiB each; save response hashes/queries notbody. Not a cross-provider factcertification.

Potential correction refinement, separately review beforeeditingcollector: coverdate in corrected report
can be originaldate older than receiptdate. For is_correction only, allowcoverdate<=receiptdate while
keeping catalog submitted_date as availability (do not overwrite with coverdate). Cover verifiedperiod
must still match reportedlabel andbodyrequested scope. Unknownlineage correction older than verified
newerprimary period may limit annual supplement but must not suppress that currentprimary. Same/newer
or unknownperiod correction stillblocks. Preserve all unresolvedcorrectionIDs and PARTIALstatus; no
automatic amendment_of inference or false historical/global completeness. No companyspecialcases.

## Approved architecture refinement: official family evidence
Observed main HTML select#family has exact options value=rcpNo=<14digits>, labels
2026.06.19 [정정] 사업보고서 ->2026.03.24 [정정] 사업보고서 ->2026.03.13 사업보고서.
Use only unique family selector, not att/doc attachment selectors. Validate uniqueIDs, samekind,
strictlydescending distinct literal dates, one original root at end; currentID/date/correctionflag
must matchcatalog. Same-daydate-only order unresolved. No receipt-digit dates or manufacturedcandidateIDs.
Store boundedfamilymembers and mainresponsehash as provenance; no newnetworkcalls.
Resolve correction immediatepredecessor only when bothcatalogrecords actuallyfetched and exact
kind/periodstart/end/requestedscope VERIFIED inbody match, familydates equal respectivecatalog dates,
and retainedcoverdate matches one familyedition date or ownreceiptdate. Parsefamily errors recorded
separately frombodyavailability; validbody canremainavailable yet lineageunresolved. Unknowncorrections
are NOT passedasoriginals to pureselector; exclude them fromcandidate inputs and apply explicit guards.
Use existingselector withverified amendment_of chain. Parentreceiptsmissing/notverified => nofakeedge.

Keep catalog submitted_date immutable; store cover_submitted_date separately. For original exactmatch;
forcorrection allow earliercoverdate, neverfuture, kind/label checks unchanged. Do not certify numbers.
Unresolved strictlyolder corrections maystay in unresolved_older_corrections withoutclearing knownnewer
primary, comparing verifiedcoverperiod ifknown or coarseofficialcatalogYYYY.MM ONLYforoldness. No
inventedday/periodstart. Same/newer/unknown month or known metadata/identity/familyintegrity conflict
stillblocks. Applyguard independently to annual supplement; unresolvedsame/newerannual cannot fall
back to olderannual. KeepPARTIAL ifanyunresolved/bodygaps; latest_confirmed staysfalse.
Missingpage coverage stillblocksallselectedIDs. No defaultrequest/filing/byte budgets raised.
Tests: three-editionchain, preservedreceiptdate, coverfuturedate/noncorrectionmismatch, unknownfamily
currentvsolder, same-day, missingparent/crossscope/crossperiod/duplicate/cycle, unreaddatedold14rowcap,
futurefamilyedition ignored aspredecessor, currentunreadamendment nooldfallback, annualguard.

## First holdout failure retained; second holdout frozen before further fixes
Firstholdout3: 유한양행complete,에스엠primarybutpartial,메리츠primaryblocked. Do notcall re-evaluation
of these3 an untouchedholdout again. Freeze newcollector-unseen 한국전력015760/NAVER035420/신한지주055550
before attachment-role fix; no networklookup untilafterfixes/review.
ActualMeritz catalog prefix [첨부정정] (span title says 첨부내용변경) is NOT a full periodic-body edition.
rcp20260406004144 viewer is auditreport,20260318001549 is charter. family selector appropriately lists
related original periodicreport, not those attachment receipts. Treating absence as integrityconflict iswrong.
Preserve correction_type body/attachment explicitly from exactcatalogprefix; attachmentsremain visible,
notpassed as periodic originals/editionchains and notconsumed as a financialbody slot. No forgedfamilyedge.
Their unread material remains an explicitattachment gap. Strictlyolder catalogperiod attachment doesn't
hide a verifiednewerperiod primary, but same/newer/unknown attachments block primary; matching/newer
annual attachment suppresses annualsupplement. KeepPARTIAL and original budgets; do notclaimattachments
have no economic significance or that all reports areverified. Same-day body amendments remainunresolved.
# P4c bounded discovery fallback (not full news analysis restoration)

Existing active profile only: cores/llm/tool_result_budget.py calls admit(result) after MCP result.
Add optional keyword-only server_name/tool_name to admit. wrap passes context. Direct admit(result)
callers and non-Perplexity/small/nontext/error/blocked tools retain behavior. Eligible tools only
perplexity_ask/perplexity_search on server perplexity, successful text-only CallToolResult and
original per-result overflow. No global flags, new requests, model calls or higher budgets.

New pure helper prism_core/search_discovery_fallback.py with no SDK dependency returns optional
JSON-ready envelope. It accepts shallow text strings + structuredContent dict and original size/hash.
Supported shapes: top-level search_results:[{url,title?,date?}], citations:[urlstrings], response:str;
content text may be one JSON object with those same keys or prose ending standalone numbered references
matching [N] https://... (whole-line only). No recursive arbitrary object traversal. Must support actual
RF text == structured.response duplicated form, nosearch_results field. Raw numerical/causal prose omitted.

Limits before scanning: original serializedsize <=131072B; at most4 textblocks, each <=65536B;
at most50 source candidates gathered and max10 outputrecords; stop scanning after1000lines. Oversize
unknown parser inputs fail closed or disclosed limit without unbounded work. ExactURL dedupe firstseen.
Reuse public_url() unchanged: no query stripping or new URL allowances. Do not output unsafe locators,
source prose, credentials, title/date inferred from prose/URL. For v1 OUTPUT ONLY reference+url;
explicit structured title/date hints deferred to avoid conflating provider semantics. No source/issuer
validation status. Wrong-company URLs remain unverified discovery candidates, never facts.

Envelope statusUNKNOWN, reasonresult_limit, retrievalreturned, original serialized_utf8_bytes/sha256,
deliverydiscovery_only, answer_withheldtrue, sources:[{reference,url}], source_content_verifiedfalse,
issuer_matchUNKNOWN, publication_timeUNKNOWN, filtered_count and candidates_seen/truncated indicators,
static action saying verify issuer/original content/publication time; withheldanswer != nonews.
Disagreeing content/structured discovery adds representation_ambiguity=true; do not silently declare
equivalent. Dedup texts/candidateURLs without promoting cross-representation agreement to validation.

Guard builds fresh CallToolResult one TextContent JSON, original structuredContent/meta removed.
Measure entire fresh MCP envelope via existing _serialized_size. Remove whole trailing records until
fits per-result AND remaining cumulative bytes (do not sliceURLs). At leastone record needed. Charge
whole fallback to evidence_bytes; new fallback_count and original_withheld_bytes counters, not a free
notice bypass. Current refusal path remains <=512B if noeligiblecandidate/room/unsupportedcase.
Synchronous admit invariant retained for parallel callers; noawait in admission. No fallback if current
cumulative budget already exhausted. Keep originalreason/hash/size even when links admitted.

Tests RED/GREEN with actual installedSDK both structured settings, runner/backend scope/OFF regression,
rawprose lost but URLs retained, private/bad/queryURLs filtered with counts, duplicate/ref conflicts,
input/line/candidate/output limits, non-Perplexity and smallidentity unchanged, error/metadata/image,
tight per-result & cumulative budgets exact measured, shared budget parallel admission, issuer UNKNOWN,
actual stored RF replay privately (no fullunverified answer committed). This recovers locators only:
article retrieval/source verification/causal explanation and tool-free news_usable branch remain pending.
