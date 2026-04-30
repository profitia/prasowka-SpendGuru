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
    contact_id: str = "",
    sequence_id: str = "",
    list_id: str = "",
    list_added: bool = False,
    sequence_added: bool = False,
    steps: dict | None = None,
) -> bool:
    """
    Wysyła mail powiadomienie approvalowe do NOTIFICATION_EMAIL.

    Subject: "Kampania {campaign_name} czeka na zatwierdzenie"
    Format: Pełny styled HTML — green banner, tabele artykuł/kontakt/status, opcjonalne Steps 1-3.

    steps: dict z email_1, follow_up_1, follow_up_2 (każdy z subject + body)
    """
    to_email = NOTIFICATION_EMAIL
    if not to_email:
        log.warning("[approval_email] NOTIFICATION_EMAIL not set — skipping")
        return False

    subject = f"Kampania {campaign_name} czeka na zatwierdzenie"
    tier_label = "Tier 1 (C-Level)" if "tier_1" in tier else "Tier 2 (Procurement/Management)"

    # Apollo links
    _seq_id = sequence_id or "69ea5642f22658000d2fdf13"
    _list_id = list_id or "69e898605270c8000d8137d3"
    apollo_seq_url = f"https://app.apollo.io/#/sequences/{_seq_id}"
    apollo_list_url = f"https://app.apollo.io/#/lists/{_list_id}"
    apollo_contact_url = f"https://app.apollo.io/#/people?contact_id={contact_id}" if contact_id else ""

    list_status = "&#10003; Dodano do listy" if list_added else "&#9888; Nie dodano do listy"
    list_color = "#198754" if list_added else "#cc6600"
    seq_status = "&#10003; Dodano do sekwencji" if sequence_added else "&#9888; Nie dodano do sekwencji"
    seq_color = "#198754" if sequence_added else "#cc6600"

    # Steps 1-3 HTML block
    steps_html = ""
    if steps:
        step_defs = [
            ("Step 1", "email_1", "#0062cc"),
            ("Step 2", "follow_up_1", "#6f42c1"),
            ("Step 3", "follow_up_2", "#20c997"),
        ]
        for label, key, color in step_defs:
            step_data = steps.get(key, {})
            subj = step_data.get("subject", "")
            body = step_data.get("body", "")
            if not subj and not body:
                continue
            import html as _html
            body_html_lines = "".join(
                f"<p style='margin:4px 0'>{_html.escape(line)}</p>" if line.strip() else "<br>"
                for line in body.split("\n")
            )
            steps_html += f"""
        <div style="margin:12px 0;padding:12px 16px;border-left:4px solid {color};background:#f8f9fa;border-radius:2px;">
          <div style="font-weight:bold;color:{color};font-size:13px;margin-bottom:6px;">{label}</div>
          <div style="margin-bottom:4px;font-size:13px;"><b>Temat:</b> {_html.escape(subj)}</div>
          <div style="margin-top:8px;padding:10px;background:#fff;border:1px solid #dee2e6;border-radius:4px;font-size:13px;line-height:1.6;">{body_html_lines}</div>
        </div>"""

        if steps_html:
            steps_html = f"""
    <!-- SEKWENCJA MAILOWA -->
    <div style="padding:0 24px 20px;">
      <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:13px;">
        <thead>
          <tr style="background:#343a40;color:#fff;">
            <th style="padding:9px 12px;text-align:left;font-size:12px;letter-spacing:.5px;font-weight:600;">
              SEKWENCJA MAILOWA (Step 1&ndash;3)
            </th>
          </tr>
        </thead>
      </table>
      {steps_html}
    </div>"""

    body_html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;font-size:14px;color:#212529;max-width:680px;margin:0 auto;padding:20px;background:#f8f9fa;">

  <div style="background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.08);">

    <!-- BANNER -->
    <div style="background:#198754;padding:18px 24px;">
      <p style="margin:0;color:#fff;font-size:17px;font-weight:bold;letter-spacing:.3px;">
        &#x1F7E2; KONTAKT DODANY DO APOLLO &mdash; CZEKA NA REVIEW
      </p>
      <p style="margin:6px 0 0;color:rgba(255,255,255,.8);font-size:12px;">
        Kampania: {campaign_name} &bull; SpendGuru Market News
      </p>
    </div>

    <!-- ARTYKU&#321; I FIRMA -->
    <div style="padding:0 24px 0;">
      <table style="width:100%;border-collapse:collapse;margin-top:20px;font-size:13px;">
        <thead>
          <tr style="background:#343a40;color:#fff;">
            <th colspan="2" style="padding:9px 12px;text-align:left;font-size:12px;letter-spacing:.5px;font-weight:600;">
              ARTYKU&#321; I FIRMA
            </th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;width:30%;background:#f8f9fa;">Artyku&#322;</td>
            <td style="padding:9px 12px;">
              <a href="{article_url}" style="color:#0d6efd;text-decoration:none;">{article_title or article_url}</a>
            </td>
          </tr>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Firma</td>
            <td style="padding:9px 12px;">{company_name or "&mdash;"}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- KONTAKT -->
    <div style="padding:0 24px 0;">
      <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:13px;">
        <thead>
          <tr style="background:#343a40;color:#fff;">
            <th colspan="2" style="padding:9px 12px;text-align:left;font-size:12px;letter-spacing:.5px;font-weight:600;">
              KONTAKT
            </th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;width:30%;background:#f8f9fa;">Imi&#281; i nazwisko</td>
            <td style="padding:9px 12px;">{full_name or "&mdash;"}</td>
          </tr>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Email</td>
            <td style="padding:9px 12px;">{email}</td>
          </tr>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Stanowisko</td>
            <td style="padding:9px 12px;">{job_title or "&mdash;"}</td>
          </tr>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Tier</td>
            <td style="padding:9px 12px;">{tier_label}</td>
          </tr>
          {'<tr style="border-bottom:1px solid #dee2e6;"><td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Apollo CRM</td><td style="padding:9px 12px;"><a href="' + apollo_contact_url + '" style="color:#0d6efd;text-decoration:none;">Otwórz kontakt w Apollo</a></td></tr>' if apollo_contact_url else ''}
        </tbody>
      </table>
    </div>

    <!-- STATUS APOLLO -->
    <div style="padding:0 24px 0;">
      <table style="width:100%;border-collapse:collapse;margin-top:16px;font-size:13px;">
        <thead>
          <tr style="background:#343a40;color:#fff;">
            <th colspan="2" style="padding:9px 12px;text-align:left;font-size:12px;letter-spacing:.5px;font-weight:600;">
              STATUS APOLLO
            </th>
          </tr>
        </thead>
        <tbody>
          <tr style="border-bottom:1px solid #dee2e6;">
            <td style="padding:9px 12px;font-weight:600;color:#555;width:30%;background:#f8f9fa;">Lista Apollo</td>
            <td style="padding:9px 12px;">
              <span style="color:{list_color};font-weight:600;">{list_status}</span>
              &nbsp;&bull;&nbsp;
              <a href="{apollo_list_url}" style="color:#0d6efd;text-decoration:none;font-size:12px;">Otwórz list&#281; &#8599;</a>
            </td>
          </tr>
          <tr>
            <td style="padding:9px 12px;font-weight:600;color:#555;background:#f8f9fa;">Sekwencja Apollo</td>
            <td style="padding:9px 12px;">
              <span style="color:{seq_color};font-weight:600;">{seq_status}</span>
              &nbsp;&bull;&nbsp;
              <a href="{apollo_seq_url}" style="color:#0d6efd;text-decoration:none;font-size:12px;">Otwórz sekwencj&#281; &#8599;</a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    {steps_html}

    <!-- FOOTER -->
    <div style="padding:16px 24px 20px;margin-top:16px;border-top:1px solid #dee2e6;">
      <p style="margin:0;color:#6c757d;font-size:11px;">
        Wiadomo&#347;&#263; wygenerowana automatycznie przez Pras&#243;wk&#281; SpendGuru (cloud runner).
      </p>
    </div>

  </div>

</body>
</html>"""

    try:
        result = send_email(to_email, subject, body_html)
        if result:
            log.info("[approval_email] Wysłano do %s (%s — %s) steps=%s",
                     to_email, company_name, full_name, "tak" if steps else "brak")
        else:
            log.warning("[approval_email] send_email zwrócił False")
        return result
    except Exception as exc:
        log.warning("[approval_email] Błąd wysyłki: %s", exc)
        return False

