import { apiGet, state } from './config.js';
import { money, curSym, escapeHtml, setLoading, showToast } from './utils.js';
import { chartColors } from './theme.js';

// Dernière réponse reçue, gardée pour recolorer le graphique (changement de
// thème) sans refaire l'appel réseau — voir redrawSymbolsTheme.
let _lastSymbolsData = null;

export async function loadSymbols() {
  const wrap = document.getElementById('chartSymbols')?.closest('.chart-wrap');
  setLoading(wrap, true);
  try {
    const data = await apiGet('/performance/by-symbol');
    _lastSymbolsData = data;
    renderSymbols(data);
  } catch(e) {
    console.warn('symbols', e);
    showToast(`Vue par symbole indisponible : ${e.message}`);
  }
  finally { setLoading(wrap, false); }
}

function renderSymbols(data) {
  const c = chartColors();
  if (state.symChart) state.symChart.destroy();
  state.symChart = new Chart(document.getElementById('chartSymbols'), {
    type: 'bar',
    data: { labels: data.map(d => d.symbol),
      datasets: [{ data: data.map(d => d.pnl), backgroundColor: data.map(d => d.pnl >= 0 ? c.posSoft : c.negSoft), borderColor: data.map(d => d.pnl >= 0 ? c.pos : c.neg), borderWidth: 1.5 }]},
    options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false }},
      datasets: { bar: { maxBarThickness: 52, categoryPercentage: 0.5, barPercentage: 0.7 } },
      scales: { x: { grid: { display: false }, ticks: { font: { family: 'DM Mono', size: 12 }}}, y: { border: { display: false }, grid: { color: c.grid }, ticks: { callback: v => curSym()+v, font: { family: 'DM Mono' }}}}}
  });

  document.getElementById('sym-list').innerHTML = data.map(d => `
    <div class="trade-row" style="grid-template-columns:1fr 100px 100px 80px">
      <span style="font-weight:500">${escapeHtml(d.symbol)}</span>
      <span class="${d.pnl >= 0 ? 'pos' : 'neg'}">${money(d.pnl)}</span>
      <span>${d.win_rate}%</span>
      <span style="color:var(--muted)">${d.trades}</span>
    </div>`).join('');
}

export function redrawSymbolsTheme() {
  if (_lastSymbolsData) renderSymbols(_lastSymbolsData);
}
