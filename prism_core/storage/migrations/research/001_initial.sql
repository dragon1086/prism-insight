CREATE TABLE securities (
    security_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE symbol_mappings (
    mapping_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    created_at TEXT NOT NULL,
    UNIQUE (security_id, provider, provider_symbol, valid_from, revision)
);

CREATE TABLE market_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    market TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    quality TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE observations (
    observation_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    security_id TEXT REFERENCES securities(security_id),
    observation_kind TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_symbol TEXT,
    source_record_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (provider, source_record_id, revision)
);

CREATE TABLE features (
    feature_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    security_id TEXT REFERENCES securities(security_id),
    strategy_id TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE proposals (
    proposal_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    strategy_id TEXT NOT NULL,
    strategy_version TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    parsed_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE proposal_dispositions (
    disposition_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    validator_version TEXT NOT NULL,
    disposition_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE outcomes (
    outcome_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    horizon TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE retrospectives (
    retrospective_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL REFERENCES proposals(proposal_id),
    review_kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE lessons (
    lesson_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE lesson_evidence (
    lesson_evidence_id TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL REFERENCES lessons(lesson_id),
    observation_id TEXT NOT NULL REFERENCES observations(observation_id),
    evidence_role TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE reports (
    report_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES market_snapshots(snapshot_id),
    report_kind TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TRIGGER symbol_mappings_append_only_update BEFORE UPDATE ON symbol_mappings BEGIN
    SELECT RAISE(ABORT, 'symbol_mappings is append-only');
END;
CREATE TRIGGER symbol_mappings_append_only_delete BEFORE DELETE ON symbol_mappings BEGIN
    SELECT RAISE(ABORT, 'symbol_mappings is append-only');
END;
CREATE TRIGGER market_snapshots_append_only_update BEFORE UPDATE ON market_snapshots BEGIN
    SELECT RAISE(ABORT, 'market_snapshots is append-only');
END;
CREATE TRIGGER market_snapshots_append_only_delete BEFORE DELETE ON market_snapshots BEGIN
    SELECT RAISE(ABORT, 'market_snapshots is append-only');
END;
CREATE TRIGGER observations_append_only_update BEFORE UPDATE ON observations BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;
CREATE TRIGGER observations_append_only_delete BEFORE DELETE ON observations BEGIN
    SELECT RAISE(ABORT, 'observations is append-only');
END;
CREATE TRIGGER features_append_only_update BEFORE UPDATE ON features BEGIN
    SELECT RAISE(ABORT, 'features is append-only');
END;
CREATE TRIGGER features_append_only_delete BEFORE DELETE ON features BEGIN
    SELECT RAISE(ABORT, 'features is append-only');
END;
CREATE TRIGGER proposals_append_only_update BEFORE UPDATE ON proposals BEGIN
    SELECT RAISE(ABORT, 'proposals is append-only');
END;
CREATE TRIGGER proposals_append_only_delete BEFORE DELETE ON proposals BEGIN
    SELECT RAISE(ABORT, 'proposals is append-only');
END;
CREATE TRIGGER proposal_dispositions_append_only_update BEFORE UPDATE ON proposal_dispositions BEGIN
    SELECT RAISE(ABORT, 'proposal_dispositions is append-only');
END;
CREATE TRIGGER proposal_dispositions_append_only_delete BEFORE DELETE ON proposal_dispositions BEGIN
    SELECT RAISE(ABORT, 'proposal_dispositions is append-only');
END;
CREATE TRIGGER outcomes_append_only_update BEFORE UPDATE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'outcomes is append-only');
END;
CREATE TRIGGER outcomes_append_only_delete BEFORE DELETE ON outcomes BEGIN
    SELECT RAISE(ABORT, 'outcomes is append-only');
END;
CREATE TRIGGER retrospectives_append_only_update BEFORE UPDATE ON retrospectives BEGIN
    SELECT RAISE(ABORT, 'retrospectives is append-only');
END;
CREATE TRIGGER retrospectives_append_only_delete BEFORE DELETE ON retrospectives BEGIN
    SELECT RAISE(ABORT, 'retrospectives is append-only');
END;
CREATE TRIGGER lessons_append_only_update BEFORE UPDATE ON lessons BEGIN
    SELECT RAISE(ABORT, 'lessons is append-only');
END;
CREATE TRIGGER lessons_append_only_delete BEFORE DELETE ON lessons BEGIN
    SELECT RAISE(ABORT, 'lessons is append-only');
END;
CREATE TRIGGER lesson_evidence_append_only_update BEFORE UPDATE ON lesson_evidence BEGIN
    SELECT RAISE(ABORT, 'lesson_evidence is append-only');
END;
CREATE TRIGGER lesson_evidence_append_only_delete BEFORE DELETE ON lesson_evidence BEGIN
    SELECT RAISE(ABORT, 'lesson_evidence is append-only');
END;
CREATE TRIGGER reports_append_only_update BEFORE UPDATE ON reports BEGIN
    SELECT RAISE(ABORT, 'reports is append-only');
END;
CREATE TRIGGER reports_append_only_delete BEFORE DELETE ON reports BEGIN
    SELECT RAISE(ABORT, 'reports is append-only');
END;
