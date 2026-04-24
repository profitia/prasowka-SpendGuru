# Prasówka SpendGuru — system codziennych prasówek branżowych

System automatycznie pobiera i filtruje artykuły z wybranych serwisów, klasyfikuje je pod kątem branży FMCG i obecności osób zarządczych (Tier 1), a następnie wysyła codzienny mail z podsumowaniem.

Artykuły zakwalifikowane mogą być eksportowane do `data/articles.json` i prezentowane na interaktywnej stronie WWW (`index.html`) z filtrowaniem, ciemnym trybem i generatorem komend do kampanii outbound.

---

## Strona WWW — podgląd artykułów prasówki

### Uruchomienie lokalne

```bash
cd "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru"

# Python (najprostsze)
python -m http.server 8080
# → otwórz: http://localhost:8080

# Alternatywnie: npx serve (jeśli masz Node.js)
npx serve .
```

Strona ładuje dane z `data/articles.json`. Nie wymaga budowania ani Node.js.

### Jak dopisać artykuły z prasówki do data/articles.json

Używaj flagi `--export-site-data` przy uruchomieniu pełnego pipeline (nie dry-run):

```bash
python src/news/orchestrator.py run --brief food_press --export-site-data --verbose
```

Zasady eksportu:
- Bez `--export-site-data` — flow działa jak dotychczas (brak zmian w articles.json)
- Z `--export-site-data` — zakwalifikowane artykuły są dopisywane do `data/articles.json`
- Duplikaty (po `source_url` i `id`) są automatycznie pomijane
- W trybie `--dry-run` nie dopisuje do `data/articles.json` — użyj flagi `--dry-run-export-preview-json` aby zapisać podgląd w `outputs/news/`

```bash
# Dry-run z podglądem JSON (nie modyfikuje data/articles.json)
python src/news/orchestrator.py run --brief food_press --dry-run --dry-run-export-preview-json --verbose
```

### Deploy na GitHub Pages

```bash
# W folderze Prasówki SpendGuru (jest to root repozytorium)
git add .
git commit -m "feat: update articles"
git push origin main

# W ustawieniach repozytorium na GitHub:
# Settings → Pages → Source: "Deploy from branch" → main → / (root)
# Strona będzie dostępna pod: https://profitia.github.io/prasowka-SpendGuru/
```

### Deploy na Vercel

```bash
# Zainstaluj Vercel CLI (jednorazowo)
npm i -g vercel

# Z folderu Prasówki SpendGuru
vercel

# Vercel wykryje statyczny projekt i wdroży index.html automatycznie
```

### Jak działa dark mode

Przełącznik w prawym górnym rogu strony. Wybór jest zapamiętywany w `localStorage` (klucz: `prasowka_darkmode`). Domyślnie tryb jasny.

### Jak działa eksport CSV

Przycisk "Eksport CSV" eksportuje aktualnie przefiltrowane artykuły. CSV zawiera:
`article_date, title, source_name, source_url, company, tier, full_name, job_title, email, industry, status, reason, context`

Jeśli wpisano adresy email na kartach artykułów — są one uwzględniane w eksporcie (pobierane z `localStorage`).

### Jak dodać kolejną prasówkę branżową

1. Dodaj plik konfiguracyjny:

```bash
cp config/food_press.yaml config/industrial.yaml
```

2. Edytuj `config/industrial.yaml` — zmień `brief_name`, `display_name`, `industry` (np. `"przemysłowa"`), `recipient_email`, `subject`, `sources`.

3. Uruchom i eksportuj:

```bash
python src/news/orchestrator.py run --brief industrial --export-site-data --verbose
```

4. Nowe artykuły z nową branżą automatycznie pojawią się na stronie. Filtr "Branża" jest budowany dynamicznie z danych w `data/articles.json`.

---

---

## Konfiguracja bazy danych (Postgres/Neon)

### Plik .env

Stwórz plik `.env` w folderze `Prasówki SpendGuru/`:

```bash
# .env — NIE commituj tego pliku (jest w .gitignore)
DATABASE_URL="postgresql://USER:PASSWORD@HOST/neondb?sslmode=require&channel_binding=require"
```

Przykład dla Neon:
```
DATABASE_URL="postgresql://tomasz:abc123@ep-cool-name-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
```

> **Uwaga:** Cudzysłowy wokół wartości są opcjonalne, ale zalecane gdy connection string zawiera `&`. Plik `.env` jest wczytywany przez `set -a; source .env; set +a` lub przez `python-dotenv`.

### Wczytanie .env lokalnie

```bash
cd "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru"
source ../.venv/bin/activate
set -a; source .env; set +a

# Test połączenia
python -c "from src.news.press_db import ensure_press_tables; ensure_press_tables(); print('OK')"
```

### Backfill — zapisz istniejące artykuły do DB

Jeśli masz już artykuły w `data/articles.json` i chcesz je załadować do bazy:

```bash
python src/news/orchestrator.py backfill-db --brief food_press --verbose
```

Operacja idempotentna — bezpieczna do wielokrotnego uruchomienia. Nie duplikuje artykułów.

### Odzyskiwanie po błędzie DATABASE_URL

Jeśli poprzedni run skończył się błędem DB (artykuły nie trafiły do bazy, ale SQLite je "widział"):

```bash
# Opcja 1: backfill z articles.json (jeśli --export-site-data był użyty)
python src/news/orchestrator.py backfill-db --brief food_press --verbose

# Opcja 2: ponowne pobranie i zapis z pominięciem filtra "seen"
python src/news/orchestrator.py run \
  --brief food_press \
  --save-to-db --skip-email --reprocess-seen --verbose
```

### GitHub Actions — sekret DATABASE_URL

Dodaj sekret w repozytorium:
`Settings → Secrets and variables → Actions → New repository secret`

| Nazwa | Wartość |
|-------|---------|
| `DATABASE_URL` | `postgresql://USER:PASS@HOST/neondb?sslmode=require&channel_binding=require` |

---

## Uruchamianie ręczne

Wszystkie komendy uruchamiane z katalogu **`Prasówki SpendGuru/`**:

```bash
# Przejdź do katalogu
cd "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru"

# Aktywuj venv nadrzędny
source ../.venv/bin/activate

# Dry-run (podgląd HTML, bez wysyłki maila)
python src/news/orchestrator.py run --brief food_press --dry-run --verbose

# Pełny run (pobiera artykuły, klasyfikuje, wysyła mail jeśli są wyniki)
python src/news/orchestrator.py run --brief food_press --verbose
```

Podgląd HTML ląduje w: `outputs/news/food_press_YYYY-MM-DD_preview.html`

---

## Jak działa konfiguracja

Każda prasówka to jeden plik YAML w `config/`:

```
config/
  food_press.yaml      ← Prasówka spożywcza
  industrial.yaml      ← (przykład następnej prasówki)
```

Struktura pliku konfiguracyjnego:

```yaml
brief_name: food_press
display_name: "Prasówka spożywcza"
recipient_email: tomasz.uscinski@profitia.pl
subject: "Prasówka spożywcza"
max_articles_per_source: 30

sources:
  - name: "Wiadomości Handlowe"
    url: "https://www.wiadomoscihandlowe.pl"
    rss_paths:
      - /feed
      - /rss.xml

criteria:
  require_fmcg: true
  require_tier1_person: true
  tier1_roles:
    - "CEO"
    - "prezes zarządu"
    # ...
```

---

## Jak dodać nowe źródło

Edytuj sekcję `sources:` w `config/food_press.yaml` (lub innej prasówki):

```yaml
sources:
  - name: "Nowe Źródło"
    url: "https://www.nowemedium.pl"
    rss_paths:
      - /feed
      - /rss.xml
```

System najpierw próbuje RSS z podanych ścieżek. Jeśli RSS nie zadziała, automatycznie przełączy się na scraping HTML strony głównej.

---

## Jak dodać kolejną prasówkę (np. przemysłową)

1. Stwórz nowy plik konfiguracyjny:

```bash
cp config/food_press.yaml config/industrial.yaml
```

2. Edytuj `config/industrial.yaml` — zmień `brief_name`, `display_name`, `recipient_email`, `subject` i `sources`.

3. Uruchom:

```bash
python src/news/orchestrator.py run --brief industrial --dry-run --verbose
```

Każda prasówka ma własny zestaw śledzonych URL-i w SQLite (`data/news_seen.sqlite`) — kolumna `brief` rozdziela konteksty.

---

## Architektura

```
src/news/
  orchestrator.py   ← główny punkt wejścia CLI
  sources.py        ← odkrywanie artykułów (RSS + HTML fallback)
  scraper.py        ← pobieranie treści artykułu
  classifier.py     ← klasyfikacja (heurystyki + opcjonalnie LLM)
  email_sender.py   ← wysyłka przez Office365 Graph API
  storage.py        ← SQLite (śledzenie już widzianych URL-i)

config/
  food_press.yaml   ← konfiguracja prasówki spożywczej

data/
  news_seen.sqlite  ← baza widzianych artykułów (tworzona automatycznie)

outputs/
  news/
    food_press_YYYY-MM-DD.log           ← log każdego uruchomienia
    food_press_YYYY-MM-DD_preview.html  ← podgląd HTML maila
```

---

## Konfiguracja wysyłki maila

System używa istniejącej integracji Office365 z katalogu nadrzędnego:
`../Integracja z Office365/.env`

Przy pierwszym uruchomieniu systemu z wysyłką (bez `--dry-run`) może być wymagane logowanie device flow (jednorazowo w przeglądarce). Token jest następnie buforowany w `.token_cache.json`.

---

## Cron — automatyczne uruchamianie (macOS)

### Opcja A: crontab (prosta)

```bash
crontab -e
```

Dodaj linię (codziennie o 7:30):

```cron
30 7 * * * /bin/bash -c 'cd "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru" && source ../.venv/bin/activate && python src/news/orchestrator.py run --brief food_press --verbose >> outputs/news/cron.log 2>&1'
```

Lub prościej — bezpośrednio z venv (bez `source`):

```cron
30 7 * * * "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/.venv/bin/python" "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru/src/news/orchestrator.py" run --brief food_press --verbose >> "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru/outputs/news/cron.log" 2>&1
```

### Opcja B: launchd (zalecana na macOS)

Utwórz plik `~/Library/LaunchAgents/pl.profitia.prasowka.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>pl.profitia.prasowka</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/.venv/bin/python</string>
    <string>/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru/src/news/orchestrator.py</string>
    <string>run</string>
    <string>--brief</string>
    <string>food_press</string>
    <string>--verbose</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>7</integer>
    <key>Minute</key>
    <integer>30</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru/outputs/news/launchd.log</string>
  <key>StandardErrorPath</key>
  <string>/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo/Prasówki SpendGuru/outputs/news/launchd.log</string>
  <key>RunAtLoad</key>
  <false/>
</dict>
</plist>
```

Załaduj:

```bash
launchctl load ~/Library/LaunchAgents/pl.profitia.prasowka.plist
```

---

## Dodawanie nowej zależności

Zależności specificzne dla systemu prasówek są w `requirements-news.txt`.
Instalacja (z aktywnym venv):

```bash
cd "/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo"
source .venv/bin/activate
pip install -r "Prasówki SpendGuru/requirements-news.txt"
```
