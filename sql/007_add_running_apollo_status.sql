-- sql/007_add_running_apollo_status.sql
-- Dodaje status pośredni "running" do apollo_status.
-- Normalizuje stare/niespójne wartości.
-- Idempotentna — bezpieczna do wielokrotnego uruchomienia.

-- 1. Normalizacja starych / niespójnych wartości
UPDATE apollo.press_articles
SET apollo_status = 'waiting', updated_at = now()
WHERE apollo_status IS NULL
   OR apollo_status IN ('Nie wysłany', 'not sent', 'nie wysłany', '');

UPDATE apollo.press_articles
SET apollo_status = 'sent', updated_at = now()
WHERE apollo_status IN ('Wysłany', 'wysłany', 'SENT');

-- 2. Rekordy utknięte w "running" (np. po restarcie serwera) → z powrotem waiting
UPDATE apollo.press_articles
SET apollo_status = 'waiting', updated_at = now()
WHERE apollo_status = 'running';

-- 3. Upewnij się że DEFAULT jest ustawiony poprawnie (bezpieczna zmiana)
ALTER TABLE apollo.press_articles
    ALTER COLUMN apollo_status SET DEFAULT 'waiting';

-- Weryfikacja
SELECT apollo_status, count(*) AS cnt
FROM apollo.press_articles
GROUP BY apollo_status
ORDER BY apollo_status;
