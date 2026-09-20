const STORAGE_KEY = 'tj-theme';

export function currentTheme() {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

function applyToggleIcon() {
  const btn = document.getElementById('theme-toggle-btn');
  if (btn) btn.textContent = currentTheme() === 'light' ? '☀' : '🌙';
}

export function toggleTheme() {
  const next = currentTheme() === 'light' ? 'dark' : 'light';
  document.documentElement.setAttribute('data-theme', next);
  localStorage.setItem(STORAGE_KEY, next);
  applyToggleIcon();
  // Les couleurs par défaut de Chart.js dépendent du thème : on les réapplique
  // AVANT de prévenir les écrans, qui reconstruisent ensuite leurs graphiques.
  applyChartDefaults();
  window.dispatchEvent(new CustomEvent('themechange'));
}

export function initTheme() {
  applyToggleIcon();
  applyChartDefaults();
  // Si Chart.js n'est pas encore là (repli CDN chargé dynamiquement, voir
  // index.html), on repose les réglages dès qu'il arrive.
  if (!window.Chart) window.addEventListener('load', applyChartDefaults, { once: true });
}

// ── Palette des graphiques ───────────────────────────────────────────────────
// Chart.js fige les couleurs à la construction : il ne comprend pas les var()
// CSS. On les lit donc ici depuis le thème courant, et chaque graphique est
// reconstruit au changement de thème (voir l'écouteur `themechange`).
export function chartColors() {
  const s = getComputedStyle(document.documentElement);
  const v = (name, fallback) => (s.getPropertyValue(name) || fallback).trim();
  const light = document.documentElement.getAttribute('data-theme') === 'light';
  return {
    grid: v('--border', '#1F2A3A'),
    muted: v('--muted', '#94A3B8'),
    text: v('--text', '#E6EDF5'),
    title: v('--text-title', '#FFFFFF'),
    surface: v('--surface', '#111827'),
    accent: v('--accent', '#60A5FA'),
    primary: v('--primary', '#2563EB'),
    violet: v('--violet', '#8B5CF6'),
    pos: v('--pos', '#10B981'),
    neg: v('--neg', '#EF4444'),
    // Aplats pleine couleur (correction : ces teintes étaient translucides —
    // rgba(...,.18) à .22 — ce qui délavait les barres et les aires sous
    // courbe et les faisait paraître "sales" sur certains fonds. Mêmes
    // teintes que pos/neg/primary/violet, sans transparence.
    posSoft: 'rgba(16,185,129,1)',
    negSoft: 'rgba(239,68,68,1)',
    primarySoft: 'rgba(37,99,235,1)',
    violetSoft: 'rgba(139,92,246,1)',
    // L'infobulle prend le contre-pied du fond pour se détacher nettement.
    tooltipBg: light ? '#0F172A' : '#1E293B',
    tooltipText: '#F8FAFC',
  };
}

// Réglages Chart.js communs à TOUS les graphiques, posés une fois.
// Avant, chaque graphique redéclarait ses polices et ses infobulles dans son
// propre bloc d'options : les styles divergeaient d'un écran à l'autre et
// toute retouche demandait de repasser sur cinq fichiers.
export function applyChartDefaults() {
  const Chart = window.Chart;
  if (!Chart) return;
  const c = chartColors();

  Chart.defaults.font.family = "'Inter', system-ui, -apple-system, sans-serif";
  Chart.defaults.font.size = 11;
  Chart.defaults.color = c.muted;
  Chart.defaults.borderColor = c.grid;
  Chart.defaults.maintainAspectRatio = false;

  // Survol : viser un point n'exige pas d'être pile dessus, et toute la
  // verticale de la date s'affiche d'un coup — plus lisible que point par
  // point sur une courbe de capital dense.
  Chart.defaults.interaction.mode = 'index';
  Chart.defaults.interaction.intersect = false;

  Object.assign(Chart.defaults.plugins.tooltip, {
    backgroundColor: c.tooltipBg,
    titleColor: c.tooltipText,
    bodyColor: c.tooltipText,
    borderColor: 'rgba(148,163,184,.25)',
    borderWidth: 1,
    cornerRadius: 8,
    padding: 10,
    titleFont: { family: "'Montserrat', sans-serif", weight: '600', size: 12 },
    bodyFont: { family: "'DM Mono', monospace", size: 12 },
    displayColors: false,
  });

  Object.assign(Chart.defaults.plugins.legend.labels, {
    usePointStyle: true,
    pointStyle: 'circle',
    boxWidth: 7,
    boxHeight: 7,
    padding: 14,
    color: c.muted,
  });

  Chart.defaults.elements.bar.borderRadius = 5;
  Chart.defaults.elements.bar.borderSkipped = false;
  Chart.defaults.elements.line.tension = 0.35;
  Chart.defaults.elements.point.hoverRadius = 5;
  Chart.defaults.elements.point.hitRadius = 12;
}
