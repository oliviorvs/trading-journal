import { apiGet, state } from './config.js';
import { money, moneyAbs, curSym, fmtDuration, currentSymbol, setLoading, escapeHtml, showToast } from './utils.js';
import { loadTrades } from './trades.js';
import { chartColors, chartFont } from './theme.js';

// Dernières données reçues du backend, gardées pour pouvoir redessiner les
// graphiques (changement de thème) sans reprovoquer un aller-retour réseau
// inutile (correctif audit UI/UX — voir redrawDashboardTheme ci-dessous et
// son usage dans app.js).
let _lastStats = null;
let _lastEquityData = null;
let _lastRealData = null;

// ── Data loading ──────────────────────────────────────────────────────────────

export async function loadAll() {
  // Purge du cache de la page Trades (30 s) : un trade modifié ou supprimé ne
  // doit pas continuer d'apparaître. La clé de ce cache porte en plus
  // l'« époque » du compte (voir trades.js), donc aucune page ne peut être
  // resservie d'un compte à l'autre même sans cette purge.
  state.tradesCache.clear();
  await Promise.all([loadMetrics(), loadTrades(), loadEquity(), loadRealEquity(), loadMovementsCard()]);
  // Le dashboard et la page Trades ne sont pas les seuls écrans à l'image des
  // données : Calendrier, Semaine/Mois, Symboles, Répartitions et Analyzer ne
  // se chargent qu'au clic sur leur onglet. Sans ce rappel, changer de compte
  // (ou resynchroniser) depuis l'un d'eux laissait à l'écran les chiffres du
  // compte ou de l'état précédent, sans rien pour le signaler.
  // Import dynamique : dashboard.js est importé par navigation.js en cascade,
  // un import statique créerait un cycle.
  try {
    const { refreshActivePanel } = await import('./navigation.js');
    await refreshActivePanel();
  } catch (e) {
    console.warn('rechargement du panneau actif', e);
  }
}

// Journal des mouvements de capital (Réglages, phase 6). Import dynamique :
// movements.js dépend lui-même de ce module (loadAll), un import statique
// créerait un cycle.
async function loadMovementsCard() {
  try {
    const mod = await import('./movements.js');
    await mod.loadMovements();
  } catch (e) {
    console.warn('mouvements de capital', e);
  }
}

// Changement de symbole ou de période sur le dashboard : on ne recharge que
// les métriques/le graphique, pas la liste complète des trades (qui ne
// change pas et n'a pas besoin de reconstruire les listes déroulantes de
// filtres à chaque clic — c'est ce qui provoquait le filtre "qui ne
// fonctionnait pas", le select se réinitialisant pendant le rechargement).
export async function applyDashboardFilters() {
  await Promise.all([loadMetrics(), loadEquity(), loadRealEquity()]);
}

// Convertit le filtre de période du dashboard ('1m'/'3m'/'6m'/'all') en date
// de départ à envoyer au backend. 'all' → pas de borne (historique complet).
function dashboardDateFrom() {
  const months = { '1m': 1, '3m': 3, '6m': 6 }[state.dashboardPeriod];
  if (!months) return null;
  const d = new Date();
  d.setMonth(d.getMonth() - months);
  return d.toISOString().slice(0, 10);
}

export function switchDashboardPeriod(period, btn) {
  state.dashboardPeriod = period;
  document.querySelectorAll('#dashboard-period-filter .filter-btn').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  applyDashboardFilters();
}

export async function loadMetrics() {
  const metricsEl = document.getElementById('metrics');
  const metrics2El = document.getElementById('metrics-2');
  const highlightsEl = document.getElementById('highlights');
  const highlightsSideEl = document.getElementById('highlights-secondary');
  const donutWrap = document.querySelector('.period-results-chart-wrap');
  setLoading(metricsEl, true);
  setLoading(metrics2El, true);
  setLoading(highlightsEl, true);
  setLoading(highlightsSideEl, true);
  setLoading(donutWrap, true);
  try {
    const params = new URLSearchParams();
    const sym = currentSymbol();
    if (sym) params.set('symbol', sym);
    const from = dashboardDateFrom();
    if (from) params.set('date_from', from);
    const d = await apiGet(`/performance/stats?${params.toString()}`);
    _lastStats = d;
    renderPeriodResults(d);

    document.getElementById('metrics').innerHTML = `
      <div class="metric"><div class="metric-label">P&L Total</div>
        <div class="metric-value ${d.total_pnl >= 0 ? 'pos' : 'neg'}">${money(d.total_pnl)}</div>
        <div class="metric-sub">${d.total_trades} trades</div></div>
      <div class="metric"><div class="metric-label">Win Rate</div>
        <div class="metric-value">${d.win_rate}%</div>
        <div class="metric-sub">${d.wins}W / ${d.losses}L${d.breakeven ? ` / ${d.breakeven}BE` : ''}</div></div>
      <div class="metric"><div class="metric-label">Max Drawdown</div>
        <div class="metric-value neg">${d.max_drawdown}%</div>
        <div class="metric-sub">${moneyAbs(d.max_drawdown_abs)}</div></div>
      <div class="metric"><div class="metric-label">Ratio R:R <span class="info-badge" title="Ratio moyen gain/risque par trade">i</span></div>
        <div class="metric-value risk">${d.rr_ratio != null ? d.rr_ratio : 'N/A'}</div>
        <div class="metric-sub">Moy. par trade</div></div>`;

    // Colonne latérale du dashboard : uniquement Profit Factor et Espérance
    // (cf. maquette). Volume total et Coûts totaux restent visibles plus
    // bas, dans la rangée de highlights, pour ne perdre aucune donnée.
    document.getElementById('metrics-2').innerHTML = `
      <div class="metric"><div class="metric-label">Profit Factor</div>
        <div class="metric-value">${d.profit_factor != null ? d.profit_factor : 'N/A'}</div>
        <div class="metric-sub">Gains / pertes</div></div>
      <div class="metric"><div class="metric-label">Espérance</div>
        <div class="metric-value ${d.expectancy >= 0 ? 'pos' : 'neg'}">${d.expectancy != null ? money(d.expectancy) : 'N/A'}</div>
        <div class="metric-sub">Par trade</div></div>`;

    // Rangée secondaire de la colonne latérale (sous la carte de compte) :
    // Série de pertes / Durée moy., comme sur la maquette.
    highlightsSideEl.innerHTML = `
      <div class="highlight-card"><div class="highlight-label">Série de pertes</div>
        <div class="highlight-value neg">${d.longest_loss_streak}</div>
        <div class="highlight-sub">Consécutifs</div></div>
      <div class="highlight-card"><div class="highlight-label">Durée moy.</div>
        <div class="highlight-value">${fmtDuration(d.avg_duration_minutes)}</div>
        <div class="highlight-sub">Par trade</div></div>`;

    document.getElementById('highlights').innerHTML = `
      <div class="highlight-card"><div class="highlight-label">Meilleur trade</div>
        <div class="highlight-value pos">${d.best_trade ? money(d.best_trade.amount) : '—'}</div>
        <div class="highlight-sub">${d.best_trade ? escapeHtml(d.best_trade.symbol) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Pire trade</div>
        <div class="highlight-value neg">${d.worst_trade ? money(d.worst_trade.amount) : '—'}</div>
        <div class="highlight-sub">${d.worst_trade ? escapeHtml(d.worst_trade.symbol) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Meilleur jour</div>
        <div class="highlight-value pos">${d.best_day ? money(d.best_day.pnl) : '—'}</div>
        <div class="highlight-sub">${d.best_day ? escapeHtml(d.best_day.date) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Pire jour</div>
        <div class="highlight-value neg">${d.worst_day ? money(d.worst_day.pnl) : '—'}</div>
        <div class="highlight-sub">${d.worst_day ? escapeHtml(d.worst_day.date) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Meilleur symbole</div>
        <div class="highlight-value pos">${d.best_symbol ? escapeHtml(d.best_symbol.symbol) : '—'}</div>
        <div class="highlight-sub">${d.best_symbol ? money(d.best_symbol.pnl) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Pire symbole</div>
        <div class="highlight-value neg">${d.worst_symbol ? escapeHtml(d.worst_symbol.symbol) : '—'}</div>
        <div class="highlight-sub">${d.worst_symbol ? money(d.worst_symbol.pnl) : ''}</div></div>
      <div class="highlight-card"><div class="highlight-label">Série de gains</div>
        <div class="highlight-value pos">${d.longest_win_streak}</div>
        <div class="highlight-sub">Consécutifs</div></div>`;
  } catch(e) {
    console.warn('metrics', e);
    showToast(`Statistiques indisponibles : ${e.message}`);
  }
  finally {
    setLoading(metricsEl, false);
    setLoading(metrics2El, false);
    setLoading(highlightsEl, false);
    setLoading(highlightsSideEl, false);
    setLoading(donutWrap, false);
  }
}

// Texte centré dans le cutout du donut (nombre de trades + W/L) — repris de
// la maquette : le donut seul ne suffit pas à lire le résultat sans survol.
function periodCenterTextPlugin(total, wins, losses, colors) {
  return {
    id: 'periodCenterText',
    afterDraw(chart) {
      const { ctx, chartArea } = chart;
      if (!chartArea) return;
      const cx = (chartArea.left + chartArea.right) / 2;
      const cy = (chartArea.top + chartArea.bottom) / 2;
      // Le donut est affiché en grand sur l'écran « Par période » et en
      // petit (96px) dans la colonne du dashboard : on dimensionne le texte
      // sur le diamètre réel plutôt qu'en dur, sinon il déborde du cutout.
      const d = Math.min(chartArea.right - chartArea.left, chartArea.bottom - chartArea.top);
      const big = Math.max(8, Math.round(d * 0.085));
      const small = Math.max(7, Math.round(d * 0.07));
      const gap = Math.round(d * 0.055);
      ctx.save();
      ctx.textAlign = 'center';
      ctx.textBaseline = 'middle';
      // Même pile typographique que le reste de l'interface (voir
      // theme.js/chartFont) : ce texte est dessiné à la main dans le canvas,
      // il n'hérite donc d'aucune règle CSS.
      const family = chartFont();
      ctx.font = `600 ${big}px ${family}`;
      ctx.fillStyle = colors.text;
      ctx.fillText(`${total} trade(s)`, cx, cy - gap);
      ctx.font = `600 ${small}px ${family}`;
      ctx.fillStyle = wins >= losses ? colors.pos : colors.neg;
      ctx.fillText(`${wins}W - ${losses}L`, cx, cy + gap);
      ctx.restore();
    },
  };
}

function renderPeriodResults(stats) {
  const summary = document.getElementById('period-results-summary');
  const winRate = document.getElementById('period-win-rate');
  const legend = document.getElementById('period-results-legend');
  const canvas = document.getElementById('chartPeriodResults');
  if (!summary || !winRate || !legend || !canvas) return;

  const breakeven = stats.breakeven || 0;
  const values = [stats.wins || 0, stats.losses || 0, breakeven];
  const labels = ['Gagnants', 'Perdants', 'Breakeven'];
  const c = chartColors();
  const chartColorsByType = [c.pos, c.neg, c.muted];

  summary.textContent = `${stats.total_trades || 0} trade(s) · ${stats.wins || 0} gagnant(s) · ${stats.losses || 0} perdant(s)`;
  winRate.textContent = `${Number(stats.win_rate || 0).toFixed(1)}%`;
  legend.innerHTML = labels.map((label, index) => `
    <div class="period-result-item" style="--result-color:${chartColorsByType[index]}">
      <div class="period-result-label">${label}</div>
      <div class="period-result-value">${values[index]}</div>
    </div>`).join('');

  if (state.periodResultsChart) state.periodResultsChart.destroy();
  state.periodResultsChart = new Chart(canvas, {
    type: 'doughnut',
    data: {
      labels,
      // Le liseré reprend la couleur de la carte : les segments se détachent
      // sans qu'un anneau gris ne vienne s'ajouter au dessin.
      datasets: [{ data: values, backgroundColor: chartColorsByType, borderColor: c.surface, borderWidth: 3, hoverOffset: 6 }],
    },
    plugins: [periodCenterTextPlugin(stats.total_trades || 0, stats.wins || 0, stats.losses || 0, c)],
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '66%',
      // Correction : ce donut héritait du réglage global `interaction.mode:
      // 'index'` (voir applyChartDefaults dans theme.js), pensé pour les
      // courbes à axe X partagé. Un donut n'a pas d'axe : ce mode y
      // produisait un survol incohérent (segment qui s'illumine au hasard,
      // ou plusieurs à la fois). `nearest`/`intersect:true` est le réglage
      // standard pour les graphiques circulaires — seul le segment
      // réellement survolé réagit.
      interaction: { mode: 'nearest', intersect: true },
      plugins: {
        legend: { display: false },
        tooltip: { callbacks: { label: context => `${context.label} : ${context.parsed}` } },
      },
    },
  });
}

export async function loadEquity() {
  const equityWrap = document.getElementById('chartEquity')?.closest('.chart-wrap');
  const ddWrap = document.getElementById('chartDD')?.closest('.chart-wrap');
  setLoading(equityWrap, true);
  setLoading(ddWrap, true);
  try {
    const params = new URLSearchParams();
    const sym = currentSymbol();
    if (sym) params.set('symbol', sym);
    const from = dashboardDateFrom();
    if (from) params.set('date_from', from);
    const data = await apiGet(`/performance/equity-curve?${params.toString()}`);
    _lastEquityData = data;
    renderEquityCharts(data);
  } catch(e) {
    console.warn('equity', e);
    showToast(`Courbe de capital indisponible : ${e.message}`);
  }
  finally {
    setLoading(equityWrap, false);
    setLoading(ddWrap, false);
  }
}

// ── Bornes de l'axe des montants (courbe de capital) ────────────────────────
//
// L'axe était fixé à `min: 0`, au motif qu'un capital réel n'est jamais
// négatif. Le raisonnement confond deux choses : ne PAS DESCENDRE sous zéro
// et COMMENCER à zéro. Sur un compte à 10 000 $ qui évolue entre 9 800 et
// 10 400, forcer l'origine à zéro écrase toute la courbe dans les 4 %
// supérieurs du cadre — une ligne plate collée en haut, où l'on ne distingue
// plus rien de l'évolution du capital. C'était le symptôme signalé.
//
// On cadre donc sur les données, avec une marge de 8 % de l'amplitude de
// part et d'autre pour que la courbe ne touche ni le haut ni le bas :
//
//   - amplitude nulle (un seul point, ou un capital strictement constant) :
//     on ouvre une fenêtre de ±1 % autour de la valeur, sinon Chart.js
//     produit un axe dégénéré et la ligne disparaît ;
//   - la borne basse est bornée à 0 : la marge ne doit pas faire apparaître
//     un capital négatif, qui n'a pas de sens — c'est la part du
//     raisonnement d'origine qui était juste, et elle est conservée ;
//   - un capital qui frôle réellement zéro garde donc zéro comme plancher.
//
// Les valeurs ne sont PAS arrondies : Chart.js choisit lui-même des
// graduations lisibles à l'intérieur des bornes qu'on lui donne.
function capitalAxis(values) {
  const finite = values.filter(v => Number.isFinite(v));
  if (!finite.length) return {};
  const lo = Math.min(...finite);
  const hi = Math.max(...finite);
  const span = hi - lo;
  const pad = span > 0 ? span * 0.08 : Math.max(Math.abs(hi) * 0.01, 1);
  return { min: Math.max(0, lo - pad), max: hi + pad };
}

function renderEquityCharts(data) {
  const equityCanvas = document.getElementById('chartEquity');
  const ddCanvas = document.getElementById('chartDD');
  const equityEmpty = document.getElementById('equity-empty');
  const ddEmpty = document.getElementById('dd-empty');

  if (!data.length) {
    if (state.equityChart) { state.equityChart.destroy(); state.equityChart = null; }
    if (state.ddChart) { state.ddChart.destroy(); state.ddChart = null; }
    equityCanvas.style.display = 'none';
    ddCanvas.style.display = 'none';
    equityEmpty.style.display = '';
    ddEmpty.style.display = '';
    return;
  }
  equityCanvas.style.display = '';
  ddCanvas.style.display = '';
  equityEmpty.style.display = 'none';
  ddEmpty.style.display = 'none';

  const labels = data.map(d => d.date.slice(5));
  const equity = data.map(d => d.equity);
  // Le drawdown est calculé côté backend (voir /api/performance/equity-curve),
  // à partir du capital réel du compte MT5 connecté.
  const dd = data.map(d => d.drawdown);
  const pointRadius = data.length <= 3 ? 3 : 0;
  // Dépôts / retraits (phase 6) : un point visible sur la courbe de capital,
  // et le détail dans l'infobulle. Le drawdown, lui, les neutralise.
  const flows = data.map(d => d.movement || 0);
  const equityRadius = flows.map(f => (f ? 4 : pointRadius));

  const c = chartColors();

  if (state.equityChart) state.equityChart.destroy();
  state.equityChart = new Chart(equityCanvas, {
    type: 'line',
    // La courbe de capital est tracée dans le bleu de marque, pas en vert :
    // le vert et le rouge disent « gain » ou « perte » sur un trade, et une
    // équité qui monte en vert ferait croire à un résultat par point.
    // Une seule courbe suffit : pas d'aire remplie sous la ligne, qui
    // alourdissait le graphique sans apporter d'information supplémentaire.
    // `tension: 0` — lignes droites entre les points. Le lissage par défaut
    // (0.35, voir theme.js) fait dépasser la courbe de Bézier au-delà des
    // valeurs réelles : sur un capital, cela dessine des creux et des sommets
    // qui n'ont jamais existé, juste avant et après chaque variation nette.
    data: { labels, datasets: [{ data: equity, borderColor: c.primary, backgroundColor: c.primary, fill: false, pointRadius: equityRadius, borderWidth: 2, tension: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: {
      label: ctx => curSym() + ctx.parsed.y.toLocaleString(),
      afterLabel: ctx => { const f = flows[ctx.dataIndex]; return f ? `${f > 0 ? 'Dépôt' : 'Retrait'} : ${curSym()}${Math.abs(f).toLocaleString()}` : undefined; },
    }}},
      // Bornes de l'axe : voir `capitalAxis()`. `min: 0` a été retiré —
      // c'était la cause de la courbe écrasée tout en haut du repère.
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8}}, y: { ...capitalAxis(equity), border: { display: false }, grid: { color: c.grid }, ticks: { callback: v => curSym()+v.toLocaleString()}}}}
  });

  if (state.ddChart) state.ddChart.destroy();
  state.ddChart = new Chart(ddCanvas, {
    type: 'line',
    data: { labels, datasets: [{ data: dd, borderColor: c.neg, backgroundColor: c.neg, fill: false, pointRadius, borderWidth: 2, tension: 0 }] },
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }, tooltip: { callbacks: { label: ctx => ctx.parsed.y.toFixed(2)+'%' }}},
      scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 8}}, y: { border: { display: false }, grid: { color: c.grid }, ticks: { callback: v => v+'%'}}}}
  });
}

export function switchChart(type, btn) {
  document.querySelectorAll('#page-dashboard .tab').forEach(t => t.classList.remove('active'));
  btn.classList.add('active');
  document.getElementById('chart-equity-panel').style.display = type === 'equity' ? '' : 'none';
  document.getElementById('chart-dd-panel').style.display = type === 'drawdown' ? '' : 'none';
  document.getElementById('chart-real-panel').style.display = type === 'real' ? '' : 'none';
  document.getElementById('chart-load-panel').style.display = type === 'load' ? '' : 'none';
  // Chart.js dimensionne mal un canvas créé dans un panneau masqué.
  if (_lastRealData) renderRealEquity(_lastRealData);
}

// ── Équité réelle et charge du dépôt (phase 6) ───────────────────────────────
export async function loadRealEquity() {
  try {
    const from = dashboardDateFrom();
    _lastRealData = await apiGet(`/performance/real-equity${from ? `?date_from=${from}` : ''}`);
    renderRealEquity(_lastRealData);
  } catch (e) {
    console.warn('real-equity', e);
    if (!String(e.message).includes('__stale_account__')) {
      _lastRealData = null;
      renderRealEquity(null);
    }
  }
}

const _ms = iso => new Date(iso).getTime();
const _fmtDate = ms => new Date(ms).toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit' });
const _fmtDateTime = ms => new Date(ms).toLocaleString('fr-FR');

function _timeScale(c) {
  return { type: 'linear', grid: { display: false },
    ticks: { maxTicksLimit: 8, callback: v => _fmtDate(v) } };
}

function renderRealEquity(d) {
  const realCanvas = document.getElementById('chartReal');
  const loadCanvas = document.getElementById('chartLoad');
  if (!realCanvas || !loadCanvas) return;
  const c = chartColors();
  const realEmpty = document.getElementById('real-empty');
  const loadEmpty = document.getElementById('load-empty');
  if (state.realEquityChart) { state.realEquityChart.destroy(); state.realEquityChart = null; }
  if (state.depositLoadChart) { state.depositLoadChart.destroy(); state.depositLoadChart = null; }

  const note = document.getElementById('real-equity-note');
  const recon = document.getElementById('real-equity-recon');
  const loadNote = document.getElementById('deposit-load-note');
  if (!d || !d.balance_points.length) {
    realCanvas.style.display = 'none'; realEmpty.style.display = '';
    loadCanvas.style.display = 'none'; loadEmpty.style.display = '';
    if (note) note.textContent = '';
    if (recon) recon.style.display = 'none';
    if (loadNote) loadNote.textContent = '';
    return;
  }

  // ── Équité réelle : solde en escalier + équité relevée ──
  realCanvas.style.display = ''; realEmpty.style.display = 'none';
  const balance = d.balance_points.map(p => ({ x: _ms(p.time), y: p.balance, delta: p.delta }));
  const equity = d.equity_points.map(p => ({ x: _ms(p.time), y: p.equity, kind: p.kind }));
  const datasets = [{
    label: 'Solde (net)', data: balance, parsing: false, borderColor: c.primary, backgroundColor: c.primary,
    stepped: 'after', fill: false, borderWidth: 2, pointRadius: balance.map(p => (p.delta && Math.abs(p.delta) > 0 ? 2 : 0)),
  }];
  if (equity.length) {
    datasets.push({
      label: 'Équité', data: equity, parsing: false, borderColor: c.neg, backgroundColor: c.neg,
      fill: false, borderWidth: 1.5, pointRadius: equity.length <= 3 ? 3 : 0, tension: 0,
    });
  }
  state.realEquityChart = new Chart(realCanvas, {
    type: 'line', data: { datasets },
    options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'nearest', intersect: false },
      plugins: { legend: { display: equity.length > 0 }, tooltip: { callbacks: {
        title: items => _fmtDateTime(items[0].parsed.x),
        label: ctx => `${ctx.dataset.label} : ${curSym()}${ctx.parsed.y.toLocaleString()}`,
      } } },
      scales: { x: _timeScale(c), y: { ...capitalAxis([...balance, ...equity].map(p => p.y)), border: { display: false }, grid: { color: c.grid },
        ticks: { callback: v => curSym() + v.toLocaleString() } } } },
  });

  const cur = d.current;
  const bits = [`Solde ${curSym()}${cur.balance.toLocaleString()}`, `Équité ${curSym()}${cur.equity.toLocaleString()}`];
  if (cur.floating) bits.push(`Flottant ${cur.floating > 0 ? '+' : '−'}${curSym()}${Math.abs(cur.floating).toLocaleString()}`);
  bits.push(`DD ${d.drawdown.max_pct}% (${d.drawdown.basis === 'equity' ? 'équité relevée' : 'solde'})`);
  if (note) note.textContent = bits.join(' · ');
  if (recon) {
    const r = d.reconciliation;
    const parts = [];
    if (!d.snapshot_count && d.account.mode === 'mt5') {
      parts.push("Aucun relevé d'équité pour l'instant : la courbe d'équité se construit à chaque synchronisation, tant que MT5 est connecté.");
    }
    if (r.gap != null && !r.reconciled) {
      parts.push(`Écart de ${curSym()}${Math.abs(r.gap).toLocaleString()} entre le solde reconstruit et celui du courtier : recalibrez le capital de référence (Réglages).`);
    }
    recon.textContent = parts.join(' ');
    recon.style.display = parts.length ? '' : 'none';
  }

  // ── Charge du dépôt ──
  const dl = d.deposit_load;
  const est = dl.estimated_points.filter(p => p.load_pct != null).map(p => ({ x: _ms(p.time), y: p.load_pct }));
  const meas = dl.measured_points.map(p => ({ x: _ms(p.time), y: p.load_pct }));
  if (!est.length && !meas.length) {
    loadCanvas.style.display = 'none'; loadEmpty.style.display = '';
  } else {
    loadCanvas.style.display = ''; loadEmpty.style.display = 'none';
    const ds = [];
    if (est.length) ds.push({ label: 'Estimée (trades)', data: est, parsing: false, borderColor: c.primary, backgroundColor: c.primary,
      stepped: 'after', fill: false, borderWidth: 2, pointRadius: 0 });
    if (meas.length) ds.push({ label: 'Mesurée (relevés)', data: meas, parsing: false, borderColor: c.neg, backgroundColor: c.neg,
      fill: false, borderWidth: 1.5, pointRadius: meas.length <= 3 ? 3 : 0 });
    state.depositLoadChart = new Chart(loadCanvas, {
      type: 'line', data: { datasets: ds },
      options: { responsive: true, maintainAspectRatio: false, interaction: { mode: 'nearest', intersect: false },
        plugins: { legend: { display: ds.length > 1 }, tooltip: { callbacks: {
          title: items => _fmtDateTime(items[0].parsed.x),
          label: ctx => `${ctx.dataset.label} : ${ctx.parsed.y.toFixed(2)}%`,
        } } },
        scales: { x: _timeScale(c), y: { min: 0, border: { display: false }, grid: { color: c.grid },
          ticks: { callback: v => v + '%' } } } },
    });
  }
  const pct = v => (v == null ? '—' : `${v}%`);
  const src = { measured: 'mesurée', estimated: 'estimée' }[dl.max_source] || '';
  const missing = dl.trades_without_margin ? ` · ${dl.trades_without_margin} trade(s) sans marge connue (ignorés)` : '';
  if (loadNote) loadNote.textContent = `Max ${pct(dl.max_pct)}${src ? ` (${src})` : ''} · moyenne ${pct(dl.average_pct)} · actuelle ${pct(dl.current_pct)}${missing}`;
}

export function redrawDashboardTheme() {
  if (_lastRealData) renderRealEquity(_lastRealData);
  if (_lastStats) renderPeriodResults(_lastStats);
  if (_lastEquityData) renderEquityCharts(_lastEquityData);
}
