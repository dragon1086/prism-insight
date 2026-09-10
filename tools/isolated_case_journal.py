"""Private, bounded stage receipts. No model or exception text is accepted."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat

STAGES = frozenset({"CLAIMED", "SERVICES_READY", "AGENT_STARTED", "AGENT_REAPED",
                    "INVOKER_JOINED", "RESPONSES_JOINED", "READ_BRIDGES_JOINED",
                    "OUTCOME_VALIDATED", "FINALIZATION_READY"})
CATEGORIES = frozenset({"NONE", "INTERNAL_FAILURE", "JOURNAL_FAILURE", "CANCELLED",
    "responses_helper_not_ready", "responses_helper_socket_invalid", "responses_cleanup_unknown",
    "responses_receipt_unknown", "invoker_cleanup_unknown", "read_bridge_cleanup_unknown",
    "responses_grace_timeout",
    "child_output_limit", "case_deadline_or_helper_failure", "case_deadline", "case_activity_uncertain",
    "case_result_invalid", "case_result_missing", "existing_case_artifact", "agent_cleanup_unknown",
    "agent_nonzero_exit", "case_result_identity_mismatch", "case_result_not_final",
    "validation_outcome_uncertain", "case_outcome_uncertain", "case_result_limit"})
_COUNTS = {"stdout_bytes", "stderr_bytes", "helper_requests", "unjoined_count", "elapsed_ms"}
_BOOLS = {"receipt_valid", "forced_kill", "poisoned", "graceful_exit", "grace_expired"}
_FIELDS = _COUNTS | _BOOLS | {"returncode", "terminal_categories", "snapshot_hashes", "failure_category", "component"}


class JournalRejected(ValueError):
    pass


class CaseJournal:
    """Exclusive creation; never resume or reinterpret a previous case journal."""
    def __init__(self, root, case_id, payload_sha256):
        root = Path(root)
        info = root.lstat()
        if (not stat.S_ISDIR(info.st_mode) or root.is_symlink() or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or root.resolve() != root
                or not isinstance(case_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,64}", case_id) is None
                or not isinstance(payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None):
            raise JournalRejected("invalid_journal_identity")
        self.case_ref = hashlib.sha256(case_id.encode()).hexdigest()
        self.payload_sha256 = payload_sha256
        self.path = root / (self.case_ref + ".stages.jsonl")
        self.sequence = 0
        self.stage = "CLAIMED"
        self.broken = False
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            info = os.fstat(fd)
            self.identity = (info.st_dev, info.st_ino)
            os.close(fd)
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            raise JournalRejected("journal_create_failed") from None

    def record(self, stage, **fields):
        if not isinstance(stage, str) or stage not in STAGES or self.sequence >= 64 or self.broken or set(fields) - _FIELDS:
            raise JournalRejected("invalid_journal_record")
        for key, value in fields.items():
            valid = False
            if key in _COUNTS:
                valid = type(value) is int and 0 <= value <= 1000000000
            elif key in _BOOLS:
                valid = type(value) is bool
            elif key == "returncode":
                valid = value is None or type(value) is int and -65536 <= value <= 65536
            elif key == "failure_category":
                valid = isinstance(value, str) and value in CATEGORIES
            elif key == "component":
                valid = isinstance(value, str) and value in {"invoker", "responses", "perplexity", "market", "agent", "case"}
            elif key in {"terminal_categories", "snapshot_hashes"}:
                valid = isinstance(value, list) and len(value) <= 16 and all(
                    isinstance(v, str) and (v in {"ok", "model_error", "model_timeout", "UNKNOWN"}
                        if key == "terminal_categories" else re.fullmatch(r"[0-9a-f]{64}", v) is not None)
                    for v in value)
            if not valid:
                raise JournalRejected("invalid_journal_record")
        record = {"schema_version": 1, "case_ref": self.case_ref, "payload_sha256": self.payload_sha256,
                  "sequence": self.sequence + 1, "stage": stage, **fields}
        encoded = (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        if len(encoded) > 4096:
            raise JournalRejected("invalid_journal_record")
        fd = None
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 64 * 4096
                    or (info.st_dev, info.st_ino) != self.identity):
                raise OSError()
            offset = 0
            while offset < len(encoded):
                count = os.write(fd, encoded[offset:])
                if count <= 0:
                    raise OSError()
                offset += count
            os.fsync(fd)
        except OSError:
            self.broken = True
            raise JournalRejected("journal_write_failed") from None
        finally:
            if fd is not None:
                os.close(fd)
        self.sequence += 1
        self.stage = stage
