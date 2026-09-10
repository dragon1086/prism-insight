import json
import os

import pytest

from tools.isolated_case_journal import CaseJournal, JournalRejected


def test_journal_private_bound_and_fsynced(tmp_path, monkeypatch):
    calls = []
    original = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (calls.append(fd), original(fd))[1])
    journal = CaseJournal(tmp_path, "case1", "a" * 64)
    journal.record("CLAIMED")
    journal.record("AGENT_REAPED", receipt_valid=True, returncode=0, forced_kill=False,
                   stdout_bytes=12, stderr_bytes=0)
    rows = [json.loads(row) for row in journal.path.read_text().splitlines()]
    assert len(calls) >= 3
    assert [row["sequence"] for row in rows] == [1, 2]
    assert all(row["payload_sha256"] == "a" * 64 for row in rows)
    assert journal.path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(JournalRejected):
        CaseJournal(tmp_path, "case1", "a" * 64)


@pytest.mark.parametrize("fields", [{"stderr": "SECRET_CANARY"}, {"returncode": "SECRET_CANARY"},
    {"stdout_bytes": -1}, {"receipt_valid": 1}, {"terminal_categories": ["SECRET_CANARY"]},
    {"snapshot_hashes": ["SECRET_CANARY"]}])
def test_journal_rejects_nonallowlisted_fields_without_writing(tmp_path, fields):
    journal = CaseJournal(tmp_path, "case1", "a" * 64)
    with pytest.raises(JournalRejected):
        journal.record("AGENT_REAPED", **fields)
    assert journal.path.read_bytes() == b""


def test_journal_fsync_failure_never_reports_success(tmp_path, monkeypatch):
    journal = CaseJournal(tmp_path, "case1", "a" * 64)
    monkeypatch.setattr(os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("SECRET_CANARY")))
    with pytest.raises(JournalRejected, match="journal_write_failed"):
        journal.record("CLAIMED")


def test_replaced_inode_and_partial_write_poison_journal(tmp_path, monkeypatch):
    journal = CaseJournal(tmp_path, "case1", "a" * 64)
    calls = []
    original = os.write
    def partial(fd, value):
        calls.append(True)
        if len(calls) == 1:
            return original(fd, value[:5])
        raise OSError("SECRET_PARTIAL_CANARY")
    monkeypatch.setattr(os, "write", partial)
    with pytest.raises(JournalRejected):
        journal.record("CLAIMED")
    assert journal.path.read_bytes() and not journal.path.read_bytes().endswith(b"\n")
    with pytest.raises(JournalRejected):
        journal.record("FINALIZATION_READY", receipt_valid=True)
    with pytest.raises(JournalRejected):
        CaseJournal(tmp_path, "case1", "a" * 64)


def test_symlink_or_hardlink_journal_not_appended(tmp_path):
    journal = CaseJournal(tmp_path, "case1", "a" * 64)
    os.link(journal.path, tmp_path / "link")
    with pytest.raises(JournalRejected):
        journal.record("CLAIMED")
    assert journal.path.read_bytes() == b""
