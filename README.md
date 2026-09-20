# MLxPhantom Journal — MT4/5

Journal de trading local pour analyser les trades MetaTrader, suivre la discipline et conserver les notes et captures d'écran dans une base SQLite.

L'application peut être utilisée dans un navigateur local ou comme application desktop Windows avec PyWebView. Les données restent sur l'ordinateur.

## Structure du projet

```text
trading-journal/
├── backend/
│   ├── main.py          ← API FastAPI (endpoints)
│   ├── database.py      ← Config SQLite
│   ├── models.py        ← Tables (trades, account, snapshots)
│   ├── schemas.py       ← Schémas de réponse API
│   ├── mt5_service.py   ← Bridge MetaTrader5
│   ├── services/analyzer/ ← Module Analyzer (voir son README)
│   └── requirements.txt
├── frontend/
│   ├── index.html       ← Interface principale et écran de connexion
│   ├── assets/logo.png  ← Logo MLxPhantom Journal
│   ├── css/styles.css
│   └── js/               ← Modules de dashboard, trades et statistiques
├── docs/                 ← Définitions de métriques figées (Analyzer)
├── db/                   ← Base SQLite et clé de chiffrement créée au lancement
├── logs/                 ← Logs backend
├── launcher.py           ← Lanceur de l'application desktop PyWebView
├── START.bat             ← Lance le backend et le frontend dans le navigateur
├── build.bat             ← Construit l'exécutable Windows
├── build_installer.bat   ← Construit l'installateur NSIS
├── installer/installer.nsi ← Script NSIS de l'installateur
├── LICENSE.txt           ← Licence d'utilisation affichée à l'installation
├── TradingJournal.spec   ← Configuration PyInstaller
└── requirements-build.txt
```

## Prérequis

- Windows 10/11
- Python 3.12 recommandé, avec **Add Python to PATH** activé
- Une connexion Internet au premier lancement pour installer les dépendances
- MetaTrader 5 installé et ouvert pour connecter un vrai compte
- Un compte MT5 et ses identifiants de trading

## Installation et lancement navigateur (Windows)

### 1. Installer Python

Télécharger Python depuis [python.org](https://www.python.org/downloads/) et cocher **Add Python to PATH**.

### 2. Installer les dépendances et démarrer

Double-cliquer sur **START.bat**. Le script vérifie Python, installe exactement les dépendances de `backend/requirements.txt` avec `python -m pip`, démarre l'API sur `http://127.0.0.1:8000`, puis ouvre l'interface sur `http://127.0.0.1:5500`.

Le script installe notamment FastAPI, Uvicorn, SQLAlchemy, Pydantic, PyWebView, FPDF2, Matplotlib, Pillow et Cryptography. En cas d'erreur d'installation, vérifiez la connexion Internet et relancez le script depuis le dossier du projet.

### 3. Installer MetaTrader 5

- Télécharger [MT5](https://www.metatrader5.com/fr/download)
- Ouvrir un compte démo chez votre broker (ex: ICMarkets, XM, Pepperstone)

### 4. Librairie Python MT5

La dépendance `MetaTrader5==5.0.45` est installée automatiquement sous Windows. Si le terminal MT5 n'est pas installé ou connecté, l'application peut fonctionner en mode simulation, mais aucune synchronisation de compte réel ne sera possible.

## Application desktop Windows

Pour construire l'exécutable :

1. Double-cliquer sur **build.bat**.
2. Le script crée ou réutilise un environnement `build_venv`.
3. Il installe `backend/requirements.txt` puis `requirements-build.txt` avec le Python de cet environnement.
4. Il génère `dist/TradingJournal.exe` avec PyInstaller.

Le script lance PyInstaller avec `python -m PyInstaller` depuis `build_venv`. Cette forme évite les erreurs causées par un ancien fichier `pyinstaller.exe` copié depuis un autre dossier ou une autre installation Python. Si l'environnement `build_venv` est endommagé, supprimez uniquement ce dossier puis relancez `build.bat` pour le recréer.

L'exécutable lance FastAPI et l'interface dans une fenêtre native PyWebView, sans ouvrir de navigateur. Il utilise le même frontend et les mêmes données que le mode navigateur.

Après une modification du code, reconstruire l'exécutable pour l'intégrer dans `dist/`.

## Création de l'installateur Windows

L'installateur nécessite [NSIS](https://nsis.sourceforge.io/Download) installé sur Windows.

1. Construire d'abord l'exécutable avec **build.bat**.
2. Installer NSIS et vérifier que `makensis.exe` est disponible dans le PATH, ou installé dans `Program Files\NSIS`.
3. Lancer **build_installer.bat**.
4. Récupérer `dist_installer/MLxPhantomJournal_Setup.exe`.

L'installateur :

- affiche `LICENSE.txt` et demande son acceptation ;
- installe l'application par utilisateur dans `%LOCALAPPDATA%\MLxPhantomJournal` ;
- ne demande pas de droits administrateur ;
- crée les raccourcis du menu Démarrer et, au choix, du Bureau ;
- ajoute l'application à la liste Windows des programmes installés ;
- conserve `db/`, `logs/` et `backend/uploads/` lors de la désinstallation.

Les données restent à côté de l'exécutable installé. Pour une sauvegarde ou une migration, copier `db/`, `logs/` et `backend/uploads/` avant de désinstaller.

## Connexion MT5

Dans le panneau latéral gauche, entrez :

- **Login** : votre numéro de compte MT5
- **Mot de passe** : votre mot de passe de trading (pas d'investisseur)
- **Serveur** : ex. `ICMarkets-Demo`, `XM-MT5`, `Pepperstone-Demo`

Ces infos se trouvent dans MetaTrader 5 → Fichier → Connexion.

Jusqu'à quatre comptes peuvent être enregistrés et un compte peut être sélectionné comme compte actif. Les mots de passe MT5 sont chiffrés avant leur stockage local.

## Fonctionnalités actuelles

- Écran de chargement MLxPhantom Journal de 5 secondes
- Landing page de connexion avec logo
- Code d'accès local au premier lancement
- Bouton **Fermer le journal** pour revenir au landing sans supprimer les données
- Dashboard avec capital, drawdown, statistiques et courbe d'équité
- Connexion MT5 et basculement entre comptes regroupés dans **Réglages**
- Calendrier et carte de chaleur par heure (`00` à `23`) et jour de la semaine
- Performances par semaine et par mois
- Répartitions par symbole, direction, setup et discipline
- **Analyzer** : sessions, conformité au plan (SOP versionné), coût des
  erreurs, performance par émotion, patterns avec intervalles de confiance,
  overtrading, qualité des données, rapport HTML autonome et exports
  CSV/JSON/Excel. Voir `backend/services/analyzer/README.md`
- Historique filtrable des trades
- Notes, tags et pièces jointes image
- Export PDF selon les filtres sélectionnés
- Dans l'application desktop, l'export PDF ouvre une boîte native **Enregistrer sous**
- Thème clair/sombre et interface responsive

Le code d'accès est un verrou local. Lors du premier lancement, l'application génère un code de récupération unique à conserver. En cas d'oubli, **Code oublié ?** demande ce code avant d'autoriser la création d'un nouveau code d'accès. Le code de récupération est stocké uniquement sous forme de hash.

La récupération par email/SMTP n'est pas nécessaire et n'est pas implémentée. Si le code d'accès et le code de récupération sont tous les deux perdus, une réinitialisation technique du stockage local peut être nécessaire ; cela ne supprime pas les données SQLite, mais retire la protection d'ouverture.

## API Documentation

Une fois lancé, accédez à la [documentation de l'API](http://127.0.0.1:8000/docs).

### Endpoints principaux

| Méthode | Endpoint | Description |
| --- | --- | --- |
| POST | `/api/mt5/connect` | Connexion compte MT5 |
| POST | `/api/mt5/sync` | Synchronisation trades |
| GET | `/api/trades` | Liste des trades (filtres disponibles) |
| GET | `/api/performance/stats` | Stats globales |
| GET | `/api/performance/equity-curve` | Courbe de capital |
| GET | `/api/performance/by-week` | P&L hebdomadaire |
| GET | `/api/performance/monthly` | P&L mensuel |
| GET | `/api/performance/by-symbol` | P&L par symbole |
| GET | `/api/performance/calendar` | Calendrier mensuel |
| GET | `/api/performance/extended-stats` | Statistiques étendues et carte horaire |
| GET | `/api/performance/repartitions` | Répartitions analytiques |
| GET | `/api/export/pdf` | Export du rapport PDF filtré |
| GET | `/api/analyzer/overview` | KPIs globaux (identiques au Dashboard) |
| GET | `/api/analyzer/dimension/{nom}` | setup, session, symbol, weekday, hour, emotion, error… |
| GET | `/api/analyzer/errors` | Fréquence et **coût** de chaque erreur |
| GET | `/api/analyzer/discipline` | Conformité au plan et Plan vs Réalité |
| GET | `/api/analyzer/patterns` | Patterns à 2 variables, avec fiabilité |
| GET | `/api/analyzer/series` · `/frequency` · `/overtrading` | Séries, fréquence, overtrading |
| GET | `/api/analyzer/data-quality` | Taux de remplissage et anomalies |
| GET | `/api/analyzer/report.html` | Rapport HTML autonome (hors ligne) |
| GET | `/api/analyzer/export/{json\|csv\|xlsx}` | Exports |
| GET/PUT | `/api/analyzer/settings` · `/symbol-map` | Sessions, seuils, normalisation |
| GET/POST | `/api/analyzer/sop` | Versions du plan de trading |
| GET/PUT | `/api/analyzer/trades/{ticket}/journal` | SOP, émotions, erreurs, tags d'un trade |
| GET/PATCH | `/api/analyzer/playbook/rules` | Règles proposées : accepter / rejeter |
| GET | `/api/attachments/file/{filename}` | Lecture authentifiée d'une pièce jointe du compte actif |

### Filtres disponibles sur `/api/trades`

```text
?symbol=EURUSD
?direction=buy
?result=win
?date_from=2026-01-01&date_to=2026-05-31
?limit=20&offset=0
```

## Base de données

SQLite — fichier `db/trading_journal.db`

Tables :

- **accounts** : informations du compte MT5
- **trades** : tous vos trades (historique + ouverts)
- **daily_snapshots** : snapshot quotidien du capital
- **attachments** : pièces jointes liées à un trade (captures d'écran), fichiers stockés dans `backend/uploads/`

Pour visualiser la BDD : installer [DB Browser for SQLite](https://sqlitebrowser.org/)

## Sécurité et données locales

- La base se trouve dans `db/trading_journal.db`.
- La clé utilisée pour chiffrer les mots de passe MT5 se trouve dans `db/.secret_key`.
- Les captures d'écran sont stockées dans `backend/uploads/` et servies uniquement via l'API authentifiée.
- Les logs sont écrits dans `logs/backend.log`.
- Sauvegarder `db/`, `backend/uploads/` et `logs/` avant de déplacer ou réinstaller l'application.
- Le code d'accès local n'est pas un mécanisme de sécurité fort : il protège l'ouverture de l'interface sur la machine, mais ne chiffre pas la base SQLite.

## Personnalisation

- **Thème clair / sombre** : bouton en haut de la barre latérale, préférence mémorisée dans le navigateur.
- **Pièces jointes** : dans la vue détaillée d'un trade (icône 👁 dans le tableau des trades), ajoutez des captures d'écran (graphique, setup avant/après). Stockées dans `backend/uploads/` — pensez à sauvegarder ce dossier avec `db/trading_journal.db` si vous migrez l'installation.

## Mode démo (sans MT5)

Si MT5 n'est pas disponible, l'API injecte automatiquement des données de démonstration au premier démarrage. Idéal pour tester l'interface.

La synchronisation est déclenchée depuis le bouton **Synchroniser** du dashboard. Aucun fichier `sync_only.py` n'est fourni actuellement pour une tâche planifiée automatique.
