"""
src/apollo_runner — samodzielny moduł integracji z Apollo.io

Punkt wejścia: run_auto(article_url, contact_info) -> dict

Nie zależy od żadnego lokalnego katalogu macOS.
Wymaga ENV: APOLLO_API_KEY, APOLLO_SEQUENCE_ID (opcjonalnie).
"""
from .runner import run_auto

__all__ = ["run_auto"]
