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

import logging
import os
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup — musi być przed importem lokalnych modułów
# ---------------------------------------------------------------------------
_src = Path(__file__).parent.parent
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator

from news.press_db import (
    load_press_articles,
    update_tier_email,
    update_apollo_status,
)

# ---------------------------------------------------------------------------
# Apollo pipeline configuration (lokalna ścieżka do workspacu Kampanie Apollo)
# ---------------------------------------------------------------------------
APOLLO_ROOT    = os.environ.get(
    "APOLLO_ROOT",
    "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo",
)
APOLLO_PYTHON  = os.environ.get(
    "APOLLO_PYTHON",
    "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/.venv/bin/python",
)
APOLLO_TIMEOUT = int(os.environ.get("APOLLO_TIMEOUT", "180"))

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
allowed_origins = [o.strip() for o in _raw_origins.split(",") if o.strip()] or ["*"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)


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


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
async def health() -> dict:
    """Health check."""
    return {"status": "ok"}


@app.get("/api/articles")
async def get_articles() -> list[dict]:
    """
    Zwraca wszystkie artykuły z apollo.press_articles.
    Format zgodny z data/articles.json, z dodatkowymi polami:
      tier1_email, tier2_email, apollo_status, updated_at.
    """
    try:
        return load_press_articles()
    except (EnvironmentError, ConnectionError) as exc:
        log.error("Błąd DB w GET /api/articles: %s", exc)
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception:
        log.exception("Nieoczekiwany błąd w GET /api/articles")
        raise HTTPException(status_code=500, detail="Błąd serwera")


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
async def run_apollo_auto(body: RunAutoRequest) -> dict:
    """
    Uruchamia auto pipeline orchestratora dla wybranego artykułu.
    Komenda: python src/news/orchestrator.py auto build-sequence --single-article-url <url> --verbose

    Uwaga: działa tylko lokalnie (wymaga dostępu do katalogu APOLLO_ROOT).
    Na Render/Railway nie zadziała bez przeniesienia projektu na serwer.
    """
    log.info(
        "POST /api/apollo/run-auto: %s (tier=%s, email=%s)",
        body.article_url[:80], body.tier, body.email,
    )

    # Niezwłocznie oznacz jako running
    try:
        update_apollo_status(body.article_url, "running")
        log.info("apollo_status → running dla %s", body.article_url[:60])
    except Exception:
        log.warning("Nie udało się ustawić running przed subprocess (ignorujem)")

    cmd = [
        APOLLO_PYTHON,
        "src/news/orchestrator.py",
        "auto",
        "build-sequence",
        "--single-article-url", body.article_url,
        "--verbose",
    ]

    def _revert_to_waiting() -> None:
        try:
            update_apollo_status(body.article_url, "waiting")
            log.info("apollo_status → waiting (revert) dla %s", body.article_url[:60])
        except Exception:
            log.exception("Nie udało się zrevertować apollo_status do waiting")

    try:
        proc = subprocess.run(
            cmd,
            cwd=APOLLO_ROOT,
            capture_output=True,
            text=True,
            timeout=APOLLO_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        log.error("Timeout subprocess dla: %s", body.article_url[:80])
        _revert_to_waiting()
        return {
            "ok": False,
            "returncode": -1,
            "stdout_tail": "",
            "stderr_tail": "",
            "message": f"Timeout — pipeline trwał ponad {APOLLO_TIMEOUT}s. Status przywrócony do Do wysłania.",
        }
    except FileNotFoundError as exc:
        log.error("Nie znaleziono interpretera lub orchestratora: %s", exc)
        _revert_to_waiting()
        return {
            "ok": False,
            "returncode": -1,
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "message": "Nie znaleziono interpretera Python lub orchestratora. Status przywrócony do Do wysłania.",
        }
    except Exception as exc:
        log.exception("Błąd subprocess w /api/apollo/run-auto")
        _revert_to_waiting()
        return {
            "ok": False,
            "returncode": -1,
            "stdout_tail": "",
            "stderr_tail": str(exc),
            "message": f"Błąd uruchomienia pipeline'u. Status przywrócony do Do wysłania.",
        }

    stdout_tail = "\n".join(proc.stdout.splitlines()[-30:]) if proc.stdout else ""
    stderr_tail = "\n".join(proc.stderr.splitlines()[-30:]) if proc.stderr else ""
    ok = proc.returncode == 0

    log.info(
        "auto build-sequence returncode=%d dla %s",
        proc.returncode, body.article_url[:60],
    )

    if ok:
        try:
            update_apollo_status(body.article_url, "sent")
            log.info("apollo_status → sent dla %s", body.article_url[:60])
        except Exception:
            log.exception("Nie udało się zaktualizować apollo_status po run-auto")
        return {
            "ok": True,
            "returncode": proc.returncode,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "message": "Kampania Apollo uruchomiona ✔",
        }
    else:
        _revert_to_waiting()
        return {
            "ok": False,
            "returncode": proc.returncode,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "message": "Nie udało się uruchomić kampanii Apollo. Status przywrócony do Do wysłania.",
        }
