CREATE TABLE strategy_books (
    book_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    market TEXT NOT NULL,
    currency TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (strategy_id, market)
);

CREATE TABLE cash_ledger (
    cash_entry_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES strategy_books(book_id),
    currency TEXT NOT NULL,
    amount TEXT NOT NULL,
    entry_kind TEXT NOT NULL,
    effective_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE paper_orders (
    order_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES strategy_books(book_id),
    proposal_id TEXT NOT NULL,
    security_id TEXT NOT NULL,
    order_state TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL REFERENCES paper_orders(order_id),
    quantity TEXT NOT NULL,
    price TEXT NOT NULL,
    fee TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE positions (
    position_snapshot_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES strategy_books(book_id),
    security_id TEXT NOT NULL,
    quantity TEXT NOT NULL,
    average_cost TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (book_id, security_id, as_of_at)
);

CREATE TABLE nav_snapshots (
    nav_snapshot_id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL REFERENCES strategy_books(book_id),
    nav TEXT NOT NULL,
    cash TEXT NOT NULL,
    as_of_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (book_id, as_of_at)
);

CREATE TRIGGER cash_ledger_append_only_update BEFORE UPDATE ON cash_ledger BEGIN
    SELECT RAISE(ABORT, 'cash_ledger is append-only');
END;
CREATE TRIGGER cash_ledger_append_only_delete BEFORE DELETE ON cash_ledger BEGIN
    SELECT RAISE(ABORT, 'cash_ledger is append-only');
END;
CREATE TRIGGER paper_orders_append_only_update BEFORE UPDATE ON paper_orders BEGIN
    SELECT RAISE(ABORT, 'paper_orders is append-only');
END;
CREATE TRIGGER paper_orders_append_only_delete BEFORE DELETE ON paper_orders BEGIN
    SELECT RAISE(ABORT, 'paper_orders is append-only');
END;
CREATE TRIGGER fills_append_only_update BEFORE UPDATE ON fills BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;
CREATE TRIGGER fills_append_only_delete BEFORE DELETE ON fills BEGIN
    SELECT RAISE(ABORT, 'fills is append-only');
END;
CREATE TRIGGER positions_append_only_update BEFORE UPDATE ON positions BEGIN
    SELECT RAISE(ABORT, 'positions is append-only');
END;
CREATE TRIGGER positions_append_only_delete BEFORE DELETE ON positions BEGIN
    SELECT RAISE(ABORT, 'positions is append-only');
END;
CREATE TRIGGER nav_snapshots_append_only_update BEFORE UPDATE ON nav_snapshots BEGIN
    SELECT RAISE(ABORT, 'nav_snapshots is append-only');
END;
CREATE TRIGGER nav_snapshots_append_only_delete BEFORE DELETE ON nav_snapshots BEGIN
    SELECT RAISE(ABORT, 'nav_snapshots is append-only');
END;
