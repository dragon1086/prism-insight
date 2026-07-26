CREATE TABLE positions_v2 (
    position_snapshot_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES strategy_books(book_id),
    security_id TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

INSERT INTO positions_v2 (
    position_snapshot_id,
    book_id,
    security_id,
    quantity,
    average_cost,
    as_of_at,
    created_at
)
SELECT
    position_snapshot_id,
    book_id,
    security_id,
    quantity,
    average_cost,
    as_of_at,
    created_at
FROM positions;

DROP TABLE positions;
ALTER TABLE positions_v2 RENAME TO positions;

CREATE INDEX positions_book_security_time_idx
    ON positions (book_id, security_id, as_of_at, position_snapshot_id);

CREATE TRIGGER positions_append_only_update BEFORE UPDATE ON positions BEGIN
    SELECT RAISE(ABORT, 'positions is append-only');
END;
CREATE TRIGGER positions_append_only_delete BEFORE DELETE ON positions BEGIN
    SELECT RAISE(ABORT, 'positions is append-only');
END;
