// Origine de l'API — déduite de la page plutôt que codée en dur.
//
// Avant : `http://127.0.0.1:8000` en dur. Dès que le backend écoutait sur un
// autre port (8000 déjà occupé par un autre programme — voir _pick_port dans
// launcher.py), l'interface continuait d'interroger 8000 et n'affichait plus
// rien, alors que le serveur tournait très bien à côté.
//
// Dans l'app desktop, frontend et API sont servis par le même process, donc
// l'origine de la page EST celle de l'API. Seul le mode legacy START.bat fait
// exception (frontend sur :5500, API sur :8000) : on le traite explicitement.
function resolveBase() {
  const { protocol, hostname, port, origin } = window.location;
  if (protocol !== 'http:' && protocol !== 'https:') {
    // Page ouverte en file:// — aucun serveur d'origine à déduire.
    return 'http://127.0.0.1:8000';
  }
  if (port === '5500') return `${protocol}//${hostname}:8000`;
  return origin;
}

export const BASE = resolveBase();
export const API = `${BASE}/api`;

let _authToken = null;

export function setAuthToken(token) {
  _authToken = token || null;
}

export function getAuthToken() {
  return _authToken;
}

const _nativeFetch = window.fetch.bind(window);
window.fetch = (input, init) => {
  const url = typeof input === 'string' ? input : (input && input.url);
  if (_authToken && url && url.startsWith(BASE)) {
    const headers = new Headers((init && init.headers) || (typeof input !== 'string' && input ? input.headers : undefined));
    headers.set('Authorization', `Bearer ${_authToken}`);
    init = { ...(init || {}), headers };
  }
  return _nativeFetch(input, init);
};

// ── Helper de lecture API ────────────────────────────────────────────────────

export const STALE_MSG = '__stale_account__';

export function bumpAccountEpoch() {
  state.accountEpoch += 1;
}

export async function apiGet(path) {
  const epoch = state.accountEpoch;
  const r = await fetch(`${API}${path}`);
  if (epoch !== state.accountEpoch) throw new Error(STALE_MSG);
  if (!r.ok) {
    const body = await r.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${r.status}`);
  }
  const data = await r.json();
  if (epoch !== state.accountEpoch) throw new Error(STALE_MSG);
  return data;
}

export async function loadAttachmentBlobUrl(filename) {
  const response = await fetch(`${API}/attachments/file/${encodeURIComponent(filename)}`);
  if (!response.ok) throw new Error(`Attachment HTTP ${response.status}`);
  return URL.createObjectURL(await response.blob());
}

if (window.__tjPendingAuthToken) {
  _authToken = window.__tjPendingAuthToken;
  delete window.__tjPendingAuthToken;
}
window.tjSetAuthToken = setAuthToken;
window.tjGetAuthToken = getAuthToken;

const now = new Date();

export const state = {
  allTrades: [],
  tradesTotal: 0,
  tradesPage: 0,
  tradesPageSize: 20,
  tradesCache: new Map(),
  setupTypes: ['breakout', 'pullback', 'news', 'range', 'autre'],
  emotionTypes: ['calme', 'stresse', 'confiant'],
  errorTypes: ['aucune', 'entree_trop_tot', 'sur_sizing', 'pas_de_stop', 'fomo', 'autre'],
  equityChart: null,
  ddChart: null,
  periodResultsChart: null,
  periodChart: null,
  symChart: null,
  rHistChart: null,
  riskDistChart: null,
  rollingChart: null,
  calYear: now.getFullYear(),
  calMonth: now.getMonth(), // 0-indexé
  CURRENCY: 'USD',
  currentPeriod: 'week',
  dashboardPeriod: 'all', // '1m' | '3m' | '6m' | 'all'
  notesModalTicket: null,
  viewModalTicket: null,
  editModalTicket: null,
  // 'edit' (modification d'un trade existant) | 'create' (ajout manuel).
  editModalMode: 'edit',
  // Type du compte actif : 'mt5' | 'manual' | null si aucun compte actif
  // (renseigné par loadAccountInfo → applyAccountModeUI, account.js).
  accountMode: 'mt5',
  // Comptes enregistrés (dernière liste reçue du sélecteur) : sert à adapter
  // les textes selon le type d'un compte (ex. suppression d'un compte manuel).
  accounts: [],
  // Voir bumpAccountEpoch().
  accountEpoch: 0,
  realEquityChart: null,
  depositLoadChart: null,
};
