"""Article discovery — RSS/Atom first, HTML scraping as fallback."""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("news.sources")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
}

_DEFAULT_RSS_PATHS = [
    "/feed",
    "/feed/rss",
    "/rss.xml",
    "/rss",
    "/feed.xml",
    "/atom.xml",
    "/news/feed",
    "/wiadomosci/feed",
]


def get_article_urls(source_cfg: dict[str, Any], max_articles: int = 30) -> list[dict]:
    """Return list of {url, title, source_name} dicts for a source config entry."""
    base_url = source_cfg["url"].rstrip("/")
    source_name = source_cfg.get("name", base_url)
    rss_paths = source_cfg.get("rss_paths", _DEFAULT_RSS_PATHS)

    # --- Try each RSS/Atom path ---
    for path in rss_paths:
        rss_url = urljoin(base_url + "/", path.lstrip("/"))
        articles = _try_rss(rss_url, source_name, max_articles)
        if articles:
            log.info("[%s] RSS OK (%s) — %d artykułów", source_name, rss_url, len(articles))
            return articles

    # --- Fallback: HTML link scraping ---
    log.info("[%s] RSS niedostępny — próba HTML scraping...", source_name)
    articles = _scrape_html_links(base_url, source_name, max_articles)
    log.info("[%s] HTML scraping — %d linków", source_name, len(articles))
    return articles


# ---------------------------------------------------------------------------
# RSS/Atom helpers
# ---------------------------------------------------------------------------

def _try_rss(rss_url: str, source_name: str, max_articles: int) -> list[dict]:
    try:
        resp = requests.get(rss_url, headers=_HEADERS, timeout=10)
        if resp.status_code != 200:
            return []

        content = resp.content

        # Try feedparser first (not always installed)
        try:
            import feedparser  # type: ignore
            feed = feedparser.parse(content)
            if feed.entries:
                return [
                    {"url": e.get("link", ""), "title": e.get("title", ""), "source_name": source_name}
                    for e in feed.entries[:max_articles]
                    if e.get("link")
                ]
        except ImportError:
            pass

        # Fallback: parse XML with BeautifulSoup
        soup = BeautifulSoup(content, "xml")
        items = soup.find_all("item") or soup.find_all("entry")
        if not items:
            return []

        results = []
        for item in items[:max_articles]:
            link_tag = item.find("link")
            title_tag = item.find("title")
            url_val = ""
            if link_tag:
                url_val = link_tag.get("href") or link_tag.get_text(strip=True)
            title_val = title_tag.get_text(strip=True) if title_tag else ""
            if url_val:
                results.append({"url": url_val.strip(), "title": title_val, "source_name": source_name})
        return results

    except Exception as exc:
        log.debug("RSS error %s: %s", rss_url, exc)
        return []


# ---------------------------------------------------------------------------
# HTML link scraping fallback
# ---------------------------------------------------------------------------

def _scrape_html_links(base_url: str, source_name: str, max_articles: int) -> list[dict]:
    try:
        resp = requests.get(base_url, headers=_HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        base_domain = base_url.split("//")[-1].split("/")[0]
        seen_urls: set[str] = set()
        results: list[dict] = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"].strip()
            if not href:
                continue

            # Make absolute
            if href.startswith("http"):
                full_url = href
            elif href.startswith("/"):
                full_url = base_url + href
            else:
                continue

            # Only same-domain links
            if base_domain not in full_url:
                continue

            # Skip if already seen
            if full_url in seen_urls:
                continue

            # Skip short/non-article links
            text = a_tag.get_text(strip=True)
            if len(text) < 25:
                continue

            # Simple article path filter (contains year or keyword segments)
            path = full_url.split("//")[-1].partition("/")[2]
            if not _looks_like_article(path):
                continue

            seen_urls.add(full_url)
            results.append({"url": full_url, "title": text[:200], "source_name": source_name})
            if len(results) >= max_articles:
                break

        return results

    except Exception as exc:
        log.error("HTML scraping error %s: %s", base_url, exc)
        return []


def _looks_like_article(path: str) -> bool:
    """Heuristic: path looks like an article URL."""
    import re
    # Has a number segment (article IDs, years, etc.)
    if re.search(r"/\d{4,}", path):
        return True
    # Has article-like keywords
    article_keywords = [
        "wiadomosc", "artykul", "news", "post", "item", "story",
        "publikacja", "tekst", ",", ".html",
    ]
    return any(kw in path.lower() for kw in article_keywords)
