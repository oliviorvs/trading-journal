import { apiGet, state } from './config.js';
import { money, setLoading, showToast } from './utils.js';
import { chartColors } from './theme.js';

let periodRequestId = 0;

// Dernière réponse reçue (avec la clé de libellé associée), gardée pour
// recolorer le graphique au changement de thème sans refaire l'appel
// réseau — voir redrawPeriodTheme.
let _lastPeriodData = null;
let _lastPeriodLabelKey = null;

// ── Pagination du graphique de période ──────────────────────────────────────
// Le graphique défilait horizontalement : le canvas était dessiné à sa
// largeur « naturelle » (82px par période) dans un conteneur à débordement,
// avec un SECOND canvas collé à gauche qui ne servait qu'à reproduire l'axe Y
// resté immobile. Trois défauts : les barres hors champ étaient
// inatteignables autrement qu'à la molette ou au doigt (rien au clavier) ;
// un dégradé en bord droit était le seul indice qu'il restait des données ;
// et l'axe Y dupliqué devait être resynchronisé à la main (largeur, échelle,
// couleurs de thème) avec le graphique réel, à chaque rendu.
// À la place : une seule toile, responsive, qui tient toujours dans la carte,
// et une pagination dès que les périodes ne rentrent plus. Une page affiche
// autant de barres que la largeur en permet sans les écraser.
const MIN_BAR_SLOT = 64;   // largeur mini lisible d'une barre + son libellé
const MIN_PER_PAGE = 4;
let periodPage = 0;

function periodsPerPage() {
  const wrap = document.querySelector('.period-chart-wrap');
  const width = wrap ? wrap.clientWidth : 0;
  if (!width) return 12;
  // On retire la place prise par l'axe Y avant de compter les barres.
  const usable = Math.max(0, width - 96);
  return Math.max(MIN_PER_PAGE, Math.floor(usable / MIN_BAR_SLOT));
}

function pageCount(total) {
  return Math.max(1, Math.ceil(total / periodsPerPage()));
}

export function changePeriodPage(delta) {
  if (!_lastPeriodData) return;
  const pages = pageCount(_lastPeriodData.length);
  const next = Math.min(pages - 1, Math.max(0, periodPage + delta));
  if (next === periodPage) return;
  periodPage = next;
  renderPeriodChart(_lastPeriodData, _lastPeriodLabelKey, { keepPage: true });
}

function renderPager(total, from, to) {
  const pager = document.getElementById('period-pager');
  const label = document.getElementById('period-pager-label');
  const prev = document.getElementById('period-prev');
  const next = document.getElementById('period-next');
  if (!pager || !label || !prev || !next) return;
  const pages = pageCount(total);
  // Tout tient en une seule vue : aucune commande affichée, rien à
  // comprendre pour l'utilisateur qui a peu d'historique.
  pager.hidden = pages <= 1;
  if (pager.hidden) return;
  label.textContent = `${from + 1}–${to} sur ${total}`;
  prev.disabled = periodPage === 0;
  next.disabled = periodPage >= pages - 1;
}

function renderPeriodSummary(data) {
  const container = document.getElementById('period-summary');
  if (!container) return;
  const totals = data.reduce((summary, period) => {
    summary.trades += period.trades || 0;
    summary.wins += period.wins || 0;
    summary.losses += period.losses || 0;
    summary.breakeven += period.breakeven || 0;
    return summary;
  }, { trades: 0, wins: 0, losses: 0, breakeven: 0 });
  const winRate = totals.trades ? (totals.wins / totals.trades * 100).toFixed(1) : '0.0';
  container.innerHTML = `
    <div class="period-summary-card"><div class="metric-label">Win rate</div><div class="metric-value">${winRate}%</div><div class="metric-sub">sur la période affichée</div></div>
    <div class="period-summary-card"><div class="metric-label">Trades</div><div class="metric-value">${totals.trades}</div><div class="metric-sub">total clôturé</div></div>
    <div class="period-summary-card"><div class="metric-label">Gagnants</div><div class="metric-value pos">${totals.wins}</div><div class="metric-sub">trades positifs</div></div>
    <div class="period-summary-card"><div class="metric-label">Perdants</div><div class="metric-value neg">${totals.losses}</div><div class="metric-sub">trades négatifs${totals.breakeven ? ` · ${totals.breakeven} BE` : ''}</div></div>`;
}

function renderSelectedPeriod(period, labelKey) {
  const selection = document.getElementById('period-selection');
  if (!selection) return;
  const label = period[labelKey];
  selection.classList.add('active');
  selection.textContent = `${label} · P&L ${money(period.pnl)} · Win rate ${period.win_rate}% · ${period.trades} trade(s) · ${period.wins} gagnant(s) · ${period.losses} perdant(s)${period.breakeven ? ` · ${period.breakeven} breakeven` : ''}`;
}

function renderPeriodChart(data, labelKey, options = {}) {
  const canvas = document.getElementById('periodChart');
  const empty = document.getElementById('period-empty');
  const title = document.getElementById('period-chart-title');
  const selection = document.getElementById('period-selection');
  const pager = document.getElementById('period-pager');
  if (!canvas || !empty || !title) return;
  title.textContent = labelKey === 'week' ? 'P&L par semaine' : 'P&L par mois';

  if (state.periodChart) {
    state.periodChart.destroy();
    state.periodChart = null;
  }
  if (!data.length) {
    canvas.style.display = 'none';
    empty.classList.add('visible');
    if (pager) pager.hidden = true;
    if (selection) {
      selection.classList.remove('active');
      selection.textContent = 'Aucune période à sélectionner.';
    }
    return;
  }
  canvas.style.display = '';
  empty.classList.remove('visible');

  // Ouverture sur la page la PLUS RÉCENTE : le backend renvoie les périodes
  // par ordre chronologique, et c'est la fin de l'historique qu'on vient
  // regarder, pas son début.
  const perPage = periodsPerPage();
  const pages = Math.max(1, Math.ceil(data.length / perPage));
  if (!options.keepPage) periodPage = pages - 1;
  periodPage = Math.min(pages - 1, Math.max(0, periodPage));
  const from = periodPage * perPage;
  const pageData = data.slice(from, from + perPage);
  renderPager(data.length, from, from + pageData.length);

  const c = chartColors();
  if (selection) {
    selection.classList.remove('active');
    selection.textContent = 'Cliquez sur une barre pour afficher le détail de la période.';
  }
  const labels = pageData.map(period => period[labelKey]);
  const values = pageData.map(period => period.pnl);
  // L'échelle est calculée sur TOUT l'historique, pas sur la page affichée :
  // sinon une même barre changerait de hauteur d'une page à l'autre et deux
  // périodes ne seraient plus comparables de vue.
  const allValues = data.map(period => period.pnl);
  const dataMin = Math.min(0, ...allValues);
  const dataMax = Math.max(0, ...allValues);
  const padding = (dataMax - dataMin || Math.max(Math.abs(dataMax), 1)) * 0.12;

  state.periodChart = new Chart(canvas, {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'P&L',
        data: values,
        backgroundColor: values.map(value => value >= 0 ? c.posSoft : c.negSoft),
        borderColor: values.map(value => value >= 0 ? c.pos : c.neg),
        borderWidth: 1,
        borderRadius: 3,
        maxBarThickness: 52,
        barPercentage: 0.72,
        categoryPercentage: 0.8,
      }],
    },
    options: {
      indexAxis: 'x',
      responsive: true,
      maintainAspectRatio: false,
      onClick: (_event, elements, chart) => {
        if (!elements.length) return;
        const index = elements[0].index;
        const period = pageData[index];
        const dataset = chart.data.datasets[0];
        // La barre sélectionnée passe au bleu de marque : c'est un état
        // d'interface (« vous regardez celle-ci »), pas un résultat.
        dataset.borderColor = pageData.map((item, itemIndex) => itemIndex === index ? c.primary : (item.pnl >= 0 ? c.pos : c.neg));
        dataset.borderWidth = pageData.map((_item, itemIndex) => itemIndex === index ? 4 : 1);
        chart.update();
        renderSelectedPeriod(period, labelKey);
      },
      scales: {
        x: { grid: { display: false }, ticks: { color: c.text, font: { family: 'DM Mono', size: 11 }, maxRotation: 45, minRotation: 45, autoSkip: false } },
        y: {
          min: dataMin - padding,
          max: dataMax + padding,
          border: { display: false },
          grid: { color: c.grid },
          ticks: { color: c.muted, font: { family: 'DM Mono' }, callback: value => money(value) },
        },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: context => ` P&L : ${money(context.parsed.y)}`,
            afterLabel: context => {
              const period = pageData[context.dataIndex];
              return [
                `Win rate : ${period.win_rate}%`,
                `Trades : ${period.trades}`,
                `Gagnants : ${period.wins}`,
                `Perdants : ${period.losses}`,
                ...(period.breakeven ? [`Breakeven : ${period.breakeven}`] : []),
              ];
            },
          },
        },
      },
    },
  });
}

// Le nombre de barres par page dépend de la largeur disponible : on repagine
// au redimensionnement, en restant sur la page courante.
let resizeTimer = null;
window.addEventListener('resize', () => {
  if (!_lastPeriodData) return;
  const panel = document.getElementById('page-week');
  if (!panel || !panel.classList.contains('active')) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => {
    renderPeriodChart(_lastPeriodData, _lastPeriodLabelKey, { keepPage: true });
  }, 150);
});

async function loadPeriodData(endpoint, labelKey) {
  const requestId = ++periodRequestId;
  const wrap = document.querySelector('.period-chart-wrap');
  setLoading(wrap, true);
  try {
    const data = await apiGet(`/performance/${endpoint}`);
    if (requestId !== periodRequestId) return;
    _lastPeriodData = data;
    _lastPeriodLabelKey = labelKey;
    renderPeriodSummary(data);
    renderPeriodChart(data, labelKey);
  } catch (error) {
    console.warn(endpoint, error);
    showToast(`Données de période indisponibles : ${error.message}`);
  } finally {
    if (requestId === periodRequestId) setLoading(wrap, false);
  }
}

export function redrawPeriodTheme() {
  if (_lastPeriodData) renderPeriodChart(_lastPeriodData, _lastPeriodLabelKey, { keepPage: true });
}

export function loadWeek() {
  return loadPeriodData('by-week', 'week');
}

export function loadMonthly() {
  return loadPeriodData('monthly', 'month');
}

export function loadPeriod() {
  return state.currentPeriod === 'week' ? loadWeek() : loadMonthly();
}

export function switchPeriod(period, btn) {
  state.currentPeriod = period;
  document.querySelectorAll('#page-week .tab').forEach(tab => tab.classList.remove('active'));
  btn.classList.add('active');
  loadPeriod();
}
