#!/usr/bin/env python3
"""
scripts/mark_bad_press_articles.py
Oznacza podejrzane rekordy w apollo.press_articles jako needs_review.
NIE usuwa rekordów ani nie zmienia emaili/statusów Apollo.

Użycie:
    cd "Prasówki SpendGuru"
    set -a; source .env; set +a
    python scripts/mark_bad_press_articles.py [--dry-run] [--verbose]

Opcje:
    --dry-run    Pokaż co zostałoby oznaczone, ale nic nie zmieniaj.
    --verbose    Wypisz szczegóły każdego zmienionego rekordu.
    --force      Oznacz też rekordy z data_quality_status = 'ok'.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).parent
_ROOT = _THIS.parent
_SRC  = _ROOT / "src"
for p in [str(_SRC), str(_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

# Reuse audit heuristics
from audit_press_articles import audit_row  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Oznacz podejrzane rekordy jako needs_review (nie usuwa danych)"
    )
    parser.add_argument("--dry-run",  action="store_true", help="Symuluj, nie zapisuj")
    parser.add_argument("--verbose",  action="store_true", help="Wypisz szczegóły każdego rekordu")
    parser.add_argument("--force",    action="store_true",
                        help="Oznacz nawet rekordy o data_quality_status = 'ok'")
    args = parser.parse_args()

    from news.press_db import get_connection
    import psycopg.rows  # type: ignore

    print("Łączenie z bazą danych...")
    with get_connection() as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute("""
                SELECT
                    id, article_id, article_url, article_title,
                    source_name, company_name,
                    tier1_person, tier1_position,
                    tier2_person, tier2_position,
                    apollo_status,
                    COALESCE(data_quality_status, 'unknown') AS data_quality_status
                FROM apollo.press_articles
                ORDER BY created_at DESC
            """)
            rows = cur.fetchall()

    print(f"Pobrano {len(rows)} rekordów.\n")

    to_mark: list[dict] = []
    for row in rows:
        r = dict(row)
        current_dqs = r.get("data_quality_status", "unknown")

        # Pomijaj już zrecenzowane rekordy (chyba że --force)
        if current_dqs in ("ok", "rejected") and not args.force:
            continue

        result = audit_row(r)
        if result["severity"] in ("warn", "critical"):
            to_mark.append({
                "id":      r["id"],
                "url":     r.get("article_url", ""),
                "title":   r.get("article_title", ""),
                "company": r.get("company_name", ""),
                "person":  r.get("tier1_person", ""),
                "issues":  result["issues"],
                "severity": result["severity"],
                "current_dqs": current_dqs,
            })

    if not to_mark:
        print("Brak rekordów do oznaczenia. Dane wyglądają poprawnie.")
        return

    print(f"Rekordy do oznaczenia jako 'needs_review': {len(to_mark)}")
    print()

    if args.verbose or args.dry_run:
        for rec in to_mark:
            sev = "🔴" if rec["severity"] == "critical" else "🟡"
            print(f"  {sev} ID={rec['id']} | {rec['company'] or '(brak firmy)'}")
            print(f"     Tytuł : {rec['title'][:70]}")
            print(f"     Osoba : {rec['person']}")
            print(f"     Błędy :")
            for iss in rec["issues"]:
                print(f"       - {iss}")
            print()

    if args.dry_run:
        print(f"[DRY-RUN] Nie wprowadzono zmian. {len(to_mark)} rekordów ZOSTAŁOBY oznaczonych.")
        return

    # Generuj notes (pierwsze 3 błędy)
    now = datetime.now(timezone.utc)
    ids_and_notes = [
        (
            rec["id"],
            "; ".join(rec["issues"][:3]),
            now,
        )
        for rec in to_mark
    ]

    print("Zapisywanie zmian...")
    updated = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            for rec_id, notes, ts in ids_and_notes:
                cur.execute("""
                    UPDATE apollo.press_articles
                    SET data_quality_status = 'needs_review',
                        data_quality_notes  = %(notes)s,
                        updated_at          = %(ts)s
                    WHERE id = %(id)s
                      AND data_quality_status NOT IN ('ok', 'rejected')
                """, {"id": rec_id, "notes": notes, "ts": ts})
                updated += cur.rowcount
        conn.commit()

    print(f"Oznaczono {updated} rekordów jako 'needs_review'.")
    print()
    print("Kolejne kroki:")
    print("  1. Sprawdź rekordy ręcznie w UI (filtr 'Do weryfikacji')")
    print("  2. Oznacz poprawne rekordy: scripts/cleanup_bad_press_articles.py --apply --mark-ok <id>")
    print("  3. Odrzuć złe rekordy: scripts/cleanup_bad_press_articles.py --apply --mark-rejected <id>")


if __name__ == "__main__":
    main()
