"""
Email sender — reuses the Office365 / Graph API setup from the parent workspace.

Reads credentials from:
    <workspace_root>/Integracja z Office365/.env
    <workspace_root>/Integracja z Office365/.token_cache.json
"""
from __future__ import annotations

import json
import logging
import os

import msal
import requests
from dotenv import load_dotenv

log = logging.getLogger("news.email_sender")

# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
# src/news/ → src/ → Prasówki SpendGuru/ → Kampanie Apollo/
_WORKSPACE_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_THIS_DIR)))
_OFFICE365_DIR = os.path.join(_WORKSPACE_ROOT, "Integracja z Office365")

load_dotenv(os.path.join(_OFFICE365_DIR, ".env"))

_TOKEN_CACHE_PATH = os.path.join(_OFFICE365_DIR, ".token_cache.json")

CLIENT_ID = os.getenv("AZURE_CLIENT_ID", "")
TENANT_ID = os.getenv("AZURE_TENANT_ID", "")
MAIL_FROM = os.getenv("MAIL_FROM", "")
SCOPES = os.getenv("MAIL_SCOPES", "Mail.Send,User.Read").split(",")

_AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"
_GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------

def _get_token() -> str:
    cache = msal.SerializableTokenCache()
    if os.path.exists(_TOKEN_CACHE_PATH):
        with open(_TOKEN_CACHE_PATH, encoding="utf-8") as f:
            cache.deserialize(f.read())

    app = msal.PublicClientApplication(CLIENT_ID, authority=_AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    log.info("Token wygasł lub brak w cache — inicjuję device flow...")
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Device flow error: {json.dumps(flow)}")

    print(flow["message"])  # prints the URL + user code for interactive login
    result = app.acquire_token_by_device_flow(flow)

    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {json.dumps(result)}")

    _save_cache(cache)
    return result["access_token"]


def _save_cache(cache: msal.SerializableTokenCache) -> None:
    if cache.has_state_changed:
        with open(_TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
            f.write(cache.serialize())


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_email(to: str, subject: str, body_html: str) -> bool:
    """
    Send an HTML email via Office365 Graph API.
    Returns True on success, False on failure.
    """
    if not CLIENT_ID or not TENANT_ID:
        raise RuntimeError(
            "Brak AZURE_CLIENT_ID / AZURE_TENANT_ID. "
            f"Uzupełnij: {os.path.join(_OFFICE365_DIR, '.env')}"
        )

    token = _get_token()

    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body_html},
            "toRecipients": [{"emailAddress": {"address": to}}],
            "from": {"emailAddress": {"address": MAIL_FROM}},
        },
        "saveToSentItems": True,
    }

    resp = requests.post(
        f"{_GRAPH_ENDPOINT}/me/sendMail",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=payload,
        timeout=30,
    )

    if resp.status_code == 202:
        log.info("Mail wysłany do: %s", to)
        return True
    else:
        log.error("Błąd wysyłki do %s: %s %s", to, resp.status_code, resp.text[:300])
        return False
