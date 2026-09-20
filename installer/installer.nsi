; NSIS installer for MLxPhantom Journal
Unicode True

!include "MUI2.nsh"

Name "MLxPhantom Journal"
OutFile "..\dist_installer\MLxPhantomJournal_Setup.exe"
InstallDir "$LOCALAPPDATA\MLxPhantomJournal"
InstallDirRegKey HKCU "Software\MLxPhantomJournal" "InstallDir"
RequestExecutionLevel user

; Même icône pour l'installeur, l'application installée et le désinstalleur.
Icon "..\frontend\icon.ico"

VIProductVersion "1.0.0.0"
VIAddVersionKey "ProductName" "MLxPhantom Journal"
VIAddVersionKey "CompanyName" "MLxPhantom Journal"
VIAddVersionKey "FileDescription" "Installateur MLxPhantom Journal"
VIAddVersionKey "FileVersion" "1.0.0"
VIAddVersionKey "LegalCopyright" "Copyright (c) 2026 MLxPhantom Journal"

!define MUI_ABORTWARNING
!define MUI_ICON "..\frontend\icon.ico"
!define MUI_UNICON "..\frontend\icon.ico"
!define MUI_FINISHPAGE_RUN "$INSTDIR\TradingJournal.exe"
!define MUI_FINISHPAGE_RUN_TEXT "Lancer MLxPhantom Journal"
!define MUI_FINISHPAGE_RUN_FUNCTION LaunchApplication

!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "..\LICENSE.txt"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_COMPONENTS
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES
!insertmacro MUI_LANGUAGE "French"

Function .onInit
    SetShellVarContext current
FunctionEnd

Function LaunchApplication
    ExecShell "open" "$INSTDIR\TradingJournal.exe"
FunctionEnd

Section "MLxPhantom Journal" SecMain
    SetOutPath "$INSTDIR"
    File "..\dist\TradingJournal.exe"

    WriteRegStr HKCU "Software\MLxPhantomJournal" "InstallDir" "$INSTDIR"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "DisplayName" "MLxPhantom Journal"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "UninstallString" '"$INSTDIR\Uninstall.exe"'
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "DisplayIcon" "$INSTDIR\TradingJournal.exe"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "Publisher" "MLxPhantom Journal"
    WriteRegStr HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "DisplayVersion" "1.0.0"
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "NoModify" 1
    WriteRegDWORD HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal" "NoRepair" 1

    CreateDirectory "$SMPROGRAMS\MLxPhantom Journal"
    CreateShortCut "$SMPROGRAMS\MLxPhantom Journal\MLxPhantom Journal.lnk" "$INSTDIR\TradingJournal.exe"
    CreateShortCut "$SMPROGRAMS\MLxPhantom Journal\Desinstaller.lnk" "$INSTDIR\Uninstall.exe"

    WriteUninstaller "$INSTDIR\Uninstall.exe"

SectionEnd

Section /o "Raccourci Bureau" SecDesktop
    CreateShortCut "$DESKTOP\MLxPhantom Journal.lnk" "$INSTDIR\TradingJournal.exe"
SectionEnd

Function un.onInit
    SetShellVarContext current
FunctionEnd

Section "Uninstall"
    Delete "$SMPROGRAMS\MLxPhantom Journal\MLxPhantom Journal.lnk"
    Delete "$SMPROGRAMS\MLxPhantom Journal\Desinstaller.lnk"
    RMDir "$SMPROGRAMS\MLxPhantom Journal"
    Delete "$DESKTOP\MLxPhantom Journal.lnk"

    Delete "$INSTDIR\TradingJournal.exe"
    Delete "$INSTDIR\Uninstall.exe"
    ; Les dossiers db, logs et backend\uploads sont volontairement conserves.
    RMDir "$INSTDIR"

    DeleteRegKey HKCU "Software\Microsoft\Windows\CurrentVersion\Uninstall\MLxPhantomJournal"
    DeleteRegKey HKCU "Software\MLxPhantomJournal"
SectionEnd

