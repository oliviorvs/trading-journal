// Journal des mouvements de capital (dépôts / retraits) — phase 6.
//
// Carte « Mouvements de capital » des Réglages (#movements-card, index.html).
// - Compte MT5 : lecture seule, les mouvements viennent de la synchro
//   (deals « balance » de MetaTrader 5). La carte n'apparaît que s'il y en a.
// - Compte manuel : saisie d'un dépôt / retrait, suppression.
// Un mouvement fait varier le capital du compte, jamais sa performance : il
// n'entre ni dans le P&L ni dans le drawdown (voir backend/services/movements.py).
import { API, apiGet, state } from './config.js';
import { moneyAbs, escapeHtml, showToast } from './utils.js';
import { loadAll } from './dashboard.js';
import { loadAccountInfo } from './account.js';

const SOURCE_LABELS = { mt5: 'MT5', manual: 'Saisi', import: 'Importé' };

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// Valeur par défaut du champ date : maintenant, à la minute, heure locale.
function nowLocalInput() {
  const d = new Date();
  d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
  return d.toISOString().slice(0, 16);
}

export async function loadMovements() {
  const card = document.getElementById('movements-card');
  if (!card) return;
  let data;
  try {
    data = await apiGet('/capital-movements');
  } catch (e) {
    card.style.display = 'none';
    return;
  }
  // Compte MT5 sans aucun mouvement, ou aucun compte : rien à montrer.
  if (!data.can_edit && !data.count) {
    card.style.display = 'none';
    return;
  }
  card.style.display = '';

  setText('mv-total-deposits', moneyAbs(data.total_deposits));
  setText('mv-total-withdrawals', moneyAbs(data.total_withdrawals));
  setText('mv-total-net', (data.net >= 0 ? '+' : '−') + moneyAbs(data.net));
  setText('movements-note', data.can_edit
    ? 'Dépôts et retraits du compte : ils font varier le capital, jamais la performance (ni P&L, ni drawdown).'
    : 'Dépôts et retraits lus depuis MetaTrader 5 (lecture seule) : ils font varier le solde, jamais la performance.');

  const form = document.getElementById('movements-form');
  if (form) {
    form.style.display = data.can_edit ? '' : 'none';
    const timeInput = document.getElementById('mv-time');
    if (data.can_edit && timeInput && !timeInput.value) timeInput.value = nowLocalInput();
  }

  const list = document.getElementById('movements-list');
  if (!list) return;
  if (!data.items.length) {
    list.innerHTML = '<div class="field-hint">Aucun dépôt ni retrait enregistré.</div>';
    return;
  }
  list.innerHTML = data.items.map(m => {
    const sign = m.type === 'deposit' ? '+' : '−';
    const del = m.editable
      ? `<button type="button" class="btn-reset" data-mv="${m.id}" style="padding:2px 8px">Supprimer</button>` : '';
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:5px 0;font-size:var(--fs-sm);border-top:1px solid var(--border2)">
      <span>${new Date(m.time).toLocaleString('fr-FR')} · ${m.type === 'deposit' ? 'Dépôt' : 'Retrait'}${m.comment ? ' · ' + escapeHtml(m.comment) : ''}
        <span class="field-hint" style="display:inline">(${SOURCE_LABELS[m.source] || escapeHtml(m.source)})</span></span>
      <span style="display:flex;align-items:center;gap:8px"><strong>${sign}${moneyAbs(m.amount)}</strong>${del}</span>
    </div>`;
  }).join('');
  list.querySelectorAll('button[data-mv]').forEach(btn => {
    btn.onclick = () => deleteCapitalMovement(Number(btn.dataset.mv));
  });
}

async function afterChange() {
  // Le capital, la courbe, le drawdown et le risque en % dépendent des mouvements.
  state.tradesCache.clear();
  await loadAccountInfo();
  await loadAll();   // recharge aussi la carte des mouvements
}

export async function addCapitalMovement() {
  const type = document.getElementById('mv-type').value;
  const amount = Number(document.getElementById('mv-amount').value);
  const time = document.getElementById('mv-time').value;
  const comment = document.getElementById('mv-comment').value.trim();
  if (!(amount > 0)) return showToast('Le montant doit être supérieur à 0');
  if (!time) return showToast('Date requise');

  const btn = document.getElementById('mv-add-btn');
  btn.disabled = true;
  try {
    const r = await fetch(`${API}/capital-movements`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ type, amount, time, comment: comment || null }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : "Ajout impossible");
    document.getElementById('mv-amount').value = '';
    document.getElementById('mv-comment').value = '';
    showToast(type === 'deposit' ? 'Dépôt enregistré' : 'Retrait enregistré');
    await afterChange();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

export async function deleteCapitalMovement(id) {
  if (!confirm('Supprimer ce mouvement de capital ? Le capital du compte sera recalculé.')) return;
  try {
    const r = await fetch(`${API}/capital-movements/${id}`, { method: 'DELETE' });
    if (!r.ok) {
      const data = await r.json().catch(() => ({}));
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Suppression impossible');
    }
    showToast('Mouvement supprimé');
    await afterChange();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  }
}
