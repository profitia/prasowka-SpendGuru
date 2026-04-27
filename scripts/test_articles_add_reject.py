#!/usr/bin/env python3
"""
scripts/test_articles_add_reject.py
====================================
Testy pytest dla nowych endpointów API:
  POST /api/articles/add
  POST /api/articles/reject

Uruchomienie:
  cd "Prasówki SpendGuru"
  pip install pytest httpx
  pytest scripts/test_articles_add_reject.py -v

Testy nie wymagają połączenia z bazą danych (mockują press_db i scraper).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Dodaj src do path
_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def client():
    """TestClient z zaaplikowanymi mockami DB i scrapera."""
    with (
        patch("news.press_db.get_connection"),
        patch("news.press_db.ensure_campaign_history_table"),
    ):
        from site_api.app import app
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c


# ---------------------------------------------------------------------------
# POST /api/articles/add — nowy artykuł
# ---------------------------------------------------------------------------

def test_add_article_new(client):
    fake_article = {
        "id": "manual_abc123",
        "source_url": "https://example.com/nowy-artykul",
        "title": "Nowy artykuł testowy",
        "article_date": "2026-04-27",
        "source_name": "example.com",
        "company": "", "industry": "", "press_type": "",
        "tier1_person": "", "tier1_position": "", "tier1_email": "",
        "tier2_person": "", "tier2_position": "", "tier2_email": "",
        "reason": "Dodano ręcznie", "context": "",
        "apollo_status": "waiting", "data_quality_status": "ok",
        "data_quality_notes": "", "created_at": "", "updated_at": "",
    }

    with (
        patch("news.press_db.article_exists", return_value=False),
        patch("news.scraper.fetch_article", return_value={
            "url": "https://example.com/nowy-artykul",
            "title": "Nowy artykuł testowy",
            "text": "Treść artykułu",
            "source_name": "example.com",
        }),
        patch("news.press_db.upsert_press_article"),
        patch("news.press_db.get_press_article_by_url", return_value=fake_article),
    ):
        resp = client.post("/api/articles/add", json={"url": "https://example.com/nowy-artykul"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
    assert data["article"]["source_url"] == "https://example.com/nowy-artykul"
    assert data["article"]["reason"] == "Dodano ręcznie"


def test_add_article_duplicate(client):
    existing = {
        "id": "abc", "source_url": "https://example.com/istniejacy",
        "title": "Istniejący artykuł", "article_date": "2026-01-01",
        "source_name": "example.com", "company": "",
        "apollo_status": "waiting", "data_quality_status": "ok",
    }

    with (
        patch("news.press_db.article_exists", return_value=True),
        patch("news.press_db.get_press_article_by_url", return_value=existing),
    ):
        resp = client.post("/api/articles/add", json={"url": "https://example.com/istniejacy"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "duplicate"
    assert data["article"]["source_url"] == "https://example.com/istniejacy"


def test_add_article_invalid_url(client):
    resp = client.post("/api/articles/add", json={"url": "nie-to-url"})
    assert resp.status_code == 422


def test_add_article_empty_url(client):
    resp = client.post("/api/articles/add", json={"url": ""})
    assert resp.status_code == 422


def test_add_article_ftp_scheme(client):
    resp = client.post("/api/articles/add", json={"url": "ftp://example.com/plik"})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/articles/reject
# ---------------------------------------------------------------------------

def test_reject_article_ok(client):
    with patch("news.press_db.reject_press_article", return_value=True):
        resp = client.post(
            "/api/articles/reject",
            json={"article_url": "https://example.com/artykul"},
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_reject_article_not_found(client):
    with patch("news.press_db.reject_press_article", return_value=False):
        resp = client.post(
            "/api/articles/reject",
            json={"article_url": "https://example.com/nieistniejacy"},
        )
    assert resp.status_code == 404


def test_reject_article_empty_url(client):
    resp = client.post("/api/articles/reject", json={"article_url": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# POST /api/articles/add — scraping failure (artykuł nadal zapisywany)
# ---------------------------------------------------------------------------

def test_add_article_scraping_fails(client):
    """Błąd scrapera nie blokuje dodania artykułu."""
    fake_article = {
        "id": "manual_xyz",
        "source_url": "https://example.com/artykul-bez-tytulu",
        "title": "",
        "article_date": "2026-04-27",
        "source_name": "example.com",
        "company": "", "industry": "", "press_type": "",
        "tier1_person": "", "tier1_position": "", "tier1_email": "",
        "tier2_person": "", "tier2_position": "", "tier2_email": "",
        "reason": "Dodano ręcznie", "context": "",
        "apollo_status": "waiting", "data_quality_status": "ok",
        "data_quality_notes": "", "created_at": "", "updated_at": "",
    }

    with (
        patch("news.press_db.article_exists", return_value=False),
        patch("news.scraper.fetch_article", side_effect=RuntimeError("timeout")),
        patch("news.press_db.upsert_press_article"),
        patch("news.press_db.get_press_article_by_url", return_value=fake_article),
    ):
        resp = client.post(
            "/api/articles/add",
            json={"url": "https://example.com/artykul-bez-tytulu"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "created"
