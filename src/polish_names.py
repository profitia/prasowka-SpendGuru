#!/usr/bin/env python3
"""
Wspólny helper — wołacz i płeć dla polskich imion.

Źródło prawdy: context/Vocative names od VSC.csv
Format: Mianownik;Wołacz;Płeć (Kobieta/Mężczyzna)

Używany przez wszystkie kampanie (article_triggered, csv_import, itd.).
"""

import csv
import os
from typing import Optional

# Ścieżka do CSV — obok tego pliku w src/data/
_CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "vocative_names.csv")

# Mapowanie etykiet płci z CSV na wartości systemowe
_GENDER_MAP = {
    "kobieta": "female",
    "mężczyzna": "male",
}

# Lazy-loaded cache
_name_data: dict[str, dict] | None = None


def _load_csv() -> dict[str, dict]:
    """
    Ładuje plik CSV z wołaczami i płcią.
    Zwraca dict: nominative_lower → {"nominative": ..., "vocative": ..., "gender": ...}
    Header jest automatycznie pomijany jeśli obecny.
    """
    global _name_data
    if _name_data is not None:
        return _name_data

    _name_data = {}

    if not os.path.exists(_CSV_PATH):
        return _name_data

    with open(_CSV_PATH, "r", encoding="utf-8") as f:
        # Wykryj separator
        sample = f.read(1024)
        f.seek(0)
        delimiter = ";" if ";" in sample else ","

        reader = csv.reader(f, delimiter=delimiter)

        for i, row in enumerate(reader):
            if len(row) < 3:
                continue

            col_a = row[0].strip()
            col_b = row[1].strip()
            col_c = row[2].strip()

            # Pomiń nagłówek — jeśli kolumna C nie jest "Kobieta" ani "Mężczyzna"
            if col_c.lower() not in _GENDER_MAP:
                continue

            if not col_a or not col_b:
                continue

            gender = _GENDER_MAP[col_c.lower()]

            _name_data[col_a.lower()] = {
                "nominative": col_a,
                "vocative": col_b,
                "gender": gender,
            }

    return _name_data


def get_polish_name_data(first_name: str) -> Optional[dict]:
    """
    Zwraca dane imienia z CSV lub None jeśli nie znaleziono.

    Returns:
        dict z kluczami: nominative, vocative, gender
        lub None jeśli imię nie istnieje w pliku CSV.
    """
    if not first_name or not first_name.strip():
        return None

    data = _load_csv()
    key = first_name.strip().lower()
    return data.get(key)


def get_vocative(first_name: str) -> Optional[str]:
    """Zwraca wołacz lub None."""
    result = get_polish_name_data(first_name)
    return result["vocative"] if result else None


def get_gender(first_name: str) -> str:
    """Zwraca 'female', 'male' lub 'unknown'."""
    result = get_polish_name_data(first_name)
    return result["gender"] if result else "unknown"


def build_greeting(gender: str, first_name_vocative: Optional[str]) -> str:
    """Buduje powitanie na podstawie płci i wołacza."""
    if gender == "female" and first_name_vocative:
        return f"Dzień dobry Pani {first_name_vocative},"
    elif gender == "male" and first_name_vocative:
        return f"Dzień dobry Panie {first_name_vocative},"
    else:
        return "Dzień dobry,"


def resolve_polish_contact(first_name: str) -> dict:
    """
    Resolves gender, vocative and greeting for a Polish first name.
    Uses CSV as primary source, safe fallback if not found.

    Returns dict with keys:
        gender: "female" | "male" | "unknown"
        first_name_vocative: str | None
        greeting: str
    """
    data = get_polish_name_data(first_name)

    if data:
        gender = data["gender"]
        vocative = data["vocative"]
    else:
        gender = "unknown"
        vocative = None

    greeting = build_greeting(gender, vocative)

    return {
        "gender": gender,
        "first_name_vocative": vocative,
        "greeting": greeting,
    }
