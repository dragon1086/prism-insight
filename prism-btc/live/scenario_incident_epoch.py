"""One incident's administrative separation. Never exchange or settlement proof.

Cutover is intentionally not exposed by the runner. A trusted deployment probe
must establish scheduler/worker quiescence before calling it; the local mutex
cannot fence an unmodified process already inside its transport.
"""
import json
import logging
import math
import os
from contextlib import suppress
from time import perf_counter_ns as _phase_clock_ns
from pathlib import Path

from live.scenario_management_quarantine import digest, _append, _events, ENDPOINT
from live.shared_entry_coordinator import mutation_lock

KIND = 'incident_epoch_v1'
ENTRY_KIND = 'incident_entry_epoch_v1'
ENTRY_SCENARIO = 's_55d333c6722dba9c167c4c34'
ENTRY_UNKNOWN = 'sc_b7cafdb91c0d43da95926732c1a850b4'
HALT = 'incident_epoch_halt_v1'
MONITOR_RELEASE = 'incident_monitor_release_v1'
TRANSPORT = 'incident_epoch_transport_v1'
FINANCE_CURSOR = 'incident_epoch_finance_cursor_v1'
SCENARIO = 's_bd46ee3659f6ee1a5c630d1f'
UNKNOWN = 'sc_c25e58e9459f4bf7de1d0d718cb1afd8'


def code_digest():
    import hashlib
    root = Path(__file__).parent
    return digest({p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                   for p in sorted(root.glob('scenario_*.py'))})


def require(ok, reason):
    if not ok:
        raise ValueError('incident_epoch_' + reason)


def receipts(conn):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='llm_scenario_broker_evidence'").fetchone():
        return []
    rows = conn.execute('SELECT kind,body FROM llm_scenario_broker_evidence WHERE kind IN (?,?) ORDER BY rowid', (KIND, ENTRY_KIND)).fetchall()
    events = [json.loads(body) for kind, body in rows if kind == KIND]
    extensions = [json.loads(body) for kind, body in rows if kind == ENTRY_KIND]
    require(len(events) <= 1 and len(extensions) <= 1, 'duplicate_cutover')
    require(not extensions or events, 'predecessor_missing')
    result = []
    for event in events + extensions:
        require(event.get('sha256') == digest(event.get('body')), 'receipt_corrupt')
        value = event['body']
        if result:
            require(value.get('previous_receipt_sha256') == digest(result[-1])
                    and value['epoch_id'] != result[-1]['epoch_id']
                    and value['scenario_id'] != result[-1]['scenario_id']
                    and value['approval']['main_uid'] == result[-1]['approval']['main_uid'], 'receipt_chain_changed')
        result.append(value)
    return result


def receipt(conn):
    values = receipts(conn)
    return values[-1] if values else None


def epoch(conn):
    from live.scenario_control import read_control
    control = read_control(conn)
    result = receipt(conn)
    if result:
        require(control and control.get('version') == 2
                and control.get('epoch_id') == result['epoch_id']
                and control.get('main_uid') == result['approval']['main_uid']
                and control.get('approval_sha256') == result['approval_sha256'], 'control_changed')
        return result['epoch_id']
    require(not control or control['version'] == 1, 'receipt_missing')
    return None


def _snapshot(broker, scenario_id=None):
    scenario_id = SCENARIO if scenario_id is None else scenario_id
    conn = broker.conn
    queries = (
        ('llm_scenario_intents', 'PRAGMA table_info(llm_scenario_intents)',
         'SELECT * FROM llm_scenario_intents WHERE scenario_id=? ORDER BY rowid'),
        ('llm_scenario_children', 'PRAGMA table_info(llm_scenario_children)',
         'SELECT * FROM llm_scenario_children WHERE scenario_id=? ORDER BY rowid'),
        ('llm_scenario_reductions', 'PRAGMA table_info(llm_scenario_reductions)',
         'SELECT * FROM llm_scenario_reductions WHERE intent_id IN (SELECT id FROM llm_scenario_intents WHERE scenario_id=?) ORDER BY rowid'),
        ('llm_scenario_entry_contracts', 'PRAGMA table_info(llm_scenario_entry_contracts)',
         'SELECT * FROM llm_scenario_entry_contracts WHERE scenario_id=? ORDER BY rowid'),
        ('llm_scenario_pending_leases', 'PRAGMA table_info(llm_scenario_pending_leases)',
         'SELECT * FROM llm_scenario_pending_leases WHERE scenario_id=? ORDER BY rowid'),
    )
    rows = {}
    for table, schema_query, query in queries:
        columns = [r[1] for r in conn.execute(schema_query)]
        require(bool(columns), 'schema_missing')
        rows[table] = [list(row) for row in conn.execute(query, (scenario_id,))]
    rows['management'] = _events(conn, 'management_quarantine_v1')
    rows['management_exits'] = _events(conn, 'management_quarantine_exit_v1')
    return rows


def pending(conn, statuses=('PENDING', None)):
    """Exclude only immutable exact incident rows, never all historical UNKNOWNs."""
    excluded = {r[0] for value in receipts(conn) for r in value['snapshot']['llm_scenario_intents']}
    rows = conn.execute('SELECT id,status FROM llm_scenario_intents').fetchall()
    return [ident for ident, status in rows if status in statuses and ident not in excluded]


def unresolved(conn):
    rows = conn.execute('SELECT DISTINCT status FROM llm_scenario_intents').fetchall()
    return pending(conn, tuple(s for s, in rows if s != 'TERMINAL'))


def sealed(conn, scenario_id):
    return any(scenario_id == value['scenario_id'] for value in receipts(conn))


def historical(conn):
    return [dict(scenario_id=value['scenario_id'], status='HISTORICAL_UNSETTLED',
                 excluded_from_performance=True, cutover_at=value['cutover_at'],
                 started_at=value['old_state']['active']['created_at']) for value in receipts(conn)]


def record_late(broker, evidence):
    body = dict(reason='historical_evidence_changed', evidence=evidence)
    if not any(digest(e.get('evidence')) == digest(evidence) for e in active_halts(broker.conn)):
        _append(broker.conn, HALT, body, broker.clock())
        broker.conn.commit()


def active_halts(conn):
    halts = _events(conn, HALT)
    releases = _events(conn, MONITOR_RELEASE)
    if releases:
        event = releases[-1]
        require(event.get('sha256') == digest(event.get('body')), 'release_corrupt')
        body = event['body']
        require(body.get('epoch_id') == epoch(conn), 'release_epoch_changed')
        if body.get('halts_sha256') == digest(halts):
            require(all(h.get('evidence', {}).get('status') == 'HISTORICAL_MONITOR_OR_COVERAGE_GAP'
                        and set(h.get('evidence', {})) <= {'status', 'error_type', 'reason'} for h in halts),
                    'release_contains_financial_evidence')
            return []
    return halts


def halted(conn):
    return bool(active_halts(conn))


def release_monitor_gap(broker, *, expected_halts_sha256):
    """Explicit operational release after complete revalidation; never called by runner."""
    with mutation_lock(broker.conn):
        current = epoch(broker.conn)
        require(current and current == broker._incident_epoch_bound, 'stale_worker')
        value = receipt(broker.conn)
        require(broker.expected_main_uid == value['approval']['main_uid']
                and broker.session.endpoint == ENDPOINT and broker.environment == 'demo', 'epoch_identity_changed')
        halts = _events(broker.conn, HALT)
        require(halts and digest(halts) == expected_halts_sha256, 'release_halts_changed')
        require(all(h.get('evidence', {}).get('status') == 'HISTORICAL_MONITOR_OR_COVERAGE_GAP'
                    and set(h.get('evidence', {})) <= {'status', 'error_type', 'reason'} for h in halts),
                'release_contains_financial_evidence')
        observed = monitor(broker, refresh=True)
        account = flat_probe(broker)['account']
        require(observed and 0 <= broker.clock()-observed['started_at'] <= 10, 'release_observation_expired')
        require(digest(_events(broker.conn, HALT)) == expected_halts_sha256, 'release_new_evidence')
        require(float(account['position']['size']) == 0 and not account['open_orders']
                and not account['legacy_fenced'] and not unresolved(broker.conn), 'release_not_flat')
        for historical in receipts(broker.conn):
            snapshot = _snapshot(broker, historical['scenario_id'])
            expected = dict(historical['snapshot'])
            expected['management'] = expected['management'] + ([historical['management_revocation']]
                if historical.get('management_revocation') else [])
            require(snapshot == expected, 'release_history_changed')
        new_risk_blocked(broker)  # Validate unchanged recovery permissions before releasing this halt.
        require(digest(_events(broker.conn, HALT)) == expected_halts_sha256, 'release_new_evidence')
        require(0 <= broker.clock()-observed['started_at'] <= 10, 'release_observation_expired')
        body = dict(epoch_id=current, halts_sha256=expected_halts_sha256,
                    source_sha256=observed['source_sha256'], observed_at=observed['started_at'],
                    released_at=broker.clock())
        _append(broker.conn, MONITOR_RELEASE, dict(body=body, sha256=digest(body)), broker.clock())
        broker.conn.commit()
        return body


def monitor(broker, *, refresh=False):
    """One explicit bounded observation; context and POST never perform REST.

    Exact terminal proofs are immutable. Their late fills/fees are watched by
    the complete rolling financial stream; only the original UNKNOWN needs an
    exact order lookup. The cursor is coverage, never settlement finality.
    """
    value = receipt(broker.conn)
    if not value:
        return
    cached = getattr(broker, '_incident_observation', None)
    if not refresh and cached and cached['epoch_id'] == value['epoch_id'] and 0 <= broker.clock()-cached['started_at'] <= 10:
        return cached
    started = broker.clock()
    broker._incident_observation = None
    from live.scenario_broker import _MONITOR_CALL_TIMING
    timing = dict(calls=[], dropped=0, measurement_failures=0, phase_ms={})
    phase_at = None
    def mark(name):
        nonlocal phase_at
        try:
            now = _phase_clock_ns()
            if type(now) is not int or now < 0 or (phase_at is not None and now < phase_at):
                raise ValueError('phase_clock_invalid')
            if phase_at is not None:
                timing['phase_ms'][name] = round(timing['phase_ms'].get(name, 0) + (now-phase_at)/1_000_000, 3)
            phase_at = now
        except Exception:
            phase_at = None
            timing['measurement_failures'] += 1
    mark('start')
    token = _MONITOR_CALL_TIMING.set(timing)
    try:
        observed_bodies = []
        all_cursors = _events(broker.conn, FINANCE_CURSOR)
        mark('cursor_load')
        values = receipts(broker.conn)
        mark('receipt_load')
        for cursor in all_cursors:
            require(cursor.get('sha256') == digest(cursor.get('body'))
                    and cursor['body']['epoch_id'] in {r['epoch_id'] for r in values}, 'finance_cursor_corrupt')
        mark('cursor_validate')
        for value in values:
            children = broker.children(value['scenario_id'])
            child = next(c for c in children if c['link_id'] == value['approval']['unknown_link_id'])
            mark('children_lookup')
            broker._query_child(child)
            mark('child_query')
            require(0 <= broker.clock()-started <= 10, 'monitor_child_lookup_deadline')
            cursors = [r for r in all_cursors if r['body']['epoch_id'] == value['epoch_id']]
            previous = cursors[-1] if cursors else None
            if previous:
                require(previous['sha256'] == digest(previous['body'])
                        and previous['body']['epoch_id'] == value['epoch_id'], 'finance_cursor_corrupt')
            through = previous['body']['end_ms'] if previous else value['financial_end_ms']
            end_ms = int(started*1000)
            require(through <= end_ms and end_ms-through <= 7*86400*1000, 'financial_coverage_gap')
            start_ms = max(value['financial_start_ms'], through-86400*1000, end_ms-7*86400*1000)
            finance = historical_finance(broker, start_ms, value['financial_end_ms'], children,
                query_end_ms=end_ms, initial=False)
            # Re-read the fixed original interval, not an ever-growing old→now
            # window: late fee/fill corrections can retain their original timestamp.
            archive = (finance if start_ms == value['financial_start_ms'] and end_ms == value['financial_end_ms'] else
                historical_finance(broker, value['financial_start_ms'], value['financial_end_ms'],
                    children, initial=False))
            if archive != value['financial_sources']:
                record_late(broker, dict(archived_financial_sources=archive, scenario_id=value['scenario_id']))
            baseline = {digest(row) for rows in value['financial_sources'].values() for row in rows}
            fresh = {kind: [row for row in rows if digest(row) not in baseline] for kind, rows in finance.items()}
            if any(fresh.values()):
                record_late(broker, dict(financial_sources=fresh, scenario_id=value['scenario_id']))
            mark('financial_observation')
            require(0 <= broker.clock()-started <= 10, 'monitor_finance_deadline')
            body = dict(epoch_id=value['epoch_id'], start_ms=start_ms, previous_end_ms=through,
                        end_ms=end_ms, source_sha256=digest(finance))
            _append(broker.conn, FINANCE_CURSOR, dict(body=body, sha256=digest(body)), broker.clock())
            broker.conn.commit()
            observed_bodies.append(body)
        sources = account_sources(broker)
        children = broker.children()
        owned_ids = {c['order_id'] for c in children if c['order_id']}
        owned_links = {c['link_id'] for c in children if c['link_id']}
        active = broker._active()
        for source in sources:
            for position in source.get('positions', {}).get('result', {}).get('list', []):
                require(float(position['size']) == 0 or (active and source['category'] == 'linear'
                    and source['scope'] == {'settleCoin': 'USDT'}
                    and position.get('symbol') == 'BTCUSDT' and position.get('positionIdx') == 0), 'foreign_position')
            for order in source.get('orders', {}).get('result', {}).get('list', []):
                # Our own order can be on the exchange before its id is saved: an entry's
                # attached stop exists from the fill, its row only after reconciliation.
                require(order.get('orderId') in owned_ids or order.get('orderLinkId') in owned_links
                        or (order.get('parentOrderLinkId') in owned_links and order.get('reduceOnly') is True
                            and order.get('stopOrderType') == 'StopLoss'), 'foreign_order')
        mark('account_observation')
        require(0 <= broker.clock()-started <= 10, 'monitor_account_deadline')
        broker._incident_observation = dict(epoch_id=receipt(broker.conn)['epoch_id'], started_at=started,
                                            source_sha256=digest(observed_bodies))
    except Exception as exc:
        mark('incomplete')
        reason = str(exc) if isinstance(exc, ValueError) and str(exc).startswith('incident_epoch_') else 'observation_failed'
        if reason not in {'incident_epoch_monitor_child_lookup_deadline',
                          'incident_epoch_monitor_finance_deadline',
                          'incident_epoch_monitor_account_deadline'}:
            record_late(broker, dict(status='HISTORICAL_MONITOR_OR_COVERAGE_GAP',
                                    error_type=type(exc).__name__, reason=reason))
        else:
            # A late observation only blocks this cycle (no fresh observation);
            # the next one re-reads from the last cursor, so nothing is skipped.
            with suppress(Exception):
                started_ms = (int(started * 1000) if type(started) in (int, float)
                              and math.isfinite(started) else None)
                logging.getLogger(__name__).warning('incident_monitor_call_timing started_ms=%s reason=%s calls=%s',
                    started_ms, reason, json.dumps(timing, separators=(',', ':'), allow_nan=False))
    finally:
        _MONITOR_CALL_TIMING.reset(token)
    return broker._incident_observation


def validate_quiescence(probe, approval, now):
    """Trusted host probe, not an operator-supplied 'no in-flight' boolean.

    Deployment owns complete process inventory and scheduler disable evidence.
    Inspect actual stopped PID identities here and bind the probe's raw evidence
    and compatible-launcher manifest to the separately approved deployment hash.
    """
    require(callable(probe), 'deployment_probe_required')
    proof = probe()
    require(isinstance(proof, dict) and digest(proof) == approval['quiescence_sha256'], 'deployment_proof_changed')
    require(0 <= now - proof['captured_at'] <= 10
            and proof['host'] == os.uname().nodename
            and proof['database'] == approval['database']
            and proof['scheduler_before'] and proof['scheduler_after']
            and proof['scheduler_before'] != proof['scheduler_after']
            and proof['process_inventory_before'] and proof['process_inventory_after']
            and proof['launcher_manifest']['epoch_protocol'] == 1
            and proof['launcher_manifest']['code_sha256'] == approval['code_sha256']
            and isinstance(proof['stopped_workers'], list), 'deployment_proof_incomplete')
    for worker in proof['stopped_workers']:
        require(type(worker['pid']) is int and worker['pid'] > 1 and worker['command']
                and worker['started_at'] and worker['termination_evidence'], 'worker_identity_missing')
        try:
            os.kill(worker['pid'], 0)
        except ProcessLookupError:
            continue
        require(False, 'old_worker_alive')
    return proof


def account_sources(broker):
    """Complete account-wide reads, including conditional/native order sources.

    Unsupported category/filter or incomplete pagination is a hard denial.
    Empty source results are not proof that the historical request was rejected.
    """
    from live.exchange_snapshot import read_complete
    started = broker.clock()
    broker._identity()
    evidence = []
    for category in ('linear', 'inverse', 'option', 'spot'):
        scopes = ({'settleCoin': 'USDT'}, {'settleCoin': 'USDC'}) if category == 'linear' else ({},)
        for scope in scopes:
            if category != 'spot':
                positions = read_complete(broker._call, 'get_positions', category=category, **scope)
                require(positions is not None, 'positions_incomplete')
                evidence.append(dict(category=category, scope=scope, positions=positions))
            filters = (None,)  # Bybit's omitted filter includes every order kind.
            for order_filter in filters:
                params = dict(category=category, **scope)
                if order_filter:
                    params['orderFilter'] = order_filter
                orders = read_complete(broker._call, 'get_open_orders', **params)
                require(orders is not None, 'orders_incomplete')
                evidence.append(dict(category=category, scope=scope, order_filter=order_filter, orders=orders))
    require(0 <= broker.clock() - started <= 10, 'account_sources_stale')
    return evidence


def historical_finance(broker, start_ms, end_ms, children, *, query_end_ms=None, initial=True):
    """Append-only financial observations, not a reconstructed settlement."""
    from live.scenario_broker import read_evidence_pages, _verify_funding_execution
    query_end_ms = end_ms if query_end_ms is None else query_end_ms
    require(0 <= start_ms <= query_end_ms and query_end_ms-start_ms <= 7*86400*1000, 'financial_window_unavailable')
    ids = {c['order_id'] for c in children if c['order_id']}
    links = {c['link_id'] for c in children}
    executions = read_evidence_pages(broker._call, 'get_executions', category='linear',
        symbol='BTCUSDT', startTime=start_ms, endTime=query_end_ms, limit=100)
    transactions = read_evidence_pages(broker._call, 'get_transaction_log', accountType='UNIFIED',
        currency='USDT', startTime=start_ms, endTime=query_end_ms, limit=50)
    if initial:
        for row in executions:
            if row.get('execType') == 'Funding':
                matches = [t for t in transactions if t.get('tradeId') == row['execId']]
                require(row.get('symbol') == 'BTCUSDT' and len(matches) == 1, 'funding_pair_missing_or_ambiguous')
                _verify_funding_execution(row, matches[0], start_ms, query_end_ms)
            else:
                require(row.get('orderId') in ids, 'unowned_execution')
    # The bounded pre-cutover interval catches late old flows even without an
    # orderId; exact old IDs catch corrections supplied after the cutover time.
    return dict(executions=sorted([r for r in executions if r.get('orderId') in ids
        or r.get('orderLinkId') in links
        or start_ms <= int(r['execTime']) <= end_ms], key=digest),
        transactions=sorted([r for r in transactions if r.get('orderId') in ids
            or r.get('orderLinkId') in links
            or start_ms <= int(r['transactionTime']) <= end_ms], key=digest))


def flat_probe(broker):
    started = broker.clock()
    evidence = account_sources(broker)
    for source in evidence:
        require(all(float(r['size']) == 0 for r in source.get('positions', {}).get('result', {}).get('list', [])), 'position_open')
        require(source.get('orders', {}).get('result', {}).get('list', []) == [], 'orders_open')
    observed = broker.capture_account()
    require(observed['exchange_flat'] and not observed['open_orders'] and not observed['legacy_fenced'], 'flat_untrusted')
    require(0 <= broker.clock() - started <= 10, 'flat_probe_stale')
    return dict(captured_at=started, account=observed, sources=evidence)


def cutover(broker, *, approval, quiescence_probe):
    """No runner calls this. Explicit residual-risk execution approval required.

    All helpers which commit or perform GETs run before BEGIN IMMEDIATE. No
    helper with an internal commit is allowed inside the atomic transition.
    """
    from live.shared_entry_coordinator import database_path
    from live.scenario_recovery import _stage_evidence, journal, ensure_schema
    with mutation_lock(broker.conn):
        conn = broker.conn
        existing = receipt(conn)
        if existing and existing['approval_id'] == approval['approval_id']:
            require(existing['approval_id'] == approval['approval_id']
                    and existing['approval_sha256'] == digest(approval), 'approval_reused')
            epoch(conn)
            return existing
        extension = existing is not None
        if extension:
            require(len(receipts(conn)) == 1
                    and approval.get('previous_receipt_sha256') == digest(existing)
                    and approval.get('scenario_id') == ENTRY_SCENARIO
                    and approval.get('unknown_link_id') == ENTRY_UNKNOWN
                    and approval.get('epoch_id') != existing['epoch_id'], 'approval_reused')
            epoch(conn)
            monitor(broker, refresh=True)
            require(not new_risk_blocked(broker), 'predecessor_not_verified')
        scenario_id = ENTRY_SCENARIO if extension else SCENARIO
        unknown_id = ENTRY_UNKNOWN if extension else UNKNOWN
        require(not conn.in_transaction, 'transaction_already_open')
        now = broker.clock()
        require(approval['scenario_id'] == scenario_id and approval['unknown_link_id'] == unknown_id
                and approval['main_uid'] == broker.expected_main_uid and broker.environment == 'demo'
                and approval['endpoint'] == broker.session.endpoint == ENDPOINT
                and approval['database'] == str(database_path(conn))
                and approval['symbol'] == 'BTCUSDT' and approval['position_idx'] == 0
                and approval['residual_hidden_order_risk_accepted'] is True
                and approval['issued_at'] <= now < approval['expires_at'] <= approval['issued_at'] + 86400
                and approval['epoch_id'] and approval['approval_id']
                and approval['code_sha256'] == code_digest(), 'approval_scope')
        proof = validate_quiescence(quiescence_probe, approval, now)
        require(not getattr(broker, '_incident_transport_active', False), 'transport_active')
        transports = _events(conn, TRANSPORT)
        require({r['call_id'] for r in transports if r['state'] == 'SENDING'}
                <= {r['call_id'] for r in transports if r['state'] == 'RETURNED'}, 'transport_lease_active')
        require(not conn.execute("SELECT 1 FROM llm_scenario_execution_batches WHERE status='EXECUTING'").fetchone(), 'execution_active')
        active = broker._active()
        require(active and active['scenario_id'] == scenario_id, 'active_changed')
        children = broker.children(scenario_id)
        for known in children:
            if known['link_id'] != unknown_id:
                broker._query_child(known)
        children = broker.children(scenario_id)
        unknown = [c for c in broker.children() if c['status'] == 'UNKNOWN' or not c['evidence']]
        require(len(unknown) == 1 and unknown[0]['link_id'] == unknown_id, 'unknown_set_changed')
        child = unknown[0]
        request = child['request']
        if extension:
            from live.scenario_entry_contract import valid_record
            stored = conn.execute('SELECT payload,status FROM llm_scenario_intents WHERE id=?', (child['intent_id'],)).fetchone()
            payload = json.loads(stored[0]) if stored else {}
            entries = payload.get('entries', [])
            require(len(children) == 1 and stored and stored[1] == 'PENDING'
                    and payload.get('action') == 'OPEN' and payload.get('scenario_id') == scenario_id
                    and payload.get('action_id') == child['intent_id'] and len(entries) == 1
                    and entries[0]['id'] == child['local_id'] and payload['expires_at'] < now
                    and child['kind'] == 'entry' and child['status'] == 'UNKNOWN'
                    and child['order_id'] is None and child['evidence'] is None
                    and request.get('reduceOnly') is False and request.get('orderType') == 'Limit'
                    and request.get('symbol') == 'BTCUSDT' and request.get('positionIdx') == 0
                    and request.get('side') == ('Buy' if active['side'] == 'LONG' else 'Sell')
                    and float(request['qty']) == float(entries[0]['quantity'])
                    and float(request['price']) == float(entries[0]['price'])
                    and request.get('tpslMode') == 'Full' and request.get('slTriggerBy') == 'MarkPrice'
                    and float(request['stopLoss']) == float(active['hard_stop'])
                    and valid_record(active.get('entry_contract'), scenario_id)
                    and digest(request) == approval.get('unknown_request_sha256'), 'entry_binding_changed')
        else:
            require(child['scenario_id'] == scenario_id and child['kind'] == 'partial_sl'
                    and child['status'] == 'UNKNOWN' and child['order_id'] is None and child['evidence'] is None
                    and request.get('reduceOnly') is True and request.get('closeOnTrigger') is True
                    and request.get('symbol') == 'BTCUSDT' and request.get('positionIdx') == 0
                    and request.get('orderType') == 'Market' and request.get('triggerBy') == 'MarkPrice'
                    and request.get('side') == ('Sell' if active['side'] == 'LONG' else 'Buy'), 'unknown_unsafe')
        from live.exchange_snapshot import read_complete
        from live.scenario_broker import read_evidence_pages
        visible = read_complete(broker._call, 'get_open_orders', category='linear', symbol='BTCUSDT', orderLinkId=unknown_id)
        require(visible is not None and visible['result']['list'] == []
                and read_evidence_pages(broker._call, 'get_order_history', category='linear',
                    symbol='BTCUSDT', orderLinkId=unknown_id) == [], 'unknown_evidence_changed')
        if not extension:
            from live.scenario_entry_contract import valid_record
            from live.scenario_management_quarantine import _latest
            management = _latest(broker)
            require(management and management['body'].get('operation') == 'AUTHORIZE', 'management_binding_missing')
            bound = management['body']
            require(bound['scenario_id'] == scenario_id and bound['unknown_link_id'] == unknown_id
                    and bound['unknown_request_sha256'] == digest(request)
                    and bound['main_uid'] == broker.expected_main_uid and bound['endpoint'] == ENDPOINT
                    and valid_record(active.get('entry_contract'), scenario_id)
                    and active['entry_contract']['contract_id'] == bound['contract_id']
                    and bound['unknown_child_identity'] == {k: child[k] for k in ('intent_id', 'local_id', 'created_at')}, 'management_binding_changed')
            for original in bound['pending_intents']:
                stored = conn.execute('SELECT scenario_id,payload,status FROM llm_scenario_intents WHERE id=?', (original['id'],)).fetchone()
                require(stored and stored[0] == scenario_id and stored[2] == 'PENDING'
                        and digest(json.loads(stored[1])) == original['payload_sha256'], 'original_intent_changed')
            require([r['id'] for r in bound['pending_intents'] if r['action'] == 'REDUCE'] == [child['intent_id']], 'reduce_binding_changed')
        require(all(c['link_id'] == unknown_id or c['status'] == 'TERMINAL' and c['evidence']
                    for c in children), 'other_children_unresolved')
        own_intents = {r[0] for r in conn.execute('SELECT id FROM llm_scenario_intents WHERE scenario_id=?', (scenario_id,))}
        require(set(unresolved(conn)) <= own_intents, 'foreign_intent')
        first = flat_probe(broker)
        second = flat_probe(broker)
        require(conn.execute("SELECT 1 FROM sqlite_master WHERE name='llm_scenario_daily_accounting'").fetchone()
                and conn.execute('SELECT 1 FROM llm_scenario_daily_accounting WHERE id=1').fetchone(), 'daily_baseline_missing')
        daily = broker._daily(second['account'])  # Commits before the transition.
        require(daily.get('new_risk_allowed') is True and daily.get('daily_proof_complete') is True, 'daily_risk_blocked')
        state = broker._recovery_state()
        require(not set(state.get('breaker', {}).get('reasons', [])) - {'three_losses'}, 'breaker_blocked')
        snapshot = _snapshot(broker, scenario_id)
        require(digest(snapshot) == approval['incident_sha256'], 'incident_changed')
        daily_raw = conn.execute('SELECT body FROM llm_scenario_daily_accounting WHERE id=1').fetchone()[0]
        raw = conn.execute('SELECT body FROM llm_scenario_state WHERE id=1').fetchone()[0]
        control_raw = conn.execute('SELECT body FROM llm_scenario_control WHERE id=1').fetchone()[0]
        require(json.loads(control_raw)['version'] == (2 if extension else 1), 'control_version')
        finance_start = int(float(active['created_at'])*1000)
        finance_end = int(second['captured_at']*1000)
        finance = historical_finance(broker, finance_start, finance_end, children)
        ensure_schema(conn)
        conn.commit()
        conn.execute('BEGIN IMMEDIATE')
        try:
            now = broker.clock()
            require(0 <= now - first['captured_at'] <= 10 and 0 <= now - second['captured_at'] <= 10
                    and now < approval['expires_at'] and 0 <= now - proof['captured_at'] <= 10, 'evidence_stale')
            require(conn.execute('SELECT body FROM llm_scenario_state WHERE id=1').fetchone()[0] == raw
                    and conn.execute('SELECT body FROM llm_scenario_control WHERE id=1').fetchone()[0] == control_raw
                    and conn.execute('SELECT body FROM llm_scenario_daily_accounting WHERE id=1').fetchone()[0] == daily_raw
                    and digest(_snapshot(broker, scenario_id)) == approval['incident_sha256'], 'transaction_evidence_changed')
            if extension:
                require(digest(receipt(conn)) == digest(existing) and not halted(conn), 'predecessor_changed')
                for original in receipts(conn):
                    expected = dict(original['snapshot'])
                    expected['management'] = expected['management'] + ([original['management_revocation']] if original.get('management_revocation') else [])
                    require(_snapshot(broker, original['scenario_id']) == expected, 'predecessor_snapshot_changed')
            fraction = min(.005, float(state.get('recovery', {}).get('risk_fraction', .005)))
            require(math.isfinite(fraction) and fraction > 0, 'risk_cap_invalid')
            result = dict(epoch_id=approval['epoch_id'], scenario_id=scenario_id, risk_fraction=fraction,
                approval=json.loads(json.dumps(approval)),
                approval_id=approval['approval_id'], approval_sha256=digest(approval),
                cutover_at=now, snapshot=snapshot, old_state=json.loads(raw),
                daily_sha256=digest(json.loads(daily_raw)), observations=[first, second],
                deployment_proof=proof, status='HISTORICAL_UNSETTLED')
            result.update(financial_sources=finance, financial_start_ms=finance_start, financial_end_ms=finance_end)
            old_management = _events(conn, 'management_quarantine_v1')
            if old_management and not extension:
                body = dict(operation='REVOKE', approval_id=old_management[-1]['body']['approval_id'],
                            reason='incident_epoch_administrative_separation', at=now,
                            approval_sha256=old_management[-1]['sha256'])
                _append(conn, 'management_quarantine_v1', dict(body=body, sha256=digest(body)), now)
                result['management_revocation'] = dict(body=body, sha256=digest(body))
            if extension:
                result['previous_receipt_sha256'] = digest(existing)
            _append(conn, ENTRY_KIND if extension else KIND, dict(body=result, sha256=digest(result)), now)
            normalized = dict(policy_version=2, epoch_id=approval['epoch_id'], stage=0,
                phase='OBSERVING', started_at=now, epoch_start_equity=second['account']['equity'],
                risk_fraction=fraction, auto_promotion=False,
                stage_evidence=_stage_evidence(second['account']['equity'], rule_version=3,
                    window_origin=digest({'incident_epoch': approval['epoch_id']})))
            normalized['bootstrap_evidence_event_id'] = journal(conn, 'MIGRATION',
                dict(before=state.get('recovery', {}), after=normalized,
                     historical_unsettled=scenario_id), now)
            state.update(active=None, version=state['version']+1, recovery=normalized,
                         incident_epoch=approval['epoch_id'])
            conn.execute('UPDATE llm_scenario_state SET body=? WHERE id=1', (json.dumps(state),))
            control = dict(version=2, state='active', main_uid=broker.expected_main_uid,
                epoch_id=approval['epoch_id'], approval_sha256=digest(approval), epoch_protocol=1)
            conn.execute('UPDATE llm_scenario_control SET body=? WHERE id=1', (json.dumps(control),))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return receipt(conn)


def new_risk_blocked(broker):
    value = receipt(broker.conn)
    if not value:
        return False
    epoch(broker.conn)
    state = broker._recovery_state()
    recovery = state.get('recovery', {})
    require(state.get('incident_epoch') == value['epoch_id']
            and recovery.get('epoch_id') == value['epoch_id']
            and recovery.get('risk_fraction') == value['risk_fraction']
            and recovery.get('auto_promotion') is False and recovery.get('stage') == 0, 'recovery_changed')
    changed = False
    for historical in receipts(broker.conn):
        current = _snapshot(broker, historical['scenario_id'])
        expected_management = historical['snapshot']['management'] + ([historical['management_revocation']] if historical.get('management_revocation') else [])
        management_changed = current['management'] != expected_management
        current['management'] = historical['snapshot']['management']
        altered = current != historical['snapshot'] or management_changed
        changed = changed or altered
        if altered and not halted(broker.conn):
            _append(broker.conn, HALT, dict(reason='historical_evidence_changed',
                epoch_id=value['epoch_id'], scenario_id=historical['scenario_id'], observed_sha256=digest(current)), broker.clock())
            broker.conn.commit()
    observed = getattr(broker, '_incident_observation', None)
    fresh = bool(observed and observed['epoch_id'] == value['epoch_id']
                 and 0 <= broker.clock()-observed['started_at'] <= 10)
    return changed or halted(broker.conn) or not fresh


def guard_write(broker, method, params):
    """Runs under the same mutex as POST and cutover; no protective fallback."""
    current = epoch(broker.conn)  # Any read failure must escape before HTTP.
    require(current == broker._incident_epoch_bound, 'stale_worker')
    if not current:
        return
    value = receipt(broker.conn)
    require(broker.expected_main_uid == value['approval']['main_uid']
            and broker.session.endpoint == ENDPOINT and broker.environment == 'demo', 'epoch_identity_changed')
    state = broker._recovery_state()
    active = state.get('active')
    require(state.get('incident_epoch') == current and active
            and not sealed(broker.conn, active['scenario_id']), 'sealed_scenario')
    ids = {params.get('orderLinkId'), params.get('orderId')} - {None, ''}
    for historical in receipts(broker.conn):
        for child in historical['snapshot']['llm_scenario_children']:
            require(not ids.intersection({child[0], child[7]}), 'sealed_order')
    if method in {'place_order', 'amend_order'} and params.get('reduceOnly') is not True:
        require(not new_risk_blocked(broker), 'new_risk_halted')
