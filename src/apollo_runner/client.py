"""
apollo_runner/client.py — minimalny klient Apollo.io API

Samodzielny, bez zewnętrznych lokalnych importów.
Wymaga: APOLLO_API_KEY w ENV.
"""
from __future__ import annotations

import logging
import os
import re
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


def normalize_sequence_id(raw: str) -> str:
    """
    Normalizuje wartość APOLLO_SEQUENCE_ID.
    Jeśli raw to pełny URL Apollo (np. https://app.apollo.io/#/sequences/<ID>),
    wyciąga samo ID. Jeśli to już samo ID, zwraca bez zmian.
    """
    raw = raw.strip()
    # Dopasuj /sequences/<id> lub /sequences/<id>/<cokolwiek>
    m = re.search(r'/sequences/([a-f0-9]{24})', raw, re.IGNORECASE)
    if m:
        extracted = m.group(1)
        log.info("normalize_sequence_id: wyciągnięto ID z URL: %s -> %s", raw, extracted)
        return extracted
    # Załóż że raw to już samo ID
    return raw


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
    Importuje kontakt do konta Apollo i zwraca jego ID.

    people/match używany tylko do wzbogacenia danych — zwraca ID z globalnej bazy
    Apollo (nie z konta użytkownika), więc nie może być używany do operacji
    list/sequence. Zawsze importujemy przez POST /contacts.
    Zwraca contact_id (str) lub None w przypadku błędu.
    """
    # Krok 1: people/match — tylko do wzbogacenia danych (imię/firma/stanowisko)
    enriched_name = full_name
    enriched_company = company_name
    enriched_title = job_title
    try:
        data = _post("people/match", {
            "email": email,
            "reveal_personal_emails": False,
            "reveal_phone_number": False,
        })
        person = data.get("person") or {}
        if not enriched_name and person.get("name"):
            enriched_name = person["name"]
        if not enriched_company and person.get("organization", {}).get("name"):
            enriched_company = person["organization"]["name"]
        if not enriched_title and person.get("title"):
            enriched_title = person["title"]
    except requests.HTTPError as exc:
        log.warning("people/match nie udał się dla %s: %s", email, exc)

    # Krok 2: zawsze importuj/utwórz kontakt w koncie przez POST /contacts
    # Jeśli kontakt już istnieje w koncie, Apollo zwróci jego ID z konta
    first_name, _, last_name = enriched_name.partition(" ") if enriched_name else ("", "", "")
    payload: dict[str, Any] = {
        "email": email,
        "first_name": first_name.strip() or None,
        "last_name": last_name.strip() or None,
        "organization_name": enriched_company or None,
        "title": enriched_title or None,
    }
    payload = {k: v for k, v in payload.items() if v is not None}

    try:
        data = _post("contacts", payload)
        contact = data.get("contact", {})
        contact_id = contact.get("id")
        if contact_id:
            log.info("Kontakt zaimportowany do konta Apollo: %s (id=%s)", email, contact_id)
            return contact_id
        log.warning("POST contacts nie zwróciło ID dla %s — próba wyszukania", email)
    except requests.HTTPError as exc:
        log.warning("POST contacts nie udał się dla %s: %s — próba wyszukania istniejącego", email, exc)

    # Krok 3: fallback — szukaj istniejącego kontaktu w koncie
    try:
        data = _post("contacts/search", {"q_keywords": email, "per_page": 1})
        contacts = data.get("contacts", [])
        if contacts:
            contact_id = contacts[0].get("id")
            log.info("Znaleziono kontakt w koncie Apollo (search): %s (id=%s)", email, contact_id)
            return contact_id
    except requests.HTTPError as exc:
        log.error("contacts/search nie udał się dla %s: %s", email, exc)

    log.error("Nie udało się zaimportować kontaktu do konta Apollo dla %s", email)
    return None


# ---------------------------------------------------------------------------
# Sender mailbox resolution
# ---------------------------------------------------------------------------

def resolve_sender_email_account_id() -> str:
    """
    Zwraca ID skrzynki nadawczej Apollo (send_email_from_email_account_id).

    Priorytety:
      1. APOLLO_SENDER_EMAIL_ACCOUNT_IDS (lista po przecinku) → bierze pierwszy ID.
         TODO: round-robin / wybór mailboxa per kampania (gdy wiele skrzynek).
      2. APOLLO_SENDER_EMAIL_ACCOUNT_ID (pojedynczy ID).

    Raises:
      EnvironmentError jeśli żadna zmienna nie jest ustawiona.
    """
    ids_raw = os.environ.get("APOLLO_SENDER_EMAIL_ACCOUNT_IDS", "").strip()
    if ids_raw:
        ids = [i.strip() for i in ids_raw.split(",") if i.strip()]
        if ids:
            if len(ids) > 1:
                log.info(
                    "resolve_sender: znaleziono %d mailboxów w APOLLO_SENDER_EMAIL_ACCOUNT_IDS, "
                    "używam pierwszego (TODO: round-robin): %s",
                    len(ids), ids[0],
                )
            return ids[0]

    single = os.environ.get("APOLLO_SENDER_EMAIL_ACCOUNT_ID", "").strip()
    if single:
        return single

    raise EnvironmentError(
        "Brak APOLLO_SENDER_EMAIL_ACCOUNT_ID — Apollo API wymaga wskazania skrzynki nadawczej. "
        "Ustaw APOLLO_SENDER_EMAIL_ACCOUNT_ID w Render Dashboard lub .env."
    )


# ---------------------------------------------------------------------------
# Sequence operations
# ---------------------------------------------------------------------------

class SequenceAddError(Exception):
    """Raised when add_contact_to_sequence fails; carries diagnostic details."""
    def __init__(self, message: str, status_code: int = 0, response_body: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


def add_contact_to_sequence(contact_id: str, sequence_id: str) -> tuple[bool, str]:
    """
    Dodaje kontakt do sekwencji (emailer_campaign) w Apollo.

    Payload zgodny z wymaganiami Apollo API:
      - emailer_campaign_id: wymagane (oprócz ID w URL)
      - send_email_from_email_account_id: wymagane — z resolve_sender_email_account_id()
      - sequence_active_in_other_campaigns: True  (bypass jeśli aktywny gdzie indziej)
      - sequence_finished_in_other_campaigns: True (bypass jeśli skończony gdzie indziej)

    Returns:
      (True, "") jeśli sukces
      (False, diagnostic_message) jeśli błąd
    """
    try:
        sender_id = resolve_sender_email_account_id()
    except EnvironmentError as exc:
        msg = str(exc)
        log.error("[SEQUENCE ADD] %s", msg)
        return False, msg

    endpoint = f"emailer_campaigns/{sequence_id}/add_contact_ids"
    url = f"{APOLLO_BASE_URL}/{endpoint}"
    payload = {
        "contact_ids": [contact_id],
        "emailer_campaign_id": sequence_id,
        "send_email_from_email_account_id": sender_id,
        "sequence_active_in_other_campaigns": True,
        "sequence_finished_in_other_campaigns": True,
    }

    log.info(
        "[SEQUENCE ADD] endpoint=%s | sequence_id=%s | contact_id=%s | sender_email_account_id=%s",
        url, sequence_id, contact_id, sender_id,
    )

    try:
        resp = requests.post(url, json=payload, headers=_headers(), timeout=30)
        status_code = resp.status_code
        try:
            resp_body = resp.json()
            resp_body_str = str(resp_body)[:600]
        except Exception:
            resp_body = None
            resp_body_str = resp.text[:600]

        log.info(
            "[SEQUENCE ADD] HTTP %d | sequence_id=%s contact_id=%s | response: %s",
            status_code, sequence_id, contact_id, resp_body_str,
        )

        if not resp.ok:
            diag = f"HTTP {status_code} | {resp_body_str}"
            log.error(
                "[SEQUENCE ADD] FAILED: contact_id=%s sequence_id=%s | %s",
                contact_id, sequence_id, diag,
            )
            return False, diag

        # Sprawdź czy kontakt nie został pominięty (HTTP 200 ale skipped)
        if resp_body and isinstance(resp_body, dict):
            skipped = resp_body.get("skipped_contact_ids", {})
            if contact_id in skipped:
                reason = skipped[contact_id]
                diag = f"contact skipped by Apollo: {reason}"
                log.error(
                    "[SEQUENCE ADD] SKIPPED: contact_id=%s sequence_id=%s | reason=%s",
                    contact_id, sequence_id, reason,
                )
                return False, diag

        log.info(
            "[SEQUENCE ADD] OK: contact_id=%s sequence_id=%s",
            contact_id, sequence_id,
        )
        return True, ""

    except requests.RequestException as exc:
        diag = f"Request error: {exc}"
        log.error(
            "[SEQUENCE ADD] exception: contact_id=%s sequence_id=%s | %s",
            contact_id, sequence_id, diag,
        )
        return False, diag


def list_sequences() -> list[dict]:
    """Zwraca listę dostępnych sekwencji (do debugowania)."""
    data = _get("emailer_campaigns", {"per_page": 50})
    campaigns = data.get("emailer_campaigns", [])
    return [{"id": c["id"], "name": c.get("name", "")} for c in campaigns]


# ---------------------------------------------------------------------------
# List (label) operations
# ---------------------------------------------------------------------------

def add_contact_to_list(contact_id: str, list_id: str) -> tuple[bool, str]:
    """
    Dodaje kontakt do listy Apollo (label).

    Próbuje dwóch endpointów:
    1. POST /api/v1/labels/{list_id}/add_contact_ids  (może zwrócić 404)
    2. Fallback: PATCH /api/v1/contacts/{contact_id} z label_ids=[list_id]

    Returns:
        (True, "") jeśli sukces
        (False, diagnostic_message) jeśli błąd
    """
    # Primary: POST add_contact_ids
    url_primary = f"{APOLLO_BASE_URL}/labels/{list_id}/add_contact_ids"
    log.info("[LIST ADD] Próba primary: url=%s | contact_id=%s", url_primary, contact_id)
    try:
        resp = requests.post(url_primary, json={"contact_ids": [contact_id]}, headers=_headers(), timeout=30)
        try:
            resp_body_str = str(resp.json())[:400]
        except Exception:
            resp_body_str = resp.text[:400]
        log.info("[LIST ADD] Primary HTTP %d | %s", resp.status_code, resp_body_str)
        if resp.ok:
            log.info("[LIST ADD] Primary OK: contact_id=%s list_id=%s", contact_id, list_id)
            return True, ""
        log.warning("[LIST ADD] Primary failed (HTTP %d) — próba fallback PATCH", resp.status_code)
    except requests.RequestException as exc:
        log.warning("[LIST ADD] Primary request error: %s — próba fallback PATCH", exc)

    # Fallback: PATCH /api/v1/contacts/{contact_id} with label_ids
    url_fallback = f"{APOLLO_BASE_URL}/contacts/{contact_id}"
    log.info("[LIST ADD] Fallback PATCH: url=%s | label_ids=[%s]", url_fallback, list_id)
    try:
        resp = requests.patch(url_fallback, json={"label_ids": [list_id]}, headers=_headers(), timeout=30)
        try:
            resp_body_str = str(resp.json())[:400]
        except Exception:
            resp_body_str = resp.text[:400]
        log.info("[LIST ADD] Fallback HTTP %d | %s", resp.status_code, resp_body_str)
        if resp.ok:
            log.info("[LIST ADD] Fallback OK: contact_id=%s list_id=%s", contact_id, list_id)
            return True, ""
        diag = f"Primary+Fallback failed | HTTP {resp.status_code} | {resp_body_str}"
        log.error("[LIST ADD] FAILED: contact_id=%s list_id=%s | %s", contact_id, list_id, diag)
        return False, diag
    except requests.RequestException as exc:
        diag = f"Fallback request error: {exc}"
        log.error("[LIST ADD] FAILED: contact_id=%s list_id=%s | %s", contact_id, list_id, diag)
        return False, diag
