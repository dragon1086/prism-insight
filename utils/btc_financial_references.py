"""Lossless references to duplicate raw rows inside one financial record."""
import copy
import hashlib
import json

TAG = '$btc_financial_refs_v1'
MAX_BYTES = 8_000_000


def _json(value):
    return json.dumps(value, allow_nan=False, sort_keys=True)


def _index(rows, key):
    if not isinstance(rows, list):
        raise ValueError('financial_source_invalid')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('financial_source_invalid')
        value = row.get(key)
        if isinstance(value, str) and value:
            result.setdefault(value, []).append(row)
    return result


def _unique(index, key):
    rows = index.get(key, [])
    if len(rows) != 1:
        raise ValueError('financial_reference_ambiguous')
    return rows[0]


def _visit(body, *, compact):
    executions = _index(body['raw_executions'], 'execId')
    transactions = _index(body['raw_transactions'], 'tradeId')
    transaction_ids = _index(body['raw_transactions'], 'id')
    trades, funding = body['trades'], body['funding']
    if not isinstance(trades, list) or not isinstance(funding, list):
        raise ValueError('financial_shape_invalid')
    for row in trades:
        ident = row['execution_id']
        for field, index in (('raw_execution', executions), ('raw_transaction', transactions)):
            target = _unique(index, ident)
            if compact:
                if row[field] != target:
                    raise ValueError('financial_reference_mismatch')
                del row[field]
            else:
                if field in row:
                    raise ValueError('financial_reference_invalid')
                row[field] = target
    for row in funding:
        target = _unique(transaction_ids, row['transaction_id'])
        if compact:
            if row['raw'] != target:
                raise ValueError('financial_reference_mismatch')
            del row['raw']
        else:
            if 'raw' in row:
                raise ValueError('financial_reference_invalid')
            row['raw'] = target


def compact(text):
    if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_BYTES:
        return text
    try:
        original = json.loads(text)
        if not isinstance(original, dict) or TAG in original or _json(original) != text:
            return text
        body = copy.deepcopy(original)
        _visit(body, compact=True)
        bundle = {TAG: 1, 'body': body, 'sha256': hashlib.sha256(text.encode('utf-8')).hexdigest()}
        encoded = _json(bundle)
        return encoded if len(encoded.encode('utf-8')) < len(text.encode('utf-8')) else text
    except (ValueError, TypeError, KeyError, OverflowError):
        return text


def restore(text):
    if not isinstance(text, str):
        raise ValueError('financial_body_invalid')
    if not text.startswith('{"' + TAG + '":'):
        return text
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('financial_body_invalid')
    body = json.loads(text)
    if not isinstance(body, dict) or TAG not in body:
        return text
    if set(body) != {TAG, 'body', 'sha256'} or type(body[TAG]) is not int or body[TAG] != 1:
        raise ValueError('financial_reference_invalid')
    try:
        restored = copy.deepcopy(body['body'])
        _visit(restored, compact=False)
        result = _json(restored)
        if len(result.encode('utf-8')) > MAX_BYTES or hashlib.sha256(result.encode('utf-8')).hexdigest() != body['sha256']:
            raise ValueError('financial_reference_changed')
        return result
    except (TypeError, KeyError, OverflowError) as exc:
        raise ValueError('financial_reference_invalid') from exc
