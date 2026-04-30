"""
cloud_message_generator.py — generuje 3-krokową sekwencję mailową na Render.

Samodzielny moduł — bez zależności od lokalnego workspace Kampanie Apollo.
Używa GitHub Models (GITHUB_TOKEN) lub OpenAI (OPENAI_API_KEY).

ENV:
    GITHUB_TOKEN        — GitHub Models (preferowany)
    OPENAI_API_KEY      — OpenAI jako fallback
    LLM_MODEL           — model do użycia (domyślnie: gpt-4.1-mini)
"""
from __future__ import annotations

import json
import logging
import os
from urllib.parse import urlparse

log = logging.getLogger("cloud_message_generator")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_GITHUB_MODELS_BASE = "https://models.inference.ai.azure.com"
_DEFAULT_MODEL = "gpt-4.1-mini"

CALENDLY_URLS: dict[str, str] = {
    "tier_1_c_level": "https://calendly.com/profitia/zakupy-a-marza-firmy",
    "tier_2_procurement_management": "https://calendly.com/profitia/standard-negocjacji-i-oszczednosci",
}

TIER_LABELS: dict[str, str] = {
    "tier_1_c_level": "Tier 1 — C-Level / Zarząd",
    "tier_2_procurement_management": "Tier 2 — Procurement Management",
}

TIER_PERSPECTIVES: dict[str, str] = {
    "tier_1_c_level": """Perspektywa TIER 1 — C-Level / Zarząd:
- Narracja: marża, rentowność, EBIT, presja kosztowa, przewidywalność wyników, kontrola ryzyka
- Pain points: presja na wynik, podwyżki dostawców bez możliwości obrony, brak wglądu w realne oszczędności
- Value prop: powtarzalny standard decyzji zakupowych przekłada się bezpośrednio na marżę
- CTA: https://calendly.com/profitia/zakupy-a-marza-firmy (15-min rozmowa)

BRIDGE dla TIER 1: Mów o ORGANIZACJI i obszarze zakupów — NIE o roli odbiorcy jako osoby.
ZAKAZANE: "W Pana roli jako Prezes...", "Jako CEO musi Pan..."
PREFEROWANE: "W takiej sytuacji w organizacji...", "Przy takiej skali to właśnie w obszarze zakupów..."

SOFT CTA DLA TIER 1 — dodaj na końcu każdego maila (różny wording):
Email 1: "Mam świadomość, że nie zajmuje się Pan bezpośrednio zakupami i warunkami współpracy z dostawcami - dlatego jeśli uzna Pan, że tak będzie lepiej, będę wdzięczny za przekazanie mojej wiadomości do Dyrektora Zakupów."
Follow-up 1: "Jeśli w Pana organizacji tym obszarem zajmuje się ktoś z zakupów lub procurement, będę wdzięczny za przekazanie tej wiadomości dalej."
Follow-up 2: "Jeśli uzna Pan, że to bardziej temat dla Dyrektora Zakupów, z góry dziękuję za przekazanie wiadomości." """,

    "tier_2_procurement_management": """Perspektywa TIER 2 — Procurement Management / Liderzy zakupów:
- Narracja: przygotowanie do negocjacji, standard pracy kupców, jakość argumentacji, cost drivers, savings
- Pain points: brak powtarzalnego standardu przygotowania, trudność uzasadnienia decyzji zarządowi
- Value prop: systematyczne przygotowanie każdej negocjacji — nie jednorazowy projekt, ale trwały standard
- CTA: https://calendly.com/profitia/standard-negocjacji-i-oszczednosci """,
}


# ---------------------------------------------------------------------------
# Article fetcher (minimal)
# ---------------------------------------------------------------------------

def _fetch_article(url: str) -> dict:
    """Pobiera tytuł i lead artykułu z URL. Zwraca {title, lead, source}."""
    result = {"title": "", "lead": "", "source": "", "url": url}
    try:
        from urllib.parse import urlparse as _up
        parsed = _up(url)
        result["source"] = parsed.netloc.lstrip("www.")
    except Exception:
        pass

    try:
        import requests
        from bs4 import BeautifulSoup
        resp = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        if not resp.ok:
            return result
        soup = BeautifulSoup(resp.text, "html.parser")

        # Title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            result["title"] = og_title["content"].strip()
        elif soup.title:
            result["title"] = soup.title.string.strip() if soup.title.string else ""

        # Description / lead
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            result["lead"] = og_desc["content"].strip()
        else:
            meta_desc = soup.find("meta", attrs={"name": "description"})
            if meta_desc and meta_desc.get("content"):
                result["lead"] = meta_desc["content"].strip()

        # Body excerpt (first 1500 chars from paragraphs)
        paragraphs = soup.find_all("p")
        body_text = " ".join(p.get_text(" ", strip=True) for p in paragraphs[:15])
        result["body_excerpt"] = body_text[:1500]

    except Exception as exc:
        log.warning("[cloud_msg] Article fetch error for %s: %s", url, exc)

    return result


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

def _call_llm(prompt: str, system: str) -> dict | None:
    """Wywołuje LLM (GitHub Models lub OpenAI). Zwraca sparsowany JSON lub None."""
    github_token = os.environ.get("GITHUB_TOKEN", "").strip()
    openai_key = os.environ.get("OPENAI_API_KEY", "").strip()
    model = os.environ.get("LLM_MODEL", _DEFAULT_MODEL).strip()

    try:
        import openai as _openai
    except ImportError:
        log.warning("[cloud_msg] openai package not installed")
        return None

    def _try_call(client: object, label: str) -> dict | None:
        try:
            resp = client.chat.completions.create(  # type: ignore[union-attr]
                model=model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.6,
                max_completion_tokens=3000,
                timeout=45,
            )
            raw = resp.choices[0].message.content or "{}"
            return json.loads(raw)
        except Exception as exc:
            log.warning("[cloud_msg] LLM call failed (%s): %s", label, exc)
            return None

    # Try GitHub Models first, fall back to OpenAI on any error (incl. 401)
    if github_token:
        log.info("[cloud_msg] Using GitHub Models: %s", model)
        client_gh = _openai.OpenAI(base_url=_GITHUB_MODELS_BASE, api_key=github_token)
        result = _try_call(client_gh, "github_models")
        if result is not None:
            return result
        log.info("[cloud_msg] GitHub Models failed — trying OpenAI fallback")

    if openai_key:
        log.info("[cloud_msg] Using OpenAI fallback: %s", model)
        client_oai = _openai.OpenAI(api_key=openai_key)
        return _try_call(client_oai, "openai")

    log.warning("[cloud_msg] No LLM credentials (GITHUB_TOKEN / OPENAI_API_KEY)")
    return None


# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

def _build_prompt(
    article: dict,
    full_name: str,
    job_title: str,
    company_name: str,
    tier: str,
) -> str:
    tier_label = TIER_LABELS.get(tier, tier)
    tier_perspective = TIER_PERSPECTIVES.get(tier, TIER_PERSPECTIVES["tier_2_procurement_management"])
    calendly = CALENDLY_URLS.get(tier, CALENDLY_URLS["tier_2_procurement_management"])

    # Resolve vocative form of first name using Polish names dictionary
    first_name = full_name.split()[0] if full_name else ""
    try:
        import sys as _sys
        import os as _os
        _src_dir = _os.path.dirname(_os.path.abspath(__file__))
        if _src_dir not in _sys.path:
            _sys.path.insert(0, _src_dir)
        from polish_names import resolve_polish_contact
        _resolved = resolve_polish_contact(first_name)
        greeting = _resolved["greeting"]
        vocative = _resolved["first_name_vocative"] or first_name
    except Exception:
        gender_guess = "female" if first_name and first_name[-1].lower() == "a" else "male"
        vocative = first_name
        pan_pani = "Pani" if gender_guess == "female" else "Panie"
        greeting = f"Dzień dobry {pan_pani} {vocative},"

    return f"""Jesteś ekspertem od komunikacji B2B i outreachu do firm produkcyjnych i FMCG w Polsce.

Twoje zadanie: wygeneruj 3-krokową sekwencję mailową (Email 1 + Follow-up 1 + Follow-up 2) dla jednego kontaktu na podstawie artykułu branżowego.

---

## KONTEKST PRODUKTU

SpendGuru to narzędzie Negotiation Intelligence — NIE platforma analityczna.
Główna obietnica: "Lepsze przygotowanie. Lepsze negocjacje. Lepszy wynik."
POZYCJONOWANIE: negotiation-first, nie analytics-first.
ZAKAZANE: zaczynanie od listy modułów, "nasze narzędzie", "nasza platforma", "demo request".

---

## DANE KONTAKTU

Imię (wołacz): {vocative}
Powitanie: {greeting}
Imię i nazwisko: {full_name}
Stanowisko: {job_title}
Firma: {company_name}
Tier: {tier_label}
Forma adresowania: Pan (dostosuj do płci jeśli imię wskazuje na kobietę → Pani)

---

## ARTYKUŁ BAZOWY (trigger)

Tytuł: {article.get("title", "")}
Źródło: {article.get("source", "")}
URL: {article.get("url", "")}
Lead: {article.get("lead", "")}
Fragment treści: {article.get("body_excerpt", "")[:1200]}

---

## TIER I PERSPEKTYWA

{tier_perspective}

---

## LOGIKA SEKWENCJI — OBOWIĄZKOWA STRUKTURA

### EMAIL 1 (D0) — 120–170 słów (bez podpisu)
Obowiązkowe elementy:
1. ANCHOR: pierwsze zdanie zawiera tytuł/temat artykułu + źródło + odniesienie do firmy
2. Hipoteza: 1 konkretny fakt z artykułu, ton osobistej obserwacji (NIE "Z artykułu wynika, że...")
3. Bridge: jak fakt przekłada się na koszty/marżę/rentowność w tej firmie
4. Framework: "Nazywam się Tomasz Uściński i jestem z Profitii - polskiej firmy, która od 15 lat pomaga firmom z branży [branża] ograniczać koszty związane z zakupami."
5. CTA (patrz sekcja CTA)

### FOLLOW-UP 1 (D+2) — 60–100 słów (bez podpisu)
- WNOSI NOWĄ WARTOŚĆ: rozwiń 1 konkretny mechanizm z artykułu, którego nie było w Email 1
- NIE powtarzaj Email 1
- CTA (patrz sekcja CTA)

### FOLLOW-UP 2 (D+2 od FU1) — 40–80 słów (bez podpisu)
- Krótki, prosty, bez presji
- Nawiąż do triggera jednym zdaniem
- CTA (patrz sekcja CTA)

---

## CTA — REGUŁY OBOWIĄZKOWE

Każde CTA MUSI mieć 3 elementy (w tej kolejności):
1. Zdanie wprowadzające (np. "Jeśli temat jest dla Pana interesujący, proszę wybrać termin krótkiego spotkania online:")
2. Link Calendly w osobnej linii: {calendly}
3. Alternatywa telefoniczna w osobnej linii: "Jeśli wygodniejsza będzie krótka rozmowa telefoniczna, proszę przesłać numer - oddzwonię."

---

## STYL I TON

- Mail ma brzmieć jak napisany PO PRZECZYTANIU konkretnego artykułu — nie jak formalna analiza
- Krótsze zdania, prostsze słownictwo, mniej technoKratyczny język
- Każde zdanie konkretne dla tej firmy — bez ogólników
- Powitanie OBOWIĄZKOWE: zacznij dokładnie od "{greeting}" — użyj tej formy bez zmian
- Po powitaniu — następny akapit od małej litery
- NIGDY em dash "—" → zawsze zwykły myślnik " - "
- Po kropce zawsze wielka litera
- ZAKAZANE: "Z artykułu wynika, że", "Artykuł pokazuje, że", "W artykule opisano"
- PREFEROWANE: "Zwróciłem uwagę na to, że", "Uderzyło mnie, że", "Czytając ten artykuł..."
- NIE generuj podpisu — podpis pochodzi z custom field Apollo

---

## FORMAT ODPOWIEDZI (JSON — nic poza JSON)

{{
  "email_1": {{"subject": "Temat maila — max 55 znaków", "body": "Pełna treść Email 1 (plain text, BEZ podpisu)"}},
  "follow_up_1": {{"subject": "Temat FU1 — różny od E1", "body": "Pełna treść FU1 (plain text, BEZ podpisu)"}},
  "follow_up_2": {{"subject": "Temat FU2 — krótki, różny od E1 i FU1", "body": "Pełna treść FU2 (plain text, BEZ podpisu)"}},
  "review_notes": {{"trigger_used": "...", "hypothesis": "...", "bridge": "..."}}
}}"""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_steps(
    article_url: str,
    full_name: str,
    job_title: str,
    company_name: str,
    tier: str,
    article_title: str = "",
) -> dict | None:
    """
    Generuje 3-krokową sekwencję mailową dla kontaktu.

    Returns:
        dict z kluczami email_1, follow_up_1, follow_up_2 (każdy z subject + body)
        lub None jeśli generacja nieudana.

    ENV:
        GITHUB_TOKEN lub OPENAI_API_KEY — wymagany
        LLM_MODEL — model (domyślnie gpt-4.1-mini)
    """
    log.info(
        "[cloud_msg] Generating steps: company=%s tier=%s url=%s",
        company_name, tier, article_url[:80],
    )

    # Fetch article
    article = _fetch_article(article_url)
    if article_title and not article.get("title"):
        article["title"] = article_title

    if not article.get("title") and not article.get("lead"):
        log.warning("[cloud_msg] Article fetch returned no content — using minimal context")

    # Build prompt
    system = (
        "Jesteś ekspertem od komunikacji B2B i outreachu. "
        "Piszesz naturalnie — krótsze zdania, prostsze słownictwo. "
        "Mail ma brzmieć jak napisany po przeczytaniu artykułu, nie jak formalna analiza. "
        "ZAKAZANE: 'Z artykułu wynika, że', 'Artykuł pokazuje, że', 'W artykule opisano'. "
        "Odpowiadasz WYŁĄCZNIE w JSON. Żadnych komentarzy poza JSON."
    )
    prompt = _build_prompt(article, full_name, job_title, company_name, tier)

    result = _call_llm(prompt, system)
    if not result or "email_1" not in result:
        log.warning("[cloud_msg] LLM returned no usable result")
        return None

    log.info(
        "[cloud_msg] Steps generated OK: e1_subj=%r fu1_subj=%r fu2_subj=%r",
        result.get("email_1", {}).get("subject", "")[:40],
        result.get("follow_up_1", {}).get("subject", "")[:40],
        result.get("follow_up_2", {}).get("subject", "")[:40],
    )
    return result
