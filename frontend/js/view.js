import { apiGet, state, loadAttachmentBlobUrl } from './config.js';
import { money, fmtDuration, escapeHtml, sourceBadge } from './utils.js';

const SETUP_LABELS = { breakout: 'Breakout', pullback: 'Pullback', news: 'News', range: 'Range', autre: 'Autre' };
const ERROR_LABELS = { aucune: 'Aucune erreur', entree_trop_tot: 'Entrée trop tôt', sur_sizing: 'Sur-sizing', pas_de_stop: 'Pas de stop', fomo: 'FOMO', autre: 'Autre' };
const EMOTION_LABELS = { calme: 'Calme', stresse: 'Stressé', confiant: 'Confiant' };
const LABEL_TEXT = { avant: 'Avant', apres: 'Après', autre: 'Autre' };

function fmtDt(iso) {
  if (!iso) return '—';
  const d = new Date(iso);
  return d.toLocaleDateString('fr-FR', { day: '2-digit', month: '2-digit', year: '2-digit' }) + ' ' + d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
}

// ── Fiche de trade : VISIONNAGE UNIQUEMENT ──────────────────────────────────
// Aucun champ éditable ici, aucune écriture possible (ni PATCH, ni upload/
// suppression de pièce jointe) : c'est le rôle exclusif de la fiche de
// modification (voir edit.js / bouton ✎). Le bouton "Modifier" en bas bascule
// vers cette fiche de modification.
export function openViewModal(ticket) {
  const trade = state.allTrades.find(t => t.ticket === ticket);
  if (!trade) return;
  state.viewModalTicket = ticket;

  // Un ticket négatif est un identifiant INTERNE (trade saisi à la main, ou
  // ligne de CSV sans colonne ticket) : ce n'est pas un numéro de broker, on
  // ne l'affiche pas comme tel. Un import de rapport MT5 garde le vrai ticket.
  const isMt5 = !trade.source || trade.source === 'mt5';
  document.getElementById('view-modal-title').textContent = trade.ticket > 0 ? `${trade.symbol} #${trade.ticket}` : trade.symbol;
  document.getElementById('view-modal-source').innerHTML = sourceBadge(trade.source || 'mt5');
  const pnlEl = document.getElementById('view-modal-pnl');
  pnlEl.textContent = money(trade.profit);
  pnlEl.className = `view-head-pnl ${trade.profit >= 0 ? 'pos' : 'neg'}`;

  document.getElementById('view-trade-grid').innerHTML = [
    ['Sens', trade.direction === 'buy' ? 'Achat' : 'Vente'],
    ['Volume', `${trade.volume} lots`],
    ['Statut', trade.is_open ? 'Ouvert' : 'Clôturé'],
    ['Ouverture', fmtDt(trade.open_time)],
    ['Clôture', fmtDt(trade.close_time)],
    ['Durée', trade.close_time ? fmtDuration((new Date(trade.close_time) - new Date(trade.open_time)) / 60000) : '—'],
    ['Prix ouv.', trade.open_price],
    ['Prix clô.', trade.close_price != null ? trade.close_price : '—'],
    ['Pips', `${trade.pips >= 0 ? '+' : ''}${trade.pips}`],
    ['Stop Loss', trade.sl != null ? trade.sl : '—'],
    ['Take Profit', trade.tp != null ? trade.tp : '—'],
    ['Commission / Swap', `${trade.commission} / ${trade.swap}`],
  ].map(([label, value]) => `
    <div><div class="view-item-label">${label}</div><div class="view-item-value">${escapeHtml(value)}</div></div>
  `).join('');

  document.getElementById('view-tags-grid').innerHTML = [
    ['Tag setup', trade.setup_tag ? (SETUP_LABELS[trade.setup_tag] || trade.setup_tag) : '—'],
    ['Tag erreur', trade.error_tag ? (ERROR_LABELS[trade.error_tag] || trade.error_tag) : '—'],
    ['Émotion', trade.emotion ? (EMOTION_LABELS[trade.emotion] || trade.emotion) : '—'],
    ['Playbook', trade.playbook || '—'],
    ['Raison de sortie', trade.exit_reason || '—'],
    ['Timeframe entrée', trade.entry_timeframe || '—'],
    ['Risque', trade.risk_percent_effective != null
      ? `${trade.risk_percent_effective}%${trade.risk_percent_source === 'auto' ? ' (auto)' : ''}`
      : '—'],
    ['R-multiple', trade.r_multiple != null ? `${trade.r_multiple >= 0 ? '+' : ''}${trade.r_multiple.toFixed(2)}R` : '—'],
    [isMt5 ? 'Commentaire MT5' : 'Commentaire', trade.comment || '—'],
  ].map(([label, value]) => `
    <div><div class="view-item-label">${label}</div><div class="view-item-value">${escapeHtml(value)}</div></div>
  `).join('');

  const noteBox = document.getElementById('view-note-box');
  if (trade.notes && trade.notes.trim()) {
    noteBox.textContent = trade.notes;
    noteBox.classList.remove('view-note-empty');
  } else {
    noteBox.textContent = 'Aucune note pour ce trade.';
    noteBox.classList.add('view-note-empty');
  }

  document.getElementById('view-modal').style.display = 'flex';
  loadAttachmentsReadOnly(ticket);
}

export function closeViewModal() {
  document.getElementById('view-modal').style.display = 'none';
  state.viewModalTicket = null;
}

async function loadAttachmentsReadOnly(ticket) {
  const grid = document.getElementById('view-attach-grid');
  grid.innerHTML = '<div class="attach-empty">Chargement…</div>';
  try {
    const items = await apiGet(`/trades/${ticket}/attachments`);
    if (!items.length) {
      grid.innerHTML = '<div class="attach-empty">Aucune pièce jointe.</div>';
      return;
    }
    // Lecture seule : pas de bouton supprimer ici (voir la fiche de
    // modification pour ajouter/supprimer des pièces jointes).
    grid.innerHTML = items.map(a => `
      <div class="attach-item">
        <img src="" data-attachment-filename="${escapeHtml(a.filename)}" alt="${escapeHtml(a.original_name || '')}">
        <div class="attach-item-label">${escapeHtml(LABEL_TEXT[a.label] || a.label)}</div>
      </div>
    `).join('');
    grid.querySelectorAll('img[data-attachment-filename]').forEach(image => {
      loadAttachmentBlobUrl(image.dataset.attachmentFilename).then(url => {
        image.src = url;
        image.addEventListener('click', () => openAttachmentLightbox(url));
      }).catch(() => image.remove());
    });
  } catch (e) {
    grid.innerHTML = '<div class="attach-empty">Impossible de charger les pièces jointes</div>';
  }
}

export function openAttachmentLightbox(url) {
  const box = document.createElement('div');
  box.className = 'attach-lightbox';
  const image = document.createElement('img');
  image.src = url;
  box.appendChild(image);
  box.onclick = () => box.remove();
  document.body.appendChild(box);
}
