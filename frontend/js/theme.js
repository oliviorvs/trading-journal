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

// ── Typographie des graphiques ───────────────────────────────────────────────
// Chart.js ne comprend pas les var() CSS : on lit --font une fois et on la
// repasse partout. Avant, chaque module écrivait `family: 'DM Mono'` en dur
// dans chaque axe — une police qui n'existe plus depuis la reprise de la
// typographie du rapport, et que le navigateur remplaçait donc par sa
// monospace par défaut (Courier sous Windows) : les graduations juraient avec
// le reste de l'interface. Les chiffres restent alignés grâce à la variante
// tabulaire, activée ci-dessous.
export function chartFont() {
  const family = getComputedStyle(document.documentElement).getPropertyValue('--font').trim();
  return family || '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif';
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
    grid: v('--border', '#27303F'),
    muted: v('--muted', '#8B97A8'),
    text: v('--text', '#E6EBF2'),
    title: v('--text-title', '#FFFFFF'),
    surface: v('--surface', '#161D28'),
    accent: v('--accent', '#4A7DFF'),
    primary: v('--primary', '#3357B2'),
    violet: v('--violet', '#8B5CF6'),
    pos: v('--pos', '#2FB67C'),
    neg: v('--neg', '#E2574C'),
    // Aplats pleine couleur des barres et des aires.
    // Correction : ces quatre teintes étaient écrites en dur et venaient de
    // l'ANCIENNE charte (#10B981, #EF4444, #2563EB) — le vert, le rouge et
    // le bleu des barres ne correspondaient donc à aucune des couleurs du
    // thème repris du rapport, et ne suivaient pas non plus le passage en
    // thème clair. Elles lisent maintenant les mêmes variables que le reste.
    posSoft: v('--pos', '#2FB67C'),
    negSoft: v('--neg', '#E2574C'),
    primarySoft: v('--primary', '#3357B2'),
    violetSoft: v('--violet', '#8B5CF6'),
    // L'infobulle prend le contre-pied du fond pour se détacher nettement.
    tooltipBg: light ? '#111111' : '#1C2533',
    tooltipText: '#FFFFFF',
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

  Chart.defaults.font.family = chartFont();
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
    titleFont: { family: chartFont(), weight: '600', size: 12 },
    bodyFont: { family: chartFont(), size: 12 },
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
