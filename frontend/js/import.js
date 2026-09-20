// Assistant d'import (étape 4) — voir Recap-structure-manuel-import.md.
//
// Trois écrans dans une seule modale (#import-modal, index.html) :
//   1. Choix du fichier → POST /api/import/preview
//   2. Correspondance de colonnes (CSV générique uniquement, quand le
//      backend renvoie needs_mapping = true)
//   3. Aperçu des trades détectés (erreurs/doublons/écarts d'intégrité en
//      évidence) + confirmation → POST /api/import/commit
//
// Le jeton d'aperçu (`_token`) vit côté backend (mémoire, TTL 30 min) : rien
// n'est écrit tant que l'utilisateur n'a pas confirmé.
import { API, apiGet, state, bumpAccountEpoch } from './config.js';
import { money, escapeHtml, showToast, confirmAction } from './utils.js';
import { loadAll } from './dashboard.js';
import { loadAccountInfo, refreshMT5State } from './account.js';

const CSV_FIELD_LABELS = {
  ticket: 'Ticket (optionnel)',
  symbol: 'Symbole *',
  direction: 'Sens (achat/vente) *',
  volume: 'Volume *',
  open_price: "Prix d'ouverture *",
  open_time: "Heure d'ouverture *",
  close_price: 'Prix de clôture',
  close_time: 'Heure de clôture',
  sl: 'Stop Loss',
  tp: 'Take Profit',
  profit: 'Profit',
  commission: 'Commission',
  swap: 'Swap',
  comment: 'Commentaire',
};

let _lastPreview = null;   // dernière réponse de /api/import/preview
let _csvHeaders = null;    // en-têtes détectés (écran de mapping)

// Destination de l'import, fixée à l'ouverture de l'assistant :
//   'active' — le compte manuel actif reçoit les trades (bouton « Importer » de
//              la page Trades) ; anti-doublons calculé sur ce compte ;
//   'new'    — un NOUVEAU compte manuel est créé par l'import (écran d'accueil
//              « Compte manuel (import) », ou aucun compte manuel actif). Le
//              compte MT5 actif n'est jamais une destination : manuel/import et
//              MT5 restent séparés.
let _target = 'active';

export function openImportModal(options) {
  _lastPreview = null;
  _csvHeaders = null;
  const wantsNew = !!(options && options.newAccount);
  _target = (!wantsNew && state.accountMode === 'manual') ? 'active' : 'new';
  document.getElementById('import-file-input').value = '';
  document.getElementById('import-target-hint').textContent = _target === 'active'
    ? "Les trades seront ajoutés au compte manuel actif."
    : "Un nouveau compte manuel sera créé avec cet import (nom, devise et capital de départ à confirmer à l'étape suivante).";
  hideImportError();
  showImportStep('file');
  document.getElementById('import-modal').style.display = 'flex';
  loadImportBatches();
}

export function closeImportModal() {
  document.getElementById('import-modal').style.display = 'none';
}

export function backToImportFile() {
  showImportStep('file');
}

function showImportStep(step) {
  document.getElementById('import-step-file').style.display = step === 'file' ? '' : 'none';
  document.getElementById('import-step-mapping').style.display = step === 'mapping' ? '' : 'none';
  document.getElementById('import-step-preview').style.display = step === 'preview' ? '' : 'none';
}

function showImportError(msg) {
  const el = document.getElementById('import-error');
  el.textContent = msg;
  el.style.display = '';
}

function hideImportError() {
  document.getElementById('import-error').style.display = 'none';
}

function pickedFile() {
  return document.getElementById('import-file-input').files[0];
}

// ── Étape 1 → aperçu ─────────────────────────────────────────────────────

export async function analyzeImportFile(mapping) {
  const file = pickedFile();
  if (!file) return showImportError('Choisissez un fichier');

  const btn = document.getElementById('import-analyze-btn');
  hideImportError();
  if (btn) { btn.disabled = true; btn.textContent = 'Analyse…'; }
  try {
    const form = new FormData();
    form.append('file', file);
    if (mapping) form.append('mapping', JSON.stringify(mapping));
    form.append('target', _target);
    const r = await fetch(`${API}/import/preview`, { method: 'POST', body: form });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : 'Fichier illisible');

    _lastPreview = data;
    if (data.needs_mapping) {
      _csvHeaders = data.csv_headers || [];
      renderMappingStep(data);
      showImportStep('mapping');
    } else {
      renderPreviewStep(data);
      showImportStep('preview');
    }
  } catch (e) {
    showImportError(e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Analyser'; }
  }
}

// ── Étape 2 : correspondance de colonnes (CSV) ──────────────────────────

function renderMappingStep(preview) {
  const container = document.getElementById('import-mapping-fields');
  const fields = preview.csv_target_fields || Object.keys(CSV_FIELD_LABELS);
  const options = ['<option value="">— Ignorer —</option>']
    .concat(_csvHeaders.map(h => `<option value="${escapeHtml(h)}">${escapeHtml(h)}</option>`))
    .join('');
  container.innerHTML = fields.map(f => `
    <div class="field">
      <label>${escapeHtml(CSV_FIELD_LABELS[f] || f)}</label>
      <select data-field="${f}">${options}</select>
    </div>
  `).join('');

  // Pré-sélection naïve : un en-tête dont le nom contient déjà le nom du champ.
  fields.forEach(f => {
    const select = container.querySelector(`select[data-field="${f}"]`);
    const guess = _csvHeaders.find(h => h.toLowerCase().replace(/[^a-z]/g, '').includes(f.replace(/_/g, '')));
    if (guess) select.value = guess;
  });
}

export async function applyImportMapping() {
  const container = document.getElementById('import-mapping-fields');
  const mapping = {};
  container.querySelectorAll('select[data-field]').forEach(sel => {
    if (sel.value) mapping[sel.dataset.field] = sel.value;
  });
  const required = ['symbol', 'direction', 'volume', 'open_price', 'open_time'];
  const missing = required.filter(f => !mapping[f]);
  if (missing.length) return showToast('Champs obligatoires non associés : ' + missing.join(', '));
  await analyzeImportFile(mapping);
}

// ── Étape 3 : aperçu + confirmation ──────────────────────────────────────

function renderPreviewStep(preview) {
  const summary = document.getElementById('import-summary');
  summary.textContent = `${preview.valid_count} trade(s) prêt(s) à importer` +
    (preview.duplicate_count ? ` · ${preview.duplicate_count} déjà importé(s) (ignoré(s))` : '') +
    (preview.error_count ? ` · ${preview.error_count} en erreur (ignoré(s))` : '') +
    (preview.movement_count ? ` · ${preview.movement_count} dépôt(s)/retrait(s) à importer` : '') +
    (preview.movement_duplicate_count ? ` · ${preview.movement_duplicate_count} dépôt(s)/retrait(s) déjà importé(s)` : '');

  const integrityEl = document.getElementById('import-integrity');
  const mismatches = Object.entries(preview.integrity || {}).filter(([, c]) => !c.matches);
  if (mismatches.length) {
    const labels = { net_profit: 'Profit net total', trades_count: 'Nombre de trades', final_balance: 'Solde final' };
    integrityEl.innerHTML = `<div class="field-hint" style="color:var(--danger,#c0392b)">
      Écart détecté avec le rapport : ${mismatches.map(([k, c]) =>
        `${escapeHtml(labels[k] || k)} calculé ${c.computed} ≠ rapport ${c.expected}`).join(' · ')}.
      <label style="display:block;margin-top:4px"><input type="checkbox" id="import-force-check"> Importer quand même</label>
    </div>`;
  } else {
    integrityEl.innerHTML = '';
  }

  renderAccountSection(preview.account_header || {});

  const rows = preview.candidates || [];
  const body = document.getElementById('import-preview-body');
  const shown = rows.slice(0, 300);
  body.innerHTML = shown.map(c => {
    const status = c.error ? `<span title="${escapeHtml(c.error)}">Erreur</span>`
      : c.duplicate ? 'Déjà importé' : 'OK';
    return `<tr${c.error ? ' style="opacity:.6"' : ''}>
      <td>${escapeHtml(c.row_ref)}</td>
      <td>${escapeHtml(c.symbol || '—')}</td>
      <td>${escapeHtml(c.direction || '—')}</td>
      <td>${c.volume ?? '—'}</td>
      <td>${c.open_time ? new Date(c.open_time).toLocaleString('fr-FR') : '—'}</td>
      <td>${c.close_time ? new Date(c.close_time).toLocaleString('fr-FR') : '—'}</td>
      <td>${c.profit != null ? money(c.profit) : '—'}</td>
      <td>${status}</td>
    </tr>`;
  }).join('') + (rows.length > shown.length
    ? `<tr><td colspan="8" class="field-hint">… et ${rows.length - shown.length} autre(s)</td></tr>` : '');

  renderImportMovements(preview);

  // Un rapport ne contenant que des dépôts / retraits reste importable.
  const commitBtn = document.getElementById('import-commit-btn');
  commitBtn.disabled = preview.valid_count === 0 && !preview.movement_count;
}

// Dépôts / retraits détectés dans le rapport (phase 6). Ils font varier le
// capital du compte, jamais sa performance.
function renderImportMovements(preview) {
  const section = document.getElementById('import-movements-section');
  const list = document.getElementById('import-movements-list');
  if (!section || !list) return;
  const movements = preview.movements || [];
  if (!movements.length) {
    section.style.display = 'none';
    list.innerHTML = '';
    return;
  }
  list.innerHTML = movements.map(m => `
    <div style="display:flex;justify-content:space-between;gap:10px;padding:3px 0;font-size:var(--fs-sm)${m.duplicate ? ';opacity:.6' : ''}">
      <span>${m.time ? new Date(m.time).toLocaleString('fr-FR') : '—'} · ${m.type === 'deposit' ? 'Dépôt' : 'Retrait'}${m.comment ? ' · ' + escapeHtml(m.comment) : ''}</span>
      <span>${money(m.type === 'deposit' ? m.amount : -m.amount)}${m.duplicate ? ' · déjà importé' : ''}</span>
    </div>`).join('');
  section.style.display = '';
}

function renderAccountSection(header) {
  const el = document.getElementById('import-account-section');
  if (_target === 'active') {
    el.innerHTML = `<div class="field-hint">Import dans le compte manuel actif.` +
      (header.initial_funding != null
        ? ` Le dépôt initial du rapport (${money(header.initial_funding)}) n'est pas importé : le capital de départ du compte est déjà défini.`
        : '') + `</div>`;
    return;
  }
  // Nouveau compte manuel : pré-rempli depuis l'en-tête du rapport quand il
  // est lisible (numéro de compte, devise — voir import_parsers
  // ._parse_account_header) ; le capital de départ est PROPOSÉ d'après le
  // solde avant le premier trade, à confirmer.
  const name = header.login ? `Compte ${header.login}` : (header.name || 'Compte importé');
  const currency = (header.currency || 'USD').toUpperCase();
  const suggested = header.suggested_initial_balance;
  const hasSuggestion = suggested != null && Number(suggested) > 0;
  el.innerHTML = `
    <div class="field-hint">Nouveau compte manuel créé avec cet import :</div>
    <div class="field"><label>Nom du compte</label><input id="import-acc-name" type="text" maxlength="50" value="${escapeHtml(String(name))}"></div>
    <div class="modal-fields-grid">
      <div class="field"><label>Devise</label><input id="import-acc-currency" type="text" maxlength="10" value="${escapeHtml(currency)}"></div>
      <div class="field"><label>Capital de départ</label><input id="import-acc-balance" type="number" step="any" min="0" placeholder="1000" value="${hasSuggestion ? escapeHtml(String(suggested)) : ''}"></div>
    </div>
    <div class="field-hint">${hasSuggestion
      ? (header.initial_funding != null
          ? 'Capital de départ proposé : dépôt initial du compte lu dans le rapport (les dépôts et retraits suivants sont importés à part) — à confirmer.'
          : 'Capital de départ proposé d\'après le rapport (solde avant le premier trade) — à confirmer.')
      : 'Capital du compte avant le premier trade importé.'}</div>
  `;
}

export async function confirmImportCommit() {
  if (!_lastPreview) return;
  const btn = document.getElementById('import-commit-btn');
  const force = document.getElementById('import-force-check')?.checked || false;

  const body = { token: _lastPreview.token, force };
  if (_target === 'new') {
    const name = document.getElementById('import-acc-name')?.value.trim();
    const currency = document.getElementById('import-acc-currency')?.value.trim();
    const balance = document.getElementById('import-acc-balance')?.value;
    if (!name) return showToast('Nom du compte requis');
    if (!(Number(balance) > 0)) return showToast('Le capital de départ doit être supérieur à 0');
    body.new_account = { name, currency: currency || 'USD', initial_balance: Number(balance) };
    // Le journal démarre à la date du premier trade importé (heure serveur du
    // rapport, stockée telle quelle : aucune conversion de fuseau).
    const firstOpen = (_lastPreview.candidates || [])
      .filter(c => !c.error && !c.duplicate && c.open_time)
      .map(c => c.open_time)
      .sort()[0];
    if (firstOpen) body.new_account.start_date = firstOpen;
  }

  btn.disabled = true;
  try {
    const r = await fetch(`${API}/import/commit`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : "Échec de l'import");

    closeImportModal();
    showToast(`${data.inserted} trade(s) importé(s)` +
      (data.movements_inserted ? ` · ${data.movements_inserted} dépôt(s)/retrait(s)` : '') +
      (data.skipped_duplicate || data.movements_skipped_duplicate
        ? ` · ${(data.skipped_duplicate || 0) + (data.movements_skipped_duplicate || 0)} doublon(s) ignoré(s)` : ''));
    // Type du compte actif d'abord (l'import a pu créer un compte manuel et
    // l'activer), puis les données, puis le sélecteur de comptes.
    if (body.new_account) bumpAccountEpoch();
    await loadAccountInfo();
    await loadAll();
    await refreshMT5State();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  } finally {
    btn.disabled = false;
  }
}

// ── Imports précédents : annulation d'un lot entier ─────────────────────────
// Chaque import forme un lot (`import_batch`) ; l'annuler supprime tous ses
// trades d'un coup (GET /api/import/batches, DELETE /api/import/batch/{id}).
// Section visible uniquement quand le compte manuel actif en a au moins un.
async function loadImportBatches() {
  const section = document.getElementById('import-batches-section');
  const list = document.getElementById('import-batches-list');
  section.style.display = 'none';
  // Seulement quand l'import va dans le compte manuel actif : créer un nouveau
  // compte n'a rien à voir avec les imports d'un autre.
  if (_target !== 'active' || state.accountMode !== 'manual') return;
  try {
    const batches = await apiGet('/import/batches');
    if (!batches.length) return;
    const fmt = iso => iso ? new Date(iso).toLocaleDateString('fr-FR') : '—';
    list.innerHTML = batches.map(b => `
      <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;padding:6px 0;font-size:var(--fs-sm)">
        <span>${b.trades_count} trade(s)${b.movements_count ? ` · ${b.movements_count} dépôt(s)/retrait(s)` : ''} · ${fmt(b.date_from)} → ${fmt(b.date_to)}</span>
        <button type="button" class="btn-reset" data-batch="${escapeHtml(b.batch_id)}" style="padding:4px 10px">Annuler cet import</button>
      </div>`).join('');
    list.querySelectorAll('button[data-batch]').forEach(btn => {
      btn.onclick = () => cancelImportBatch(btn.dataset.batch);
    });
    section.style.display = '';
  } catch (e) {
    // Fonction annexe : jamais bloquante pour l'assistant lui-même.
    console.warn('import batches', e);
  }
}

async function cancelImportBatch(batchId) {
  if (!await confirmAction("Annuler cet import ? Tous les trades et les dépôts / retraits qu'il a créés seront supprimés définitivement (y compris les notes et pièces jointes des trades).", { confirmLabel: 'Annuler l’import' })) return;
  try {
    const r = await fetch(`${API}/import/batch/${encodeURIComponent(batchId)}`, { method: 'DELETE' });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : "Annulation impossible");
    showToast(`${data.deleted} trade(s) supprimé(s)` + (data.movements_deleted ? ` · ${data.movements_deleted} dépôt(s)/retrait(s)` : ''));
    state.tradesCache.clear();
    await loadAccountInfo();
    await loadAll();
    await loadImportBatches();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  }
}
