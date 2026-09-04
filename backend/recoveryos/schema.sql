-- RecoveryOS storage. Plain SQLite via the standard library: twelve tables,
-- simple selects, and an append-only ledger. An ORM would be ceremony here.
--
-- Money is INTEGER paise everywhere. Timestamps are ISO-8601 strings in naive
-- Asia/Kolkata local time (see policy/guardrails.py for why).

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS customers (
    customer_id              TEXT PRIMARY KEY,
    name                     TEXT NOT NULL,
    segment                  TEXT NOT NULL,
    tenure_months            INTEGER NOT NULL,
    lifetime_value_paise     INTEGER NOT NULL,
    prior_payments_ok        INTEGER NOT NULL,
    prior_payments_failed    INTEGER NOT NULL,
    prior_self_cures         INTEGER NOT NULL DEFAULT 0,
    pays_after_payday        INTEGER NOT NULL DEFAULT 0,
    prior_recoveries_by_action TEXT NOT NULL DEFAULT '{}',
    preferred_channel        TEXT NOT NULL,
    dlt_consent              INTEGER NOT NULL DEFAULT 1,
    opted_out                INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS cases (
    case_id                  TEXT PRIMARY KEY,
    customer_id              TEXT NOT NULL REFERENCES customers(customer_id),
    case_type                TEXT NOT NULL,
    amount_paise             INTEGER NOT NULL,
    currency                 TEXT NOT NULL DEFAULT 'INR',
    failure_reason           TEXT NOT NULL,
    raw_error_code           TEXT,
    created_at               TEXT NOT NULL,
    due_at                   TEXT,
    is_recurring             INTEGER NOT NULL DEFAULT 0,
    mandate_id               TEXT,
    pre_debit_notice_sent_at TEXT,
    afa_present              INTEGER NOT NULL DEFAULT 0,
    afa_exempt_category      INTEGER NOT NULL DEFAULT 0,
    instrument_type          TEXT NOT NULL,
    instrument_expired       INTEGER NOT NULL DEFAULT 0,
    state                    TEXT NOT NULL DEFAULT 'OPEN',
    stop_reason              TEXT,
    recovered_paise          INTEGER NOT NULL DEFAULT 0,
    attempts_made            INTEGER NOT NULL DEFAULT 0,
    last_attempt_at          TEXT,
    next_action_at           TEXT,
    archetype                TEXT NOT NULL DEFAULT 'UNSPECIFIED',
    is_holdout               INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cases_customer ON cases(customer_id);
CREATE INDEX IF NOT EXISTS idx_cases_state ON cases(state);

-- The simulator's hidden response model. Physically separate from `cases` so
-- that "the policy accidentally read the answer key" is a visible mistake
-- rather than an invisible one. Only simulator code may read this table.
CREATE TABLE IF NOT EXISTS truths (
    case_id                  TEXT PRIMARY KEY REFERENCES cases(case_id),
    truth_json               TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS contacts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id              TEXT NOT NULL REFERENCES customers(customer_id),
    case_id                  TEXT,
    at                       TEXT NOT NULL,
    channel                  TEXT NOT NULL,
    action                   TEXT NOT NULL,
    run_id                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_customer ON contacts(customer_id, at);

CREATE TABLE IF NOT EXISTS attempts (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id                  TEXT NOT NULL REFERENCES cases(case_id),
    at                       TEXT NOT NULL,
    outcome                  TEXT NOT NULL,
    run_id                   TEXT
);
CREATE INDEX IF NOT EXISTS idx_attempts_case ON attempts(case_id, at);

CREATE TABLE IF NOT EXISTS promises (
    case_id                  TEXT PRIMARY KEY REFERENCES cases(case_id),
    state                    TEXT NOT NULL,
    promised_at              TEXT,
    promised_for             TEXT,
    promised_amount_paise    INTEGER,
    confidence               REAL NOT NULL DEFAULT 0,
    source_text              TEXT
);

-- Append-only, hash-chained decision ledger. `prev_hash` + `entry_hash` make
-- tampering detectable: see audit/ledger.py and GET /audit/verify.
CREATE TABLE IF NOT EXISTS audit_log (
    seq                      INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                   TEXT NOT NULL,
    case_id                  TEXT NOT NULL,
    at                       TEXT NOT NULL,
    payload_json             TEXT NOT NULL,
    prev_hash                TEXT NOT NULL,
    entry_hash               TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit_log(case_id, seq);
CREATE INDEX IF NOT EXISTS idx_audit_run ON audit_log(run_id, seq);

CREATE TABLE IF NOT EXISTS runs (
    run_id                   TEXT PRIMARY KEY,
    policy_name              TEXT NOT NULL,
    started_at               TEXT NOT NULL,
    finished_at              TEXT,
    seed                     INTEGER NOT NULL,
    metrics_json             TEXT
);
