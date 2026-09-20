import { apiGet } from './config.js';
import { money, escapeHtml, showToast } from './utils.js';

const LABELS = {
  StopLoss: 'StopLoss', TakeProfit: 'TakeProfit', Manuel: 'Manuel', Breakeven: 'Breakeven',
  breakout: 'Breakout', pullback: 'Pullback', news: 'News', range: 'Range', autre: 'Autre',
};
const label = (v) => LABELS[v] || v;

function pctCell(pct) {
  const v = pct != null ? pct : 0;
  return `<span class="pct-cell">${v}%</span>`;
}

function rowsOrEmpty(id, rows, cols) {
  const el = document.getElementById(id);
  if (!rows.length) {
    el.innerHTML = `<div class="repart-empty" style="grid-column:1/-1">Aucune donnée — renseignez ce champ depuis la note d'un trade</div>`;
    return;
  }
  el.innerHTML = rows.map(cols).join('');
}

export async function loadRepartitions() {
  try {
    const d = await apiGet('/performance/repartitions');

    rowsOrEmpty('repart-symbol', d.by_symbol, (row) => `
      <div class="repart-row repart-5b">
        <span style="text-align:left">${escapeHtml(row.symbol)}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    const ls = d.long_short;
    const totalTrades = (ls.long.trades || 0) + (ls.short.trades || 0);
    const longPct = totalTrades ? Math.round(ls.long.trades / totalTrades * 100) : 0;
    document.getElementById('ls-bar-fill').style.width = longPct + '%';
    const metrics = [
      ['Trades', ls.long.trades, ls.short.trades, false],
      ['Taux de réussite', ls.long.win_rate + '%', ls.short.win_rate + '%', false],
      ['P&L', money(ls.long.pnl), money(ls.short.pnl), true],
      ['P&L moy./trade', money(ls.long.avg_pnl), money(ls.short.avg_pnl), true],
      ['Gain moyen', money(ls.long.avg_win), money(ls.short.avg_win), true],
      ['Perte moyenne', money(ls.long.avg_loss), money(ls.short.avg_loss), true],
    ];
    document.getElementById('repart-long-short').innerHTML = metrics.map(([lab, l, s, colored]) => `
      <div class="repart-row repart-3">
        <span style="text-align:left;color:var(--muted)">${lab}</span>
        <span class="${colored ? (String(l).startsWith('-') ? 'neg' : 'pos') : ''}">${l}</span>
        <span class="${colored ? (String(s).startsWith('-') ? 'neg' : 'pos') : ''}">${s}</span>
      </div>`).join('');

    rowsOrEmpty('repart-tag', d.by_tag, (row) => `
      <div class="repart-row repart-6">
        <span style="text-align:left">${escapeHtml(label(row.tag))}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span>${row.avg_r != null ? (row.avg_r >= 0 ? '+' : '') + row.avg_r.toFixed(2) + 'R' : '—'}</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    rowsOrEmpty('repart-playbook', d.by_playbook, (row) => `
      <div class="repart-row repart-6">
        <span style="text-align:left">${escapeHtml(row.playbook)}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span>${row.avg_r != null ? (row.avg_r >= 0 ? '+' : '') + row.avg_r.toFixed(2) + 'R' : '—'}</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    rowsOrEmpty('repart-exit', d.by_exit_reason, (row) => `
      <div class="repart-row repart-6" title="${row.avg_risk != null ? 'Risque moyen : ' + row.avg_risk + '%' : ''}">
        <span style="text-align:left">${escapeHtml(label(row.reason))}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span>${row.avg_r != null ? (row.avg_r >= 0 ? '+' : '') + row.avg_r.toFixed(2) + 'R' : '—'}</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    rowsOrEmpty('repart-duration', d.by_duration.filter(r => r.trades > 0), (row) => `
      <div class="repart-row repart-5b">
        <span style="text-align:left">${escapeHtml(row.duration)}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    rowsOrEmpty('repart-timeframe', d.by_timeframe, (row) => `
      <div class="repart-row repart-6">
        <span style="text-align:left">${escapeHtml(row.timeframe)}</span><span>${row.trades}</span>${pctCell(row.pct_of_total)}<span>${row.win_rate}%</span>
        <span>${row.avg_r != null ? (row.avg_r >= 0 ? '+' : '') + row.avg_r.toFixed(2) + 'R' : '—'}</span>
        <span class="${row.pnl >= 0 ? 'pos' : 'neg'}">${money(row.pnl)}</span>
      </div>`);

    renderDiscipline(d.discipline);
  } catch (e) {
    console.warn('repartitions', e);
    showToast(`Répartitions indisponibles : ${e.message}`);
  }
}

function renderDiscipline(disc) {
  const gradeClass = (g) => g === 'N/A' ? 'grade-NA' : `grade-${g}`;
  const ring = document.getElementById('discipline-ring');
  ring.textContent = disc.overall_grade;
  ring.className = 'discipline-ring ' + gradeClass(disc.overall_grade);

  document.getElementById('discipline-list').innerHTML = [
    ['Usage du Stop Loss', disc.sl_grade],
    ['Taille de position', disc.sizing_grade],
    ['Discipline horaire', disc.hourly_grade],
  ].map(([lab, g]) => `
    <div class="discipline-item">
      <span>${lab}</span>
      <span class="grade-pill ${gradeClass(g)}">${g}</span>
    </div>`).join('');

  document.getElementById('discipline-series').textContent =
    `Séries : ${disc.longest_win_streak}G · ${disc.longest_loss_streak}P · Jours profitables ${disc.profitable_days_pct}%`;
}
