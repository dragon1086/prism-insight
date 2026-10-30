"""Lossless MODEL_RAW references to a same-slot MODEL_REQUEST."""
import gzip
import hashlib
import json
import zlib

MAGIC = b'PRISM_REVIEW_EVENT_REF_V1\x00'
MAX_BYTES = 8_000_000


def _json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _unpack(encoded):
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        unpacked = decoder.decompress(encoded[len(MAGIC):], MAX_BYTES + 1)
        if len(unpacked) > MAX_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError('review_event_reference_invalid')
        marker = json.loads(unpacked)
        if not isinstance(marker, dict):
            raise ValueError('review_event_reference_invalid')
        return marker
    except (UnicodeError, zlib.error, json.JSONDecodeError) as exc:
        raise ValueError('review_event_reference_invalid') from exc


def compact(raw_text, sources, slot, sequence):
    """Return a reference only for a unique, exactly reconstructible request."""
    if (not isinstance(raw_text, str) or len(raw_text.encode()) > MAX_BYTES
            or not isinstance(sources, list) or len(sources) > 128 or type(slot) is not int or slot < 0
            or type(sequence) is not int or sequence < 0):
        return raw_text
    try:
        event = json.loads(raw_text)
        actual = event.get('actual_request') if isinstance(event, dict) else None
        if (not isinstance(event, dict) or _json(event) != raw_text or event.get('kind') != 'MODEL_RAW'
                or event.get('phase') not in {'single', 'candidate', 'selection'}
                or not isinstance(actual, dict) or 'timeout' not in actual):
            return raw_text
        matching = []
        for source_sequence, source_text in sources:
            if (type(source_sequence) is not int or not 0 <= source_sequence < sequence
                    or not isinstance(source_text, str) or len(source_text.encode()) > MAX_BYTES):
                continue
            source = json.loads(source_text)
            request = source.get('request') if isinstance(source, dict) else None
            if (isinstance(source, dict) and _json(source) == source_text and source.get('kind') == 'MODEL_REQUEST'
                    and source.get('phase') == event['phase'] and isinstance(request, dict)
                    and 'timeout' in request and {k: v for k, v in request.items() if k != 'timeout'}
                    == {k: v for k, v in actual.items() if k != 'timeout'}):
                matching.append((source_sequence, source_text))
        if len(matching) != 1:
            return raw_text
        source_sequence, source_text = matching[0]
        rest = dict(event)
        del rest['actual_request']
        marker = {'slot': slot, 'sequence': sequence, 'source_sequence': source_sequence,
                  'source_sha256': _sha(source_text), 'raw_sha256': _sha(raw_text),
                  'actual_timeout': actual['timeout'], 'event': rest}
        encoded = MAGIC + gzip.compress(_json(marker).encode('utf-8'), compresslevel=1, mtime=0)
        legacy = gzip.compress(raw_text.encode('utf-8'), compresslevel=1, mtime=0)
        return encoded if len(encoded) < len(legacy) and restore(encoded, source_text, slot, sequence) == raw_text else raw_text
    except (ValueError, TypeError, KeyError, OverflowError):
        return raw_text


def restore(encoded, source_text, slot, sequence):
    """Rebuild exact stored text; never substitute missing or changed evidence."""
    if (not isinstance(encoded, bytes) or not encoded.startswith(MAGIC)
            or not isinstance(source_text, str) or len(source_text.encode()) > MAX_BYTES or type(slot) is not int or slot < 0
            or type(sequence) is not int or sequence < 0):
        raise ValueError('review_event_reference_invalid')
    try:
        marker = _unpack(encoded)
        if (set(marker) != {'slot', 'sequence', 'source_sequence', 'source_sha256',
                'raw_sha256', 'actual_timeout', 'event'} or type(marker['slot']) is not int or marker['slot'] != slot
                or type(marker['sequence']) is not int or marker['sequence'] != sequence
                or type(marker['source_sequence']) is not int or not 0 <= marker['source_sequence'] < sequence
                or marker['source_sha256'] != _sha(source_text)):
            raise ValueError('review_event_reference_invalid')
        source = json.loads(source_text)
        event = marker['event']
        if (not isinstance(source, dict) or _json(source) != source_text or source.get('kind') != 'MODEL_REQUEST'
                or not isinstance(event, dict) or event.get('kind') != 'MODEL_RAW' or 'actual_request' in event
                or event.get('phase') not in {'single', 'candidate', 'selection'} or event.get('phase') != source.get('phase')
                or not isinstance(source.get('request'), dict) or 'timeout' not in source['request']):
            raise ValueError('review_event_reference_invalid')
        request = dict(source['request'])
        request['timeout'] = marker['actual_timeout']
        result = _json(dict(event, actual_request=request))
        if len(result.encode()) > MAX_BYTES or _sha(result) != marker['raw_sha256']:
            raise ValueError('review_event_reference_changed')
        return result
    except (KeyError, TypeError, UnicodeError, json.JSONDecodeError, OverflowError) as exc:
        raise ValueError('review_event_reference_invalid') from exc


def read_event(value, conn, slot, sequence, *, decode_legacy):
    if not isinstance(value, bytes) or not value.startswith(MAGIC):
        return decode_legacy(value)
    if conn is None or type(slot) is not int or slot < 0 or type(sequence) is not int or sequence < 0:
        raise ValueError('review_event_reference_context_missing')
    marker = _unpack(value)
    source_sequence = marker.get('source_sequence')
    if type(source_sequence) is not int or not 0 <= source_sequence < sequence:
        raise ValueError('review_event_reference_invalid')
    rows = conn.execute('SELECT body FROM llm_scenario_review_events WHERE slot=? AND sequence=? LIMIT 2',
                        (slot, source_sequence)).fetchall()
    if len(rows) != 1 or isinstance(rows[0][0], bytes) and rows[0][0].startswith(MAGIC):
        raise ValueError('review_event_reference_source_missing')
    return restore(value, decode_legacy(rows[0][0]), slot, sequence)
