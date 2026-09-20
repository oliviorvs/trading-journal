import { apiGet, state } from './config.js';
import { money, setLoading, showToast } from './utils.js';
import { chartColors } from './theme.js';

// R-multiple signé, une décimale (ex. +8.6R, -1.2R) — même convention que
// le reste de l'app (voir trades.js/repartitions.js), simplement en une
// décimale ici pour rester lisible dans une cellule de calendrier.
function rFmt(r) {
  if (r == null || isNaN(r)) return null;
  return `${r >= 0 ? '+' : ''}${r.toFixed(1)}R`;
}

export async function buildCalendar() {
  const label = new Date(state.calYear, state.calMonth).toLocaleDateString('fr-FR', { month: 'long', year: 'numeric' });
  const monthLabel = label.charAt(0).toUpperCase() + label.slice(1);
  document.getElementById('cal-month-label').textContent = monthLabel;
  const periodEl = document.getElementById('cal-period-label');
  if (periodEl) periodEl.textContent = monthLabel;

  let data = { summary: { pnl: 0, r_sum: 0, trades: 0 }, days: {} };
  try {
    data = await apiGet(`/performance/calendar?year=${state.calYear}&month=${state.calMonth + 1}`);
  } catch(e) {
    console.warn('calendar', e);
    showToast(`Calendrier indisponible : ${e.message}`);
  }

  const { summary, days: dayMap } = data;
  const summaryEl = document.getElementById('cal-summary');
  if (summaryEl) {
    const rTxt = rFmt(summary.r_sum);
    summaryEl.textContent = [money(summary.pnl, 0), rTxt, `${summary.trades} trade${summary.trades > 1 ? 's' : ''}`]
      .filter(Boolean).join(' · ');
  }

  const firstDay = new Date(state.calYear, state.calMonth, 1).getDay();
  const offset = firstDay === 0 ? 6 : firstDay - 1;
  const daysInMonth = new Date(state.calYear, state.calMonth + 1, 0).getDate();
  const dayHeads = ['Lun','Mar','Mer','Jeu','Ven','Sam','Dim'];

  let html = dayHeads.map(d => `<div class="cal-head">${d}</div>`).join('');
  for (let i = 0; i < offset; i++) html += `<div class="cal-cell empty"></div>`;
  for (let d = 1; d <= daysInMonth; d++) {
    const day = dayMap[String(d)];
    if (!day || !day.trades) {
      html += `<div class="cal-cell no-trade">
        <div class="cal-cell-top"><span class="cal-day-num">${d}</span></div>
        <div class="cal-no-trade">Aucun trade</div>
      </div>`;
      continue;
    }
    const sign = day.pnl >= 0 ? 'pos' : 'neg';
    const rTxt = rFmt(day.r_sum);
    const metaTxt = [rTxt, `${day.trades} trade${day.trades > 1 ? 's' : ''}`].filter(Boolean).join(' · ');
    const winRateTxt = day.win_rate != null ? `${day.win_rate}% win rate` : '';
    html += `<div class="cal-cell ${sign}">
      <div class="cal-cell-top"><span class="cal-day-num">${d}</span><span class="cal-dot ${sign}"></span></div>
      <span class="cal-pnl ${sign}">${money(day.pnl, 0)}</span>
      <span class="cal-meta">${metaTxt}</span>
      <span class="cal-winrate">${winRateTxt}</span>
    </div>`;
  }
  document.getElementById('cal-grid').innerHTML = html;
}

export function changeMonth(delta) {
  state.calMonth += delta;
  if (state.calMonth > 11) { state.calMonth = 0; state.calYear++; }
  if (state.calMonth < 0) { state.calMonth = 11; state.calYear--; }
  buildCalendar();
}

// ── Stats étendues : R-multiples, risque, chaleur horaire, perf. glissante ──

// Dernière réponse reçue, gardée pour recolorer les graphiques (changement
// de thème) sans refaire l'appel réseau — voir redrawCalendarTheme.
let _lastExtendedStats = null;

export async function loadExtendedStats() {
  const wraps = ['chartRHist', 'chartRiskDist', 'chartRolling'].map(id => document.getElementById(id)?.closest('.chart-wrap'));
  wraps.forEach(w => setLoading(w, true));
  try {
    const d = await apiGet('/performance/extended-stats');
    _lastExtendedStats = d;
    renderRHistogram(d.r_histogram);
    renderRiskDistribution(d.risk_distribution);
    renderHeatmap(d.heatmap);
    renderRolling(d.rolling);
  } catch (e) {
    console.warn('extended-stats', e);
    showToast(`Statistiques étendues indisponibles : ${e.message}`);
  }
  finally { wraps.forEach(w => setLoading(w, false)); }
}

// Le dégradé de la heatmap horaire est calculé à partir de couleurs fixes
// (voir heatColor) indépendantes du thème clair/sombre : seuls les deux
// histogrammes et le graphique glissant ont besoin d'être redessinés.
export function redrawCalendarTheme() {
  if (!_lastExtendedStats) return;
  renderRHistogram(_lastExtendedStats.r_histogram);
  renderRiskDistribution(_lastExtendedStats.risk_distribution);
  renderRolling(_lastExtendedStats.rolling);
}

function avgLabel(buckets) {
  const total = buckets.reduce((s, b) => s + b.count, 0);
  return total ? `${total} trade(s) avec risque renseigné` : 'Aucun trade avec R-multiple calculable (risque non renseigné)';
}

// Récapitulatif texte "bucket : count (pct%)" affiché sous les histogrammes —
// donne une lecture directe des pourcentages sans avoir à survoler chaque barre.
function bucketBreakdown(buckets) {
  const withData = buckets.filter(b => b.count > 0);
  if (!withData.length) return '';
  return withData.map(b => `${b.bucket} : ${b.count} (${b.pct}%)`).join('  ·  ');
}

function renderRHistogram(buckets) {
  document.getElementById('r-hist-sub').textContent = avgLabel(buckets);
  document.getElementById('r-hist-breakdown').textContent = bucketBreakdown(buckets);
  const c = chartColors();
  if (state.rHistChart) state.rHistChart.destroy();
  state.rHistChart = new Chart(document.getElementById('chartRHist'), {
    type: 'bar',
    data: {
      labels: buckets.map(b => b.bucket),
      datasets: [{
        data: buckets.map(b => b.count),
        backgroundColor: buckets.map(b => b.bucket.startsWith('-') || b.bucket.startsWith('<-') ? c.negSoft : c.posSoft),
        borderColor: buckets.map(b => b.bucket.startsWith('-') || b.bucket.startsWith('<-') ? c.neg : c.pos),
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false }, datalabels: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const b = buckets[ctx.dataIndex];
          return `${b.count} trade(s) — ${b.pct}%`;
        } } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 10, family: 'DM Mono' } } },
        y: { border: { display: false }, grid: { color: c.grid }, ticks: { precision: 0, font: { family: 'DM Mono' } } },
      },
    },
  });
}

function renderRiskDistribution(buckets) {
  document.getElementById('risk-dist-breakdown').textContent = bucketBreakdown(buckets);
  const c = chartColors();
  if (state.riskDistChart) state.riskDistChart.destroy();
  state.riskDistChart = new Chart(document.getElementById('chartRiskDist'), {
    type: 'bar',
    data: {
      labels: buckets.map(b => b.bucket),
      // Distribution du RISQUE : violet, la troisième couleur de la charte —
      // ni un gain ni une perte, une exposition.
      datasets: [{ data: buckets.map(b => b.count), backgroundColor: c.violetSoft, borderColor: c.violet, borderWidth: 1.5 }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: (ctx) => {
          const b = buckets[ctx.dataIndex];
          return `${b.count} trade(s) — ${b.pct}%`;
        } } },
      },
      scales: {
        x: { grid: { display: false }, ticks: { font: { size: 10, family: 'DM Mono' } } },
        y: { border: { display: false }, grid: { color: c.grid }, ticks: { precision: 0, font: { family: 'DM Mono' } } },
      },
    },
  });
}

function heatColor(pnl, maxAbs) {
  if (!pnl) return '';
  const intensity = maxAbs ? Math.min(1, Math.abs(pnl) / maxAbs) : 0;
  const alpha = 0.15 + intensity * 0.55;
  return pnl > 0 ? `rgba(16,185,129,${alpha.toFixed(2)})` : `rgba(239,68,68,${alpha.toFixed(2)})`;
}

function renderHeatmap(cells) {
  const days = ['Lun', 'Mar', 'Mer', 'Jeu', 'Ven', 'Sam', 'Dim'];
  const hours = Array.from({ length: 24 }, (_, hour) => String(hour).padStart(2, '0'));
  const maxAbs = Math.max(1, ...cells.map(c => Math.abs(c.pnl)));

  let html = '<div class="heat-label"></div>' + hours.map(h => `<div class="heat-head">${h}</div>`).join('');
  for (const day of days) {
    html += `<div class="heat-label">${day}</div>`;
    for (const hour of hours) {
      const cell = cells.find(c => c.day === day && c.hour === hour);
      const pnl = cell ? cell.pnl : 0;
      const bg = heatColor(pnl, maxAbs);
      const title = pnl ? `${day} ${hour}h — ${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}` : `${day} ${hour}h`;
      html += `<div class="heat-cell" style="${bg ? `background:${bg}` : ''}" title="${title}"></div>`;
    }
  }
  document.getElementById('heatmap-grid').innerHTML = html;
}

function renderRolling(rolling) {
  const c = chartColors();
  if (state.rollingChart) state.rollingChart.destroy();
  if (!rolling.length) return;
  state.rollingChart = new Chart(document.getElementById('chartRolling'), {
    type: 'line',
    data: {
      labels: rolling.map(r => r.index),
      datasets: [
        { label: 'WR%', data: rolling.map(r => r.win_rate), borderColor: c.pos, pointRadius: 0, borderWidth: 2, tension: 0.3, yAxisID: 'y' },
        { label: 'R cum.', data: rolling.map(r => r.r_cumulative), borderColor: c.primary, pointRadius: 0, borderWidth: 2, tension: 0.3, yAxisID: 'y1' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: c.muted } } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: { family: 'DM Mono' } } },
        y: { position: 'left', border: { display: false }, grid: { color: c.grid }, ticks: { callback: v => v + '%', font: { family: 'DM Mono' } } },
        y1: { position: 'right', border: { display: false }, grid: { display: false }, ticks: { callback: v => v + 'R', font: { family: 'DM Mono' } } },
      },
    },
  });
}
