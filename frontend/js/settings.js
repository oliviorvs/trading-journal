import { API, apiGet, state } from './config.js';
import { showToast, moneyAbs, escapeHtml } from './utils.js';
import { loadAll } from './dashboard.js';
import { loadAccountInfo } from './account.js';

export async function loadSettings() {
  try {
    const d = await apiGet('/settings');
    state.CURRENCY = d.currency || 'USD';
    state.setupTypes = d.setup_types || state.setupTypes;
    state.emotionTypes = d.emotion_types || state.emotionTypes;
    state.errorTypes = d.error_types || state.errorTypes;
    return d;
  } catch(e) {
    console.warn('settings', e);
    showToast(`Réglages indisponibles : ${e.message}`);
    return { currency: 'USD' };
  }
}

export function loadSettingsForm() {
  apiGet('/settings').then(d => {
    // Champ en lecture seule (voir index.html) : affiche la devise réelle
    // du compte MT5 actif, dérivée côté backend — non éditable ici.
    document.getElementById('set-currency').value = d.currency;
    state.setupTypes = d.setup_types || state.setupTypes;
    state.emotionTypes = d.emotion_types || state.emotionTypes;
    state.errorTypes = d.error_types || state.errorTypes;
  }).catch(e => {
    console.warn('settings form', e);
    showToast(`Réglages indisponibles : ${e.message}`);
  });
  loadReferenceCapital();
}

export function renderSetupOptions() {
  const select = document.getElementById('inp-setup-tag');
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">—</option>' + state.setupTypes.map(value => {
    const label = value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ');
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join('');
  select.value = current;
}

export function renderEmotionOptions() {
  const select = document.getElementById('inp-emotion');
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">—</option>' + state.emotionTypes.map(value => {
    const label = value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ');
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join('');
  select.value = current;
}

export function renderErrorOptions() {
  const select = document.getElementById('inp-error-tag');
  if (!select) return;
  const current = select.value;
  select.innerHTML = '<option value="">—</option>' + state.errorTypes.map(value => {
    const label = value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ');
    return `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`;
  }).join('');
  select.value = current;
}

// Compteurs d'usage (nombre de trades référençant chaque valeur), chargés à
// l'ouverture de la modale — voir openSettingsListsModal(). Permet de
// désactiver le bouton « Suppr. » AVANT le clic plutôt que de laisser
// l'utilisateur cliquer, attendre la requête, et découvrir le refus après
// coup (le backend refuse dans tous les cas — voir routers/settings.py — ce
// n'est ici qu'un raccourci visuel, jamais la seule protection).
let _tagUsage = { setup_types: {}, emotion_types: {}, error_types: {} };

function renderManagedList(elementId, values, usage) {
  const list = document.getElementById(elementId);
  if (!list) return;
  list.innerHTML = values.map(value => {
    const label = value.charAt(0).toUpperCase() + value.slice(1).replaceAll('_', ' ');
    const count = usage[value] || 0;
    const locked = count > 0;
    // Cadenas + décompte plutôt qu'un bouton qui échouera silencieusement :
    // l'utilisateur voit tout de suite POURQUOI il ne peut pas supprimer,
    // sans avoir à cliquer pour le découvrir.
    return `<div class="managed-item">
      <span>${escapeHtml(label)}</span>
      ${locked
        ? `<span class="managed-locked" title="Utilisé par ${count} trade(s) — retaguez-les avant de retirer cette valeur">🔒 ${count}</span>`
        : `<button type="button" class="managed-delete" data-value="${escapeHtml(value)}" title="Supprimer ${escapeHtml(label)}">Suppr.</button>`}
    </div>`;
  }).join('');
  list.querySelectorAll('.managed-delete').forEach(button => {
    button.addEventListener('click', () => deleteManagedValue(elementId, button.dataset.value));
  });
}

function renderAllManagedLists() {
  renderManagedList('setup-types-list', state.setupTypes, _tagUsage.setup_types);
  renderManagedList('emotion-types-list', state.emotionTypes, _tagUsage.emotion_types);
  renderManagedList('error-types-list', state.errorTypes, _tagUsage.error_types);
}

export async function openSettingsListsModal() {
  document.getElementById('settings-lists-modal').style.display = 'flex';
  // Rendu immédiat sans les cadenas (l'utilisateur ne doit pas attendre le
  // réseau pour voir la liste), puis on complète dès que l'usage est connu.
  renderAllManagedLists();
  try {
    _tagUsage = await apiGet('/settings/tag-usage');
  } catch (e) {
    console.warn('tag-usage', e);
    _tagUsage = { setup_types: {}, emotion_types: {}, error_types: {} };
  }
  renderAllManagedLists();
}

export function closeSettingsListsModal() {
  document.getElementById('settings-lists-modal').style.display = 'none';
}

async function persistManagedLists(setupTypes, emotionTypes, errorTypes) {
  const response = await fetch(`${API}/settings`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ setup_types: setupTypes, emotion_types: emotionTypes, error_types: errorTypes }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(data.detail || 'Enregistrement impossible');
  state.setupTypes = data.setup_types;
  state.emotionTypes = data.emotion_types;
  state.errorTypes = data.error_types;
  renderSetupOptions();
  renderEmotionOptions();
  renderErrorOptions();
}

export async function addManagedValue(kind) {
  const input = document.getElementById(`${kind}-type-input`);
  const value = input.value.trim().toLowerCase();
  if (!value) return;
  const values = kind === 'setup' ? state.setupTypes : kind === 'emotion' ? state.emotionTypes : state.errorTypes;
  if (values.includes(value)) { input.focus(); return; }
  try {
    await persistManagedLists(
      kind === 'setup' ? [...values, value] : state.setupTypes,
      kind === 'emotion' ? [...values, value] : state.emotionTypes,
      kind === 'error' ? [...values, value] : state.errorTypes,
    );
    input.value = '';
    renderAllManagedLists();
  } catch (error) { showToast(`Erreur : ${error.message}`); }
}

async function deleteManagedValue(elementId, value) {
  const isSetup = elementId === 'setup-types-list';
  const isEmotion = elementId === 'emotion-types-list';
  const values = isSetup ? state.setupTypes : isEmotion ? state.emotionTypes : state.errorTypes;
  if (values.length <= 1) { showToast('Gardez au moins une valeur'); return; }
  try {
    await persistManagedLists(
      isSetup ? values.filter(item => item !== value) : state.setupTypes,
      isEmotion ? values.filter(item => item !== value) : state.emotionTypes,
      !isSetup && !isEmotion ? values.filter(item => item !== value) : state.errorTypes,
    );
    renderAllManagedLists();
  } catch (error) {
    // Le bouton « Suppr. » n'est proposé que pour les valeurs qui semblaient
    // libres au moment du rendu (voir openSettingsListsModal) — mais un
    // trade a pu être retagué entre-temps par un autre onglet, ou l'usage
    // n'a tout simplement pas fini de charger. Le backend reste la seule
    // source de vérité : son refus (409) est donc affiché tel quel, jamais
    // masqué derrière un message générique.
    showToast(error.message || 'Suppression refusée');
    // On rafraîchit l'usage pour que le cadenas apparaisse immédiatement,
    // sans attendre une réouverture de la modale.
    try { _tagUsage = await apiGet('/settings/tag-usage'); } catch (_e) { /* tant pis, on garde l'ancien */ }
    renderAllManagedLists();
  }
}

// Correction #3 : le capital de référence est désormais figé
// (Account.initial_balance) plutôt que reconstruit dynamiquement — on
// l'affiche pour que l'utilisateur comprenne ce que "Recalibrer" va
// changer avant de cliquer.
async function loadReferenceCapital() {
  const el = document.getElementById('ref-capital-value');
  try {
    const r = await fetch(`${API}/account`);
    if (!r.ok) { el.textContent = '—'; return; }
    const d = await r.json();
    el.textContent = d.initial_balance != null ? moneyAbs(d.initial_balance) : 'Pas encore initialisé (synchronisez une fois)';
  } catch(e) { el.textContent = '—'; }
}

export async function recalibrateCapital() {
  try {
    const r = await fetch(`${API}/account`);
    if (!r.ok) { showToast('Aucun compte actif'); return; }
    const acc = await r.json();
    const resp = await fetch(`${API}/mt5/accounts/${acc.login}/recalibrate`, { method: 'POST' });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || 'Recalibrage échoué');
    showToast('Capital de référence recalibré');
    await loadReferenceCapital();
    await loadAll();
  } catch(e) {
    showToast('Erreur : ' + e.message);
  }
}

export async function saveSettings() {
  // La devise n'est plus envoyée : elle n'est plus éditable côté backend
  // (voir SettingsUpdate) et reste toujours dérivée du compte MT5 actif.
  try {
    const r = await fetch(`${API}/settings`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    if (!r.ok) {
      const body = await r.json().catch(() => ({}));
      throw new Error(body.detail || `HTTP ${r.status}`);
    }
    showToast('Réglages enregistrés');
    // Réglage lu à chaque appel côté backend (jamais mis en cache) : on
    // recharge tout ce qui dépend de la devise. Le capital de référence
    // n'est plus un réglage : il vient directement du solde MT5 réel.
    await loadSettings();
    await loadAll();
    await loadAccountInfo();
    renderSetupOptions();
    renderEmotionOptions();
  } catch(e) { showToast(`Erreur lors de l'enregistrement : ${e.message}`); }
}
