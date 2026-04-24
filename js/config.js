/**
 * Prasówka SpendGuru — konfiguracja API
 *
 * Produkcja: https://prasowka-spendguru-api.onrender.com
 * Lokalnie:  http://localhost:8000
 */

// Jeśli zmienna jest już ustawiona (np. przez CI/CD), nie nadpisuj.
if (typeof window.PRASOWKA_API_URL === 'undefined') {
  // Produkcja — Render.com
  window.PRASOWKA_API_URL = "https://prasowka-spendguru.onrender.com";

  // Lokalny development: odkomentuj poniższe i zakomentuj linię powyżej
  // window.PRASOWKA_API_URL = 'http://localhost:8000';
}
