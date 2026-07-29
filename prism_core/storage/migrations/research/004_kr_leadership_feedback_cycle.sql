CREATE TABLE leadership_history_events (
    leadership_event_id TEXT PRIMARY KEY,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    security_id TEXT NOT NULL,
    provider_symbol TEXT NOT NULL,
    session_date TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    candidate_channels_json TEXT NOT NULL,
    relative_strength_5d TEXT,
    relative_strength_20d TEXT,
    relative_strength_60d TEXT,
    high_52_week_state TEXT NOT NULL CHECK (
        high_52_week_state IN ('NEW_HIGH', 'NEAR_HIGH', 'BELOW_HIGH', 'UNKNOWN')
    ),
    high_52_week_distance_pct TEXT,
    momentum_state TEXT NOT NULL CHECK (
        momentum_state IN ('ACCELERATING', 'ADVANCING', 'FLAT', 'WEAKENING', 'DECLINING', 'UNKNOWN')
    ),
    momentum_score TEXT,
    peak_state TEXT NOT NULL CHECK (
        peak_state IN ('NOT_PEAKED', 'APPROACHING', 'PEAKED', 'DECLINING', 'UNKNOWN')
    ),
    peak_score TEXT,
    group_id TEXT NOT NULL,
    group_state TEXT NOT NULL CHECK (
        group_state IN ('LEADING', 'EMERGING', 'NARROW', 'FADING')
    ),
    source_snapshot_id TEXT NOT NULL,
    evidence_ids_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (market, security_id, session_date, strategy_id, strategy_version, revision)
);
CREATE INDEX leadership_history_pit
    ON leadership_history_events (
        market, security_id, strategy_id, strategy_version,
        available_at, as_of_at, session_date, revision
    );
CREATE TRIGGER leadership_history_events_append_only_update
BEFORE UPDATE ON leadership_history_events BEGIN
    SELECT RAISE(ABORT, 'leadership_history_events is append-only');
END;
CREATE TRIGGER leadership_history_events_append_only_delete
BEFORE DELETE ON leadership_history_events BEGIN
    SELECT RAISE(ABORT, 'leadership_history_events is append-only');
END;

CREATE TABLE process_quality_outcomes (
    process_event_id TEXT PRIMARY KEY,
    proposal_record_id TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    security_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    data_quality TEXT NOT NULL CHECK (data_quality IN ('PASS', 'WARN', 'FAIL', 'UNAVAILABLE')),
    evidence_quality TEXT NOT NULL CHECK (evidence_quality IN ('PASS', 'WARN', 'FAIL', 'UNAVAILABLE')),
    predicate_quality TEXT NOT NULL CHECK (predicate_quality IN ('PASS', 'WARN', 'FAIL', 'UNAVAILABLE')),
    validator_quality TEXT NOT NULL CHECK (validator_quality IN ('PASS', 'WARN', 'FAIL', 'UNAVAILABLE')),
    evidence_ids_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (proposal_record_id, strategy_id, strategy_version)
        REFERENCES trade_plan_proposals(proposal_record_id, strategy_id, strategy_version),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (proposal_record_id, revision)
);
CREATE INDEX process_quality_outcomes_pit
    ON process_quality_outcomes (
        market, security_id, strategy_id, strategy_version, available_at, as_of_at
    );
CREATE TRIGGER process_quality_outcomes_append_only_update
BEFORE UPDATE ON process_quality_outcomes BEGIN
    SELECT RAISE(ABORT, 'process_quality_outcomes is append-only');
END;
CREATE TRIGGER process_quality_outcomes_append_only_delete
BEFORE DELETE ON process_quality_outcomes BEGIN
    SELECT RAISE(ABORT, 'process_quality_outcomes is append-only');
END;