-- sql/010_add_data_quality.sql
-- Dodaje kolumny jakości danych do apollo.press_articles.
-- Idempotentna — bezpieczna do wielokrotnego uruchomienia.

-- 1. Kolumny jakości
ALTER TABLE apollo.press_articles
    ADD COLUMN IF NOT EXISTS data_quality_status TEXT NOT NULL DEFAULT 'unknown',
    ADD COLUMN IF NOT EXISTS data_quality_notes  TEXT,
    ADD COLUMN IF NOT EXISTS reviewed_at         TIMESTAMPTZ;

-- 2. Indeks na data_quality_status dla szybkiego filtrowania
CREATE INDEX IF NOT EXISTS press_articles_data_quality_status_idx
    ON apollo.press_articles (data_quality_status);

-- Weryfikacja
SELECT data_quality_status, count(*) AS cnt
FROM apollo.press_articles
GROUP BY data_quality_status
ORDER BY data_quality_status;
