"""
src/site_api/app.py — FastAPI backend dla Prasówki SpendGuru.

Endpointy:
  GET  /api/articles              → lista artykułów z apollo.press_articles
  POST /api/articles/contact      → zapisz email tier1/tier2
  POST /api/articles/status       → zapisz apollo_status
  GET  /health                    → health check

Uruchomienie lokalne:
  cd "Prasówki SpendGuru"
  export DATABASE_URL="postgresql://USER:PASS@HOST/neondb?sslmode=require&channel_binding=require"
  uvicorn src.site_api.app:app --reload --port 8000

Deployment (Render/Railway):
  - ustaw DATABASE_URL w zmiennych środowiskowych usługi
  - start command: uvicorn src.site_api.app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import subprocess
import sys
from datetime import date as _date
from pathlib import Path
from urllib.parse import urlparse

import urllib.request
import urllib.error
import json as _json

# ---------------------------------------------------------------------------
# Path setup — musi być przed importem lokalnych modułów
# ---------------------------------------------------------------------------
_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from fastapi import FastAPI, HTTPException, Query, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from news.press_db import (
    load_press_articles,
    update_tier_email,
    update_apollo_status,
    insert_campaign_history,
    load_campaign_history_by_email,
    ensure_campaign_history_table,
    ensure_press_tables,
    reset_stale_running_statuses,
    article_exists,
    upsert_press_article,
    get_press_article_by_url,
    reject_press_article,
    update_article_fields,
    accept_press_article,
)

# ---------------------------------------------------------------------------
# Apollo runner config
# ---------------------------------------------------------------------------
APOLLO_TIMEOUT   = int(os.environ.get("APOLLO_TIMEOUT", "180"))

# Local orchestrator mode: when APOLLO_ROOT is set the endpoint calls the full
# Kampanie Apollo orchestrator via subprocess instead of the cloud runner.
# Set APOLLO_ROOT to the absolute path of the "Kampanie Apollo" workspace folder.
APOLLO_ROOT     = os.environ.get("APOLLO_ROOT", "").strip()
APOLLO_PYTHON   = os.environ.get("APOLLO_PYTHON", sys.executable)
APOLLO_CAMPAIGN = os.environ.get("APOLLO_CAMPAIGN", "spendguru_market_news")

# Recipient for approval / notification emails (cloud path)
NOTIFICATION_EMAIL = os.environ.get("NOTIFICATION_EMAIL", "tomasz.uscinski@profitia.pl")

# ---------------------------------------------------------------------------
# GitHub Actions pipeline trigger config
# ---------------------------------------------------------------------------
_GH_PAT           = os.environ.get("GITHUB_PAT", "")
_GH_REPO_OWNER    = os.environ.get("GITHUB_REPO_OWNER", "profitia")
_GH_REPO_NAME     = os.environ.get("GITHUB_REPO_NAME", "prasowka-SpendGuru")
_GH_WORKFLOW_FILE = os.environ.get("GITHUB_WORKFLOW_FILE", "daily_prasowka.yml")
_GH_BRANCH        = os.environ.get("GITHUB_BRANCH", "main")

_GH_API_BASE      = "https://api.github.com"


def _gh_request(method: str, path: str, body: dict | None = None, pat: str = "") -> tuple[int, dict]:
    """Wykonuje request do GitHub API. Zwraca (status_code, response_dict)."""
    url = f"{_GH_API_BASE}{path}"
    data = _json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization":        f"Bearer {pat or _GH_PAT}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type":         "application/json",
            "User-Agent":           "PrasowkaSpendGuru/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return resp.status, (_json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body_err = _json.loads(raw)
        except Exception:
            body_err = {"message": raw.decode(errors="replace")}
        return exc.code, body_err

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
log = logging.getLogger("site_api")

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Prasówka SpendGuru API",
    version="1.0.0",
    description="Backend API dla Prasówki SpendGuru — przechowuje kontakty i statusy Apollo w Postgres/Neon.",
)

# CORS — pozwala na dostęp z GitHub Pages i localhost
_raw_origins = os.environ.get("CORS_ORIGINS", "")
_default_origins = [
    "https://profitia.github.io",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
if _raw_origins.strip():
    allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()]
else:
    allowed_origins = _default_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup() -> None:
    """Inicjalizacja przy starcie: migracje DB, reset stuck statusów."""
    # Migracje tabeli press_articles (idempotentne)
    try:
        ensure_press_tables()
        log.info("press_articles table/migrations OK")
    except Exception:
        log.exception("Nie udało się wykonać migracji press_articles przy starcie")

    # Tabela historii kampanii
    try:
        ensure_campaign_history_table()
        log.info("press_campaign_history table OK")
    except Exception:
        log.exception("Nie udało się utworzyć tabeli campaign_history przy starcie")

    # Reset artykułów ze statusem 'running' (stuck po restarcie serwera)
    try:
        reset_count = reset_stale_running_statuses()
        if reset_count:
            log.warning("Startup: zresetowano %d artykułów ze stale 'running' → 'waiting'", reset_count)
    except Exception:
        log.exception("Nie udało się zresetować stale 'running' statusów")


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------
class ContactRequest(BaseModel):
    article_url: str
    tier: str          # "tier_1_c_level" | "tier_2_procurement_management"
    email: str

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        allowed = {"tier_1_c_level", "tier_2_procurement_management"}
        if v not in allowed:
            raise ValueError(f"tier musi być jednym z: {sorted(allowed)}")
        return v

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip()
        if v and "@" not in v:
            raise ValueError("Nieprawidłowy format email")
        return v


class StatusRequest(BaseModel):
    article_url: str
    apollo_status: str

    @field_validator("apollo_status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        allowed = {"waiting", "running", "sent"}
        if v not in allowed:
            raise ValueError(f"apollo_status musi być jednym z: {allowed}")
        return v


class RunAutoRequest(BaseModel):
    article_url: str
    company_name: str = ""
    full_name: str = ""
    email: str
    tier: str
    job_title: str = ""
    context: str = ""  # article context / trigger summary — forwarded to orchestrator

    @field_validator("email")
    @classmethod
    def validate_email_run(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v:
            raise ValueError("Nieprawidłowy format email")
        return v

    @field_validator("tier")
    @classmethod
    def validate_tier_run(cls, v: str) -> str:
        allowed = {"tier_1_c_level", "tier_2_procurement_management"}
        if v not in allowed:
            raise ValueError(f"tier musi być jednym z: {sorted(allowed)}")
        return v


class AddArticleRequest(BaseModel):
    url: str

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("URL nie może być pusty")
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError("URL musi zaczynać się od http:// lub https://")
        if not parsed.netloc:
            raise ValueError("Nieprawidłowy URL — brak domeny")
        return v


class RejectArticleRequest(BaseModel):
    article_url: str

    @field_validator("article_url")
    @classmethod
    def validate_article_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("article_url nie może być pusty")
        return v


class UpdateFieldsRequest(BaseModel):
    article_url:    str
    company:        str = ""
    tier1_person:   str = ""
    tier1_position: str = ""
    tier2_person:   str = ""
    tier2_position: str = ""

    @field_validator("article_url")
    @classmethod
    def validate_fields_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("article_url nie może być pusty")
        return v


class AcceptArticleRequest(BaseModel):
    article_url: str

    @field_validator("article_url")
    @classmethod
    def validate_accept_url(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("article_url nie może być pusty")
        return v


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    """Health check."""
    return {"status": "ok"}


@app.get("/api/articles")
async def get_articles(
    quality: str = Query(
        default="",
        description="Filtruj po data_quality_status. Wartości: ok,unknown,needs_review,rejected. "
                    "Można podać kilka po przecinku. Domyślnie: ok,unknown (bez rejected).",
    ),
) -> list[dict]:
    """
    Zwraca artykuły z apollo.press_articles.
    Format zgodny z data/articles.json, z dodatkowymi polami:
      tier1_email, tier2_email, apollo_status, data_quality_status, updated_at.
    Domyślnie pomija rekordy 'rejected' (chyba że explicite podano quality=rejected).
    """
    try:
        articles = load_press_articles()
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w GET /api/articles: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w GET /api/articles")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    # Quality filter: domyślnie ok + unknown (bez rejected)
    _allowed_statuses = {"ok", "unknown", "needs_review", "rejected"}
    requested = {s.strip().lower() for s in quality.split(",") if s.strip()}
    requested = requested & _allowed_statuses  # tylko znane statusy
    if not requested:
        requested = {"ok", "unknown"}

    # Uwaga: _LOAD_SQL już filtruje rejected — tu dodatkowo uwzględniamy
    # wszystkie żądane statusy (w tym ewentualne needs_review)
    filtered = [a for a in articles if (a.get("data_quality_status") or "unknown") in requested]
    log.debug("GET /api/articles: total=%d, quality=%s, returned=%d", len(articles), requested, len(filtered))
    return filtered


@app.post("/api/articles/add")
async def add_article(body: AddArticleRequest) -> dict:
    """
    Ręcznie dodaje artykuł po URL. Używa tego samego flow co artykuły automatyczne
    (upsert do apollo.press_articles, widoczny w UI jak każdy inny artykuł).

    - Duplikat: zwraca {"status": "duplicate", "article": <istniejący rekord>}
    - Nowy:     scrape tytułu, upsert, zwraca {"status": "created", "article": <nowy rekord>}
    """
    url = body.url
    log.info("POST /api/articles/add: %s", url[:120])

    try:
        # Sprawdź duplikat
        if article_exists(url):
            existing = get_press_article_by_url(url)
            log.info("add_article: duplikat %s", url[:80])
            return {"status": "duplicate", "article": existing}

        # Scrape tytułu i tekstu (błąd scraping nie blokuje dodania)
        try:
            from news.scraper import fetch_article
            scraped = fetch_article(url)
        except Exception as exc:
            log.warning("add_article: scraping nie powiodło się dla %s: %s", url[:80], exc)
            scraped = {"url": url, "title": "", "text": "", "source_name": ""}

        # source_name z domeny URL (fallback jeśli scraper nie zwrócił)
        parsed_url = urlparse(url)
        domain = parsed_url.hostname or ""
        if domain.startswith("www."):
            domain = domain[4:]
        source_name = scraped.get("source_name") or domain

        # Generuj article_id z hasha URL
        article_id = "manual_" + hashlib.sha256(url.encode()).hexdigest()[:12]

        # Buduj słownik site_article (ten sam format co output orchestratora)
        article = {
            "id":             article_id,
            "source_url":     url,
            "title":          scraped.get("title") or "",
            "article_date":   _date.today().isoformat(),
            "source_name":    source_name,
            "company":        "",
            "industry":       "",
            "press_type":     "",
            "tier1_person":   "",
            "tier1_position": "",
            "tier2_person":   "",
            "tier2_position": "",
            "reason":         "Dodano ręcznie",
            "context":        "",
            "data_quality_status": "ok",
        }

        upsert_press_article(article)

        # Wczytaj z DB żeby zwrócić pełny rekord (z polami DB jak created_at)
        saved = get_press_article_by_url(url)
        log.info("add_article: zapisano %s (id=%s)", url[:80], article_id)
        return {"status": "created", "article": saved or article}

    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/add: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except HTTPException:
        raise
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/add")
        raise HTTPException(status_code=500, detail="Błąd serwera")


@app.post("/api/articles/reject")
async def reject_article(body: RejectArticleRequest) -> dict:
    """
    Trwale odrzuca artykuł: ustawia data_quality_status='rejected'.

    Artykuł znika z UI i nie wraca po rebuildzie danych z bazy.
    Nie usuwa fizycznie wiersza — zachowuje historię kampanii i chroni przed
    ponownym dodaniem przez pipeline (UPSERT ON CONFLICT zachowuje 'rejected').
    """
    log.info("POST /api/articles/reject: %s", body.article_url[:120])

    try:
        found = reject_press_article(body.article_url)
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/reject: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/reject")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Artykuł nie znaleziony: {body.article_url}",
        )

    log.info("reject_article: odrzucono %s", body.article_url[:80])
    return {"ok": True}


@app.post("/api/articles/fields")
async def update_fields(body: UpdateFieldsRequest) -> dict:
    """
    Aktualizuje pola edytowalne artykułu: firma, osoby, stanowiska.

    Body: { article_url, company, tier1_person, tier1_position, tier2_person, tier2_position }
    Zwraca zaktualizowany rekord z nowymi wartościami pól.
    """
    log.info("POST /api/articles/fields: %s", body.article_url[:120])
    try:
        result = update_article_fields(
            body.article_url,
            body.company,
            body.tier1_person,
            body.tier1_position,
            body.tier2_person,
            body.tier2_position,
        )
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/fields: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/fields")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artykuł nie znaleziony: {body.article_url}",
        )

    log.info(
        "update_fields: zaktualizowano pola dla %s (company=%r, t1=%r, t2=%r)",
        body.article_url[:60], body.company[:30] if body.company else "", body.tier1_person[:30] if body.tier1_person else "", body.tier2_person[:30] if body.tier2_person else "",
    )
    return result


@app.post("/api/articles/accept")
async def accept_article(body: AcceptArticleRequest) -> dict:
    """
    Ustawia data_quality_status='ok' dla artykułu (ręczna akceptacja przez użytkownika).

    Body: { article_url }
    Zwraca {"ok": True}.
    """
    log.info("POST /api/articles/accept: %s", body.article_url[:120])
    try:
        found = accept_press_article(body.article_url)
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/accept: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/accept")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    if not found:
        raise HTTPException(
            status_code=404,
            detail=f"Artykuł nie znaleziony: {body.article_url}",
        )

    log.info("accept_article: zaakceptowano %s", body.article_url[:80])
    return {"ok": True}


@app.post("/api/pipeline/trigger")
async def trigger_pipeline() -> dict:
    """
    Wyzwala ręczne uruchomienie pipeline'u prasówki przez GitHub Actions
    (workflow_dispatch na daily_prasowka.yml).

    Wymaga zmiennej środowiskowej GITHUB_PAT z tokenem PAT (scope: workflow).
    """
    pat = os.environ.get("GITHUB_PAT", "")
    if not pat:
        raise HTTPException(
            status_code=503,
            detail="Brak konfiguracji GITHUB_PAT — ręczne uruchamianie pipeline'u jest niedostępne.",
        )

    path = (
        f"/repos/{_GH_REPO_OWNER}/{_GH_REPO_NAME}"
        f"/actions/workflows/{_GH_WORKFLOW_FILE}/dispatches"
    )
    status, resp_body = _gh_request("POST", path, body={"ref": _GH_BRANCH}, pat=pat)

    if status == 204:
        log.info("pipeline/trigger: workflow_dispatch OK (%s)", _GH_WORKFLOW_FILE)
        runs_url = (
            f"https://github.com/{_GH_REPO_OWNER}/{_GH_REPO_NAME}/actions"
            f"/workflows/{_GH_WORKFLOW_FILE}"
        )
        return {
            "ok":       True,
            "message":  "Pipeline uruchomiony. Artykuły pojawią się w ciągu kilku minut.",
            "runs_url": runs_url,
        }

    log.error("pipeline/trigger: GitHub zwrócił %d: %s", status, resp_body)
    detail = resp_body.get("message") or f"GitHub HTTP {status}"
    raise HTTPException(status_code=502, detail=f"GitHub API: {detail}")


@app.get("/api/pipeline/status")
async def pipeline_status() -> dict:
    """
    Zwraca status ostatniego uruchomienia pipeline'u (GitHub Actions).

    Wymaga GITHUB_PAT.
    """
    pat = os.environ.get("GITHUB_PAT", "")
    if not pat:
        return {"available": False, "reason": "GITHUB_PAT nie skonfigurowany"}

    path = (
        f"/repos/{_GH_REPO_OWNER}/{_GH_REPO_NAME}"
        f"/actions/workflows/{_GH_WORKFLOW_FILE}/runs"
        f"?per_page=1&branch={_GH_BRANCH}"
    )
    status, resp_body = _gh_request("GET", path, pat=pat)

    if status != 200:
        log.warning("pipeline/status: GitHub zwrócił %d", status)
        return {"available": False, "reason": f"GitHub HTTP {status}"}

    runs = resp_body.get("workflow_runs", [])
    if not runs:
        return {"available": True, "last_run": None}

    run = runs[0]
    return {
        "available":  True,
        "last_run": {
            "id":         run.get("id"),
            "status":     run.get("status"),       # queued | in_progress | completed
            "conclusion": run.get("conclusion"),   # success | failure | cancelled | None
            "started_at": run.get("run_started_at") or run.get("created_at"),
            "html_url":   run.get("html_url"),
        },
    }


@app.post("/api/articles/contact")
async def save_contact(body: ContactRequest) -> dict:
    """
    Zapisuje email dla tier1 lub tier2 artykułu.

    Body: { article_url, tier, email }
    Zwraca zaktualizowany rekord z tier1_email, tier2_email, apollo_status.
    """
    try:
        result = update_tier_email(body.article_url, body.tier, body.email)
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/contact: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/contact")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artykuł nie znaleziony w bazie: {body.article_url}",
        )

    log.info("Zapisano email [%s] dla %s → %s", body.tier, body.article_url[:60], body.email)
    return result


@app.post("/api/articles/status")
async def save_status(body: StatusRequest) -> dict:
    """
    Aktualizuje apollo_status artykułu.

    Body: { article_url, apollo_status }
    Zwraca zaktualizowany rekord.
    """
    try:
        result = update_apollo_status(body.article_url, body.apollo_status)
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w POST /api/articles/status: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w POST /api/articles/status")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Artykuł nie znaleziony w bazie: {body.article_url}",
        )

    log.info("Status → %s dla %s", body.apollo_status, body.article_url[:60])
    return result


@app.post("/api/apollo/run-auto")
async def run_apollo_auto(body: RunAutoRequest, background_tasks: BackgroundTasks) -> dict:
    """
    Uruchamia kampanię Apollo dla wybranego artykułu.

    Dwa tryby:
    - LOCAL (APOLLO_ROOT ustawiony): wywołuje pełny orchestrator Kampanie Apollo
      via subprocess (manual mode). Obsługuje AI wiadomości, dynamiczną sekwencję
      i email approwalowy.
    - CLOUD (APOLLO_ROOT nie ustawiony, Render): używa uproszczonego apollo_runner
      + wysyła email approwalowy bezpośrednio przez Office365 Graph API.

    ENV wymagane dla trybu CLOUD:
      APOLLO_API_KEY, APOLLO_SEQUENCE_ID, APOLLO_SENDER_EMAIL_ACCOUNT_ID
      MSAL_TOKEN_CACHE_B64 (dla emaila approwalowego)

    ENV wymagane dla trybu LOCAL:
      APOLLO_ROOT — ścieżka do workspace'u Kampanie Apollo
      APOLLO_PYTHON — (opcjonalnie) python z venv Kampanie Apollo
    """
    mode = "local_orchestrator" if APOLLO_ROOT else "cloud_runner"
    log.info(
        "[apollo] POST /api/apollo/run-auto: url=%s tier=%s email=%s mode=%s",
        body.article_url[:80], body.tier, body.email, mode,
    )
    log.info(
        "[apollo] Payload: company=%r full_name=%r job_title=%r context_len=%d",
        body.company_name[:50] if body.company_name else "",
        body.full_name[:50] if body.full_name else "",
        body.job_title[:50] if body.job_title else "",
        len(body.context),
    )

    # Mark as running immediately
    try:
        update_apollo_status(body.article_url, "running")
        log.info("[apollo] apollo_status → running dla %s", body.article_url[:60])
    except Exception:
        log.warning("[apollo] Nie udało się ustawić running (ignorujem)")

    def _revert_to_waiting() -> None:
        try:
            update_apollo_status(body.article_url, "waiting")
            log.info("[apollo] apollo_status → waiting (revert)")
        except Exception:
            log.exception("[apollo] Nie udało się zrevertować do waiting")

    def _save_history(campaign_status: str = "sent") -> None:
        try:
            _articles = load_press_articles()
            _art = next((a for a in _articles if a.get("source_url") == body.article_url), {})
            insert_campaign_history(
                email=body.email,
                full_name=body.full_name,
                company_name=body.company_name or _art.get("company", ""),
                job_title=body.job_title,
                tier=body.tier,
                article_url=body.article_url,
                article_title=_art.get("title", ""),
                source_name=_art.get("source_name", ""),
                press_type=_art.get("press_type", ""),
                industry=_art.get("industry", ""),
                campaign_status=campaign_status,
            )
        except Exception:
            log.exception("[apollo] Nie udało się zapisać historii kampanii")

    # -------------------------------------------------------------------
    # PATH A — LOCAL: pełny orchestrator Kampanie Apollo via subprocess
    # -------------------------------------------------------------------
    if APOLLO_ROOT:
        log.info("[apollo] Tryb LOCAL — orchestrator w: %s", APOLLO_ROOT)

        contacts_list = [{
            "email":        body.email,
            "tier":         body.tier,
            "full_name":    body.full_name,
            "job_title":    body.job_title,
            "company_name": body.company_name,
        }]
        contacts_json_str = _json.dumps(contacts_list, ensure_ascii=False)

        cmd = [
            APOLLO_PYTHON,
            "src/news/orchestrator.py",
            "manual",
            "--article-url",   body.article_url,
            "--contacts-json", contacts_json_str,
            "--campaign",      APOLLO_CAMPAIGN,
            "--verbose",
        ]
        log.info("[apollo] Komenda: %s src/news/orchestrator.py manual --article-url %s ...",
                 APOLLO_PYTHON, body.article_url[:60])

        def _run_subprocess() -> subprocess.CompletedProcess:
            env = {**os.environ, "PYTHONPATH": os.path.join(APOLLO_ROOT, "src")}
            return subprocess.run(
                cmd,
                cwd=APOLLO_ROOT,
                capture_output=True,
                text=True,
                timeout=APOLLO_TIMEOUT,
                env=env,
            )

        try:
            proc = await asyncio.to_thread(_run_subprocess)
            log.info("[apollo] orchestrator exit=%d stdout_len=%d",
                     proc.returncode, len(proc.stdout))
            if proc.stderr:
                log.debug("[apollo] orchestrator stderr (last 2000):\n%s", proc.stderr[-2000:])
        except asyncio.TimeoutError:
            _revert_to_waiting()
            return {
                "ok": False,
                "message": f"Timeout ({APOLLO_TIMEOUT}s) — orchestrator nie odpowiedział. Status przywrócony.",
                "details": {},
            }
        except Exception as exc:
            log.exception("[apollo] Błąd wywołania subprocess")
            _revert_to_waiting()
            return {"ok": False, "message": f"Błąd subprocess: {exc}", "details": {}}

        if proc.returncode != 0:
            err_snippet = (proc.stderr or "")[-500:]
            log.error("[apollo] Orchestrator błąd exit=%d: %s", proc.returncode, err_snippet)
            _revert_to_waiting()
            return {
                "ok": False,
                "message": f"Orchestrator zakończył błędem (exit {proc.returncode}). Status przywrócony.",
                "details": {"stderr": err_snippet},
            }

        # Parse JSON result from stdout
        orch_result: dict = {}
        try:
            out = proc.stdout.strip()
            # The orchestrator prints JSON as last block — find last '{'
            json_start = out.rfind('{')
            if json_start >= 0:
                orch_result = _json.loads(out[json_start:])
        except Exception as exc:
            log.warning("[apollo] Nie udało się sparsować JSON orchestratora: %s", exc)

        status = orch_result.get("status", "")
        log.info("[apollo] orchestrator status=%s sequence=%s enrolled=%s",
                 status,
                 orch_result.get("sequence_name", ""),
                 orch_result.get("contacts_enrolled", "?"))

        if status == "MANUAL_COMPLETE":
            try:
                update_apollo_status(body.article_url, "sent")
                log.info("[apollo] apollo_status → sent (orchestrator sukces)")
            except Exception:
                log.exception("[apollo] Nie udało się zapisać sent")
            _save_history("sent")
            return {
                "ok": True,
                "message": (
                    f"Kampania Apollo uruchomiona ✔"
                    f" | sekwencja: {orch_result.get('sequence_name', '')}"
                    f" | enrolled: {orch_result.get('contacts_enrolled', 0)}"
                ),
                "details": orch_result,
            }
        else:
            _revert_to_waiting()
            return {
                "ok": False,
                "message": f"Orchestrator zakończył ze statusem: {status or 'UNKNOWN'}. Status przywrócony.",
                "details": orch_result,
            }

    # -------------------------------------------------------------------
    # PATH B — CLOUD: apollo_runner + email approwalowy
    # -------------------------------------------------------------------
    log.info("[apollo] Tryb CLOUD — apollo_runner + email approwalowy")

    # Step 1: Apollo runner
    try:
        from apollo_runner import run_auto
        result = await asyncio.to_thread(
            run_auto,
            article_url=body.article_url,
            email=body.email,
            full_name=body.full_name,
            company_name=body.company_name,
            job_title=body.job_title,
            tier=body.tier,
        )
        log.info("[apollo] apollo_runner ok=%s details=%s", result.get("ok"), result.get("details"))
    except Exception as exc:
        log.exception("[apollo] Błąd apollo_runner")
        _revert_to_waiting()
        return {
            "ok": False,
            "message": f"Błąd uruchomienia runnera: {exc}. Status przywrócony do Do wysłania.",
            "details": {},
        }

    ok = result.get("ok", False)

    if not ok:
        _revert_to_waiting()
        return {
            "ok": False,
            "message": result.get("message", "Nie udało się uruchomić kampanii Apollo. Status przywrócony."),
            "details": result.get("details", {}),
        }

    # Apollo runner succeeded
    try:
        update_apollo_status(body.article_url, "sent")
        log.info("[apollo] apollo_status → sent")
    except Exception:
        log.exception("[apollo] Nie udało się zapisać sent")

    _save_history("sent")

    # Step 2 + 3: Generate AI Steps 1-3 and send approval email — in background
    # (avoids Render 30s request timeout)
    _articles = load_press_articles()
    _art_info = next((a for a in _articles if a.get("source_url") == body.article_url), {})

    _approval_kwargs = dict(
        article_url=body.article_url,
        full_name=body.full_name,
        job_title=body.job_title,
        company_name=body.company_name or _art_info.get("company", ""),
        tier=body.tier,
        article_title=_art_info.get("title", "") or body.article_url,
        contact_id=result.get("contact_id", ""),
        sequence_id=result.get("sequence_id", ""),
        list_id=result.get("list_id", ""),
        list_added=result.get("list_added", False),
        sequence_added=result.get("details", {}).get("sequence_added", False),
        contact_email=body.email,
        campaign_name=APOLLO_CAMPAIGN,
    )

    def _bg_steps_and_email(**kwargs: object) -> None:
        _article_url = kwargs["article_url"]
        _full_name = kwargs["full_name"]
        _job_title = kwargs["job_title"]
        _company_name = kwargs["company_name"]
        _tier = kwargs["tier"]
        _article_title = kwargs["article_title"]

        # Generate AI steps
        steps: dict | None = None
        try:
            from cloud_message_generator import generate_steps
            log.info("[apollo/bg] Generuję AI Steps 1-3...")
            steps = generate_steps(
                article_url=_article_url,
                full_name=_full_name,
                job_title=_job_title,
                company_name=_company_name,
                tier=_tier,
                article_title=_article_title,
            )
            log.info("[apollo/bg] Steps generation: %s", "ok" if steps else "None/failed")
        except Exception as exc:
            log.warning("[apollo/bg] Steps generation failed: %s", exc)

        # Save AI Steps as Apollo contact custom fields (used by sequence templates)
        if steps and kwargs.get("contact_id"):
            try:
                from apollo_runner.client import update_contact_custom_fields
                _contact_id = str(kwargs["contact_id"])
                step1 = steps.get("email_1", {})
                step2 = steps.get("follow_up_1", {})
                step3 = steps.get("follow_up_2", {})
                SIGNATURE_HTML = (
                    '<p style="font-family: Aptos, Calibri, Arial, sans-serif; font-size: 11pt;'
                    ' color: #000000; line-height: 1.5; margin: 2px 0 2px 0;">Pozdrawiam,</p>'
                    '<p style="font-family: Aptos, Calibri, Arial, sans-serif; font-size: 11pt;'
                    ' color: #000000; line-height: 1.5; margin: 0 0 0 0;">'
                    '<strong>Tomasz Uściński</strong><br>'
                    'Senior Client Partner | Procurement<br>'
                    '+48&#8203; 787&#8203; 417&#8203; 293'
                    '</p>'
                    '<p style="font-family: Aptos, Calibri, Arial, sans-serif; font-size: 11pt;'
                    ' color: #000000; line-height: 1.5; margin: 8px 0 0 0;">'
                    '<strong>PROFITIA Management Consultants Mazurowski i Wspólnicy Spółka Jawna</strong><br>'
                    'Negotiation Intelligence w Zakupach | Doradztwo procurement'
                    ' | Certyfikowany Partner CIPS w Polsce | Szkolenia | Analityka zakupowa<br>'
                    '02-715 Warszawa, Villa Metro, ul. Puławska 145, V p.'
                    '</p>'
                    '<p style="font-family: Aptos, Calibri, Arial, sans-serif; font-size: 9pt;'
                    ' color: #949494; line-height: 1.4; margin: 8px 0 0 0;">'
                    'Uwaga: Ten e-mail jest poufny i przeznaczony tylko dla adresata (-ów) tej wiadomości.'
                    ' Jeżeli nie jesteś adresatem niniejszej wiadomości, usuń oryginał wiadomości'
                    ' wraz z wszelkimi wydrukami (kopiami) i załącznikami.'
                    '</p>'
                )
                custom_fields: dict[str, str] = {
                    "pl_market_news_signature_tu": SIGNATURE_HTML,
                }
                if step1.get("subject"):
                    custom_fields["sg_market_news_email_step_1_subject"] = step1["subject"]
                if step1.get("body"):
                    custom_fields["sg_market_news_email_step_1_body"] = step1["body"]
                if step2.get("subject"):
                    custom_fields["sg_market_news_email_step_2_subject"] = step2["subject"]
                if step2.get("body"):
                    custom_fields["sg_market_news_email_step_2_body"] = step2["body"]
                if step3.get("subject"):
                    custom_fields["sg_market_news_email_step_3_subject"] = step3["subject"]
                if step3.get("body"):
                    custom_fields["sg_market_news_email_step_3_body"] = step3["body"]
                ok = update_contact_custom_fields(_contact_id, custom_fields)
                log.info("[apollo/bg] Custom fields update: %s", "ok ✔" if ok else "FAILED ✘")
            except Exception as exc:
                log.warning("[apollo/bg] Custom fields update exception: %s", exc)

        # Send approval email
        try:
            from news.email_sender import send_approval_email
            email_sent = send_approval_email(
                article_title=_article_title,
                article_url=_article_url,
                company_name=_company_name,
                full_name=_full_name,
                email=kwargs["contact_email"],
                job_title=_job_title,
                tier=_tier,
                campaign_name=kwargs["campaign_name"],
                contact_id=kwargs["contact_id"],
                sequence_id=kwargs["sequence_id"],
                list_id=kwargs["list_id"],
                list_added=kwargs["list_added"],
                sequence_added=kwargs["sequence_added"],
                steps=steps,
            )
            log.info("[apollo/bg] Email approwalowy: %s", "wysłany ✔" if email_sent else "NIEUDANY ✘")
        except Exception as exc:
            log.warning("[apollo/bg] Email approwalowy exception: %s", exc)

    background_tasks.add_task(_bg_steps_and_email, **_approval_kwargs)
    log.info("[apollo] Steps+email scheduled as background task")

    msg = result.get("message", "Kampania Apollo uruchomiona ✔")
    return {
        "ok": True,
        "message": msg,
        "details": result.get("details", {}),
    }


@app.get("/api/campaign-history")
async def get_campaign_history(
    email: str = Query(..., min_length=1, description="Adres email kontaktu"),
) -> dict:
    """
    Zwraca historię kampanii Apollo dla podanego emaila (case-insensitive).

    Query param: email=22@a.pl
    Zwraca: { email, sent_count, last_sent_at, items: [...] }
    """
    email = email.strip()
    if not email or "@" not in email:
        raise HTTPException(status_code=422, detail="Nieprawidłowy format email")

    try:
        items = load_campaign_history_by_email(email)
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w GET /api/campaign-history: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w GET /api/campaign-history")
        raise HTTPException(status_code=500, detail="Błąd serwera")

    sent_count   = sum(it.get("run_count", 1) for it in items)
    last_sent_at = items[0]["campaign_run_at"] if items else None

    log.info("campaign-history: email=%s sent_count=%d rows=%d", email[:40], sent_count, len(items))
    return {
        "email":        email,
        "sent_count":   sent_count,
        "last_sent_at": last_sent_at,
        "items": [
            {
                "campaign_run_at": it["campaign_run_at"],
                "full_name":       it["full_name"],
                "company_name":    it["company_name"],
                "job_title":       it["job_title"],
                "article_title":   it["article_title"],
                "article_url":     it["article_url"],
                "source_name":     it["source_name"],
                "run_count":       it.get("run_count", 1),
            }
            for it in items
        ],
    }
