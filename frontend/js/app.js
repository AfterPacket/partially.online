'use strict';

const SEV_ORDER = { severe: 4, significant: 3, minor: 2, normal: 1 };

// ── State ─────────────────────────────────────────────────────────────────────
let allEvents = [];
let refreshTimer = null;
let currentDetailCode = null;

// ── Boot ──────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  initMap();
  loadAll();
  _setupResolvedBar();

  document.getElementById('dp-close').addEventListener('click', closeDetail);
  document.getElementById('filter-sev').addEventListener('change', renderEvents);
  document.getElementById('filter-type').addEventListener('change', renderEvents);
  document.getElementById('hist-days').addEventListener('change', () => {
    if (currentDetailCode) loadCountryHistory(currentDetailCode);
  });
  document.getElementById('hist-category').addEventListener('change', () => {
    if (currentDetailCode) loadCountryHistory(currentDetailCode);
  });

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
    return '<div class="event-card" onclick="showCountryDetail(\''+ev.country_code+'\')">'+
      '<div class="ec-top">'+
        '<span class="sev-pip sev-' + ev.severity + '"></span>'+
        '<span class="ec-title">' + esc(ev.title) + '</span>'+
      '</div>'+
      '<div class="ec-meta">'+
        '<span class="type-tag">' + esc(ev.event_type) + '</span>'+
        (ev.region_name ? '<span class="region-tag">' + esc(ev.region_name) + '</span>' : '')+
        '<span>' + esc(ev.country_name) + '</span>'+
        '<span class="source-tag">' + ev.source.toUpperCase() + '</span>'+
        (ev.probe_confirmed ? '<span class="probe-tag">&#10003; confirmed</span>' : '')+
        '<span style="margin-left:auto">' + time + '</span>'+
      '</div>'+
    '</div>';
  }).join('');
}

// ── Country detail panel ──────────────────────────────────────────────────────
window.showCountryDetail = async function(code) {
  if (window.focusCountry) window.focusCountry(code);
  currentDetailCode = code;

  const panel = document.getElementById('detail-panel');
  const data  = await apiFetch('/api/countries/' + code);
  if (!data) return;

  document.getElementById('dp-title').textContent = data.name + ' (' + data.code + ')';

  const badge = document.getElementById('dp-badge');
  badge.textContent  = data.status.charAt(0).toUpperCase() + data.status.slice(1);
  badge.className    = 'sev-badge sev-' + data.status;

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
          (ev.source_url ? '<a class="dev-link" href="'+esc(ev.source_url)+'" target="_blank" rel="noopener">View source &rarr;</a>' : '')+
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
    const end       = ev.resolved_at ? new Date(ev.resolved_at) : new Date();
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
    const duration    = (ev.start_time && ev.resolved_at)
      ? _duration(new Date(ev.start_time), new Date(ev.resolved_at))
      : '';
    const place = ev.region_name ? ev.region_name + ', ' + ev.country_name : ev.country_name;
    return '<div class="resolved-card" onclick="showCountryDetail(\''+ev.country_code+'\')">'+
      '<div class="rc-country">' + esc(place) + ' (' + ev.country_code + ')</div>'+
      '<div class="rc-type">' + esc(ev.event_type) + ' &mdash; ' + ev.source.toUpperCase() + '</div>'+
      '<div class="rc-time">&#10003; Resolved ' + resolvedAgo + '</div>'+
      (duration ? '<div class="rc-duration">Duration: ' + duration + '</div>' : '')+
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

function _relTime(date) {
  const sec = Math.floor((Date.now() - date) / 1000);
  if (sec < 60)   return sec + 's ago';
  if (sec < 3600) return Math.floor(sec/60) + 'm ago';
  if (sec < 86400) return Math.floor(sec/3600) + 'h ago';
  return Math.floor(sec/86400) + 'd ago';
}
