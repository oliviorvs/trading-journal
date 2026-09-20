import { API, apiGet, state } from './config.js';
import { money, showToast, currentSymbol, escapeHtml, sourceBadge, confirmAction } from './utils.js';

let tradesRequestId = 0;

export async function loadTrades() {
  try {
    await populateSymbolFilters();
    await applyTradeFilters();
  } catch(e) {
    console.warn('trades', e);
    showToast(`Trades indisponibles : ${e.message}`);
  }
}

// Remplit les filtres symbole (page Trades + dashboard) à partir de
// /api/trades/symbols plutôt que de `state.allTrades` : ce dernier ne
// contient qu'une page de trades (state.tradesPageSize), donc les
// symboles présents uniquement au-delà de la 1ère page manquaient au menu
// déroulant dès que le compte dépassait 100 trades.
async function populateSymbolFilters() {
  const tfSym = document.getElementById('tf-symbol');
  const sel = document.getElementById('sym-select');
  const currentTf = tfSym.value;
  const current = currentSymbol();
  try {
    const syms = await apiGet('/trades/symbols');
    tfSym.innerHTML = '<option value="">Tous</option>' + syms.map(s => `<option>${escapeHtml(s)}</option>`).join('');
    tfSym.value = currentTf;
    sel.innerHTML = '<option value="">Tous les symboles</option>' + syms.map(s => `<option>${escapeHtml(s)}</option>`).join('');
    sel.value = current;
  } catch(e) {
    // Remonté à loadTrades(), qui prévient l'utilisateur une seule fois.
    console.warn('symbols', e);
    throw e;
  }
}

// ── Filtres de la page Trades (symbole / sens / résultat / période) ─────────
// Les filtres sont envoyés au backend (qui les supporte déjà nativement sur
// /api/trades) plutôt qu'appliqués seulement côté client, pour rester
// cohérents avec les autres pages et permettre le filtre par date.
export async function applyTradeFilters() {
  const requestId = ++tradesRequestId;
  const params = new URLSearchParams();
  const symbol = document.getElementById('tf-symbol').value;
  const direction = document.getElementById('tf-direction').value;
  const result = document.getElementById('tf-result').value;
  const from = document.getElementById('tf-from').value;
  const to = document.getElementById('tf-to').value;
  const source = currentSourceFilter();
  if (symbol) params.set('symbol', symbol);
  if (direction) params.set('direction', direction);
  if (result) params.set('result', result);
  if (source) params.set('source', source);
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);
  try {
    params.set('limit', state.tradesPageSize);
    params.set('offset', state.tradesPage * state.tradesPageSize);
    // La clé porte l'« époque » du compte actif (voir config.js) en plus des
    // filtres. Avant, elle ne contenait QUE les filtres : deux comptes
    // différents consultés à moins de 30 s d'intervalle avec les mêmes
    // filtres partageaient la même entrée, et le second se voyait servir les
    // trades du premier. La purge au changement de compte corrigeait le cas
    // courant, mais rien n'empêchait structurellement la collision — c'est
    // maintenant impossible, même si un futur chemin oublie de purger.
    const cacheKey = `a${state.accountEpoch}|${params.toString()}`;
    const cached = state.tradesCache.get(cacheKey);
    let data;
    if (cached && cached.expires > Date.now()) {
      data = cached.data;
    } else {
      data = await apiGet(`/trades?${params.toString()}`);
      state.tradesCache.set(cacheKey, { data, expires: Date.now() + 30000 });
    }
    if (requestId !== tradesRequestId) return;
    const maxPage = Math.max(0, Math.ceil(data.total / state.tradesPageSize) - 1);
    if (state.tradesPage > maxPage) {
      state.tradesPage = maxPage;
      applyTradeFilters();
      return;
    }
    state.tradesTotal = data.total;
    state.allTrades = data.items;
    renderTradeList(data.items);
    renderPagination();
  } catch(e) {
    console.warn('filter trades', e);
    showToast(`Chargement des trades impossible : ${e.message}`);
  }
}

// Le filtre « Source » n'existe que sur un compte manuel (saisies + imports) :
// sur un compte MT5, tout vient de la synchro. Il est masqué dans ce cas (voir
// applyAccountModeUI, account.js) ; on ignore alors sa valeur, même résiduelle
// d'un compte manuel consulté juste avant.
function currentSourceFilter() {
  if (state.accountMode !== 'manual') return '';
  const el = document.getElementById('tf-source');
  return el ? el.value : '';
}

export function resetTradeFilters() {
  document.getElementById('tf-symbol').value = '';
  document.getElementById('tf-direction').value = '';
  document.getElementById('tf-result').value = '';
  const sourceSelect = document.getElementById('tf-source');
  if (sourceSelect) sourceSelect.value = '';
  document.getElementById('tf-from').value = '';
  document.getElementById('tf-to').value = '';
  state.tradesPage = 0;
  applyTradeFilters();
}

export function changeTradePageSize(size) {
  const pageSize = Number(size);
  if (![10, 20].includes(pageSize)) return;
  state.tradesPageSize = pageSize;
  state.tradesPage = 0;
  applyTradeFilters();
}

export function changeTradePage(delta) {
  const maxPage = Math.max(0, Math.ceil(state.tradesTotal / state.tradesPageSize) - 1);
  state.tradesPage = Math.min(maxPage, Math.max(0, state.tradesPage + delta));
  applyTradeFilters();
}

function renderPagination() {
  const container = document.getElementById('trade-pagination');
  if (!container) return;
  const maxPage = Math.max(0, Math.ceil(state.tradesTotal / state.tradesPageSize) - 1);
  container.innerHTML = `<button onclick="changeTradePage(-1)" ${state.tradesPage === 0 ? 'disabled' : ''} aria-label="Page précédente">‹</button><span>Page ${state.tradesPage + 1} / ${maxPage + 1} · ${state.tradesTotal} trades</span><button onclick="changeTradePage(1)" ${state.tradesPage >= maxPage ? 'disabled' : ''} aria-label="Page suivante">›</button>`;
}

// ── Export PDF du journal (trades + notes) sur les filtres actuellement
// appliqués (symbole/sens/résultat/période) ──────────────────────────────
// Réutilise volontairement les mêmes champs que applyTradeFilters() (voir
// plus haut), pour que le PDF reflète exactement ce que l'utilisateur voit
// à l'écran sur la page Trades — pas seulement la période (correction #11).
export async function exportJournalPdf() {
  const symbol = document.getElementById('tf-symbol').value;
  const direction = document.getElementById('tf-direction').value;
  const result = document.getElementById('tf-result').value;
  const from = document.getElementById('tf-from').value;
  const to = document.getElementById('tf-to').value;
  const source = currentSourceFilter();
  const params = new URLSearchParams();
  if (symbol) params.set('symbol', symbol);
  if (direction) params.set('direction', direction);
  if (result) params.set('result', result);
  if (source) params.set('source', source);
  if (from) params.set('date_from', from);
  if (to) params.set('date_to', to);

  const btn = document.getElementById('btn-export-pdf');
  const originalText = btn.textContent;
  btn.disabled = true;
  btn.textContent = 'Génération...';
  try {
    const r = await fetch(`${API}/export/pdf?${params.toString()}`);
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || 'Export échoué');
    }
    const blob = await r.blob();
    // Récupère le nom de fichier suggéré par le backend (Content-Disposition)
    // plutôt que d'en recalculer un côté client, pour rester cohérent avec
    // la période réellement utilisée par l'export (ex. si l'un des deux
    // champs est vide).
    const disposition = r.headers.get('Content-Disposition') || '';
    const match = disposition.match(/filename="?([^"]+)"?/);
    const filename = match ? match[1] : 'journal_trading.pdf';

    if (window.pywebview && window.pywebview.api && window.pywebview.api.save_pdf) {
      const encodedPdf = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result.split(',')[1]);
        reader.onerror = () => reject(new Error('Lecture du PDF impossible'));
        reader.readAsDataURL(blob);
      });
      const saved = await window.pywebview.api.save_pdf(filename, encodedPdf);
      showToast(saved ? 'PDF enregistré' : 'Enregistrement annulé');
    } else {
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      showToast('PDF exporté');
    }
  } catch(e) {
    showToast('Erreur : ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = originalText;
  }
}

// ── Trade list ────────────────────────────────────────────────────────────────

export function renderTradeList(list) {
  // Sur un compte MT5 (ou une page 100 % MT5), un badge « MT5 » sur chaque
  // ligne ne dit rien : on ne l'affiche que lorsque la page mélange plusieurs
  // origines — ou pour un trade saisi / importé.
  const mixedSources = list.some(t => t.source && t.source !== 'mt5');
  document.getElementById('trade-list').innerHTML = list.map(t => {
    // Année omise ici (colonne resserrée pour tenir sur un écran 1360px) —
    // la date complète reste disponible dans la fiche "Voir" du trade.
    const openDt = new Date(t.open_time).toLocaleDateString('fr-FR', { day:'2-digit', month:'2-digit' }) + ' ' + new Date(t.open_time).toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit' });
    const closeDt = t.close_time ? (new Date(t.close_time).toLocaleDateString('fr-FR', { day:'2-digit', month:'2-digit' }) + ' ' + new Date(t.close_time).toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit' })) : '—';
    const statusLabel = t.is_open ? '<span class="badge open-badge">OUVERT</span>' : '<span style="color:var(--muted);font-size:11px">Clôturée</span>';
    const hasNote = !!(t.notes && t.notes.trim());
    // Note affichée directement dans le tableau (aperçu tronqué, texte complet au survol)
    // escapeHtml() (pas un simple remplacement de guillemets) : cette valeur
    // est utilisée à la fois comme attribut title="..." et comme contenu
    // HTML du span juste en dessous, donc <, >, & doivent aussi être échappés.
    const noteText = hasNote ? escapeHtml(t.notes.trim()) : '—';
    const riskCell = t.risk_percent_effective != null
      ? `${t.risk_percent_effective}%${t.risk_percent_source === 'auto' ? '<span class="risk-auto-mark" title="Estimé automatiquement depuis le Stop Loss">auto</span>' : ''}`
      : '—';
    const rCell = t.r_multiple != null ? `${t.r_multiple >= 0 ? '+' : ''}${t.r_multiple.toFixed(2)}R` : '—';
    // `data-label` n'est lu que par l'affichage en cartes (voir
    // trade-table.css, sous 900px) : chaque cellule y affiche son intitulé
    // de colonne, puisque la ligne d'en-tête disparaît à ce format.
    return `<div class="trade-row">
      <span class="cell-symbol" data-label="Symbole" style="font-weight:500" title="${escapeHtml(t.symbol)}">${escapeHtml(t.symbol)}</span>
      <span data-label="Sens"><span class="badge badge-${t.direction}">${t.direction === 'buy' ? 'Achat' : 'Vente'}</span></span>
      <span data-label="Vol.">${t.volume}</span>
      <span data-label="Ouverture" style="color:var(--muted)" title="${new Date(t.open_time).toLocaleString('fr-FR')}">${openDt}</span>
      <span data-label="Clôture" style="color:var(--muted)" title="${t.close_time ? new Date(t.close_time).toLocaleString('fr-FR') : ''}">${closeDt}</span>
      <span data-label="Prix ouv.">${t.open_price}</span>
      <span data-label="Prix clô.">${t.close_price != null ? t.close_price : '—'}</span>
      <span data-label="Pips" style="color:var(--muted)">${t.pips >= 0 ? '+' : ''}${t.pips}</span>
      <span data-label="Profit" class="${t.profit >= 0 ? 'pos' : 'neg'}">${money(t.profit)}</span>
      <span data-label="Risque SL" title="${t.risk_percent_source === 'auto' ? 'Estimation automatique' : t.risk_percent_source === 'manuel' ? 'Saisi manuellement' : ''}">${riskCell}</span>
      <span data-label="Ratio R" class="${t.r_multiple >= 0 ? 'pos' : (t.r_multiple != null ? 'neg' : '')}">${rCell}</span>
      <span data-label="Note" class="note-preview ${hasNote ? 'has-note' : ''}" title="${noteText}">${noteText}</span>
      <span data-label="Statut" class="cell-status">${statusLabel}${t.source && (t.source !== 'mt5' || mixedSources) ? sourceBadge(t.source) : ''}</span>
      <span class="row-actions">
        <button class="view-btn" onclick="openViewModal(${t.ticket})" title="Voir le trade (lecture seule)">👁</button>
        <button class="note-btn ${hasNote ? 'has-note' : ''}" onclick="openEditModal(${t.ticket})" title="${t.source === 'mt5' ? 'Modifier (tags, note, pièces jointes)' : 'Modifier (données, tags, note, pièces jointes)'}">✎${hasNote ? '<span class="note-dot"></span>' : ''}</button>
        <button class="delete-btn" onclick="deleteTrade(${t.ticket})" title="Supprimer">✕</button>
      </span>
    </div>`;
  }).join('') || '<div style="padding:24px;text-align:center;color:var(--muted)">Aucun trade</div>';
}

export async function deleteTrade(ticket) {
  const trade = state.allTrades.find(t => t.ticket === ticket);
  // Trade saisi/importé : aucune synchro MT5 ne peut le recréer, la
  // suppression est donc définitive. Trade MT5 : il peut revenir.
  const definitive = trade && trade.source !== 'mt5';
  const label = ticket > 0 ? `le trade #${ticket}` : (trade ? `ce trade ${trade.symbol}` : 'ce trade');
  const message = definitive
    ? `Supprimer définitivement ${label} ? Cette action ne peut pas être annulée.`
    : `Supprimer ${label} du journal ? Cette action ne peut pas être annulée (il pourra revenir lors d'une prochaine synchronisation MT5).`;
  if (!await confirmAction(message, { confirmLabel: 'Supprimer' })) return;
  try {
    const r = await fetch(`${API}/trades/${ticket}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error();
    state.tradesCache.clear();
    if (definitive) {
      // Le capital du compte manuel dépend des trades : tout recharger.
      const { loadAll } = await import('./dashboard.js');
      const { loadAccountInfo } = await import('./account.js');
      await loadAll();
      await loadAccountInfo();
    } else {
      await applyTradeFilters();
    }
    showToast('Trade supprimé');
  } catch(e) { showToast('Erreur lors de la suppression'); }
}
