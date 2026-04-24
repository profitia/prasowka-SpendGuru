"""
press_db.py — Warstwa Postgres dla artykułów prasówki SpendGuru.

Używa psycopg (v3). Połączenie pobierane ze zmiennej środowiskowej DATABASE_URL.

Upsert po article_url (UNIQUE). Przy konflikcie:
- Aktualizuje wszystkie pola z wyjątkiem tier1_email, tier2_email
  i apollo_status, jeśli były już ręcznie ustawione.

Funkcje:
    get_connection()                → psycopg.Connection
    ensure_press_tables()           → tworzy tabelę jeśli nie istnieje
    upsert_press_article(article)   → upsert jednego artykułu
    upsert_press_articles(articles) → upsert listy artykułów (batched)
    article_exists(article_url)     → bool
    load_press_articles()           → list[dict]
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date
from typing import Optional

log = logging.getLogger("news.press_db")

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE SCHEMA IF NOT EXISTS apollo;

CREATE TABLE IF NOT EXISTS apollo.press_articles (
    id                   BIGSERIAL PRIMARY KEY,
    article_id           TEXT NOT NULL,
    article_url          TEXT NOT NULL UNIQUE,
    article_title        TEXT,
    article_date         DATE,
    source_name          TEXT,
    company_name         TEXT,
    industry             TEXT,
    press_type           TEXT,
    tier1_person         TEXT,
    tier1_position       TEXT,
    tier1_email          TEXT,
    tier2_person         TEXT,
    tier2_position       TEXT,
    tier2_email          TEXT,
    reason               TEXT,
    context              TEXT,
    apollo_status        TEXT NOT NULL DEFAULT 'waiting',
    raw_payload          JSONB,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    data_quality_status  TEXT NOT NULL DEFAULT 'unknown',
    data_quality_notes   TEXT,
    reviewed_at          TIMESTAMPTZ
);

-- Idempotent migrations for existing tables (safe to run multiple times)
ALTER TABLE apollo.press_articles
    ADD COLUMN IF NOT EXISTS data_quality_status TEXT NOT NULL DEFAULT 'unknown';
ALTER TABLE apollo.press_articles
    ADD COLUMN IF NOT EXISTS data_quality_notes TEXT;
ALTER TABLE apollo.press_articles
    ADD COLUMN IF NOT EXISTS reviewed_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS press_articles_article_url_idx    ON apollo.press_articles (article_url);
CREATE INDEX IF NOT EXISTS press_articles_company_name_idx   ON apollo.press_articles (company_name);
CREATE INDEX IF NOT EXISTS press_articles_source_name_idx    ON apollo.press_articles (source_name);
CREATE INDEX IF NOT EXISTS press_articles_industry_idx       ON apollo.press_articles (industry);
CREATE INDEX IF NOT EXISTS press_articles_press_type_idx     ON apollo.press_articles (press_type);
CREATE INDEX IF NOT EXISTS press_articles_tier1_person_idx   ON apollo.press_articles (tier1_person);
CREATE INDEX IF NOT EXISTS press_articles_tier2_person_idx   ON apollo.press_articles (tier2_person);
CREATE INDEX IF NOT EXISTS press_articles_apollo_status_idx  ON apollo.press_articles (apollo_status);
CREATE INDEX IF NOT EXISTS press_articles_article_date_idx   ON apollo.press_articles (article_date);
CREATE INDEX IF NOT EXISTS press_articles_data_quality_idx   ON apollo.press_articles (data_quality_status);
"""

# Upsert: ON CONFLICT (article_url)
# - Preserve tier1_email / tier2_email if already set (ręcznie dodany email)
# - Preserve apollo_status if manually changed (anything other than 'Nie wysłany')
_UPSERT_SQL = """
INSERT INTO apollo.press_articles (
    article_id, article_url, article_title, article_date,
    source_name, company_name, industry, press_type,
    tier1_person, tier1_position,
    tier2_person, tier2_position,
    reason, context, raw_payload,
    data_quality_status
) VALUES (
    %(article_id)s, %(article_url)s, %(article_title)s, %(article_date)s,
    %(source_name)s, %(company_name)s, %(industry)s, %(press_type)s,
    %(tier1_person)s, %(tier1_position)s,
    %(tier2_person)s, %(tier2_position)s,
    %(reason)s, %(context)s, %(raw_payload)s,
    %(data_quality_status)s
)
ON CONFLICT (article_url) DO UPDATE SET
    article_id     = EXCLUDED.article_id,
    article_title  = EXCLUDED.article_title,
    article_date   = EXCLUDED.article_date,
    source_name    = EXCLUDED.source_name,
    company_name   = EXCLUDED.company_name,
    industry       = EXCLUDED.industry,
    press_type     = EXCLUDED.press_type,
    tier1_person   = EXCLUDED.tier1_person,
    tier1_position = EXCLUDED.tier1_position,
    tier2_person   = EXCLUDED.tier2_person,
    tier2_position = EXCLUDED.tier2_position,
    reason         = EXCLUDED.reason,
    context        = EXCLUDED.context,
    raw_payload    = EXCLUDED.raw_payload,
    tier1_email = CASE
        WHEN apollo.press_articles.tier1_email IS NOT NULL
             AND apollo.press_articles.tier1_email <> ''
        THEN apollo.press_articles.tier1_email
        ELSE EXCLUDED.tier1_email
    END,
    tier2_email = CASE
        WHEN apollo.press_articles.tier2_email IS NOT NULL
             AND apollo.press_articles.tier2_email <> ''
        THEN apollo.press_articles.tier2_email
        ELSE EXCLUDED.tier2_email
    END,
    apollo_status = CASE
        WHEN apollo.press_articles.apollo_status IN ('sent', 'running')
        THEN apollo.press_articles.apollo_status
        ELSE 'waiting'
    END,
    -- Zachowaj data_quality_status jeśli już recenzowany (ok/rejected),
    -- w przeciwnym razie ustaw na 'unknown' (nowe dane z pipeline)
    data_quality_status = CASE
        WHEN apollo.press_articles.data_quality_status IN ('ok', 'rejected')
        THEN apollo.press_articles.data_quality_status
        ELSE 'unknown'
    END,
    updated_at = now()
"""

_EXISTS_SQL = """
SELECT 1 FROM apollo.press_articles WHERE article_url = %(url)s LIMIT 1
"""

_LOAD_SQL = """
SELECT
    id, article_id, article_url, article_title,
    article_date, source_name, company_name, industry,
    press_type, tier1_person, tier1_position, tier1_email,
    tier2_person, tier2_position, tier2_email,
    reason, context, apollo_status, created_at, updated_at,
    COALESCE(data_quality_status, 'unknown') AS data_quality_status,
    data_quality_notes
FROM apollo.press_articles
WHERE COALESCE(data_quality_status, 'unknown') <> 'rejected'
ORDER BY article_date DESC NULLS LAST, created_at DESC
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _sanitize_db_url(raw: str) -> str:
    """
    Czyści DATABASE_URL z typowych problemów przy wczytaniu z pliku .env:
    - usuwa otaczające cudzysłowy (pojedyncze lub podwójne)
    - przycina białe znaki
    Nie modyfikuje wartości gdy jest poprawna.
    """
    url = raw.strip()
    if len(url) >= 2 and url[0] in ('"', "'") and url[-1] == url[0]:
        url = url[1:-1].strip()
    return url


def _log_db_host(url: str) -> None:
    """Loguje host bazy bez hasła (bezpiecznie)."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        safe = f"{parsed.scheme}://{parsed.hostname}{parsed.path}"
        if parsed.port:
            safe = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}{parsed.path}"
        log.info("[DB] Łączenie z: %s", safe)
    except Exception:
        log.info("[DB] Łączenie z bazą danych...")


def get_connection():
    """
    Zwraca połączenie psycopg (v3).
    Wymaga DATABASE_URL w zmiennych środowiskowych.
    Obsługuje otaczające cudzysłowy i znak & w connection stringu.
    """
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise ImportError(
            "Brak modułu psycopg. Zainstaluj: pip install 'psycopg[binary]'"
        ) from exc

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url or not raw_url.strip():
        raise EnvironmentError(
            "Brak zmiennej środowiskowej DATABASE_URL.\n"
            "Ustaw ją w pliku .env lub eksportuj przed uruchomieniem:\n"
            '  DATABASE_URL="postgresql://USER:PASS@HOST/neondb?sslmode=require"'
        )

    url = _sanitize_db_url(raw_url)
    if not url.startswith(("postgresql://", "postgres://")):
        raise EnvironmentError(
            f"DATABASE_URL ma nieprawidłowy format (musi zaczynać się od "
            f"'postgresql://' lub 'postgres://').\n"
            f"Aktualna wartość zaczyna się od: {url[:40]!r}"
        )

    _log_db_host(url)
    try:
        return psycopg.connect(url)
    except Exception as exc:
        raise ConnectionError(
            f"Nie można połączyć się z bazą danych: {exc}\n"
            f"Sprawdź DATABASE_URL i dostępność serwera."
        ) from exc


# ---------------------------------------------------------------------------
# Schema management
# ---------------------------------------------------------------------------

def ensure_press_tables() -> None:
    """
    Tworzy tabelę apollo.press_articles i indeksy jeśli nie istnieją.
    Idempotentna — bezpieczna do wielokrotnego wywołania.
    """
    with get_connection() as conn:
        conn.execute(_CREATE_TABLE_SQL)
        conn.commit()
    log.debug("ensure_press_tables: OK")


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def _parse_date(val: str | None) -> Optional[date]:
    """Konwertuje string daty na date lub None."""
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(val.strip(), fmt).date()
        except ValueError:
            continue
    return None


def _to_db_row(article: dict) -> dict:
    """
    Mapuje słownik artykułu (format site_article z orchestratora)
    na słownik parametrów do upsert SQL.

    Oczekiwane klucze wejściowe (site article schema):
        id, source_url, title, article_date, source_name, company,
        industry, press_type, tier1_person, tier1_position,
        tier2_person, tier2_position, reason, context,
        raw_payload (opcjonalnie — pełny surowy artykuł)
    """
    raw = article.get("raw_payload")
    raw_json: Optional[str] = None
    if raw is not None:
        try:
            raw_json = json.dumps(raw, ensure_ascii=False)
        except (TypeError, ValueError):
            raw_json = None

    return {
        "article_id":    article.get("id") or "",
        "article_url":   article.get("source_url") or "",
        "article_title": article.get("title") or None,
        "article_date":  _parse_date(article.get("article_date")),
        "source_name":   article.get("source_name") or None,
        "company_name":  article.get("company") or None,
        "industry":      article.get("industry") or None,
        "press_type":    article.get("press_type") or None,
        "tier1_person":  article.get("tier1_person") or None,
        "tier1_position": article.get("tier1_position") or None,
        "tier2_person":  article.get("tier2_person") or None,
        "tier2_position": article.get("tier2_position") or None,
        "reason":              article.get("reason") or None,
        "context":             article.get("context") or None,
        "raw_payload":         raw_json,
        "data_quality_status": article.get("data_quality_status") or "unknown",
    }


def upsert_press_article(article: dict) -> None:
    """
    Upsert jednego artykułu do apollo.press_articles.

    article: słownik w formacie site_article (z _map_to_site_article w orchestratorze),
             opcjonalnie z polem 'raw_payload' zawierającym surowy artykuł.
    """
    row = _to_db_row(article)
    url = row["article_url"]
    if not url:
        log.warning("Pominięto artykuł bez article_url: %s", article.get("title"))
        return

    with get_connection() as conn:
        conn.execute(_UPSERT_SQL, row)
        conn.commit()
    log.debug("upsert_press_article: %s", url)


def upsert_press_articles(articles: list[dict]) -> int:
    """
    Batch upsert listy artykułów do apollo.press_articles.

    Każdy artykuł to słownik w formacie site_article z orchestratora.
    Zwraca liczbę pomyślnie zapisanych rekordów.
    """
    if not articles:
        return 0

    rows = []
    for a in articles:
        row = _to_db_row(a)
        if not row["article_url"]:
            log.warning("Pominięto artykuł bez article_url: %s", a.get("title"))
            continue
        rows.append(row)

    if not rows:
        return 0

    import psycopg  # type: ignore
    saved = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for row in rows:
                try:
                    cur.execute(_UPSERT_SQL, row)
                    saved += 1
                except Exception as exc:
                    log.error(
                        "Błąd upsert dla %s: %s", row.get("article_url"), exc
                    )
                    conn.rollback()
        conn.commit()

    log.info("upsert_press_articles: zapisano %d / %d artykułów", saved, len(rows))
    return saved


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------

def article_exists(article_url: str) -> bool:
    """Sprawdza czy artykuł o podanym URL już istnieje w bazie."""
    with get_connection() as conn:
        cur = conn.execute(_EXISTS_SQL, {"url": article_url})
        return cur.fetchone() is not None


def load_press_articles() -> list[dict]:
    """
    Zwraca wszystkie artykuły z apollo.press_articles jako listę słowników.
    Pola zgodne z formatem site_article (articles.json) dla łatwej integracji.
    """
    import psycopg.rows  # type: ignore

    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(_LOAD_SQL)
            rows = cur.fetchall()

    result = []
    for r in rows:
        result.append({
            "id":             r["article_id"],
            "source_url":     r["article_url"],
            "title":          r["article_title"] or "",
            "article_date":   r["article_date"].isoformat() if r["article_date"] else "",
            "source_name":    r["source_name"] or "",
            "company":        r["company_name"] or "",
            "industry":       r["industry"] or "",
            "press_type":     r["press_type"] or "",
            "tier1_person":   r["tier1_person"] or "",
            "tier1_position": r["tier1_position"] or "",
            "tier1_email":    r["tier1_email"] or "",
            "tier2_person":   r["tier2_person"] or "",
            "tier2_position": r["tier2_position"] or "",
            "tier2_email":    r["tier2_email"] or "",
            "reason":         r["reason"] or "",
            "context":              r["context"] or "",
            "apollo_status":        r["apollo_status"],
            "data_quality_status":  r["data_quality_status"] or "unknown",
            "data_quality_notes":   r["data_quality_notes"] or "",
            "created_at":           r["created_at"].isoformat() if r["created_at"] else "",
            "updated_at":           r["updated_at"].isoformat() if r["updated_at"] else "",
        })
    return result


# ---------------------------------------------------------------------------
# Contact field updates (API endpoints)
# ---------------------------------------------------------------------------

_UPDATE_TIER1_EMAIL_SQL = """
UPDATE apollo.press_articles
SET tier1_email = %(email)s, updated_at = now()
WHERE article_url = %(article_url)s
RETURNING article_id, article_url, tier1_email, tier2_email, apollo_status, updated_at
"""

_UPDATE_TIER2_EMAIL_SQL = """
UPDATE apollo.press_articles
SET tier2_email = %(email)s, updated_at = now()
WHERE article_url = %(article_url)s
RETURNING article_id, article_url, tier1_email, tier2_email, apollo_status, updated_at
"""

_UPDATE_APOLLO_STATUS_SQL = """
UPDATE apollo.press_articles
SET apollo_status = %(apollo_status)s, updated_at = now()
WHERE article_url = %(article_url)s
RETURNING article_id, article_url, tier1_email, tier2_email, apollo_status, updated_at
"""


def _row_to_contact_response(row: dict) -> dict:
    """Mapuje wiersz z bazy na odpowiedź API."""
    return {
        "article_id":    row["article_id"],
        "article_url":   row["article_url"],
        "tier1_email":   row.get("tier1_email") or "",
        "tier2_email":   row.get("tier2_email") or "",
        "apollo_status": row.get("apollo_status") or "waiting",
        "updated_at":    row["updated_at"].isoformat() if row.get("updated_at") else "",
    }


def update_tier_email(article_url: str, tier: str, email: str) -> Optional[dict]:
    """
    Aktualizuje tier1_email lub tier2_email dla artykułu.
    Zwraca zaktualizowany rekord lub None jeśli artykuł nie znaleziony.
    """
    import psycopg.rows  # type: ignore

    if tier == "tier_1_c_level":
        sql = _UPDATE_TIER1_EMAIL_SQL
    elif tier == "tier_2_procurement_management":
        sql = _UPDATE_TIER2_EMAIL_SQL
    else:
        raise ValueError(f"Nieznany tier: {tier!r}")

    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(sql, {"email": email, "article_url": article_url})
            row = cur.fetchone()
        conn.commit()

    if row is None:
        return None
    return _row_to_contact_response(row)


def update_apollo_status(article_url: str, apollo_status: str) -> Optional[dict]:
    """
    Aktualizuje apollo_status dla artykułu.
    Dozwolone wartości: waiting, running, sent.
    Zwraca zaktualizowany rekord lub None jeśli artykuł nie znaleziony.
    """
    import psycopg.rows  # type: ignore

    allowed = {"waiting", "running", "sent"}
    if apollo_status not in allowed:
        raise ValueError(
            f"Nieprawidłowy apollo_status: {apollo_status!r}. "
            f"Dozwolone: {sorted(allowed)}"
        )

    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                _UPDATE_APOLLO_STATUS_SQL,
                {"apollo_status": apollo_status, "article_url": article_url},
            )
            row = cur.fetchone()
            rowcount = cur.rowcount
        conn.commit()

    log.info(
        "update_apollo_status: url=%s status=%s rowcount=%d",
        article_url[:60], apollo_status, rowcount,
    )

    if row is None:
        log.warning("update_apollo_status: artykuł nie znaleziony dla url=%s", article_url[:60])
        return None
    return _row_to_contact_response(row)


# ---------------------------------------------------------------------------
# Campaign history
# ---------------------------------------------------------------------------

# Rozbite na osobne statement-y — psycopg v3 nie obsługuje multi-statement w jednym execute().
_CREATE_CAMPAIGN_HISTORY_STMTS: list[str] = [
    # Tabela bez expression constraint (Postgres nie obsługuje UNIQUE(expr) inline)
    """
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
        run_count        INT          NOT NULL DEFAULT 1,
        raw_payload      JSONB
    )
    """,
    # run_count dla istniejących tabel (idempotentna)
    """
    ALTER TABLE apollo.press_campaign_history
        ADD COLUMN IF NOT EXISTS run_count INT NOT NULL DEFAULT 1
    """,
    # Unikalny expression index (jedyna poprawna składnia dla lower(email))
    """
    CREATE UNIQUE INDEX IF NOT EXISTS ux_press_campaign_history_email_article
        ON apollo.press_campaign_history (lower(email), article_url)
    """,
    """
    CREATE INDEX IF NOT EXISTS campaign_history_email_idx
        ON apollo.press_campaign_history (lower(email))
    """,
    """
    CREATE INDEX IF NOT EXISTS campaign_history_run_at_idx
        ON apollo.press_campaign_history (campaign_run_at DESC)
    """,
    """
    CREATE INDEX IF NOT EXISTS campaign_history_company_idx
        ON apollo.press_campaign_history (company_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS campaign_history_full_name_idx
        ON apollo.press_campaign_history (full_name)
    """,
    """
    CREATE INDEX IF NOT EXISTS campaign_history_article_url_idx
        ON apollo.press_campaign_history (article_url)
    """,
]

_INSERT_CAMPAIGN_HISTORY_SQL = """
INSERT INTO apollo.press_campaign_history (
    email, full_name, company_name, job_title, tier,
    article_url, article_title, source_name, press_type, industry,
    campaign_status, campaign_run_at, run_count
) VALUES (
    %(email)s, %(full_name)s, %(company_name)s, %(job_title)s, %(tier)s,
    %(article_url)s, %(article_title)s, %(source_name)s, %(press_type)s, %(industry)s,
    %(campaign_status)s, now(), 1
)
ON CONFLICT (lower(email), article_url)
DO UPDATE SET
    campaign_run_at = now(),
    campaign_status = EXCLUDED.campaign_status,
    run_count       = apollo.press_campaign_history.run_count + 1,
    full_name       = EXCLUDED.full_name,
    company_name    = EXCLUDED.company_name,
    job_title       = EXCLUDED.job_title,
    tier            = EXCLUDED.tier,
    article_title   = EXCLUDED.article_title,
    source_name     = EXCLUDED.source_name,
    press_type      = EXCLUDED.press_type,
    industry        = EXCLUDED.industry
RETURNING id, email, campaign_run_at, run_count
"""

_LOAD_CAMPAIGN_HISTORY_BY_EMAIL_SQL = """
SELECT
    id, email, full_name, company_name, job_title, tier,
    article_url, article_title, source_name, press_type, industry,
    campaign_status, campaign_run_at, created_at, run_count
FROM apollo.press_campaign_history
WHERE lower(email) = lower(%(email)s)
ORDER BY campaign_run_at DESC
"""


def ensure_campaign_history_table() -> None:
    """Tworzy tabelę press_campaign_history jeśli nie istnieje (idempotentna)."""
    with get_connection() as conn:
        for stmt in _CREATE_CAMPAIGN_HISTORY_STMTS:
            conn.execute(stmt)
        conn.commit()
    log.debug("ensure_campaign_history_table: OK")


def insert_campaign_history(
    *,
    email: str,
    full_name: str = "",
    company_name: str = "",
    job_title: str = "",
    tier: str = "",
    article_url: str = "",
    article_title: str = "",
    source_name: str = "",
    press_type: str = "",
    industry: str = "",
    campaign_status: str = "sent",
) -> Optional[dict]:
    """
    Zapisuje lub aktualizuje rekord w historii kampanii.
    UNIQUE (lower(email), article_url) — przy ponownym uruchomieniu dla tej
    samej kombinacji aktualizuje campaign_run_at zamiast duplikować.
    """
    import psycopg.rows  # type: ignore

    params = {
        "email":           email.strip().lower(),
        "full_name":       full_name or None,
        "company_name":    company_name or None,
        "job_title":       job_title or None,
        "tier":            tier or None,
        "article_url":     article_url or None,
        "article_title":   article_title or None,
        "source_name":     source_name or None,
        "press_type":      press_type or None,
        "industry":        industry or None,
        "campaign_status": campaign_status,
    }

    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(_INSERT_CAMPAIGN_HISTORY_SQL, params)
            row = cur.fetchone()
        conn.commit()

    log.info(
        "insert_campaign_history: email=%s article_url=%s id=%s run_count=%s",
        email[:40], (article_url or "")[:60], row["id"] if row else "?", row["run_count"] if row else "?",
    )
    return dict(row) if row else None


def load_campaign_history_by_email(email: str) -> list[dict]:
    """
    Zwraca historię kampanii dla danego emaila (case-insensitive).
    Wyniki posortowane od najnowszego.
    """
    import psycopg.rows  # type: ignore

    if not email or not email.strip():
        return []

    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(_LOAD_CAMPAIGN_HISTORY_BY_EMAIL_SQL, {"email": email.strip()})
            rows = cur.fetchall()

    result = []
    for r in rows:
        result.append({
            "id":               r["id"],
            "email":            r["email"],
            "full_name":        r["full_name"] or "",
            "company_name":     r["company_name"] or "",
            "job_title":        r["job_title"] or "",
            "tier":             r["tier"] or "",
            "article_url":      r["article_url"] or "",
            "article_title":    r["article_title"] or "",
            "source_name":      r["source_name"] or "",
            "press_type":       r["press_type"] or "",
            "industry":         r["industry"] or "",
            "campaign_status":  r["campaign_status"],
            "run_count":        r["run_count"] if r["run_count"] is not None else 1,
            "campaign_run_at":  r["campaign_run_at"].isoformat() if r["campaign_run_at"] else "",
            "created_at":       r["created_at"].isoformat() if r["created_at"] else "",
        })
    return result

