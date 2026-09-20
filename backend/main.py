"""Point d'entrée de l'API — assemblage de l'application FastAPI.

Depuis la refonte (voir section 6 de l'audit — "découper main.py en
modules"), ce fichier ne fait plus QUE de l'assemblage : création de l'app,
CORS, montage des fichiers statiques, inclusion des routers, et démarrage.
Toute la logique métier vit désormais dans :
- state.py                    → singletons (service MT5), compte actif, réglages
- services/stats.py           → calcul financier (drawdown, R-multiple, risque...)
- services/pdf_report.py      → génération du rapport PDF
- routers/mt5.py              → connexion, comptes, synchronisation MT5
- routers/accounts.py         → comptes manuels (sans MT5)
- routers/movements.py        → dépôts / retraits (journal des mouvements de capital)
- routers/settings.py         → réglages et compte actif
- routers/trades.py           → CRUD trades + pièces jointes
- routers/performance.py      → statistiques, courbes, agrégations, export PDF
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
from typing import Optional
import atexit
import os
import threading
import uvicorn

import auth_utils
from database import engine, Base, run_light_migrations
import mt5_service
import state
from routers import auth as auth_router
from routers import accounts as accounts_router
from routers import mt5 as mt5_router
from routers import settings as settings_router
from routers import trades as trades_router
from routers import performance as performance_router
from routers import import_router
from routers import movements as movements_router
from routers import analyzer as analyzer_router

# IMPORTANT : les modèles de l'Analyzer doivent être connus de SQLAlchemy
# AVANT `create_all()`, sinon leurs tables ne sont jamais créées (create_all
# ne crée que ce qui est déjà enregistré sur `Base.metadata`). C'est le
# premier des deux pièges d'intégration listés au §4 du cahier v2.
# `noqa: F401` — import volontairement « inutilisé » : c'est son effet de
# bord (enregistrement des modèles) qui compte.
from services.analyzer import models as analyzer_models  # noqa: F401

Base.metadata.create_all(bind=engine)
run_light_migrations()
state.seed_default_settings()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Démarrage / arrêt de l'API.

    Deux corrections ici :

    1. `@app.on_event("startup")` est déprécié (avertissement à chaque
       lancement avec FastAPI >= 0.109, suppression annoncée) — remplacé par
       le gestionnaire `lifespan` officiel.
    2. Rien de ce qui touche MetaTrader5 ne s'exécute avant que le serveur
       n'écoute : on ne fait que LANCER des threads démons.
       - `start_mt5_import()` charge le paquet MetaTrader5 (DLL native,
         plusieurs secondes possibles) en tâche de fond ; voir l'en-tête de
         mt5_service.py.
       - `auto_connect_mt5` attend ensuite cet import, puis reconnecte et
         resynchronise le compte actif — UNE SEULE FOIS, au démarrage :
         c'est la restauration de la session précédente, pas une boucle.
         Elle aussi est désormais GARDÉE (voir sa docstring) : ce chemin
         précis avait échappé à la correction précédente et forçait donc
         l'ouverture de MT5 à chaque lancement de l'application, y compris
         quand l'utilisateur l'avait délibérément laissé fermé.
       - `state.start_passive_reconnect_worker()` lance ENSUITE la boucle
         de reconnexion de fond, gardée de la même façon. Terminal fermé au
         démarrage ⇒ ni l'une ni l'autre ne tente quoi que ce soit ; la
         restauration se fera d'elle-même, sans action de l'utilisateur,
         dès que cette boucle détectera que le terminal a été ouvert.
       Dans les deux cas, la garde vérifie d'abord, par la liste des
       process système et un simple test réseau (jamais par l'API
       MetaTrader5), que le terminal tourne déjà ET que la machine a une
       connexion internet ; si l'une des deux conditions manque — et en
       particulier si MT5 n'est pas ouvert — rien n'est tenté. Voir
       state.reconnect_active_account() et mt5_service.is_terminal_running().
       L'API répond donc immédiatement, et la fenêtre de l'app s'ouvre sans
       attendre MT5 — l'interface reflète l'avancement via /api/mt5/status.
    """
    mt5_service.start_mt5_import()
    threading.Thread(target=state.auto_connect_mt5, daemon=True, name="mt5-auto-connect").start()
    state.start_passive_reconnect_worker()
    yield


app = FastAPI(title="Trading Journal API", version="1.0.0", lifespan=lifespan)

# Deux modes de lancement coexistent :
# - launcher.py (app desktop) : frontend ET API servis par ce process, donc
#   même origine — CORS sans objet, mais l'origine peut varier si le port par
#   défaut est occupé (voir _pick_port dans launcher.py) ;
# - START.bat (mode legacy) : frontend sur :5500, API sur :8000.
# Correction : la liste précédente ne contenait que :5500. Dès que l'app
# tournait sur un autre port que 8000, ou que le frontend était ouvert
# directement sur l'API, les requêtes pré-volées étaient refusées. On
# autorise donc toute origine locale — l'API reste de toute façon protégée
# par le middleware d'authentification ci-dessous et n'écoute que sur
# 127.0.0.1.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _extract_token(request: Request) -> Optional[str]:
    """Jeton de session lu UNIQUEMENT dans l'en-tête Authorization.

    Correctif sécurité : un repli `?token=...` existait pour les balises
    <img> des pièces jointes. Il n'est plus utilisé par aucun appelant —
    les captures passent maintenant par `loadAttachmentBlobUrl()`
    (frontend/js/config.js), qui utilise fetch() et porte donc l'en-tête.
    Or uvicorn journalise la query string complète dans son access log
    (`logs/backend.log`) : chaque requête ainsi formée y recopiait un jeton
    de session valide en clair, réutilisable jusqu'à son expiration. On
    supprime ce chemin d'entrée plutôt que de conserver une surface
    d'attaque sans usage.
    """
    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:]
    return None


@app.middleware("http")
async def enforce_auth(request: Request, call_next):
    """Bloque tout /api/* (hors /api/auth/*, qui sert justement à obtenir
    une session) et /uploads/* sans jeton de session valide.

    Correctif audit UI/UX — le verrou précédent (voir l'historique de
    frontend/index.html) ne protégeait que l'affichage de la page : cette
    API répondait sans aucun contrôle à quiconque pouvait l'atteindre sur
    127.0.0.1:8000, contournement trivial en ouvrant un autre onglet ou en
    appelant l'API directement. Centralisé ici en middleware (plutôt qu'en
    `Depends()` répété sur chaque router) pour ne jamais oublier de
    protéger une route existante ou future.
    """
    if request.method == "OPTIONS":
        # Une requête de pré-vol CORS ne porte jamais d'Authorization —
        # elle doit être traitée par CORSMiddleware, pas bloquée ici.
        return await call_next(request)
    path = request.url.path
    protected = (path.startswith("/api/") and not path.startswith("/api/auth/")) or path.startswith("/uploads/")
    if protected and not auth_utils.verify_session(_extract_token(request)):
        return JSONResponse({"detail": "Authentification requise."}, status_code=401)
    return await call_next(request)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """En-têtes défensifs sur toutes les réponses, y compris les 401.

    Déclaré APRÈS enforce_auth : Starlette exécute les middlewares HTTP dans
    l'ordre inverse de leur déclaration, celui-ci enveloppe donc l'autre et
    s'applique aussi aux réponses d'erreur qu'il produit.

    - nosniff : empêche le navigateur de deviner un type MIME autre que
      celui déclaré — pertinent ici pour /api/attachments/file/*, qui sert
      des fichiers d'origine utilisateur.
    - no-referrer : rien ne doit fuiter vers l'extérieur (Google Fonts, CDN)
      sur l'URL locale de l'application.
    - DENY : cette interface n'a aucune raison d'être encadrée.

    Pas de Content-Security-Policy ici : la page contient des scripts inline
    (écran de verrouillage, thème, écran de démarrage) et des gestionnaires
    onclick, qu'une CSP stricte casserait. À reprendre si ces scripts sont
    un jour externalisés.
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response


# Correction : mt5.shutdown() n'était jamais appelé, y compris à l'arrêt de
# l'API — le terminal MT5 restait initialisé côté process inutilement.
atexit.register(state.mt5.shutdown)

app.include_router(auth_router.router)
app.include_router(mt5_router.router)
app.include_router(accounts_router.router)
app.include_router(settings_router.router)
app.include_router(trades_router.router)
app.include_router(performance_router.router)
app.include_router(performance_router.export_router)
app.include_router(import_router.router)
app.include_router(movements_router.router)
# Analyzer (module additif) : retirer cette seule ligne le désactive
# entièrement côté API — aucune donnée n'est perdue, les tables restent
# simplement inertes.
app.include_router(analyzer_router.router)

# Frontend servi par ce même process/port (ajouté pour l'empaquetage desktop,
# voir launcher.py) : évite d'avoir à lancer un second serveur HTTP juste
# pour contourner le blocage des modules ES en file:// (cf. START.bat).
# Monté en dernier et volontairement à la racine "/" : Starlette teste les
# routes dans leur ordre de déclaration, donc toutes les routes /api/* et
# /uploads ci-dessus restent prioritaires — seul ce qui ne matche aucune
# d'elles retombe sur les fichiers statiques du frontend.
FRONTEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))
# Correction : `StaticFiles(directory=...)` lève une RuntimeError À L'IMPORT
# si le dossier n'existe pas. Dans l'app compilée (console=False), cela se
# traduisait par une fenêtre qui ne s'ouvre jamais, sans message. On vérifie
# donc explicitement, avec un log clair : l'API reste utilisable même sans
# frontend embarqué.
if os.path.isdir(FRONTEND_DIR):
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
else:
    state.logger.error(
        "Dossier frontend introuvable (%s) — l'API démarre mais l'interface "
        "ne sera pas servie sur ce port.",
        FRONTEND_DIR,
    )


if __name__ == "__main__":
    # reload=True (mode développement) surveille tous les fichiers du dossier
    # et REDÉMARRE tout le process API dès qu'un fichier change — y compris
    # le fichier SQLite lui-même (et son "-journal" temporaire créé à chaque
    # écriture) et backend/uploads/ (pièces jointes). Résultat concret : à
    # chaque note/tag enregistré ou chaque pièce jointe ajoutée, l'API
    # redémarrait en plein milieu, coupant la connexion du frontend pendant
    # ~1-2s — ce qui donnait l'impression que l'application "se fermait".
    # Cette appli tourne en local pour un seul utilisateur, pas en dev actif
    # sur le code : pas besoin de reload.
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
