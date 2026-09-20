# Trading Journal

Journal de trading local pour MetaTrader 4/5. L'application regroupe les
trades, les comptes, les mouvements de capital, les notes et les analyses dans
une base SQLite locale.

Elle fonctionne :

- dans un navigateur, avec l'API FastAPI et le frontend servi séparément ;
- comme application Windows native PyWebView, avec l'API et le frontend dans
  un seul exécutable.

Les données restent sur l'ordinateur. L'application ne transmet aucune
donnée de trading à un service distant.

## Fonctionnalités

- Connexion à un ou plusieurs comptes MetaTrader 5 ;
- comptes manuels, utilisables sans terminal MT5 ;
- synchronisation de l'historique des trades et des informations de compte ;
- import de relevés MT5 CSV, HTML et XLSX avec aperçu, mapping et détection
  des doublons ;
- saisie et modification manuelle des trades ;
- dépôts, retraits et ajustements de capital ;
- dashboard avec capital, equity, drawdown, rendement, risque et statistiques ;
- calendrier de performance, performances par période, symbole et direction ;
- répartitions par setup, session, émotion, erreur et discipline ;
- notes, tags et captures d'écran attachées aux trades ;
- export PDF, CSV, JSON et XLSX ;
- thème clair/sombre et interface responsive ;
- verrou local par code d'accès, avec session API protégée ;
- module **Analyzer** : SOP versionné, qualité des données, patterns,
  overtrading, fiabilité statistique, rapport HTML autonome et exports.

L'Analyzer est un outil d'analyse rétrospective. Il n'exécute aucun ordre,
n'envoie aucun signal et ne remplace pas une décision de trading. Sa
documentation technique se trouve dans
[`backend/services/analyzer/README.md`](backend/services/analyzer/README.md).

## Prérequis

- Windows 10 ou 11 ;
- Python 3.12 recommandé ;
- connexion internet uniquement pour installer les dépendances ;
- MetaTrader 5 installé et ouvert pour une connexion réelle ;
- compte MT5 et identifiants de trading pour la synchronisation.

MetaTrader 5 est optionnel : sans terminal disponible, l'application reste
utilisable avec les comptes manuels et le mode simulation intégré.

## Démarrage en mode navigateur

Depuis la racine du projet, double-cliquer sur `START.bat`.

Le script :

1. vérifie Python et peut tenter son installation sous Windows ;
2. installe `backend/requirements.txt` ;
3. crée ou réutilise la base SQLite dans `db/` ;
4. démarre l'API sur `http://127.0.0.1:8000` ;
5. sert `frontend/` sur `http://127.0.0.1:5500` ;
6. ouvre l'interface dans le navigateur.

Documentation interactive de l'API :
[`http://127.0.0.1:8000/docs`](http://127.0.0.1:8000/docs).

Pour arrêter ce mode, fermer les fenêtres **Trading Journal API** et
**Trading Journal Web** ouvertes par `START.bat`.

## Première ouverture

Au premier lancement, l'application demande de créer un code d'accès local et
fournit un code de récupération. Le code d'accès et le code de récupération
sont stockés sous forme de hash.

Le code d'accès protège l'interface et les routes de données de l'API. Il ne
chiffre pas la base SQLite et ne protège pas un ordinateur auquel un tiers a
un accès administrateur complet.

En cas d'oubli, l'option **Code oublié ?** permet de définir un nouveau code
avec le code de récupération. Si les deux codes sont perdus, une réinitialisation
technique du stockage d'authentification peut retirer le verrou sans supprimer
la base de trading ; sauvegarder les données avant toute intervention.

## Comptes et MT5

L'application accepte les comptes MT5 et les comptes manuels. Un compte peut
être défini comme compte actif ; les trades, réglages, mouvements et analyses
sont isolés par compte.

Pour connecter MT5, renseigner dans l'interface :

- le numéro de login ;
- le mot de passe de trading, pas le mot de passe investisseur ;
- le nom exact du serveur du broker.

Les mots de passe MT5 sont chiffrés avant leur stockage local. Si le terminal
MT5 est fermé ou si MetaTrader5 n'est pas disponible, l'interface reste
ouverte et la reconnexion passive attend que le terminal soit disponible.

## Import de relevés

Le module d'import accepte les relevés MT5 pris en charge par les parseurs du
projet. Le flux est :

1. sélectionner un fichier ;
2. afficher l'aperçu et les lignes reconnues ;
3. corriger le mapping si nécessaire ;
4. valider l'import ;
5. consulter l'historique des lots importés ou annuler un lot.

Les fichiers réellement utilisés pour valider les parseurs sont synthétiques.
Un relevé provenant d'un broker doit donc être contrôlé dans l'aperçu avant
validation, en particulier pour les dépôts et retraits.

## Application Windows

### Construire l'exécutable

Lancer `build.bat` depuis la racine du projet. Le script :

1. crée ou réutilise `build_venv` ;
2. installe les dépendances de `backend/requirements.txt` ;
3. installe les outils de `requirements-build.txt` ;
4. nettoie `build/` et `dist/` ;
5. compile `TradingJournal.spec` avec PyInstaller.

Le résultat est `dist/TradingJournal.exe`. Les métadonnées Windows de
l'exécutable indiquent l'auteur/éditeur **OLI Tech - Olivio Rvs**.

L'exécutable lance l'API, sert automatiquement le frontend et ouvre une
fenêtre native PyWebView. Il choisit un port local libre si le port 8000 est
déjà occupé. Les exports PDF, CSV, JSON et XLSX utilisent les boîtes de
dialogue natives de sauvegarde.

### Construire l'installateur

Installer [NSIS](https://nsis.sourceforge.io/Download), puis :

1. lancer `build.bat` ;
2. vérifier que `dist/TradingJournal.exe` existe ;
3. lancer `build_installer.bat`.

Le résultat est `dist_installer/MLxPhantomJournal_Setup.exe`. L'installateur :

- affiche `LICENSE.txt` ;
- installe par utilisateur, sans droits administrateur, dans
  `%LOCALAPPDATA%\MLxPhantomJournal` ;
- crée les raccourcis du menu Démarrer et, au choix, du Bureau ;
- ajoute une entrée de désinstallation Windows ;
- conserve les données locales lors de la désinstallation.

## Structure du projet

```text
.
├── backend/
│   ├── main.py                 Assemblage FastAPI, sécurité et frontend statique
│   ├── database.py             Moteur SQLite et migrations légères
│   ├── models.py               Modèles principaux du journal
│   ├── schemas.py              Schémas d'API
│   ├── auth_utils.py           Hash et sessions locales
│   ├── mt5_service.py          Connexion, simulation et synchronisation MT5
│   ├── state.py                État du compte actif et services partagés
│   ├── routers/                Routes auth, comptes, trades, import, stats...
│   └── services/               Calculs, imports, exports, mouvements, Analyzer
├── frontend/
│   ├── index.html              Interface principale
│   ├── css/                    Styles de l'application
│   ├── js/                     Modules dashboard, trades, import, Analyzer...
│   └── assets/                 Logo et ressources visuelles
├── tests/                      Tests backend et données de test
├── docs/                       Définitions des métriques
├── db/                         Base SQLite et clé de chiffrement locales
├── logs/                       Logs de l'API
├── launcher.py                 Lanceur de l'application desktop
├── START.bat                   Démarrage navigateur
├── build.bat                   Build PyInstaller
├── build_installer.bat         Build de l'installateur NSIS
├── TradingJournal.spec         Configuration PyInstaller
├── version_info.txt            Métadonnées Windows de l'exécutable
├── installer/installer.nsi     Script NSIS
├── LICENSE.txt                 Licence d'utilisation
└── requirements-build.txt      Dépendances de compilation uniquement
```

## API principale

Toutes les routes de données sont sous `/api/` et nécessitent une session,
à l'exception des routes d'authentification. Les groupes disponibles sont :

| Groupe | Usage |
| --- | --- |
| `/api/auth` | statut, création et vérification du code d'accès |
| `/api/accounts` | comptes manuels et gestion des comptes |
| `/api/mt5` | connexion, statut, synchronisation et comptes MT5 |
| `/api/trades` | liste, création, modification et pièces jointes |
| `/api/import` | aperçu, validation et lots d'import |
| `/api/capital-movements` | dépôts, retraits et ajustements |
| `/api/performance` | statistiques, périodes, calendrier et répartitions |
| `/api/analyzer` | analyses avancées, SOP, rapport et exports |
| `/api/export` | génération des rapports PDF |
| `/api/settings` | préférences et compte actif |

La documentation complète et les schémas sont disponibles dans Swagger sur
`/docs` lorsque l'API est démarrée.

## Tests

Depuis la racine du projet :

```bat
python -m pip install -r backend\requirements.txt
python -m pip install pytest httpx openpyxl
python -m pytest tests -q
```

Les tests utilisent des copies temporaires de la base, des logs et des
uploads ; le dossier `db/` du projet n'est pas modifié. L'authentification est
désactivée dans les tests et MetaTrader 5 est simulé.

`tests/make_report.py` génère des relevés synthétiques pour tester les
parseurs. La synchronisation MT5 réelle avec un terminal et les formats
spécifiques des brokers doivent être vérifiés séparément.

## Données locales et sauvegardes

Les fichiers importants sont :

- `db/trading_journal.db` : données du journal ;
- `db/.secret_key` : clé de chiffrement des identifiants MT5 ;
- `backend/uploads/` : captures d'écran et pièces jointes ;
- `logs/backend.log` : journal de fonctionnement.

Pour sauvegarder ou migrer une installation, arrêter l'application puis
copier `db/`, `backend/uploads/` et, si nécessaire, `logs/`. Ne pas publier la
base, la clé ou les pièces jointes dans un dépôt public.

## Licence

Voir [`LICENSE.txt`](LICENSE.txt). L'application est fournie sans garantie et
ne constitue pas un conseil financier.