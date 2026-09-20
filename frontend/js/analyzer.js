/* Analyzer — un seul panneau, dix sous-onglets (décision D7).
 *
 * Chargé À LA DEMANDE par navigation.js, comme les autres pages : le module
 * n'est téléchargé qu'au premier clic sur l'onglet. Il n'y a pas de bundler
 * dans ce projet, donc chaque import dynamique est une vraie requête — d'où
 * le soin à ne rien importer d'inutile ici.
 *
 * Principe d'affichage (règle R8) : aucun pourcentage n'est montré sans son
 * `n`, et aucun R moyen sans sa couverture R. Un groupe sous le seuil est
 * ATTÉNUÉ, jamais masqué.
 */
import { apiGet, API } from './config.js';
import { money, escapeHtml, setLoading, showToast } from './utils.js';

const EMPTY = '<div class="repart-empty" style="grid-column:1/-1">Aucune donnée — renseignez ce champ depuis la fiche d\'un trade</div>';

let currentView = 'overview';

// ── Filtres courants ────────────────────────────────────────────────────────

function filterQuery() {
  const parts = [];
  const from = document.getElementById('an-date-from')?.value;
  const to = document.getElementById('an-date-to')?.value;
  const symbol = document.getElementById('an-symbol')?.value;
  const source = document.getElementById('an-source')?.value;
  const base = document.getElementById('an-base')?.value;
  if (from) parts.push(`date_from=${from}`);
  if (to) parts.push(`date_to=${to}`);
  if (symbol) parts.push(`symbol=${encodeURIComponent(symbol)}`);
  if (source) parts.push(`source=${source}`);
  if (base) parts.push(`base=${base}`);
  return parts.length ? `?${parts.join('&')}` : '';
}

/** Lecture d'un endpoint Analyzer avec les filtres courants. */
function get(path) {
  return apiGet(`/analyzer${path}${filterQuery()}`);
}

// ── Formatage ───────────────────────────────────────────────────────────────

const num = (v, digits = 2, suffix = '') => (v == null ? '—' : `${v.toFixed(digits)}${suffix}`);
const pct = (v) => (v == null ? '—' : `${v.toFixed(1)}%`);

function rCell(row) {
  if (row.avg_r == null) return '<span class="muted">—</span>';
  const sign = row.avg_r >= 0 ? '+' : '';
  // La couverture R accompagne TOUJOURS le R moyen : « +0,8R » calculé sur
  // 3 trades renseignés parmi 50 n'a pas le même sens que sur 48 parmi 50.
  return `${sign}${row.avg_r.toFixed(2)}R <span class="r-cover">(${Math.round(row.r_coverage)}%)</span>`;
}

function badge(status) {
  if (!status) return '';
  const cls = {
    'À SURVEILLER': 'rel-watch',
    'SIGNAL INTÉRESSANT': 'rel-ok',
    'PATTERN ROBUSTE': 'rel-strong',
  }[status] || '';
  return `<span class="rel-badge ${cls}">${escapeHtml(status)}</span>`;
}

function winCell(row) {
  const ci = row.win_rate_ci;
  const base = pct(row.win_rate);
  if (!ci) return base;
  return `${base}<br><span class="ci-hint">[${ci.low.toFixed(0)}–${ci.high.toFixed(0)}]</span>`;
}

function pnlCell(value) {
  return `<span class="${value >= 0 ? 'pos' : 'neg'}">${money(value)}</span>`;
}

function rowClasses(row) {
  return [row.low_sample ? 'low-sample' : '', row.inconclusive ? 'inconclusive' : '']
    .filter(Boolean).join(' ');
}

/** Tableau standard à 8 colonnes, utilisé par toutes les dimensions. */
function dimensionTable(rows, keyLabel) {
  if (!rows || !rows.length) return EMPTY;
  const header = `<div class="repart-row repart-header repart-8">
      <span>${escapeHtml(keyLabel)}</span><span>n</span><span>Part</span><span>Win rate</span>
      <span>Résultat</span><span>Expect.</span><span>R moy.</span><span>Fiabilité</span></div>`;
  const body = rows.map(row => `
    <div class="repart-row repart-8 ${rowClasses(row)}">
      <span>${escapeHtml(String(row.key))}</span>
      <span>${row.trades}</span>
      <span>${pct(row.pct_of_total)}</span>
      <span>${winCell(row)}</span>
      ${pnlCell(row.pnl)}
      ${pnlCell(row.expectancy)}
      <span>${rCell(row)}</span>
      <span>${badge(row.status)}</span>
    </div>`).join('');
  return header + body;
}

function card(title, inner, subtitle) {
  return `<div class="chart-card" style="margin-bottom:16px">
    <div class="chart-head"><div>
      <span class="chart-title">${escapeHtml(title)}</span>
      ${subtitle ? `<div class="metric-sub">${escapeHtml(subtitle)}</div>` : ''}
    </div></div>
    <div class="trade-table">${inner}</div>
  </div>`;
}

function metric(label, value, sub) {
  return `<div class="metric">
    <div class="metric-label">${escapeHtml(label)}</div>
    <div class="metric-value">${value}</div>
    ${sub ? `<div class="metric-sub">${escapeHtml(sub)}</div>` : ''}
  </div>`;
}

// ── Sous-onglets ────────────────────────────────────────────────────────────

const VIEWS = {
  overview: renderOverview,
  setups: renderSetups,
  sessions: renderSessions,
  instruments: renderInstruments,
  temporal: renderTemporal,
  discipline: renderDiscipline,
  psychology: renderPsychology,
  patterns: renderPatterns,
  report: renderReport,
  settings: renderSettings,
};

export async function switchAnalyzerTab(view, button) {
  currentView = view;
  document.querySelectorAll('#analyzer-tabs .tab').forEach(t => t.classList.remove('active'));
  if (button) button.classList.add('active');
  await refresh();
}

export async function applyAnalyzerFilters() {
  await refresh();
}

export async function resetAnalyzerFilters() {
  ['an-date-from', 'an-date-to'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  ['an-symbol', 'an-source', 'an-base'].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.value = id === 'an-base' ? 'gross' : '';
  });
  await refresh();
}

export async function loadAnalyzer() {
  await populateSymbols();
  await refresh();
}

async function refresh() {
  const container = document.getElementById('analyzer-body');
  if (!container) return;
  setLoading(container, true);
  try {
    container.innerHTML = await VIEWS[currentView]();
  } catch (error) {
    console.warn('analyzer', error);
    showToast(`Analyzer indisponible : ${error.message}`);
    container.innerHTML = `<div class="repart-empty">Analyse indisponible — ${escapeHtml(error.message)}</div>`;
  } finally {
    setLoading(container, false);
  }
}

async function populateSymbols() {
  const select = document.getElementById('an-symbol');
  if (!select || select.dataset.filled) return;
  try {
    const data = await apiGet('/analyzer/symbol-map');
    select.innerHTML = '<option value="">Tous les symboles</option>'
      + data.raw_symbols.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join('');
    select.dataset.filled = '1';
  } catch (e) { /* la liste reste sur « tous » — non bloquant */ }
}

// ── Vue d'ensemble ──────────────────────────────────────────────────────────

async function renderOverview() {
  const data = await get('/overview');
  const a = data.analyzer;
  const j = data.journal;

  const metrics = [
    metric('Trades', a.trades),
    metric('Win rate', pct(a.win_rate), `${a.wins ?? j.wins} gagnants · ${j.losses} perdants`),
    metric('Résultat', pnlCell(a.pnl), a.base === 'net' ? 'base nette' : 'base brute'),
    metric('Profit factor', num(a.profit_factor), a.profit_factor == null ? 'aucune perte sur la période' : ''),
    metric('Expectancy', pnlCell(a.expectancy), 'par trade'),
    metric('Expectancy (R)', a.expectancy_r == null ? '—' : `${a.expectancy_r >= 0 ? '+' : ''}${a.expectancy_r.toFixed(2)}R`,
      `couverture R ${pct(a.r_coverage)}`),
    metric('Drawdown max', `${num(j.max_drawdown, 2)}%`, money(j.max_drawdown_abs)),
    metric('Total R', a.total_r == null ? '—' : `${a.total_r >= 0 ? '+' : ''}${a.total_r.toFixed(2)}R`,
      `${a.r_count} trades avec R`),
  ].join('');

  const coverageWarning = a.r_coverage < 50 && a.trades > 0
    ? `<div class="analyzer-warn">Seuls ${pct(a.r_coverage)} de vos trades ont un R calculable
       (Stop Loss ou risque renseigné). Tous les chiffres en R ci-dessus portent sur ce
       sous-ensemble, pas sur l'ensemble de vos trades.</div>`
    : '';

  const parity = `<div class="analyzer-note">
    Brut ${money(a.gross_pnl)} · Net ${money(a.net_pnl)} —
    ces chiffres sont calculés par les mêmes fonctions que le Dashboard.</div>`;

  return coverageWarning + `<div class="metrics-row">${metrics}</div>` + parity;
}

// ── Dimensions ──────────────────────────────────────────────────────────────

async function renderSetups() {
  const [setups, playbooks] = await Promise.all([
    get('/dimension/setup'), get('/dimension/playbook'),
  ]);
  return extremesNote(setups)
    + card('Par setup', dimensionTable(setups.rows, 'Setup'))
    + card('Par playbook · stratégie', dimensionTable(playbooks.rows, 'Playbook'));
}

async function renderInstruments() {
  const symbols = await get('/dimension/symbol');
  const note = symbols.rows.length > 1
    ? '<div class="analyzer-note">Deux lignes pour le même instrument ? Fusionnez-les dans l\'onglet Paramètres.</div>'
    : '';
  return extremesNote(symbols) + card('Par instrument', dimensionTable(symbols.rows, 'Instrument')) + note;
}

async function renderSessions() {
  const sessions = await get('/dimension/session');
  const details = (sessions.session_details || []).map(d => `
    <div class="repart-row repart-5b">
      <span>${escapeHtml(d.session)}</span>
      <span>${d.trades}</span>
      <span>${escapeHtml(d.best_setup || '—')}</span>
      <span>${escapeHtml(d.worst_setup || '—')}</span>
      <span>${escapeHtml(d.best_hour || '—')}</span>
    </div>`).join('');
  const header = `<div class="repart-row repart-header repart-5b">
    <span>Session</span><span>n</span><span>Meilleur setup</span><span>Pire setup</span><span>Meilleure heure</span></div>`;

  return extremesNote(sessions)
    + card('Par session', dimensionTable(sessions.rows, 'Session'),
        'Fenêtres en heure serveur du courtier — modifiables dans Paramètres')
    + card('Détail par session', details ? header + details : EMPTY);
}

async function renderTemporal() {
  const [weekday, hour, month] = await Promise.all([
    get('/dimension/weekday'), get('/dimension/hour'), get('/dimension/month'),
  ]);
  return card('Par jour de la semaine', dimensionTable(weekday.rows, 'Jour'))
    + card('Par heure', dimensionTable(hour.rows, 'Heure'))
    + card('Par mois', dimensionTable(month.rows, 'Mois'));
}

function extremesNote(payload) {
  if (!payload.best && !payload.worst) return '';
  return `<div class="analyzer-note" style="margin-bottom:12px">
    Meilleur : <strong>${escapeHtml(payload.best || '—')}</strong> ·
    Pire : <strong>${escapeHtml(payload.worst || '—')}</strong>
    <span class="muted">(par espérance, groupes de moins de 3 trades ignorés)</span></div>`;
}

// ── Psychologie ─────────────────────────────────────────────────────────────

async function renderPsychology() {
  const [emotions, errors, overtrading] = await Promise.all([
    get('/dimension/emotion'), get('/errors'), get('/overtrading'),
  ]);

  const overlapNote = emotions.overlapping
    ? '<div class="analyzer-note">Certains trades portent plusieurs émotions : la somme des effectifs dépasse le total.</div>'
    : '';

  const errorHeader = `<div class="repart-row repart-header repart-7">
    <span>Erreur</span><span>n</span><span>Win rate</span><span>Coût</span>
    <span>Coût (R)</span><span>Écart / sans erreur</span><span>Fiabilité</span></div>`;
  const errorRows = (errors.rows || []).map(row => `
    <div class="repart-row repart-7 ${rowClasses(row)}">
      <span>${escapeHtml(row.key)}</span>
      <span>${row.trades}</span>
      <span>${pct(row.win_rate)}</span>
      ${pnlCell(row.cost)}
      <span>${row.cost_r == null ? '—' : `${row.cost_r.toFixed(2)}R`}</span>
      ${row.delta_expectancy == null ? '<span class="muted">—</span>' : pnlCell(row.delta_expectancy)}
      <span>${badge(row.status)}</span>
    </div>`).join('');

  const reference = errors.reference
    ? `Référence : ${errors.reference_trades} trades déclarés sans erreur, espérance ${money(errors.reference.expectancy)}.`
    : 'Aucun trade déclaré « sans erreur » : l\'écart ne peut pas être calculé.';

  const rankRows = (overtrading.by_rank || []).map(row => `
    <div class="repart-row repart-6 ${rowClasses(row)}">
      <span>${escapeHtml(row.key)}</span><span>${row.trades}</span>
      <span>${pct(row.win_rate)}</span>${pnlCell(row.pnl)}${pnlCell(row.expectancy)}
      <span>${badge(row.status)}</span></div>`).join('');
  const rankHeader = `<div class="repart-row repart-header repart-6">
    <span>Rang dans la journée</span><span>n</span><span>Win rate</span>
    <span>Résultat</span><span>Expect.</span><span>Fiabilité</span></div>`;

  const afterLoss = overtrading.after_loss && overtrading.other_trades
    ? `<div class="analyzer-note">Après une perte le même jour : espérance
       ${money(overtrading.after_loss.expectancy)} sur ${overtrading.after_loss.trades} trades,
       contre ${money(overtrading.other_trades.expectancy)} sinon
       (écart ${money(overtrading.after_loss_delta)}).</div>`
    : '';

  return card('Par émotion', dimensionTable(emotions.rows, 'Émotion')) + overlapNote
    + card('Coût des erreurs', errorRows ? errorHeader + errorRows : EMPTY, reference)
    + card('Overtrading · rang du trade dans la journée', rankRows ? rankHeader + rankRows : EMPTY)
    + afterLoss;
}

// ── Discipline ──────────────────────────────────────────────────────────────

async function renderDiscipline() {
  const [data, frequency, series] = await Promise.all([
    get('/discipline'), get('/frequency'), get('/series'),
  ]);
  const c = data.compliance;

  const sopNote = c.sop_version
    ? `Plan « ${c.sop_version.name} », seuil ${Math.round(c.threshold * 100)} % — ${c.scored_trades} trades notés, ${c.unscored_trades} non renseignés.`
    : 'Aucun plan enregistré : créez-le dans l\'onglet Paramètres, puis notez vos trades depuis leur fiche.';

  const complianceRows = (c.rows || []).map(row => `
    <div class="repart-row repart-6 ${rowClasses(row)}">
      <span>${escapeHtml(row.key)}${row.excluded_from_comparison ? ' <span class="muted">(hors comparaison)</span>' : ''}</span>
      <span>${row.trades}</span><span>${pct(row.win_rate)}</span>
      ${pnlCell(row.pnl)}${pnlCell(row.expectancy)}<span>${rCell(row)}</span></div>`).join('');
  const complianceHeader = `<div class="repart-row repart-header repart-6">
    <span>Conformité</span><span>n</span><span>Win rate</span><span>Résultat</span>
    <span>Expect.</span><span>R moy.</span></div>`;

  const itemRows = (c.by_item || []).map(item => `
    <div class="repart-row repart-5b">
      <span>${escapeHtml(item.item)}</span>
      <span>${item.respected ? item.respected.trades : 0}</span>
      <span>${item.respected ? money(item.respected.expectancy) : '—'}</span>
      <span>${item.skipped ? money(item.skipped.expectancy) : '—'}</span>
      ${item.delta_expectancy == null ? '<span class="muted">—</span>' : pnlCell(item.delta_expectancy)}
    </div>`).join('');
  const itemHeader = `<div class="repart-row repart-header repart-5b">
    <span>Critère du plan</span><span>Respecté (n)</span><span>Expect. si respecté</span>
    <span>Expect. sinon</span><span>Écart</span></div>`;

  const frequencyRows = (frequency.rows || []).map(row => `
    <div class="repart-row repart-6 ${rowClasses(row)}">
      <span>${escapeHtml(row.key)}</span><span>${row.trades}</span>
      <span>${pct(row.win_rate)}</span>${pnlCell(row.pnl)}${pnlCell(row.expectancy)}
      <span>${badge(row.status)}</span></div>`).join('');
  const frequencyHeader = `<div class="repart-row repart-header repart-6">
    <span>Journées à…</span><span>n</span><span>Win rate</span><span>Résultat</span>
    <span>Expect.</span><span>Fiabilité</span></div>`;

  const drawdowns = (series.drawdowns || []).map(d => `
    <div class="repart-row repart-5b">
      <span>${escapeHtml(d.from)} → ${escapeHtml(d.trough)}</span>
      ${pnlCell(d.depth)}
      <span>${d.trades_in_drawdown}</span>
      <span>${d.recovered ? `${d.trades_to_recover} trades` : 'en cours'}</span>
      <span>${d.recovered ? `${d.days_to_recover} j` : '—'}</span></div>`).join('');
  const drawdownHeader = `<div class="repart-row repart-header repart-5b">
    <span>Période</span><span>Baisse</span><span>Trades</span>
    <span>Récupération</span><span>Délai</span></div>`;

  const seriesNote = `<div class="analyzer-note">
    Plus longue série gagnante : ${series.longest_win} · perdante : ${series.longest_loss} ·
    alternance ${pct(series.alternation_rate)}
    ${series.current_streak ? ` · série en cours : ${series.current_streak.length} ${series.current_streak.kind === 'win' ? 'gain(s)' : 'perte(s)'}` : ''}</div>`;

  return card('Conformité au plan', complianceRows ? complianceHeader + complianceRows : EMPTY, sopNote)
    + (itemRows ? card('Critère par critère', itemHeader + itemRows,
        'Quel élément de votre plan fait réellement une différence ?') : '')
    + card('Fréquence de trading', frequencyRows ? frequencyHeader + frequencyRows : EMPTY,
        `${frequency.trading_days} jours de trading · ${num(frequency.avg_per_day, 2)} trades/jour en moyenne`)
    + card('Épisodes de baisse et récupération', drawdowns ? drawdownHeader + drawdowns : EMPTY)
    + seriesNote;
}

// ── Patterns ────────────────────────────────────────────────────────────────

async function renderPatterns() {
  const data = await get('/patterns');
  const rows = (data.rows || []).map(row => `
    <div class="repart-row repart-8 ${rowClasses(row)}">
      <span>${escapeHtml(row.condition)}</span>
      <span>${row.trades}</span>
      <span>${winCell(row)}</span>
      ${pnlCell(row.expectancy)}
      <span>${row.expectancy_r == null ? '—' : `${row.expectancy_r >= 0 ? '+' : ''}${row.expectancy_r.toFixed(2)}R`}</span>
      ${pnlCell(row.delta_expectancy)}
      <span>${num(row.profit_factor)}</span>
      <span>${badge(row.status)}</span>
    </div>`).join('');
  const header = `<div class="repart-row repart-header repart-8">
    <span>Condition</span><span>n</span><span>Win rate</span><span>Expect.</span>
    <span>Exp. (R)</span><span>Écart</span><span>PF</span><span>Fiabilité</span></div>`;

  const warning = `<div class="analyzer-warn">${escapeHtml(data.multiplicity.text)}</div>`;
  return warning
    + card('Patterns détectés', rows ? header + rows : EMPTY,
        `Seuil : ${data.min_n} trades minimum · croisements limités à 2 variables`)
    + `<div class="analyzer-note">${escapeHtml(data.note)}
       Les lignes en pointillés sont statistiquement indiscernables du hasard.</div>`;
}

// ── Rapport, exports, règles ────────────────────────────────────────────────

async function renderReport() {
  const [quality, rules] = await Promise.all([
    get('/data-quality'), apiGet('/analyzer/playbook/rules'),
  ]);

  const coverage = (quality.coverage || []).map(item => {
    const cls = item.coverage < 34 ? 'low' : (item.coverage < 67 ? 'mid' : '');
    return `<div class="quality-item">
      <div class="quality-head">
        <span class="quality-label">${escapeHtml(item.field)}</span>
        <span class="quality-pct">${item.coverage.toFixed(0)}%</span>
      </div>
      <div class="quality-bar ${cls}"><span style="width:${item.coverage}%"></span></div>
      <div class="metric-sub">${item.filled} renseignés · ${item.missing} manquants</div>
    </div>`;
  }).join('');

  const anomalies = (quality.anomalies || []).map(a => `
    <li><span class="anomaly-count">${a.count}</span> — ${escapeHtml(a.label)}
      <span class="anomaly-hint">${escapeHtml(a.hint || '')}</span></li>`).join('');

  const ruleCards = (rules.rules || []).map(rule => `
    <div class="rule-card ${rule.status}">
      <div class="rule-caveat">${escapeHtml(rule.caveat)}</div>
      <div class="rule-text">${escapeHtml(rule.text)}</div>
      ${rule.evidence ? `<div class="rule-evidence">n = ${rule.evidence.n ?? '—'}${rule.evidence.status ? ` · ${escapeHtml(rule.evidence.status)}` : ''}</div>` : ''}
      ${rule.status === 'proposed' ? `<div class="rule-actions">
        <button class="btn-sync" onclick="decideAnalyzerRule(${rule.id},'accepted')">Accepter</button>
        <button class="btn-reset" onclick="decideAnalyzerRule(${rule.id},'rejected')">Rejeter</button>
      </div>` : `<div class="metric-sub">${rule.status === 'accepted' ? 'Acceptée' : 'Rejetée'}</div>`}
    </div>`).join('');

  const actions = `<div class="filters" style="margin-bottom:16px">
    <button class="btn-sync" onclick="openAnalyzerReport()">Ouvrir le rapport HTML</button>
    <button class="btn-sync" onclick="downloadAnalyzerExport('csv')">Export CSV</button>
    <button class="btn-sync" onclick="downloadAnalyzerExport('xlsx')">Export Excel</button>
    <button class="btn-sync" onclick="downloadAnalyzerExport('json')">Export JSON</button>
    <button class="btn-reset" onclick="generateAnalyzerRules()">Générer des règles</button>
  </div>
  <div class="analyzer-note" style="margin-bottom:16px">
    Le rapport et les exports contiennent vos données de trading et sont produits
    uniquement sur cette action. Rien n'est envoyé hors de cette machine.</div>`;

  return actions
    + `<div class="chart-card" style="margin-bottom:16px">
        <div class="chart-head"><div><span class="chart-title">Qualité des données</span>
        <div class="metric-sub">La fiabilité des analyses dépend directement de ces taux</div></div></div>
        <div class="quality-grid">${coverage || EMPTY}</div></div>`
    + (anomalies ? `<div class="chart-card" style="margin-bottom:16px">
        <div class="chart-head"><span class="chart-title">Anomalies détectées</span></div>
        <ul class="anomaly-list">${anomalies}</ul></div>` : '')
    + `<div class="chart-card">
        <div class="chart-head"><div><span class="chart-title">Règles proposées</span>
        <div class="metric-sub">Des observations à valider — aucune n'est appliquée automatiquement</div></div></div>
        ${ruleCards || '<div class="repart-empty">Aucune règle proposée — cliquez sur « Générer des règles ».</div>'}</div>`;
}

export async function openAnalyzerReport() {
  const path = `/analyzer/report.html${filterQuery()}`;

  // Mode desktop (pywebview) : pas d'onglets dans la fenêtre embarquée —
  // `window.open()` n'y a aucun effet fiable, c'était la cause du bug
  // « n'affiche rien ». Le rapport est ouvert dans une VRAIE fenêtre
  // native séparée.
  if (window.pywebview && window.pywebview.api && window.pywebview.api.open_html_report) {
    try {
      const html = await (await fetchOrThrow(path)).text();
      const opened = await window.pywebview.api.open_html_report(html);
      if (!opened) showToast('Ouverture du rapport impossible');
    } catch (error) {
      showToast(`Rapport indisponible : ${error.message}`);
    }
    return;
  }

  // Mode navigateur (START.bat) : l'onglet doit s'ouvrir de façon SYNCHRONE,
  // pendant que le clic est encore actif — sinon le fetch qui suit fait
  // perdre le geste utilisateur et le navigateur bloque le popup sans le
  // moindre message, ce qui produisait le même symptôme « rien ne s'affiche ».
  const tab = window.open('', '_blank');
  try {
    const html = await (await fetchOrThrow(path)).text();
    const url = URL.createObjectURL(new Blob([html], { type: 'text/html' }));
    if (tab) {
      tab.location.href = url;
    } else {
      showToast("Autorisez les fenêtres popup pour ouvrir le rapport, ou utilisez l'export.");
    }
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch (error) {
    if (tab) tab.close();
    showToast(`Rapport indisponible : ${error.message}`);
  }
}

export async function downloadAnalyzerExport(format) {
  const path = `/analyzer/export/${format}${filterQuery()}`;
  const labels = { csv: 'CSV', xlsx: 'Excel', json: 'JSON' };
  try {
    const response = await fetchOrThrow(path);
    const disposition = response.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : `analyzer.${format}`;
    const blob = await response.blob();

    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_file) {
      const encoded = await blobToBase64(blob);
      const saved = await window.pywebview.api.save_file(filename, encoded, labels[format] || 'Fichier');
      showToast(saved ? 'Fichier enregistré' : 'Enregistrement annulé');
      return;
    }
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
    showToast('Export téléchargé');
  } catch (error) {
    showToast(`Export impossible : ${error.message}`);
  }
}

async function fetchOrThrow(path) {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response;
}

function blobToBase64(blob) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(',')[1]);
    reader.onerror = () => reject(new Error('Lecture du fichier impossible'));
    reader.readAsDataURL(blob);
  });
}

export async function generateAnalyzerRules() {
  try {
    const response = await fetch(`${API}/analyzer/runs${filterQuery()}`, { method: 'POST' });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    showToast(data.rules_created
      ? `${data.rules_created} règle(s) proposée(s)`
      : 'Aucune nouvelle règle — rien de suffisamment net sur cette période');
    await refresh();
  } catch (error) {
    showToast(`Génération impossible : ${error.message}`);
  }
}

export async function decideAnalyzerRule(id, status) {
  try {
    const response = await fetch(`${API}/analyzer/playbook/rules/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ status }),
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    await refresh();
  } catch (error) {
    showToast(`Mise à jour impossible : ${error.message}`);
  }
}

// ── Paramètres ──────────────────────────────────────────────────────────────

async function renderSettings() {
  const [settings, sop, symbolMap] = await Promise.all([
    apiGet('/analyzer/settings'), apiGet('/analyzer/sop').catch(() => null),
    apiGet('/analyzer/symbol-map'),
  ]);

  const sessions = (settings.settings.sessions || []).map((w, index) => `
    <div class="session-row" data-index="${index}">
      <input value="${escapeHtml(w.label)}" placeholder="Nom" data-field="label">
      <input value="${escapeHtml(w.start)}" placeholder="08:00" data-field="start">
      <input value="${escapeHtml(w.end)}" placeholder="13:00" data-field="end">
      <button class="btn-reset" onclick="this.closest('.session-row').remove()">Retirer</button>
    </div>`).join('');

  const suggestions = (symbolMap.suggestions || []).map(s => `
    <div class="analyzer-note">${escapeHtml(s.raw_symbols.join(' · '))} →
      <strong>${escapeHtml(s.canonical)}</strong>
      <button class="btn-sync" style="margin-left:8px"
        onclick="applySymbolSuggestion('${escapeHtml(s.canonical)}', ${escapeHtml(JSON.stringify(s.raw_symbols)).replace(/"/g, '&quot;')})">Appliquer</button>
    </div>`).join('');

  const mappings = (symbolMap.mappings || []).map((m, index) => `
    <div class="symbol-map-row" data-index="${index}">
      <input class="filter-field" value="${escapeHtml(m.raw_symbol)}" data-field="raw_symbol"
             style="background:var(--surface-2);border:1px solid var(--border2);color:var(--text);padding:7px 10px">
      <span class="arrow">→</span>
      <input value="${escapeHtml(m.canonical_symbol)}" data-field="canonical_symbol"
             style="background:var(--surface-2);border:1px solid var(--border2);color:var(--text);padding:7px 10px">
      <button class="btn-reset" onclick="this.closest('.symbol-map-row').remove()">Retirer</button>
    </div>`).join('');

  const sopItemRows = (sop && sop.active ? sop.active.items : []).map((item, index) => `
    <div class="session-row" data-index="${index}" data-item-id="${item.id}">
      <input value="${escapeHtml(item.label)}" placeholder="Ex. Structure claire" data-field="label">
      <label style="display:flex;align-items:center;gap:6px;font-size:var(--fs-base);color:var(--muted)">
        <input type="checkbox" data-field="required" ${item.required ? 'checked' : ''} style="width:auto"> Requis
      </label>
      <span></span>
      <button class="btn-reset" onclick="this.closest('.session-row').remove()">Retirer</button>
    </div>`).join('');

  const sopHistory = sop && sop.versions && sop.versions.length > 1
    ? `<div class="analyzer-note">Versions précédentes : ${sop.versions.slice(1)
        .map(v => escapeHtml(v.name)).join(' · ')}
        — leurs analyses passées ne changent jamais quand vous modifiez le plan.</div>`
    : '';

  return `
    <div class="chart-card" style="margin-bottom:16px">
      <div class="chart-head"><div><span class="chart-title">Fenêtres de session</span>
        <div class="metric-sub">Heures du SERVEUR de votre courtier, pas votre heure locale.
          L'ordre compte : un trade appartient à la première fenêtre qui le contient.</div></div></div>
      <div id="an-sessions">${sessions}</div>
      <div class="filters" style="margin-top:10px">
        <button class="btn-sync" onclick="addAnalyzerSession()">Ajouter une fenêtre</button>
        <button class="btn-sync" onclick="saveAnalyzerSessions()">Enregistrer</button>
      </div>
    </div>

    <div class="chart-card" style="margin-bottom:16px">
      <div class="chart-head"><div><span class="chart-title">Normalisation des symboles</span>
        <div class="metric-sub">Regroupez les variantes d'un même instrument (XAUUSD.a, GOLD → XAUUSD)</div></div></div>
      ${suggestions ? `<div style="margin-bottom:12px">${suggestions}</div>` : ''}
      <div id="an-symbol-map">${mappings}</div>
      <div class="filters" style="margin-top:10px">
        <button class="btn-sync" onclick="addAnalyzerSymbolRule()">Ajouter une règle</button>
        <button class="btn-sync" onclick="saveAnalyzerSymbolMap()">Enregistrer</button>
      </div>
    </div>

    <div class="chart-card">
      <div class="chart-head"><div><span class="chart-title">Plan de trading (SOP)</span>
        <div class="metric-sub">Modifier le plan crée une NOUVELLE version : vos analyses passées ne changent pas</div></div></div>
      <div class="analyzer-note" style="margin-bottom:12px">
        <strong>À quoi ça sert :</strong> chaque élément ci-dessous est une condition de VOTRE
        stratégie (ex. « structure claire », « retest confirmé »). Une fois le plan créé, une
        case à cocher apparaît pour chaque élément dans la fiche de modification de vos trades
        (bouton « Modifier » sur un trade). Un trade est jugé <strong>conforme</strong> quand il
        atteint le seuil défini ci-dessous — visible dans l'onglet Discipline. Sans plan
        enregistré, cette case n'apparaît nulle part : c'est pourquoi elle semblait manquante.
      </div>
      <div id="an-sop-items">${sopItemRows}</div>
      <div class="filters" style="margin:10px 0">
        <button class="btn-sync" onclick="addAnalyzerSopItem()">Ajouter un élément</button>
      </div>
      <div class="filter-field" style="max-width:260px;margin-bottom:12px">
        <label for="an-sop-threshold">Seuil de conformité</label>
        <select id="an-sop-threshold">
          <option value="1" ${!sop || !sop.active || sop.active.threshold >= 1 ? 'selected' : ''}>Tous les éléments requis (100%)</option>
          <option value="0.8" ${sop && sop.active && Math.abs(sop.active.threshold - 0.8) < 0.01 ? 'selected' : ''}>80% des éléments requis</option>
          <option value="0.6" ${sop && sop.active && Math.abs(sop.active.threshold - 0.6) < 0.01 ? 'selected' : ''}>60% des éléments requis</option>
        </select>
      </div>
      <div class="filters">
        <button class="btn-sync" onclick="saveAnalyzerSop()">
          ${sop && sop.active ? 'Enregistrer (nouvelle version)' : 'Créer le plan'}</button>
        <button class="btn-reset" onclick="createDefaultSop()">Réinitialiser aux 6 éléments par défaut</button>
      </div>
      ${sopHistory}
    </div>`;
}

export function addAnalyzerSopItem() {
  const container = document.getElementById('an-sop-items');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', `
    <div class="session-row">
      <input placeholder="Ex. Confirmation" data-field="label">
      <label style="display:flex;align-items:center;gap:6px;font-size:var(--fs-base);color:var(--muted)">
        <input type="checkbox" data-field="required" checked style="width:auto"> Requis
      </label>
      <span></span>
      <button class="btn-reset" onclick="this.closest('.session-row').remove()">Retirer</button>
    </div>`);
}

export async function saveAnalyzerSop() {
  const items = [...document.querySelectorAll('#an-sop-items .session-row')].map(row => ({
    label: row.querySelector('[data-field="label"]').value.trim(),
    required: row.querySelector('[data-field="required"]').checked,
  })).filter(item => item.label);
  if (!items.length) return showToast('Ajoutez au moins un élément au plan');
  const threshold = Number(document.getElementById('an-sop-threshold').value || 1);
  try {
    const response = await fetch(`${API}/analyzer/sop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ name: `SOP ${new Date().toLocaleDateString('fr-FR')}`, items, threshold }),
    });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    showToast('Plan enregistré — nouvelle version active');
    await refresh();
  } catch (error) {
    showToast(`Enregistrement impossible : ${error.message}`);
  }
}

export function addAnalyzerSession() {
  const container = document.getElementById('an-sessions');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', `
    <div class="session-row">
      <input placeholder="Nom" data-field="label">
      <input placeholder="08:00" data-field="start">
      <input placeholder="13:00" data-field="end">
      <button class="btn-reset" onclick="this.closest('.session-row').remove()">Retirer</button>
    </div>`);
}

export async function saveAnalyzerSessions() {
  const rows = [...document.querySelectorAll('#an-sessions .session-row')].map(row => ({
    label: row.querySelector('[data-field="label"]').value.trim(),
    start: row.querySelector('[data-field="start"]').value.trim(),
    end: row.querySelector('[data-field="end"]').value.trim(),
  }));
  await put('/analyzer/settings', { sessions: rows }, 'Fenêtres de session enregistrées');
}

export function addAnalyzerSymbolRule() {
  const container = document.getElementById('an-symbol-map');
  if (!container) return;
  container.insertAdjacentHTML('beforeend', `
    <div class="symbol-map-row">
      <input placeholder="XAUUSD.a" data-field="raw_symbol"
        style="background:var(--surface-2);border:1px solid var(--border2);color:var(--text);padding:7px 10px">
      <span class="arrow">→</span>
      <input placeholder="XAUUSD" data-field="canonical_symbol"
        style="background:var(--surface-2);border:1px solid var(--border2);color:var(--text);padding:7px 10px">
      <button class="btn-reset" onclick="this.closest('.symbol-map-row').remove()">Retirer</button>
    </div>`);
}

export async function saveAnalyzerSymbolMap() {
  const mappings = [...document.querySelectorAll('#an-symbol-map .symbol-map-row')].map(row => ({
    raw_symbol: row.querySelector('[data-field="raw_symbol"]').value.trim(),
    canonical_symbol: row.querySelector('[data-field="canonical_symbol"]').value.trim(),
  })).filter(m => m.raw_symbol && m.canonical_symbol);
  await put('/analyzer/symbol-map', { mappings }, 'Normalisation enregistrée');
}

export async function applySymbolSuggestion(canonical, rawSymbols) {
  const existing = [...document.querySelectorAll('#an-symbol-map .symbol-map-row')].map(row => ({
    raw_symbol: row.querySelector('[data-field="raw_symbol"]').value.trim(),
    canonical_symbol: row.querySelector('[data-field="canonical_symbol"]').value.trim(),
  })).filter(m => m.raw_symbol && !rawSymbols.includes(m.raw_symbol));
  const mappings = existing.concat(rawSymbols.map(raw => ({
    raw_symbol: raw, canonical_symbol: canonical,
  })));
  await put('/analyzer/symbol-map', { mappings }, `Symboles regroupés sous ${canonical}`);
}

export async function createDefaultSop() {
  if (!confirm('Créer un plan avec les 6 éléments par défaut ? '
    + "Si vous en avez déjà un, il est remplacé par une NOUVELLE version — "
    + 'vos analyses passées ne changent pas.')) return;
  try {
    const response = await fetch(`${API}/analyzer/sop`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    showToast('Plan réinitialisé aux 6 éléments par défaut');
    await refresh();
  } catch (error) {
    showToast(`Réinitialisation impossible : ${error.message}`);
  }
}

async function put(path, body, successMessage) {
  try {
    const response = await fetch(`${API}${path}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error((await response.json()).detail || `HTTP ${response.status}`);
    showToast(successMessage);
    await refresh();
  } catch (error) {
    showToast(error.message);
  }
}

// Les graphiques de cet onglet sont en HTML/CSS (barres de couverture,
// pastilles de fiabilité) : aucun canvas Chart.js à recolorer. On se contente
// donc de rejouer le rendu, qui relit les tokens CSS du nouveau thème.
export function redrawAnalyzerTheme() {
  if (document.getElementById('page-analyzer')?.classList.contains('active')) refresh();
}
