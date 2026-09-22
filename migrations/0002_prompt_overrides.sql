-- Prompt templates edited from the admin UI. Files under prompts/ remain the defaults.
CREATE TABLE IF NOT EXISTS prompt_overrides (
    prompt_id  TEXT PRIMARY KEY,
    content    TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by TEXT
);
