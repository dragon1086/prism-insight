"""Read legacy and bounded compressed BTC review records without changing text."""
import gzip
import zlib

MAGIC = b'PRISM_REVIEW_GZIP_V1\x00'
MAX_BYTES = 8_000_000


def review_storage(text):
    if not isinstance(text, str):
        raise ValueError('review_body_type_invalid')
    raw = text.encode('utf-8')
    if len(raw) > MAX_BYTES:
        raise ValueError('review_body_too_large')
    packed = MAGIC + gzip.compress(raw, compresslevel=1, mtime=0)
    return packed if len(packed) < len(raw) else text


def review_text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise ValueError('review_body_type_invalid')
    if not value.startswith(MAGIC):
        return value.decode('utf-8')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(value[len(MAGIC):], MAX_BYTES + 1)
    except zlib.error as exc:
        raise ValueError('review_body_compression_invalid') from exc
    if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('review_body_compression_invalid')
    return raw.decode('utf-8')


def tape_storage(conn, slot, text, *, allow_failed=False):
    """Use references only when already durable evidence restores the exact tape."""
    from utils.btc_review_references import MAX_REFERENCES, compact, pack, restore
    import json

    original = review_storage(text)
    tape = json.loads(text)
    status = tape.get('outcome', {}).get('status')
    if status != 'COMPLETE' and not (allow_failed is True and status == 'FAILED'):
        return original
    decision = conn.execute('SELECT snapshot,context FROM llm_scenario_decisions WHERE slot=?',
                            (slot,)).fetchone()
    if decision is None:
        return original
    try:
        snapshot = review_text(decision[0]) if isinstance(decision[0], bytes) else decision[0]
        from utils.btc_review_event_references import read_event
        count = sum(op.get('kind') == 'record' for op in tape['operations'])
        if count > MAX_REFERENCES - 2:
            return original
        rows = conn.execute('SELECT sequence,body FROM llm_scenario_review_events WHERE slot=? '
                            'ORDER BY sequence LIMIT ?', (slot, count))
        events, size = [], 0
        for sequence, body in rows:
            if sequence != len(events):
                return original
            event = read_event(body, conn, slot, sequence, decode_legacy=review_text)
            size += len(event.encode('utf-8'))
            if size > MAX_BYTES:
                return original
            events.append(event)
        if len(events) != count:
            return original
        bundle = compact(text, snapshot, decision[1], events)
        if restore(bundle, snapshot, decision[1], events) != text:
            return original
        encoded = pack(bundle)
    except (ValueError, TypeError, KeyError, IndexError):
        return original
    original_size = len(original.encode('utf-8')) if isinstance(original, str) else len(original)
    return encoded if len(encoded) < original_size else original


def validate_tape_for_commit(conn, slot, text, encoded):
    """Hold the write lock while checking the references that the tape will use."""
    from utils.btc_review_references import MAGIC as REFERENCE_MAGIC, read_tape
    if not isinstance(encoded, bytes) or not encoded.startswith(REFERENCE_MAGIC):
        return encoded
    if not conn.in_transaction:
        conn.execute('BEGIN IMMEDIATE')
    try:
        if read_tape(encoded, conn, slot, decode_legacy=review_text) == text:
            return encoded
    except ValueError:
        pass
    return review_storage(text)
