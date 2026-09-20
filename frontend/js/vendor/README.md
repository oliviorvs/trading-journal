# Dépendances front embarquées

Ce dossier contient les librairies tierces servies **localement**, pour que
l'application fonctionne sans connexion internet (objectif « app locale »).

## Chart.js 4.4.1

`chart.umd.min.js` est **versionné avec le projet**. C'est le build UMD
officiel de Chart.js 4.4.1 (licence MIT, `chart.js@4.4.1` sur npm), repassé
sous `terser` (`-c -m --comments false`) pour retirer les derniers
commentaires et espaces.

Note : le fichier `dist/chart.umd.js` que publie le paquet npm est déjà du
code condensé (noms de variables réduits, aucun retour à la ligne) — malgré
son nom sans « .min », ce n'est pas un build « lisible ». Le passage par
`terser` ne réduit donc que marginalement sa taille (≈1%) ; il n'existe pas
de build UMD sensiblement plus petit à récupérer par ailleurs.

`frontend/index.html` le charge en `<script defer>`, **sans aucun repli
CDN** : sur un poste hors ligne, une requête vouée à échouer laisserait tous
les graphiques cassés sans message. Si le fichier disparaît, `onerror`
positionne `window.__chartMissing` et le problème est signalé dans
l'interface plutôt que masqué.

Pour mettre à jour la version :

```bash
npm pack chart.js@<version>
tar xzf chart.js-<version>.tgz package/dist/chart.umd.js
npx terser package/dist/chart.umd.js -c -m --comments false \
  -o frontend/js/vendor/chart.umd.min.js
```

Le dossier `frontend/` entier est embarqué par PyInstaller
(`("frontend", "frontend")` dans `TradingJournal.spec`) : aucun réglage de
build supplémentaire n'est nécessaire.

## Polices

`index.html` charge Inter, DM Mono et Montserrat depuis Google Fonts **de façon
asynchrone, et uniquement si `navigator.onLine` est vrai** — sur un poste hors
ligne, aucune requête n'est même tentée. Les polices de repli (`system-ui`,
`monospace`, définies par `--font` / `--mono` dans `css/base/variables.css`)
s'appliquent alors. Pour un rendu strictement identique hors ligne, télécharger
les `.woff2` dans ce dossier et remplacer le chargement par des règles
`@font-face` locales.
