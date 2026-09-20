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
