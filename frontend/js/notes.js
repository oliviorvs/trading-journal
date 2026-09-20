import { API, state } from './config.js';
import { showToast, currentSymbol } from './utils.js';
import { renderTradeList } from './trades.js';

export function openNotesModal(ticket) {
  const trade = state.allTrades.find(t => t.ticket === ticket);
  if (!trade) return;
  state.notesModalTicket = ticket;
  document.getElementById('notes-modal-title').textContent = `Note de trade — ${trade.symbol} #${ticket}`;
  document.getElementById('notes-textarea').value = trade.notes || '';
  document.getElementById('inp-setup-tag').value = trade.setup_tag || '';
  document.getElementById('inp-error-tag').value = trade.error_tag || '';
  document.getElementById('inp-emotion').value = trade.emotion || '';
  document.getElementById('inp-playbook').value = trade.playbook || '';
  document.getElementById('inp-exit-reason').value = trade.exit_reason || '';
  document.getElementById('inp-entry-timeframe').value = trade.entry_timeframe || '';
  document.getElementById('inp-risk-percent').value = trade.risk_percent != null ? trade.risk_percent : '';
  document.getElementById('notes-modal').style.display = 'flex';
}

export function closeNotesModal() {
  document.getElementById('notes-modal').style.display = 'none';
  state.notesModalTicket = null;
}

export async function saveNotes() {
  if (state.notesModalTicket == null) return;
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
  try {
    const r = await fetch(`${API}/trades/${state.notesModalTicket}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error();
    const updated = await r.json();
    const idx = state.allTrades.findIndex(t => t.ticket === state.notesModalTicket);
    if (idx !== -1) state.allTrades[idx] = updated;
    closeNotesModal();
    showToast('Note enregistrée');
    const sym = currentSymbol();
    renderTradeList(sym ? state.allTrades.filter(t => t.symbol === sym) : state.allTrades);
  } catch(e) { showToast('Erreur lors de l\'enregistrement de la note'); }
}
