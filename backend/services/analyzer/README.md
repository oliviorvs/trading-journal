# Module Analyzer

Onglet d'analyses avancées du journal. Répond à une seule question :
**« Qu'est-ce qui caractérise mes bons trades, mes mauvais trades et mes
erreurs répétées ? »**

Il n'exécute aucun trade, ne donne aucun signal, n'envoie rien hors de la
machine, et ne transforme jamais une petite série statistique en règle
automatique.

Documents de référence : `docs/definitions-metriques.md` (définitions figées)
et le cahier des charges v2.

---

## Comment le désactiver

Le module est **additif et réversible**. Pour le retirer entièrement :

1. commenter `app.include_router(analyzer_router.router)` dans `backend/main.py` ;
2. retirer l'entrée `goTo('analyzer',this)` de `frontend/index.html`.

Les tables restent en base, inertes. **Aucune donnée n'est perdue** : ni les
trades (jamais écrits par ce module), ni les annotations saisies.

## Structure

```
backend/services/analyzer/
├── models.py       13 tables, toutes avec account_id, zéro ALTER sur l'existant
├── adapter.py      lecture UNIQUE → TradeView immuables + mémorisation
├── config.py       réglages (compte → global → défaut)
├── sessions.py     fenêtres de session en heure serveur
├── metrics.py      métriques de groupe (compose services/stats.py)
├── reliability.py  Wilson, intervalles, statuts
├── sop.py          plan de trading versionné
├── playbook.py     règles proposées
├── report.py       rapport HTML autonome
├── exports.py      JSON / CSV / XLSX
└── analyzers/      performance · dimensions · psychology · behaviour ·
                    discipline · patterns · data_quality
backend/routers/analyzer.py     endpoints /api/analyzer/*
frontend/js/analyzer.js         panneau à 10 sous-onglets, chargé à la demande
frontend/css/components/analyzer.css
```

## Les règles qui structurent le code

| # | Règle | Où elle se voit |
|---|---|---|
| R1 | Le journal est la source de vérité | aucun `db.add(Trade)` dans ce module |
| R2 | Zéro `ALTER` | `models.py` ne déclare que de nouvelles tables |
| R3 | Isolation par compte | `account_id` partout, lectures via `state.filter_active` |
| R4 | Une seule définition des métriques | `performance.py` appelle `stats.compute_stats` **telle quelle** |
| R5 | Zéro régression | les 57 tests d'origine tournent à chaque fois |
| R7 | Lecture unique | aucun analyseur ne voit la session SQLAlchemy |
| R8 | Statistique prudente | `n` toujours affiché, aucune règle automatique |
| R9 | Local et léger | **aucune dépendance ajoutée** |

## Deux pièges d'intégration

1. **Les modèles doivent être importés dans `main.py` AVANT
   `Base.metadata.create_all()`** — sinon les tables ne sont jamais créées et
   chaque endpoint tombe en 500. Un test le vérifie
   (`test_tables_are_created_on_a_fresh_database`).
2. **`tests/conftest.py` vide les tables modèle par modèle** : toute nouvelle
   table doit y être ajoutée, sinon les tests se contaminent entre eux et les
   échecs dépendent de l'ordre d'exécution.

## Pourquoi une mémorisation par révision

`Trade` n'a pas de date de modification, seulement `created_at` : la clé de
cache envisagée à l'origine (« date de modification des trades ») n'est pas
calculable. Un compteur de révision est donc incrémenté par des écouteurs
SQLAlchemy posés **par ce module** sur `Trade`, les tables `trade_*` et les
tables de configuration — aucun router existant n'est modifié.

Le compteur est **global**, pas par compte : toute écriture invalide le cache
de tous les comptes. C'est volontaire (les écritures sont rares, recharger
coûte quelques secondes) et cela évite une comptabilité fine facile à fausser.

## Décisions appliquées (§19 du cahier)

D1 brut **et** net, défaut brut · D2 pas de Pandas · D3 sessions en heure
serveur · D4 mémorisation en mémoire · D5 SOP à 6 éléments configurable ·
D6 émotions/erreurs multiples · D7 un onglet + sous-onglets · D8 WAL et
sauvegarde **hors périmètre** · D9 statut plafonné si l'intervalle contient 0.

## Écarts assumés par rapport au cahier v2

1. **`analyzer_symbol_map`** : contrainte sur `(account_id, raw_symbol)` et non
   `raw_symbol` seul — deux courtiers peuvent utiliser le même suffixe
   différemment, et l'isolation par compte (R3) prime.
2. **Plan vs Réalité, volet « R:R prévu »** : `TradeView` n'expose pas les prix
   bruts, volontairement. Le module fournit à la place la distribution du R
   **réalisé** par raison de sortie. À arbitrer si l'on préfère enrichir
   `TradeView`.

## Ce qui n'a pas été vérifié

- build PyInstaller et installateur **sous Windows** ;
- rendu visuel à **1360×768**, thèmes clair et sombre ;
- **la convention horaire sur un vrai trade MT5** — comparer l'heure affichée
  dans MT5 et celle du journal. Toute l'analyse par session en dépend : si les
  heures ne sont pas celles du serveur du courtier, les fenêtres sont à
  décaler (onglet Paramètres) ;
- performance au premier chargement à 100 000 trades : **objectif non
  atteint**, voir `tests/README.md`.
