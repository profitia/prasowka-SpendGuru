"""
apollo_runner/client.py — minimalny klient Apollo.io API

Samodzielny, bez zewnętrznych lokalnych importów.
Wymaga: APOLLO_API_KEY w ENV.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import requests

log = logging.getLogger("apollo_runner.client")

APOLLO_BASE_URL = os.environ.get("APOLLO_BASE_URL", "https://api.apollo.io/api/v1")


def _get_api_key() -> str:
    key = os.environ.get("APOLLO_API_KEY", "").strip()
    if not key:
        raise EnvironmentError(
            "Brak APOLLO_API_KEY w zmiennych środowiskowych. "
            "Ustaw APOLLO_API_KEY w Render Dashboard lub .env."
        )
    return key


def _headers() -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": _get_api_key(),
    }


def _post(endpoint: str, payload: dict | None = None) -> dict:
    url = f"{APOLLO_BASE_URL}/{endpoint.lstrip('/')}"
    resp = requests.post(url, json=payload or {}, headers=_headers(), timeout=30)
    if not resp.ok:
        try:
            err_body = resp.json()
        except Exception:
            err_body = resp.text[:500]
        raise requests.HTTPError(
            f"Apollo API {resp.status_code} dla {url} — {err_body}",
            response=resp,
        )
    return resp.json()


def _get(endpoint: str, params: dict | None = None) -> dict:
    url = f"{APOLLO_BASE_URL}/{endpoint.lstrip('/')}"
    resp = requests.get(url, params=params or {}, headers=_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


# ---------------------------------------------------------------------------
# Contact operations
# ---------------------------------------------------------------------------

def find_or_create_contact(
    email: str,
    full_name: str = "",
    company_name: str = "",
    job_title: str = "",
) -> str | None:
    """
    Szuka kontaktu w Apollo po emailu. Jeśli nie istnieje, tworzy nowy.
    Zwraca contact_id (str) lub None w przypadku błędu.
    """
    # Spróbuj people/match (email reveal / match)
    try:
        data = _post("people/match", {
            "email": email,
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        })
        person = data.get("person")
        if person:
            contact_id = person.get("id")
            if contact_id:
                log.info("Znaleziono istniejący kontakt w Apollo: %s (id=%s)", email, contact_id)
                return contact_id
    except requests.HTTPError as exc:
        log.warning("people/match nie udał się dla %s: %s", email, exc)

    # Brak match — utwórz kontakt
    first_name, _, last_name = full_name.partition(" ") if full_name else ("", "", "")
    payload: dict[str, Any] = {
        "email": email,
        "first_name": first_name.strip() or None,
        "last_name": last_name.strip() or None,
        "organization_name": company_name or None,
        "title": job_title or None,
    }
    # Usuń puste wartości
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        data = _post("contacts", payload)
        contact = data.get("contact", {})
        contact_id = contact.get("id")
        log.info("Utworzono nowy kontakt w Apollo: %s (id=%s)", email, contact_id)
        return contact_id
    except requests.HTTPError as exc:
        log.error("Nie udało się utworzyć kontaktu w Apollo dla %s: %s", email, exc)
        return None


# ---------------------------------------------------------------------------
# Sequence operations
# ---------------------------------------------------------------------------

def add_contact_to_sequence(contact_id: str, sequence_id: str) -> bool:
    """
    Dodaje kontakt do sekwencji (emailer_campaign) w Apollo.
    Zwraca True jeśli sukces.
    """
    try:
        data = _post(
            f"emailer_campaigns/{sequence_id}/add_contact_ids",
            {"contact_ids": [contact_id], "send_email_from_email_account_id": None},
        )
        log.info(
            "Dodano kontakt %s do sekwencji %s: %s",
            contact_id, sequence_id, data,
        )
        return True
    except requests.HTTPError as exc:
        log.error(
            "Nie udało się dodać kontaktu %s do sekwencji %s: %s",
            contact_id, sequence_id, exc,
        )
        return False


def list_sequences() -> list[dict]:
    """Zwraca listę dostępnych sekwencji (do debugowania)."""
    data = _get("emailer_campaigns", {"per_page": 50})
    campaigns = data.get("emailer_campaigns", [])
    return [{"id": c["id"], "name": c.get("name", "")} for c in campaigns]
