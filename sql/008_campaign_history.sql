-- sql/008_campaign_history.sql
-- Tworzy tabelę apollo.press_campaign_history do przechowywania historii wysyłek kampanii.
-- Unikalna kombinacja lower(email) + article_url — przy konflikcie aktualizuje run_at.
-- Idempotentna — bezpieczna do wielokrotnego uruchomienia.

CREATE TABLE IF NOT EXISTS apollo.press_campaign_history (
    id               BIGSERIAL    PRIMARY KEY,
    email            TEXT         NOT NULL,
    full_name        TEXT,
    company_name     TEXT,
    job_title        TEXT,
    tier             TEXT,
    article_url      TEXT,
    article_title    TEXT,
    source_name      TEXT,
    press_type       TEXT,
    industry         TEXT,
    campaign_status  TEXT         NOT NULL DEFAULT 'sent',
    campaign_run_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    raw_payload      JSONB,
    CONSTRAINT press_campaign_history_email_article_uq
        UNIQUE (lower(email), article_url)
);

CREATE INDEX IF NOT EXISTS campaign_history_email_idx
    ON apollo.press_campaign_history (lower(email));

CREATE INDEX IF NOT EXISTS campaign_history_run_at_idx
    ON apollo.press_campaign_history (campaign_run_at DESC);

CREATE INDEX IF NOT EXISTS campaign_history_company_idx
    ON apollo.press_campaign_history (company_name);

CREATE INDEX IF NOT EXISTS campaign_history_full_name_idx
    ON apollo.press_campaign_history (full_name);

CREATE INDEX IF NOT EXISTS campaign_history_article_url_idx
    ON apollo.press_campaign_history (article_url);
