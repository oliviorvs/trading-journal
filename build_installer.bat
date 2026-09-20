@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   MLxPhantom Journal - Creation de l'installeur
echo ============================================
echo.

if not exist "frontend\icon.ico" (
    echo ERREUR: frontend\icon.ico introuvable.
    echo Ajoutez l'icone avant de compiler l'installeur.
    pause
    exit /b 1
)

if not exist "dist\TradingJournal.exe" (
    echo ERREUR: dist\TradingJournal.exe introuvable.
    echo Lancez d'abord build.bat pour compiler l'application.
    pause
    exit /b 1
)

REM --- Localisation de makensis.exe --------------------------------------
set "MAKENSIS="
where makensis.exe >nul 2>nul
if not errorlevel 1 (
    set "MAKENSIS=makensis.exe"
) else if exist "%ProgramFiles(x86)%\NSIS\makensis.exe" (
    set "MAKENSIS=%ProgramFiles(x86)%\NSIS\makensis.exe"
) else if exist "%ProgramFiles%\NSIS\makensis.exe" (
    set "MAKENSIS=%ProgramFiles%\NSIS\makensis.exe"
)

if "%MAKENSIS%"=="" (
    echo ERREUR: NSIS introuvable.
    echo Installez NSIS ^(gratuit^) : https://nsis.sourceforge.io/Download
    echo Puis relancez ce script.
    pause
    exit /b 1
)

if not exist "dist_installer" mkdir "dist_installer"

echo Compilation avec %MAKENSIS% ...
"%MAKENSIS%" "installer\installer.nsi"
if errorlevel 1 (
    echo ERREUR: la compilation de l'installeur a echoue, voir le detail ci-dessus.
    pause
    exit /b 1
)

echo.
echo ============================================
echo   Installeur pret : dist_installer\MLxPhantomJournal_Setup.exe
echo ============================================
echo.
echo Cet installeur :
echo   - ne demande PAS les droits administrateur
echo   - installe dans %%LOCALAPPDATA%%\TradingJournal ^(par utilisateur^)
echo   - affiche et fait accepter la licence d'utilisation
echo   - cree un raccourci Menu Demarrer ^(+ Bureau si coche^)
echo   - ajoute une entree dans "Applications" pour desinstaller proprement
echo.
pause
