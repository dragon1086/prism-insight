CREATE TABLE migration_002_symbol_mapping_guard (
    row_count INTEGER NOT NULL CHECK (row_count = 0)
);
INSERT INTO migration_002_symbol_mapping_guard
SELECT COUNT(*) FROM symbol_mappings;
DROP TABLE migration_002_symbol_mapping_guard;

CREATE TABLE security_alias_events (
    alias_evidence_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    market TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source_record_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (
        quality IN ('FRESH', 'STALE', 'PARTIAL', 'UNAVAILABLE', 'CONFLICT')
    ),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_date)
);

CREATE INDEX security_alias_lookup_idx
ON security_alias_events(provider, provider_symbol, available_at, valid_from, valid_to);

CREATE TABLE security_listing_events (
    listing_evidence_id TEXT PRIMARY KEY,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    market TEXT NOT NULL,
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('LISTED', 'DELISTED')),
    effective_at TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (
        quality IN ('FRESH', 'STALE', 'PARTIAL', 'UNAVAILABLE', 'CONFLICT')
    ),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_date)
);

CREATE INDEX security_listing_lookup_idx
ON security_listing_events(security_id, available_at, effective_at);

CREATE TABLE corporate_action_events (
    action_evidence_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    security_id TEXT NOT NULL REFERENCES securities(security_id),
    provider TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    source_record_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    action_type TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    ratio TEXT,
    cash_amount TEXT,
    currency TEXT,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_date TEXT NOT NULL,
    quality TEXT NOT NULL CHECK (
        quality IN ('FRESH', 'STALE', 'PARTIAL', 'UNAVAILABLE', 'CONFLICT')
    ),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_date)
);

CREATE INDEX corporate_action_as_of_idx
ON corporate_action_events(security_id, available_at, effective_at, action_id);

CREATE TRIGGER securities_append_only_update BEFORE UPDATE ON securities BEGIN
    SELECT RAISE(ABORT, 'securities is append-only');
END;
CREATE TRIGGER securities_append_only_delete BEFORE DELETE ON securities BEGIN
    SELECT RAISE(ABORT, 'securities is append-only');
END;
CREATE TRIGGER symbol_mappings_frozen_insert BEFORE INSERT ON symbol_mappings BEGIN
    SELECT RAISE(ABORT, 'symbol_mappings is frozen after migration 002');
END;
CREATE TRIGGER security_alias_events_append_only_update BEFORE UPDATE ON security_alias_events BEGIN
    SELECT RAISE(ABORT, 'security_alias_events is append-only');
END;
CREATE TRIGGER security_alias_events_append_only_delete BEFORE DELETE ON security_alias_events BEGIN
    SELECT RAISE(ABORT, 'security_alias_events is append-only');
END;
CREATE TRIGGER security_listing_events_append_only_update BEFORE UPDATE ON security_listing_events BEGIN
    SELECT RAISE(ABORT, 'security_listing_events is append-only');
END;
CREATE TRIGGER security_listing_events_append_only_delete BEFORE DELETE ON security_listing_events BEGIN
    SELECT RAISE(ABORT, 'security_listing_events is append-only');
END;
CREATE TRIGGER corporate_action_events_append_only_update BEFORE UPDATE ON corporate_action_events BEGIN
    SELECT RAISE(ABORT, 'corporate_action_events is append-only');
END;
CREATE TRIGGER corporate_action_events_append_only_delete BEFORE DELETE ON corporate_action_events BEGIN
    SELECT RAISE(ABORT, 'corporate_action_events is append-only');
END;
