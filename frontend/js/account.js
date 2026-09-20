import { API, state, bumpAccountEpoch } from './config.js';
import { moneyAbs, showToast } from './utils.js';
import { loadAll } from './dashboard.js';
import { goTo } from './navigation.js';

const CONNECT_COLLAPSE_KEY = 'tj-connect-collapsed';
const STATUS_POLL_MS = 15000; // fréquence de vérification "réelle" de la connexion MT5

// ── Reconnexion manuelle explicite ───────────────────────────────────────────
// Le backend ne retente plus JAMAIS de connexion MT5 tout seul (voir
// state.reconnect_active_account, côté backend) : initialize() relance le
// terminal s'il n'est pas ouvert, et une boucle de fond le rouvrait donc
// silencieusement même après une fermeture volontaire. Reconnecter est
// désormais toujours un geste — ce bouton, ou le clic sur le compte actif
// dans le sélecteur quand il apparaît déconnecté (voir renderAccountSwitcher).
let _reconnecting = false;

export async function reconnectMT5() {
  if (_reconnecting) return;
  _reconnecting = true;
  const btn = document.getElementById('btn-relaunch-mt5');
  if (btn) { btn.disabled = true; btn.textContent = '⟳ Connexion…'; }
  try {
    const r = await fetch(`${API}/mt5/reconnect`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Reconnexion échouée');
    showToast('MT5 reconnecté');
    await loadAll();
    await loadAccountInfo();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  } finally {
    _reconnecting = false;
    if (btn) { btn.disabled = false; btn.textContent = '⟳ Relancer MT5'; }
    await refreshMT5State();
  }
}

// ── Repli / dépli de la carte "Connexion MT5" ────────────────────────────────
// Une fois connecté, les champs login/mot de passe/serveur ne servent plus
// au quotidien : l'utilisateur peut replier la carte pour alléger la
// sidebar. La préférence est mémorisée (comme le thème) pour rester repliée
// d'un lancement à l'autre.
function setConnectCardCollapsed(collapsed) {
  document.getElementById('connect-card-body').style.display = collapsed ? 'none' : '';
  const btn = document.getElementById('connect-toggle-btn');
  btn.textContent = collapsed ? '▸' : '▾';
  btn.title = collapsed ? 'Afficher la connexion MT5' : 'Masquer la connexion MT5';
  localStorage.setItem(CONNECT_COLLAPSE_KEY, collapsed ? '1' : '0');
}

let _pollTimer = null;
let _wasConnected = false;

// Démarre/arrête le polling selon la visibilité de l'onglet/fenêtre.
// Correction : le setInterval tournait auparavant en continu même fenêtre
// minimisée ou onglet en arrière-plan — requêtes réseau inutiles en
// permanence (et, pour l'app packagée, activité non nécessaire pendant
// que l'utilisateur ne regarde pas l'écran).
//
// Étape 5 : le polling ne concerne QUE les comptes MT5. Avec un compte manuel
// actif, il n'y a aucune connexion à surveiller : refreshMT5State() l'arrête
// (et le relance dès qu'un compte MT5 redevient actif).
function _startPolling() {
  if (_pollTimer != null) return;
  _pollTimer = setInterval(refreshMT5State, STATUS_POLL_MS);
}

function _stopPolling() {
  if (_pollTimer == null) return;
  clearInterval(_pollTimer);
  _pollTimer = null;
}

export function initConnectCard() {
  setConnectCardCollapsed(localStorage.getItem(CONNECT_COLLAPSE_KEY) === '1');
  // État réel de la connexion vérifié tout de suite, puis à intervalle
  // régulier tant que la fenêtre est au premier plan : si le serveur du
  // broker cesse de répondre en cours de session (coupure réseau,
  // plateforme fermée...), la pastille "Connecté" ne doit pas rester
  // figée indéfiniment sans qu'aucune action ne l'ait provoqué.
  // (refreshMT5State démarre lui-même le polling si le compte actif est MT5.)
  refreshMT5State();
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) {
      _stopPolling();
    } else {
      // Rattrape immédiatement un éventuel changement manqué pendant
      // l'absence, et relance le polling si le compte actif est MT5.
      refreshMT5State();
    }
  });
}

export function toggleConnectCard() {
  const isCollapsed = document.getElementById('connect-card-body').style.display === 'none';
  setConnectCardCollapsed(!isCollapsed);
}


export async function connectMT5() {
  const login = document.getElementById('inp-login').value;
  const pass = document.getElementById('inp-pass').value;
  const server = document.getElementById('inp-server').value;
  const label = document.getElementById('inp-label').value.trim();
  if (!login || !pass || !server) return showToast('Remplissez tous les champs');

  const btn = document.querySelector('.btn-connect');
  btn.disabled = true;
  btn.textContent = 'Connexion...';

  try {
    // Identifiants envoyés dans le corps JSON (jamais en query string) pour
    // ne pas exposer le mot de passe dans les logs serveur ni l'historique
    // du navigateur.
    const r = await fetch(`${API}/mt5/connect`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ login: Number(login), password: pass, server, label: label || null }),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Connexion échouée');

    btn.textContent = 'Connecté ✓';
    document.getElementById('inp-pass').value = '';
    document.getElementById('inp-label').value = '';

    if (data.needs_initialization) {
      // Compte jamais initialisé : on ne charge pas encore le dashboard
      // (il n'y a aucun trade synchronisé tant que le choix n'est pas
      // fait) — on ouvre directement l'écran de choix.
      openInitJournalModal(data.account);
    } else {
      await loadAll();
      await loadAccountInfo();
      setConnectCardCollapsed(true);
    }
    await refreshMT5State();
  } catch(e) {
    showToast('Erreur : ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Connecter';
  }
}

// ── Initialisation du journal (table rase / "commencer maintenant") ─────────
// Un compte fraîchement connecté (ou reconnecté sans avoir jamais choisi de
// mode) doit passer par cet écran avant la première synchronisation — voir
// le garde-fou côté backend sur POST /api/mt5/sync.
let _pendingInitLogin = null;

function openInitJournalModal(accountInfo) {
  _pendingInitLogin = accountInfo.login;
  document.getElementById('init-journal-summary').textContent =
    `Compte #${accountInfo.login} — solde actuel : ${moneyAbs(accountInfo.balance)}`;
  document.getElementById('init-journal-modal').style.display = 'flex';
}

export async function initializeJournalFromNow() {
  await _submitInitialization({ mode: 'from_now' }, '.btn-primary');
}

export async function initializeJournalHistorical() {
  const dateInput = document.getElementById('init-history-date');
  if (!dateInput.value) return showToast('Choisissez une date de départ');
  // Correction : <input type="date"> renvoie "AAAA-MM-JJ", envoyé tel quel
  // ("AAAA-MM-JJT00:00:00", sans fuseau). Le backend le recevait donc comme
  // une date NAÏVE et la traitait directement comme minuit UTC (voir
  // MT5Service.initialize_journal_historical) — pour quiconque n'est pas à
  // UTC, ce n'est PAS minuit dans son fuseau local, et la fenêtre d'import
  // ne correspond alors plus vraiment au jour choisi à l'écran (elle peut
  // démarrer plusieurs heures avant ou après minuit local, incluant ou
  // excluant des trades de la journée sélectionnée). En construisant un
  // `Date` local puis `toISOString()`, le navigateur calcule le VRAI instant
  // UTC correspondant à minuit dans le fuseau de l'utilisateur ; le backend
  // reçoit alors une date "aware" et applique sa conversion UTC existante
  // sur la bonne valeur.
  const localMidnightUtcIso = new Date(`${dateInput.value}T00:00:00`).toISOString();
  await _submitInitialization(
    { mode: 'historical', start_date: localMidnightUtcIso },
    '#init-journal-modal button:not(.btn-primary)',
  );
}

async function _submitInitialization(body, btnSelector) {
  const login = _pendingInitLogin;
  if (login == null) return;
  const btn = document.querySelector(btnSelector);
  if (btn) btn.disabled = true;
  try {
    const r = await fetch(`${API}/mt5/accounts/${login}/initialize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Initialisation échouée');

    document.getElementById('init-journal-modal').style.display = 'none';
    _pendingInitLogin = null;
    showToast(body.mode === 'historical' ? 'Import de l\'historique en cours...' : 'Journal initialisé — suivi à partir de maintenant');

    await syncTrades();
    await loadAccountInfo();
    await refreshMT5State();
    setConnectCardCollapsed(true);
  } catch(e) {
    showToast('Erreur : ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
}

export async function syncTrades() {
  // Compte manuel : aucune connexion MT5, donc rien à synchroniser (le
  // backend refuserait d'ailleurs la requête). Sortie silencieuse : c'est
  // aussi le chemin de la resynchro automatique au démarrage.
  if (state.accountMode === 'manual') return;
  // Le bouton ne donnait aucun retour : sur un compte chargé, la
  // synchronisation prend plusieurs secondes pendant lesquelles rien ne
  // bougeait à l'écran, et rien n'empêchait de la relancer en boucle.
  const buttons = document.querySelectorAll('.btn-sync[data-sync]');
  buttons.forEach(b => { b.disabled = true; b.dataset.label = b.textContent; b.textContent = 'Synchronisation…'; });
  try {
    const r = await fetch(`${API}/mt5/sync`, { method: 'POST' });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'Synchronisation échouée');
    await loadAll();
    await loadAccountInfo();
    await refreshMT5State();
    showToast(d.synced ? `${d.synced} trade(s) importé(s)` : 'Journal déjà à jour');
  } finally {
    buttons.forEach(b => { b.disabled = false; if (b.dataset.label) b.textContent = b.dataset.label; });
  }
}

// syncTrades() lève volontairement en cas d'échec plutôt que d'avaler
// l'erreur elle-même : deux appelants (initializeJournal…, la resynchro
// silencieuse au démarrage — voir app.js) l'attrapent DÉJÀ eux-mêmes, avec
// des réactions différentes et volontaires (toast pour l'un, simple
// `console.warn` pour l'autre, qui ne doit pas alarmer l'utilisateur au
// lancement si MT5 est fermé). Mais le bouton « ⟳ Synchroniser » l'appelait
// directement, sans aucun `catch` — c'est très exactement ce que montre le
// journal serveur : la synchronisation échoue côté backend, et côté
// interface il ne se passe RIEN DU TOUT, ni toast ni message, juste le
// bouton qui se réactive en silence. C'est ce wrapper, et lui seul, qui est
// câblé sur le clic du bouton.
export async function syncTradesFromButton() {
  try {
    await syncTrades();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  }
}

// ── Bascule / suppression entre comptes enregistrés (jusqu'à 4) ─────────────

export async function switchAccount(login) {
  try {
    const r = await fetch(`${API}/mt5/accounts/${login}/switch`, { method: 'POST' });
    const data = await r.json();
    if (!r.ok) throw new Error(data.detail || 'Bascule échouée');
    bumpAccountEpoch();

    if (data.needs_initialization) {
      openInitJournalModal(data.account);
    } else {
      // Le type du compte (MT5 / manuel) conditionne ce que charge la page
      // Trades (filtre de source) : on le renseigne AVANT loadAll().
      await loadAccountInfo();
      await loadAll();
      showToast('Compte changé');
    }
    await refreshMT5State();
  } catch(e) {
    showToast('Erreur : ' + e.message);
  }
}

// ── Suppression de compte — confirmation avec case à cocher (correction #4) ──
// Un confirm() natif ne peut pas porter de case à cocher : on passe par une
// petite modale dédiée (#delete-account-modal dans index.html) qui mémorise
// le login en attente de confirmation, décoché par défaut pour la
// suppression des trades associés.
let _pendingDeleteLogin = null;

export function deleteAccount(login, ev) {
  if (ev) ev.stopPropagation(); // ne pas déclencher switchAccount() sur le pill parent
  _pendingDeleteLogin = login;
  const target = (state.accounts || []).find(a => a.login === login);
  const message = document.getElementById('delete-account-message');
  if (message) {
    message.textContent = target && target.mode === 'manual'
      ? "Retirer ce compte manuel du sélecteur ? Sans suppression de ses trades, ils restent en base mais ne s'afficheront plus nulle part."
      : 'Retirer ce compte du sélecteur ? Les identifiants ne seront plus enregistrés.';
  }
  document.getElementById('chk-delete-trades').checked = false;
  document.getElementById('delete-account-modal').style.display = 'flex';
}

export function closeDeleteAccountModal() {
  _pendingDeleteLogin = null;
  document.getElementById('delete-account-modal').style.display = 'none';
}

export async function confirmDeleteAccount() {
  const login = _pendingDeleteLogin;
  if (login == null) return;
  const deleteTrades = document.getElementById('chk-delete-trades').checked;
  closeDeleteAccountModal();
  try {
    const params = new URLSearchParams({ delete_trades: deleteTrades ? 'true' : 'false' });
    const r = await fetch(`${API}/mt5/accounts/${login}?${params.toString()}`, { method: 'DELETE' });
    if (!r.ok && r.status !== 204) {
      const data = await r.json().catch(() => ({}));
      throw new Error(data.detail || 'Suppression échouée');
    }
    bumpAccountEpoch();
    await loadAccountInfo();   // type du compte actif d'abord (voir switchAccount)
    await loadAll();
    await refreshMT5State();
    showToast(deleteTrades ? 'Compte et trades supprimés' : 'Compte retiré');
    // Plus aucun compte : on remontre l'écran d'accueil plutôt qu'un journal vide.
    await maybeShowWelcome();
  } catch(e) {
    showToast('Erreur : ' + e.message);
  }
}

// Liste des comptes enregistrés (MT5 + manuels). `null` = API injoignable
// (distinct de `[]` = aucun compte : c'est ce qui déclenche l'écran d'accueil).
async function fetchAccounts() {
  try {
    const r = await fetch(`${API}/mt5/accounts`);
    return r.ok ? await r.json() : [];
  } catch (e) {
    return null;
  }
}

function renderAccountSwitcher(connected, accounts) {
  const wrap = document.getElementById('account-switcher');
  state.accounts = accounts;
  wrap.innerHTML = '';
  accounts.forEach(a => {
    const pill = document.createElement('div');
    const isManual = a.mode === 'manual';
    const disconnected = !isManual && a.is_active && !connected;
    pill.className = 'acc-pill' + (a.is_active ? ' active' : '') + (disconnected ? ' disconnected' : '');
    // Un compte manuel n'a pas de login MT5 : son identifiant interne est
    // négatif et synthétique, il n'est jamais montré (ni dans le libellé, ni
    // dans l'infobulle).
    pill.title = isManual
      ? `Compte manuel — ${a.label || a.name || ''}`
      : disconnected
        ? `#${a.login} — ${a.server} — déconnecté, cliquez pour relancer MT5`
        : `#${a.login} — ${a.server}`;
    // Compte déjà actif MAIS déconnecté (MT5 fermé, session tombée) :
    // cliquer dessus doit tenter une reconnexion, exactement comme le
    // bouton « Relancer MT5 » — c'est très exactement le geste que
    // l'utilisateur attend ici (« seul l'appui sur un compte doit le
    // rouvrir »), et sans lui ce pill actif ne répondait à aucun clic.
    pill.onclick = () => {
      if (!a.is_active) { switchAccount(a.login); return; }
      if (disconnected) reconnectMT5();
    };
    const label = document.createElement('span');
    label.textContent = a.label || a.name || (isManual ? 'Compte manuel' : '#' + a.login);
    pill.appendChild(label);
    if (isManual) {
      const tag = document.createElement('span');
      tag.className = 'acc-pill-tag';
      tag.textContent = 'MAN';
      pill.appendChild(tag);
    }
    const del = document.createElement('span');
    del.className = 'acc-pill-del';
    del.textContent = '✕';
    del.title = 'Retirer ce compte';
    del.onclick = (ev) => deleteAccount(a.login, ev);
    pill.appendChild(del);
    wrap.appendChild(pill);
  });
  if (accounts.length < 4) {
    // Un seul « + » : l'écran d'accueil propose les trois types de compte
    // (MT5, manuel avec import, full manuel).
    const add = document.createElement('div');
    add.className = 'acc-pill-add';
    add.textContent = '+';
    add.title = 'Ajouter un compte (max. 4)';
    add.onclick = openWelcomeModal;
    wrap.appendChild(add);
  }
  return accounts;
}

// ── Bandeau d'état (réseau / MT5) ────────────────────────────────────────────
// L'app doit rester utilisable hors ligne : le journal est en base locale et
// s'affiche normalement. Ce bandeau ne masque donc rien et ne bloque rien, il
// explique seulement pourquoi les chiffres du COMPTE ne bougent pas.
function setAppBanner(message) {
  const banner = document.getElementById('app-status-banner');
  const text = document.getElementById('app-status-banner-text');
  if (!banner || !text) return;
  if (!message) { banner.hidden = true; return; }
  text.textContent = message;
  banner.hidden = false;
}

function renderConnectivityBanner({ connected, loading, simulated, apiReachable }) {
  if (!apiReachable) {
    setAppBanner("Service local injoignable — les données affichées peuvent ne pas être à jour. Redémarrez l'application si le problème persiste.");
    return;
  }
  if (connected) { setAppBanner(null); return; }
  if (loading) { setAppBanner('Initialisation de MetaTrader 5 — le journal reste consultable pendant ce temps.'); return; }
  // navigator.onLine ne prouve pas qu'internet fonctionne, mais un `false`
  // est fiable : la machine n'a aucune interface réseau active. On distingue
  // donc les deux causes que l'utilisateur peut corriger lui-même.
  if (navigator.onLine === false) {
    setAppBanner('Pas de connexion internet — MetaTrader 5 ne peut pas être joint. Le journal reste consultable hors ligne.');
    return;
  }
  if (simulated) {
    setAppBanner('MetaTrader 5 indisponible sur ce poste — le journal reste consultable, la synchronisation est suspendue.');
    return;
  }
  setAppBanner('MetaTrader 5 indisponible — vérifiez que le terminal est ouvert. Le journal reste consultable hors ligne.');
}

// Le bandeau réagit aussi au branchement/débranchement du réseau, sans
// attendre le prochain sondage de statut.
window.addEventListener('offline', () => refreshMT5State());
window.addEventListener('online', () => refreshMT5State());

// ── Compte manuel actif : pas de MT5 à surveiller ───────────────────────────
// Ni bandeau « MetaTrader 5 indisponible », ni polling de /api/mt5/status, ni
// pastille « Non connecté » : ces états n'ont aucun sens pour un compte qui
// n'utilise pas MT5. Le journal ne dépend que de la base locale.
function renderManualAccountState(accounts) {
  _stopPolling();
  _wasConnected = false;
  setAppBanner(null);
  renderAccountSwitcher(false, accounts);
  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const demoWarning = document.getElementById('demo-warning');
  dot.classList.remove('live');
  text.textContent = 'Compte manuel actif — MT5 non utilisé';
  text.title = "Ce compte n'utilise pas MetaTrader 5 : aucune connexion ni synchronisation.";
  demoWarning.style.display = 'none';
  document.getElementById('account-card').style.display = 'block';
}

// ── État réel de la connexion MT5 ────────────────────────────────────────────
// Interroge /api/mt5/status à chaque appel : ce n'est PAS un drapeau mis en
// cache côté frontend, la réponse reflète l'état vérifié côté backend au
// moment de l'appel (voir MT5Service.is_connected). Si le serveur du broker
// ne répond plus, "connected" repasse à false et la pastille l'affiche
// immédiatement, sans attendre une action explicite de l'utilisateur.
export async function refreshMT5State() {
  // Compte actif manuel : aucune interrogation de MT5 (voir plus haut).
  const accounts = await fetchAccounts();
  const activeAccount = (accounts || []).find(a => a.is_active);
  if (activeAccount && activeAccount.mode === 'manual') {
    renderManualAccountState(accounts);
    return;
  }
  // Compte MT5 actif (ou aucun) : la surveillance de la connexion tourne,
  // tant que la fenêtre est visible (idempotent).
  if (!document.hidden) _startPolling();

  const dot = document.getElementById('status-dot');
  const text = document.getElementById('status-text');
  const demoWarning = document.getElementById('demo-warning');

  let connected = false, simulated = false, lastError = null, loading = false;
  let apiReachable = true;
  try {
    const r = await fetch(`${API}/mt5/status`);
    // Sans ce contrôle, un corps d'erreur ({"detail": ...}) était interprété
    // comme un état de connexion — tous les champs ressortaient `undefined`
    // au lieu d'être traités comme « non connecté ».
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const s = await r.json();
    connected = !!s.connected;
    simulated = !!s.simulated;
    loading = !!s.loading;
    lastError = s.last_error || null;
  } catch(e) {
    // API injoignable ou réponse d'erreur : on ne peut pas prétendre être connecté
    connected = false;
    apiReachable = false;
  }

  // Aucun compte enregistré : rien à surveiller, donc pas de bandeau « MT5
  // indisponible » derrière l'écran d'accueil (seule une API injoignable reste signalée).
  if (apiReachable && accounts && accounts.length === 0) setAppBanner(null);
  else renderConnectivityBanner({ connected, loading, simulated, apiReachable });

  const accountsList = renderAccountSwitcher(connected, accounts || []);

  if (connected && !_wasConnected) {
    _wasConnected = true;
    await loadAccountInfo();
    await loadAll();
  }

  if (!connected) {
    _wasConnected = false;
    dot.classList.remove('live');
    // MetaTrader5 est chargé en tâche de fond au démarrage (voir
    // mt5_service.py) : pendant ces quelques secondes, l'app est déjà
    // ouverte et utilisable. Afficher "Non connecté" laissait croire à un
    // échec alors que la connexion n'a simplement pas encore été tentée.
    //
    // Correction : quand MT5 n'est simplement pas installé sur le poste
    // (`simulated` vrai avant toute tentative de connexion), la pastille
    // affichait le même "Non connecté" générique qu'une vraie coupure —
    // sans dire à l'utilisateur que la cause est l'absence du terminal, ni
    // qu'un mode démo reste disponible.
    if (loading) {
      text.textContent = 'Initialisation MT5…';
      text.title = 'Chargement de MetaTrader5 en cours';
    } else if (simulated) {
      text.textContent = 'MT5 non installé sur ce poste';
      text.title = 'Le terminal MetaTrader 5 est introuvable — connectez-vous pour utiliser le mode démo.';
    } else {
      text.textContent = 'Non connecté';
      text.title = lastError || '';
    }
    demoWarning.style.display = 'none';
    document.getElementById('account-card').style.display = accountsList.length ? 'block' : 'none';
    return;
  }

  dot.classList.add('live');
  demoWarning.style.display = simulated ? 'block' : 'none';

  const active = accountsList.find(a => a.is_active);
  text.textContent = simulated
    ? 'Connecté ✓ (démo)'
    : `Connecté ✓ — ${(active && (active.label || active.name)) || ''}`;
  text.title = '';
}

// ── Compte MT5 (solde/équité/marge réels du compte actif) ───────────────────

// ── Type du compte actif → interface (étape 5) ─────────────────────────────
// Un compte MANUEL n'a ni connexion MT5, ni synchro, ni marge : tout ce qui
// relève de MT5 disparaît de l'interface, et ce qui n'existe que pour lui
// (ajout/import de trades, filtre de source) apparaît. `mode` vaut 'mt5',
// 'manual', ou null quand aucun compte n'est actif.
function applyAccountModeUI(mode) {
  const previous = state.accountMode;
  state.accountMode = mode;
  const manual = mode === 'manual';

  // On quitte un compte manuel : son filtre de source (et les pages de
  // trades mises en cache avec) ne doit pas fuiter sur le compte suivant.
  if (previous === 'manual' && !manual) {
    const sourceSelect = document.getElementById('tf-source');
    if (sourceSelect) sourceSelect.value = '';
    state.tradesCache.clear();
  }

  const show = (id, visible, display = '') => {
    const el = document.getElementById(id);
    if (el) el.style.display = visible ? display : 'none';
  };
  show('btn-add-trade', manual);
  // Importer : dans le compte manuel actif, ou — sans aucun compte actif —
  // vers un nouveau compte manuel créé par l'import. Un compte MT5 reçoit ses
  // trades de la synchro : pour importer, on passe par « + » (accueil).
  show('btn-import-trades', manual || mode === null);
  show('tf-source-field', manual);
  document.querySelectorAll('.btn-sync[data-sync]').forEach(b => { b.style.display = manual ? 'none' : ''; });
  show('btn-relaunch-mt5', !manual);
  // Marge libre : notion propre à MT5.
  show('dashboard-margin-row', !manual);
  show('acc-margin-stat', !manual);

  const setText = (id, text) => { const el = document.getElementById(id); if (el) el.textContent = text; };
  setText('chart-equity-title', manual ? 'Courbe de capital (capital du compte manuel)' : 'Courbe de capital (solde compte MT5)');
  setText('account-card-icon', manual ? 'MAN' : 'MT5');
  const overview = document.getElementById('dashboard-account-card');
  if (overview) overview.setAttribute('aria-label', manual ? 'Situation du compte manuel' : 'Situation du compte MT5');

  // Réglages › Capital de référence : pour un compte manuel c'est le capital
  // de départ saisi, et le recalibrage n'a pas de sens (le capital se recalcule seul).
  show('ref-capital-recalibrate', !manual);
  setText('ref-capital-label', manual ? 'Capital de départ' : 'Capital de référence actuel');
  setText('ref-capital-note', manual
    ? 'Capital de départ saisi à la création du compte. Le capital courant = capital de départ + dépôts − retraits + résultat net (profit + commission + swap) des trades clôturés : il se recalcule tout seul.'
    : "Il est figé une fois pour toutes à la première synchronisation de votre compte MT5 — il ne bouge plus tout seul.");
}

// ── Compte actif (solde/équité/marge) ───────────────────────────────────────

export async function loadAccountInfo() {
  try {
    const r = await fetch(`${API}/account`);
    if (!r.ok) {
      // Aucun compte actif : rien à afficher, et le type est inconnu.
      applyAccountModeUI(null);
      setAccountDisplay('acc-balance', 'dashboard-balance', '—');
      setAccountDisplay('acc-equity', 'dashboard-equity', '—');
      setAccountDisplay('acc-margin', 'dashboard-margin', '—');
      return;
    }
    const d = await r.json();
    applyAccountModeUI(d.mode || 'mt5');
    const manual = state.accountMode === 'manual';
    setAccountDisplay('acc-balance', 'dashboard-balance', moneyAbs(d.balance));
    setAccountDisplay('acc-equity', 'dashboard-equity', moneyAbs(d.equity));
    if (!manual) setAccountDisplay('acc-margin', 'dashboard-margin', moneyAbs(d.free_margin));
    document.getElementById('account-card').style.display = 'block';
    // Compte manuel : pas de login MT5 (l'identifiant interne est négatif et
    // synthétique) ni de serveur — on ne pré-remplit pas le formulaire MT5.
    document.getElementById('inp-login').value = manual ? '' : d.login;
    document.getElementById('inp-server').value = manual ? '' : (d.server || '');
  } catch(e) { /* le sélecteur de comptes (refreshMT5State) gère déjà la visibilité de la carte */ }
}

function setAccountDisplay(settingsId, dashboardId, value) {
  const settingsElement = document.getElementById(settingsId);
  const dashboardElement = document.getElementById(dashboardId);
  if (settingsElement) settingsElement.textContent = value;
  if (dashboardElement) dashboardElement.textContent = value;
}

// ── Écran d'accueil : trois portes d'entrée ─────────────────────────────────
// Connecter MT5 · Compte manuel (import) · Full manuel. Affiché tant qu'aucun
// compte n'existe (voir maybeShowWelcome) et via le « + » du sélecteur.
export function openWelcomeModal() {
  document.getElementById('welcome-modal').style.display = 'flex';
}

export function closeWelcomeModal() {
  document.getElementById('welcome-modal').style.display = 'none';
}

export async function maybeShowWelcome() {
  const accounts = await fetchAccounts();
  if (accounts && accounts.length === 0) openWelcomeModal();
}

export async function chooseWelcomeOption(choice) {
  closeWelcomeModal();
  if (choice === 'mt5') {
    // La connexion MT5 vit dans Réglages : on y amène l'utilisateur, carte
    // dépliée. Login/serveur sont vidés : ils sont pré-remplis avec ceux du
    // compte actif (reconnexion), or ici on en ajoute un NOUVEAU.
    const nav = document.querySelector('.nav-item[onclick*="settings"]');
    if (nav) await goTo('settings', nav);
    setConnectCardCollapsed(false);
    const login = document.getElementById('inp-login');
    document.getElementById('inp-server').value = '';
    if (login) { login.value = ''; login.focus(); }
  } else if (choice === 'import') {
    const { openImportModal } = await import('./import.js');
    openImportModal({ newAccount: true });
  } else if (choice === 'manual') {
    openManualAccountModal();
  }
}

// ── Compte manuel (sans MT5) ────────────────────────────────────────────────
// Le même type de compte sert « manuel avec import » et « full manuel » ;
// seule la porte d'entrée diffère (écran d'accueil : « Full manuel » ouvre
// cette modale, « Compte manuel (import) » ouvre l'assistant d'import).
export function openManualAccountModal() {
  document.getElementById('man-name').value = '';
  document.getElementById('man-currency').value = 'USD';
  document.getElementById('man-balance').value = '';
  document.getElementById('man-start').value = '';
  document.getElementById('man-leverage').value = '100';
  document.getElementById('manual-account-modal').style.display = 'flex';
}

export function closeManualAccountModal() {
  document.getElementById('manual-account-modal').style.display = 'none';
}

export async function createManualAccount() {
  const name = document.getElementById('man-name').value.trim();
  const currency = document.getElementById('man-currency').value.trim();
  const balance = document.getElementById('man-balance').value;
  const start = document.getElementById('man-start').value;
  if (!name) return showToast('Nom du compte requis');
  if (balance === '' || !(Number(balance) > 0)) return showToast('Le capital de départ doit être supérieur à 0');

  const btn = document.getElementById('man-submit');
  btn.disabled = true;
  try {
    const body = { name, currency: currency || 'USD', initial_balance: Number(balance) };
    const leverage = Number(document.getElementById('man-leverage').value);
    if (leverage >= 1) body.leverage = Math.round(leverage);
    // Minuit LOCAL de la date choisie → instant UTC explicite (même règle que
    // l'initialisation historique d'un compte MT5, voir plus haut).
    if (start) body.start_date = new Date(`${start}T00:00:00`).toISOString();
    const r = await fetch(`${API}/accounts/manual`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) {
      const d = data.detail;
      throw new Error(typeof d === 'string' ? d : 'Saisie invalide — vérifiez le nom, la devise et le capital');
    }
    closeManualAccountModal();
    bumpAccountEpoch();
    showToast('Compte manuel créé');
    await loadAccountInfo();   // renseigne state.accountMode AVANT le rechargement
    await loadAll();
    await refreshMT5State();
  } catch (e) {
    showToast('Erreur : ' + e.message);
  } finally {
    btn.disabled = false;
  }
}
