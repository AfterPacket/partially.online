'use strict';

const SEV_ORDER = { severe: 4, significant: 3, minor: 2, normal: 1 };

// ── State ─────────────────────────────────────────────────────────────────────
let allEvents = [];
let refreshTimer = null;
let currentDetailCode = null;
let currentDetailShare = null;
let siteHashtag = '';   // served by /api/status (SITE_HASHTAG env var)

// ── Banners ────────────────────────────────────────────────────────────────────
const BANNER_ICONS = { info: 'ℹ', warning: '⚠', success: '✓' };

function _dismissedBannerIds() {
  try { return JSON.parse(localStorage.getItem('dismissed_banners') || '[]'); }
  catch { return []; }
}

function _dismissBannerId(id) {
  const ids = _dismissedBannerIds();
  if (!ids.includes(id)) { ids.push(id); localStorage.setItem('dismissed_banners', JSON.stringify(ids)); }
}

async function loadBanners() {
  const data = await apiFetch('/api/banners');
  if (!data || !data.banners) return;
  const dismissed = _dismissedBannerIds();
  const stack = document.getElementById('banner-stack');
  // Only render banners the user hasn't dismissed
  const visible = data.banners.filter(b => !dismissed.includes(b.id));
  stack.innerHTML = visible.map(b => {
    const icon = BANNER_ICONS[b.level] || BANNER_ICONS.info;
    // Build message safely — only allow links, escape everything else
    const safe = esc(b.message).replace(/\[([^\]]+)\]\(([^)]+)\)/g, (_, text, href) => {
      // Only allow http/https links
      const url = href.startsWith('http') ? href : '';
      return url ? '<a href="' + esc(url) + '" target="_blank" rel="noopener">' + esc(text) + '</a>' : esc(text);
    });
    return '<div class="banner banner-level-' + safeClass(b.level) + '" data-banner-id="' + b.id + '"' +
      '><span class="banner-icon"' + '>' + icon + '</span>' +
      '<div class="banner-body"' + '>' + safe + '</div>' +
      '<button class="banner-close" data-banner-id="' + esc(String(b.id)) + '">&times;</button>' +
      '</div>';
  }).join('');
}

function _setupBanners() {
  document.getElementById('banner-stack').addEventListener('click', (e) => {
    const btn = e.target.closest('.banner-close');
    if (!btn) return;
    const id = parseInt(btn.dataset.bannerId, 10);
    _dismissBannerId(id);
    const row = btn.closest('.banner');
    if (row) row.remove();
  });
}

// ── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  loadAll();
  loadBanners();
  _setupResolvedBar();
  _setupShareMenu();
  _setupBanners();

  document.getElementById('dp-close').addEventListener('click', closeDetail);
  document.getElementById('filter-sev').addEventListener('change', renderEvents);
  document.getElementById('filter-type').addEventListener('change', renderEvents);
  document.getElementById('hist-days').addEventListener('change', () => {
    if (currentDetailCode) loadCountryHistory(currentDetailCode);
  });
  document.getElementById('hist-category').addEventListener('change', () => {
    if (currentDetailCode) loadCountryHistory(currentDetailCode);
  });

  // Event/resolved cards are built from server data as HTML strings, so
  // they can't carry inline onclick="" attributes -- the CSP's script-src
  // has no 'unsafe-inline', and browsers silently block inline handlers
  // under it. Delegate from the (static, listener-attached-once) containers
  // instead, keyed off a data-country attribute on each card.
  document.getElementById('event-list').addEventListener('click', (e) => {
    const shareBtn = e.target.closest('.share-btn');
    if (shareBtn) { openShareMenu(shareBtn, shareBtn.dataset); return; }
    const card = e.target.closest('.event-card');
    if (card && card.dataset.country) window.showCountryDetail(card.dataset.country);
  });
  document.getElementById('resolved-list').addEventListener('click', (e) => {
    const shareBtn = e.target.closest('.share-btn');
    if (shareBtn) { openShareMenu(shareBtn, shareBtn.dataset); return; }
    const card = e.target.closest('.resolved-card');
    if (card && card.dataset.country) window.showCountryDetail(card.dataset.country);
  });

  // Deep link: ?country=CODE opens that country's detail panel on load,
  // so a shared link actually lands on the relevant place, not just the
  // homepage.
  const deepLinkCountry = new URLSearchParams(location.search).get('country');
  if (deepLinkCountry && /^[a-zA-Z]{2,3}$/.test(deepLinkCountry))
    window.showCountryDetail(deepLinkCountry.toUpperCase());

  // Auto-refresh every 5 minutes
  // Auto-refresh every 60 s — lightweight API poll, backend collects on its own schedule
  refreshTimer = setInterval(loadAll, 60 * 1000);
});

// ── Data loading ──────────────────────────────────────────────────────────────
async function loadAll() {
  try {
    const [status, events, countries] = await Promise.all([
      apiFetch('/api/status'),
      apiFetch('/api/events?limit=200'),
      apiFetch('/api/countries'),
    ]);

    if (status)    updateHeader(status);
    if (countries) updateMapColors(countries.countries || []);
    if (events) {
      allEvents = events.events || [];
      renderEvents();
    }
    // Load resolved events in parallel (non-blocking)
    apiFetch('/api/events/resolved?days=7').then(r => {
      if (r) renderResolvedBar(r.events || []);
    });
  } catch (e) {
    console.error('loadAll failed:', e);
  }
}

// ── Header ────────────────────────────────────────────────────────────────────
function updateHeader(s) {
  siteHashtag = s.site_hashtag || '';
  document.getElementById('val-active').textContent = s.active_events;
  document.getElementById('val-severe').textContent = s.severe_events;
  const d = new Date(s.last_updated);
  document.getElementById('hstat-time').textContent =
    'Updated ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

// ── Events list ───────────────────────────────────────────────────────────────
function renderEvents() {
  const sevFilter  = document.getElementById('filter-sev').value;
  const typeFilter = document.getElementById('filter-type').value;

  let filtered = allEvents.filter(ev => {
    if (sevFilter  && ev.severity   !== sevFilter)  return false;
    if (typeFilter && ev.event_type !== typeFilter) return false;
    return true;
  });

  // Sort: severity desc, then time desc
  filtered.sort((a, b) =>
    (SEV_ORDER[b.severity] - SEV_ORDER[a.severity]) ||
    new Date(b.start_time) - new Date(a.start_time)
  );

  const el = document.getElementById('event-list');

  if (!filtered.length) {
    el.innerHTML = '<div class="empty">No events match filters</div>';
    return;
  }

  el.innerHTML = filtered.map(ev => {
    const time = ev.start_time ? _relTime(new Date(ev.start_time)) : '';
    return '<div class="event-card" data-country="' + esc(ev.country_code) + '">'+
      '<div class="ec-top">'+
        '<span class="sev-pip sev-' + safeClass(ev.severity) + '"></span>'+
        '<span class="ec-title">' + esc(ev.title) + '</span>'+
      '</div>'+
      '<div class="ec-meta">'+
        '<span class="type-tag">' + esc(ev.event_type) + '</span>'+
        (ev.region_name ? '<span class="region-tag">' + esc(ev.region_name) + '</span>' : '')+
        '<span>' + esc(ev.country_name) + '</span>'+
        '<span class="source-tag">' + esc(ev.source.toUpperCase()) + '</span>'+
        (ev.probe_confirmed ? '<span class="probe-tag">&#10003; confirmed</span>' : '')+
        '<span style="margin-left:auto">' + time + '</span>'+
        _shareBtnHTML(ev)+
      '</div>'+
    '</div>';
  }).join('');
}

// Small pill button carrying everything openShareMenu() needs in data-*
// attributes, so the delegated click handler doesn't need to look the
// event back up from allEvents (which doesn't hold resolved/detail-panel
// events anyway).
function _shareBtnHTML(ev) {
  return '<button class="share-btn" data-title="' + esc(ev.title) + '" '+
    'data-severity="' + esc(ev.severity) + '" data-type="' + esc(ev.event_type) + '" '+
    'data-source="' + esc(ev.source) + '" data-country="' + esc(ev.country_code) + '" '+
    'data-country-name="' + esc(ev.country_name || '') + '" '+
    'data-region="' + esc(ev.region_name || '') + '">Share</button>';
}

// ── Country detail panel ──────────────────────────────────────────────────────
window.showCountryDetail = async function(code) {
  // Validate country code to prevent path traversal
  if (!/^[a-zA-Z]{2,3}$/.test(code)) return;
  if (window.focusCountry) window.focusCountry(code);
  currentDetailCode = code;

  const panel = document.getElementById('detail-panel');
  const data  = await apiFetch('/api/countries/' + code);
  if (!data) return;

  document.getElementById('dp-title').textContent = data.name + ' (' + data.code + ')';

  const badge = document.getElementById('dp-badge');
  badge.textContent  = data.status.charAt(0).toUpperCase() + data.status.slice(1);
  badge.className    = 'sev-badge sev-' + safeClass(data.status);

  currentDetailShare = {
    title: 'Internet status for ' + data.name,
    severity: data.status,
    type: (data.active_events && data.active_events.length) ? data.active_events[0].event_type : '',
    source: '',
    country: data.code,
    countryName: data.name,
    region: '',
  };

  // Events
  const evEl = document.getElementById('dp-events');
  if (!data.active_events || !data.active_events.length) {
    evEl.innerHTML = '<div class="empty">No active events</div>';
  } else {
    evEl.innerHTML = data.active_events.map(ev =>
      '<div class="detail-ev">'+
        '<div class="dev-title">' + esc(ev.title) + '</div>'+
        '<div class="dev-desc">'  + esc(ev.description) + '</div>'+
        '<div class="dev-meta">'+
          '<span class="type-tag">' + esc(ev.event_type) + '</span>'+
          (ev.region_name ? '<span class="region-tag">' + esc(ev.region_name) + '</span>' : '')+
          (ev.probe_confirmed ? '<span class="probe-tag">&#10003; probe confirmed</span>' : '<span class="probe-tag probe-unconfirmed">probe pending</span>')+
          (ev.source_url && ev.source_url.startsWith('http') ? '<a class="dev-link" href="'+esc(ev.source_url)+'" target="_blank" rel="noopener">View source &rarr;</a>' : '')+
        '</div>'+
      '</div>'
    ).join('');
  }

  // History chart
  renderHistoryChart(data.history || []);
  loadCountryHistory(code);

  panel.classList.remove('hidden');
};

function closeDetail() {
  document.getElementById('detail-panel').classList.add('hidden');
  currentDetailCode = null;
}

// ── Country history log (event-level, filterable by range/category) ──────────
async function loadCountryHistory(code) {
  const days     = document.getElementById('hist-days').value;
  const category = document.getElementById('hist-category').value;
  const qs = new URLSearchParams({ days });
  if (category) qs.set('category', category);

  const el   = document.getElementById('hist-table');
  const data = await apiFetch('/api/countries/' + code + '/history?' + qs.toString());
  if (currentDetailCode !== code) return;   // panel moved on to another country meanwhile

  if (!data || !data.events || !data.events.length) {
    el.innerHTML = '<div class="empty">No history in this range</div>';
    return;
  }

  el.innerHTML = data.events.map(ev => {
    const t = ev.start_time ? new Date(ev.start_time).toLocaleString([], {
      month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
    }) : '';
    const place  = ev.region_name ? (ev.region_name + ' — ' + ev.event_type) : ev.event_type;
    const values = (ev.actual_value != null && ev.baseline_value != null)
      ? Math.round(ev.actual_value) + ' vs ' + Math.round(ev.baseline_value)
      : '';
    const end       = ev.end_time ? new Date(ev.end_time) : (ev.resolved_at ? new Date(ev.resolved_at) : new Date());
    const dur       = ev.start_time ? _duration(new Date(ev.start_time), end) : '';
    const stateText = ev.is_active ? 'Active' : 'Resolved';
    const stateCls  = ev.is_active ? 'is-active' : 'is-resolved';
    return '<div class="hist-row">'+
      '<span class="hist-time">' + t + '</span>'+
      '<span class="hist-place">' + esc(place) + '</span>'+
      '<span class="hist-values">' + values + '</span>'+
      '<span class="hist-state ' + stateCls + '">' + stateText + (dur ? ' &middot; ' + dur : '') + '</span>'+
    '</div>';
  }).join('');
}

// ── Share menu ────────────────────────────────────────────────────────────────
// X and Bluesky both support simple "intent" URLs that pre-fill a compose
// box. Mastodon is federated -- there's no single share endpoint, but any
// standard Mastodon instance exposes /share?text=... once you know the
// user's home instance, so we ask once and remember it.
let shareTarget = null;

function _setupShareMenu() {
  const menu = document.getElementById('share-menu');

  document.getElementById('dp-share').addEventListener('click', (e) => {
    if (currentDetailShare) openShareMenu(e.currentTarget, currentDetailShare);
  });

  document.getElementById('share-mastodon').addEventListener('click', () => {
    if (!shareTarget) return;
    const remembered = localStorage.getItem('mastodonInstance') || 'mastodon.social';
    const entered = prompt('Your Mastodon instance (e.g. mastodon.social):', remembered);
    if (!entered) return;
    const instance = entered.trim().replace(/^https?:\/\//, '').replace(/\/.*$/, '');
    if (!instance) return;
    localStorage.setItem('mastodonInstance', instance);
    const tags = _shareHashtags(shareTarget);
    const text = _shareText(shareTarget) + ' ' + _shareUrl(shareTarget.country) + (tags ? ' ' + tags : '');
    window.open('https://' + instance + '/share?text=' + encodeURIComponent(text), '_blank', 'noopener');
    closeShareMenu();
  });

  document.getElementById('share-copy').addEventListener('click', () => {
    if (!shareTarget) return;
    navigator.clipboard.writeText(_shareUrl(shareTarget.country)).catch(() => {});
    const btn = document.getElementById('share-copy');
    btn.textContent = 'Copied!';
    setTimeout(() => { btn.textContent = 'Copy link'; }, 1200);
    setTimeout(closeShareMenu, 600);
  });

  document.addEventListener('click', (e) => {
    if (menu.classList.contains('hidden')) return;
    if (e.target.closest('.share-menu') || e.target.closest('.share-btn')) return;
    closeShareMenu();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeShareMenu();
  });
}

function _shareText(d) {
  // Guarantee region-scoped events name their region (mirrors backend
  // _display_title) — two regions of one country must not read identically.
  let title = d.title || '';
  if (d.region && !title.toLowerCase().includes(d.region.toLowerCase()))
    title += ' (' + d.region + ')';
  const parts = [title];
  if (d.severity || d.type) {
    parts.push('— ' + [d.severity, d.type].filter(Boolean).map(
      s => s.charAt(0).toUpperCase() + s.slice(1)
    ).join(' '));
  }
  if (d.source) parts.push('(source: ' + d.source.toUpperCase() + ')');
  return parts.join(' ') + '.';
}

function _shareUrl(countryCode) {
  return location.origin + '/?country=' + encodeURIComponent(countryCode);
}

// CamelCase a label into a hashtag: 'Jammu & Kashmir' -> '#JammuKashmir'.
// Mirrors _hashtag() in backend/alerts.py so shared and auto posts match.
function _hashtag(label) {
  if (!label) return '';
  const tag = label.replace(/[^\p{L}\p{N}]+/gu, ' ').trim().split(/\s+/)
    .map(w => w.charAt(0).toUpperCase() + w.slice(1)).join('');
  return tag ? '#' + tag : '';
}

function _shareHashtags(d) {
  const tags = [];
  if (siteHashtag) {
    const site = siteHashtag.trim();
    tags.push(site.startsWith('#') ? site : '#' + site);
  }
  [d.countryName, d.region, d.type].forEach(label => {
    const t = _hashtag(label);
    if (t && !tags.includes(t)) tags.push(t);
  });
  return tags.join(' ');
}

function openShareMenu(anchorEl, data) {
  shareTarget = {
    title: data.title, severity: data.severity, type: data.type,
    source: data.source, country: data.country,
    countryName: data.countryName || '', region: data.region || '',
  };
  const url  = _shareUrl(shareTarget.country);
  const text = _shareText(shareTarget);
  const tags = _shareHashtags(shareTarget);
  const tagSuffix = tags ? ' ' + tags : '';

  document.getElementById('share-x').href =
    'https://twitter.com/intent/tweet?text=' + encodeURIComponent(text + tagSuffix) + '&url=' + encodeURIComponent(url);
  document.getElementById('share-bsky').href =
    'https://bsky.app/intent/compose?text=' + encodeURIComponent(text + ' ' + url + tagSuffix);

  const menu = document.getElementById('share-menu');
  menu.classList.remove('hidden');
  const rect = anchorEl.getBoundingClientRect();
  const menuW = menu.offsetWidth || 170;
  menu.style.top  = Math.min(rect.bottom + 6, window.innerHeight - 180) + 'px';
  menu.style.left = Math.min(rect.left, window.innerWidth - menuW - 8) + 'px';
}

function closeShareMenu() {
  document.getElementById('share-menu').classList.add('hidden');
  shareTarget = null;
}

// ── Resolved events bar ──────────────────────────────────────────────────────

function _setupResolvedBar() {
  document.getElementById('resolved-toggle').addEventListener('click', () => {
    const bar = document.getElementById('resolved-bar');
    bar.classList.toggle('collapsed');
  });
}

function renderResolvedBar(events) {
  const bar   = document.getElementById('resolved-bar');
  const list  = document.getElementById('resolved-list');
  const count = document.getElementById('resolved-count');

  if (!events.length) {
    bar.classList.add('hidden');
    return;
  }

  bar.classList.remove('hidden');
  count.textContent = events.length + ' in last 7 days';

  list.innerHTML = events.map(ev => {
    const resolvedAgo = ev.resolved_at ? _relTime(new Date(ev.resolved_at)) : '';
    const duration    = (ev.start_time && ev.end_time)
      ? _duration(new Date(ev.start_time), new Date(ev.end_time))
      : (ev.start_time && ev.resolved_at)
      ? _duration(new Date(ev.start_time), new Date(ev.resolved_at))
      : '';
    const place = ev.region_name ? ev.region_name + ', ' + ev.country_name : ev.country_name;
    return '<div class="resolved-card" data-country="' + esc(ev.country_code) + '">'+
      '<div class="rc-country">' + esc(place) + ' (' + esc(ev.country_code) + ')</div>'+
      '<div class="rc-type">' + esc(ev.event_type) + ' &mdash; ' + esc(ev.source.toUpperCase()) + '</div>'+
      '<div class="rc-time">&#10003; Resolved ' + resolvedAgo + '</div>'+
      (duration ? '<div class="rc-duration">Duration: ' + duration + '</div>' : '')+
      _shareBtnHTML(ev)+
    '</div>';
  }).join('');
}

function _duration(start, end) {
  const sec = Math.floor((end - start) / 1000);
  if (sec < 60)    return sec + 's';
  if (sec < 3600)  return Math.floor(sec / 60) + 'm';
  if (sec < 86400) return Math.floor(sec / 3600) + 'h ' + Math.floor((sec % 3600) / 60) + 'm';
  return Math.floor(sec / 86400) + 'd ' + Math.floor((sec % 86400) / 3600) + 'h';
}

// ── Helpers ───────────────────────────────────────────────────────────────────
async function apiFetch(url, opts) {
  try {
    const r = await fetch(url, opts);
    if (!r.ok) throw new Error(r.status);
    return r.json();
  } catch (e) {
    console.error('API error', url, e);
    return null;
  }
}

function esc(s) {
  if (!s) return '';
  return String(s)
    .replace(/&/g,'&amp;')
    .replace(/</g,'&lt;')
    .replace(/>/g,'&gt;')
    .replace(/"/g,'&quot;');
}

/** Strip anything that isn't a letter, digit, or hyphen — safe for CSS class names. */
function safeClass(s) {
  return String(s || '').replace(/[^a-zA-Z0-9-]/g, '');
}

function _relTime(date) {
  const sec = Math.floor((Date.now() - date) / 1000);
  if (sec < 60)   return sec + 's ago';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return Math.floor(sec/86400) + 'd ago';
}
