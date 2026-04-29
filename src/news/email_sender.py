"""
Email sender — reuses the Office365 / Graph API setup from the parent workspace.

Reads credentials from:
    <workspace_root>/Integracja z Office365/.env
    <workspace_root>/Integracja z Office365/.token_cache.json

For cloud deployments (Render) where the .env and token cache file are not
available, set the following environment variables:
    AZURE_CLIENT_ID       — Azure app client ID
    AZURE_TENANT_ID       — Azure tenant ID
    MAIL_FROM             — sender email address
    MSAL_TOKEN_CACHE_B64  — base64-encoded content of .token_cache.json
    NOTIFICATION_EMAIL    — approval notification recipient (default: tomasz.uscinski@profitia.pl)

To generate MSAL_TOKEN_CACHE_B64 from a local token cache:
    python -c "import base64; print(base64.b64encode(open('Integracja z Office365/.token_cache.json','rb').read()).decode())"
"""
from __future__ import annotations

import base64
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

# Env vars — can be set on Render instead of using local .env file
CLIENT_ID  = os.getenv("AZURE_CLIENT_ID", "")
TENANT_ID  = os.getenv("AZURE_TENANT_ID", "")
MAIL_FROM  = os.getenv("MAIL_FROM", "")
SCOPES     = os.getenv("MAIL_SCOPES", "Mail.Send,User.Read").split(",")

# Cloud deployment: base64-encoded serialized MSAL token cache
# Generate with: base64.b64encode(open('.token_cache.json','rb').read()).decode()
MSAL_TOKEN_CACHE_B64 = os.getenv("MSAL_TOKEN_CACHE_B64", "")

# Recipient of approval / notification emails
NOTIFICATION_EMAIL = os.getenv("NOTIFICATION_EMAIL", "tomasz.uscinski@profitia.pl")

_AUTHORITY      = f"https://login.microsoftonline.com/{TENANT_ID}"
_GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


# ---------------------------------------------------------------------------
# Token acquisition
# ---------------------------------------------------------------------------

def _get_token() -> str:
    cache = msal.SerializableTokenCache()

    # Priority 1: env var (for cloud deployments on Render)
    if MSAL_TOKEN_CACHE_B64:
        try:
            cache_json = base64.b64decode(MSAL_TOKEN_CACHE_B64).decode("utf-8")
            cache.deserialize(cache_json)
            log.info("[email_sender] MSAL token cache loaded from MSAL_TOKEN_CACHE_B64 env var")
        except Exception as exc:
            log.warning("[email_sender] Failed to load MSAL cache from env var: %s", exc)
    # Priority 2: local file
    elif os.path.exists(_TOKEN_CACHE_PATH):
        with open(_TOKEN_CACHE_PATH, encoding="utf-8") as f:
            cache.deserialize(f.read())
        log.debug("[email_sender] MSAL token cache loaded from %s", _TOKEN_CACHE_PATH)

    app = msal.PublicClientApplication(CLIENT_ID, authority=_AUTHORITY, token_cache=cache)

    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    log.info("[email_sender] Token wygasł lub brak w cache — inicjuję device flow...")
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
        # Only save to file when running locally (file path exists or is writable)
        if os.path.exists(_OFFICE365_DIR):
            try:
                with open(_TOKEN_CACHE_PATH, "w", encoding="utf-8") as f:
                    f.write(cache.serialize())
            except OSError:
                pass  # Read-only filesystem (cloud) — skip silently


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


def send_approval_email(
    article_title: str,
    article_url: str,
    company_name: str,
    full_name: str,
    email: str,
    job_title: str,
    tier: str,
    campaign_name: str = "spendguru_market_news",
) -> bool:
    """
    Wysyła mail powiadomienie approvalowe do NOTIFICATION_EMAIL.

    Subject: "Kampania {campaign_name} czeka na zatwierdzenie"
    """
    to_email = NOTIFICATION_EMAIL
    if not to_email:
        log.warning("[approval_email] NOTIFICATION_EMAIL not set — skipping")
        return False

    subject = f"Kampania {campaign_name} czeka na zatwierdzenie"
    tier_label = "Tier 1 (C-Level)" if "tier_1" in tier else "Tier 2 (Procurement/Management)"

    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#333;max-width:620px;margin:0 auto;padding:20px;">
  <div style="background:#f0f4f8;border-left:4px solid #0078d4;padding:16px 20px;margin-bottom:24px;border-radius:2px;">
    <h2 style="margin:0 0 6px 0;color:#0078d4;font-size:18px;">Kampania czeka na zatwierdzenie</h2>
    <p style="margin:0;color:#666;font-size:12px;">SpendGuru Market News &mdash; {campaign_name}</p>
  </div>

  <table style="width:100%;border-collapse:collapse;margin-bottom:24px;font-size:14px;">
    <tr>
      <td style="padding:10px 12px;background:#f8f9fa;font-weight:bold;color:#555;width:32%;border-bottom:1px solid #dee2e6;">Artyku&#322;</td>
      <td style="padding:10px 12px;border-bottom:1px solid #dee2e6;">
        <a href="{article_url}" style="color:#0078d4;text-decoration:none;">{article_title or article_url}</a>
      </td>
    </tr>
    <tr>
      <td style="padding:10px 12px;background:#f8f9fa;font-weight:bold;color:#555;border-bottom:1px solid #dee2e6;">Firma</td>
      <td style="padding:10px 12px;border-bottom:1px solid #dee2e6;">{company_name}</td>
    </tr>
    <tr>
      <td style="padding:10px 12px;background:#f8f9fa;font-weight:bold;color:#555;border-bottom:1px solid #dee2e6;">Kontakt</td>
      <td style="padding:10px 12px;border-bottom:1px solid #dee2e6;">{full_name} &lt;{email}&gt;</td>
    </tr>
    <tr>
      <td style="padding:10px 12px;background:#f8f9fa;font-weight:bold;color:#555;border-bottom:1px solid #dee2e6;">Stanowisko</td>
      <td style="padding:10px 12px;border-bottom:1px solid #dee2e6;">{job_title or "&mdash;"}</td>
    </tr>
    <tr>
      <td style="padding:10px 12px;background:#f8f9fa;font-weight:bold;color:#555;">Tier</td>
      <td style="padding:10px 12px;">{tier_label}</td>
    </tr>
  </table>

  <p style="color:#888;font-size:12px;margin-top:20px;padding-top:14px;border-top:1px solid #dee2e6;">
    Wygenerowano automatycznie przez Pras&#243;wk&#281; SpendGuru (cloud runner).
  </p>
</body>
</html>"""

    try:
        result = send_email(to_email, subject, body_html)
        if result:
            log.info("[approval_email] Wysłano do %s (%s — %s)", to_email, company_name, full_name)
        else:
            log.warning("[approval_email] send_email zwrócił False")
        return result
    except Exception as exc:
        log.warning("[approval_email] Błąd wysyłki: %s", exc)
        return False
