-- Email Automation Intelligence Dashboard schema (SQLite).
-- A message is one campaign's email to one recipient. Engagement is an
-- append-only event log, so every reported number can be recomputed from it.

CREATE TABLE IF NOT EXISTS campaigns (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    goal             TEXT NOT NULL,            -- what the message should achieve; feeds the prompt
    subject_hint     TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'draft'
                     CHECK (status IN ('draft', 'sending', 'sent')),
    sheet_id         TEXT,
    sheet_range      TEXT,
    is_sample        INTEGER NOT NULL DEFAULT 0 CHECK (is_sample IN (0, 1)),
    created_at       TEXT NOT NULL,
    started_at       TEXT
);

CREATE TABLE IF NOT EXISTS recipients (
    id               INTEGER PRIMARY KEY,
    email            TEXT NOT NULL UNIQUE COLLATE NOCASE,
    first_name       TEXT NOT NULL DEFAULT '',
    last_name        TEXT NOT NULL DEFAULT '',
    company          TEXT NOT NULL DEFAULT '',
    title            TEXT NOT NULL DEFAULT '',
    attributes       TEXT NOT NULL DEFAULT '{}', -- extra sheet columns, as JSON
    source           TEXT NOT NULL DEFAULT 'sheet',
    unsubscribed_at  TEXT,
    created_at       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS messages (
    id               INTEGER PRIMARY KEY,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    recipient_id     INTEGER NOT NULL REFERENCES recipients (id) ON DELETE CASCADE,
    subject          TEXT NOT NULL DEFAULT '',
    body             TEXT NOT NULL DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'ready', 'generation_failed',
                                       'sending', 'sent', 'failed', 'skipped')),
    generated_by     TEXT,                     -- Groq model id, or 'template'
    tracking_token   TEXT NOT NULL UNIQUE,
    gmail_message_id TEXT,
    gmail_thread_id  TEXT,
    error            TEXT,
    sent_at          TEXT,
    created_at       TEXT NOT NULL,
    UNIQUE (campaign_id, recipient_id)
);

CREATE TABLE IF NOT EXISTS engagement_events (
    id               INTEGER PRIMARY KEY,
    message_id       INTEGER NOT NULL REFERENCES messages (id) ON DELETE CASCADE,
    event_type       TEXT NOT NULL
                     CHECK (event_type IN ('opened', 'clicked', 'replied',
                                           'bounced', 'unsubscribed')),
    occurred_at      TEXT NOT NULL,
    detail           TEXT,
    dedupe_key       TEXT UNIQUE               -- stops a re-sync counting a reply twice
);

CREATE TABLE IF NOT EXISTS insights (
    id               INTEGER PRIMARY KEY,
    campaign_id      INTEGER NOT NULL REFERENCES campaigns (id) ON DELETE CASCADE,
    content          TEXT NOT NULL,            -- JSON array of observations
    generated_by     TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_messages_campaign_status ON messages (campaign_id, status);
CREATE INDEX IF NOT EXISTS idx_messages_sent_at ON messages (sent_at);
CREATE INDEX IF NOT EXISTS idx_events_message_type ON engagement_events (message_id, event_type);
CREATE INDEX IF NOT EXISTS idx_events_occurred_at ON engagement_events (occurred_at);
CREATE INDEX IF NOT EXISTS idx_insights_campaign ON insights (campaign_id, created_at);
