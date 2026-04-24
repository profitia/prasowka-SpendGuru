"""
apollo_runner/runner.py — główna funkcja run_auto()

Przepływ:
  1. Sprawdź czy APOLLO_API_KEY jest dostępny
  2. Znajdź lub utwórz kontakt w Apollo po emailu
  3. Dodaj kontakt do sekwencji (APOLLO_SEQUENCE_ID z ENV)
  4. Zwróć słownik wynikowy { ok, contact_id, sequence_id, message }

Nie zależy od żadnych lokalnych ścieżek macOS.
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("apollo_runner.runner")


def run_auto(
    article_url: str,
    email: str,
    full_name: str = "",
    company_name: str = "",
    job_title: str = "",
    tier: str = "",
    **_extra: object,
) -> dict:
    """
    Samodzielny Apollo runner — nie wymaga lokalnego katalogu Kampanie Apollo.

    Args:
        article_url:  URL artykułu (dla logowania / traceability)
        email:        Email kontaktu (wymagany)
        full_name:    Pełne imię i nazwisko
        company_name: Nazwa firmy
        job_title:    Stanowisko
        tier:         Segment (tier_1_c_level | tier_2_procurement_management)

    Returns:
        dict z kluczami: ok (bool), contact_id, sequence_id, message, details

    ENV vars:
        APOLLO_API_KEY      — wymagany
        APOLLO_SEQUENCE_ID  — opcjonalny; jeśli brak, runner symuluje sukces z info
    """
    if not os.environ.get("APOLLO_API_KEY", "").strip():
        return {
            "ok": False,
            "contact_id": None,
            "sequence_id": None,
            "message": (
                "Brak APOLLO_API_KEY w zmiennych środowiskowych. "
                "Ustaw APOLLO_API_KEY w Render Dashboard."
            ),
            "details": {},
        }

    sequence_id = os.environ.get("APOLLO_SEQUENCE_ID", "").strip()

    # Import tutaj (nie przy starcie modułu) — obsługuje ENV brak klucza
    from .client import find_or_create_contact, add_contact_to_sequence

    log.info(
        "run_auto start: email=%s company=%s tier=%s article=%s",
        email, company_name or "—", tier or "—", article_url[:80],
    )

    # --- Krok 1: Znajdź / utwórz kontakt ---
    contact_id = find_or_create_contact(
        email=email,
        full_name=full_name,
        company_name=company_name,
        job_title=job_title,
    )

    if not contact_id:
        return {
            "ok": False,
            "contact_id": None,
            "sequence_id": sequence_id or None,
            "message": (
                f"Nie udało się znaleźć ani utworzyć kontaktu w Apollo dla {email}. "
                "Sprawdź APOLLO_API_KEY i logi."
            ),
            "details": {},
        }

    # --- Krok 2: Dodaj do sekwencji (jeśli skonfigurowana) ---
    if not sequence_id:
        log.warning(
            "APOLLO_SEQUENCE_ID nie ustawiony — kontakt %s (id=%s) zaimportowany do Apollo, "
            "ale NIE dodany do sekwencji. Ustaw APOLLO_SEQUENCE_ID w ENV.",
            email, contact_id,
        )
        return {
            "ok": True,
            "contact_id": contact_id,
            "sequence_id": None,
            "message": (
                f"Kontakt zaimportowany do Apollo (id: {contact_id}), "
                "ale APOLLO_SEQUENCE_ID nie jest ustawiony — sekwencja nie uruchomiona. "
                "Ustaw APOLLO_SEQUENCE_ID w Render Dashboard."
            ),
            "details": {"email": email, "sequence_added": False},
        }

    added = add_contact_to_sequence(contact_id, sequence_id)

    if added:
        log.info(
            "run_auto sukces: email=%s contact_id=%s sequence_id=%s",
            email, contact_id, sequence_id,
        )
        return {
            "ok": True,
            "contact_id": contact_id,
            "sequence_id": sequence_id,
            "message": f"Kontakt dodany do sekwencji Apollo (contact_id: {contact_id})",
            "details": {
                "email": email,
                "sequence_added": True,
                "sequence_id": sequence_id,
            },
        }
    else:
        return {
            "ok": False,
            "contact_id": contact_id,
            "sequence_id": sequence_id,
            "message": (
                f"Kontakt zaimportowany (id: {contact_id}), "
                f"ale nie udało się dodać do sekwencji {sequence_id}. Sprawdź logi."
            ),
            "details": {"email": email, "sequence_added": False},
        }
