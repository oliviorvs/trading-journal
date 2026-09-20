import { goTo } from './navigation.js';
import { connectMT5, syncTrades, syncTradesFromButton, reconnectMT5, loadAccountInfo, initConnectCard, toggleConnectCard, switchAccount, deleteAccount, closeDeleteAccountModal, confirmDeleteAccount, initializeJournalFromNow, initializeJournalHistorical, openManualAccountModal, closeManualAccountModal, createManualAccount, openWelcomeModal, closeWelcomeModal, chooseWelcomeOption, maybeShowWelcome } from './account.js';
import { state } from './config.js';
import { loadSettings, renderSetupOptions, renderEmotionOptions, renderErrorOptions, openSettingsListsModal, closeSettingsListsModal, addManagedValue } from './settings.js';
import { loadAll, switchChart, applyDashboardFilters, switchDashboardPeriod, redrawDashboardTheme } from './dashboard.js';
import { initTheme, toggleTheme } from './theme.js';

const _moduleCache = {};
function lazy(path, fnName) {
  return async (...args) => {
    if (!_moduleCache[path]) _moduleCache[path] = import(path);
    const mod = await _moduleCache[path];
    return mod[fnName](...args);
  };
}

Object.assign(window, {
  goTo,
  connectMT5, syncTrades, syncTradesFromButton, reconnectMT5,
  switchAccount, deleteAccount, closeDeleteAccountModal, confirmDeleteAccount,
  initializeJournalFromNow, initializeJournalHistorical,
  openManualAccountModal, closeManualAccountModal, createManualAccount,
  openWelcomeModal, closeWelcomeModal, chooseWelcomeOption,
  loadAll, switchChart,
  applyDashboardFilters, switchDashboardPeriod,
  toggleTheme,
  toggleConnectCard,
  renderSetupOptions,
  renderEmotionOptions,
  renderErrorOptions,
  openSettingsListsModal, closeSettingsListsModal, addManagedValue,

  saveSettings: lazy('./settings.js', 'saveSettings'),
  recalibrateCapital: lazy('./settings.js', 'recalibrateCapital'),

  applyTradeFilters: lazy('./trades.js', 'applyTradeFilters'),
  resetTradeFilters: lazy('./trades.js', 'resetTradeFilters'),
  changeTradePage: lazy('./trades.js', 'changeTradePage'),
  changeTradePageSize: lazy('./trades.js', 'changeTradePageSize'),
  deleteTrade: lazy('./trades.js', 'deleteTrade'),
  exportJournalPdf: lazy('./trades.js', 'exportJournalPdf'),

  switchPeriod: lazy('./period.js', 'switchPeriod'),
  changePeriodPage: lazy('./period.js', 'changePeriodPage'),
  changeMonth: lazy('./calendar.js', 'changeMonth'),

  // Fiche "vue" (lecture seule) et fiche "modification" (toutes les
  // écritures : tags, note, pièces jointes) sont deux modules distincts.
  openViewModal: lazy('./view.js', 'openViewModal'),
  closeViewModal: lazy('./view.js', 'closeViewModal'),
  openAttachmentLightbox: lazy('./view.js', 'openAttachmentLightbox'),

  openEditModal: lazy('./edit.js', 'openEditModal'),
  openCreateTradeModal: lazy('./edit.js', 'openCreateTradeModal'),
  closeEditModal: lazy('./edit.js', 'closeEditModal'),
  editFromView: lazy('./edit.js', 'editFromView'),
  saveNotes: lazy('./edit.js', 'saveNotes'),
  uploadAttachment: lazy('./edit.js', 'uploadAttachment'),
  deleteAttachment: lazy('./edit.js', 'deleteAttachment'),

  addCapitalMovement: lazy('./movements.js', 'addCapitalMovement'),
  deleteCapitalMovement: lazy('./movements.js', 'deleteCapitalMovement'),

  // Analyzer — chargé à la demande comme le reste ; toutes ces fonctions
  // sont appelées depuis des onclick du panneau page-analyzer.
  switchAnalyzerTab: lazy('./analyzer.js', 'switchAnalyzerTab'),
  applyAnalyzerFilters: lazy('./analyzer.js', 'applyAnalyzerFilters'),
  resetAnalyzerFilters: lazy('./analyzer.js', 'resetAnalyzerFilters'),
  openAnalyzerReport: lazy('./analyzer.js', 'openAnalyzerReport'),
  downloadAnalyzerExport: lazy('./analyzer.js', 'downloadAnalyzerExport'),
  generateAnalyzerRules: lazy('./analyzer.js', 'generateAnalyzerRules'),
  decideAnalyzerRule: lazy('./analyzer.js', 'decideAnalyzerRule'),
  addAnalyzerSession: lazy('./analyzer.js', 'addAnalyzerSession'),
  saveAnalyzerSessions: lazy('./analyzer.js', 'saveAnalyzerSessions'),
  addAnalyzerSymbolRule: lazy('./analyzer.js', 'addAnalyzerSymbolRule'),
  saveAnalyzerSymbolMap: lazy('./analyzer.js', 'saveAnalyzerSymbolMap'),
  applySymbolSuggestion: lazy('./analyzer.js', 'applySymbolSuggestion'),
  createDefaultSop: lazy('./analyzer.js', 'createDefaultSop'),

  openImportModal: lazy('./import.js', 'openImportModal'),
  closeImportModal: lazy('./import.js', 'closeImportModal'),
  backToImportFile: lazy('./import.js', 'backToImportFile'),
  analyzeImportFile: lazy('./import.js', 'analyzeImportFile'),
  applyImportMapping: lazy('./import.js', 'applyImportMapping'),
  confirmImportCommit: lazy('./import.js', 'confirmImportCommit'),
});

window.addEventListener('themechange', async () => {
  redrawDashboardTheme();
  const activePanel = document.querySelector('.panel.active');
  if (!activePanel) return;
  if (activePanel.id === 'page-calendar') {
    const { redrawCalendarTheme } = await import('./calendar.js');
    redrawCalendarTheme();
  }
  if (activePanel.id === 'page-symbols') {
    const { redrawSymbolsTheme } = await import('./symbols.js');
    redrawSymbolsTheme();
  }
  if (activePanel.id === 'page-week') {
    const { redrawPeriodTheme } = await import('./period.js');
    redrawPeriodTheme();
  }
  if (activePanel.id === 'page-analyzer') {
    const { redrawAnalyzerTheme } = await import('./analyzer.js');
    redrawAnalyzerTheme();
  }

});

let automaticSyncStarted = false;

async function syncAfterAuthentication() {
  if (automaticSyncStarted) return;
  // Compte manuel actif : aucune connexion MT5, donc aucune synchro à lancer.
  // (Le type du compte est connu ici : loadAccountInfo() a déjà tourné dans
  // loadDashboardData.) Le drapeau reste levé pour laisser une nouvelle
  // tentative après un déverrouillage sur un compte MT5.
  if (state.accountMode === 'manual') return;
  automaticSyncStarted = true;
  try {
    // Resynchronise SI MT5 est joignable. Un échec ici (MT5 fermé, compte
    // pas encore initialisé, broker injoignable) ne doit RIEN empêcher :
    // le dashboard a déjà été alimenté depuis la base par
    // loadDashboardData(), qui ne dépend pas de MT5.
    await syncTrades();
  } catch (error) {
    console.warn('sync MT5 indisponible au démarrage', error);
  }
}

// ── Init ──────────────────────────────────────────────────────────────────────
// initTheme() est purement local (localStorage + classes CSS) : aucune raison
// de le retarder, l'écran de verrouillage doit déjà s'afficher au bon thème.
initTheme();

// initConnectCard() pose un listener `visibilitychange` et démarre le
// polling : il ne doit tourner qu'une fois, même après un verrouillage /
// déverrouillage (bouton « Fermer le journal »), sinon les listeners et les
// timers s'empilent.
let connectCardReady = false;

// Écran de démarrage (défini dans index.html) : il reste affiché pendant le
// chargement des données et annonce la fonction en cours. Appels encapsulés
// pour qu'une absence du contrôleur ne bloque jamais le chargement.
const BOOT_CAPTION = 'CHARGEMENT DE VOTRE JOURNAL';
function boot(method, ...args) {
  try { window.tjBoot?.[method]?.(...args); } catch (e) { /* purement cosmétique */ }
}

async function loadDashboardData() {
  if (!connectCardReady) {
    connectCardReady = true;
    boot('step', 46, BOOT_CAPTION, 'initConnectCard()');
    initConnectCard();        // déclenche /api/mt5/status : nécessite le jeton
  }
  boot('step', 55, BOOT_CAPTION, 'loadSettings()');
  await loadSettings();

  boot('step', 62, BOOT_CAPTION, 'renderSetupOptions()');
  renderSetupOptions();
  renderEmotionOptions();
  renderErrorOptions();

  boot('step', 72, BOOT_CAPTION, 'loadAll()');
  await loadAll();

  boot('step', 88, BOOT_CAPTION, 'loadAccountInfo()');
  await loadAccountInfo();
}

async function onAuthenticated() {
  try {
    await loadDashboardData();
    // L'écran est libéré ICI, pas après la synchronisation : le dashboard est
    // déjà complet et lisible, et syncTrades() dépend de MT5 — donc peut
    // prendre du temps ou échouer sans que cela concerne l'utilisateur.
    boot('done');
    // Aucun compte enregistré : l'écran d'accueil propose les trois façons
    // d'alimenter le journal (MT5, manuel avec import, full manuel).
    maybeShowWelcome().catch(() => {});
  } catch (error) {
    console.warn('chargement initial du dashboard', error);
    boot('fail', 'chargement interrompu');
  }
  await syncAfterAuthentication();
}

// Pas de `{ once: true }` : après un verrouillage manuel puis un nouveau
// déverrouillage, les données affichées appartiennent à l'ancienne session et
// le jeton a changé — il faut les recharger.
window.addEventListener('tj-authenticated', onAuthenticated);

if (window.__tjAuthReady) {
  // Le module a fini de charger APRÈS le déverrouillage : l'événement est
  // déjà passé, on enchaîne directement.
  onAuthenticated();
}
