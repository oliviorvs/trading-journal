"""Point d'entrée de l'API — assemblage de l'application FastAPI.

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

from services.analyzer import models as analyzer_models  # noqa: F401

Base.metadata.create_all(bind=engine)
run_light_migrations()
state.seed_default_settings()

@asynccontextmanager
async def lifespan(_app: FastAPI):

    mt5_service.start_mt5_import()
    threading.Thread(target=state.auto_connect_mt5, daemon=True, name="mt5-auto-connect").start()
    state.start_passive_reconnect_worker()
    yield


app = FastAPI(title="Trading Journal API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _extract_token(request: Request) -> Optional[str]:

    header = request.headers.get("authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:]
    return None


@app.middleware("http")
async def enforce_auth(request: Request, call_next):

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
    """
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("X-Frame-Options", "DENY")
    return response

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
