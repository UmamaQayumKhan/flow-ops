-- Tables the workflows read and write. Applied on first Postgres boot.
--
-- Two things here are deliberate and worth noting:
--   * natural unique keys, so a webhook delivered twice cannot double-book
--   * a dead-letter table, so nothing a workflow could not process is dropped

CREATE TABLE IF NOT EXISTS flowops_ticket_log (
    id                 BIGSERIAL PRIMARY KEY,
    ticket_id          TEXT        NOT NULL,
    workspace_id       TEXT,
    intent             TEXT,
    priority           TEXT,
    escalated          BOOLEAN     NOT NULL DEFAULT FALSE,
    confidence         NUMERIC(4,3),
    injection_detected BOOLEAN     NOT NULL DEFAULT FALSE,
    execution_id       TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Webhooks are at-least-once. Without this, a retried delivery logs twice
    -- and every rate in the daily digest is wrong.
    CONSTRAINT flowops_ticket_log_unique UNIQUE (ticket_id, execution_id)
);

CREATE INDEX IF NOT EXISTS flowops_ticket_log_created_idx
    ON flowops_ticket_log (created_at DESC);
CREATE INDEX IF NOT EXISTS flowops_ticket_log_escalated_idx
    ON flowops_ticket_log (escalated) WHERE escalated;


CREATE TABLE IF NOT EXISTS flowops_dead_letter (
    id           BIGSERIAL PRIMARY KEY,
    workflow     TEXT        NOT NULL,
    execution_id TEXT,
    reason       TEXT        NOT NULL,
    payload      JSONB,
    replayed_at  TIMESTAMPTZ,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flowops_dead_letter_open_idx
    ON flowops_dead_letter (created_at DESC) WHERE replayed_at IS NULL;


CREATE TABLE IF NOT EXISTS flowops_leads (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT        NOT NULL UNIQUE,
    company         TEXT,
    domain          TEXT,
    score           INTEGER     CHECK (score BETWEEN 0 AND 100),
    band            TEXT        CHECK (band IN ('hot','warm','cold','disqualified')),
    -- Records how often the model's own band disagreed with the thresholds.
    -- Useful signal: a rising rate means the rubric and the prompt have drifted.
    band_corrected  BOOLEAN     NOT NULL DEFAULT FALSE,
    owner           TEXT,
    source          TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS flowops_leads_band_idx ON flowops_leads (band, updated_at DESC);


CREATE TABLE IF NOT EXISTS flowops_leads_archive (
    id         BIGSERIAL PRIMARY KEY,
    email      TEXT NOT NULL,
    score      INTEGER,
    band       TEXT,
    rationale  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE TABLE IF NOT EXISTS flowops_invoices (
    id              BIGSERIAL PRIMARY KEY,
    invoice_number  TEXT        NOT NULL,
    vendor_name     TEXT        NOT NULL,
    vendor_tax_id   TEXT,
    issue_date      DATE,
    due_date        DATE,
    currency        CHAR(3),
    subtotal        NUMERIC(14,2),
    tax_amount      NUMERIC(14,2),
    total           NUMERIC(14,2) CHECK (total > 0),
    line_item_count INTEGER,
    execution_id    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- The same invoice emailed twice must not book twice.
    CONSTRAINT flowops_invoices_unique UNIQUE (invoice_number, vendor_name),
    -- Belt and braces alongside the arithmetic check in the workflow: the
    -- database refuses a row whose components do not reconcile.
    CONSTRAINT flowops_invoices_reconcile CHECK (
        subtotal IS NULL OR tax_amount IS NULL OR total IS NULL
        OR abs((subtotal + tax_amount) - total) <= 0.01
    )
);

CREATE INDEX IF NOT EXISTS flowops_invoices_vendor_idx
    ON flowops_invoices (vendor_name, issue_date DESC);
