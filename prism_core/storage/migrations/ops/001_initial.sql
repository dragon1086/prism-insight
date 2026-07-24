CREATE TABLE job_runs (
    run_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE leases (
    lease_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    owner_id TEXT NOT NULL,
    acquired_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE heartbeats (
    heartbeat_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES job_runs(run_id),
    observed_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE alerts (
    alert_id TEXT PRIMARY KEY,
    run_id TEXT REFERENCES job_runs(run_id),
    severity TEXT NOT NULL,
    alert_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE backup_records (
    backup_id TEXT PRIMARY KEY,
    database_kind TEXT NOT NULL,
    source_identity TEXT NOT NULL,
    destination_identity TEXT NOT NULL,
    status TEXT NOT NULL,
    checksum TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE recovery_events (
    recovery_event_id TEXT PRIMARY KEY,
    backup_id TEXT REFERENCES backup_records(backup_id),
    event_kind TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER heartbeats_append_only_update
BEFORE UPDATE ON heartbeats BEGIN
    SELECT RAISE(ABORT, 'heartbeats is append-only');
END;
CREATE TRIGGER heartbeats_append_only_delete
BEFORE DELETE ON heartbeats BEGIN
    SELECT RAISE(ABORT, 'heartbeats is append-only');
END;

CREATE TRIGGER alerts_append_only_update
BEFORE UPDATE ON alerts BEGIN
    SELECT RAISE(ABORT, 'alerts is append-only');
END;
CREATE TRIGGER alerts_append_only_delete
BEFORE DELETE ON alerts BEGIN
    SELECT RAISE(ABORT, 'alerts is append-only');
END;

CREATE TRIGGER backup_records_append_only_update
BEFORE UPDATE ON backup_records BEGIN
    SELECT RAISE(ABORT, 'backup_records is append-only');
END;
CREATE TRIGGER backup_records_append_only_delete
BEFORE DELETE ON backup_records BEGIN
    SELECT RAISE(ABORT, 'backup_records is append-only');
END;

CREATE TRIGGER recovery_events_append_only_update
BEFORE UPDATE ON recovery_events BEGIN
    SELECT RAISE(ABORT, 'recovery_events is append-only');
END;
CREATE TRIGGER recovery_events_append_only_delete
BEFORE DELETE ON recovery_events BEGIN
    SELECT RAISE(ABORT, 'recovery_events is append-only');
END;
