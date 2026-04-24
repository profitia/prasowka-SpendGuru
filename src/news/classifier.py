"""
Article classifier — heuristics + optional LLM enrichment.

Stage 1 (always):  keyword heuristics
  - FMCG/food/production keywords in article text
  - Tier 1 role mention (regex)
  - Polish person name near role mention

Stage 2 (if LLM available):  LLM enrichment via existing openai_client
  - Validates and enriches heuristic findings
  - Extracts cleaner company name, role, outbound context

To disable LLM: set LLM_PROVIDER=none or remove OPENAI_API_KEY / GITHUB_TOKEN.
"""
from __future__ import annotations

import logging
import os
import re
import sys
from typing import Optional

log = logging.getLogger("news.classifier")

# ---------------------------------------------------------------------------
# FMCG / food / production keywords (Polish + common English)
# ---------------------------------------------------------------------------
_FMCG_KEYWORDS = [
    "producent", "produkcja", "spożywcz", "fmcg", "marka własna", "private label",
    "żywność", "żywnościow", "napój", "napoje", "przetwórstwo", "przetworcz",
    "artykuły spożywcze", "sklep", "sieć handlow", "dystrybucj", "eksport",
    "fabryka", "zakład produkcyjn", "brand", "marka", "portfolio produktów",
    "mięso", "nabiał", "mleko", "pieczywo", "chleb", "słodycze", "przekąski",
    "kawa", "herbata", "alkohol", "piwo", "wino", "wódka", "olej", "tłuszcz",
    "warzywa", "owoce", "mrożonki", "konserwy", "przetwory", "dania gotowe",
    "karma", "suplementy", "nutraceutyki", "retailing", "retail",
    "hipermarket", "supermarket", "dyskont", "biedronka", "lidl", "kaufland",
    "auchan", "carrefour", "żabka", "dino", "chata polska",
    "kategoria", "category", "sku", "półka", "listing", "planogram",
]

# ---------------------------------------------------------------------------
# Tier 1 role patterns (Polish + English)
# ---------------------------------------------------------------------------
_TIER1_ROLE_PATTERNS = [
    r"prezes\s+(?:zarządu|wykonawczy|grupy|spółki)?",
    r"wiceprezes\s+(?:zarządu|wykonawczy|grupy|spółki)?",
    r"CEO",
    r"CFO",
    r"COO",
    r"CTO",
    r"CMO",
    r"CRO",
    r"członek\s+zarządu",
    r"właściciel(?:ka)?",
    r"założyciel(?:ka)?",
    r"współzałożyciel(?:ka)?",
    r"founder",
    r"co-founder",
    r"managing\s+director",
    r"general\s+manager",
    r"dyrektor\s+(?:generalny|zarządzający|wykonawczy|finansowy|operacyjny|handlowy)",
    r"partner\s+zarządzający",
    r"managing\s+partner",
    r"board\s+(?:member|director)",
    r"member\s+of\s+(?:the\s+)?board",
]

_TIER1_RE = re.compile(
    r"(?:" + "|".join(_TIER1_ROLE_PATTERNS) + r")",
    re.IGNORECASE,
)

# Polish/international name: two or more capitalised words
_NAME_RE = re.compile(
    r"\b([A-ZŁÓŚĄĆĘŃŹŻ][a-złóśąćęńźż\-]{1,30}"
    r"(?:\s+[A-ZŁÓŚĄĆĘŃŹŻ][a-złóśąćęńźż\-]{1,30}){1,2})\b"
)

# Characters to look around a role mention for a nearby name
_NAME_WINDOW = 200


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_article(article: dict, criteria: dict) -> dict:
    """
    Classify a single article.

    Returns dict:
        qualified (bool), company (str), person (str), role (str),
        reason (str), outbound_context (str)
    """
    title = article.get("title", "") or ""
    text = article.get("text", "") or ""
    full = f"{title}. {text}"

    result: dict = {
        "qualified": False,
        "company": "",
        "person": "",
        "role": "",
        "reason": "",
        "outbound_context": "",
    }

    # --- Stage 1a: FMCG/food sector keywords ---
    fmcg_hits = _count_fmcg_keywords(full)
    if fmcg_hits == 0:
        result["reason"] = "Brak słów kluczowych branży FMCG/spożywczej"
        return result

    # --- Stage 1b: Tier 1 role + person name ---
    person, role = _find_tier1_person(full)
    if not person:
        result["reason"] = "Brak osoby Tier 1 (rola zarządcza + imię i nazwisko)"
        return result

    # --- Stage 1c: Fill heuristic result ---
    result["person"] = person
    result["role"] = role
    result["company"] = _extract_company_name(full)
    result["reason"] = (
        f"Osoba zarządcza: {person} ({role}); "
        f"branża FMCG ({fmcg_hits} słów kluczowych)"
    )
    result["outbound_context"] = _build_outbound_context(article, person, role, result["company"])

    # --- Stage 2 (optional): LLM enrichment ---
    llm = _try_llm_classify(article, person, role)
    if llm:
        result.update(llm)
    else:
        result["qualified"] = True

    return result


# ---------------------------------------------------------------------------
# Heuristic helpers
# ---------------------------------------------------------------------------

def _count_fmcg_keywords(text: str) -> int:
    lower = text.lower()
    return sum(1 for kw in _FMCG_KEYWORDS if kw in lower)


def _find_tier1_person(text: str) -> tuple[str, str]:
    """Return (person_name, role_label) or ('', '') if not found."""
    for match in _TIER1_RE.finditer(text):
        role_label = match.group(0).strip()
        start = max(0, match.start() - _NAME_WINDOW)
        end = min(len(text), match.end() + _NAME_WINDOW)
        window = text[start:end]
        names = _NAME_RE.findall(window)
        for candidate in names:
            parts = candidate.split()
            if len(parts) >= 2 and _is_plausible_name(candidate):
                return candidate, role_label
    return "", ""


def _is_plausible_name(name: str) -> bool:
    """Reject obvious non-names (single-word stopwords, very short words)."""
    stopwords = {
        "Zarząd", "Firma", "Spółka", "Polska", "Prezes", "Dyrektor",
        "Właściciel", "Założyciel", "Członek", "General", "Managing",
    }
    parts = name.split()
    return all(p not in stopwords for p in parts) and all(len(p) >= 2 for p in parts)


def _extract_company_name(text: str) -> str:
    """Very simple heuristic: look for entity + legal suffix."""
    pattern = re.compile(
        r"(?:firmy?|grupy?|spółk[ai]|koncernu?|przedsiębiorstw[ao])?\s*"
        r"([A-ZŁÓŚĄĆĘŃŹŻ][A-Za-zŁÓŚĄĆĘŃŹŻłóśąćęńźż\s\-&]{2,40}?)"
        r"\s*(?:Sp\.\s*z\s*o\.o\.|S\.A\.|sp\.j\.|S\.K\.A\.|Group|Polska|GmbH|Ltd|AG)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text[:3000]):
        name = m.group(1).strip()
        if 2 < len(name) < 60:
            return name
    return ""


def _build_outbound_context(article: dict, person: str, role: str, company: str) -> str:
    title = article.get("title", "")
    company_str = company or "tej firmy"
    return (
        f"Artykuł dotyczy {company_str} ({role}: {person}). "
        f"Temat: {title[:120]}. "
        f"Potencjalny hook do kampanii outbound: aktualne działania lub zmiany w firmie."
    )


# ---------------------------------------------------------------------------
# LLM enrichment (optional — uses parent workspace openai_client)
# ---------------------------------------------------------------------------

def _try_llm_classify(article: dict, person_hint: str, role_hint: str) -> Optional[dict]:
    """
    Try to enrich classification via LLM.
    Returns None on any error (LLM not configured, import failure, API error).
    """
    try:
        _inject_parent_src()
        from config.openai_client import is_available, get_client, get_fallback_model  # type: ignore
        if not is_available():
            log.debug("LLM niedostępny (brak klucza/tokena)")
            return None

        client = get_client()
        model = get_fallback_model()

        prompt = f"""Przeanalizuj artykuł prasowy i oceń, czy dotyczy osoby zarządczej (CEO, prezes, CFO, COO, właściciel itp.) z firmy z branży FMCG/spożywczej/produkcyjnej.

Tytuł: {article.get('title', '')}
Tekst (fragment): {article.get('text', '')[:2000]}

Heurystycznie znaleziono: osoba={person_hint}, rola={role_hint}

Odpowiedz TYLKO w JSON (bez markdown):
{{
  "qualified": true/false,
  "person": "Imię Nazwisko lub puste",
  "role": "stanowisko lub puste",
  "company": "nazwa firmy lub puste",
  "reason": "krótkie uzasadnienie po polsku (1 zdanie)",
  "outbound_context": "1-2 zdania kontekstu do kampanii outbound po polsku"
}}"""

        import json
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Jesteś analitykiem prasowym. Odpowiadasz wyłącznie w JSON."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_completion_tokens=400,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        result = json.loads(raw)
        log.debug("LLM classify: qualified=%s, company=%s", result.get("qualified"), result.get("company"))
        return result

    except Exception as exc:
        log.debug("LLM classify error: %s", exc)
        return None


def _inject_parent_src() -> None:
    """Add parent workspace src/ to sys.path so we can import config.openai_client."""
    _this = os.path.dirname(os.path.abspath(__file__))
    # this → src/news/ → src/ → Prasówki SpendGuru/ → Kampanie Apollo/
    parent_src = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(_this))),  # Kampanie Apollo/
        "src",
    )
    if parent_src not in sys.path:
        sys.path.insert(0, parent_src)
