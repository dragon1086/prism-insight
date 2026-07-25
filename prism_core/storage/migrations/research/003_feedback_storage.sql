-- Task 18 supersedes the underspecified 001 feedback tables without rewriting them.
-- Existing rows remain readable; new writes use this provenance-complete event cluster.

CREATE TABLE feedback_runs (
    feedback_run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    run_kind TEXT NOT NULL,
    config_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (feedback_run_id, strategy_id, strategy_version)
);

CREATE TABLE decision_snapshots (
    decision_snapshot_id TEXT PRIMARY KEY,
    feedback_run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    market TEXT NOT NULL CHECK (market IN ('KR', 'US')),
    security_id TEXT NOT NULL,
    data_snapshot_id TEXT NOT NULL,
    feature_snapshot_id TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    quant_score_id TEXT NOT NULL,
    quant_score_version TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    snapshot_json TEXT NOT NULL,
    data_quality TEXT NOT NULL CHECK (data_quality IN ('FRESH', 'STALE', 'PARTIAL', 'UNAVAILABLE', 'CONFLICT')),
    quality_disposition TEXT NOT NULL CHECK (quality_disposition IN ('ACCEPT', 'REPORT_ONLY', 'REJECT')),
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (feedback_run_id, strategy_id, strategy_version)
        REFERENCES feedback_runs(feedback_run_id, strategy_id, strategy_version),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (decision_snapshot_id, strategy_id, strategy_version),
    UNIQUE (feedback_run_id, feature_snapshot_id, content_hash)
);

CREATE TABLE trade_plan_proposals (
    proposal_record_id TEXT PRIMARY KEY,
    proposal_key TEXT NOT NULL,
    proposal_id TEXT,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    decision_snapshot_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    parse_status TEXT NOT NULL CHECK (parse_status IN ('PARSED', 'REJECTED')),
    validation_status TEXT NOT NULL CHECK (validation_status IN ('ACCEPTED', 'REJECTED')),
    proposed_decision TEXT CHECK (proposed_decision IN ('ENTRY_CANDIDATE', 'WATCH', 'NO_ENTRY', 'REPORT_ONLY')),
    raw_output_ref TEXT NOT NULL,
    raw_output TEXT NOT NULL,
    normalized_proposal_json TEXT,
    model_provider TEXT NOT NULL,
    model_id TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    sampling_version TEXT NOT NULL,
    sampling_json TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (decision_snapshot_id, strategy_id, strategy_version)
        REFERENCES decision_snapshots(decision_snapshot_id, strategy_id, strategy_version),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    CHECK (parse_status != 'REJECTED' OR validation_status = 'REJECTED'),
    CHECK (parse_status = 'REJECTED' OR (proposal_id IS NOT NULL AND normalized_proposal_json IS NOT NULL)),
    UNIQUE (proposal_record_id, strategy_id, strategy_version)
);
CREATE UNIQUE INDEX trade_plan_proposals_strategy_revision
    ON trade_plan_proposals(proposal_key, strategy_id, strategy_version, revision);
CREATE UNIQUE INDEX trade_plan_proposals_proposal_revision
    ON trade_plan_proposals(proposal_id, revision) WHERE proposal_id IS NOT NULL;
CREATE INDEX trade_plan_proposals_pit
    ON trade_plan_proposals(strategy_id, available_at, as_of_at, proposal_key, revision);

CREATE TABLE proposal_disposition_events (
    disposition_event_id TEXT PRIMARY KEY,
    proposal_record_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    sequence_no INTEGER NOT NULL CHECK (sequence_no >= 0),
    field_path TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('ACCEPT', 'CLAMP', 'RECALCULATE', 'REJECT')),
    reason TEXT NOT NULL,
    proposed_value_json TEXT,
    resolved_value_json TEXT,
    evidence_refs_json TEXT NOT NULL,
    validator_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
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
    UNIQUE (proposal_record_id, sequence_no),
    UNIQUE (proposal_record_id, content_hash)
);
CREATE INDEX proposal_disposition_events_pit
    ON proposal_disposition_events(strategy_id, available_at, proposal_record_id, sequence_no);

CREATE TABLE proposal_outcomes (
    outcome_event_id TEXT PRIMARY KEY,
    proposal_record_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK (
        (strategy_id = 'SWING_V1' AND horizon_sessions IN (5, 10, 20)) OR
        (strategy_id = 'TREND_V1' AND horizon_sessions IN (20, 60, 120))
    ),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    outcome_state TEXT NOT NULL CHECK (outcome_state IN (
        'NO_ENTRY', 'REJECTED', 'ELIGIBLE_NOT_EXECUTED', 'INTERNALLY_SIMULATED',
        'EXPIRED', 'CANCELLED', 'UNAVAILABLE', 'UNKNOWN'
    )),
    quality TEXT NOT NULL CHECK (quality IN ('FRESH', 'STALE', 'PARTIAL', 'UNAVAILABLE', 'CONFLICT')),
    outcome_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    config_version TEXT NOT NULL,
    code_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    FOREIGN KEY (proposal_record_id, strategy_id, strategy_version)
        REFERENCES trade_plan_proposals(proposal_record_id, strategy_id, strategy_version),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (proposal_record_id, horizon_sessions, revision),
    UNIQUE (proposal_record_id, horizon_sessions, content_hash)
);
CREATE INDEX proposal_outcomes_pit
    ON proposal_outcomes(strategy_id, available_at, proposal_record_id, horizon_sessions, revision);

CREATE TABLE retrospective_events (
    retrospective_event_id TEXT PRIMARY KEY,
    proposal_record_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    review_kind TEXT NOT NULL CHECK (review_kind IN ('PROCESS', 'OUTCOME')),
    revision INTEGER NOT NULL CHECK (revision >= 0),
    retrospective_json TEXT NOT NULL,
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
    UNIQUE (proposal_record_id, review_kind, revision),
    UNIQUE (proposal_record_id, review_kind, content_hash)
);

CREATE TABLE lesson_candidates (
    lesson_candidate_event_id TEXT PRIMARY KEY,
    lesson_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision >= 0),
    status TEXT NOT NULL CHECK (status IN ('CANDIDATE', 'SHADOW', 'SUSPENDED', 'RETIRED')),
    candidate_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    UNIQUE (lesson_candidate_event_id, strategy_id, strategy_version)
);
CREATE UNIQUE INDEX lesson_candidates_strategy_revision
    ON lesson_candidates(lesson_id, strategy_id, strategy_version, revision);

CREATE TABLE lesson_evidence_events (
    lesson_evidence_event_id TEXT PRIMARY KEY,
    lesson_candidate_event_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL CHECK (strategy_id IN ('SWING_V1', 'TREND_V1')),
    strategy_version TEXT NOT NULL,
    evidence_role TEXT NOT NULL CHECK (evidence_role IN ('SUPPORT', 'CONTRA')),
    proposal_record_id TEXT,
    observation_id TEXT,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    ingested_at TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    FOREIGN KEY (lesson_candidate_event_id, strategy_id, strategy_version)
        REFERENCES lesson_candidates(lesson_candidate_event_id, strategy_id, strategy_version),
    FOREIGN KEY (proposal_record_id) REFERENCES trade_plan_proposals(proposal_record_id),
    FOREIGN KEY (observation_id) REFERENCES observations(observation_id),
    CHECK ((proposal_record_id IS NOT NULL) != (observation_id IS NOT NULL)),
    CHECK (observed_at <= available_at),
    CHECK (available_at <= ingested_at),
    CHECK (available_at <= as_of_at),
    FOREIGN KEY (proposal_record_id, strategy_id, strategy_version)
        REFERENCES trade_plan_proposals(proposal_record_id, strategy_id, strategy_version)
);

CREATE UNIQUE INDEX lesson_evidence_events_proposal_identity
    ON lesson_evidence_events (lesson_candidate_event_id, evidence_role, proposal_record_id)
    WHERE proposal_record_id IS NOT NULL;
CREATE UNIQUE INDEX lesson_evidence_events_observation_identity
    ON lesson_evidence_events (lesson_candidate_event_id, evidence_role, observation_id)
    WHERE observation_id IS NOT NULL;

-- Keep 001 rows, but prevent a second writable source of feedback truth.
CREATE TRIGGER proposals_frozen_insert BEFORE INSERT ON proposals BEGIN
    SELECT RAISE(ABORT, 'proposals is frozen after migration 003');
END;
CREATE TRIGGER proposal_dispositions_frozen_insert BEFORE INSERT ON proposal_dispositions BEGIN
    SELECT RAISE(ABORT, 'proposal_dispositions is frozen after migration 003');
END;
CREATE TRIGGER outcomes_frozen_insert BEFORE INSERT ON outcomes BEGIN
    SELECT RAISE(ABORT, 'outcomes is frozen after migration 003');
END;
CREATE TRIGGER retrospectives_frozen_insert BEFORE INSERT ON retrospectives BEGIN
    SELECT RAISE(ABORT, 'retrospectives is frozen after migration 003');
END;
CREATE TRIGGER lesson_evidence_frozen_insert BEFORE INSERT ON lesson_evidence BEGIN
    SELECT RAISE(ABORT, 'lesson_evidence is frozen after migration 003');
END;

CREATE TRIGGER feedback_runs_append_only_update BEFORE UPDATE ON feedback_runs BEGIN SELECT RAISE(ABORT, 'feedback_runs is append-only'); END;
CREATE TRIGGER feedback_runs_append_only_delete BEFORE DELETE ON feedback_runs BEGIN SELECT RAISE(ABORT, 'feedback_runs is append-only'); END;
CREATE TRIGGER decision_snapshots_append_only_update BEFORE UPDATE ON decision_snapshots BEGIN SELECT RAISE(ABORT, 'decision_snapshots is append-only'); END;
CREATE TRIGGER decision_snapshots_append_only_delete BEFORE DELETE ON decision_snapshots BEGIN SELECT RAISE(ABORT, 'decision_snapshots is append-only'); END;
CREATE TRIGGER trade_plan_proposals_append_only_update BEFORE UPDATE ON trade_plan_proposals BEGIN SELECT RAISE(ABORT, 'trade_plan_proposals is append-only'); END;
CREATE TRIGGER trade_plan_proposals_append_only_delete BEFORE DELETE ON trade_plan_proposals BEGIN SELECT RAISE(ABORT, 'trade_plan_proposals is append-only'); END;
CREATE TRIGGER proposal_disposition_events_append_only_update BEFORE UPDATE ON proposal_disposition_events BEGIN SELECT RAISE(ABORT, 'proposal_disposition_events is append-only'); END;
CREATE TRIGGER proposal_disposition_events_append_only_delete BEFORE DELETE ON proposal_disposition_events BEGIN SELECT RAISE(ABORT, 'proposal_disposition_events is append-only'); END;
CREATE TRIGGER proposal_outcomes_append_only_update BEFORE UPDATE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER proposal_outcomes_append_only_delete BEFORE DELETE ON proposal_outcomes BEGIN SELECT RAISE(ABORT, 'proposal_outcomes is append-only'); END;
CREATE TRIGGER retrospective_events_append_only_update BEFORE UPDATE ON retrospective_events BEGIN SELECT RAISE(ABORT, 'retrospective_events is append-only'); END;
CREATE TRIGGER retrospective_events_append_only_delete BEFORE DELETE ON retrospective_events BEGIN SELECT RAISE(ABORT, 'retrospective_events is append-only'); END;
CREATE TRIGGER lesson_candidates_append_only_update BEFORE UPDATE ON lesson_candidates BEGIN SELECT RAISE(ABORT, 'lesson_candidates is append-only'); END;
CREATE TRIGGER lesson_candidates_append_only_delete BEFORE DELETE ON lesson_candidates BEGIN SELECT RAISE(ABORT, 'lesson_candidates is append-only'); END;
CREATE TRIGGER lesson_evidence_events_append_only_update BEFORE UPDATE ON lesson_evidence_events BEGIN SELECT RAISE(ABORT, 'lesson_evidence_events is append-only'); END;
CREATE TRIGGER lesson_evidence_events_append_only_delete BEFORE DELETE ON lesson_evidence_events BEGIN SELECT RAISE(ABORT, 'lesson_evidence_events is append-only'); END;
