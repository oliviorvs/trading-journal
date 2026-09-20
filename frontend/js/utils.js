import { state } from './config.js';

// Formatage monétaire basé sur Intl.NumberFormat (API native du navigateur)
// plutôt que sur une table de correspondance codée en dur (USD/EUR/GBP
// uniquement, auparavant). N'importe quel code devise ISO 4217 valide
// fonctionne désormais directement : symbole correct, nombre de décimales
// approprié par défaut (2 pour USD/EUR, 0 pour JPY...), et conventions
// d'affichage (espace, position du symbole) selon la locale du navigateur.
function currencyFormatter(options = {}) {
  return new Intl.NumberFormat(undefined, {
    style: 'currency',
    currency: state.CURRENCY,
    currencyDisplay: 'symbol',
    ...options,
  });
}

// Symbole (ou code) de la devise seul, pour les cas où seul un préfixe est
// nécessaire (axes/labels de graphiques Chart.js concaténés à une valeur
// brute, ex. `curSym() + v`).
export function curSym() {
  try {
    const parts = currencyFormatter({ minimumFractionDigits: 0, maximumFractionDigits: 0 }).formatToParts(0);
    const part = parts.find(p => p.type === 'currency');
    return part ? part.value : state.CURRENCY + ' ';
  } catch (e) {
    // Code devise invalide/non reconnu par le navigateur : repli sur le
    // code brut plutôt que de faire planter tout l'affichage.
    return state.CURRENCY + ' ';
  }
}

// Formatte un montant avec signe explicite (+/-), utilisé pour tout P&L.
// `decimals` reste optionnel : s'il n'est pas fourni, Intl.NumberFormat
// applique le nombre de décimales standard de la devise (2 pour USD/EUR/GBP,
// 0 pour JPY, etc.) plutôt qu'une valeur fixe qui ne conviendrait pas à
// toutes les devises.
export function money(n, decimals) {
  if (n == null || isNaN(n)) return '—';
  const digits = decimals != null ? { minimumFractionDigits: decimals, maximumFractionDigits: decimals } : {};
  try {
    return currencyFormatter({ signDisplay: 'always', ...digits }).format(n);
  } catch (e) {
    const sign = n >= 0 ? '+' : '-';
    return `${sign}${curSym()}${Math.abs(n).toLocaleString(undefined, digits)}`;
  }
}

// Formatte un montant en valeur absolue (sans signe), utilisé pour
// soldes/capitaux mais aussi pour des valeurs qui peuvent être négatives en
// interne (ex. max_drawdown_abs, toujours ≤ 0 côté backend).
// Correction : Math.abs() manquait — un montant négatif (ex. drawdown)
// s'affichait mal formé, du type "$-450.00" au lieu de "$450.00".
export function moneyAbs(n, decimals) {
  if (n == null || isNaN(n)) return '—';
  const digits = decimals != null ? { minimumFractionDigits: decimals, maximumFractionDigits: decimals } : {};
  try {
    return currencyFormatter(digits).format(Math.abs(n));
  } catch (e) {
    return `${curSym()}${Math.abs(n).toLocaleString(undefined, digits)}`;
  }
}

// Échappe les caractères HTML spéciaux d'une chaîne avant interpolation dans
// un template innerHTML. À utiliser systématiquement pour tout texte libre
// (notes, commentaire MT5, nom de fichier original, playbook, etc.) qui
// n'est pas garanti de venir d'une liste de valeurs fixes côté app.
export function escapeHtml(str) {
  if (str == null) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// ── Origine d'un trade (étape 5) ─────────────────────────────────────────────
// `source` : 'mt5' (synchro) | 'manual' (saisie) | 'import' (fichier). Libellés
// courts pour la colonne Statut du tableau, libellés longs en infobulle.
const SOURCE_BADGES = {
  mt5:    { short: 'MT5',    long: 'Synchronisé depuis MetaTrader 5' },
  manual: { short: 'Manuel', long: 'Saisi à la main' },
  import: { short: 'Import', long: 'Importé depuis un fichier' },
};

export function sourceBadge(source) {
  const meta = SOURCE_BADGES[source];
  if (!meta) return '';
  return `<span class="badge badge-source badge-source-${source}" title="${escapeHtml(meta.long)}">${meta.short}</span>`;
}

export function showToast(msg) {
  // Lecture abandonnée parce que le compte a changé entre-temps : pas une erreur.
  if (typeof msg === 'string' && msg.includes('__stale_account__')) return;
  const t = document.getElementById('toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(t._timer);
  t._timer = setTimeout(() => t.classList.remove('show'), 2200);
}

let pendingConfirmation = null;

export function confirmAction(message, options = {}) {
  const modal = document.getElementById('confirm-modal');
  if (!modal) return Promise.resolve(false);

  if (pendingConfirmation) pendingConfirmation(false);
  const title = document.getElementById('confirm-modal-title');
  const text = document.getElementById('confirm-modal-message');
  const submit = document.getElementById('confirm-modal-submit');
  const cancel = document.getElementById('confirm-modal-cancel');
  const close = document.getElementById('confirm-modal-close');
  title.textContent = options.title || 'Confirmation';
  text.textContent = message;
  submit.textContent = options.confirmLabel || 'Confirmer';

  return new Promise(resolve => {
    const finish = result => {
      if (pendingConfirmation !== finish) return;
      pendingConfirmation = null;
      modal.style.display = 'none';
      resolve(result);
    };
    pendingConfirmation = finish;
    submit.onclick = () => finish(true);
    cancel.onclick = () => finish(false);
    close.onclick = () => finish(false);
    modal.style.display = 'flex';
  });
}

export function fmtDuration(minutes) {
  if (minutes == null) return '—';
  if (minutes < 60) return `${Math.round(minutes)} min`;
  const hours = minutes / 60;
  if (hours < 24) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} j`;
}

export function currentSymbol() {
  return document.getElementById('sym-select').value || '';
}

// ── États de chargement (correctif audit UI/UX #4) ──────────────────────────
// Affiche un léger voile + spinner sur un conteneur pendant un fetch, pour
// qu'un changement de filtre/période ne donne plus l'impression de n'avoir
// "rien fait" pendant que la requête réseau est en cours (voir usages dans
// dashboard.js, calendar.js, symbols.js, period.js).
export function setLoading(el, loading) {
  if (!el) return;
  if (loading) {
    el.classList.add('is-loading');
    if (!el.querySelector(':scope > .loading-overlay')) {
      const overlay = document.createElement('div');
      overlay.className = 'loading-overlay';
      overlay.innerHTML = '<div class="loading-spinner"></div>';
      el.appendChild(overlay);
    }
  } else {
    el.classList.remove('is-loading');
    const overlay = el.querySelector(':scope > .loading-overlay');
    if (overlay) overlay.remove();
  }
}
