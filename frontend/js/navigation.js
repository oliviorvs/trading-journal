export async function goTo(page, el) {
  document.querySelectorAll('.panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  el.classList.add('active');

  switch (page) {
    case 'calendar': {
      const { buildCalendar, loadExtendedStats } = await import('./calendar.js');
      buildCalendar();
      loadExtendedStats();
      break;
    }
    case 'week': {
      const { loadPeriod } = await import('./period.js');
      loadPeriod();
      break;
    }
    case 'symbols': {
      const { loadSymbols } = await import('./symbols.js');
      loadSymbols();
      break;
    }
    case 'trades': {
      const { applyTradeFilters } = await import('./trades.js');
      applyTradeFilters();
      break;
    }
    case 'settings': {
      const { loadSettingsForm } = await import('./settings.js');
      loadSettingsForm();
      break;
    }
    case 'analyzer': {
      const { loadAnalyzer } = await import('./analyzer.js');
      loadAnalyzer();
      break;
    }
    case 'repartitions': {
      const { loadRepartitions } = await import('./repartitions.js');
      loadRepartitions();
      break;
    }
    // 'dashboard' : déjà chargé et rempli au démarrage, rien à faire ici.
  }
}

// ── Rechargement du panneau affiché ─────────────────────────────────────────
//
// ISOLATION DES COMPTES. `loadAll()` ne recharge que le dashboard et la page
// Trades. Tous les autres panneaux (Calendrier, Semaine/Mois, Symboles,
// Répartitions, Analyzer) ne sont alimentés que par `goTo()`, c'est-à-dire au
// moment où l'on clique sur leur entrée de menu.
//
// Conséquence avant cette correction : en basculant de compte depuis l'un de
// ces écrans, le panneau restait à l'image du compte PRÉCÉDENT — calendrier,
// heatmap, répartitions et analyses comprises — jusqu'à ce que l'utilisateur
// reparte et y revienne. Rien ne le signalait, et les chiffres affichés
// étaient parfaitement plausibles : c'est exactement la confusion de données
// entre comptes qu'on cherchait à éliminer.
//
// Appelée après chaque changement de compte (bascule, suppression, création,
// import vers un nouveau compte — voir account.js et import.js). Ne fait rien
// pour le dashboard et la page Trades, déjà couverts par `loadAll()`.
export async function refreshActivePanel() {
  const active = document.querySelector('.panel.active');
  if (!active) return;
  try {
    switch (active.id) {
      case 'page-calendar': {
        const { buildCalendar, loadExtendedStats } = await import('./calendar.js');
        await buildCalendar();
        await loadExtendedStats();
        break;
      }
      case 'page-week': {
        const { loadPeriod } = await import('./period.js');
        await loadPeriod();
        break;
      }
      case 'page-symbols': {
        const { loadSymbols } = await import('./symbols.js');
        await loadSymbols();
        break;
      }
      case 'page-repartitions': {
        const { loadRepartitions } = await import('./repartitions.js');
        await loadRepartitions();
        break;
      }
      case 'page-analyzer': {
        const { loadAnalyzer } = await import('./analyzer.js');
        await loadAnalyzer();
        break;
      }
      // 'page-dashboard', 'page-trades', 'page-settings' : rechargés par
      // loadAll() / loadAccountInfo(), rien à faire de plus ici.
    }
  } catch (e) {
    // Un panneau qui ne se recharge pas ne doit pas interrompre la bascule
    // de compte elle-même (le reste de l'interface est déjà à jour).
    console.warn('rechargement du panneau actif', e);
  }
}
