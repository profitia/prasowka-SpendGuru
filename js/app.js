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
    apollo_status: apolloStatus !== undefined ? apolloStatus : (existing.apollo_status ?? 'Nie wysłany'),
    updated_at:    new Date().toISOString(),
  };
  localStorage.setItem(contactKey(articleId, tier), JSON.stringify(data));
  return data;
}

function getContactEmail(articleId, tier) {
  return getContact(articleId, tier)?.email ?? '';
}

function getApolloStatus(articleId, tier) {
  return getContact(articleId, tier)?.apollo_status ?? 'Nie wysłany';
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
      const dbStatus = article.apollo_status || 'Nie wysłany';
      const existing = getContact(article.id, tier);

      // Overwrite localStorage with DB values (DB wins)
      const email = dbEmail || existing?.email || '';
      saveContact(article.id, tier, article, email, dbStatus);
    });
  });
}

async function loadArticles() {
  let loaded = false;

  // 1. Try API
  if (API_BASE_URL) {
    try {
      const res = await fetch(`${API_BASE_URL}/api/articles`);
      if (res.ok) {
        allArticles  = await res.json();
        apiAvailable = true;
        syncDbContactsToLocalStorage();
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
  if (statuses.length === 0) statuses.push('Nie wysłany');
  return statuses;
}

function applyAndRender() {
  const f = getFilters();

  filteredArticles = allArticles.filter(a => {
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

function renderCommandSection(articleId, tier, email, article) {
  if (!email || !email.includes('@')) {
    return `<p class="cmd-hint">Wpisz i zapisz email, aby wygenerować komendę</p>`;
  }

  const fullName = tier === 1 ? (article.tier1_person ?? '') : (article.tier2_person ?? '');
  const cmd      = buildCommand(article, fullName, email, tier);
  const confirmId = `copy_confirm_${articleId}_t${tier}`;

  return `
    <div class="cmd-wrapper">
      <pre class="cmd-code"><code>${escHtml(cmd)}</code></pre>
      <div class="cmd-footer">
        <button class="btn-copy"
          data-article-id="${escAttr(articleId)}"
          data-tier="${tier}">
          Kopiuj komendę
        </button>
        <span class="copy-confirm" id="${confirmId}" aria-live="polite"></span>
      </div>
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
  const apolloStatus = contact?.apollo_status ?? 'Nie wysłany';
  const isChecked    = apolloStatus === 'Wysłany' ? 'checked' : '';
  const tierClass    = tier === 1 ? 'tier1' : 'tier2';
  const inputId      = `email_${escAttr(article.id)}_t${tier}`;
  const badgeClass   = apolloStatus === 'Wysłany' ? 'apollo-badge--sent' : 'apollo-badge--unsent';
  const cmdHtml      = renderCommandSection(article.id, tier, savedEmail, article);

  return `
    <div class="person-block ${tierClass}"
         data-article-id="${escAttr(article.id)}"
         data-tier="${tier}">
      <div class="person-header">
        <span class="tier-label">Osoba Tier ${tier}</span>
        <span class="apollo-badge ${badgeClass}" id="status_badge_${escAttr(article.id)}_t${tier}">${escHtml(apolloStatus)}</span>
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
                 ${isChecked}>
          Wysłany do Apollo
        </label>
        <span class="apollo-confirm" id="apollo_confirm_${escAttr(article.id)}_t${tier}" aria-live="polite"></span>
      </div>
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

  return `
<article class="card" data-id="${escAttr(article.id)}">
  <div class="card-badges">
    ${article.industry ? `<span class="badge badge-industry">${escHtml(article.industry)}</span>` : ''}
    ${article.press_type ? `<span class="badge badge-press">${escHtml(article.press_type)}</span>` : ''}
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
  saveContact(articleId, tier, article, email, existing?.apollo_status ?? 'Nie wysłany');

  // Refresh command block
  const cmdEl = document.getElementById(`cmd_${articleId}_t${tier}`);
  if (cmdEl) cmdEl.innerHTML = renderCommandSection(articleId, tier, email, article);

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
}

function handleApolloCheckbox(e) {
  const cb = e.target;
  if (!cb.classList.contains('apollo-checkbox')) return;

  const articleId   = cb.dataset.articleId;
  const tier        = parseInt(cb.dataset.tier, 10);
  const article     = allArticles.find(a => a.id === articleId);
  if (!article) return;

  const apolloStatus = cb.checked ? 'Wysłany' : 'Nie wysłany';
  const existing     = getContact(articleId, tier);
  saveContact(articleId, tier, article, existing?.email ?? '', apolloStatus);

  // Update badge
  const badgeEl = document.getElementById(`status_badge_${articleId}_t${tier}`);
  if (badgeEl) {
    badgeEl.textContent = apolloStatus;
    badgeEl.className   = `apollo-badge ${apolloStatus === 'Wysłany' ? 'apollo-badge--sent' : 'apollo-badge--unsent'}`;
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

// ============================================================
// Copy command (event delegation on #cards)
// ============================================================

function handleCopyClick(e) {
  const btn = e.target.closest('.btn-copy');
  if (!btn) return;

  const articleId = btn.dataset.articleId;
  const tier      = parseInt(btn.dataset.tier, 10);
  const cmdEl     = document.getElementById(`cmd_${articleId}_t${tier}`);
  const code      = cmdEl?.querySelector('code');
  if (!code) return;

  navigator.clipboard.writeText(code.textContent).then(() => {
    const confirmEl = document.getElementById(`copy_confirm_${articleId}_t${tier}`);
    if (confirmEl) {
      confirmEl.textContent = '✓ Skopiowano';
      setTimeout(() => { confirmEl.textContent = ''; }, 2000);
    }
  }).catch(() => {
    // Fallback for older browsers
    const ta = document.createElement('textarea');
    ta.value = code.textContent;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);

    const confirmEl = document.getElementById(`copy_confirm_${articleId}_t${tier}`);
    if (confirmEl) {
      confirmEl.textContent = '✓ Skopiowano';
      setTimeout(() => { confirmEl.textContent = ''; }, 2000);
    }
  });
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
      const apolloStatus = contact?.apollo_status ?? 'Nie wysłany';
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

  // Status & tier checkboxes — delegate on their containers
  document.getElementById('filterStatus').addEventListener('change', applyAndRender);
  document.querySelectorAll('[name="filterTier"]').forEach(cb =>
    cb.addEventListener('change', applyAndRender)
  );

  // Reset
  document.getElementById('resetFilters').addEventListener('click', resetFilters);

  // Export
  document.getElementById('exportCsvBtn').addEventListener('click', exportCSV);

  // Event delegation on cards grid
  const cardsEl = document.getElementById('cards');
  cardsEl.addEventListener('click',  e => { handleSaveEmail(e); handleCopyClick(e); });
  cardsEl.addEventListener('change', handleApolloCheckbox);

  // Event delegation on pagination
  document.getElementById('pagination').addEventListener('click', handlePaginationClick);
});
