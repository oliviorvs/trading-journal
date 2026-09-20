import { API, apiGet, state, loadAttachmentBlobUrl } from './config.js';
import { showToast, escapeHtml } from './utils.js';
import { applyTradeFilters } from './trades.js';
import { closeViewModal } from './view.js';

const LABEL_TEXT = { avant: 'Avant', apres: 'Après', autre: 'Autre' };

// Champs de DONNÉES du trade (formulaire) : éditables seulement pour un trade
// manuel/importé et à la création. Un trade MT5 garde prix/volume/dates du
// broker (le backend refuse ces champs avec une erreur 400).
const DATA_INPUT_IDS = [
  'inp-symbol', 'inp-direction', 'inp-open-time', 'inp-close-time', 'inp-open-price',
  'inp-close-price', 'inp-volume', 'inp-profit', 'inp-sl', 'inp-tp', 'inp-commission', 'inp-swap', 'inp-margin',
];

// L'API renvoie des heures naïves ("2026-09-04T16:43:50") : heure serveur du
// broker, jamais convertie. On les met telles quelles dans le champ
// datetime-local et on les renvoie telles quelles — aucun décalage de fuseau.
function toInputDateTime(iso) {
  return iso ? String(iso).slice(0, 19) : '';
}

function fromInputDateTime(value) {
  if (!value) return null;
  return value.length === 16 ? `${value}:00` : value;
}

function numOrNull(id) {
  const raw = document.getElementById(id).value;
  return raw === '' ? null : Number(raw);
}

function setDataSectionVisible(visible) {
  document.getElementById('edit-data-section').style.display = visible ? '' : 'none';
}

function fillDataFields(trade) {
  const set = (id, v) => { document.getElementById(id).value = v == null ? '' : v; };
  set('inp-symbol', trade ? trade.symbol : '');
  set('inp-direction', trade ? trade.direction : 'buy');
  set('inp-open-time', trade ? toInputDateTime(trade.open_time) : '');
  set('inp-close-time', trade ? toInputDateTime(trade.close_time) : '');
  set('inp-open-price', trade ? trade.open_price : '');
  set('inp-close-price', trade ? trade.close_price : '');
  set('inp-volume', trade ? trade.volume : '');
  set('inp-profit', trade ? trade.profit : '');
  set('inp-sl', trade ? trade.sl : '');
  set('inp-tp', trade ? trade.tp : '');
  set('inp-commission', trade ? trade.commission : '');
  set('inp-swap', trade ? trade.swap : '');
  set('inp-margin', trade && trade.margin != null ? trade.margin : '');
}

// Message lisible depuis une réponse d'erreur : `detail` est une chaîne
// (erreurs métier du backend) ou une liste (validation Pydantic).
async function errorMessage(response, fallback) {
  const body = await response.json().catch(() => ({}));
  const d = body && body.detail;
  if (typeof d === 'string') return d;
  if (Array.isArray(d) && d.length) {
    const first = d[0];
    const field = Array.isArray(first.loc) ? first.loc[first.loc.length - 1] : '';
    return field ? `${field} : ${first.msg}` : first.msg;
  }
  return fallback;
}

// Recharge tout ce qui dépend des données du trade (métriques, courbe,
// liste, solde du compte) après une création ou une modification de données.
async function refreshAfterDataChange() {
  const { loadAll } = await import('./dashboard.js');
  const { loadAccountInfo } = await import('./account.js');
  await loadAll();
  await loadAccountInfo();
}

// ── Fiche de modification : regroupe TOUTES les écritures liées à un trade
// (tags, contexte, note, ajout/suppression de pièces jointes). C'est le seul
// endroit de l'app qui modifie un trade — la fiche "vue" (view.js) reste
// strictement en lecture.
export function openEditModal(ticket) {
  const trade = state.allTrades.find(t => t.ticket === ticket);
  if (!trade) return;
  state.editModalTicket = ticket;
  state.editModalMode = 'edit';

  document.getElementById('edit-modal-title').textContent = ticket > 0 ? `Modifier — ${trade.symbol} #${ticket}` : `Modifier — ${trade.symbol}`;
  // Données éditables uniquement hors MT5 ; pièces jointes toujours dispo.
  const editableData = trade.source !== 'mt5';
  setDataSectionVisible(editableData);
  if (editableData) fillDataFields(trade);
  document.getElementById('edit-attach-section').style.display = '';
  document.getElementById('inp-setup-tag').value = trade.setup_tag || '';
  document.getElementById('inp-error-tag').value = trade.error_tag || '';
  document.getElementById('inp-emotion').value = trade.emotion || '';
  document.getElementById('inp-playbook').value = trade.playbook || '';
  document.getElementById('inp-exit-reason').value = trade.exit_reason || '';
  document.getElementById('inp-entry-timeframe').value = trade.entry_timeframe || '';
  document.getElementById('inp-risk-percent').value = trade.risk_percent != null ? trade.risk_percent : '';
  const hint = document.getElementById('risk-auto-hint');
  if (hint) {
    if (trade.risk_percent == null && trade.risk_percent_effective != null) {
      hint.textContent = `Estimé automatiquement depuis le SL : ${trade.risk_percent_effective}% du capital du moment. Laissez vide pour garder ce calcul, ou saisissez une valeur pour la remplacer.`;
      hint.style.display = '';
    } else if (trade.sl == null) {
      hint.textContent = 'Aucun Stop Loss renseigné : le risque ne peut pas être estimé automatiquement, saisissez-le manuellement si besoin.';
      hint.style.display = '';
    } else {
      hint.style.display = 'none';
    }
  }
  document.getElementById('notes-textarea').value = trade.notes || '';

  document.getElementById('edit-modal').style.display = 'flex';
  loadEditAttachments(ticket);
}

// Ajout manuel d'un trade (compte manuel) : même modale, mode « création ».
// Les pièces jointes ne sont proposées qu'après création (elles sont liées
// au ticket, attribué par le serveur).
export function openCreateTradeModal() {
  state.editModalTicket = null;
  state.editModalMode = 'create';

  document.getElementById('edit-modal-title').textContent = 'Ajouter un trade';
  setDataSectionVisible(true);
  fillDataFields(null);
  ['inp-setup-tag', 'inp-error-tag', 'inp-emotion', 'inp-playbook', 'inp-exit-reason',
   'inp-entry-timeframe', 'inp-risk-percent', 'notes-textarea']
    .forEach(id => { document.getElementById(id).value = ''; });
  const hint = document.getElementById('risk-auto-hint');
  if (hint) hint.style.display = 'none';
  document.getElementById('edit-attach-section').style.display = 'none';

  document.getElementById('edit-modal').style.display = 'flex';
}

export function closeEditModal() {
  document.getElementById('edit-modal').style.display = 'none';
  state.editModalTicket = null;
  state.editModalMode = 'edit';
}

// Pont depuis la fiche "vue" : ferme le visionnage et ouvre directement la
// modification du même trade.
export function editFromView() {
  const ticket = state.viewModalTicket;
  closeViewModal();
  if (ticket != null) openEditModal(ticket);
}

export async function saveNotes() {
  const creating = state.editModalMode === 'create';
  const ticket = state.editModalTicket;
  if (!creating && ticket == null) return;
  const existing = creating ? null : state.allTrades.find(t => t.ticket === ticket);
  // Données envoyées seulement à la création et pour un trade non-MT5.
  const withData = creating || (existing && existing.source !== 'mt5');

  const riskRaw = document.getElementById('inp-risk-percent').value;
  const body = {
    notes: document.getElementById('notes-textarea').value,
    setup_tag: document.getElementById('inp-setup-tag').value || null,
    error_tag: document.getElementById('inp-error-tag').value || null,
    emotion: document.getElementById('inp-emotion').value || null,
    playbook: document.getElementById('inp-playbook').value || null,
    exit_reason: document.getElementById('inp-exit-reason').value || null,
    entry_timeframe: document.getElementById('inp-entry-timeframe').value || null,
    risk_percent: riskRaw !== '' ? Number(riskRaw) : null,
  };

  if (withData) {
    const symbol = document.getElementById('inp-symbol').value.trim();
    const openTime = fromInputDateTime(document.getElementById('inp-open-time').value);
    const openPrice = numOrNull('inp-open-price');
    const volume = numOrNull('inp-volume');
    // Contrôles de saisie évidents côté client (message immédiat) ; toutes les
    // règles de fond (prix > 0, clôture ≥ ouverture…) sont revérifiées par le serveur.
    if (!symbol) return showToast('Symbole requis');
    if (!openTime) return showToast("Date d'ouverture requise");
    if (openPrice == null) return showToast("Prix d'ouverture requis");
    if (volume == null) return showToast('Volume requis');
    Object.assign(body, {
      symbol,
      direction: document.getElementById('inp-direction').value,
      open_time: openTime,
      close_time: fromInputDateTime(document.getElementById('inp-close-time').value),
      open_price: openPrice,
      close_price: numOrNull('inp-close-price'),
      volume,
      sl: numOrNull('inp-sl'),
      tp: numOrNull('inp-tp'),
      // Profit, commission, swap : vides = 0 (jamais null côté serveur).
      profit: numOrNull('inp-profit') ?? 0,
      commission: numOrNull('inp-commission') ?? 0,
      swap: numOrNull('inp-swap') ?? 0,
      // Marge : facultative (vide = estimée quand c'est possible).
      margin: numOrNull('inp-margin'),
    });
  }

  try {
    const r = await fetch(creating ? `${API}/trades` : `${API}/trades/${ticket}`, {
      method: creating ? 'POST' : 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) {
      // Message précis du serveur (validation des prix, dates, volume…) ;
      // repli sur le message historique pour la plage du risque.
      const fallback = r.status === 422
        ? 'Saisie invalide — vérifiez les champs (risque entre 0 et 100 %, prix et volume positifs)'
        : "Erreur lors de l'enregistrement";
      throw new Error(await errorMessage(r, fallback));
    }
    const saved = await r.json();
    if (!creating) {
      const idx = state.allTrades.findIndex(t => t.ticket === ticket);
      if (idx !== -1) state.allTrades[idx] = saved;
    }
    showToast(creating ? 'Trade ajouté' : 'Modifications enregistrées');
    closeEditModal();
    state.tradesCache.clear();
    if (creating) state.tradesPage = 0;
    if (withData) {
      // Profit, prix ou dates changés : métriques, courbe et solde bougent.
      await refreshAfterDataChange();
    } else {
      // Réapplique les filtres réellement actifs sur la page Trades.
      await applyTradeFilters();
    }
  } catch (e) { showToast(e.message || 'Erreur lors de l\'enregistrement'); }
}

// ── Pièces jointes (ajout / suppression) ────────────────────────────────────

async function loadEditAttachments(ticket) {
  const grid = document.getElementById('edit-attach-grid');
  grid.innerHTML = '<div class="attach-empty">Chargement…</div>';
  try {
    const items = await apiGet(`/trades/${ticket}/attachments`);
    renderEditAttachments(items);
  } catch (e) {
    grid.innerHTML = '<div class="attach-empty">Impossible de charger les pièces jointes</div>';
  }
}

function renderEditAttachments(items) {
  const grid = document.getElementById('edit-attach-grid');
  if (!items.length) {
    grid.innerHTML = '<div class="attach-empty">Aucune pièce jointe — ajoutez une capture d\'écran du graphique ou du setup.</div>';
    return;
  }
  grid.innerHTML = items.map(a => `
    <div class="attach-item">
      <img src="" data-attachment-filename="${escapeHtml(a.filename)}" alt="${escapeHtml(a.original_name || '')}">
      <div class="attach-item-label">${escapeHtml(LABEL_TEXT[a.label] || a.label)}</div>
      <button class="attach-item-remove" onclick="deleteAttachment(${a.id}, ${state.editModalTicket})" title="Supprimer">✕</button>
    </div>
  `).join('');
  grid.querySelectorAll('img[data-attachment-filename]').forEach(image => {
    loadAttachmentBlobUrl(image.dataset.attachmentFilename).then(url => {
      image.src = url;
      image.addEventListener('click', () => openAttachmentLightbox(url));
    }).catch(() => image.remove());
  });
}

export async function uploadAttachment() {
  const ticket = state.editModalTicket;
  if (ticket == null) return;
  const fileInput = document.getElementById('attach-file-input');
  const label = document.getElementById('attach-label-select').value;
  const file = fileInput.files[0];
  if (!file) { showToast('Choisissez une image d\'abord'); return; }

  const formData = new FormData();
  formData.append('file', file);
  formData.append('label', label);
  try {
    const r = await fetch(`${API}/trades/${ticket}/attachments`, { method: 'POST', body: formData });
    if (!r.ok) throw new Error();
    fileInput.value = '';
    showToast('Pièce jointe ajoutée');
    await loadEditAttachments(ticket);
  } catch (e) { showToast('Erreur lors de l\'envoi de l\'image'); }
}

export async function deleteAttachment(id, ticket) {
  if (!confirm('Supprimer cette pièce jointe ?')) return;
  try {
    const r = await fetch(`${API}/attachments/${id}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) throw new Error();
    showToast('Pièce jointe supprimée');
    await loadEditAttachments(ticket);
  } catch (e) { showToast('Erreur lors de la suppression'); }
}
