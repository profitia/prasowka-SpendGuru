/**
 * Prasówka SpendGuru — konfiguracja API
 *
 * Zmień PRASOWKA_API_URL na URL swojego deploymentu (Render/Railway)
 * przed wdrożeniem na GitHub Pages.
 *
 * Lokalnie: http://localhost:8000
 * Produkcja: https://prasowka-api.onrender.com (lub inny URL)
 */

// Jeśli zmienna jest już ustawiona (np. przez CI/CD), nie nadpisuj.
if (typeof window.PRASOWKA_API_URL === 'undefined') {
  window.PRASOWKA_API_URL = 'http://localhost:8000';
}
