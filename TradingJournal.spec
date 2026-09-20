# -*- mode: python ; coding: utf-8 -*-
"""
Fichier de build PyInstaller pour l'app desktop "Trading Journal".

À lancer via build.bat (qui installe les dépendances puis appelle
`pyinstaller TradingJournal.spec`), pas directement avec `pyinstaller
launcher.py` — les options ci-dessous (add-data, hidden-imports) sont
indispensables et ne peuvent pas toutes tenir dans une ligne de commande.

Pourquoi autant de hidden-imports / collect_all : `backend/` et `frontend/`
sont ajoutés en `datas` (fichiers copiés tels quels dans le paquet), pas
analysés comme du code par PyInstaller — c'est volontaire (voir le
commentaire de BASE_DIR dans launcher.py) : `main.py` est importé
dynamiquement au runtime via `sys.path.insert` + `import main`, une fois
`sys._MEIPASS` connu. Conséquence : PyInstaller ne peut PAS remonter tout
seul les imports de fastapi/sqlalchemy/etc. utilisés par ces fichiers,
puisqu'il ne les analyse jamais comme du code Python — d'où la liste
explicite ci-dessous. Si un "ModuleNotFoundError" apparaît malgré tout au
lancement de l'exe, c'est presque toujours une dépendance manquante ici.
"""
from PyInstaller.utils.hooks import collect_all

datas = [
    ("backend", "backend"),
    ("frontend", "frontend"),
]
binaries = []
hiddenimports = [
    # uvicorn choisit sa boucle/son protocole par nom de string au runtime
    # (config.py) : modulegraph ne peut pas deviner ces modules, il faut les
    # lister explicitement.
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    # pywebview : le backend Windows réel (EdgeChromium/WinForms) est
    # sélectionné dynamiquement selon ce qui est installé sur la machine.
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    # python-multipart : le module importé s'appelle "multipart", pas
    # "python_multipart" (utilisé par FastAPI pour lire les form-data des
    # pièces jointes).
    "multipart",
    # MetaTrader5 est optionnel (Windows + terminal MT5 installés) et déjà
    # protégé par un try/except dans mt5_service.py — s'il n'est pas
    # installé dans l'environnement de build, PyInstaller l'ignorera
    # silencieusement sans faire échouer le build.
    "MetaTrader5",
]

# Packages dont les imports internes sont trop dynamiques pour être
# détectés un par un : on demande à PyInstaller de tout embarquer (code +
# données, ex. les polices/mpl-data de matplotlib).
for pkg in (
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_core",
    "sqlalchemy",
    "cryptography",
    "fpdf",
    "matplotlib",
    "PIL",
    # openpyxl — lecture des rapports MT5 xlsx (étape 4, import).
    "openpyxl",
    # psutil charge un sous-module compilé différent par OS (_pswindows,
    # _psutil_windows.pyd) : collect_all() plutôt qu'un simple hiddenimport,
    # pour ne pas avoir à lister ces internes à la main.
    "psutil",
):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

a = Analysis(
    ["launcher.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="TradingJournal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # console=False : comportement d'une vraie app desktop, pas de fenêtre
    # noire derrière pywebview. Les erreurs de démarrage restent
    # consultables dans logs/backend.log (voir logging.basicConfig dans
    # backend/main.py, qui écrit dans un fichier en plus de la console) et,
    # pour un crash non intercepté, disable_windowed_traceback=False fait
    # apparaître une boîte de dialogue d'erreur au lieu de fermer sans rien
    # dire.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="frontend/icon.ico",
)
