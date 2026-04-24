#!/usr/bin/env python3
"""
rebuild_articles_json_from_db.py
=================================
Generuje czysty plik data/articles.json z bazy Neon Postgres.

Pobiera tylko rekordy z data_quality_status IN ('ok', 'unknown').
Pomija: rejected, needs_review.
Waliduje każdy rekord przed zapisem (validator wbudowany).

Użycie:
  python scripts/rebuild_articles_json_from_db.py [--dry-run] [--verbose]

Opcje:
  --dry-run     Nie zapisuje pliku, tylko drukuje podsumowanie
  --verbose     Szczegółowy output (co pomija i dlaczego)
  --allow-unknown  Dołącz rekordy z data_quality_status='unknown' (domyślnie tak)
  --only-ok        Tylko rekordy data_quality_status='ok'
  --output PATH    Ścieżka wyjściowa (domyślnie: data/articles.json)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

# ── Ścieżki ─────────────────────────────────────────────────────────────────
_THIS = Path(__file__).resolve()
_ROOT = _THIS.parent.parent   # Prasówki SpendGuru/

sys.path.insert(0, str(_ROOT / "src"))   # żeby znaleźć news.press_db

# ── Walidacja rekordów ───────────────────────────────────────────────────────

# Wzorce fragmentów zdań (nie-nazwy firm)
_SENTENCE_FRAGMENTS = [
    r"\bzarejestruj\b",
    r"\bco wym\b",
    r"\bz mleka\b",
    r"\bdo ekspansji z\b",
    r"\bzgromadziło\b",
    r"\bsetki uczestników\b",
    r"\bwym\b$",          # kończy się "wym"
    r"\brynku istotne\b",
    r"&\s*spodek",
    r"kongresowe\b",
    r"\blatach wym\b",
    r"\bcji zrealiz",
    r"\brum kongres",
]

# Znane nazwy mediów/portali — nie mogą być person
_MEDIA_NAMES = {
    "le monde", "downdetector", "to downdetector", "portal spożywczy",
    "wiadomości handlowe", "polskie stronnictwo ludowe",
    "europejski kongres gospodarczy", "europejskiego kongresu gospodarczego",
    "kongres", "spodek", "niemiec za", "k- stąd",
}

# Nievalid stanowiska (za krótkie, skróty bez sensu, lub wiadomo błędne)
_BAD_POSITIONS = {"cro", "cto", "cfo", "właściciel", "k-"}

_VALID_POSITION_KEYWORDS = {
    "prezes", "dyrektor", "kierownik", "zarząd", "szef", "wiceprezes",
    "ceo", "coo", "partner", "manager", "head", "president", "vp",
    "member", "officer", "founder", "managing",
}


def _is_sentence_fragment(text: str) -> bool:
    t_lower = text.lower().strip()
    original = text.strip()
    for pat in _SENTENCE_FRAGMENTS:
        if re.search(pat, t_lower):
            return True
    # Zaczyna się od małej litery i ma ≥2 słowa → może być fragment zdania
    # (sprawdzamy oryginalny tekst, nie lowercased!)
    words = original.split()
    if len(words) >= 2 and original[0].islower():
        return True
    return False


def _is_media_name(text: str) -> bool:
    t = text.lower().strip()
    for m in _MEDIA_NAMES:
        if m in t:
            return True
    return False


def _is_real_person(name: str) -> bool:
    """Heurystycznie sprawdza, czy name wygląda jak imię i nazwisko."""
    if not name or not name.strip():
        return False
    name = name.strip()
    if _is_media_name(name):
        return False
    # Musi zawierać co najmniej jedno słowo z dużej litery
    parts = name.split()
    if len(parts) < 2:
        return False
    # Wszystkie słowa zaczynają się od dużej litery (prawdopodobne imię + nazwisko)
    if not all(p[0].isupper() for p in parts if p):
        return False
    # Nie może być za długie (fragment zdania)
    if len(name) > 60:
        return False
    return True


def _is_valid_position(pos: str) -> bool:
    if not pos or not pos.strip():
        return True  # brak stanowiska — nie dyskwalifikuje
    p = pos.lower().strip()
    if p in _BAD_POSITIONS:
        return False
    # Sprawdź czy zawiera sensowne słowo kluczowe
    for kw in _VALID_POSITION_KEYWORDS:
        if kw in p:
            return True
    # Bardzo krótkie stanowiska (≤3 znaki) bez sensu
    if len(p) <= 3:
        return False
    return True


def _is_valid_company(company: str) -> bool:
    if not company or not company.strip():
        return False  # brak firmy — błąd
    c = company.strip()
    if _is_sentence_fragment(c):
        return False
    # Zbyt krótkie
    if len(c) < 3:
        return False
    return True


def validate_record(row: dict) -> tuple[bool, list[str]]:
    """
    Waliduje rekord z DB. Zwraca (ok: bool, errors: list[str]).
    Błędy krytyczne powodują pominięcie rekordu w eksporcie.
    """
    errors: list[str] = []

    company = (row.get("company_name") or "").strip()
    person  = (row.get("tier1_person") or "").strip()
    pos     = (row.get("tier1_position") or "").strip()

    if not _is_valid_company(company):
        errors.append(f"company_name niepoprawna: {company!r}")

    if person and not _is_real_person(person):
        errors.append(f"tier1_person nie wygląda jak osoba: {person!r}")

    if pos and not _is_valid_position(pos):
        errors.append(f"tier1_position nieprawidłowe: {pos!r}")

    return (len(errors) == 0), errors


# ── Mapowanie DB → JSON ──────────────────────────────────────────────────────

def _fmt_date(val) -> str:
    if val is None:
        return ""
    if isinstance(val, (date, datetime)):
        return val.strftime("%Y-%m-%d")
    return str(val)


def _fmt_dt(val) -> str:
    if val is None:
        return ""
    if isinstance(val, datetime):
        return val.isoformat()
    return str(val)


def db_row_to_json(row: dict) -> dict:
    """Mapuje wiersz z DB na format data/articles.json."""
    return {
        "id":              row.get("article_id") or str(row.get("id", "")),
        "industry":        row.get("industry") or "",
        "press_type":      row.get("press_type") or "",
        "article_date":    _fmt_date(row.get("article_date")),
        "title":           row.get("article_title") or "",
        "source_name":     row.get("source_name") or "",
        "source_url":      row.get("article_url") or "",
        "company":         row.get("company_name") or "",
        "tier1_person":    row.get("tier1_person") or "",
        "tier1_position":  row.get("tier1_position") or "",
        "tier2_person":    row.get("tier2_person") or "",
        "tier2_position":  row.get("tier2_position") or "",
        "reason":          row.get("reason") or "",
        "context":         row.get("context") or "",
        "contact_email":   row.get("tier1_email") or "",
        "status":          row.get("apollo_status") or "waiting",
        "data_quality_status": row.get("data_quality_status") or "unknown",
        "created_at":      _fmt_dt(row.get("created_at")),
        "updated_at":      _fmt_dt(row.get("updated_at")),
    }


# ── Główna logika ────────────────────────────────────────────────────────────

def load_db_env() -> None:
    """Ładuje .env jeśli plik istnieje (dotenv-lite)."""
    env_path = _ROOT / ".env"
    if env_path.exists():
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k not in os.environ:
                    os.environ[k] = v


def fetch_quality_records(allow_statuses: set[str], verbose: bool) -> list[dict]:
    """Pobiera z DB rekordy z podanymi statusami jakości."""
    from news.press_db import get_connection

    placeholders = ", ".join(f"'{s}'" for s in allow_statuses)
    sql = f"""
        SELECT
            id, article_id, article_url, article_title, article_date,
            source_name, company_name, industry, press_type,
            tier1_person, tier1_position, tier1_email,
            tier2_person, tier2_position, tier2_email,
            reason, context, apollo_status, created_at, updated_at,
            COALESCE(data_quality_status, 'unknown') AS data_quality_status,
            data_quality_notes
        FROM apollo.press_articles
        WHERE COALESCE(data_quality_status, 'unknown') IN ({placeholders})
        ORDER BY article_date DESC NULLS LAST, created_at DESC
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    if verbose:
        print(f"  Pobrano {len(rows)} rekordów z DB (statusy: {', '.join(sorted(allow_statuses))})")
    return rows


def rebuild(args) -> None:
    load_db_env()

    allow_statuses = {"ok", "unknown"} if not args.only_ok else {"ok"}

    print("Pobieranie danych z bazy...")
    rows = fetch_quality_records(allow_statuses, args.verbose)

    output: list[dict] = []
    skipped_quality: list[tuple[str, list[str]]] = []
    skipped_validation: list[tuple[str, list[str]]] = []

    for row in rows:
        title  = row.get("article_title") or "(brak tytułu)"
        art_id = row.get("article_id") or str(row.get("id", "?"))

        # 1. Sprawdź data_quality_status (z DB)
        dqs = row.get("data_quality_status") or "unknown"
        if dqs not in allow_statuses:
            skipped_quality.append((art_id, [f"data_quality_status={dqs}"]))
            continue

        # 2. Walidacja heurystyczna (dodatkowa ochrona)
        ok, errors = validate_record(row)
        if not ok:
            skipped_validation.append((art_id, errors))
            if args.verbose:
                print(f"  POMINIĘTO [{art_id}]: {title[:60]}")
                for e in errors:
                    print(f"    - {e}")
            continue

        output.append(db_row_to_json(row))

    # Podsumowanie
    print(f"\n{'='*60}")
    print(f"PODSUMOWANIE REBUILDU")
    print(f"{'='*60}")
    print(f"  Rekordy z DB              : {len(rows)}")
    print(f"  Pominiętych (jakość DB)   : {len(skipped_quality)}")
    print(f"  Pominiętych (walidacja)   : {len(skipped_validation)}")
    print(f"  Wyeksportowanych          : {len(output)}")

    if skipped_validation:
        print(f"\n  Rekordy pominięte przez walidator:")
        for art_id, errors in skipped_validation:
            print(f"    [{art_id}]: {', '.join(errors)}")

    if args.dry_run:
        print(f"\n[DRY RUN] Nie zapisano pliku. Użyj bez --dry-run aby zapisać.")
        if output:
            print(f"\nPrzykład pierwszego rekordu który zostałby zapisany:")
            print(json.dumps(output[0], ensure_ascii=False, indent=2))
        return

    # Zapis
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"\n  Zapisano: {output_path} ({len(output)} rekordów)")

    if not output:
        print("\n  UWAGA: Plik jest pusty — brak rekordów spełniających kryteria jakości.")
        print("  Użyj scripts/cleanup_bad_press_articles.py aby zatwierdzić poprawne rekordy.")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generuje data/articles.json z bazy Neon Postgres (tylko czyste rekordy)"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Nie zapisuj — tylko podsumowanie"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Szczegółowy output"
    )
    parser.add_argument(
        "--only-ok", action="store_true",
        help="Eksportuj tylko rekordy z data_quality_status='ok'"
    )
    parser.add_argument(
        "--output", default=str(_ROOT / "data" / "articles.json"),
        help="Ścieżka wyjściowa (domyślnie: data/articles.json)"
    )
    args = parser.parse_args()
    rebuild(args)


if __name__ == "__main__":
    main()
