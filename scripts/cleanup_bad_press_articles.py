#!/usr/bin/env python3
"""
scripts/cleanup_bad_press_articles.py
Opcjonalny cleanup — przegląda rekordy needs_review i pozwala je
oznaczyć jako 'ok' lub 'rejected'. NIE usuwa rekordów automatycznie.

Użycie:
    cd "Prasówki SpendGuru"
    set -a; source .env; set +a

    # Pokaż co zostałoby oznaczone bez zmian (domyślnie)
    python scripts/cleanup_bad_press_articles.py --dry-run

    # Oznacz konkretny rekord jako ok
    python scripts/cleanup_bad_press_articles.py --apply --mark-ok 42

    # Oznacz konkretny rekord jako rejected
    python scripts/cleanup_bad_press_articles.py --apply --mark-rejected 42

    # Automatycznie oznacz wszystkie krytyczne jako rejected (OSTROŻNIE)
    python scripts/cleanup_bad_press_articles.py --apply --auto-reject-critical

    # Pokaż podsumowanie jakości danych
    python scripts/cleanup_bad_press_articles.py --summary
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

_THIS = Path(__file__).parent
_ROOT = _THIS.parent
_SRC  = _ROOT / "src"
for p in [str(_SRC), str(_ROOT), str(_THIS)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from audit_press_articles import audit_row  # type: ignore


def _load_rows(conn) -> list[dict]:
    import psycopg.rows  # type: ignore
    with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
        cur.execute("""
            SELECT
                id, article_url, article_title, source_name, company_name,
                tier1_person, tier1_position,
                tier2_person, tier2_position,
                apollo_status, tier1_email, tier2_email,
                COALESCE(data_quality_status, 'unknown') AS data_quality_status,
                data_quality_notes
            FROM apollo.press_articles
            ORDER BY created_at DESC
        """)
        return [dict(r) for r in cur.fetchall()]


def _update_status(conn, rec_id: int, new_status: str, notes: str | None = None) -> bool:
    now = datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute("""
            UPDATE apollo.press_articles
            SET data_quality_status = %(status)s,
                data_quality_notes  = COALESCE(%(notes)s, data_quality_notes),
                reviewed_at         = %(ts)s,
                updated_at          = %(ts)s
            WHERE id = %(id)s
        """, {"id": rec_id, "status": new_status, "notes": notes, "ts": now})
        return cur.rowcount > 0


def cmd_summary(rows: list[dict]) -> None:
    dqs = Counter(r["data_quality_status"] for r in rows)
    apollo = Counter(r["apollo_status"] for r in rows)

    print("=" * 60)
    print("PODSUMOWANIE JAKOŚCI DANYCH")
    print("=" * 60)
    print(f"  Łącznie rekordów: {len(rows)}")
    print()
    print("  data_quality_status:")
    for status in ("ok", "unknown", "needs_review", "rejected"):
        print(f"    {status:20s}: {dqs.get(status, 0)}")
    print()
    print("  apollo_status:")
    for status in ("waiting", "running", "sent"):
        print(f"    {status:20s}: {apollo.get(status, 0)}")
    print()

    # Ostrzeżenie: sent rekordy ze złą jakością
    sent_bad = [
        r for r in rows
        if r["apollo_status"] == "sent" and r["data_quality_status"] in ("needs_review", "rejected")
    ]
    if sent_bad:
        print(f"  ⚠️  UWAGA: {len(sent_bad)} rekordów ze statusem 'sent' ma złą jakość danych!")
        for r in sent_bad[:5]:
            print(f"     ID={r['id']} | {r['company_name']} | {r['data_quality_status']}")


def cmd_dry_run(rows: list[dict]) -> None:
    print("=" * 60)
    print("DRY-RUN: co zostałoby zmienione")
    print("=" * 60)

    sev_order = {"critical": 0, "warn": 1, "ok": 2}
    results = []
    for r in rows:
        if r["data_quality_status"] == "ok":
            continue
        res = audit_row(r)
        if res["severity"] != "ok":
            results.append((r, res))

    results.sort(key=lambda x: sev_order[x[1]["severity"]])

    would_reject = [r for r, res in results if res["severity"] == "critical"]
    would_review = [r for r, res in results if res["severity"] == "warn"]
    would_keep   = [r for r in rows if r["data_quality_status"] == "ok"]

    print(f"\n  Zostałyby ODRZUCONE (critical): {len(would_reject)}")
    for r, res in [(r, res) for r, res in results if res["severity"] == "critical"][:5]:
        print(f"    ID={r['id']} | {r['company_name'][:40]} | błędy: {'; '.join(res['issues'][:2])}")

    print(f"\n  Zostałyby OZNACZONE needs_review (warn): {len(would_review)}")
    for r, res in [(r, res) for r, res in results if res["severity"] == "warn"][:5]:
        print(f"    ID={r['id']} | {r['company_name'][:40]} | błędy: {'; '.join(res['issues'][:2])}")

    print(f"\n  ZOSTAWIONE bez zmian (ok): {len(would_keep)}")

    print()
    print("Aby wprowadzić zmiany, dodaj flagę --apply.")
    print("Aby odrzucić konkretny rekord: --apply --mark-rejected <id>")
    print("Aby zaakceptować konkretny rekord: --apply --mark-ok <id>")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cleanup rekordów needs_review w apollo.press_articles"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run",  action="store_true", help="Pokaż co zostałoby zrobione (domyślnie)")
    mode.add_argument("--apply",    action="store_true", help="Zastosuj zmiany")
    mode.add_argument("--summary",  action="store_true", help="Podsumowanie jakości danych")

    parser.add_argument("--mark-ok",       type=int, metavar="ID", help="Oznacz rekord ID jako ok")
    parser.add_argument("--mark-rejected", type=int, metavar="ID", help="Oznacz rekord ID jako rejected")
    parser.add_argument("--mark-review",   type=int, metavar="ID", help="Oznacz rekord ID jako needs_review")
    parser.add_argument("--auto-reject-critical", action="store_true",
                        help="Automatycznie oznacz WSZYSTKIE krytyczne rekordy jako rejected (ostrożnie!)")
    parser.add_argument("--notes", type=str, default=None, help="Dodatkowa notatka do rekordu")
    args = parser.parse_args()

    # Domyślnie dry-run
    if not args.apply and not args.summary:
        args.dry_run = True

    from news.press_db import get_connection

    print("Łączenie z bazą danych...")
    conn = get_connection()

    try:
        rows = _load_rows(conn)
        print(f"Pobrano {len(rows)} rekordów.\n")

        if args.summary:
            cmd_summary(rows)
            return

        if args.dry_run:
            cmd_dry_run(rows)
            return

        # --apply mode
        changed = 0

        if args.mark_ok is not None:
            ok = _update_status(conn, args.mark_ok, "ok", args.notes)
            if ok:
                print(f"✔ ID={args.mark_ok} oznaczono jako 'ok'")
                changed += 1
            else:
                print(f"✗ ID={args.mark_ok} nie znaleziono")

        if args.mark_rejected is not None:
            ok = _update_status(conn, args.mark_rejected, "rejected", args.notes)
            if ok:
                print(f"✔ ID={args.mark_rejected} oznaczono jako 'rejected'")
                changed += 1
            else:
                print(f"✗ ID={args.mark_rejected} nie znaleziono")

        if args.mark_review is not None:
            ok = _update_status(conn, args.mark_review, "needs_review", args.notes)
            if ok:
                print(f"✔ ID={args.mark_review} oznaczono jako 'needs_review'")
                changed += 1
            else:
                print(f"✗ ID={args.mark_review} nie znaleziono")

        if args.auto_reject_critical:
            sev_order = {"critical": 0, "warn": 1, "ok": 2}
            critical_ids = []
            for r in rows:
                if r["data_quality_status"] in ("ok", "rejected"):
                    continue
                res = audit_row(r)
                if res["severity"] == "critical":
                    critical_ids.append((r["id"], "; ".join(res["issues"][:3])))

            print(f"Auto-reject: {len(critical_ids)} rekordów krytycznych")
            for rec_id, notes in critical_ids:
                ok = _update_status(conn, rec_id, "rejected", notes)
                if ok:
                    print(f"  ✔ ID={rec_id} → rejected")
                    changed += 1

        if changed:
            conn.commit()
            print(f"\nZapisano {changed} zmian.")
        else:
            print("\nBrak zmian do zapisania.")
            print("Użyj --mark-ok, --mark-rejected lub --auto-reject-critical z flagą --apply.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
