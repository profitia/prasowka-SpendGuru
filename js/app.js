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

const STATUS_LABELS = {
  new:              'Nowy',
  used_in_campaign: 'W kampanii',
  rejected:         'Odrzucony',
  to_verify:        'Do weryfikacji',
};

const TIER_VALUES = {
  1: 'tier_1_c_level',
  2: 'tier_2_procurement_management',
};

// ============================================================
// State
// ============================================================
let allArticles     = [];
let filteredArticles = [];
let currentPage     = 1;

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

function emailKey(articleId, tier) {
  return `prasowka_email_${articleId}_tier${tier}`;
}

function storedEmail(articleId, tier) {
  return localStorage.getItem(emailKey(articleId, tier)) ?? '';
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

async function loadArticles() {
  try {
    const res = await fetch('data/articles.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    allArticles = await res.json();
    buildFilterOptions();
    applyAndRender();
  } catch (err) {
    document.getElementById('cards').innerHTML =
      `<div class="empty-state"><p>Błąd ładowania artykułów: ${escHtml(err.message)}</p>
       <p style="font-size:.8rem;opacity:.7">Upewnij się że serwer działa i plik data/articles.json istnieje.</p></div>`;
    document.getElementById('resultsCount').textContent = 'Błąd';
  }
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

function applyAndRender() {
  const f = getFilters();

  filteredArticles = allArticles.filter(a => {
    // Full-text search
    if (f.search) {
      const haystack = [
        a.title, a.company, a.tier1_person, a.tier1_position,
        a.tier2_person, a.tier2_position, a.reason, a.context,
        a.source_name, a.industry,
      ].join(' ').toLowerCase();
      if (!haystack.includes(f.search)) return false;
    }

    if (f.industry && a.industry !== f.industry) return false;
    if (f.status.length && !f.status.includes(a.status)) return false;
    if (f.source && a.source_name !== f.source) return false;
    if (f.company && !(a.company ?? '').toLowerCase().includes(f.company)) return false;
    if (f.tier.includes('has_tier1') && !a.tier1_person) return false;
    if (f.tier.includes('has_tier2') && !a.tier2_person) return false;
    if (f.dateFrom && a.article_date && a.article_date < f.dateFrom) return false;
    if (f.dateTo && a.article_date && a.article_date > f.dateTo) return false;

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
    return `<p class="cmd-hint">Wpisz email, aby wygenerować komendę</p>`;
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

  const saved    = storedEmail(article.id, tier);
  const cmdHtml  = renderCommandSection(article.id, tier, saved, article);
  const inputId  = `email_${escAttr(article.id)}_t${tier}`;
  const tierClass = tier === 1 ? 'tier1' : 'tier2';

  return `
    <div class="person-block ${tierClass}"
         data-article-id="${escAttr(article.id)}"
         data-tier="${tier}">
      <div class="person-header">
        <span class="tier-label">Osoba Tier ${tier}</span>
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
        <input
          type="email"
          id="${inputId}"
          class="email-input"
          placeholder="Wpisz email osoby kontaktowej…"
          value="${escAttr(saved)}"
          data-article-id="${escAttr(article.id)}"
          data-tier="${tier}"
          autocomplete="off"
          spellcheck="false">
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
  const statusLabel = STATUS_LABELS[article.status] ?? article.status ?? '—';
  const statusClass = `badge-status-${article.status ?? 'new'}`;
  const dateStr     = formatDate(article.article_date);

  const tier1Html = renderPersonBlock(article, 1);
  const tier2Html = article.tier2_person ? renderPersonBlock(article, 2) : '';

  const noTier2 = !article.tier2_person;

  return `
<article class="card" data-id="${escAttr(article.id)}">
  <div class="card-badges">
    ${article.industry ? `<span class="badge badge-industry">${escHtml(article.industry)}</span>` : ''}
    <span class="badge ${statusClass}">${escHtml(statusLabel)}</span>
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
  ${noTier2 ? '' : ''}

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
// Email input handling (event delegation on #cards)
// ============================================================

function handleEmailInput(e) {
  const input = e.target;
  if (!input.classList.contains('email-input')) return;

  const articleId = input.dataset.articleId;
  const tier      = parseInt(input.dataset.tier, 10);
  const email     = input.value.trim();

  // Persist in localStorage
  const key = emailKey(articleId, tier);
  email ? localStorage.setItem(key, email) : localStorage.removeItem(key);

  // Find the article
  const article = allArticles.find(a => a.id === articleId);
  if (!article) return;

  // Update command block
  const cmdEl = document.getElementById(`cmd_${articleId}_t${tier}`);
  if (cmdEl) {
    cmdEl.innerHTML = renderCommandSection(articleId, tier, email, article);
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
    'tier', 'full_name', 'job_title', 'email',
    'industry', 'status', 'reason', 'context',
  ];

  const rows = [];

  filteredArticles.forEach(a => {
    const addRow = (tier, person, position) => {
      if (!person) return;
      const email = storedEmail(a.id, tier) || (tier === 1 ? a.contact_email ?? '' : '');
      rows.push({
        article_date: a.article_date ?? '',
        title:        a.title ?? '',
        source_name:  a.source_name ?? '',
        source_url:   a.source_url ?? '',
        company:      a.company ?? '',
        tier:         TIER_VALUES[tier],
        full_name:    person,
        job_title:    position ?? '',
        email,
        industry:     a.industry ?? '',
        status:       a.status ?? '',
        reason:       a.reason ?? '',
        context:      a.context ?? '',
      });
    };

    addRow(1, a.tier1_person, a.tier1_position);
    addRow(2, a.tier2_person, a.tier2_position);
  });

  if (rows.length === 0) {
    alert('Brak danych do eksportu. Upewnij się że artykuły mają przypisane osoby kontaktowe.');
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
  cardsEl.addEventListener('input',  handleEmailInput);
  cardsEl.addEventListener('click',  handleCopyClick);

  // Event delegation on pagination
  document.getElementById('pagination').addEventListener('click', handlePaginationClick);
});
