-- sql/009_add_run_count.sql
-- Dodaje kolumnę run_count do apollo.press_campaign_history.
-- Idempotentna — bezpieczna do wielokrotnego uruchomienia.

ALTER TABLE apollo.press_campaign_history
    ADD COLUMN IF NOT EXISTS run_count INT NOT NULL DEFAULT 1;
