'use strict';

let histChart = null;

const SEV_COLOR_MAP = {
  severe:      '#ff3030',
  significant: '#ff8c00',
  minor:       '#ffd700',
  normal:      '#2d7d4a',
};

function renderHistoryChart(history) {
  const ctx = document.getElementById('hist-chart');
  if (!ctx) return;

  if (histChart) {
    histChart.destroy();
    histChart = null;
  }

  if (!history || history.length === 0) {
    ctx.parentElement.innerHTML = '<div class="empty">No historical data</div>';
    return;
  }

  // Sort by date and fill last 30 days
  const sorted = [...history].sort((a, b) => a.date.localeCompare(b.date));
  const last30 = _last30Days();
  const byDate  = Object.fromEntries(sorted.map(d => [d.date, d]));

  const labels = last30.map(d => d.slice(5));   // MM-DD
  const scores = last30.map(d => byDate[d] ? byDate[d].score : 0);
  const colors = last30.map(d => {
    if (!byDate[d]) return 'rgba(30,60,90,0.4)';
    return SEV_COLOR_MAP[byDate[d].status] || '#3b82f6';
  });

  histChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        data:            scores,
        backgroundColor: colors,
        borderRadius:    2,
        borderSkipped:   false,
      }],
    },
    options: {
      responsive:          true,
      maintainAspectRatio: false,
      animation:           false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => 'Score: ' + ctx.parsed.y.toFixed(1),
          },
        },
      },
      scales: {
        x: {
          ticks:  { color: '#7090b0', font: { size: 10 }, maxRotation: 0, maxTicksLimit: 10 },
          grid:   { color: 'rgba(28,46,69,0.6)' },
        },
        y: {
          min:    0,
          max:    100,
          ticks:  { color: '#7090b0', font: { size: 10 }, stepSize: 25 },
          grid:   { color: 'rgba(28,46,69,0.6)' },
        },
      },
    },
  });
}

function _last30Days() {
  const out = [];
  const d   = new Date();
  for (let i = 29; i >= 0; i--) {
    const t = new Date(d);
    t.setDate(d.getDate() - i);
    out.push(t.toISOString().slice(0, 10));
  }
  return out;
}
