#!/usr/bin/env python3
"""
scripts/audit_press_articles.py
Audytuje dane w apollo.press_articles i raportuje podejrzane rekordy.

Użycie:
    cd "Prasówki SpendGuru"
    set -a; source .env; set +a
    python scripts/audit_press_articles.py
    python scripts/audit_press_articles.py --json > audit_report.json
    python scripts/audit_press_articles.py --top 20
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Optional

# Path setup
_THIS = Path(__file__).parent
_ROOT = _THIS.parent
_SRC  = _ROOT / "src"
for p in [str(_SRC), str(_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)


# ---------------------------------------------------------------------------
# Heuristics
# ---------------------------------------------------------------------------

# Słowa i frazy sugerujące że company_name to fragment zdania lub tekstu
_COMPANY_SENTENCE_FRAGMENTS = [
    r"\bco wym\b", r"\bz mleka\b", r"\bzarejestruj\b", r"\bspodek\b",
    r"\bkongres\b", r"\bproblem\b", r"\brynek\b", r"\bartykuł\b",
    r"\bczytaj\b", r"\bwięcej\b", r"\bkliknij\b", r"\bdownloader\b",
    r"\bdowndetect", r"\b(?:le |la |les )\b",   # media nazwy (Le Monde itp.)
    r"\bforum\b", r"\bportal\b", r"\bserwis\b", r"\bredakcja\b",
    r"\bnewsletter\b", r"\bprasówka\b",
    r"^\s*\w{1,4}\s*$",            # bardzo krótkie (1-4 znaki)
    r"[.!?]\s",                    # zdanie z interpunkcją w środku
    r"\d{4}-\d{2}-\d{2}",         # data w nazwie
]
_COMPANY_FRAG_RE = [re.compile(p, re.IGNORECASE) for p in _COMPANY_SENTENCE_FRAGMENTS]

# Nazwy mediów, które mogą trafić do company_name lub full_name
_MEDIA_NAMES = {
    "le monde", "reuters", "bloomberg", "forbes", "rzeczpospolita",
    "puls biznesu", "business insider", "wirtualna polska", "wp",
    "onet", "interia", "tvn24", "polsat news", "spożywczy.pl",
    "portalspożywczy", "fresh market", "hurt & detal", "dlahandlu",
    "newsweek", "polityka", "fakt", "dziennik gazeta prawna",
    "gazeta wyborcza", "pap", "rp.pl",
}

# Stanowiska — akceptowalne wzorce
_VALID_POSITION_RE = re.compile(
    r"(?:prezes|wiceprezes|dyrektor|kierownik|manager|ceo|cfo|coo|cto|cmo|cro|"
    r"właściciel|założyciel|partner|członek|head|director|president|vice.president|"
    r"country.manager|general.manager|managing|board|zarząd|zarządzający|operacyjny|"
    r"handlowy|finansowy|marketingu|sprzedaży|zakupów|supply|logistyk|commercial|"
    r"category|procurement|retail|trade|chief|officer|vp\b|svp\b|evp\b)",
    re.IGNORECASE,
)

# Wzorzec imienia i nazwiska — przynajmniej 2 człony, każdy z wielkiej litery
_NAME_RE = re.compile(
    r"^[A-ZŁÓŚĄĆĘŃŹŻ][a-złóśąćęńźż\-]{1,30}"
    r"(?:\s+[A-ZŁÓŚĄĆĘŃŹŻ][a-złóśąćęńźż\-]{1,30})+$"
)

# Słowa, które NIGDY nie są imieniem osoby (nazwy mediów, słowa kluczowe itp.)
_NOT_A_PERSON = {
    "Le Monde", "Reuters", "Bloomberg", "Forbes", "Business Insider",
    "Wirtualna Polska", "To Downdetector", "Downdetector", "Polska Press",
    "Puls Biznesu", "Rzeczpospolita", "Gazeta Prawna",
}


def _is_company_sentence(name: Optional[str]) -> list[str]:
    """Zwraca listę powodów dlaczego company_name wygląda jak fragment zdania."""
    if not name:
        return ["brak company_name"]
    issues = []
    if len(name.strip()) < 3:
        issues.append(f"za krótka ({len(name.strip())} znaki)")
    if len(name) > 80:
        issues.append("za długa (>80 znaków)")
    for pat in _COMPANY_FRAG_RE:
        if pat.search(name):
            issues.append(f"fragment zdania: pasuje do wzorca '{pat.pattern}'")
            break
    name_lower = name.strip().lower()
    for media in _MEDIA_NAMES:
        if media in name_lower:
            issues.append(f"wygląda jak nazwa medium: '{media}'")
            break
    return issues


def _is_valid_person(name: Optional[str]) -> list[str]:
    """Zwraca listę powodów dlaczego full_name nie wygląda jak osoba."""
    if not name:
        return []  # brak osoby = OK (pole opcjonalne)
    issues = []
    if name in _NOT_A_PERSON:
        issues.append(f"to nazwa medium/serwisu: '{name}'")
    if not _NAME_RE.match(name.strip()):
        issues.append("nie pasuje do wzorca Imię Nazwisko (dwa człony z wielkiej litery)")
    name_lower = name.strip().lower()
    for media in _MEDIA_NAMES:
        if media in name_lower:
            issues.append(f"zawiera nazwę medium: '{media}'")
    # Bardzo krótka "nazwa"
    parts = name.strip().split()
    if any(len(p) < 2 for p in parts):
        issues.append("jeden z członów ma < 2 znaki")
    return issues


def _is_valid_position(pos: Optional[str]) -> list[str]:
    """Zwraca listę powodów dlaczego stanowisko wygląda podejrzanie."""
    if not pos:
        return []  # brak stanowiska = OK (opcjonalne)
    issues = []
    if len(pos.strip()) < 3:
        issues.append(f"stanowisko za krótkie: '{pos}'")
    if not _VALID_POSITION_RE.search(pos):
        issues.append(f"nieznane stanowisko: '{pos}'")
    return issues


def _has_valid_url(url: Optional[str]) -> bool:
    if not url:
        return False
    return url.startswith(("http://", "https://"))


def audit_row(row: dict) -> dict:
    """
    Audytuje jeden rekord. Zwraca dict z:
        id, article_url, issues (list[str]), severity ('ok'|'warn'|'critical')
    """
    issues: list[str] = []

    # URL
    if not _has_valid_url(row.get("article_url")):
        issues.append("brak lub nieprawidłowy article_url")

    # Tytuł
    if not row.get("article_title"):
        issues.append("brak article_title")

    # company_name
    co_issues = _is_company_sentence(row.get("company_name"))
    for i in co_issues:
        issues.append(f"company_name: {i}")

    # tier1_person / tier1_position
    p1_issues = _is_valid_person(row.get("tier1_person"))
    for i in p1_issues:
        issues.append(f"tier1_person: {i}")

    p1pos_issues = _is_valid_position(row.get("tier1_position"))
    for i in p1pos_issues:
        issues.append(f"tier1_position: {i}")

    # tier2_person / tier2_position
    p2_issues = _is_valid_person(row.get("tier2_person"))
    for i in p2_issues:
        issues.append(f"tier2_person: {i}")

    p2pos_issues = _is_valid_position(row.get("tier2_position"))
    for i in p2pos_issues:
        issues.append(f"tier2_position: {i}")

    # Krzyżowe: title lub source_name w złym polu
    title = (row.get("article_title") or "").lower()
    source = (row.get("source_name") or "").lower()
    co_lower = (row.get("company_name") or "").lower()
    if title and co_lower and title[:30] in co_lower:
        issues.append("company_name wygląda jak fragment tytułu artykułu")
    if source and co_lower and source in co_lower:
        issues.append("company_name wygląda jak source_name (nazwa portalu)")

    p1 = (row.get("tier1_person") or "").lower()
    if source and p1 and source in p1:
        issues.append("tier1_person wygląda jak source_name (nazwa portalu)")

    # Severity
    critical_patterns = [
        "brak article_url", "brak article_title",
        "company_name: brak", "company_name: fragment zdania",
        "company_name: wygląda jak nazwę medium",
        "tier1_person: to nazwa medium",
        "company_name wygląda jak fragment tytułu",
        "company_name wygląda jak source_name",
        "tier1_person wygląda jak source_name",
    ]
    if any(any(c in iss for c in critical_patterns) for iss in issues):
        severity = "critical"
    elif issues:
        severity = "warn"
    else:
        severity = "ok"

    return {
        "id":              row.get("id"),
        "article_url":     row.get("article_url") or "",
        "article_title":   row.get("article_title") or "",
        "source_name":     row.get("source_name") or "",
        "company_name":    row.get("company_name") or "",
        "tier1_person":    row.get("tier1_person") or "",
        "tier1_position":  row.get("tier1_position") or "",
        "tier2_person":    row.get("tier2_person") or "",
        "tier2_position":  row.get("tier2_position") or "",
        "apollo_status":   row.get("apollo_status") or "",
        "data_quality_status": row.get("data_quality_status") or "unknown",
        "issues":          issues,
        "severity":        severity,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Audyt jakości danych w apollo.press_articles")
    parser.add_argument("--json",    action="store_true", help="Wypisz pełny raport w JSON")
    parser.add_argument("--top",     type=int, default=10, help="Ile najgorszych rekordów pokazać (domyślnie 10)")
    parser.add_argument("--min-severity", choices=["ok", "warn", "critical"], default="warn",
                        help="Minimalny poziom błędu do wypisania (domyślnie warn)")
    args = parser.parse_args()

    from news.press_db import get_connection
    import psycopg.rows  # type: ignore

    print("Łączenie z bazą danych...")
    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("""
                SELECT
                    id, article_id, article_url, article_title,
                    article_date, source_name, company_name,
                    tier1_person, tier1_position,
                    tier2_person, tier2_position,
                    apollo_status,
                    COALESCE(data_quality_status, 'unknown') AS data_quality_status
                FROM apollo.press_articles
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

    print(f"Pobrano {len(rows)} rekordów.\n")

    results = [audit_row(dict(r)) for r in rows]

    # Statystyki
    total     = len(results)
    ok_cnt    = sum(1 for r in results if r["severity"] == "ok")
    warn_cnt  = sum(1 for r in results if r["severity"] == "warn")
    crit_cnt  = sum(1 for r in results if r["severity"] == "critical")

    # Statystyki po data_quality_status
    from collections import Counter
    dqs_counts = Counter(r["data_quality_status"] for r in results)

    if args.json:
        print(json.dumps({
            "summary": {
                "total": total, "ok": ok_cnt, "warn": warn_cnt, "critical": crit_cnt,
                "by_data_quality_status": dict(dqs_counts),
            },
            "records": [r for r in results if r["severity"] != "ok"],
        }, ensure_ascii=False, indent=2))
        return

    # Text report
    print("=" * 70)
    print("PODSUMOWANIE AUDYTU DANYCH")
    print("=" * 70)
    print(f"  Łącznie rekordów : {total}")
    print(f"  OK               : {ok_cnt}")
    print(f"  Ostrzeżenia      : {warn_cnt}")
    print(f"  Krytyczne        : {crit_cnt}")
    print()
    print("  data_quality_status:")
    for status, cnt in sorted(dqs_counts.items()):
        print(f"    {status:20s}: {cnt}")
    print()

    # Sortuj: krytyczne najpierw, potem warn, potem ok; w ramach severity wg liczby błędów
    sev_order = {"critical": 0, "warn": 1, "ok": 2}
    problematic = sorted(
        [r for r in results if r["severity"] in ("critical", "warn")],
        key=lambda r: (sev_order[r["severity"]], -len(r["issues"])),
    )

    min_sev = {"ok": 2, "warn": 1, "critical": 0}[args.min_severity]
    show = [r for r in problematic if sev_order[r["severity"]] <= min_sev]
    show = show[:args.top]

    if not show:
        print("Brak rekordów spełniających kryteria audytu.")
        return

    print(f"TOP {min(args.top, len(show))} NAJGORSZYCH REKORDÓW:")
    print("-" * 70)
    for i, r in enumerate(show, 1):
        sev_label = "🔴 KRYTYCZNE" if r["severity"] == "critical" else "🟡 OSTRZEŻENIE"
        print(f"\n{i}. [{sev_label}] ID={r['id']}")
        print(f"   URL    : {r['article_url'][:80]}")
        print(f"   Tytuł  : {r['article_title'][:70]}")
        print(f"   Firma  : {r['company_name']}")
        print(f"   Osoba  : {r['tier1_person']} / {r['tier1_position']}")
        print(f"   Źródło : {r['source_name']}")
        print(f"   Status : apollo={r['apollo_status']} | quality={r['data_quality_status']}")
        print(f"   Błędy  :")
        for iss in r["issues"]:
            print(f"     - {iss}")

    print()
    print(f"Łącznie podejrzanych rekordów: {len(problematic)} / {total}")
    print()
    print("Aby oznaczyć podejrzane rekordy w bazie uruchom:")
    print("  python scripts/mark_bad_press_articles.py")
    print()
    print("Aby zobaczyć pełny raport JSON uruchom:")
    print("  python scripts/audit_press_articles.py --json > audit_report.json")


if __name__ == "__main__":
    main()
