@echo off
title Trading Journal - Demarrage
color 0A
setlocal EnableDelayedExpansion

echo.
echo                    JOURNAL  MT4/5
echo.

cd /d "%~dp0"

:: ============================================================
:: [1/5] Verification / installation de Python
:: ============================================================
echo [1/5] Verification de Python...
where python >nul 2>&1
if errorlevel 1 (
    echo  Python non trouve. Tentative d'installation automatique...

    where winget >nul 2>&1
    if not errorlevel 1 (
        echo  Installation via winget...
        winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements
    ) else (
        echo  winget indisponible, telechargement de l'installateur officiel...
        set "PY_INSTALLER=%TEMP%\python-installer.exe"
        powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.4/python-3.12.4-amd64.exe' -OutFile '%PY_INSTALLER%' } catch { exit 1 }"
        if errorlevel 1 (
            echo  ERREUR : impossible de telecharger Python.
            echo  Installez-le manuellement : https://www.python.org/downloads/
            pause
            exit /b 1
        )
        echo  Installation silencieuse de Python en cours...
        "%PY_INSTALLER%" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
        del "%PY_INSTALLER%" >nul 2>&1
    )

    call :refresh_path

    where python >nul 2>&1
    if errorlevel 1 (
        echo  ERREUR : Python n'a pas pu etre installe automatiquement.
        echo  Installez-le manuellement sur https://www.python.org/downloads/
        echo  Cochez "Add Python to PATH" lors de l'installation, puis relancez ce script.
        pause
        exit /b 1
    )
    echo  OK - Python installe avec succes.
) else (
    echo  OK - Python deja present.
)

echo.
echo [2/5] Installation des dependances...
python -m pip install -r "%~dp0backend\requirements.txt"
if errorlevel 1 (
    echo  ERREUR : impossible d'installer les dependances de backend.
    echo  Verifiez votre connexion Internet et relancez START.bat.
    pause
    exit /b 1
)
echo  OK - Toutes les dependances sont pretes.
cd backend

echo.
echo [3/5] Verification de la base de donnees...
if not exist "..\db" (
    mkdir "..\db"
    echo  Dossier db\ cree.
)
if exist "..\db\trading_journal.db" (
    echo  OK - Base de donnees existante trouvee, elle sera reutilisee.
) else (
    echo  Aucune base trouvee, elle sera creee automatiquement au demarrage du serveur.
)

echo.
echo [4/5] Demarrage du serveur API...
start "Trading Journal API" cmd /k "python main.py"

echo.
echo  Attente du demarrage ^(3 secondes^)...
timeout /t 3 /nobreak >nul

echo.
echo [5/5] Ouverture du journal dans le navigateur...
:: Les modules ES du frontend ("import"/"export") sont bloqués par le
:: navigateur en file:// (erreur CORS silencieuse) : on sert le dossier
:: frontend via un petit serveur HTTP local au lieu d'ouvrir le fichier
:: directement.
start "Trading Journal Web" cmd /k "cd /d "%~dp0frontend" && python -m http.server 5500"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5500/index.html"

echo.
echo  ============================================
echo   Journal de trading demarre avec succes !
echo   Site : http://127.0.0.1:5500
echo   API  : http://127.0.0.1:8000
echo   Docs : http://127.0.0.1:8000/docs
echo  ============================================
echo.
echo  Pour arreter : fermez les fenetres "Trading Journal API" et "Trading Journal Web"
echo.
pause
exit /b 0

:refresh_path
for /f "usebackq tokens=2,*" %%A in (`reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" /v PATH 2^>nul`) do set "SYS_PATH=%%B"
for /f "usebackq tokens=2,*" %%A in (`reg query "HKCU\Environment" /v PATH 2^>nul`) do set "USER_PATH=%%B"
set "PATH=%SYS_PATH%;%USER_PATH%"
goto :eof
