import hashlib
import json

import pytest

from tools import btc_review_manifest_batch as subject


def test_selects_at_most_20_and_rejects_modified_manifest(tmp_path):
    path = tmp_path / 'fingerprints.json'
    rows = [{'rowid': i, 'slot':7, 'sequence':i, 'expected_type':'text', 'expected_sha256':'b' * 64}
            for i in range(1, 26)]
    path.write_text(json.dumps({'source_identity':'a' * 64, 'rows':rows}))
    path.chmod(0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    first = subject.select_batch(path, digest, 'a' * 64, 0)
    assert len(first['rows']) == 20 and first['rows'][-1]['rowid'] == 20
    second = subject.select_batch(path, digest, 'a' * 64, 20)
    assert [row['rowid'] for row in second['rows']] == [21, 22, 23, 24, 25]
    path.write_text(path.read_text() + ' ')
    with pytest.raises(ValueError, match='fingerprint_manifest_changed'):
        subject.select_batch(path, digest, 'a' * 64, 0)


def test_rejects_wrong_identity_and_insecure_file(tmp_path):
    path = tmp_path / 'fingerprints.json'
    path.write_text(json.dumps({'source_identity':'a' * 64, 'rows':[]}))
    path.chmod(0o600)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='fingerprint_manifest_invalid'):
        subject.select_batch(path, digest, 'b' * 64, 0)
    path.chmod(0o644)
    with pytest.raises(ValueError, match='manifest_not_private'):
        subject.select_batch(path, digest, 'a' * 64, 0)
