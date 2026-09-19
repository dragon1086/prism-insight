# Continue P3b / P4

User asks to clarify data-use restrictions and continue remaining work. General TV terms restrict
data-fed non-display/algorithmic use, not only an order API; official MCP explicitly supports AI/code
analysis. We must not assert a final MCP-specific legal interpretation or freeze independent work.
No operations/trades/deployment or live credential modification during this phase.

## P3b: credential supplier
One new module + tests; integrates with existing P2/P3a only through injected supplier factory.
Factory construction/OFF zero I/O. ON-only supplier reads explicit private store; no auto registration,
login, metadata discovery, retries, dependencies, or global config changes.
Preserve existing credential JSON format and unrelated entries. Exactly one matching server name/url/issuer.
Cross-process stable sibling lock, reread after lock, protected atomic token rotation, static errors.
Fresh (>600s) grant returns without network or credential/state rewrite; creating the stable private
sibling lock is allowed. Expiry comparisons UTC, decision clock injected.

Format authority (read-only diagnostic, do not run against live auth):
`/Users/rocky/.cokacdir/workspace/8iglw6jg/tradingview_refresh_session.py`.
Synthetic shape: {"opaque_entry_key":{"server_name":"tradingview",
"server_url":"https://mcp.tradingview.com/mcp","issuer":"https://www.tradingview.com",
"access_token":"SYNTHETIC_ACCESS_ONLY","refresh_token":"SYNTHETIC_REFRESH_ONLY",
"client_id":"SYNTHETIC_CLIENT_ONLY","expires_at":1790000000000},"other":{...}}.
Top-level JSON object preserves arbitrary unrelated entries. Matching entry must be exactly one dict
whose server_name/server_url/issuer equal these three literal constants. expires_at is positive integer
Unix milliseconds (not bool/string); access token printable nonspace ASCII <=8192 and expiry valid.
Fresh access needs no refresh_token/client_id; expiring access requires both validated before marker.
Fixed token endpoint: https://www.tradingview.com/mcp/oauth/token (current public unauthenticated
OAuth discovery also confirmed issuer/token endpoint and refresh_token grant, 2026-09-19).
Generation SHA256 of canonical JSON containing ONLY selected entry's server_name/server_url/issuer/
client_id/access_token/refresh_token (missing optional keys represented null), sorted keys and compact
JSON. Never hash arbitrary metadata or expires_at: metadata-only/expiry-only edits cannot reset an
ambiguous refresh. Marker is checked before fresh-grant return. Unrelated entries are preserved.
Sibling files named <credential-basename>.lock and <credential-basename>.refresh-state.json;
marker JSON exactly {"version":1,"generation":<sha256>,"state":"IN_FLIGHT"}.
Marker is not silently removed/reset after error. Changed selected entry (not unrelated entries) unlocks.
Store/lock/state regular, owner-only, owner uid, no symlink/hardlink; directory owner-private.
File limit256KiB, JSON duplicate/nonfinite rejected. Default refresh fixed HTTPS token endpoint,
HTTPX trust_env=false/follow_redirects=false, one POST, bounded body64KiB and 20s timeout.
Responses require Bearer token type, printable nonspace access/refresh/client_id <=8192, expiry
positive integer >30s and <=86400; optional replacement refresh token must be valid if present.

Persistent nonsecret attempt marker: before any refresh POST, atomically save selected-entry generation
SHA256 + state=IN_FLIGHT in private sibling state file under same lock. Same generation marked inflight
blocks a second attempt, including after process death/ambiguous response. Changed credential generation
allows a new attempt. No automatic cooldown retry of possibly rotated token. Success atomically fsyncs
new store before returning a grant; old marker is harmless because committed generation changes.
Both marker and credential replacements require fsync(tempfile) then os.replace then fsync(directory fd).
Marker directory-fsync failure prevents POST; credential directory-fsync failure returns no grant.
Failure injection covers both boundaries and leaves marker intact (no uncertain retry).
Unknown server-side rotation after response loss cannot be guaranteed recoverable; explicit failure
requires operator reauthorization/replacement outside this module. Do not silently clear marker.
Legacy helper must not run concurrently (it does not share lock). Detect noncooperating store content
change before replace, refuse overwrite; this cannot recover an already rotated secret.

API make_credential_supplier(path, *, http_client_factory=None, clock=None,
lock_timeout_seconds=5, refresh_timeout_seconds=20) -> async callable returning TVAccessGrant.
No generic plugin storage framework. Use directory fd + nofollow relative opens, flock nonblocking
with bounded async sleep, bounded file operations run in to_thread but drain operation on cancellation
to avoid races/orphan writers. Once transaction begins marker/network/commit task is owned/shielded
and drained before rethrow cancellation; no bare unowned background task. Total time cooperative,
safe cleanup may exceed P2 nominal timeout. Filesystem hang/process kill not a hard-bound promise.
Reuse context-local P3a logging guard. Secret-bearing errors sanitized; no grant until durable commit.

## P4a: deterministic periodic filing selection
Independent pure module and tests, plus TV document-discovery field preservation needed to feed it.
Do not edit existing offline filing_selection.py (it selects excerpts from an already chosen document).
Choose latest known available financial reporting period, then correct edition of that filing;
annual reports supplement missing detail, never replace a newer half/quarter by default.
Current TV listing has reported(event timestamp), fiscal_year/period, category, symbols and exact view IDs;
none proves filing-publication timestamp or an unambiguous calendar period. Preserve discovery fields
without manufacturing public filing facts. Verified normalized records are a separate input contract.
No source statistics/news trading claims from snippets. Future and ambiguous publication, mismatched
entity/scope, correction link conflicts, inaccessible latest body, incomplete lists remain explicit.

P3b/P4a are independently implementable; actual report/BUY runtime integration and live source
validation remain gated followups. No trade/selection/score/prompt changes in these modules.
# Tests before implementation

P3b RED then GREEN: OFF no path/factory/SDK calls; fresh no HTTP/write; strict store/entry/expiry
validation; symlink/hardlink/mode/owner; exactly one refresh under same-process and subprocess lock;
re-read after lock; unrelated fields; optional rotated token; durable marker before POST; ambiguous
failure retry blocked until generation changes; bad/oversize response; cancellation before/in/after
HTTP & commit drain; no orphan tasks/lock; external writer detected; raw secrets absent logs/traceback;
actual P2/P3a composition with synthetic store/MockTransport, no network. Test failures include static
AUTH_* reasons. Can't claim process kill/network loss can never lose rotation.

P4a: latest half-year beats older annual, correction of older year cannot beat latest half,
future correction excluded, current correction unread does not silently revert, explicit relation chains,
same-day date-only publication ambiguous, previous date-only eligible with upper-bound label, UTC/DST,
list incomplete prevents global-latest claim, wrong issuer/scope, missing period/publication/URL,
conflicting duplicate IDs, permutation invariance, annual supplement label distinct from base.
Actual TV-shaped listing regression must retain category/fiscal labels/symbol membership/total
without upgrading reported to published. Existing normalizer compatibility tests stay green.
# P4a exact filing selector contract

Pure `prism_core/filing_catalog.py`; dataclass FilingCandidate fields:
filing_id/entity_id/source_url (strings), kind ('annual','interim','quarterly'),
period_start/period_end (date or None), scope ('consolidated','standalone' or None),
published_at (aware datetime OR date OR None), publication_verified (literal bool),
amendment_of (str or None), body_status ('available','unread','unavailable').
Source URL is locator/provenance, not independent proof. Validate using existing public_url to avoid
inventing a second URL sanitizer. Selector only uses normalized metadata and returns IDs; no fetch/LLM.

`select_periodic_filings(candidates, *, entity_id, scope, decision_at, market_timezone,
listing_complete=False)` returns dict: status, primary_id, latest_candidate_id, annual_supplement_id,
latest_confirmed, reasons, exclusions[{filing_id,reason}], publication_bounds per eligible ID,
fact_validated=false. Input list/tuple <=1000; invalid selector config raises ValueError.
Duplicates exact collapse; same ID with differing metadata yields AMBIGUOUS and no selected IDs.
Exact target entity only; other entities and nonperiodic kinds excluded. Missing/wrong expected scope:
wrong known scope excluded; missing scope unresolved and blocks latest-confirmed.

Publication: requires publication_verified is True. Naive/missing/unverified => unresolved.
Aware timestamp normalized UTC <=decision eligible; later excluded FUTURE_PUBLICATION.
Date-only: if prior to cutoff local date, eligible with precision=date and exclusive upper bound at
next local midnight; same cutoff date ambiguous, later date future. Never fabricate receipt clock.
Record period_end after verified local publication date => invalid/unresolved, not actual report.
Missing/reversed periods/unsafe URL/body_status => unresolved. Metadata unresolved target candidate
blocks latest-confirmed (keep best confirmed candidate as best-known only; status INCOMPLETE).

Corrections require explicit amendment_of chain present in provided list, no cycles, matching
entity/scope/kind/period boundaries, publication bound order unambiguous. Future corrections do not
supersede prior editions at historical cutoff. Orphan/conflicting chain => unresolved. Multiple sibling
corrections without explicit ordering are AMBIGUOUS even if publication times differ: cannot assume
one replaces all correction scopes. Follow terminal eligible correction. Newer edition may be unread;
do not fall back silently to prior body. Whole-document replacement semantics are an explicit input
assumption, not extraction of partial corrected fields. Any ambiguity touching current candidate blocks
selection. An unresolved amendment_of chain connected to the current best-known series (e.g. unknown
publication) sets UNRESOLVED_CURRENT_EDITION, primary_id=None, and records blocked_by IDs. Do not use
the older body as current. Unresolved unrelated older series prevents latest-confirmed but retains best-known ID.

Selection: highest period_end first; older-period amendment cannot override newer half/quarter.
For same period_end and different unrelated original report IDs, AMBIGUOUS (no arbitrary kind priority).
If eligible terminal body available => primary_id selected; otherwise primary_id=None,
latest_candidate_id preserved, status LATEST_BODY_UNAVAILABLE (no annual/old fallback as primary).
Annual supplement is most recent eligible annual edition with body available and period_end <=primary
period_end, only when primary not annual and no ambiguity; labeled supplementary, never current basis.
Status priority AMBIGUOUS, UNRESOLVED_CURRENT_EDITION, LATEST_BODY_UNAVAILABLE, NO_ELIGIBLE, INCOMPLETE, SELECTED.
latest_confirmed only complete supplied listing + no unresolved candidates + usable selected edition.
Completeness is caller evidence, not inferred from number of returned rows. No global latest guarantee
from a truncated/filtered search. Inputs must never mutate; output independent of input order.

TV discovery preservation in existing tradingview_evidence.py: keep document category, fiscal_year,
fiscal_period and nested symbol membership, reported/event timestamp, exact view IDs and provider total.
Mark fields as discovery-only and publication/period mapping unknown. Do NOT map Q2 to June or reported
to filing submission. Coverage includes nested symbols. total not enough to certify official completeness.
Existing 29 normalizer regressions plus realistic saved-shape fixtures (synthetic values) must stay green.

Real RF primary-source observation (not fixture-made proof): DART rcpNo20260814001631 cover
2026-01-01..2026-06-30; submission date2026-08-14; no exact verified time. Full official catalog completeness
not established. Can call it verified newer primary candidate, not proven absolute latest. Retain evidence
notes and actual official URLs; never use secondary mirror timestamps as official time.
