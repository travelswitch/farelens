-- FareLens initial schema.
-- Applied automatically at startup by farelens.db.migrations (idempotent).

CREATE TABLE IF NOT EXISTS users (
    id                   BIGSERIAL PRIMARY KEY,
    username             TEXT NOT NULL UNIQUE,
    password_hash        TEXT NOT NULL,
    must_change_password BOOLEAN NOT NULL DEFAULT FALSE,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at        TIMESTAMPTZ
);

-- Single-row table: the one LLM provider configuration for this deployment.
CREATE TABLE IF NOT EXISTS llm_config (
    id          SMALLINT PRIMARY KEY CHECK (id = 1),
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    settings    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- non-secret provider fields
    secrets     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- encrypted provider fields
    options     JSONB NOT NULL DEFAULT '{}'::jsonb,   -- temperature, max_tokens, ...
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  TEXT
);

CREATE TABLE IF NOT EXISTS api_keys (
    id            BIGSERIAL PRIMARY KEY,
    name          TEXT NOT NULL,
    key_prefix    TEXT NOT NULL,
    key_hash      TEXT NOT NULL UNIQUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    TEXT,
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS fare_rules_summary_cache (
    cache_key        TEXT PRIMARY KEY,
    lang             TEXT NOT NULL,
    is_mobile_view   BOOLEAN NOT NULL DEFAULT FALSE,
    summary_markdown TEXT NOT NULL,
    provider         TEXT,
    model            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    hit_count        BIGINT NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_fare_rules_summary_cache_last_accessed
    ON fare_rules_summary_cache (last_accessed_at);

CREATE TABLE IF NOT EXISTS usage_events (
    id                BIGSERIAL PRIMARY KEY,
    occurred_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    feature           TEXT NOT NULL,          -- summary | chat | test
    provider          TEXT,
    model             TEXT,
    status            TEXT NOT NULL,          -- ok | error
    error_code        TEXT,
    cache_status      TEXT,                   -- redis-hit | postgres-hit | generated | n/a
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    total_tokens      INTEGER,
    latency_ms        INTEGER,
    lang              TEXT,
    api_key_id        BIGINT,
    convo_id          TEXT,
    request_id        TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_events_occurred_at ON usage_events (occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_feature ON usage_events (feature, occurred_at DESC);
