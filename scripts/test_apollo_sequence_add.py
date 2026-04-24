#!/usr/bin/env python3
"""
scripts/test_apollo_sequence_add.py — ręczny test dodawania kontaktu do sekwencji Apollo.

Użycie:
  cd "Prasówki SpendGuru"
  source "../.venv/bin/activate"
  export APOLLO_API_KEY="..."
  export APOLLO_SEQUENCE_ID="<ID lub pełny URL>"
  export TEST_CONTACT_ID="<Apollo contact_id>"   # opcjonalny — jeśli pominięty, pyta o email
  python scripts/test_apollo_sequence_add.py

Wypisuje pełną odpowiedź Apollo na każdym kroku.
"""
from __future__ import annotations

import json
import os
import re
import sys

import requests

# ---------------------------------------------------------------------------
# Konfiguracja
# ---------------------------------------------------------------------------
API_KEY        = os.environ.get("APOLLO_API_KEY", "").strip()
SEQUENCE_ID_RAW = os.environ.get("APOLLO_SEQUENCE_ID", "").strip()
CONTACT_ID     = os.environ.get("TEST_CONTACT_ID", "").strip()
BASE_URL       = os.environ.get("APOLLO_BASE_URL", "https://api.apollo.io/api/v1")

HEADERS = {
    "Content-Type": "application/json",
    "Cache-Control": "no-cache",
}
if API_KEY:
    HEADERS["X-Api-Key"] = API_KEY


def _pp(data: object) -> str:
    """Pretty-print JSON or string."""
    if isinstance(data, (dict, list)):
        return json.dumps(data, ensure_ascii=False, indent=2)
    return str(data)


def normalize_sequence_id(raw: str) -> str:
    """Wyciąga samo ID z pełnego URL Apollo, jeśli potrzeba."""
    m = re.search(r'/sequences/([a-f0-9]{24})', raw, re.IGNORECASE)
    if m:
        extracted = m.group(1)
        print(f"  [NORMALIZE] URL → ID: {raw!r} → {extracted!r}")
        return extracted
    return raw


def guard_env() -> bool:
    ok = True
    if not API_KEY:
        print("ERROR: APOLLO_API_KEY nie jest ustawiony")
        ok = False
    if not SEQUENCE_ID_RAW:
        print("ERROR: APOLLO_SEQUENCE_ID nie jest ustawiony")
        ok = False
    return ok


def step_find_contact_by_email(email: str) -> str | None:
    """Próbuje znaleźć contact_id po emailu przez people/match."""
    url = f"{BASE_URL}/people/match"
    payload = {
        "email": email,
        "reveal_personal_emails": False,
        "reveal_phone_number": False,
    }
    print(f"\n--- KROK: people/match ---")
    print(f"  URL: {url}")
    print(f"  Payload: {_pp(payload)}")

    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    print(f"  HTTP: {resp.status_code}")
    try:
        body = resp.json()
        print(f"  Response: {_pp(body)}")
        person = body.get("person")
        if person:
            cid = person.get("id")
            print(f"  → contact_id znaleziony: {cid}")
            return cid
        print("  → brak kontaktu (person=null)")
    except Exception as exc:
        print(f"  Response (raw): {resp.text[:500]}")
        print(f"  JSON parse error: {exc}")
    return None


def step_add_to_sequence(contact_id: str, sequence_id: str) -> None:
    """Próbuje dodać contact_id do sequence_id i wypisuje pełną odpowiedź."""
    url = f"{BASE_URL}/emailer_campaigns/{sequence_id}/add_contact_ids"
    payload = {
        "contact_ids": [contact_id],
        "emailer_campaign_id": sequence_id,
        "sequence_active_in_other_campaigns": True,
        "sequence_finished_in_other_campaigns": True,
    }

    print(f"\n--- KROK: add_contact_to_sequence ---")
    print(f"  URL:          {url}")
    print(f"  contact_id:   {contact_id}")
    print(f"  sequence_id:  {sequence_id}")
    print(f"  Payload:      {_pp(payload)}")

    resp = requests.post(url, json=payload, headers=HEADERS, timeout=30)
    print(f"  HTTP status:  {resp.status_code}")

    try:
        body = resp.json()
        print(f"  Response body:\n{_pp(body)}")
    except Exception:
        print(f"  Response (raw): {resp.text[:800]}")

    if resp.ok:
        print("\n  ✔ SUKCES — kontakt dodany do sekwencji")
    else:
        print(f"\n  ✗ BŁĄD — HTTP {resp.status_code}")
        print("  Sprawdź czy:")
        print("    - APOLLO_SEQUENCE_ID jest poprawne (znajdziesz w URL sekwencji w Apollo)")
        print("    - APOLLO_API_KEY ma uprawnienia do sekwencji")
        print("    - Kontakt istnieje w Apollo (contact_id jest CRM ID, nie prospecting ID)")
        print("    - Sekwencja jest aktywna (lub użyj bypass_active=True)")


def main() -> None:
    print("=" * 60)
    print("Apollo Sequence Add — test diagnostyczny")
    print("=" * 60)

    if not guard_env():
        sys.exit(1)

    sequence_id = normalize_sequence_id(SEQUENCE_ID_RAW)
    print(f"\nAPOLLO_SEQUENCE_ID (raw):       {SEQUENCE_ID_RAW!r}")
    print(f"APOLLO_SEQUENCE_ID (normalized): {sequence_id!r}")
    print(f"BASE_URL:                        {BASE_URL}")

    contact_id = CONTACT_ID

    if not contact_id:
        email = input("\nWpisz email kontaktu (lub wklej contact_id po '--'): ").strip()
        if email.startswith("--"):
            contact_id = email[2:].strip()
            print(f"Użycie podanego contact_id: {contact_id}")
        else:
            contact_id = step_find_contact_by_email(email)
            if not contact_id:
                print("\nNie znaleziono kontaktu. Podaj TEST_CONTACT_ID ręcznie lub utwórz kontakt w Apollo.")
                sys.exit(1)
    else:
        print(f"\nTEST_CONTACT_ID: {contact_id}")

    step_add_to_sequence(contact_id, sequence_id)

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
