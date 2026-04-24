"""Fetch and extract full article text from a URL."""
from __future__ import annotations

import logging

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("news.scraper")

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "pl-PL,pl;q=0.9,en-US;q=0.8",
}

# CSS selectors tried in order to find main article body
_BODY_SELECTORS = [
    "article",
    '[class*="article-body"]',
    '[class*="article-content"]',
    '[class*="entry-content"]',
    '[class*="post-content"]',
    '[class*="content-body"]',
    '[class*="news-body"]',
    "main",
]

# Max characters of article text to keep (saves LLM tokens)
_MAX_TEXT_CHARS = 8_000


def fetch_article(url: str) -> dict:
    """
    Fetch and parse article at *url*.

    Returns dict:
        url, title, text, source_name (empty, caller fills in)
    """
    try:
        resp = requests.get(url, headers=_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Remove noise elements
        for tag in soup(["script", "style", "nav", "footer", "aside", "header", "noscript"]):
            tag.decompose()

        # Title
        title = _extract_title(soup)

        # Body text
        text = _extract_text(soup)

        return {"url": url, "title": title, "text": text, "source_name": ""}

    except Exception as exc:
        log.warning("Fetch error %s: %s", url, exc)
        return {"url": url, "title": "", "text": "", "source_name": ""}


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in ["h1", ".article-title", ".entry-title", ".news-title", ".post-title"]:
        el = soup.select_one(selector)
        if el:
            return el.get_text(strip=True)
    if soup.title:
        return soup.title.get_text(strip=True)
    return ""


def _extract_text(soup: BeautifulSoup) -> str:
    for selector in _BODY_SELECTORS:
        el = soup.select_one(selector)
        if el:
            raw = el.get_text(separator=" ", strip=True)
            if len(raw) > 200:
                return " ".join(raw.split())[:_MAX_TEXT_CHARS]

    # Last resort: full page text
    raw = soup.get_text(separator=" ", strip=True)
    return " ".join(raw.split())[:_MAX_TEXT_CHARS]
