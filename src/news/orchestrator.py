#!/usr/bin/env python3
"""
Prasówka SpendGuru — Orchestrator

Użycie:
  python src/news/orchestrator.py run --brief food_press [--dry-run] [--verbose]
  python src/news/orchestrator.py backfill-db --brief food_press [--verbose]

Tryby:
  run           Pełny pipeline: pobierz → filtruj → klasyfikuj → wyślij mail
  run --dry-run Nie wysyłaj maila, zapisz podgląd HTML w outputs/news/
  backfill-db   Wczytaj artykuły z data/articles.json i zapisz do Postgres
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Path setup — must happen before any local imports
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))   # src/news/
_SRC_DIR = os.path.dirname(_THIS_DIR)                     # src/
_ROOT_DIR = os.path.dirname(_SRC_DIR)                     # Prasówki SpendGuru/

for _p in [_SRC_DIR, _ROOT_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

import yaml  # noqa: E402 (after sys.path setup)
from news.sources import get_article_urls        # noqa: E402
from news.scraper import fetch_article           # noqa: E402
from news.classifier import classify_article     # noqa: E402
from news.storage import NewsStorage             # noqa: E402
# email_sender imported lazily only when actually sending (avoids msal dependency in CI)

log = logging.getLogger("prasowka.orchestrator")


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def _load_config(brief_name: str) -> dict:
    path = os.path.join(_ROOT_DIR, "config", f"{brief_name}.yaml")
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"Nie znaleziono konfiguracji dla prasówki '{brief_name}': {path}\n"
            f"Dostępne pliki: {os.listdir(os.path.join(_ROOT_DIR, 'config'))}"
        )
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# HTML email builder
# ---------------------------------------------------------------------------

def _build_html(articles: list[dict], display_name: str, date_str: str) -> str:
    items_html = ""
    for art in articles:
        cls = art.get("classification", {})
        items_html += f"""
    <div style="border:1px solid #e0e0e0;border-radius:6px;padding:16px 20px;
                margin-bottom:20px;background:#fafafa;">
      <h3 style="margin:0 0 4px 0;font-size:15px;line-height:1.4;">
        <a href="{art['url']}" style="color:#1a73e8;text-decoration:none;">
          {art.get('title') or art['url']}
        </a>
      </h3>
      <p style="margin:2px 0 10px 0;font-size:12px;color:#777;">
        Źródło: <strong>{art.get('source_name','')}</strong> &nbsp;|&nbsp;
        <a href="{art['url']}" style="color:#777;">{art['url']}</a>
      </p>
      <table style="font-size:13px;border-collapse:collapse;width:100%;">
        <tr>
          <td style="color:#555;font-weight:600;padding:2px 12px 2px 0;white-space:nowrap;
                     vertical-align:top;width:120px;">Firma</td>
          <td style="padding:2px 0;">{cls.get('company') or '—'}</td>
        </tr>
        <tr>
          <td style="color:#555;font-weight:600;padding:2px 12px 2px 0;white-space:nowrap;
                     vertical-align:top;">Osoba Tier 1</td>
          <td style="padding:2px 0;">{cls.get('person') or '—'}</td>
        </tr>
        <tr>
          <td style="color:#555;font-weight:600;padding:2px 12px 2px 0;white-space:nowrap;
                     vertical-align:top;">Stanowisko</td>
          <td style="padding:2px 0;">{cls.get('role') or '—'}</td>
        </tr>
        <tr>
          <td style="color:#555;font-weight:600;padding:2px 12px 2px 0;white-space:nowrap;
                     vertical-align:top;">Powód</td>
          <td style="padding:2px 0;">{cls.get('reason') or '—'}</td>
        </tr>
        <tr>
          <td style="color:#555;font-weight:600;padding:2px 12px 2px 0;white-space:nowrap;
                     vertical-align:top;">Kontekst</td>
          <td style="padding:2px 0;">{cls.get('outbound_context') or '—'}</td>
        </tr>
      </table>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{display_name} - {date_str}</title>
</head>
<body style="font-family:Arial,Helvetica,sans-serif;max-width:720px;margin:0 auto;
             padding:24px;color:#333;background:#fff;">
  <h1 style="font-size:22px;border-bottom:2px solid #1a73e8;padding-bottom:10px;
             margin-bottom:6px;">
    {display_name} - {date_str}
  </h1>
  <p style="color:#555;margin-bottom:24px;">
    Liczba zakwalifikowanych artykułów: <strong>{len(articles)}</strong>
  </p>
  {items_html}
  <hr style="border:none;border-top:1px solid #e0e0e0;margin-top:32px;">
  <p style="font-size:11px;color:#aaa;margin-top:8px;">
    Wygenerowano automatycznie przez system Prasówki SpendGuru.
  </p>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Site data export (data/articles.json)
# ---------------------------------------------------------------------------

def _make_article_id(source_name: str, url: str) -> str:
    """Generate a stable, readable article ID."""
    slug = re.sub(r'[^a-z0-9]+', '', source_name.lower())[:20]
    url_hash = hashlib.md5(url.encode()).hexdigest()[:10]
    return f"{slug}-{url_hash}"


def _map_to_site_article(
    article: dict,
    cls: dict,
    brief_name: str,
    industry: str,
    now: datetime,
) -> dict:
    """Map pipeline article + classification to data/articles.json schema."""
    url = article.get("url") or article.get("source_url") or ""
    source_name = article.get("source_name") or ""
    return {
        "id":             _make_article_id(source_name, url),
        "industry":       industry,
        "press_type":     brief_name,
        "article_date":   article.get("date") or "",
        "title":          article.get("title") or "",
        "source_name":    source_name,
        "source_url":     url,
        "company":        cls.get("company") or "",
        "tier1_person":   cls.get("person") or "",
        "tier1_position": cls.get("role") or "",
        "tier2_person":   "",
        "tier2_position": "",
        "reason":         cls.get("reason") or "",
        "context":        cls.get("outbound_context") or "",
        "contact_email":  "",
        "status":         "new",
        "created_at":     now.isoformat(),
        "updated_at":     now.isoformat(),
    }


def _export_site_data(
    qualified: list[dict],
    brief_name: str,
    industry: str,
    now: datetime,
    target_path: str,
) -> int:
    """
    Merge qualified articles into target_path (JSON array).
    Deduplicates by source_url and id.
    Returns the count of newly added articles.
    """
    # Load existing data
    existing: list[dict] = []
    if os.path.exists(target_path):
        try:
            with open(target_path, encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        except (json.JSONDecodeError, OSError):
            existing = []

    existing_urls = {a.get("source_url") for a in existing if a.get("source_url")}
    existing_ids  = {a.get("id")         for a in existing if a.get("id")}

    added = 0
    for article in qualified:
        cls = article.get("classification", {})
        record = _map_to_site_article(article, cls, brief_name, industry, now)

        if record["source_url"] in existing_urls or record["id"] in existing_ids:
            log.debug("SKIP (already in articles.json): %s", record["source_url"])
            continue

        existing.append(record)
        existing_urls.add(record["source_url"])
        existing_ids.add(record["id"])
        added += 1

    with open(target_path, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)

    return added


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_brief(
    brief_name: str,
    dry_run: bool,
    verbose: bool,
    export_site_data: bool = False,
    dry_run_export_preview_json: bool = False,
    save_to_db: bool = False,
    skip_email: bool = False,
    reprocess_seen: bool = False,
) -> None:
    cfg = _load_config(brief_name)

    display_name = cfg.get("display_name", brief_name)
    recipient = cfg["recipient_email"]
    subject_base = cfg.get("subject", display_name)
    max_per_source = cfg.get("max_articles_per_source", 30)
    criteria = cfg.get("criteria", {})
    industry = cfg.get("industry", brief_name)

    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d.%m.%Y")
    subject = f"{subject_base} - {date_str}"

    db_path = os.path.join(_ROOT_DIR, "data", "news_seen.sqlite")
    storage = NewsStorage(db_path)

    outputs_dir = os.path.join(_ROOT_DIR, "outputs", "news")
    os.makedirs(outputs_dir, exist_ok=True)

    total_found = 0
    total_new = 0
    qualified: list[dict] = []

    for source_cfg in cfg.get("sources", []):
        source_name = source_cfg.get("name", source_cfg["url"])
        log.info("Pobieranie artykułów z: %s", source_name)

        article_refs = get_article_urls(source_cfg, max_articles=max_per_source)
        total_found += len(article_refs)
        log.info("  Znaleziono: %d artykułów", len(article_refs))

        for ref in article_refs:
            url = (ref.get("url") or "").strip()
            if not url:
                continue

            if storage.is_seen(url, brief_name):
                if reprocess_seen:
                    log.debug("  REPROCESS (already seen): %s", url)
                else:
                    log.debug("  SKIP (already seen): %s", url)
                    continue

            total_new += 1
            log.info("  Pobieranie treści: %s", url)
            article = fetch_article(url)
            article["source_name"] = source_name
            if not article.get("title") and ref.get("title"):
                article["title"] = ref["title"]

            cls = classify_article(article, criteria)
            log.info(
                "  Klasyfikacja: qualified=%s | %s",
                cls["qualified"],
                cls["reason"][:80],
            )

            if cls["qualified"]:
                article["classification"] = cls

                # Zapisz do DB PRZED oznaczeniem jako seen, żeby nie stracić artykułu
                db_upsert_ok = True
                if save_to_db and not dry_run:
                    try:
                        from news.press_db import upsert_press_article as _db_one  # noqa: E402
                        site_rec = {
                            **_map_to_site_article(article, cls, brief_name, industry, now),
                            "raw_payload": {k: v for k, v in article.items() if k != "text"},
                        }
                        _db_one(site_rec)
                    except Exception as exc:
                        log.error(
                            "  [DB] Błąd upsert dla %s: %s — artykuł NIE zostanie oznaczony jako seen",
                            url, exc,
                        )
                        db_upsert_ok = False

                if not dry_run and db_upsert_ok:
                    storage.mark_seen(url, brief_name, qualified=True)

                qualified.append(article)
            else:
                # Niekwalifikowany — oznacz jako seen bez warunku DB
                if not dry_run:
                    storage.mark_seen(url, brief_name, qualified=False)

    storage.close()

    # --- Summary log ---
    log.info(
        "\nPodsumowanie: znaleziono=%d | nowych=%d | zakwalifikowanych=%d",
        total_found, total_new, len(qualified),
    )

    log_path = os.path.join(outputs_dir, f"{brief_name}_{now.strftime('%Y-%m-%d')}.log")
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(
            f"{now.isoformat()} | total={total_found} | new={total_new} "
            f"| qualified={len(qualified)} | dry_run={dry_run}\n"
        )

    if not qualified:
        log.info("Brak nowych artykułów spełniających kryteria. Mail nie zostanie wysłany.")
        log.info("Log: %s", log_path)
        return

    # --- Build HTML ---
    html = _build_html(qualified, display_name, date_str)

    # --- Save preview ---
    preview_path = os.path.join(
        outputs_dir, f"{brief_name}_{now.strftime('%Y-%m-%d')}_preview.html"
    )
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    log.info("Podgląd HTML: %s", preview_path)

    # --- Save to Postgres (batch dla artykułów nie zapisanych wyżej per-artykuł) ---
    # Per-article upsert jest już wyżej (z blokiem mark_seen).
    # Ten blok pozostaje jako fallback gdy save_to_db=True ale per-article path nie załadował się
    # (np. dry_run=False ale wyżej nie było artykułów do zapisania batchą).
    # W praktyce dla dry_run=False zakwalifikowane są już zapisane wyżej per-artykuł.
    if save_to_db and dry_run:
        # dry_run: nie zapisujemy do DB
        log.info("[DB] Pominięto zapis do DB (dry-run)")

    # --- Export to site data (data/articles.json) ---
    if export_site_data and not dry_run:
        site_data_path = os.path.join(_ROOT_DIR, "data", "articles.json")
        added = _export_site_data(qualified, brief_name, industry, now, site_data_path)
        log.info("[EXPORT] Dodano %d nowych artykułów do %s", added, site_data_path)
    elif dry_run and dry_run_export_preview_json:
        preview_json_path = os.path.join(
            outputs_dir, f"{brief_name}_{now.strftime('%Y-%m-%d')}_preview_articles.json"
        )
        added = _export_site_data(qualified, brief_name, industry, now, preview_json_path)
        log.info(
            "[DRY-RUN PREVIEW JSON] Zapisano %d artykułów do %s",
            added, preview_json_path,
        )

    # --- Send or skip ---
    if dry_run or skip_email:
        reason = "DRY-RUN" if dry_run else "SKIP-EMAIL"
        log.info("[%s] Mail NIE wysłany. Podgląd zapisany: %s", reason, preview_path)
    else:
        log.info("Wysyłam mail do: %s (temat: %s)", recipient, subject)
        from news.email_sender import send_email  # lazy import — msal needed only here
        send_email(recipient, subject, html)


# ---------------------------------------------------------------------------
# Backfill DB — wczytaj data/articles.json → upsert do Postgres
# ---------------------------------------------------------------------------

def backfill_db(brief_name: str, verbose: bool) -> None:
    """
    Wczytuje artykuły z data/articles.json i upsertuje je do apollo.press_articles.

    Nie pobiera nowych artykułów, nie klasyfikuje, nie wysyła maila.
    Nie patrzy na SQLite seen.
    Operacja idempotentna — bezpieczna do wielokrotnego uruchomienia.
    """
    from news.press_db import upsert_press_articles as _db_upsert, ensure_press_tables  # noqa: E402

    site_data_path = os.path.join(_ROOT_DIR, "data", "articles.json")
    if not os.path.exists(site_data_path):
        log.error("Brak pliku: %s", site_data_path)
        log.error("Uruchom najpierw pipeline z --export-site-data, aby wypełnić plik.")
        return

    try:
        with open(site_data_path, encoding="utf-8") as f:
            articles: list[dict] = json.load(f)
        if not isinstance(articles, list):
            raise ValueError("Plik articles.json nie jest tablicą JSON")
    except (json.JSONDecodeError, ValueError, OSError) as exc:
        log.error("Nie można wczytać %s: %s", site_data_path, exc)
        return

    log.info("[BACKFILL] Wczytano %d artykułów z %s", len(articles), site_data_path)

    if not articles:
        log.info("[BACKFILL] Brak artykułów do zapisania.")
        return

    # Upewnij się że tabela istnieje
    try:
        ensure_press_tables()
    except Exception as exc:
        log.error("[BACKFILL] Nie można połączyć z bazą / stworzyć tabeli: %s", exc)
        return

    # Upsert
    try:
        saved = _db_upsert(articles)
        log.info("[BACKFILL] Upsert zakończony: zapisano %d / %d artykułów", saved, len(articles))
    except Exception as exc:
        log.error("[BACKFILL] Błąd podczas upsert: %s", exc)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prasówka SpendGuru — orkiestrator dzienny",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Przykłady:
  python src/news/orchestrator.py run --brief food_press --verbose
  python src/news/orchestrator.py run --brief food_press --dry-run --verbose
  python src/news/orchestrator.py run --brief food_press --save-to-db --skip-email --verbose
  python src/news/orchestrator.py run --brief food_press --save-to-db --skip-email --reprocess-seen --verbose
  python src/news/orchestrator.py backfill-db --brief food_press --verbose
""",
    )

    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")

    run_p = subparsers.add_parser("run", help="Uruchom prasówkę")
    run_p.add_argument("--brief", required=True, metavar="NAME",
                       help="Nazwa prasówki, np. food_press")
    run_p.add_argument("--dry-run", action="store_true",
                       help="Nie wysyłaj maila, wygeneruj tylko podgląd HTML")
    run_p.add_argument("--export-site-data", action="store_true",
                       help="Dopisz zakwalifikowane artykuły do data/articles.json "
                            "(nie działa z --dry-run; użyj --dry-run-export-preview-json)")
    run_p.add_argument("--dry-run-export-preview-json", action="store_true",
                       help="W trybie --dry-run zapisz artykuły do outputs/news/*_preview_articles.json")
    run_p.add_argument("--save-to-db", action="store_true",
                       help="Zapisz zakwalifikowane artykuły do bazy Postgres (wymaga DATABASE_URL)")
    run_p.add_argument("--skip-email", action="store_true",
                       help="Nie wysyłaj maila (np. w trybie CI/CD)")
    run_p.add_argument("--reprocess-seen", action="store_true",
                       help="Pomija filtr SQLite 'seen' — ponownie przetwarza już widziane URL-e "
                            "(przydatne gdy poprzedni run skończył się błędem DB)")
    run_p.add_argument("--verbose", action="store_true",
                       help="Szczegółowe logi (DEBUG)")

    backfill_p = subparsers.add_parser(
        "backfill-db",
        help="Wczytaj data/articles.json i upsertuj do Postgres (bez pobierania artykułów)",
    )
    backfill_p.add_argument("--brief", required=True, metavar="NAME",
                             help="Nazwa prasówki (używana tylko do logowania)")
    backfill_p.add_argument("--verbose", action="store_true",
                             help="Szczegółowe logi (DEBUG)")

    args = parser.parse_args()

    if args.command == "run":
        _setup_logging(args.verbose)
        run_brief(
            args.brief,
            dry_run=args.dry_run,
            verbose=args.verbose,
            export_site_data=args.export_site_data,
            dry_run_export_preview_json=args.dry_run_export_preview_json,
            save_to_db=args.save_to_db,
            skip_email=args.skip_email,
            reprocess_seen=args.reprocess_seen,
        )
    elif args.command == "backfill-db":
        _setup_logging(args.verbose)
        backfill_db(args.brief, args.verbose)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
