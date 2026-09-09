; ApplyCanary Windows Installer — NSIS script
; Build with: makensis scripts/installer.nsi
;
; Expects the PyInstaller output in dist/ApplyCanary/

!include "MUI2.nsh"

Name "ApplyCanary"
OutFile "dist\ApplyCanary_windows_x64_setup.exe"
InstallDir "$LOCALAPPDATA\ApplyCanary"
InstallDirRegKey HKCU "Software\ApplyCanary" "InstallDir"
RequestExecutionLevel user

; ---- Interface ----
!define MUI_ICON "assets\icon.ico"
!define MUI_UNICON "assets\icon.ico"
!define MUI_ABORTWARNING
!define MUI_WELCOMEPAGE_TITLE "ApplyCanary Setup"
!define MUI_WELCOMEPAGE_TEXT "This wizard will install ApplyCanary on your computer.$\r$\n$\r$\nYour AI Career Agent — job discovery, ATS scoring and interview prep in a native desktop window."

; ---- Pages ----
!insertmacro MUI_PAGE_WELCOME
!insertmacro MUI_PAGE_LICENSE "LICENSE"
!insertmacro MUI_PAGE_DIRECTORY
!insertmacro MUI_PAGE_INSTFILES
!insertmacro MUI_PAGE_FINISH

!insertmacro MUI_UNPAGE_CONFIRM
!insertmacro MUI_UNPAGE_INSTFILES

!insertmacro MUI_LANGUAGE "English"

Section "ApplyCanary" SecMain
    SetOutPath "$INSTDIR"

    ; Copy the entire PyInstaller bundle
    File /r "dist\ApplyCanary\*.*"

    ; Store install path
    WriteRegStr HKCU "Software\ApplyCanary" "InstallDir" "$INSTDIR"

    ; Create uninstaller
    WriteUninstaller "$INSTDIR\Uninstall.exe"

    ; Start Menu shortcut
    CreateDirectory "$SMPROGRAMS\ApplyCanary"
    CreateShortCut "$SMPROGRAMS\ApplyCanary\ApplyCanary.lnk" "$INSTDIR\ApplyCanary.exe" "" "$INSTDIR\assets\icon.ico"
    CreateShortCut "$SMPROGRAMS\ApplyCanary\Uninstall.lnk" "$INSTDIR\Uninstall.exe"

    ; Desktop shortcut
    CreateShortCut "$DESKTOP\ApplyCanary.lnk" "$INSTDIR\ApplyCanary.exe" "" "$INSTDIR\assets\icon.ico"

    ; Add to PATH so it can be launched from terminal
    EnVar::AddValue "PATH" "$INSTDIR"
SectionEnd

Section "Uninstall"
    ; Remove files
    RMDir /r "$INSTDIR"

    ; Remove shortcuts
    RMDir /r "$SMPROGRAMS\ApplyCanary"
    Delete "$DESKTOP\ApplyCanary.lnk"

    ; Remove registry keys
    DeleteRegKey HKCU "Software\ApplyCanary"
    EnVar::RemoveValue "PATH" "$INSTDIR"
SectionEnd
