@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   Trading Journal - Build de l'app desktop
echo ============================================
echo.

if not exist "frontend\icon.ico" (
    echo ERREUR: frontend\icon.ico introuvable.
    echo Ajoutez l'icone avant de lancer le build.
    pause
    exit /b 1
)

REM --- 1. Environnement virtuel dedie au build -------------------------
REM Separe de tout venv de dev existant : evite qu'un paquet installe a la
REM main pendant le developpement (et jamais mis dans requirements.txt) se
REM retrouve absent de l'exe final sans que personne ne le remarque.
if not exist "build_venv" (
    echo [1/5] Creation de l'environnement de build...
    python -m venv build_venv
    if errorlevel 1 (
        echo ERREUR: Python introuvable ou echec de creation du venv.
        pause
        exit /b 1
    )
) else (
    echo [1/5] Environnement de build existant, reutilise.
)

call build_venv\Scripts\activate.bat

echo [2/5] Installation des dependances de l'app...
python -m pip install --upgrade pip >nul
python -m pip install -r backend\requirements.txt
if errorlevel 1 (
    echo ERREUR: echec de l'installation de backend\requirements.txt
    pause
    exit /b 1
)

echo [3/5] Installation des dependances de build (PyInstaller)...
python -m pip install -r requirements-build.txt
if errorlevel 1 (
    echo ERREUR: echec de l'installation de requirements-build.txt
    pause
    exit /b 1
)

echo [4/5] Nettoyage des builds precedents...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"

echo [5/5] Compilation avec PyInstaller...
python -m PyInstaller TradingJournal.spec
if errorlevel 1 (
    echo ERREUR: la compilation a echoue, voir le detail ci-dessus.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Build termine : dist\TradingJournal.exe
echo ============================================
echo.
echo Au premier lancement, l'exe va creer a cote de lui :
echo   db\        (base de donnees + cle de chiffrement)
echo   logs\      (backend.log)
echo   backend\uploads\  (pieces jointes)
echo Deplacer l'exe = deplacer ces dossiers avec lui (copier le tout
echo ensemble, pas seulement le .exe, si vous voulez garder vos donnees).
echo.
pause
