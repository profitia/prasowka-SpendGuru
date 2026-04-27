/**
 * Prasówka SpendGuru — main application
 *
 * Loads articles from data/articles.json, renders filterable card wall.
 * Emails are persisted in localStorage; each article/person generates
 * a ready-to-run terminal command for the manual campaign pipeline.
 */

'use strict';

// ============================================================
// Constants
// ============================================================
const PAGE_SIZE = 100;
const WORKSPACE = '/Users/tomaszuscinski/Documents/Visual Code Studio/Kampanie Apollo';
const VENV_ACTIVATE = 'source .venv/bin/activate';
const ORCHESTRATOR = 'python src/news/orchestrator.py manual';

// API backend URL (set in js/config.js; override with window.PRASOWKA_API_URL)
const API_BASE_URL = (typeof window.PRASOWKA_API_URL !== 'undefined') ? window.PRASOWKA_API_URL : '';

const TIER_VALUES = {
  1: 'tier_1_c_level',
  2: 'tier_2_procurement_management',
};

// ============================================================
// State
// ============================================================
let allArticles      = [];
let filteredArticles = [];
let currentPage      = 1;
let apiAvailable     = false;  // true gdy GET /api/articles zadziałało

// Lock preventing duplicate clicks for the same article+tier while request is in-flight
const runningLocks = {};

// ============================================================
// Utilities
// ============================================================

function escHtml(str) {
  return String(str ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function escAttr(str) {
  return String(str ?? '').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formatDate(str) {
  if (!str) return '';
  try {
    return new Date(str).toLocaleDateString('pl-PL', { day: '2-digit', month: '2-digit', year: 'numeric' });
  } catch {
    return str;
  }
}

function parseName(fullName) {
  const parts = String(fullName ?? '').trim().split(/\s+/);
  return { first: parts[0] ?? '', last: parts.slice(1).join(' ') };
}

function csvCell(val) {
  return '"' + String(val ?? '').replace(/"/g, '""') + '"';
}

function debounce(fn, ms) {
  let t;
  return (...args) => { clearTimeout(t); t = setTimeout(() => fn(...args), ms); };
}

// ============================================================
// Contact storage (localStorage)
// ============================================================

function statusLabel(status) {
  if (status === 'sent')    return 'Wysłany';
  if (status === 'running') return 'Uruchamianie';
  return 'Do wysłania';
}

function contactKey(articleId, tier) {
  return `prasowka_contact_${articleId}_t${tier}`;
}

function getContact(articleId, tier) {
  const raw = localStorage.getItem(contactKey(articleId, tier));
  if (!raw) return null;
  try { return JSON.parse(raw); } catch { return null; }
}

function saveContact(articleId, tier, article, email, apolloStatus) {
  const person   = tier === 1 ? (article.tier1_person ?? '') : (article.tier2_person ?? '');
  const position = tier === 1 ? (article.tier1_position ?? '') : (article.tier2_position ?? '');
  const existing = getContact(articleId, tier) ?? {};
  const data = {
    article_id:    articleId,
    article_url:   article.source_url ?? '',
    company:       article.company ?? '',
    tier:          TIER_VALUES[tier],
    full_name:     person,
    job_title:     position,
    email:         email !== undefined ? email : (existing.email ?? ''),
    apollo_status: apolloStatus !== undefined ? apolloStatus : (existing.apollo_status ?? 'waiting'),
    updated_at:    new Date().toISOString(),
  };
  localStorage.setItem(contactKey(articleId, tier), JSON.stringify(data));
  return data;
}

function getContactEmail(articleId, tier) {
  return getContact(articleId, tier)?.email ?? '';
}

function getApolloStatus(articleId, tier) {
  return getContact(articleId, tier)?.apollo_status ?? 'waiting';
}

// ============================================================
// Dark mode
// ============================================================

function initDarkMode() {
  const saved = localStorage.getItem('prasowka_darkmode') === 'true';
  applyTheme(saved);
  document.getElementById('darkToggle').checked = saved;
}

function applyTheme(isDark) {
  document.documentElement.setAttribute('data-theme', isDark ? 'dark' : 'light');
  localStorage.setItem('prasowka_darkmode', isDark);
}

// ============================================================
// Data loading
// ============================================================

/**
 * Pre-populates localStorage contacts with data from DB (DB is source of truth).
 * Called after successful API load. Overwrites only if DB has richer data.
 */
function syncDbContactsToLocalStorage() {
  allArticles.forEach(article => {
    [1, 2].forEach(tier => {
      const hasPerson = tier === 1 ? !!article.tier1_person : !!article.tier2_person;
      if (!hasPerson) return;

      const dbEmail  = tier === 1 ? (article.tier1_email || '') : (article.tier2_email || '');
      const dbStatus = article.apollo_status || 'waiting';
      const existing = getContact(article.id, tier);

      // Overwrite localStorage with DB values (DB wins)
      const email = dbEmail || existing?.email || '';
      saveContact(article.id, tier, article, email, dbStatus);
    });
  });
}

/**
 * Synchronously refreshes history flag elements for all currently visible
 * contacts that already have data in _historyCache.
 * Call after cache population or after any re-render that may reset flag elements.
 */
function _refreshVisibleHistoryFlags() {
  const start = (currentPage - 1) * PAGE_SIZE;
  const end   = Math.min(start + PAGE_SIZE, filteredArticles.length);
  filteredArticles.slice(start, end).forEach(article => {
    [1, 2].forEach(tier => {
      const email = getContactEmail(article.id, tier);
      if (!email || !email.includes('@')) return;
      const cached = _historyCache[email.trim().toLowerCase()];
      if (!cached) return;
      const el = document.getElementById(`hist_${article.id}_t${tier}`);
      if (el) el.outerHTML = renderHistoryFlag(article.id, tier, cached);
    });
  });
}

/**
 * Pre-fetches campaign history for every contact that has an email.
 * Results stored in _historyCache so renderResults() can use them synchronously.
 * Always called as fire-and-forget (no await at call site).
 */
async function preloadCampaignHistory() {
  if (!apiAvailable || !API_BASE_URL) return;
  const emailsSeen = new Set();
  allArticles.forEach(article => {
    [1, 2].forEach(tier => {
      const email = getContactEmail(article.id, tier);
      if (email && email.includes('@')) emailsSeen.add(email.trim().toLowerCase());
    });
  });
  // Parallel fetch — results go into _historyCache via fetchCampaignHistory
  await Promise.allSettled([...emailsSeen].map(fetchCampaignHistory));
  // Cache is now fully populated — update any visible flags in the DOM
  _refreshVisibleHistoryFlags();
}

async function loadArticles() {
  let loaded = false;

  // 1. Try API
  if (API_BASE_URL) {
    try {
      // Pobierz wszystkie rekordy łącznie z needs_review (nie rejected)
      // aby umożliwić filtr jakości danych po stronie frontendu.
      // Rekordy 'rejected' są pomijane przez _LOAD_SQL w backendzie.
      const res = await fetch(`${API_BASE_URL}/api/articles?quality=ok,unknown,needs_review`);
      if (res.ok) {
        allArticles  = await res.json();
        apiAvailable = true;
        syncDbContactsToLocalStorage();
        preloadCampaignHistory();  // fire-and-forget
        loaded = true;
      } else {
        console.warn('[API] GET /api/articles zwrócił HTTP', res.status, '— fallback na JSON');
      }
    } catch (err) {
      console.info('[API] Niedostępne, używam fallback data/articles.json:', err.message);
    }
  }

  // 2. Fallback: static JSON
  if (!loaded) {
    try {
      const res = await fetch('data/articles.json');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      allArticles = await res.json();
    } catch (err) {
      document.getElementById('cards').innerHTML =
        `<div class="empty-state"><p>Błąd ładowania artykułów: ${escHtml(err.message)}</p>
         <p style="font-size:.8rem;opacity:.7">Upewnij się że serwer działa i plik data/articles.json istnieje.</p></div>`;
      document.getElementById('resultsCount').textContent = 'Błąd';
      return;
    }
  }

  buildFilterOptions();
  applyAndRender();
}

// ============================================================
// Build filter dropdowns from data
// ============================================================

function buildFilterOptions() {
  const industries = [...new Set(allArticles.map(a => a.industry).filter(Boolean))].sort();
  const sources    = [...new Set(allArticles.map(a => a.source_name).filter(Boolean))].sort();

  const industryEl = document.getElementById('filterIndustry');
  industries.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v.charAt(0).toUpperCase() + v.slice(1);
    industryEl.appendChild(opt);
  });

  const sourceEl = document.getElementById('filterSource');
  sources.forEach(v => {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    sourceEl.appendChild(opt);
  });
}

// ============================================================
// Filter & sort
// ============================================================

function getFilters() {
  return {
    search:    document.getElementById('searchInput').value.trim().toLowerCase(),
    industry:  document.getElementById('filterIndustry').value,
    status:    [...document.querySelectorAll('[name="filterStatus"]:checked')].map(el => el.value),
    quality:   [...document.querySelectorAll('[name="filterQuality"]:checked')].map(el => el.value),
    source:    document.getElementById('filterSource').value,
    company:   document.getElementById('filterCompany').value.trim().toLowerCase(),
    tier:      [...document.querySelectorAll('[name="filterTier"]:checked')].map(el => el.value),
    dateFrom:  document.getElementById('filterDateFrom').value,
    dateTo:    document.getElementById('filterDateTo').value,
    sortBy:    document.getElementById('sortBy').value,
  };
}

function getArticleApolloStatuses(article) {
  const statuses = [];
  if (article.tier1_person) statuses.push(getApolloStatus(article.id, 1));
  if (article.tier2_person) statuses.push(getApolloStatus(article.id, 2));
  if (statuses.length === 0) statuses.push('waiting');
  return statuses;
}

function applyAndRender() {
  const f = getFilters();

  filteredArticles = allArticles.filter(a => {
    // Quality filter (domyślnie ok + unknown; nie pokazuj rejected)
    const artQuality = a.data_quality_status || 'unknown';
    if (f.quality.length) {
      if (!f.quality.includes(artQuality)) return false;
    } else {
      // Gdy żaden checkbox nie jest zaznaczony — pokaż ok + unknown
      if (!['ok', 'unknown'].includes(artQuality)) return false;
    }

    // Apollo status filter
    if (f.status.length) {
      const articleStatuses = getArticleApolloStatuses(a);
      if (!f.status.some(s => articleStatuses.includes(s))) return false;
    }

    if (f.industry && a.industry !== f.industry) return false;
    if (f.source && a.source_name !== f.source) return false;
    if (f.company && !(a.company ?? '').toLowerCase().includes(f.company)) return false;
    if (f.tier.includes('has_tier1') && !a.tier1_person) return false;
    if (f.tier.includes('has_tier2') && !a.tier2_person) return false;
    if (f.dateFrom && a.article_date && a.article_date < f.dateFrom) return false;
    if (f.dateTo && a.article_date && a.article_date > f.dateTo) return false;

    // Full-text search (includes saved emails and statuses)
    if (f.search) {
      const email1  = getContactEmail(a.id, 1);
      const email2  = getContactEmail(a.id, 2);
      const status1 = getApolloStatus(a.id, 1);
      const status2 = getApolloStatus(a.id, 2);
      const haystack = [
        a.title, a.source_name, a.source_url, a.company,
        a.tier1_person, a.tier1_position, email1,
        a.tier2_person, a.tier2_position, email2,
        a.industry, status1, status2, a.reason, a.context,
      ].join(' ').toLowerCase();
      if (!haystack.includes(f.search)) return false;
    }

    return true;
  });

  // Sort
  filteredArticles.sort((a, b) => {
    if (f.sortBy === 'date_desc') return (b.article_date ?? '').localeCompare(a.article_date ?? '');
    if (f.sortBy === 'date_asc')  return (a.article_date ?? '').localeCompare(b.article_date ?? '');
    if (f.sortBy === 'company_az') return (a.company ?? '').localeCompare(b.company ?? '');
    return 0;
  });

  currentPage = 1;
  renderResults();
}

function resetFilters() {
  document.getElementById('searchInput').value = '';
  document.getElementById('filterIndustry').value = '';
  document.getElementById('filterSource').value = '';
  document.getElementById('filterCompany').value = '';
  document.getElementById('filterDateFrom').value = '';
  document.getElementById('filterDateTo').value = '';
  document.querySelectorAll('[name="filterStatus"]').forEach(cb => { cb.checked = false; });
  document.querySelectorAll('[name="filterTier"]').forEach(cb => { cb.checked = false; });
  // Quality reset: tylko ok + unknown (domyślne)
  document.querySelectorAll('[name="filterQuality"]').forEach(cb => {
    cb.checked = cb.value === 'ok' || cb.value === 'unknown';
  });
  document.getElementById('sortBy').value = 'date_desc';
  applyAndRender();
}

// ============================================================
// Render results
// ============================================================

function renderResults() {
  const total  = filteredArticles.length;
  const start  = (currentPage - 1) * PAGE_SIZE;
  const end    = Math.min(start + PAGE_SIZE, total);
  const page   = filteredArticles.slice(start, end);

  document.getElementById('resultsCount').textContent =
    total === 1 ? '1 wynik' : `${total} wyników`;

  const cardsEl = document.getElementById('cards');

  if (page.length === 0) {
    cardsEl.innerHTML =
      '<div class="empty-state"><p>Brak artykułów spełniających kryteria filtrów.</p></div>';
  } else {
    cardsEl.innerHTML = page.map(renderCard).join('');
  }

  // Restore history flags — from cache (sync) then fetch missing ones (async)
  _refreshVisibleHistoryFlags();
  if (apiAvailable && API_BASE_URL) {
    page.forEach(article => {
      [1, 2].forEach(tier => {
        const email = getContactEmail(article.id, tier);
        if (!email || !email.includes('@')) return;
        if (!_historyCache[email.trim().toLowerCase()]) {
          showHistoryForEmail(article.id, tier, email);
        }
      });
    });
  }

  renderPagination(total);
}

// ============================================================
// Terminal command generator
// ============================================================

function buildCommand(article, fullName, email, tier) {
  const { first, last } = parseName(fullName);
  const tierValue = TIER_VALUES[tier];
  const position  = tier === 1 ? (article.tier1_position ?? '') : (article.tier2_position ?? '');

  const contacts = JSON.stringify([{
    full_name:    fullName,
    first_name:   first,
    last_name:    last,
    email:        email,
    tier:         tierValue,
    job_title:    position,
    company_name: article.company ?? '',
  }]);

  return (
    `cd "${WORKSPACE}" && \\\n` +
    `${VENV_ACTIVATE} && \\\n` +
    `${ORCHESTRATOR} \\\n` +
    `  --article-url "${article.source_url}" \\\n` +
    `  --contacts-json '${contacts}' \\\n` +
    `  --verbose`
  );
}

function renderCommandSection(articleId, tier, email, article, apolloStatus) {
  apolloStatus    = apolloStatus ?? 'waiting';
  const hasEmail  = !!(email && email.includes('@'));
  const isSent    = apolloStatus === 'sent';
  const isRunning = apolloStatus === 'running';
  const confirmId = `copy_confirm_${articleId}_t${tier}`;
  const previewId = `cmd_preview_${articleId}_t${tier}`;

  let previewHtml = '';
  if (hasEmail) {
    const fullName = tier === 1 ? (article.tier1_person ?? '') : (article.tier2_person ?? '');
    const cmd = buildCommand(article, fullName, email, tier);
    previewHtml = `
      <div class="cmd-preview" id="${escAttr(previewId)}" hidden>
        <pre class="cmd-code"><code>${escHtml(cmd)}</code></pre>
      </div>`;
  }

  const runLabel    = isSent ? 'Wyślij ponownie' : (isRunning ? 'Uruchamianie...' : 'Uruchom kampanię Apollo');
  const runDisabled = !hasEmail || isRunning ? 'disabled' : '';
  const runClass    = `btn-run-apollo${isSent ? ' btn-run-apollo--resend' : ''}${isRunning ? ' btn-run-apollo--running' : ''}${!hasEmail ? ' btn-run-apollo--disabled' : ''}`;
  const artUrl      = escAttr(article.source_url ?? '');

  const hintHtml = !hasEmail
    ? `<span class="cmd-hint">Zapisz email, aby uruchomić kampanię</span>`
    : `<span class="cmd-hint cmd-hint--info">${isSent ? 'Kampania wysłana. Kliknij "Wyślij ponownie", aby uruchomić ponownie.' : isRunning ? 'Pipeline w trakcie uruchamiania...' : 'Uruchom auto pipeline lub skopiuj komendę do terminala.'}</span>
           <button class="btn-cmd-toggle"
                   data-preview-id="${escAttr(previewId)}">▸ Pokaż komendę</button>`;

  return `
    <div class="cmd-wrapper">
      <div class="apollo-actions">
        <button class="${runClass}"
                data-article-id="${escAttr(articleId)}"
                data-tier="${tier}"
                data-article-url="${artUrl}"
                ${runDisabled}>
          ${escHtml(runLabel)}
        </button>
        <button class="btn-copy${hasEmail ? '' : ' btn-copy--disabled'}"
                data-article-id="${escAttr(articleId)}"
                data-tier="${tier}"
                ${hasEmail ? '' : 'disabled'}>
          Kopiuj komendę
        </button>
      </div>
      ${hintHtml}
      <span class="copy-confirm" id="${confirmId}" aria-live="polite"></span>
      ${previewHtml}
    </div>`;
}

// ============================================================
// Person block (Tier 1 or Tier 2)
// ============================================================

function renderPersonBlock(article, tier) {
  const person   = tier === 1 ? article.tier1_person : article.tier2_person;
  const position = tier === 1 ? article.tier1_position : article.tier2_position;

  if (!person) return '';

  const contact      = getContact(article.id, tier);
  const savedEmail   = contact?.email ?? '';
  const apolloStatus = contact?.apollo_status ?? 'waiting';
  const isChecked    = apolloStatus === 'sent' ? 'checked' : '';
  const tierClass    = tier === 1 ? 'tier1' : 'tier2';
  const inputId      = `email_${escAttr(article.id)}_t${tier}`;
  const badgeClass   = apolloStatus === 'sent' ? 'apollo-badge--sent'
    : apolloStatus === 'running' ? 'apollo-badge--running'
    : 'apollo-badge--unsent';
  const cmdHtml      = renderCommandSection(article.id, tier, savedEmail, article, apolloStatus);

  return `
    <div class="person-block ${tierClass}"
         data-article-id="${escAttr(article.id)}"
         data-tier="${tier}">
      <div class="person-header">
        <span class="tier-label">Osoba Tier ${tier}</span>
        <span class="apollo-badge ${badgeClass}" id="status_badge_${escAttr(article.id)}_t${tier}">${escHtml(statusLabel(apolloStatus))}</span>
      </div>
      <div class="card-field">
        <span class="field-label">Imię i nazwisko</span>
        <span class="field-value">${escHtml(person)}</span>
      </div>
      <div class="card-field">
        <span class="field-label">Stanowisko</span>
        <span class="field-value">${escHtml(position || '—')}</span>
      </div>
      <div class="email-field">
        <label class="field-label" for="${inputId}">Email</label>
        <div class="email-input-row">
          <input
            type="email"
            id="${inputId}"
            class="email-input"
            placeholder="Wpisz email osoby kontaktowej…"
            value="${escAttr(savedEmail)}"
            data-article-id="${escAttr(article.id)}"
            data-tier="${tier}"
            autocomplete="off"
            spellcheck="false">
          <button class="btn-save"
                  data-article-id="${escAttr(article.id)}"
                  data-tier="${tier}">Zapisz</button>
        </div>
        <span class="save-confirm" id="save_confirm_${escAttr(article.id)}_t${tier}" aria-live="polite"></span>
      </div>
      <div class="apollo-row">
        <label class="apollo-label">
          <input type="checkbox" class="apollo-checkbox"
                 data-article-id="${escAttr(article.id)}"
                 data-tier="${tier}"
                 ${isChecked} disabled>
          Wysłany do Apollo
        </label>
        <span class="apollo-confirm" id="apollo_confirm_${escAttr(article.id)}_t${tier}" aria-live="polite"></span>
      </div>
      <div class="history-flag-wrapper" id="hist_${escAttr(article.id)}_t${tier}"></div>
      <div class="command-block" id="cmd_${escAttr(article.id)}_t${tier}">
        ${cmdHtml}
      </div>
    </div>`;
}

// ============================================================
// Article card
// ============================================================

function renderCard(article) {
  const dateStr   = formatDate(article.article_date);
  const tier1Html = renderPersonBlock(article, 1);
  const tier2Html = article.tier2_person ? renderPersonBlock(article, 2) : '';

  // Quality badge (tylko gdy nie ok)
  const dqs = article.data_quality_status || 'unknown';
  const qualityBadge = dqs === 'needs_review'
    ? `<span class="badge badge-quality badge-quality--warn" title="Do weryfikacji">⚠ Do weryfikacji</span>`
    : dqs === 'rejected'
    ? `<span class="badge badge-quality badge-quality--rejected" title="Odrzucony">✗ Odrzucony</span>`
    : '';

  return `
<article class="card" data-id="${escAttr(article.id)}">
  <div class="card-badges">
    ${article.industry ? `<span class="badge badge-industry">${escHtml(article.industry)}</span>` : ''}
    ${article.press_type ? `<span class="badge badge-press">${escHtml(article.press_type)}</span>` : ''}
    ${qualityBadge}
  </div>

  <h2 class="card-title">
    <a href="${escAttr(article.source_url)}" target="_blank" rel="noopener noreferrer">
      ${escHtml(article.title || '(brak tytułu)')}
    </a>
  </h2>

  <div class="card-meta">
    ${dateStr ? `<span>${escHtml(dateStr)}</span><span class="sep">·</span>` : ''}
    <span class="source-name">${escHtml(article.source_name || '—')}</span>
  </div>

  <hr class="card-divider">

  <div class="card-field">
    <span class="field-label">Firma</span>
    <span class="field-value">${escHtml(article.company || '—')}</span>
  </div>

  ${tier1Html}
  ${tier2Html}

  <hr class="card-divider">

  <div class="card-field card-field--block">
    <span class="field-label">Powód kwalifikacji</span>
    <p class="field-text">${escHtml(article.reason || '—')}</p>
  </div>

  <div class="card-field card-field--block">
    <span class="field-label">Kontekst do kampanii</span>
    <p class="field-text">${escHtml(article.context || '—')}</p>
  </div>

  <div class="card-actions">
    <a href="${escAttr(article.source_url)}"
       target="_blank"
       rel="noopener noreferrer"
       class="btn btn-primary">
      Otwórz artykuł ↗
    </a>
    <button class="btn btn-danger btn-reject"
            data-article-id="${escAttr(article.id)}"
            data-article-url="${escAttr(article.source_url ?? '')}">
      Odrzuć
    </button>
  </div>
</article>`;
}

// ============================================================
// Email save + Apollo checkbox (event delegation on #cards)
// ============================================================

async function postToApi(path, body) {
  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const msg = await res.text().catch(() => res.statusText);
      console.warn(`[API] POST ${path} zwracił HTTP ${res.status}:`, msg);
      return { ok: false };
    }
    return { ok: true, data: await res.json() };
  } catch (err) {
    console.warn(`[API] POST ${path} nie powiódł się:`, err.message);
    return { ok: false };
  }
}

// ---------------------------------------------------------------------------
// Campaign history
// ---------------------------------------------------------------------------
const _historyCache = {};  // email (lowercase) → historyData from API

async function fetchCampaignHistory(email) {
  if (!apiAvailable || !API_BASE_URL || !email || !email.includes('@')) return null;
  const key = email.trim().toLowerCase();
  try {
    const res = await fetch(`${API_BASE_URL}/api/campaign-history?email=${encodeURIComponent(email)}`);
    if (!res.ok) return null;
    const data = await res.json();
    _historyCache[key] = data;
    return data;
  } catch {
    return null;
  }
}

function renderHistoryFlag(articleId, tier, historyData) {
  const wrapperId = `hist_${articleId}_t${tier}`;
  if (!historyData || historyData.sent_count === 0) {
    return `<div class="history-flag-wrapper" id="${wrapperId}"></div>`;
  }

  const count = historyData.sent_count;
  const flagClass = count >= 2 ? 'history-flag--warn' : 'history-flag--info';
  const detailsId = `hist_details_${articleId}_t${tier}`;

  const rows = (historyData.items || []).map(it => {
    const date = it.campaign_run_at
      ? new Date(it.campaign_run_at).toLocaleDateString('pl-PL', { day:'2-digit', month:'2-digit', year:'numeric' })
      : '—';
    const titleHtml = it.article_url
      ? `<a class="history-item-link" href="${escAttr(it.article_url)}" target="_blank" rel="noopener">${escHtml(it.article_title || it.article_url)}</a>`
      : escHtml(it.article_title || '—');
    const runCountHtml = (it.run_count ?? 1) > 1
      ? ` <span class="history-item-run-count" title="Liczba uruchomień">×${it.run_count}</span>`
      : '';
    return `<div class="history-item">
      <span class="history-item-date">${date}${runCountHtml}</span>
      <span class="history-item-company">${escHtml(it.company_name || '—')}</span>
      <span class="history-item-person">${escHtml(it.full_name || '—')}</span>
      <span class="history-item-role">${escHtml(it.job_title || '—')}</span>
      <span class="history-item-article">${titleHtml}</span>
    </div>`;
  }).join('');

  return `<div class="history-flag-wrapper" id="${wrapperId}">
    <button class="history-flag ${flagClass}"
            data-details-id="${escAttr(detailsId)}"
            aria-expanded="false">
      ⧔ Wcześniejsze kampanie: ${count}
    </button>
    <div class="history-details" id="${detailsId}" hidden>
      <div class="history-details-header">
        <span>Data</span><span>Firma</span><span>Osoba</span><span>Stanowisko</span><span>Artykuł</span>
      </div>
      ${rows}
    </div>
  </div>`;
}

async function showHistoryForEmail(articleId, tier, email) {
  const wrapperId = `hist_${articleId}_t${tier}`;
  if (!email || !email.includes('@')) {
    const el = document.getElementById(wrapperId);
    if (el) el.innerHTML = '';
    return;
  }
  const key = email.trim().toLowerCase();
  let data = _historyCache[key] ?? null;
  if (!data) data = await fetchCampaignHistory(email);
  // Re-query element AFTER the async fetch — the DOM may have been re-rendered
  // during the await (e.g. filter change), making any pre-fetch reference stale.
  const el = document.getElementById(wrapperId);
  if (!el) return;
  el.outerHTML = renderHistoryFlag(articleId, tier, data);
}

function handleHistoryFlagClick(e) {
  const btn = e.target.closest('.history-flag');
  if (!btn) return;
  const detailsEl = document.getElementById(btn.dataset.detailsId);
  if (!detailsEl) return;
  const isHidden = detailsEl.hidden;
  detailsEl.hidden = !isHidden;
  btn.setAttribute('aria-expanded', isHidden ? 'true' : 'false');
}

function handleSaveEmail(e) {
  const btn = e.target.closest('.btn-save');
  if (!btn) return;

  const articleId = btn.dataset.articleId;
  const tier      = parseInt(btn.dataset.tier, 10);
  const article   = allArticles.find(a => a.id === articleId);
  if (!article) return;

  const inputEl = document.getElementById(`email_${articleId}_t${tier}`);
  const email   = inputEl?.value.trim() ?? '';

  // Basic validation
  if (email && !email.includes('@')) {
    inputEl?.classList.add('input-error');
    return;
  }
  inputEl?.classList.remove('input-error');

  // Save to localStorage — preserve existing apollo_status
  const existing = getContact(articleId, tier);
  saveContact(articleId, tier, article, email, existing?.apollo_status ?? 'waiting');

  // Refresh command block
  const cmdEl = document.getElementById(`cmd_${articleId}_t${tier}`);
  const existingContact = getContact(articleId, tier);
  const currentStatus   = existingContact?.apollo_status ?? 'waiting';
  if (cmdEl) cmdEl.innerHTML = renderCommandSection(articleId, tier, email, article, currentStatus);

  // Confirmation element
  const confirmEl = document.getElementById(`save_confirm_${articleId}_t${tier}`);

  // POST to API (non-blocking)
  if (apiAvailable && API_BASE_URL && article.source_url) {
    const tierValue = TIER_VALUES[tier];
    postToApi('/api/articles/contact', {
      article_url: article.source_url,
      tier: tierValue,
      email,
    }).then(({ ok }) => {
      if (confirmEl) {
        confirmEl.textContent = ok ? 'Zapisano w bazie ✔' : 'Zapisano lokalnie';
        setTimeout(() => { confirmEl.textContent = ''; }, 2500);
      }
    });
  } else {
    if (confirmEl) {
      confirmEl.textContent = 'Zapisano lokalnie';
      setTimeout(() => { confirmEl.textContent = ''; }, 2500);
    }
  }

  // Load/refresh campaign history for this email (non-blocking)
  if (email && email.includes('@')) {
    showHistoryForEmail(articleId, tier, email);
  }
}

function handleApolloCheckbox(e) {
  const cb = e.target;
  if (!cb.classList.contains('apollo-checkbox')) return;

  const articleId   = cb.dataset.articleId;
  const tier        = parseInt(cb.dataset.tier, 10);
  const article     = allArticles.find(a => a.id === articleId);
  if (!article) return;

  const apolloStatus = cb.checked ? 'sent' : 'waiting';
  const existing     = getContact(articleId, tier);
  saveContact(articleId, tier, article, existing?.email ?? '', apolloStatus);

  // Update badge
  const badgeEl = document.getElementById(`status_badge_${articleId}_t${tier}`);
  if (badgeEl) {
    badgeEl.textContent = statusLabel(apolloStatus);
    badgeEl.className   = `apollo-badge ${apolloStatus === 'sent' ? 'apollo-badge--sent' : 'apollo-badge--unsent'}`;
  }

  // Confirmation element
  const confirmEl = document.getElementById(`apollo_confirm_${articleId}_t${tier}`);

  // POST to API (non-blocking)
  if (apiAvailable && API_BASE_URL && article.source_url) {
    postToApi('/api/articles/status', {
      article_url:   article.source_url,
      apollo_status: apolloStatus,
    }).then(({ ok }) => {
      if (confirmEl) {
        confirmEl.textContent = ok ? 'Zapisano w bazie ✔' : 'Status zaktualizowany';
        setTimeout(() => { confirmEl.textContent = ''; }, 2500);
      }
    });
  } else {
    if (confirmEl) {
      confirmEl.textContent = 'Status zaktualizowany';
      setTimeout(() => { confirmEl.textContent = ''; }, 2500);
    }
  }
}

function handleCmdToggle(e) {
  const btn = e.target.closest('.btn-cmd-toggle');
  if (!btn) return;

  const previewEl = document.getElementById(btn.dataset.previewId);
  if (!previewEl) return;

  if (previewEl.hasAttribute('hidden')) {
    previewEl.removeAttribute('hidden');
    btn.textContent = '▾ Ukryj komendę';
  } else {
    previewEl.setAttribute('hidden', '');
    btn.textContent = '▸ Pokaż komendę';
  }
}

function _markSentInUI(articleId, tier, article) {
  saveContact(articleId, tier, article, getContact(articleId, tier)?.email ?? '', 'sent');

  const cbEl = document.querySelector(`.apollo-checkbox[data-article-id="${articleId}"][data-tier="${tier}"]`);
  if (cbEl) cbEl.checked = true;

  const badgeEl = document.getElementById(`status_badge_${articleId}_t${tier}`);
  if (badgeEl) {
    badgeEl.textContent = statusLabel('sent');
    badgeEl.className   = 'apollo-badge apollo-badge--sent';
  }
}

function handleCopyClick(e) {
  const btn = e.target.closest('.btn-copy');
  if (!btn) return;

  const articleId = btn.dataset.articleId;
  const tier      = parseInt(btn.dataset.tier, 10);
  const article   = allArticles.find(a => a.id === articleId);
  const cmdEl     = document.getElementById(`cmd_${articleId}_t${tier}`);
  const code      = cmdEl?.querySelector('code');
  if (!code || !article) return;

  const confirmEl = document.getElementById(`copy_confirm_${articleId}_t${tier}`);

  function onCopied() {
    showToast('Skopiowano komendę ✔', 'success', 4000);
    if (confirmEl) {
      confirmEl.textContent = 'Skopiowano komendę ✔';
      setTimeout(() => { confirmEl.textContent = ''; }, 3000);
    }
  }

  function onError() {
    if (confirmEl) {
      confirmEl.textContent = 'Błąd kopiowania — spróbuj ręcznie';
      setTimeout(() => { confirmEl.textContent = ''; }, 3000);
    }
  }

  // Try modern clipboard API first
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(code.textContent).then(onCopied).catch(onError);
  } else {
    // Fallback execCommand
    try {
      const ta = document.createElement('textarea');
      ta.value = code.textContent;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
      onCopied();
    } catch {
      onError();
    }
  }
}

// ============================================================
// Toast notifications
// ============================================================

function showToast(message, type = 'info', duration = 5000) {
  const container = document.getElementById('toast-container');
  if (!container) return null;

  const toast = document.createElement('div');
  toast.className = `toast toast--${type}`;
  toast.setAttribute('role', 'status');
  toast.textContent = message;

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast-close';
  closeBtn.setAttribute('aria-label', 'Zamknij');
  closeBtn.textContent = '×';
  closeBtn.onclick = () => dismissToast(toast);
  toast.appendChild(closeBtn);

  container.appendChild(toast);
  requestAnimationFrame(() => toast.classList.add('toast--visible'));

  if (duration > 0) {
    setTimeout(() => dismissToast(toast), duration);
  }

  return toast;
}

function dismissToast(toast) {
  if (!toast || toast._dismissed) return;
  toast._dismissed = true;
  toast.classList.remove('toast--visible');
  toast.classList.add('toast--hiding');
  setTimeout(() => { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
}

// ============================================================
// Apollo auto pipeline runner
// ============================================================
async function handleRunApollo(e) {
  const btn = e.target.closest('.btn-run-apollo');
  if (!btn || btn.disabled) return;

  const articleId = btn.dataset.articleId;
  const tier      = parseInt(btn.dataset.tier, 10);
  const lockKey   = `${articleId}_t${tier}`;

  // Debounce: ignore duplicate clicks while request is in-flight
  if (runningLocks[lockKey]) return;
  runningLocks[lockKey] = true;

  const article = allArticles.find(a => a.id === articleId);
  if (!article) { runningLocks[lockKey] = false; return; }

  const contact = getContact(articleId, tier);
  if (!contact?.email) { runningLocks[lockKey] = false; return; }

  const confirmEl  = document.getElementById(`copy_confirm_${articleId}_t${tier}`);
  const prevStatus = contact.apollo_status ?? 'waiting';

  if (confirmEl) confirmEl.textContent = '';

  // Immediately show cold-start toast (no auto-dismiss — dismissed after request completes)
  const coldStartToast = showToast('Budzę API… to może potrwać do 60 sekund', 'info', 0);

  // After 5 s update the message if still waiting for a response
  const coldStartTimer = setTimeout(() => {
    if (coldStartToast && !coldStartToast._dismissed) {
      const closeBtn = coldStartToast.querySelector('.toast-close');
      coldStartToast.textContent = 'API nadal startuje… proszę czekać';
      if (closeBtn) coldStartToast.appendChild(closeBtn);
    }
  }, 5000);

  // Ustaw running w lokalnym cache i odśwież badge / blok
  saveContact(articleId, tier, article, contact.email, 'running');
  const cmdElRun = document.getElementById(`cmd_${articleId}_t${tier}`);
  if (cmdElRun) cmdElRun.innerHTML = renderCommandSection(articleId, tier, contact.email, article, 'running');
  const badgeElRun = document.getElementById(`status_badge_${articleId}_t${tier}`);
  if (badgeElRun) {
    badgeElRun.textContent = statusLabel('running');
    badgeElRun.className   = 'apollo-badge apollo-badge--running';
  }

  const body = {
    article_url:  article.source_url ?? '',
    company_name: article.company ?? '',
    full_name:    contact.full_name ?? '',
    email:        contact.email,
    tier:         TIER_VALUES[tier],
    job_title:    contact.job_title ?? '',
  };

  let result = { ok: false, message: 'Błąd połączenia z API' };

  try {
    const controller = new AbortController();
    const tid = setTimeout(() => controller.abort(), 210_000); // 210 s > backend 180 s
    const res = await fetch(`${API_BASE_URL}/api/apollo/run-auto`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(body),
      signal:  controller.signal,
    });
    clearTimeout(tid);
    result = await res.json().catch(() => ({ ok: false, message: `HTTP ${res.status}` }));
  } catch (err) {
    result = {
      ok:      false,
      message: err.name === 'AbortError' ? 'Timeout — pipeline trwał za długo' : `Błąd połączenia: ${err.message}`,
    };
  }

  // Dismiss cold-start toast regardless of outcome
  clearTimeout(coldStartTimer);
  dismissToast(coldStartToast);

  if (result.ok) {
    _markSentInUI(articleId, tier, article);
    const cmdEl = document.getElementById(`cmd_${articleId}_t${tier}`);
    if (cmdEl) cmdEl.innerHTML = renderCommandSection(articleId, tier, contact.email, article, 'sent');
    const successMsg = prevStatus === 'sent'
      ? 'Kampania Apollo uruchomiona ponownie ✔'
      : 'Kampania Apollo uruchomiona ✔';
    showToast(successMsg, 'success', 5000);
    if (confirmEl) {
      confirmEl.textContent = successMsg;
      setTimeout(() => { confirmEl.textContent = ''; }, 4000);
    }
    // Odśwież historię (nowy wpis lub aktualizacja run_count)
    if (contact.email && contact.email.includes('@')) {
      delete _historyCache[contact.email.trim().toLowerCase()];
      showHistoryForEmail(articleId, tier, contact.email);
    }
  } else {
    // Revert do poprzedniego statusu (waiting lub sent)
    const revertStatus = prevStatus === 'sent' ? 'sent' : 'waiting';
    saveContact(articleId, tier, article, contact.email, revertStatus);
    const cmdElErr = document.getElementById(`cmd_${articleId}_t${tier}`);
    if (cmdElErr) cmdElErr.innerHTML = renderCommandSection(articleId, tier, contact.email, article, revertStatus);
    const badgeElErr = document.getElementById(`status_badge_${articleId}_t${tier}`);
    if (badgeElErr) {
      badgeElErr.textContent = statusLabel(revertStatus);
      badgeElErr.className   = `apollo-badge apollo-badge--${revertStatus === 'sent' ? 'sent' : 'unsent'}`;
    }
    const errorMsg = result.message || 'Nie udało się uruchomić kampanii Apollo. Status przywrócony.';
    showToast('Nie udało się uruchomić kampanii Apollo', 'error', 6000);
    console.error('[Apollo runner] błąd:', errorMsg);
    if (confirmEl) {
      confirmEl.textContent = errorMsg;
      setTimeout(() => { confirmEl.textContent = ''; }, 5000);
    }
  }

  runningLocks[lockKey] = false;
}

// ============================================================
// Pagination
// ============================================================

function renderPagination(total) {
  const totalPages = Math.ceil(total / PAGE_SIZE);
  const el = document.getElementById('pagination');

  if (totalPages <= 1) {
    el.innerHTML = '';
    return;
  }

  const prevDisabled = currentPage === 1         ? 'disabled' : '';
  const nextDisabled = currentPage === totalPages ? 'disabled' : '';

  el.innerHTML = `
    <button class="btn btn-ghost" data-page="${currentPage - 1}" ${prevDisabled}>
      ← Poprzednia
    </button>
    <span class="pagination-info">Strona ${currentPage} z ${totalPages}
      &nbsp;·&nbsp; ${total} wyników</span>
    <button class="btn btn-ghost" data-page="${currentPage + 1}" ${nextDisabled}>
      Następna →
    </button>`;
}

function handlePaginationClick(e) {
  const btn = e.target.closest('[data-page]');
  if (!btn) return;
  const page = parseInt(btn.dataset.page, 10);
  if (isNaN(page)) return;
  const totalPages = Math.ceil(filteredArticles.length / PAGE_SIZE);
  if (page < 1 || page > totalPages) return;
  currentPage = page;
  renderResults();
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

// ============================================================
// CSV Export
// ============================================================

function exportCSV() {
  const fields = [
    'article_date', 'title', 'source_name', 'source_url', 'company',
    'tier', 'full_name', 'job_title', 'email', 'apollo_status',
    'industry', 'reason', 'context',
  ];

  const rows = [];

  filteredArticles.forEach(a => {
    const addRow = (tier, person, position) => {
      if (!person) return;
      const contact      = getContact(a.id, tier);
      const email        = contact?.email ?? '';
      const apolloStatus = contact?.apollo_status ?? 'waiting';
      rows.push({
        article_date:  a.article_date ?? '',
        title:         a.title ?? '',
        source_name:   a.source_name ?? '',
        source_url:    a.source_url ?? '',
        company:       a.company ?? '',
        tier:          TIER_VALUES[tier],
        full_name:     person,
        job_title:     position ?? '',
        email,
        apollo_status: apolloStatus,
        industry:      a.industry ?? '',
        reason:        a.reason ?? '',
        context:       a.context ?? '',
      });
    };

    addRow(1, a.tier1_person, a.tier1_position);
    addRow(2, a.tier2_person, a.tier2_position);
  });

  if (rows.length === 0) {
    alert('Brak kontaktów do eksportu.');
    return;
  }

  const csv = [
    fields.join(','),
    ...rows.map(r => fields.map(f => csvCell(r[f])).join(',')),
  ].join('\r\n');

  const blob = new Blob(['\uFEFF' + csv], { type: 'text/csv;charset=utf-8;' });
  const url  = URL.createObjectURL(blob);
  const a    = document.createElement('a');
  a.href     = url;
  a.download = `prasowka_spendguru_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// ============================================================
// Add article by URL
// ============================================================

async function handleAddArticle() {
  const input    = document.getElementById('addArticleUrl');
  const statusEl = document.getElementById('addArticleStatus');
  const btn      = document.getElementById('addArticleBtn');

  const url = (input?.value ?? '').trim();

  // Frontend validation
  if (!url) {
    statusEl.textContent = 'Podaj URL artykułu.';
    statusEl.className   = 'add-article-status add-article-status--error';
    return;
  }
  try {
    const p = new URL(url);
    if (!['http:', 'https:'].includes(p.protocol)) throw new Error();
  } catch {
    statusEl.textContent = 'Nieprawidłowy URL — musi zaczynać się od http:// lub https://';
    statusEl.className   = 'add-article-status add-article-status--error';
    return;
  }

  if (!apiAvailable || !API_BASE_URL) {
    statusEl.textContent = 'Funkcja niedostępna — API nie jest podłączone.';
    statusEl.className   = 'add-article-status add-article-status--error';
    return;
  }

  btn.disabled           = true;
  statusEl.textContent   = 'Pobieram artykuł…';
  statusEl.className     = 'add-article-status add-article-status--info';

  try {
    const res = await fetch(`${API_BASE_URL}/api/articles/add`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ url }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      statusEl.textContent = `Błąd: ${err.detail || res.statusText}`;
      statusEl.className   = 'add-article-status add-article-status--error';
      return;
    }

    const data = await res.json();

    if (data.status === 'duplicate') {
      statusEl.textContent = 'Artykuł już istnieje w bazie.';
      statusEl.className   = 'add-article-status add-article-status--warn';
      const existingId   = data.article?.id ?? '';
      const existingCard = existingId
        ? document.querySelector(`.card[data-id="${escAttr(existingId)}"]`)
        : null;
      if (existingCard) {
        existingCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
        existingCard.classList.add('card--highlight');
        setTimeout(() => existingCard.classList.remove('card--highlight'), 2000);
      } else {
        showToast('Artykuł już istnieje — może być na innej stronie wyników.', 'info', 4000);
      }
    } else {
      // Nowy artykuł
      statusEl.textContent = 'Artykuł dodany ✔';
      statusEl.className   = 'add-article-status add-article-status--ok';
      input.value          = '';

      if (data.article) {
        allArticles.unshift(data.article);
        applyAndRender();
        setTimeout(() => {
          const newCard = document.querySelector(`.card[data-id="${escAttr(data.article.id)}"]`);
          if (newCard) {
            newCard.scrollIntoView({ behavior: 'smooth', block: 'center' });
            newCard.classList.add('card--highlight');
            setTimeout(() => newCard.classList.remove('card--highlight'), 2000);
          }
        }, 80);
      }
      showToast('Artykuł dodany ✔', 'success', 4000);
    }
  } catch (err) {
    statusEl.textContent = `Błąd połączenia: ${err.message}`;
    statusEl.className   = 'add-article-status add-article-status--error';
  } finally {
    btn.disabled = false;
    setTimeout(() => {
      statusEl.textContent = '';
      statusEl.className   = 'add-article-status';
    }, 6000);
  }
}

// ============================================================
// Reject article
// ============================================================

async function handleRejectArticle(e) {
  const btn = e.target.closest('.btn-reject');
  if (!btn) return;

  const articleId  = btn.dataset.articleId;
  const articleUrl = btn.dataset.articleUrl;

  if (!confirm('Odrzucić ten artykuł?\n\nZostanie trwale oznaczony jako odrzucony w bazie danych i nie będzie ponownie wyświetlany.')) {
    return;
  }

  if (!apiAvailable || !API_BASE_URL) {
    showToast('Funkcja niedostępna — API nie jest podłączone.', 'error', 4000);
    return;
  }

  btn.disabled = true;

  try {
    const res = await fetch(`${API_BASE_URL}/api/articles/reject`, {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify({ article_url: articleUrl }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
      showToast(`Błąd odrzucania: ${err.detail || res.statusText}`, 'error', 5000);
      btn.disabled = false;
      return;
    }

    // Usuń z tablicy i przerenderuj
    allArticles = allArticles.filter(a => a.id !== articleId);
    applyAndRender();
    showToast('Artykuł odrzucony i usunięty z widoku.', 'success', 4000);
  } catch (err) {
    showToast(`Błąd połączenia: ${err.message}`, 'error', 5000);
    btn.disabled = false;
  }
}

// ============================================================
// Bootstrap
// ============================================================

document.addEventListener('DOMContentLoaded', () => {
  initDarkMode();
  loadArticles();

  // Dark mode toggle
  document.getElementById('darkToggle')
    .addEventListener('change', e => applyTheme(e.target.checked));

  // Search (debounced)
  document.getElementById('searchInput')
    .addEventListener('input', debounce(applyAndRender, 280));

  // Filter selects
  ['filterIndustry', 'filterSource', 'sortBy'].forEach(id => {
    document.getElementById(id).addEventListener('change', applyAndRender);
  });

  // Company text filter (debounced)
  document.getElementById('filterCompany')
    .addEventListener('input', debounce(applyAndRender, 280));

  // Date filters
  document.getElementById('filterDateFrom').addEventListener('change', applyAndRender);
  document.getElementById('filterDateTo').addEventListener('change', applyAndRender);

  // Status & tier & quality checkboxes — delegate on their containers
  document.getElementById('filterStatus').addEventListener('change', applyAndRender);
  document.getElementById('filterQuality').addEventListener('change', applyAndRender);
  document.querySelectorAll('[name="filterTier"]').forEach(cb =>
    cb.addEventListener('change', applyAndRender)
  );

  // Reset
  document.getElementById('resetFilters').addEventListener('click', resetFilters);

  // Export
  document.getElementById('exportCsvBtn').addEventListener('click', exportCSV);

  // Event delegation on cards grid
  const cardsEl = document.getElementById('cards');
  cardsEl.addEventListener('click',  e => { handleSaveEmail(e); handleCopyClick(e); handleCmdToggle(e); handleRunApollo(e); handleHistoryFlagClick(e); handleRejectArticle(e); });
  cardsEl.addEventListener('change', handleApolloCheckbox);

  // Add article by URL
  document.getElementById('addArticleBtn')
    .addEventListener('click', handleAddArticle);
  document.getElementById('addArticleUrl')
    .addEventListener('keydown', e => { if (e.key === 'Enter') handleAddArticle(); });

  // Event delegation on pagination
  document.getElementById('pagination').addEventListener('click', handlePaginationClick);
});
