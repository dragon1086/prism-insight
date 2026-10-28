"""Lossless recovery references to existing same-slot review events."""
import hashlib
import gzip
import json
import zlib

TAG = '$btc_recovery_review_ref_v1'
MAGIC = b'PRISM_RECOVERY_REFS_V1\x00'
MAX_BYTES = 256 * 1024


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def sha(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def compact(review_raw, recovery_raw, slot, sequence):
    if not isinstance(review_raw, str) or not isinstance(recovery_raw, str):
        return recovery_raw
    if max(len(review_raw.encode()), len(recovery_raw.encode())) > MAX_BYTES:
        return recovery_raw
    if type(slot) is not int or slot < 0 or type(sequence) is not int or sequence < 0:
        return recovery_raw
    try:
        review, recovery = json.loads(review_raw), json.loads(recovery_raw)
        if not isinstance(review, dict) or not isinstance(recovery, dict) or TAG in recovery:
            return recovery_raw
        if (canonical(recovery) != recovery_raw or type(recovery.get('calculated_review_version')) is not int
                or recovery['calculated_review_version'] != 1 or review.get('kind') not in ('MODEL_REQUEST', 'MODEL_RAW')):
            return recovery_raw
        if 'calculated_review_version' in review:
            return recovery_raw
        extra = {'calculated_review_version': 1}
        if 'input_id' not in review and 'input_id' in recovery:
            extra['input_id'] = recovery['input_id']
        if dict(review, **extra) != recovery:
            return recovery_raw
        marker = {TAG: 1, 'slot': slot, 'sequence': sequence, 'review_sha256': sha(review_raw),
                  'recovery_sha256': sha(recovery_raw), 'extra': extra}
        encoded = canonical(marker)
        return encoded if len(encoded.encode()) < len(recovery_raw.encode()) else recovery_raw
    except (TypeError, ValueError, OverflowError):
        return recovery_raw


def restore(encoded, review_raw, slot, sequence):
    if not isinstance(encoded, str) or not isinstance(review_raw, str):
        raise ValueError('recovery_reference_invalid')
    if max(len(encoded.encode('utf-8')), len(review_raw.encode('utf-8'))) > MAX_BYTES:
        raise ValueError('recovery_reference_invalid')
    marker = json.loads(encoded)
    if not isinstance(marker, dict) or TAG not in marker:
        return encoded
    if (set(marker) != {TAG, 'slot', 'sequence', 'review_sha256', 'recovery_sha256', 'extra'}
            or type(marker[TAG]) is not int or marker[TAG] != 1
            or type(slot) is not int or slot < 0 or type(sequence) is not int or sequence < 0
            or type(marker['slot']) is not int or type(marker['sequence']) is not int
            or marker['slot'] != slot or marker['sequence'] != sequence or sha(review_raw) != marker['review_sha256']):
        raise ValueError('recovery_reference_invalid')
    extra = marker['extra']
    if not isinstance(extra, dict) or set(extra) not in ({'calculated_review_version'}, {'calculated_review_version', 'input_id'}) \
            or type(extra.get('calculated_review_version')) is not int or extra['calculated_review_version'] != 1:
        raise ValueError('recovery_reference_invalid')
    review = json.loads(review_raw)
    if not isinstance(review, dict) or any(key in review for key in extra):
        raise ValueError('recovery_reference_changed')
    restored = canonical(dict(review, **extra))
    if len(restored.encode()) > MAX_BYTES or sha(restored) != marker['recovery_sha256']:
        raise ValueError('recovery_reference_changed')
    return restored


def pack(encoded):
    if not isinstance(encoded, str) or len(encoded.encode('utf-8')) > MAX_BYTES:
        raise ValueError('recovery_reference_invalid')
    marker = json.loads(encoded)
    if not isinstance(marker, dict) or type(marker.get(TAG)) is not int or marker[TAG] != 1:
        raise ValueError('recovery_reference_invalid')
    return MAGIC + gzip.compress(encoded.encode('utf-8'), compresslevel=1, mtime=0)


def read_recovery(value, conn, slot, *, decode_legacy):
    """Resolve an explicitly tagged reference, preserving all legacy text."""
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        return decode_legacy(value)
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        decoded = decoder.decompress(value[len(MAGIC):], MAX_BYTES + 1)
        if len(decoded) > MAX_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('recovery_reference_compression_invalid')
        raw = decoded.decode('utf-8')
        marker = json.loads(raw)
    except (zlib.error, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError('recovery_reference_compression_invalid') from exc
    if not isinstance(marker, dict) or TAG not in marker:
        raise ValueError('recovery_reference_invalid')
    sequence = marker.get('sequence')
    if conn is None or type(slot) is not int or slot < 0 or type(sequence) is not int or sequence < 0:
        raise ValueError('recovery_reference_context_missing')
    rows = conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=? AND sequence=? LIMIT 2',
                        (slot, sequence)).fetchall()
    if len(rows) != 1:
        raise ValueError('recovery_reference_source_missing')
    return restore(raw, decode_legacy(rows[0][0]), slot, sequence)
